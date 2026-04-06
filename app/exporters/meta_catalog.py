from __future__ import annotations

import csv
import re
from decimal import Decimal
from html import unescape
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.domain import ProductRecord, ShopContext, VariantRecord


CSV_COLUMNS = [
    "id",
    "item_group_id",
    "title",
    "description",
    "availability",
    "condition",
    "price",
    "sale_price",
    "link",
    "image_link",
    "additional_image_link",
    "brand",
    "google_product_category",
    "product_type",
    "color",
    "size",
    "material",
    "pattern",
    "gtin",
    "mpn",
]

OPTION_FIELD_MAP = {
    "color": "color",
    "colour": "color",
    "size": "size",
    "material": "material",
    "pattern": "pattern",
}

DEFAULT_CONDITION = "new"


def build_meta_catalog_rows(
    shop: ShopContext,
    products: list[ProductRecord],
) -> tuple[list[dict[str, str]], list[str]]:
    rows: list[dict[str, str]] = []
    warnings: list[str] = []
    used_ids: set[str] = set()

    for product in products:
        product_image_urls = _unique_urls(product.images)
        base_description = _clean_description(product.description_html or product.description)

        for variant in product.variants:
            row_id = _build_unique_row_id(variant, used_ids)
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
            row["item_group_id"] = str(product.legacy_id or _fallback_gid(product.id))
            row["title"] = _build_variant_title(product.title, variant)
            row["description"] = base_description
            row["availability"] = "in stock" if variant.available_for_sale else "out of stock"
            row["condition"] = DEFAULT_CONDITION
            row["price"] = price
            row["sale_price"] = sale_price
            row["link"] = link
            row["image_link"] = primary_image
            row["additional_image_link"] = ",".join(image_urls[1:])
            row["brand"] = (product.vendor or "").strip()
            row["google_product_category"] = (product.category_full_name or "").strip()
            row["product_type"] = (product.product_type or "").strip()
            row["color"] = option_values.get("color", "")
            row["size"] = option_values.get("size", "")
            row["material"] = option_values.get("material", "")
            row["pattern"] = option_values.get("pattern", "")
            row["gtin"] = (variant.barcode or "").strip()
            row["mpn"] = (variant.sku or "").strip()
            rows.append(row)

    return rows, warnings


def write_meta_catalog_csv(rows: list[dict[str, str]], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8-sig", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def _build_unique_row_id(variant: VariantRecord, used_ids: set[str]) -> str:
    preferred = (variant.sku or "").strip() or (variant.legacy_id or _fallback_gid(variant.id))
    candidate = preferred
    if candidate not in used_ids:
        used_ids.add(candidate)
        return candidate

    suffix = variant.legacy_id or _fallback_gid(variant.id)
    candidate = f"{preferred}-{suffix}"
    used_ids.add(candidate)
    return candidate


def _build_variant_title(product_title: str, variant: VariantRecord) -> str:
    option_values = [option.value.strip() for option in variant.selected_options if option.value.strip()]
    normalized_variant_title = (variant.title or "").strip()
    if normalized_variant_title.lower() == "default title" or not normalized_variant_title:
        return product_title.strip()
    if option_values:
        return f"{product_title.strip()} - {' / '.join(option_values)}"
    return f"{product_title.strip()} - {normalized_variant_title}"


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
    for option in variant.selected_options:
        option_name = option.name.strip().lower()
        mapped_key = OPTION_FIELD_MAP.get(option_name)
        if mapped_key and option.value.strip():
            values[mapped_key] = option.value.strip()
    return values


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
