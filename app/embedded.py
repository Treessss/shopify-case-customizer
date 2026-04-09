from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

import jwt
from fastapi import HTTPException, Request, status
from jwt import InvalidTokenError

from app.shopify_client import normalize_shop_domain


SHOP_COOKIE_NAME = "shopify_shop"
HOST_COOKIE_NAME = "shopify_host"


@dataclass(slots=True)
class EmbeddedAppContext:
    shop_domain: str | None
    host: str | None
    embedded: bool
    api_key: str | None


def build_embedded_context(
    request: Request,
    *,
    api_key: str | None,
    default_shop_domain: str | None,
) -> EmbeddedAppContext:
    query_shop = _normalize_shop_or_none(request.query_params.get("shop"))
    cookie_shop = _normalize_shop_or_none(request.cookies.get(SHOP_COOKIE_NAME))
    default_shop = _normalize_shop_or_none(default_shop_domain)

    host = (request.query_params.get("host") or request.cookies.get(HOST_COOKIE_NAME) or "").strip()
    embedded_flag = request.query_params.get("embedded") == "1" or bool(host)

    return EmbeddedAppContext(
        shop_domain=query_shop or cookie_shop or default_shop,
        host=host or None,
        embedded=embedded_flag,
        api_key=(api_key or "").strip() or None,
    )


def apply_iframe_protection_headers(response, context: EmbeddedAppContext) -> None:
    shop_domain = context.shop_domain or "*.myshopify.com"
    response.headers["Content-Security-Policy"] = (
        f"frame-ancestors https://{shop_domain} https://admin.shopify.com;"
    )
    if "X-Frame-Options" in response.headers:
        del response.headers["X-Frame-Options"]

    if context.shop_domain:
        response.set_cookie(
            SHOP_COOKIE_NAME,
            context.shop_domain,
            httponly=True,
            samesite="lax",
        )
    if context.host:
        response.set_cookie(
            HOST_COOKIE_NAME,
            context.host,
            httponly=True,
            samesite="lax",
        )


def verify_embedded_admin_request(
    request: Request,
    *,
    client_id: str | None,
    client_secret: str | None,
    expected_shop: str | None = None,
) -> str:
    normalized_expected_shop = _normalize_shop_or_none(expected_shop)
    bearer_token = _extract_bearer_token(request)

    # If the app secret isn't configured yet, keep local development usable.
    if not client_id or not client_secret:
        if normalized_expected_shop:
            return normalized_expected_shop
        fallback_shop = _normalize_shop_or_none(request.query_params.get("shop"))
        if fallback_shop:
            return fallback_shop
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Shopify embedded auth is not configured on the server.",
        )

    if not bearer_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Shopify session token.",
        )

    try:
        claims = decode_shopify_id_token(
            bearer_token,
            client_id=client_id,
            client_secret=client_secret,
            expected_shop=normalized_expected_shop,
        )
    except InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid Shopify session token: {exc}",
        ) from exc

    resolved_shop = _normalize_shop_or_none(claims.get("dest")) or _normalize_shop_or_none(claims.get("iss"))
    if not resolved_shop:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Shopify session token does not include a valid shop domain.",
        )
    return resolved_shop


def decode_shopify_id_token(
    token: str,
    *,
    client_id: str,
    client_secret: str,
    expected_shop: str | None = None,
) -> dict:
    claims = jwt.decode(
        token,
        client_secret,
        algorithms=["HS256"],
        audience=client_id,
        options={
            "require": ["aud", "dest", "exp", "iat", "iss", "nbf", "sub"],
        },
    )

    normalized_expected_shop = _normalize_shop_or_none(expected_shop)
    normalized_dest = _normalize_shop_or_none(claims.get("dest"))
    normalized_issuer_shop = _normalize_shop_or_none(claims.get("iss"))
    issuer_value = str(claims.get("iss") or "")

    if normalized_expected_shop and normalized_dest != normalized_expected_shop:
        raise InvalidTokenError("Token shop does not match the current embedded shop.")

    if not issuer_value.endswith("/admin"):
        raise InvalidTokenError("Token issuer is not a Shopify admin origin.")

    if normalized_issuer_shop and normalized_dest and normalized_issuer_shop != normalized_dest:
        raise InvalidTokenError("Token issuer and destination shop do not match.")

    return claims


def _extract_bearer_token(request: Request) -> str | None:
    authorization = request.headers.get("Authorization") or ""
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


def _normalize_shop_or_none(raw_value: str | None) -> str | None:
    if not raw_value:
        return None
    value = str(raw_value).strip()
    if not value:
        return None
    if value.startswith(("https://", "http://")):
        value = urlsplit(value).netloc or value
    if "/admin" in value:
        value = value.split("/admin", maxsplit=1)[0]
    try:
        return normalize_shop_domain(value)
    except ValueError:
        return None
