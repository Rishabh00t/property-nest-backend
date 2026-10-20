"""WhatsApp chatbot routes.

Public (called by Meta):
  GET  /whatsapp/webhook   - webhook verification handshake
  POST /whatsapp/webhook   - incoming messages + delivery statuses, all builders

Builder workspace (Supabase bearer token, role admin):
  GET/PUT/DELETE /whatsapp/account, POST /whatsapp/account/test, ...
  GET /whatsapp/conversations, GET/POST .../{id}/messages, PATCH .../{id}

Handlers are plain `def` so FastAPI runs them in a worker thread; the Supabase,
Graph API and Gemini clients are all blocking.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from starlette.concurrency import run_in_threadpool

from app.core.config import settings
from app.schemas.whatsapp import AgentMessageRequest, ConversationStatusUpdate, WhatsAppAccountUpdate
from app.services.whatsapp_service import ConflictError, whatsapp_service
from app.utils.auth import CurrentUser, require_builder

whatsapp_router = APIRouter()


def _webhook_url(request: Request) -> str:
    base = settings.public_backend_url or str(request.base_url)
    return base.rstrip("/") + "/whatsapp/webhook"


def _call(fn, *args):
    try:
        return fn(*args)
    except ConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


# ---- Meta webhook -----------------------------------------------------------


@whatsapp_router.get("/webhook", response_class=PlainTextResponse)
def verify_webhook(
    mode: str = Query("", alias="hub.mode"),
    token: str = Query("", alias="hub.verify_token"),
    challenge: str = Query("", alias="hub.challenge"),
):
    if mode == "subscribe" and whatsapp_service.is_valid_verify_token(token):
        return challenge
    raise HTTPException(status_code=403, detail="Verification failed.")


@whatsapp_router.post("/webhook")
async def receive_webhook(request: Request):
    # The signature covers the exact raw bytes, so read them before parsing.
    raw_body = await request.body()
    signature = request.headers.get("x-hub-signature-256", "")
    try:
        payload = json.loads(raw_body or b"{}")
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON.")

    # Signatures are checked per builder inside the service (each builder's
    # number may belong to a different Meta app with its own secret).
    # Always answer 200 once parsed: a non-200 makes Meta retry the whole
    # batch for days, and duplicates are already de-duplicated by message id.
    try:
        await run_in_threadpool(whatsapp_service.process_webhook, payload, raw_body, signature)
    except Exception as error:
        print(f"WhatsApp webhook processing error: {error}")
    return {"status": "ok"}


# ---- Builder: account & bot settings ----------------------------------------


@whatsapp_router.get("/account")
def get_account(request: Request, user: CurrentUser = Depends(require_builder)):
    return _call(whatsapp_service.get_account, user, _webhook_url(request))


@whatsapp_router.put("/account")
def save_account(payload: WhatsAppAccountUpdate, request: Request, user: CurrentUser = Depends(require_builder)):
    return _call(
        whatsapp_service.save_account, user, payload.model_dump(exclude_unset=True), _webhook_url(request)
    )


@whatsapp_router.post("/account/test")
def test_account(request: Request, user: CurrentUser = Depends(require_builder)):
    return _call(whatsapp_service.test_connection, user, _webhook_url(request))


@whatsapp_router.post("/account/rotate-verify-token")
def rotate_verify_token(request: Request, user: CurrentUser = Depends(require_builder)):
    return _call(whatsapp_service.rotate_verify_token, user, _webhook_url(request))


@whatsapp_router.delete("/account")
def disconnect_account(user: CurrentUser = Depends(require_builder)):
    _call(whatsapp_service.disconnect, user)
    return {"success": True}


# ---- Builder: inbox ---------------------------------------------------------


@whatsapp_router.get("/conversations")
def list_conversations(
    status: str | None = Query(None, pattern="^(bot|human|closed)$"),
    user: CurrentUser = Depends(require_builder),
):
    return _call(whatsapp_service.list_conversations, user, status)


@whatsapp_router.get("/conversations/{conversation_id}/messages")
def get_messages(conversation_id: str, user: CurrentUser = Depends(require_builder)):
    return _call(whatsapp_service.get_messages, user, conversation_id)


@whatsapp_router.post("/conversations/{conversation_id}/messages")
def send_message(conversation_id: str, payload: AgentMessageRequest, user: CurrentUser = Depends(require_builder)):
    return _call(whatsapp_service.send_agent_message, user, conversation_id, payload.body)


@whatsapp_router.patch("/conversations/{conversation_id}")
def update_conversation(
    conversation_id: str, payload: ConversationStatusUpdate, user: CurrentUser = Depends(require_builder)
):
    return _call(whatsapp_service.set_conversation_status, user, conversation_id, payload.status)
