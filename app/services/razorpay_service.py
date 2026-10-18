from __future__ import annotations

import hashlib
import hmac

import razorpay

from app.core.config import settings


class RazorpayService:
    """Thin wrapper around the Razorpay SDK for subscription billing.

    Signature verification is implemented manually with hmac/hashlib against
    Razorpay's documented formulas, rather than relying on the SDK's
    `utility.verify_*` helpers, so behavior doesn't depend on which SDK
    version is installed.
    """

    def __init__(self) -> None:
        self._client: razorpay.Client | None = None

    def _get_client(self) -> razorpay.Client:
        if not settings.has_razorpay:
            raise RuntimeError("RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET must be set.")
        if self._client is None:
            self._client = razorpay.Client(auth=(settings.razorpay_key_id, settings.razorpay_key_secret))
        return self._client

    def create_customer(self, *, name: str, email: str, notes: dict) -> dict:
        client = self._get_client()
        return client.customer.create(
            {
                "name": name or email or "PropCore customer",
                "email": email,
                "notes": notes,
                "fail_existing": "0",
            }
        )

    def create_subscription(
        self,
        *,
        plan_id: str,
        customer_id: str,
        notes: dict | None = None,
        total_count: int = 120,
    ) -> dict:
        client = self._get_client()
        return client.subscription.create(
            {
                "plan_id": plan_id,
                "customer_id": customer_id,
                "total_count": total_count,
                "customer_notify": 1,
                "notes": notes or {},
            }
        )

    def create_plan(self, *, name: str, amount_paise: int, interval: str, description: str = "") -> dict:
        """Creates a Razorpay Plan. `interval` is 'monthly' or 'yearly'; used
        by the sync_razorpay_plans.py helper script, not by the request path."""
        client = self._get_client()
        period = "yearly" if interval == "yearly" else "monthly"
        return client.plan.create(
            {
                "period": period,
                "interval": 1,
                "item": {
                    "name": name,
                    "amount": amount_paise,
                    "currency": "INR",
                    "description": description,
                },
            }
        )

    def verify_subscription_payment_signature(
        self,
        *,
        razorpay_payment_id: str,
        razorpay_subscription_id: str,
        razorpay_signature: str,
    ) -> bool:
        """Per Razorpay docs: signature = HMAC_SHA256(key_secret,
        payment_id + "|" + subscription_id)."""
        if not settings.razorpay_key_secret:
            return False
        message = f"{razorpay_payment_id}|{razorpay_subscription_id}".encode("utf-8")
        expected = hmac.new(settings.razorpay_key_secret.encode("utf-8"), message, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, razorpay_signature)

    def verify_webhook_signature(self, *, raw_body: bytes, signature: str) -> bool:
        """Per Razorpay docs: signature = HMAC_SHA256(webhook_secret, raw_request_body)."""
        if not settings.razorpay_webhook_secret or not signature:
            return False
        expected = hmac.new(
            settings.razorpay_webhook_secret.encode("utf-8"), raw_body, hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, signature)


razorpay_service = RazorpayService()
