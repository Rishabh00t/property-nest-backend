from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from supabase import Client, create_client

from app.core.config import settings
from app.services.razorpay_service import razorpay_service
from app.utils.auth import CurrentUser

# Razorpay does not guarantee webhook delivery ordering, but each event
# carries the subscription entity's own current status, so simply writing
# whatever status the latest-received event reports is idempotent and safe
# even if two events arrive out of order.


class BillingService:
    def __init__(self) -> None:
        self._supabase: Client | None = None

    def _get_supabase(self) -> Client:
        if not settings.has_supabase:
            raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set for billing.")
        if self._supabase is None:
            self._supabase = create_client(settings.supabase_url, settings.supabase_service_role_key)
        return self._supabase

    @staticmethod
    def _data(response: Any) -> Any:
        # postgrest-py's .maybe_single().execute() returns None outright
        # (not a response object with .data = None) when no row matches,
        # so every maybe_single() read must go through this instead of
        # touching `.data` directly.
        if response is None:
            return None
        return getattr(response, "data", None)

    # ---- Plans -------------------------------------------------------

    def list_active_plans(self) -> list[dict[str, Any]]:
        supabase = self._get_supabase()
        result = (
            supabase.table("billing_plans")
            .select("*")
            .eq("is_active", True)
            .order("amount_paise")
            .execute()
        )
        return result.data or []

    def get_plan(self, plan_id: str) -> dict[str, Any]:
        supabase = self._get_supabase()
        result = supabase.table("billing_plans").select("*").eq("id", plan_id).maybe_single().execute()
        data = self._data(result)
        if not data:
            raise ValueError(f"Plan {plan_id} was not found.")
        return data

    # ---- Subscribe -----------------------------------------------------

    def start_subscription(self, user: CurrentUser, plan_id: str) -> dict[str, Any]:
        supabase = self._get_supabase()
        plan = self.get_plan(plan_id)
        if not plan.get("razorpay_plan_id"):
            raise ValueError("This plan isn't configured for checkout yet. Please contact support.")

        existing = (
            supabase.table("subscriptions")
            .select("razorpay_customer_id")
            .eq("profile_id", user.id)
            .maybe_single()
            .execute()
        )
        razorpay_customer_id = (self._data(existing) or {}).get("razorpay_customer_id")

        if not razorpay_customer_id:
            customer = razorpay_service.create_customer(
                name=user.company_name or user.email or user.id,
                email=user.email or "",
                notes={"profile_id": user.id},
            )
            razorpay_customer_id = customer["id"]

        subscription = razorpay_service.create_subscription(
            plan_id=plan["razorpay_plan_id"],
            customer_id=razorpay_customer_id,
            notes={"profile_id": user.id, "internal_plan_id": plan_id},
        )

        row = {
            "profile_id": user.id,
            "plan_id": plan_id,
            "razorpay_customer_id": razorpay_customer_id,
            "razorpay_subscription_id": subscription["id"],
            "status": subscription.get("status", "created"),
        }
        supabase.table("subscriptions").upsert(row, on_conflict="profile_id").execute()

        return {
            "subscription_id": subscription["id"],
            "razorpay_subscription_id": subscription["id"],
            "razorpay_key_id": settings.razorpay_key_id,
        }

    # ---- Verify (fast-path UX only — the webhook is the source of truth) --

    def verify_payment(
        self, user: CurrentUser, payment_id: str, subscription_id: str, signature: str
    ) -> bool:
        if not razorpay_service.verify_subscription_payment_signature(
            razorpay_payment_id=payment_id,
            razorpay_subscription_id=subscription_id,
            razorpay_signature=signature,
        ):
            return False

        supabase = self._get_supabase()
        # Scoped to this user's own subscription row so a stolen/replayed
        # payload can't be used to flip someone else's subscription active.
        supabase.table("subscriptions").update({"status": "active"}).eq(
            "razorpay_subscription_id", subscription_id
        ).eq("profile_id", user.id).execute()
        return True

    # ---- Webhook (source of truth for all state changes) ------------------

    def process_webhook(self, raw_body: bytes, payload: dict[str, Any], event_id: str | None) -> None:
        supabase = self._get_supabase()
        event_type = payload.get("event", "")
        resolved_event_id = event_id or hashlib.sha256(raw_body).hexdigest()

        already = (
            supabase.table("razorpay_webhook_events")
            .select("id")
            .eq("id", resolved_event_id)
            .maybe_single()
            .execute()
        )
        if self._data(already):
            return  # Already processed this exact event — no-op for idempotency.

        entity = payload.get("payload", {})
        subscription_entity = (entity.get("subscription") or {}).get("entity")
        payment_entity = (entity.get("payment") or {}).get("entity")

        subscription_row: dict[str, Any] | None = None
        if subscription_entity:
            rp_sub_id = subscription_entity.get("id")
            update: dict[str, Any] = {"status": subscription_entity.get("status")}
            current_end = subscription_entity.get("current_end")
            if current_end:
                update["current_period_end"] = datetime.fromtimestamp(
                    current_end, tz=timezone.utc
                ).isoformat()

            updated = (
                supabase.table("subscriptions")
                .update(update)
                .eq("razorpay_subscription_id", rp_sub_id)
                .execute()
            )
            subscription_row = (updated.data or [None])[0]
            if subscription_row is None:
                lookup = (
                    supabase.table("subscriptions")
                    .select("id, profile_id")
                    .eq("razorpay_subscription_id", rp_sub_id)
                    .maybe_single()
                    .execute()
                )
                subscription_row = self._data(lookup)

        if event_type == "subscription.charged" and payment_entity and subscription_row:
            supabase.table("invoices").insert(
                {
                    "subscription_id": subscription_row.get("id"),
                    "profile_id": subscription_row.get("profile_id"),
                    "razorpay_payment_id": payment_entity.get("id"),
                    "amount_paise": payment_entity.get("amount"),
                    "currency": payment_entity.get("currency", "INR"),
                    "status": "paid",
                    "paid_at": datetime.now(tz=timezone.utc).isoformat(),
                }
            ).execute()
        elif event_type == "payment.failed" and payment_entity:
            notes = payment_entity.get("notes") or {}
            profile_id = notes.get("profile_id") or (subscription_row or {}).get("profile_id")
            if profile_id:
                supabase.table("invoices").insert(
                    {
                        "subscription_id": (subscription_row or {}).get("id"),
                        "profile_id": profile_id,
                        "razorpay_payment_id": payment_entity.get("id"),
                        "amount_paise": payment_entity.get("amount", 0),
                        "currency": payment_entity.get("currency", "INR"),
                        "status": "failed",
                    }
                ).execute()

        supabase.table("razorpay_webhook_events").insert(
            {"id": resolved_event_id, "event_type": event_type, "payload": payload}
        ).execute()

    # ---- Me ---------------------------------------------------------------

    def get_my_billing(self, user: CurrentUser) -> dict[str, Any]:
        supabase = self._get_supabase()
        subscription = (
            supabase.table("subscriptions")
            .select("*, billing_plans(*)")
            .eq("profile_id", user.id)
            .maybe_single()
            .execute()
        )
        invoices = (
            supabase.table("invoices")
            .select("*")
            .eq("profile_id", user.id)
            .order("created_at", desc=True)
            .limit(20)
            .execute()
        )
        return {"subscription": self._data(subscription), "invoices": self._data(invoices) or []}

    # ---- Super-admin overview ----------------------------------------------

    def get_admin_overview(self) -> dict[str, Any]:
        supabase = self._get_supabase()

        active = (
            supabase.table("subscriptions")
            .select("id, billing_plans(amount_paise, interval)", count="exact")
            .eq("status", "active")
            .execute()
        )
        active_rows = active.data or []
        mrr_paise = 0
        for row in active_rows:
            plan = row.get("billing_plans") or {}
            amount = plan.get("amount_paise") or 0
            if plan.get("interval") == "yearly":
                amount = amount // 12
            mrr_paise += amount

        invoices = (
            supabase.table("invoices")
            .select("*, profiles(company_name)")
            .order("created_at", desc=True)
            .limit(50)
            .execute()
        )
        invoice_rows = invoices.data or []
        failed_count = sum(1 for row in invoice_rows if row.get("status") == "failed")

        return {
            "mrr_paise": mrr_paise,
            "active_subscriptions": active.count if active.count is not None else len(active_rows),
            "failed_invoices_recent": failed_count,
            "invoices": invoice_rows,
        }


billing_service = BillingService()
