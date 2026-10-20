"""Thin wrapper around the Meta WhatsApp Cloud API (Graph API)."""
from __future__ import annotations

import hashlib
import hmac
from typing import Any

import httpx

from app.core.config import settings

# WhatsApp rejects text bodies longer than 4096 characters.
MAX_TEXT_LENGTH = 4096


class WhatsAppApiError(Exception):
    pass


def _graph_url(path: str) -> str:
    return f"https://graph.facebook.com/{settings.whatsapp_graph_version}/{path}"


def _request(method: str, path: str, access_token: str, **kwargs: Any) -> dict[str, Any]:
    try:
        response = httpx.request(
            method,
            _graph_url(path),
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=15,
            **kwargs,
        )
    except httpx.HTTPError as error:
        raise WhatsAppApiError(f"Could not reach WhatsApp: {error}") from error

    data = response.json() if response.content else {}
    if response.status_code >= 400:
        message = (data.get("error") or {}).get("message") or response.text
        raise WhatsAppApiError(message)
    return data


def send_text(phone_number_id: str, access_token: str, to: str, body: str) -> str:
    """Sends a text message and returns Meta's message id (wamid...)."""
    if len(body) > MAX_TEXT_LENGTH:
        body = body[: MAX_TEXT_LENGTH - 1] + "…"
    data = _request(
        "POST",
        f"{phone_number_id}/messages",
        access_token,
        json={
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "text",
            "text": {"preview_url": False, "body": body},
        },
    )
    return ((data.get("messages") or [{}])[0]).get("id", "")


def mark_read(phone_number_id: str, access_token: str, message_id: str) -> None:
    """Shows blue ticks to the customer. Best-effort only."""
    try:
        _request(
            "POST",
            f"{phone_number_id}/messages",
            access_token,
            json={"messaging_product": "whatsapp", "status": "read", "message_id": message_id},
        )
    except WhatsAppApiError as error:
        print(f"WhatsApp mark_read failed: {error}")


def get_phone_number_info(phone_number_id: str, access_token: str) -> dict[str, Any]:
    return _request(
        "GET",
        phone_number_id,
        access_token,
        params={"fields": "display_phone_number,verified_name,quality_rating"},
    )


def verify_signature(raw_body: bytes, signature_header: str, app_secret: str) -> bool:
    """Validates Meta's X-Hub-Signature-256 header ("sha256=<hex hmac>")."""
    if not signature_header.startswith("sha256=") or not app_secret:
        return False
    expected = hmac.new(app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header.removeprefix("sha256="))
