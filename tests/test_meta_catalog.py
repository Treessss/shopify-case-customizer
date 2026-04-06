from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from app.domain import ImageRef, OptionValue, ProductRecord, ShopContext, VariantRecord
from app.exporters.meta_catalog import build_meta_catalog_rows, write_meta_catalog_csv


class MetaCatalogExporterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.shop = ShopContext(
            shop_domain="demo-store.myshopify.com",
            currency_code="USD",
            store_name="Demo Store",
        )

    def test_variant_image_takes_priority_over_product_image(self) -> None:
        product = ProductRecord(
            id="gid://shopify/Product/1",
            legacy_id="1",
            title="Trail Shoe",
            handle="trail-shoe",
            description="",
            description_html="<p>Lightweight and fast</p>",
            online_store_url="https://demo-store.com/products/trail-shoe",
            vendor="Northwind",
            product_type="Shoes",
            category_full_name="Apparel & Accessories > Shoes",
            status="ACTIVE",
            images=[ImageRef(id="p1", url="https://cdn.example.com/product.jpg")],
            variants=[
                VariantRecord(
                    id="gid://shopify/ProductVariant/11",
                    legacy_id="11",
                    title="Blue / 42",
                    sku="SKU-42",
                    barcode="1234567890123",
                    price=Decimal("89.90"),
                    compare_at_price=Decimal("99.90"),
                    available_for_sale=True,
                    inventory_quantity=8,
                    selected_options=[
                        OptionValue(name="Color", value="Blue"),
                        OptionValue(name="Size", value="42"),
                    ],
                    images=[ImageRef(id="v1", url="https://cdn.example.com/variant.jpg")],
                )
            ],
        )

        rows, warnings = build_meta_catalog_rows(self.shop, [product])

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["image_link"], "https://cdn.example.com/variant.jpg")
        self.assertEqual(rows[0]["additional_image_link"], "https://cdn.example.com/product.jpg")
        self.assertEqual(rows[0]["price"], "99.90 USD")
        self.assertEqual(rows[0]["sale_price"], "89.90 USD")
        self.assertEqual(rows[0]["color"], "Blue")
        self.assertEqual(rows[0]["size"], "42")
        self.assertEqual(warnings, [])

    def test_default_title_variant_uses_product_title(self) -> None:
        product = ProductRecord(
            id="gid://shopify/Product/2",
            legacy_id="2",
            title="Beanie",
            handle="beanie",
            description="Classic beanie",
            description_html="Classic beanie",
            online_store_url=None,
            vendor="Northwind",
            product_type="Accessories",
            category_full_name=None,
            status="ACTIVE",
            images=[],
            variants=[
                VariantRecord(
                    id="gid://shopify/ProductVariant/21",
                    legacy_id="21",
                    title="Default Title",
                    sku=None,
                    barcode=None,
                    price=Decimal("15"),
                    compare_at_price=None,
                    available_for_sale=False,
                    inventory_quantity=0,
                    selected_options=[],
                    images=[],
                )
            ],
        )

        rows, warnings = build_meta_catalog_rows(self.shop, [product])

        self.assertEqual(rows[0]["title"], "Beanie")
        self.assertEqual(rows[0]["link"], "https://demo-store.myshopify.com/products/beanie?variant=21")
        self.assertEqual(rows[0]["availability"], "out of stock")
        self.assertEqual(len(warnings), 1)
        self.assertIn("has no usable image", warnings[0])

    def test_csv_writer_outputs_expected_headers(self) -> None:
        rows = [
            {
                "id": "SKU-1",
                "item_group_id": "100",
                "title": "Hat",
                "description": "Warm hat",
                "availability": "in stock",
                "condition": "new",
                "price": "10.00 USD",
                "sale_price": "",
                "link": "https://example.com",
                "image_link": "https://image.example.com/1.jpg",
                "additional_image_link": "",
                "brand": "Northwind",
                "google_product_category": "",
                "product_type": "Accessories",
                "color": "",
                "size": "",
                "material": "",
                "pattern": "",
                "gtin": "",
                "mpn": "SKU-1",
            }
        ]

        with TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "catalog.csv"
            write_meta_catalog_csv(rows, path)
            content = path.read_text(encoding="utf-8-sig")

        self.assertIn("id,item_group_id,title,description,availability", content)
        self.assertIn("SKU-1", content)


if __name__ == "__main__":
    unittest.main()
