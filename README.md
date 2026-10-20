# PropCore Chatbot Backend

FastAPI backend for the chatbot used by the PropCore frontend.

## Structure

```text
backend/
  app/
    main.py
    api/
      router.py
      endpoints/
        bot.py
        personalized_bot.py
    core/
      config.py
      prompts.py
    schemas/
      chat.py
      property_chat.py
    services/
      chat_service.py
      property_chat_service.py
    utils/
      connection_manager.py
  requirements.txt
  .env.example
```

## Run

```bash
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8001
```

## WebSocket

`ws://localhost:8001/ws/chat`

## REST

`POST /api/chat`

## Personalized Property Chat

This backend also supports a property-specific chatbot that creates and resumes rows in `public.leads` using the property/project ID, name, phone number, and optional email.

### Required environment variables

```bash
SUPABASE_URL=...
SUPABASE_SERVICE_ROLE_KEY=...
```

### Endpoints

`POST /api/property-chat/leads`

`POST /api/property-chat/leads/{lead_id}/messages`

`WS /ws/property-chat/{lead_id}`

## Billing (Razorpay subscriptions)

Handles SaaS plan billing for builder accounts (Pro/Enterprise) via Razorpay's
Subscriptions API. All endpoints except `/billing/plans` and `/billing/webhook`
require an `Authorization: Bearer <supabase access token>` header.

### Required environment variables

```bash
RAZORPAY_KEY_ID=...
RAZORPAY_KEY_SECRET=...
RAZORPAY_WEBHOOK_SECRET=...   # from the webhook configured in the Razorpay dashboard
```

### One-time setup

1. Apply the `create_billing_tables` Supabase migration (adds `billing_plans`,
   `subscriptions`, `invoices`, `razorpay_webhook_events`, seeded with a Pro
   and an Enterprise row that have no `razorpay_plan_id` yet).
2. Run `python -m scripts.sync_razorpay_plans` to create the matching Plans in
   Razorpay and backfill `billing_plans.razorpay_plan_id`. Re-run it whenever
   you add a plan or switch between Razorpay test/live keys.
3. In the Razorpay dashboard, add a webhook pointed at
   `https://<this-backend>/billing/webhook` subscribed to: `subscription.activated`,
   `subscription.charged`, `subscription.completed`, `subscription.cancelled`,
   `subscription.pending`, `subscription.halted`, `payment.failed`.

### Endpoints

`GET /billing/plans` — active plans (public).

`POST /billing/subscribe` — `{ plan_id }` → creates/reuses a Razorpay customer
and subscription for the caller, returns the ids the frontend needs to open
Razorpay Checkout.

`POST /billing/verify` — verifies the Checkout success payload's signature
(fast-path UX only; the webhook is the source of truth for subscription state).

`GET /billing/me` — the caller's current subscription + recent invoices.

`GET /billing/admin/overview` — MRR, active subscription count, and recent
invoices across all tenants. Requires the `super_admin` role.

`POST /billing/webhook` — Razorpay webhook receiver. Verifies
`X-Razorpay-Signature` against the raw body and is idempotent per
`X-Razorpay-Event-Id`.
