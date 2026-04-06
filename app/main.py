from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from app.embedded import (
    apply_iframe_protection_headers,
    build_embedded_context,
    verify_embedded_admin_request,
)
from app.exporters.meta_catalog import build_meta_catalog_rows, write_meta_catalog_csv
from app.job_store import job_store
from app.settings import EXPORTS_DIR, settings
from app.shopify_client import (
    ShopifyGraphQLClient,
    exchange_client_credentials,
    normalize_shop_domain,
)


app = FastAPI(title="Shopify Facebook Catalog Exporter")
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")


class ExportRequest(BaseModel):
    shop_domain: str = Field(default="")
    access_token: str | None = None
    client_id: str | None = None
    client_secret: str | None = None
    api_version: str | None = None
    export_mode: str = "bulk"
    product_query: str = settings.default_product_query


@app.middleware("http")
async def add_shopify_frame_protection(request: Request, call_next):
    response = await call_next(request)
    if "text/html" in (response.headers.get("content-type") or ""):
        context = build_embedded_context(
            request,
            api_key=settings.default_client_id,
            default_shop_domain=settings.default_shop_domain,
        )
        apply_iframe_protection_headers(response, context)
    return response


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    embedded_context = build_embedded_context(
        request,
        api_key=settings.default_client_id,
        default_shop_domain=settings.default_shop_domain,
    )
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "embedded_context_json": json.dumps(
                {
                    "shopDomain": embedded_context.shop_domain,
                    "host": embedded_context.host,
                    "embedded": embedded_context.embedded,
                    "apiKey": embedded_context.api_key,
                }
            ),
            "default_api_version": settings.default_api_version,
            "default_product_query": settings.default_product_query,
            "default_shop_domain": embedded_context.shop_domain or "",
            "shopify_api_key": settings.default_client_id or "",
            "has_embedded_credentials": bool(settings.default_client_id and settings.default_client_secret),
        },
    )


@app.post("/api/jobs")
def create_export_job(payload: ExportRequest, request: Request, background_tasks: BackgroundTasks) -> dict:
    shop_domain = verify_embedded_admin_request(
        request,
        client_id=settings.default_client_id,
        client_secret=settings.default_client_secret,
        expected_shop=payload.shop_domain or settings.default_shop_domain,
    )
    export_mode = payload.export_mode if payload.export_mode in {"bulk", "direct"} else "bulk"
    product_query = (payload.product_query or settings.default_product_query).strip()

    job = job_store.create(
        shop_domain=shop_domain,
        export_mode=export_mode,
        product_query=product_query,
    )
    background_tasks.add_task(
        run_export_job,
        job.id,
        payload.model_copy(
            update={
                "shop_domain": shop_domain,
                "access_token": None,
                "client_id": None,
                "client_secret": None,
            }
        ),
    )
    return {"job": job.to_dict()}


@app.get("/api/jobs/{job_id}")
def get_export_job(job_id: str, request: Request) -> dict:
    job = job_store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    verify_embedded_admin_request(
        request,
        client_id=settings.default_client_id,
        client_secret=settings.default_client_secret,
        expected_shop=job.shop_domain,
    )
    return {"job": job.to_dict()}


@app.get("/api/jobs/{job_id}/download")
def download_export(job_id: str, request: Request) -> FileResponse:
    job = job_store.get(job_id)
    if not job or not job.file_path or not job.file_name:
        raise HTTPException(status_code=404, detail="Export file not found")
    verify_embedded_admin_request(
        request,
        client_id=settings.default_client_id,
        client_secret=settings.default_client_secret,
        expected_shop=job.shop_domain,
    )
    path = Path(job.file_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Export file was removed")
    return FileResponse(path=path, filename=job.file_name, media_type="text/csv")


@app.post("/webhooks")
async def receive_webhook(request: Request) -> dict:
    await request.body()
    return {"ok": True}


@app.get("/healthz")
def healthcheck() -> dict:
    return {"ok": True}


def run_export_job(job_id: str, payload: ExportRequest) -> None:
    job_store.update(job_id, status="running", error=None, warnings=[])
    try:
        shop_domain = normalize_shop_domain(payload.shop_domain or settings.default_shop_domain or "")
        api_version = (payload.api_version or settings.default_api_version).strip()
        product_query = (payload.product_query or settings.default_product_query).strip()
        export_mode = payload.export_mode if payload.export_mode in {"bulk", "direct"} else "bulk"
        access_token = resolve_access_token(payload, shop_domain)

        client = ShopifyGraphQLClient(
            shop_domain=shop_domain,
            access_token=access_token,
            api_version=api_version,
            timeout_seconds=settings.request_timeout_seconds,
        )
        if export_mode == "direct":
            shop_context, products = client.fetch_products_direct(product_query)
        else:
            shop_context, products = client.fetch_products_bulk(
                product_query,
                poll_interval_seconds=settings.bulk_poll_interval_seconds,
                max_wait_seconds=settings.bulk_max_wait_seconds,
            )

        rows, warnings = build_meta_catalog_rows(shop_context, products)
        timestamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
        safe_shop_name = shop_domain.replace(".", "-")
        output_path = EXPORTS_DIR / f"{safe_shop_name}-meta-catalog-{timestamp}.csv"
        write_meta_catalog_csv(rows, output_path)

        job_store.update(
            job_id,
            status="completed",
            file_name=output_path.name,
            file_path=str(output_path),
            row_count=len(rows),
            warning_count=len(warnings),
            warnings=warnings[:50],
            error=None,
        )
    except Exception as exc:  # pragma: no cover - surfaced to UI
        job_store.update(job_id, status="failed", error=str(exc))


def resolve_access_token(payload: ExportRequest, shop_domain: str) -> str:
    if payload.access_token and payload.access_token.strip():
        return payload.access_token.strip()
    if settings.default_access_token:
        return settings.default_access_token.strip()

    client_id = (payload.client_id or settings.default_client_id or "").strip()
    client_secret = (payload.client_secret or settings.default_client_secret or "").strip()
    if client_id and client_secret:
        return exchange_client_credentials(
            shop_domain=shop_domain,
            client_id=client_id,
            client_secret=client_secret,
            timeout_seconds=settings.request_timeout_seconds,
        )
    raise ValueError(
        "Missing Shopify credentials. Provide an Admin API access token, or a client_id and client_secret."
    )
