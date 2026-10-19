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

## WhatsApp chatbot (multi-builder)

Each builder connects **their own** WhatsApp Business number from
*Admin → WhatsApp Bot*. An AI assistant (Gemini, same config as the website
chat) answers buyers about that builder's projects, creates CRM leads
(source `WhatsApp`), and hands chats to a human on request.

### How messages are routed between builders

```
Buyer ──WhatsApp──▶ Meta ──POST /whatsapp/webhook──▶ backend
                                 │  value.metadata.phone_number_id
                                 ▼
             whatsapp_accounts (phone_number_id UNIQUE) ──▶ builder_id
                                 │
        conversation / messages / lead are all stamped with builder_id
                                 │
              reply is sent from that builder's number with their token
```

* One webhook URL serves every builder; the receiving number decides the builder.
* A number can only be connected to one workspace (`409` otherwise).
* The same buyer messaging two builders gets two separate conversations.
* Every admin endpoint filters by the caller's id, and Supabase RLS limits
  direct reads/Realtime to `builder_id = auth.uid()`.
* Signatures (`X-Hub-Signature-256`) are verified per builder: with their own
  Meta app's secret if they saved one, otherwise the platform `WHATSAPP_APP_SECRET`.
* Access tokens / app secrets are Fernet-encrypted (`ENCRYPTION_KEY`) and never
  returned to the browser.
* Webhook retries are de-duplicated by Meta's message id (`wa_message_id` UNIQUE).
* Chat history lives in Postgres (not memory), so it works on serverless/Vercel.

### Required environment variables

```bash
WHATSAPP_VERIFY_TOKEN=...   # platform Meta app (optional if every builder uses their own app)
WHATSAPP_APP_SECRET=...     # platform Meta app secret
PUBLIC_BACKEND_URL=https://<this-backend>
ENCRYPTION_KEY=...          # python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
WHATSAPP_GRAPH_VERSION=v21.0
```

### One-time setup

1. Apply the `create_whatsapp_chatbot` Supabase migration.
2. Set the env vars above and redeploy.
3. Each builder: Admin → WhatsApp Bot → Connection → paste Phone number ID +
   permanent access token, then copy the shown Callback URL and Verify token
   into their Meta app (WhatsApp → Configuration → Webhook) and subscribe to
   the `messages` field.

### Endpoints

`GET /whatsapp/webhook` — Meta verification handshake (platform or per-builder verify token).

`POST /whatsapp/webhook` — incoming messages and delivery statuses for all builders.

Builder-only (`Authorization: Bearer <supabase token>`, role `admin`):

`GET | PUT | DELETE /whatsapp/account` — connection + bot settings (masked secrets).
`POST /whatsapp/account/test` — checks the credentials against the Graph API.
`POST /whatsapp/account/rotate-verify-token`

`GET /whatsapp/conversations?status=bot|human|closed`
`GET /whatsapp/conversations/{id}/messages` — thread + `can_reply` (24h window), clears unread.
`POST /whatsapp/conversations/{id}/messages` — `{ body }` agent reply; pauses the bot for that chat.
`PATCH /whatsapp/conversations/{id}` — `{ status }` take over / hand back / close.
