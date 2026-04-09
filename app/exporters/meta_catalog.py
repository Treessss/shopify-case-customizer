from __future__ import annotations

import csv
from io import StringIO
import json
import re
from decimal import Decimal
from html import unescape
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.domain import ProductRecord, ShopContext, VariantRecord


CSV_COLUMNS = [
    "id",
    "title",
    "description",
    "google product category",
    "product type",
    "link",
    "image link",
    "condition",
    "availability",
    "price",
    "sale price",
    "sale price effective date",
    "gtin",
    "brand",
    "mpn",
    "item group id",
    "gender",
    "age group",
    "color",
    "size",
    "material",
    "pattern",
    "additional_variant_attributes",
    "custom_data",
    "shipping",
    "shipping weight",
]

OPTION_FIELD_MAP = {
    "color": "color",
    "colour": "color",
    "color name": "color",
    "colour name": "color",
    "shade": "color",
    "tone": "color",
    "size": "size",
    "sizing": "size",
    "model": "size",
    "phone model": "size",
    "device": "size",
    "device model": "size",
    "capacity": "size",
    "gender": "gender",
    "sex": "gender",
    "age group": "age group",
    "age_group": "age group",
    "age": "age group",
    "material": "material",
    "fabric": "material",
    "composition": "material",
    "pattern": "pattern",
    "style": "pattern",
    "design": "pattern",
    "print": "pattern",
    "finish": "pattern",
    "case type": "pattern",
}

PRIMARY_OPTION_NAMES = {
    "color",
    "colour",
    "size",
    "gender",
    "age group",
    "material",
    "pattern",
}

DEFAULT_CONDITION = "new"


def build_meta_catalog_rows(
    shop: ShopContext,
    products: list[ProductRecord],
) -> tuple[list[dict[str, str]], list[str]]:
    rows: list[dict[str, str]] = []
    warnings: list[str] = []

    for product in products:
        product_image_urls = _unique_urls(product.images)
        base_description = _clean_description(product.description_html or product.description)

        for variant in product.variants:
            row_id = _build_variant_row_id(variant)
            link = _build_variant_link(shop.shop_domain, product, variant)
            image_urls = _merge_image_urls(_unique_urls(variant.images), product_image_urls)
            primary_image = image_urls[0] if image_urls else ""

            if not primary_image:
                warnings.append(
                    f"{product.title} / {variant.title or variant.legacy_id or variant.id} has no usable image."
                )
            if not link:
                warnings.append(
                    f"{product.title} / {variant.title or variant.legacy_id or variant.id} has no usable storefront link."
                )

            price, sale_price = _format_price_fields(
                variant.price,
                variant.compare_at_price,
                shop.currency_code,
            )
            option_values = _extract_option_values(variant)

            row = {column: "" for column in CSV_COLUMNS}
            row["id"] = row_id
            row["title"] = product.title.strip()
            row["description"] = base_description
            row["google product category"] = (product.category_full_name or "").strip()
            row["product type"] = (product.product_type or "").strip()
            row["link"] = link
            row["image link"] = primary_image
            row["condition"] = DEFAULT_CONDITION
            row["availability"] = "in stock" if variant.available_for_sale else "out of stock"
            row["price"] = price
            row["sale price"] = sale_price
            row["sale price effective date"] = ""
            row["gtin"] = (variant.barcode or "").strip()
            row["brand"] = (product.vendor or shop.store_name or "").strip()
            row["mpn"] = (variant.sku or "").strip()
            row["item group id"] = str(product.legacy_id or _fallback_gid(product.id))
            row["gender"] = option_values.get("gender", "")
            row["age group"] = option_values.get("age group", "")
            row["color"] = option_values.get("color", "")
            row["size"] = option_values.get("size", "")
            row["material"] = option_values.get("material", "")
            row["pattern"] = option_values.get("pattern", "")
            row["additional_variant_attributes"] = _build_additional_variant_attributes(variant)
            row["custom_data"] = _build_custom_data(variant)
            row["shipping"] = ""
            row["shipping weight"] = ""
            rows.append(row)

    return rows, warnings


