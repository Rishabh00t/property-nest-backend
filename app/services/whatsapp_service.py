"""Multi-builder WhatsApp chatbot.

How a message is routed:
  Meta → POST /whatsapp/webhook (one URL for the whole platform)
       → value.metadata.phone_number_id
       → whatsapp_accounts row (phone_number_id is UNIQUE) → builder_id
Everything created afterwards (conversation, messages, lead) is stamped with
that builder_id, and every admin API call is filtered by the caller's id, so
one builder can never read or reply to another builder's chats.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from supabase import Client, create_client

from app.core.config import settings
from app.core.prompts import build_whatsapp_prompt
from app.services import whatsapp_client
from app.services.lead_scoring_service import LeadScoringService
from app.services.llm import get_chat_model
from app.utils.auth import CurrentUser
from app.utils.crypto import decrypt_secret, encrypt_secret, mask_secret

HISTORY_LIMIT = 20                       # messages of context sent to the AI
CUSTOMER_SERVICE_WINDOW = timedelta(hours=24)
HIDDEN_PROJECT_STATUSES = ("Draft", "Rejected")
PROJECT_FIELDS = (
    "id,project_name,project_type,project_summary,tagline,address,zone,city,configurations,"
    "min_budget,max_budget,min_price_unit,max_price_unit,price_per_sqft,possession_date,"
    "construction_status,highlights,payment_plan,rera,status"
)
ACCOUNT_SETTING_FIELDS = (
    "bot_enabled",
    "bot_name",
    "welcome_message",
    "custom_instructions",
    "default_project_id",
    "auto_create_leads",
    "handoff_keywords",
    "handoff_message",
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _data(response: Any) -> Any:
    # .maybe_single().execute() returns None (not a response) when nothing matches.
    if response is None:
        return None
    return getattr(response, "data", None)


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class ConflictError(Exception):
    pass


class WhatsAppService:
    def __init__(self) -> None:
        self._supabase: Client | None = None
        self._scoring = LeadScoringService()

    def _db(self) -> Client:
        if not settings.has_supabase:
            raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set for the WhatsApp bot.")
        if self._supabase is None:
            self._supabase = create_client(settings.supabase_url, settings.supabase_service_role_key)
        return self._supabase

    # =====================================================================
    # Webhook: verification + signature
    # =====================================================================

    def is_valid_verify_token(self, token: str) -> bool:
        """Meta's GET handshake. Accepts the platform token or any builder's own token."""
        if not token:
            return False
        if settings.whatsapp_verify_token and secrets.compare_digest(token, settings.whatsapp_verify_token):
            return True
        row = _data(
            self._db().table("whatsapp_accounts").select("id").eq("verify_token", token).limit(1).execute()
        )
        return bool(row)

    def get_account_by_phone_number_id(self, phone_number_id: str) -> dict | None:
        return _data(
            self._db()
            .table("whatsapp_accounts")
            .select("*")
            .eq("phone_number_id", phone_number_id)
            .maybe_single()
            .execute()
        )

    def is_signature_valid(self, account: dict, raw_body: bytes, signature: str) -> bool:
        # A builder using their own Meta app signs with their own app secret;
        # numbers attached to the platform app are signed with the platform secret.
        app_secret = decrypt_secret(account.get("app_secret")) or settings.whatsapp_app_secret
        if not app_secret:
            if settings.app_env == "development":
                print("WARNING: no WhatsApp app secret configured; skipping signature check (development only).")
                return True
            return False
        return whatsapp_client.verify_signature(raw_body, signature, app_secret)

    # =====================================================================
    # Webhook: processing
    # =====================================================================

    def process_webhook(self, payload: dict, raw_body: bytes, signature: str) -> None:
        if payload.get("object") != "whatsapp_business_account":
            return

        for entry in payload.get("entry") or []:
            for change in entry.get("changes") or []:
                if change.get("field") != "messages":
                    continue
                value = change.get("value") or {}
                phone_number_id = (value.get("metadata") or {}).get("phone_number_id")
                if not phone_number_id:
                    continue

                account = self.get_account_by_phone_number_id(phone_number_id)
                if not account:
                    print(f"WhatsApp webhook for unknown phone_number_id {phone_number_id}; ignoring.")
                    continue
                if not self.is_signature_valid(account, raw_body, signature):
                    print(f"WhatsApp webhook signature mismatch for account {account['id']}; ignoring.")
                    continue

                contacts = {c.get("wa_id"): (c.get("profile") or {}).get("name") for c in value.get("contacts") or []}

                for message in value.get("messages") or []:
                    try:
                        self._handle_inbound(account, message, contacts.get(message.get("from")))
                    except Exception as error:  # never let one bad message fail the whole batch
                        print(f"WhatsApp inbound handling failed ({message.get('id')}): {error}")

                for status in value.get("statuses") or []:
                    try:
                        self._handle_status(account, status)
                    except Exception as error:
                        print(f"WhatsApp status handling failed: {error}")

    def _handle_status(self, account: dict, status: dict) -> None:
        new_status = status.get("status")
        if new_status not in ("sent", "delivered", "read", "failed"):
            return
        update: dict[str, Any] = {"status": new_status}
        if new_status == "failed":
            errors = status.get("errors") or [{}]
            update["error"] = errors[0].get("title") or errors[0].get("message") or "Delivery failed"
        (
            self._db()
            .table("whatsapp_messages")
            .update(update)
            .eq("wa_message_id", status.get("id"))
            .eq("builder_id", account["builder_id"])
            .execute()
        )

    @staticmethod
    def _extract_text(message: dict) -> tuple[str, str]:
        """Returns (message_type, text) for any inbound WhatsApp message type."""
        msg_type = message.get("type") or "unknown"
        if msg_type == "text":
            return msg_type, (message.get("text") or {}).get("body", "")
        if msg_type == "button":
            return msg_type, (message.get("button") or {}).get("text", "")
        if msg_type == "interactive":
            interactive = message.get("interactive") or {}
            reply = interactive.get("button_reply") or interactive.get("list_reply") or {}
            return msg_type, reply.get("title", "")
        media = message.get(msg_type) or {}
        caption = media.get("caption") if isinstance(media, dict) else None
        return msg_type, f"[{msg_type}]" + (f" {caption}" if caption else "")

    def _handle_inbound(self, account: dict, message: dict, contact_name: str | None) -> None:
        db = self._db()
        builder_id = account["builder_id"]
        wa_id = message.get("from")
        wa_message_id = message.get("id")
        if not wa_id or not wa_message_id:
            return

        msg_type, text = self._extract_text(message)
        conversation = self._get_or_create_conversation(account, wa_id, contact_name)

        # Idempotency: Meta retries webhooks, so the UNIQUE wa_message_id makes
        # a retry a no-op instead of a duplicate message + duplicate bot reply.
        inserted = _data(
            db.table("whatsapp_messages")
            .upsert(
                {
                    "conversation_id": conversation["id"],
                    "builder_id": builder_id,
                    "direction": "inbound",
                    "sender": "customer",
                    "message_type": msg_type,
                    "body": text,
                    "wa_message_id": wa_message_id,
                    "status": "received",
                },
                on_conflict="wa_message_id",
                ignore_duplicates=True,
            )
            .execute()
        )
        if not inserted:
            return
        inbound_row = inserted[0]

        is_first_message = conversation.get("last_inbound_at") is None
        projects = self._builder_projects(builder_id)
        # A project named in this message wins (lets buyers switch projects mid-chat).
        project_id = self._match_project_name(text, projects) or conversation.get("project_id")             or self._fallback_project(projects, account)

        conversation_update: dict[str, Any] = {
            "unread_count": (conversation.get("unread_count") or 0) + 1,
            "last_message_preview": text[:200],
            "last_message_at": _now().isoformat(),
            "last_inbound_at": _now().isoformat(),
            "project_id": project_id,
        }
        if contact_name:
            conversation_update["contact_name"] = contact_name
        if conversation.get("status") == "closed":
            conversation_update["status"] = "bot"
        conversation = {**conversation, **conversation_update}

        lead_id = self._ensure_lead(account, conversation, projects)
        if lead_id:
            conversation_update["lead_id"] = lead_id
            conversation["lead_id"] = lead_id

        db.table("whatsapp_conversations").update(conversation_update).eq("id", conversation["id"]).execute()

        access_token = decrypt_secret(account["access_token"])
        whatsapp_client.mark_read(account["phone_number_id"], access_token, wa_message_id)

        if lead_id:
            self._track_lead_activity(lead_id, project_id, wa_id)

        # ---- Decide whether the bot replies --------------------------------
        if not account.get("bot_enabled") or conversation.get("status") != "bot":
            return  # a human owns this chat, or the builder switched the bot off

        if self._wants_human(text, account.get("handoff_keywords") or []):
            self._send_and_store(account, conversation, account.get("handoff_message") or "", sender="bot")
            db.table("whatsapp_conversations").update({"status": "human"}).eq("id", conversation["id"]).execute()
            return

        if is_first_message and account.get("welcome_message"):
            self._send_and_store(account, conversation, account["welcome_message"], sender="bot")

        reply = self._generate_reply(account, conversation, projects, inbound_row["id"], text)
        if reply:
            self._send_and_store(account, conversation, reply, sender="bot")

    # =====================================================================
    # Conversation / lead helpers
    # =====================================================================

    def _get_or_create_conversation(self, account: dict, wa_id: str, contact_name: str | None) -> dict:
        db = self._db()

        def find() -> dict | None:
            return _data(
                db.table("whatsapp_conversations")
                .select("*")
                .eq("account_id", account["id"])
                .eq("wa_id", wa_id)
                .maybe_single()
                .execute()
            )

        existing = find()
        if existing:
            return existing
        try:
            created = _data(
                db.table("whatsapp_conversations")
                .insert(
                    {
                        "builder_id": account["builder_id"],
                        "account_id": account["id"],
                        "wa_id": wa_id,
                        "contact_name": contact_name,
                    }
                )
                .execute()
            )
            return created[0]
        except Exception:
            # Two webhooks for the same new customer raced; the other one won.
            existing = find()
            if existing:
                return existing
            raise

    def _builder_projects(self, builder_id: str) -> list[dict]:
        rows = _data(
            self._db()
            .table("projects")
            .select(PROJECT_FIELDS)
            .eq("builder", builder_id)
            .order("created_at", desc=True)
            .limit(25)
            .execute()
        )
        return [row for row in rows or [] if row.get("status") not in HIDDEN_PROJECT_STATUSES]

    @staticmethod
    def _match_project_name(text: str, projects: list[dict]) -> str | None:
        """QR codes / website buttons open WhatsApp with a pre-filled message such
        as "Hi, I'm interested in Skyline Residences", so a name match is enough.
        Longest names first so "Skyline Residences" beats "Skyline"."""
        lowered = text.lower()
        for project in sorted(projects, key=lambda p: len(p.get("project_name") or ""), reverse=True):
            name = (project.get("project_name") or "").strip().lower()
            if name and name in lowered:
                return project["id"]
        return None

    @staticmethod
    def _fallback_project(projects: list[dict], account: dict) -> str | None:
        if account.get("default_project_id"):
            return account["default_project_id"]
        if len(projects) == 1:
            return projects[0]["id"]
        return None

    def _ensure_lead(self, account: dict, conversation: dict, projects: list[dict]) -> str | None:
        """Creates/links a CRM lead (source = WhatsApp) for the conversation.

        Leads belong to a builder through leads.project_id → projects.builder,
        so a lead is only created once we know a project of *this* builder.
        """
        if conversation.get("lead_id") or not account.get("auto_create_leads"):
            return conversation.get("lead_id")

        project_id = conversation.get("project_id") or (projects[0]["id"] if projects else None)
        if not project_id:
            return None

        db = self._db()
        phone = f"+{conversation['wa_id']}"
        existing = _data(
            db.table("leads")
            .select("id")
            .eq("project_id", project_id)
            .in_("phone", [phone, conversation["wa_id"]])
            .limit(1)
            .execute()
        )
        if existing:
            return existing[0]["id"]

        project_name = next((p.get("project_name") for p in projects if p["id"] == project_id), None)
        created = _data(
            db.table("leads")
            .insert(
                {
                    "name": conversation.get("contact_name") or "WhatsApp Guest",
                    "phone": phone,
                    "source": "WhatsApp",
                    "interest": project_name or "Property enquiry",
                    "budget": "Not specified",
                    "score": 50,
                    "status": "New",
                    "project_id": project_id,
                }
            )
            .execute()
        )
        return created[0]["id"] if created else None

    def _track_lead_activity(self, lead_id: str, project_id: str | None, wa_id: str) -> None:
        """Feeds WhatsApp messages into the existing lead-scoring engine."""
        try:
            self._db().table("analytics_events").insert(
                {
                    "session_id": f"whatsapp:{wa_id}",
                    "lead_id": lead_id,
                    "project_id": project_id,
                    "event_type": "chat_message_sent",
                    "metadata": {"channel": "whatsapp"},
                }
            ).execute()
            self._scoring.recalculate_score(lead_id)
        except Exception as error:
            print(f"WhatsApp lead scoring skipped: {error}")

    @staticmethod
    def _wants_human(text: str, keywords: list[str]) -> bool:
        lowered = text.lower()
        return any(keyword.strip().lower() in lowered for keyword in keywords if keyword.strip())

    # =====================================================================
    # AI reply
    # =====================================================================

    def _generate_reply(
        self, account: dict, conversation: dict, projects: list[dict], current_message_id: str, text: str
    ) -> str:
        db = self._db()
        history_rows = _data(
            db.table("whatsapp_messages")
            .select("id,sender,body")
            .eq("conversation_id", conversation["id"])
            .order("created_at", desc=True)
            .limit(HISTORY_LIMIT + 1)
            .execute()
        ) or []
        history_rows = [row for row in reversed(history_rows) if row["id"] != current_message_id]

        profile = _data(
            db.table("profiles").select("company_name").eq("id", account["builder_id"]).maybe_single().execute()
        ) or {}

        focus = next((p for p in projects if p["id"] == conversation.get("project_id")), None)
        others = [p for p in projects if p is not focus][:10]

        prompt = build_whatsapp_prompt(
            bot_name=account.get("bot_name") or "PropCore Assistant",
            company_name=profile.get("company_name"),
            focus_project=focus,
            other_projects=others,
            custom_instructions=account.get("custom_instructions"),
            customer_name=conversation.get("contact_name"),
        )

        messages: list = [SystemMessage(content=prompt)]
        for row in history_rows:
            if not row.get("body"):
                continue
            if row["sender"] == "customer":
                messages.append(HumanMessage(content=row["body"]))
            else:
                messages.append(AIMessage(content=row["body"]))
        messages.append(HumanMessage(content=text))

        try:
            response = get_chat_model().invoke(messages)
            content = response.content
            if isinstance(content, list):  # some Gemini responses come back as content parts
                content = "".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in content)
            return str(content).strip()
        except Exception as error:
            print(f"WhatsApp AI reply failed: {error}")
            return "Thanks for your message! Our sales team will get back to you shortly."

    # =====================================================================
    # Sending
    # =====================================================================

    def _send_and_store(
        self, account: dict, conversation: dict, body: str, *, sender: str, sent_by: str | None = None
    ) -> dict:
        if not body.strip():
            return {}
        db = self._db()
        row: dict[str, Any] = {
            "conversation_id": conversation["id"],
            "builder_id": account["builder_id"],
            "direction": "outbound",
            "sender": sender,
            "sent_by": sent_by,
            "body": body,
            "status": "sending",
        }
        try:
            wa_message_id = whatsapp_client.send_text(
                account["phone_number_id"], decrypt_secret(account["access_token"]), conversation["wa_id"], body
            )
            row.update({"wa_message_id": wa_message_id or None, "status": "sent"})
        except whatsapp_client.WhatsAppApiError as error:
            row.update({"status": "failed", "error": str(error)[:500]})

        stored = _data(db.table("whatsapp_messages").insert(row).execute())
        db.table("whatsapp_conversations").update(
            {"last_message_preview": body[:200], "last_message_at": _now().isoformat()}
        ).eq("id", conversation["id"]).execute()
        return (stored or [row])[0]

    # =====================================================================
    # Builder (admin) API
    # =====================================================================

    def _account_for_builder(self, builder_id: str) -> dict | None:
        return _data(
            self._db().table("whatsapp_accounts").select("*").eq("builder_id", builder_id).maybe_single().execute()
        )

    @staticmethod
    def _public_account(account: dict | None, webhook_url: str) -> dict:
        base = {
            "webhook_url": webhook_url,
            "platform_app_available": bool(settings.whatsapp_verify_token and settings.whatsapp_app_secret),
        }
        if not account:
            return {**base, "connected": False, "account": None}
        token = decrypt_secret(account.get("access_token"))
        public = {key: value for key, value in account.items() if key not in ("access_token", "app_secret")}
        public["access_token_masked"] = mask_secret(token)
        public["has_app_secret"] = bool(account.get("app_secret"))
        return {**base, "connected": account.get("connection_status") == "connected", "account": public}

    def get_account(self, user: CurrentUser, webhook_url: str) -> dict:
        return self._public_account(self._account_for_builder(user.id), webhook_url)

    def save_account(self, user: CurrentUser, payload: dict, webhook_url: str) -> dict:
        db = self._db()
        existing = self._account_for_builder(user.id)
        update: dict[str, Any] = {key: payload[key] for key in ACCOUNT_SETTING_FIELDS if key in payload}

        if update.get("default_project_id"):
            owned = _data(
                db.table("projects")
                .select("id")
                .eq("id", update["default_project_id"])
                .eq("builder", user.id)
                .maybe_single()
                .execute()
            )
            if not owned:
                raise ValueError("Default project must be one of your own projects.")

        for key in ("phone_number_id", "waba_id"):
            if payload.get(key):
                update[key] = payload[key].strip()
        if payload.get("access_token"):
            update["access_token"] = encrypt_secret(payload["access_token"].strip())
        if "app_secret" in payload:
            secret = (payload.get("app_secret") or "").strip()
            update["app_secret"] = encrypt_secret(secret) if secret else None

        credentials_changed = any(key in update for key in ("phone_number_id", "access_token", "app_secret"))
        if credentials_changed:
            update["connection_status"] = "pending"
            update["last_error"] = None

        if update.get("phone_number_id"):
            taken = _data(
                db.table("whatsapp_accounts")
                .select("builder_id")
                .eq("phone_number_id", update["phone_number_id"])
                .neq("builder_id", user.id)
                .limit(1)
                .execute()
            )
            if taken:
                raise ConflictError("This WhatsApp number is already connected to another PropCore workspace.")

        if existing:
            if update:
                db.table("whatsapp_accounts").update(update).eq("id", existing["id"]).execute()
        else:
            if not update.get("phone_number_id") or not update.get("access_token"):
                raise ValueError("Phone number ID and access token are required to connect WhatsApp.")
            update["builder_id"] = user.id
            update["verify_token"] = secrets.token_urlsafe(24)
            db.table("whatsapp_accounts").insert(update).execute()

        account = self._account_for_builder(user.id)
        if credentials_changed and account:
            account = self._test_connection(account)
        return self._public_account(account, webhook_url)

    def _test_connection(self, account: dict) -> dict:
        try:
            info = whatsapp_client.get_phone_number_info(
                account["phone_number_id"], decrypt_secret(account["access_token"])
            )
            update = {
                "connection_status": "connected",
                "last_error": None,
                "display_phone_number": info.get("display_phone_number"),
                "verified_name": info.get("verified_name"),
            }
        except whatsapp_client.WhatsAppApiError as error:
            update = {"connection_status": "error", "last_error": str(error)[:500]}
        self._db().table("whatsapp_accounts").update(update).eq("id", account["id"]).execute()
        return {**account, **update}

    def test_connection(self, user: CurrentUser, webhook_url: str) -> dict:
        account = self._account_for_builder(user.id)
        if not account:
            raise ValueError("Connect a WhatsApp number first.")
        return self._public_account(self._test_connection(account), webhook_url)

    def rotate_verify_token(self, user: CurrentUser, webhook_url: str) -> dict:
        account = self._account_for_builder(user.id)
        if not account:
            raise ValueError("Connect a WhatsApp number first.")
        new_token = secrets.token_urlsafe(24)
        self._db().table("whatsapp_accounts").update({"verify_token": new_token}).eq("id", account["id"]).execute()
        return self._public_account({**account, "verify_token": new_token}, webhook_url)

    def disconnect(self, user: CurrentUser) -> None:
        # Cascades to this builder's conversations and messages.
        self._db().table("whatsapp_accounts").delete().eq("builder_id", user.id).execute()

    # ---- Inbox ------------------------------------------------------------

    def list_conversations(self, user: CurrentUser, status: str | None) -> list[dict]:
        query = (
            self._db()
            .table("whatsapp_conversations")
            .select("*, leads(id,name,status,score), projects(id,project_name)")
            .eq("builder_id", user.id)
            .order("last_message_at", desc=True)
            .limit(200)
        )
        if status:
            query = query.eq("status", status)
        return _data(query.execute()) or []

    def _owned_conversation(self, user: CurrentUser, conversation_id: str) -> dict:
        conversation = _data(
            self._db()
            .table("whatsapp_conversations")
            .select("*")
            .eq("id", conversation_id)
            .eq("builder_id", user.id)  # tenant isolation
            .maybe_single()
            .execute()
        )
        if not conversation:
            raise LookupError("Conversation not found.")
        return conversation

    def get_messages(self, user: CurrentUser, conversation_id: str) -> dict:
        db = self._db()
        conversation = self._owned_conversation(user, conversation_id)
        messages = _data(
            db.table("whatsapp_messages")
            .select("*")
            .eq("conversation_id", conversation_id)
            .order("created_at")
            .limit(500)
            .execute()
        ) or []
        if conversation.get("unread_count"):
            db.table("whatsapp_conversations").update({"unread_count": 0}).eq("id", conversation_id).execute()
            conversation["unread_count"] = 0
        return {"conversation": conversation, "messages": messages, "can_reply": self._in_window(conversation)}

    @staticmethod
    def _in_window(conversation: dict) -> bool:
        last_inbound = _parse_ts(conversation.get("last_inbound_at"))
        return bool(last_inbound and _now() - last_inbound < CUSTOMER_SERVICE_WINDOW)

    def send_agent_message(self, user: CurrentUser, conversation_id: str, body: str) -> dict:
        conversation = self._owned_conversation(user, conversation_id)
        if not self._in_window(conversation):
            raise ConflictError(
                "WhatsApp only allows free-form replies within 24 hours of the customer's last message. "
                "Ask the customer to message you again, or use an approved template."
            )
        account = self._account_for_builder(user.id)
        if not account:
            raise ValueError("WhatsApp is not connected.")

        message = self._send_and_store(account, conversation, body, sender="agent", sent_by=user.id)
        # A human replied, so pause the bot for this chat to avoid talking over them.
        if conversation.get("status") == "bot":
            self._db().table("whatsapp_conversations").update({"status": "human"}).eq("id", conversation_id).execute()
        return message

    def set_conversation_status(self, user: CurrentUser, conversation_id: str, status: str) -> dict:
        self._owned_conversation(user, conversation_id)
        updated = _data(
            self._db()
            .table("whatsapp_conversations")
            .update({"status": status})
            .eq("id", conversation_id)
            .eq("builder_id", user.id)
            .execute()
        )
        return (updated or [{}])[0]


whatsapp_service = WhatsAppService()
