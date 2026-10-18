"""One-off/idempotent script: creates a Razorpay Plan for every row in
`billing_plans` that doesn't have a `razorpay_plan_id` yet, and writes the
resulting id back to Supabase.

Run this once after seeding `billing_plans` (or after adding a new plan)
and whenever you rotate between Razorpay test and live mode, since plan ids
are not shared between the two.

Usage (from property-nest-backend/):
    python -m scripts.sync_razorpay_plans
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from supabase import create_client  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.services.razorpay_service import razorpay_service  # noqa: E402


def main() -> None:
    if not settings.has_supabase:
        raise SystemExit("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set.")
    if not settings.has_razorpay:
        raise SystemExit("RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET must be set.")

    supabase = create_client(settings.supabase_url, settings.supabase_service_role_key)
    plans = supabase.table("billing_plans").select("*").is_("razorpay_plan_id", "null").execute().data or []

    if not plans:
        print("Nothing to do — every billing_plans row already has a razorpay_plan_id.")
        return

    for plan in plans:
        # Enterprise is sold as a negotiated custom deal, not self-serve at a
        # fixed price — skip it here so it never gets a razorpay_plan_id and
        # the app's "Contact sales" treatment for it stays correct. A super
        # admin can set a real, deal-specific razorpay_plan_id by hand once
        # they've negotiated with a customer.
        if plan["name"].strip().lower() == "enterprise":
            print(f"Skipping {plan['name']} ({plan['interval']}) — negotiated custom deal, not self-serve.")
            continue

        created = razorpay_service.create_plan(
            name=plan["name"],
            amount_paise=plan["amount_paise"],
            interval=plan["interval"],
            description=f"{plan['name']} ({plan['interval']})",
        )
        supabase.table("billing_plans").update({"razorpay_plan_id": created["id"]}).eq(
            "id", plan["id"]
        ).execute()
        print(f"{plan['name']} ({plan['interval']}) -> {created['id']}")


if __name__ == "__main__":
    main()
