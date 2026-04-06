from datetime import datetime, timedelta, timezone
import unittest

import jwt

from jwt import InvalidTokenError

from app.embedded import decode_shopify_id_token


class EmbeddedTokenTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client_id = "client-id"
        self.client_secret = "client-secret"
        self.shop_domain = "example-shop.myshopify.com"

    def _issue_token(self, *, shop_domain: str | None = None) -> str:
        now = datetime.now(timezone.utc)
        shop = shop_domain or self.shop_domain
        payload = {
            "iss": f"https://{shop}/admin",
            "dest": f"https://{shop}",
            "aud": self.client_id,
            "sub": "42",
            "exp": int((now + timedelta(minutes=5)).timestamp()),
            "nbf": int((now - timedelta(seconds=5)).timestamp()),
            "iat": int(now.timestamp()),
            "jti": "token-id",
            "sid": "session-id",
        }
        return jwt.encode(payload, self.client_secret, algorithm="HS256")

    def test_decodes_valid_token(self) -> None:
        token = self._issue_token()

        claims = decode_shopify_id_token(
            token,
            client_id=self.client_id,
            client_secret=self.client_secret,
            expected_shop=self.shop_domain,
        )

        self.assertEqual(claims["aud"], self.client_id)
        self.assertEqual(claims["dest"], f"https://{self.shop_domain}")

    def test_rejects_token_for_different_shop(self) -> None:
        token = self._issue_token(shop_domain="different-shop.myshopify.com")

        with self.assertRaises(InvalidTokenError):
            decode_shopify_id_token(
                token,
                client_id=self.client_id,
                client_secret=self.client_secret,
                expected_shop=self.shop_domain,
            )


if __name__ == "__main__":
    unittest.main()