def write_meta_catalog_csv(rows: list[dict[str, str]], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8-sig", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def render_meta_catalog_csv(rows: list[dict[str, str]]) -> str:
    buffer = StringIO()
    writer = csv.DictWriter(buffer, fieldnames=CSV_COLUMNS)
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def _build_variant_row_id(variant: VariantRecord) -> str:
    return str(variant.legacy_id or _fallback_gid(variant.id))


def _build_variant_link(shop_domain: str, product: ProductRecord, variant: VariantRecord) -> str:
    variant_suffix = variant.legacy_id or _fallback_gid(variant.id)
    if product.online_store_url:
        split_url = urlsplit(product.online_store_url)
        params = dict(parse_qsl(split_url.query, keep_blank_values=True))
        params["variant"] = str(variant_suffix)
        return urlunsplit(
            (
                split_url.scheme,
                split_url.netloc,
                split_url.path,
                urlencode(params),
                split_url.fragment,
            )
        )
    if product.handle:
        return f"https://{shop_domain}/products/{product.handle}?variant={variant_suffix}"
    return ""


def _format_price_fields(
    price: Decimal | None,
    compare_at_price: Decimal | None,
    currency_code: str,
) -> tuple[str, str]:
    if price is None:
        return "", ""
    if compare_at_price is not None and compare_at_price > price:
        return _money(compare_at_price, currency_code), _money(price, currency_code)
    return _money(price, currency_code), ""


def _money(amount: Decimal, currency_code: str) -> str:
    return f"{amount.quantize(Decimal('0.01'))} {currency_code}"


def _extract_option_values(variant: VariantRecord) -> dict[str, str]:
    values: dict[str, str] = {}
    priorities: dict[str, int] = {}
    for option in variant.selected_options:
        option_name = _normalize_option_name(option.name)
        mapped_key = OPTION_FIELD_MAP.get(option_name)
        if not mapped_key or not option.value.strip():
            continue
        priority = 2 if option_name in PRIMARY_OPTION_NAMES else 1
        if priority >= priorities.get(mapped_key, 0):
            values[mapped_key] = option.value.strip()
            priorities[mapped_key] = priority
    return values


def _build_custom_data(variant: VariantRecord) -> str:
    payload: dict[str, str] = {}
    for option in variant.selected_options:
        key = _normalize_custom_data_key(option.name)
        value = option.value.strip()
        if not key or not value:
            continue
        payload[key] = value
    if not payload:
        return ""
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _build_additional_variant_attributes(variant: VariantRecord) -> str:
    payload: dict[str, str] = {}
    for option in variant.selected_options:
        option_name = _normalize_option_name(option.name)
        if OPTION_FIELD_MAP.get(option_name):
            continue
        key = _normalize_custom_data_key(option.name)
        value = option.value.strip()
        if not key or not value:
            continue
        payload[key] = value
    if not payload:
        return ""
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _normalize_option_name(value: str) -> str:
    normalized = value.strip().lower().replace("_", " ").replace("-", " ")
    return re.sub(r"\s+", " ", normalized)


def _normalize_custom_data_key(value: str) -> str:
    normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
    normalized = re.sub(r"[^a-z0-9_]+", "_", normalized)
    normalized = re.sub(r"_+", "_", normalized).strip("_")
    return normalized


def _clean_description(raw_value: str) -> str:
    text = unescape(raw_value or "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _unique_urls(images) -> list[str]:
    seen: set[str] = set()
    urls: list[str] = []
    for image in images:
        url = (image.url or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        urls.append(url)
    return urls


def _merge_image_urls(primary: list[str], fallback: list[str]) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for bucket in (primary, fallback):
        for url in bucket:
            if url and url not in seen:
                seen.add(url)
                urls.append(url)
    return urls


def _fallback_gid(value: str) -> str:
    return value.rsplit("/", maxsplit=1)[-1]
