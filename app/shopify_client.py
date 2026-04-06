from __future__ import annotations

import json
import time
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from app.domain import ImageRef, OptionValue, ProductRecord, ShopContext, VariantRecord


USER_AGENT = "shopify-facebook-catalog-exporter/1.0"


def normalize_shop_domain(raw_value: str) -> str:
    value = (raw_value or "").strip()
    value = value.removeprefix("https://").removeprefix("http://")
    value = value.split("/", 1)[0]
    if not value:
        raise ValueError("shop_domain is required")
    if "." not in value:
        value = f"{value}.myshopify.com"
    return value.lower()


def exchange_client_credentials(
    shop_domain: str,
    client_id: str,
    client_secret: str,
    timeout_seconds: int,
) -> str:
    payload = urlencode(
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "client_credentials",
        }
    ).encode("utf-8")
    request = Request(
        url=f"https://{normalize_shop_domain(shop_domain)}/admin/oauth/access_token",
        data=payload,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": USER_AGENT,
        },
        method="POST",
    )
    with urlopen(request, timeout=timeout_seconds) as response:
        body = json.loads(response.read().decode("utf-8"))

    access_token = body.get("access_token")
    if not access_token:
        raise RuntimeError(f"Unable to exchange client credentials: {body}")
    return access_token


@dataclass(slots=True)
class BulkOperationState:
    id: str
    status: str
    error_code: str | None
    url: str | None
    partial_data_url: str | None
    object_count: str | None


