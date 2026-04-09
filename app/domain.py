from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal


@dataclass(slots=True)
class ImageRef:
    id: str | None
    url: str
    alt_text: str | None = None


@dataclass(slots=True)
class OptionValue:
    name: str
    value: str


@dataclass(slots=True)
class VariantRecord:
    id: str
    legacy_id: str | None
    title: str
    sku: str | None
    barcode: str | None
    price: Decimal | None
    compare_at_price: Decimal | None
    available_for_sale: bool
    inventory_quantity: int | None
    selected_options: list[OptionValue] = field(default_factory=list)
    images: list[ImageRef] = field(default_factory=list)


@dataclass(slots=True)
class ProductRecord:
    id: str
    legacy_id: str | None
    title: str
    handle: str
    description: str
    description_html: str
    online_store_url: str | None
    vendor: str | None
    product_type: str | None
    category_full_name: str | None
    status: str | None
    images: list[ImageRef] = field(default_factory=list)
    variants: list[VariantRecord] = field(default_factory=list)


@dataclass(slots=True)
class ShopContext:
    shop_domain: str
    currency_code: str
    store_name: str | None = None
