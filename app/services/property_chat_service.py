from __future__ import annotations

import re
from datetime import datetime, timezone

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from supabase import Client, create_client

from app.core.config import settings
from app.core.prompts import build_property_chat_prompt
from app.schemas.property_chat import (
    PropertyChatResponse,
    PropertyLeadCreateRequest,
    PropertyLeadResponse,
)


CHAT_HISTORY: dict[str, list[dict]] = {}


class PropertyChatService:
    def __init__(self):
        self.model = ChatGoogleGenerativeAI(
            api_key=settings.gemini_api_key,
            model="models/gemini-2.5-flash",
        )
        self._supabase: Client | None = None

    def create_or_resume_lead(self, payload: PropertyLeadCreateRequest) -> PropertyLeadResponse:
        supabase = self._get_supabase()
        project_row = self._get_project(supabase, payload.project_id)

        existing = (
            supabase.table("leads")
            .select("*")
            .eq("project_id", payload.project_id)
            .eq("phone", payload.phone)
            .maybe_single()
            .execute()
        )
        existing_row = self._response_data(existing) or {}

        lead_row = existing_row or self._build_lead_record(project_row, payload)
        if existing_row:
            update_payload = {
                "name": payload.name,
                "email": payload.email or existing_row.get("email"),
                "interest": existing_row.get("interest")
                or payload.project_name
                or project_row.get("project_name")
                or "Property enquiry",
                "budget": payload.budget or existing_row.get("budget") or "Not specified",
                "source": existing_row.get("source") or "Website",
                "project_id": project_row.get("id") or payload.project_id,
            }
            result = (
                supabase.table("leads")
                .update(update_payload)
                .eq("id", existing_row["id"])
                .execute()
            )
            lead_row = (self._response_data(result) or [existing_row])[0]
        else:
            try:
                insert_result = supabase.table("leads").insert(lead_row).execute()
                lead_row = (self._response_data(insert_result) or [lead_row])[0]
            except Exception as error:
                fallback_record = dict(lead_row)
                fallback_record["project_id"] = None
                print(f"Lead insert fallback without project_id: {error}")
                insert_result = supabase.table("leads").insert(fallback_record).execute()
                lead_row = (self._response_data(insert_result) or [fallback_record])[0]

        CHAT_HISTORY.setdefault(lead_row["id"], [])
        return self._map_lead_response(
            lead_row,
            CHAT_HISTORY.get(lead_row["id"], []),
            payload.project_name or project_row.get("project_name") or "",
            payload.builder_name or self._builder_name(project_row),
        )

    def get_response(self, lead_id: str, message: str) -> PropertyChatResponse:
        supabase = self._get_supabase()
        lead_row = self._get_lead(supabase, lead_id)
        if not lead_row:
            raise ValueError("Lead not found.")

        project_row = self._get_project(supabase, lead_row["project_id"])
        if not project_row:
            raise ValueError("Project not found.")

        history = CHAT_HISTORY.setdefault(lead_id, [])
        prompt = build_property_chat_prompt(
            project_name=project_row.get("project_name") or lead_row.get("interest") or "this project",
            builder_name=self._builder_name(project_row),
            location=project_row.get("address") or project_row.get("zone") or lead_row.get("location"),
            city=project_row.get("city") or lead_row.get("city"),
            user_name=lead_row.get("name"),
        )

        messages = [SystemMessage(content=prompt), *self._history_to_messages(history), HumanMessage(content=message)]

        try:
            response = self.model.invoke(messages)
            reply = response.content
        except Exception as error:
            error_text = str(error)
            if "RESOURCE_EXHAUSTED" in error_text or "quota" in error_text.lower():
                reply = (
                    "The personalized chatbot is temporarily unavailable because the Gemini quota is exhausted. "
                    "Please try again later."
                )
            else:
                reply = "The personalized chatbot ran into an unexpected issue. Please try again in a moment."
            print(f"Property chat service error: {error}")

        history.extend(
            [
                self._history_item("user", message),
                self._history_item("assistant", reply),
            ]
        )
        if len(history) > 40:
            del history[:-40]

        supabase.table("leads").update({"updated_at": datetime.now(timezone.utc).isoformat()}).eq("id", lead_id).execute()

        return PropertyChatResponse(
            lead_id=lead_row["id"],
            reply=reply,
            chat_history=history,
        )

    def get_lead(self, lead_id: str) -> PropertyLeadResponse:
        supabase = self._get_supabase()
        lead_row = self._get_lead(supabase, lead_id)
        if not lead_row:
            raise ValueError("Lead not found.")
        history = CHAT_HISTORY.setdefault(lead_id, [])
        project_row = self._get_project(supabase, lead_row["project_id"])
        return self._map_lead_response(
            lead_row,
            history,
            project_row.get("project_name") or lead_row.get("interest") or "",
            self._builder_name(project_row),
        )

    def clear_history(self, lead_id: str) -> None:
        CHAT_HISTORY.pop(lead_id, None)

    def _get_supabase(self) -> Client:
        if not settings.has_supabase:
            raise RuntimeError(
                "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set for personalized property chat."
            )

        if self._supabase is None:
            self._supabase = create_client(settings.supabase_url, settings.supabase_service_role_key)
        return self._supabase

    @staticmethod
    def _build_lead_record(project_row: dict, payload: PropertyLeadCreateRequest) -> dict:
        return {
            "name": payload.name,
            "phone": payload.phone,
            "email": payload.email,
            "source": "Website",
            "interest": payload.project_name or project_row.get("project_name") or "Property enquiry",
            "budget": payload.budget or "Not specified",
            "score": 50,
            "status": "New",
            "assigned_to": None,
            "project_id": project_row.get("id") or payload.project_id or None,
        }

    @staticmethod
    def _get_project(supabase: Client, project_id: str) -> dict:
        result = (
            supabase.table("projects")
            .select("id,project_name,builder,address,city,zone")
            .eq("id", project_id)
            .maybe_single()
            .execute()
        )
        return PropertyChatService._response_data(result) or {}

    @staticmethod
    def _get_lead(supabase: Client, lead_id: str) -> dict:
        result = supabase.table("leads").select("*").eq("id", lead_id).maybe_single().execute()
        return PropertyChatService._response_data(result) or {}

    @staticmethod
    def _builder_name(project_row: dict) -> str | None:
        builder = project_row.get("builder")
        if isinstance(builder, str) and builder.strip() and not PropertyChatService._looks_like_uuid(builder):
            return builder
        return None

    @staticmethod
    def _looks_like_uuid(value: str) -> bool:
        return bool(re.fullmatch(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", value))

    @staticmethod
    def _history_to_messages(history: list[dict]) -> list[HumanMessage | AIMessage]:
        messages: list[HumanMessage | AIMessage] = []
        for item in history:
            role = item.get("role")
            content = item.get("content", "")
            if role == "user":
                messages.append(HumanMessage(content=content))
            elif role == "assistant":
                messages.append(AIMessage(content=content))
        return messages

    @staticmethod
    def _history_item(role: str, content: str) -> dict:
        return {
            "role": role,
            "content": content,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

    @staticmethod
    def _response_data(response):
        if response is None:
            return None
        return getattr(response, "data", None)

    @staticmethod
    def _map_lead_response(
        row: dict,
        history: list[dict],
        project_name: str,
        builder_name: str | None,
    ) -> PropertyLeadResponse:
        return PropertyLeadResponse(
            lead_id=row["id"],
            project_id=row.get("project_id"),
            project_name=project_name or row.get("interest") or "",
            builder_name=builder_name,
            name=row.get("name") or "Guest",
            phone=row.get("phone") or "",
            email=row.get("email"),
            source=row.get("source") or "Website",
            interest=row.get("interest"),
            budget=row.get("budget") or "Not specified",
            score=row.get("score"),
            status=row.get("status") or "New",
            chat_history=history,
        )