class ShopifyGraphQLClient:
    def __init__(
        self,
        shop_domain: str,
        access_token: str,
        api_version: str,
        timeout_seconds: int,
    ) -> None:
        self.shop_domain = normalize_shop_domain(shop_domain)
        self.access_token = access_token.strip()
        self.api_version = api_version
        self.timeout_seconds = timeout_seconds
        self.endpoint = (
            f"https://{self.shop_domain}/admin/api/{self.api_version}/graphql.json"
        )

    def graphql(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = json.dumps({"query": query, "variables": variables or {}}).encode("utf-8")
        request = Request(
            url=self.endpoint,
            data=payload,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "X-Shopify-Access-Token": self.access_token,
                "User-Agent": USER_AGENT,
            },
            method="POST",
        )
        with urlopen(request, timeout=self.timeout_seconds) as response:
            body = json.loads(response.read().decode("utf-8"))

        if body.get("errors"):
            raise RuntimeError(f"Shopify GraphQL errors: {body['errors']}")
        data = body.get("data") or {}
        return data

    def fetch_shop_context(self) -> ShopContext:
        data = self.graphql(
            """
            query ShopContext {
              shop {
                name
                currencyCode
              }
            }
            """
        )
        shop = data["shop"]
        return ShopContext(
            shop_domain=self.shop_domain,
            currency_code=shop["currencyCode"],
            store_name=shop.get("name"),
        )

    def fetch_products_direct(self, product_query: str) -> tuple[ShopContext, list[ProductRecord]]:
        shop_context = self.fetch_shop_context()
        products: list[ProductRecord] = []
        cursor: str | None = None

        query = """
        query ProductsPage($first: Int!, $after: String, $query: String) {
          products(first: $first, after: $after, query: $query, sortKey: ID) {
            pageInfo {
              hasNextPage
              endCursor
            }
            nodes {
              __typename
              id
              legacyResourceId
              title
              handle
              description
              descriptionHtml
              onlineStoreUrl
              vendor
              productType
              status
              category {
                fullName
              }
              media(first: 20) {
                nodes {
                  __typename
                  ... on MediaImage {
                    id
                    alt
                    image {
                      altText
                      url
                    }
                  }
                }
              }
              variants(first: 250) {
                nodes {
                  __typename
                  id
                  legacyResourceId
                  title
                  sku
                  barcode
                  price
                  compareAtPrice
                  availableForSale
                  inventoryQuantity
                  selectedOptions {
                    name
                    value
                  }
                  media(first: 10) {
                    nodes {
                      __typename
                      ... on MediaImage {
                        id
                        alt
                        image {
                          altText
                          url
                        }
                      }
                    }
                  }
                }
              }
            }
          }
        }
        """

        while True:
            data = self.graphql(
                query,
                {"first": 25, "after": cursor, "query": product_query},
            )
            connection = data["products"]
            for node in connection["nodes"]:
                products.append(self._parse_product_node(node))
            if not connection["pageInfo"]["hasNextPage"]:
                break
            cursor = connection["pageInfo"]["endCursor"]

        return shop_context, products

    def fetch_products_bulk(
        self,
        product_query: str,
        poll_interval_seconds: float,
        max_wait_seconds: int,
    ) -> tuple[ShopContext, list[ProductRecord]]:
        shop_context = self.fetch_shop_context()
        operation_id = self.start_bulk_export(product_query)
        state = self.wait_for_bulk_completion(
            operation_id,
            poll_interval_seconds=poll_interval_seconds,
            max_wait_seconds=max_wait_seconds,
        )
        if not state.url:
            raise RuntimeError("Bulk operation completed without a download URL.")
        return shop_context, self.parse_bulk_jsonl(self.download_jsonl(state.url))

    def start_bulk_export(self, product_query: str) -> str:
        bulk_query = self._build_bulk_query(product_query)
        data = self.graphql(
            """
            mutation StartBulkExport($query: String!) {
              bulkOperationRunQuery(query: $query, groupObjects: false) {
                bulkOperation {
                  id
                  status
                }
                userErrors {
                  field
                  message
                }
              }
            }
            """,
            {"query": bulk_query},
        )
        payload = data["bulkOperationRunQuery"]
        user_errors = payload.get("userErrors") or []
        if user_errors:
            raise RuntimeError(f"Bulk operation failed to start: {user_errors}")
        operation = payload.get("bulkOperation")
        if not operation:
            raise RuntimeError("Bulk operation did not return an operation id.")
        return operation["id"]

    def wait_for_bulk_completion(
        self,
        operation_id: str,
        poll_interval_seconds: float,
        max_wait_seconds: int,
    ) -> BulkOperationState:
        started_at = time.monotonic()
        while True:
            state = self.fetch_bulk_operation_state(operation_id)
            if state.status == "COMPLETED":
                return state
            if state.status in {"FAILED", "CANCELED", "CANCELING", "EXPIRED"}:
                raise RuntimeError(
                    "Bulk operation failed "
                    f"(status={state.status}, error_code={state.error_code}, "
                    f"partial_data_url={state.partial_data_url})"
                )
            if (time.monotonic() - started_at) > max_wait_seconds:
                raise TimeoutError(
                    f"Bulk export timed out after {max_wait_seconds} seconds."
                )
            time.sleep(poll_interval_seconds)

    def fetch_bulk_operation_state(self, operation_id: str) -> BulkOperationState:
        data = self.graphql(
            """
            query BulkState($id: ID!) {
              node(id: $id) {
                ... on BulkOperation {
                  id
                  status
                  errorCode
                  url
                  partialDataUrl
                  objectCount
                }
              }
            }
            """,
            {"id": operation_id},
        )
        operation = data.get("node")
        if not operation:
            raise RuntimeError(f"Bulk operation {operation_id} was not found.")
        return BulkOperationState(
            id=operation["id"],
            status=operation["status"],
            error_code=operation.get("errorCode"),
            url=operation.get("url"),
            partial_data_url=operation.get("partialDataUrl"),
            object_count=operation.get("objectCount"),
        )

    def download_jsonl(self, url: str) -> Iterable[dict[str, Any]]:
        request = Request(url, headers={"User-Agent": USER_AGENT})
        with urlopen(request, timeout=self.timeout_seconds) as response:
            for raw_line in response:
                line = raw_line.decode("utf-8").strip()
                if line:
                    yield json.loads(line)

    def parse_bulk_jsonl(self, items: Iterable[dict[str, Any]]) -> list[ProductRecord]:
        products: dict[str, ProductRecord] = {}
        variants: dict[str, VariantRecord] = {}
        product_variant_ids: dict[str, list[str]] = defaultdict(list)
        images_by_parent: dict[str, list[ImageRef]] = defaultdict(list)

        for item in items:
            node_type = item.get("__typename")
            parent_id = item.get("__parentId")

            if node_type == "Product":
                products[item["id"]] = ProductRecord(
                    id=item["id"],
                    legacy_id=_string_or_none(item.get("legacyResourceId")),
                    title=item.get("title") or "",
                    handle=item.get("handle") or "",
                    description=item.get("description") or "",
                    description_html=item.get("descriptionHtml") or "",
                    online_store_url=item.get("onlineStoreUrl"),
                    vendor=item.get("vendor"),
                    product_type=item.get("productType"),
                    category_full_name=(item.get("category") or {}).get("fullName"),
                    status=item.get("status"),
                )
            elif node_type == "ProductVariant":
                variant = VariantRecord(
                    id=item["id"],
                    legacy_id=_string_or_none(item.get("legacyResourceId")),
                    title=item.get("title") or "",
                    sku=item.get("sku"),
                    barcode=item.get("barcode"),
                    price=_to_decimal(item.get("price")),
                    compare_at_price=_to_decimal(item.get("compareAtPrice")),
                    available_for_sale=bool(item.get("availableForSale")),
                    inventory_quantity=item.get("inventoryQuantity"),
                    selected_options=[
                        OptionValue(name=option.get("name") or "", value=option.get("value") or "")
                        for option in item.get("selectedOptions") or []
                    ],
                )
                variants[variant.id] = variant
                if parent_id:
                    product_variant_ids[parent_id].append(variant.id)
            elif node_type == "MediaImage" and parent_id:
                image = _parse_image(item)
                if image:
                    images_by_parent[parent_id].append(image)

        for product_id, product in products.items():
            product.images = images_by_parent.get(product_id, [])
            product.variants = []
            for variant_id in product_variant_ids.get(product_id, []):
                variant = variants[variant_id]
                variant.images = images_by_parent.get(variant_id, [])
                product.variants.append(variant)

        return list(products.values())

    def _parse_product_node(self, node: dict[str, Any]) -> ProductRecord:
        product = ProductRecord(
            id=node["id"],
            legacy_id=_string_or_none(node.get("legacyResourceId")),
            title=node.get("title") or "",
            handle=node.get("handle") or "",
            description=node.get("description") or "",
            description_html=node.get("descriptionHtml") or "",
            online_store_url=node.get("onlineStoreUrl"),
            vendor=node.get("vendor"),
            product_type=node.get("productType"),
            category_full_name=(node.get("category") or {}).get("fullName"),
            status=node.get("status"),
            images=[
                image
                for media_node in node.get("media", {}).get("nodes", [])
                for image in [_parse_image(media_node)]
                if image
            ],
            variants=[],
        )
        for variant_node in node.get("variants", {}).get("nodes", []):
            product.variants.append(
                VariantRecord(
                    id=variant_node["id"],
                    legacy_id=_string_or_none(variant_node.get("legacyResourceId")),
                    title=variant_node.get("title") or "",
                    sku=variant_node.get("sku"),
                    barcode=variant_node.get("barcode"),
                    price=_to_decimal(variant_node.get("price")),
                    compare_at_price=_to_decimal(variant_node.get("compareAtPrice")),
                    available_for_sale=bool(variant_node.get("availableForSale")),
                    inventory_quantity=variant_node.get("inventoryQuantity"),
                    selected_options=[
                        OptionValue(
                            name=option.get("name") or "",
                            value=option.get("value") or "",
                        )
                        for option in variant_node.get("selectedOptions") or []
                    ],
                    images=[
                        image
                        for media_node in variant_node.get("media", {}).get("nodes", [])
                        for image in [_parse_image(media_node)]
                        if image
                    ],
                )
            )
        return product

    @staticmethod
    def _build_bulk_query(product_query: str) -> str:
        query_literal = json.dumps(product_query)
        return f"""
        {{
          products(first: 250, query: {query_literal}, sortKey: ID) {{
            edges {{
              node {{
                __typename
                id
                legacyResourceId
                title
                handle
                description
                descriptionHtml
                onlineStoreUrl
                vendor
                productType
                status
                category {{
                  fullName
                }}
                media(first: 20) {{
                  edges {{
                    node {{
                      __typename
                      ... on MediaImage {{
                        id
                        alt
                        image {{
                          altText
                          url
                        }}
                      }}
                    }}
                  }}
                }}
                variants(first: 250) {{
                  edges {{
                    node {{
                      __typename
                      id
                      legacyResourceId
                      title
                      sku
                      barcode
                      price
                      compareAtPrice
                      availableForSale
                      inventoryQuantity
                      selectedOptions {{
                        name
                        value
                      }}
                      media(first: 10) {{
                        edges {{
                          node {{
                            __typename
                            ... on MediaImage {{
                              id
                              alt
                              image {{
                                altText
                                url
                              }}
                            }}
                          }}
                        }}
                      }}
                    }}
                  }}
                }}
              }}
            }}
          }}
        }}
        """.strip()


def _parse_image(node: dict[str, Any]) -> ImageRef | None:
    image_payload = node.get("image") or {}
    url = image_payload.get("url")
    if not url:
        return None
    alt_text = image_payload.get("altText") or node.get("alt")
    return ImageRef(id=node.get("id"), url=url, alt_text=alt_text)


def _to_decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _string_or_none(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value)
