from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
RUNTIME_MODE = os.getenv("APP_RUNTIME_MODE") or ("serverless" if os.getenv("VERCEL") else "server")
EXPORTS_DIR = Path(
    os.getenv("EXPORTS_DIR") or ("/tmp/exports" if RUNTIME_MODE == "serverless" else str(BASE_DIR / "exports"))
)


def load_dotenv(dotenv_path: Path) -> None:
    if not dotenv_path.exists():
        return
    for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", maxsplit=1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


load_dotenv(BASE_DIR / ".env")


def load_shopify_app_client_id() -> str | None:
    config_path = BASE_DIR / "shopify.app.toml"
    if not config_path.exists():
        return None
    try:
        parsed = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return None
    client_id = parsed.get("client_id")
    return client_id.strip() if isinstance(client_id, str) and client_id.strip() else None


@dataclass(slots=True)
class Settings:
    runtime_mode: str = RUNTIME_MODE
    default_shop_domain: str | None = os.getenv("SHOPIFY_SHOP_DOMAIN")
    default_access_token: str | None = os.getenv("SHOPIFY_ACCESS_TOKEN")
    default_client_id: str | None = (
        os.getenv("SHOPIFY_CLIENT_ID") or os.getenv("SHOPIFY_API_KEY") or load_shopify_app_client_id()
    )
    default_client_secret: str | None = os.getenv("SHOPIFY_CLIENT_SECRET") or os.getenv(
        "SHOPIFY_API_SECRET"
    )
    default_api_version: str = os.getenv("SHOPIFY_API_VERSION", "2026-04")
    request_timeout_seconds: int = int(os.getenv("SHOPIFY_REQUEST_TIMEOUT_SECONDS", "45"))
    bulk_poll_interval_seconds: float = float(os.getenv("SHOPIFY_BULK_POLL_INTERVAL_SECONDS", "2.0"))
    bulk_max_wait_seconds: int = int(os.getenv("SHOPIFY_BULK_MAX_WAIT_SECONDS", "600"))
    default_product_query: str = os.getenv("SHOPIFY_DEFAULT_PRODUCT_QUERY", "status:active")

    @property
    def uses_inline_exports(self) -> bool:
        return self.runtime_mode == "serverless"


settings = Settings()
EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
