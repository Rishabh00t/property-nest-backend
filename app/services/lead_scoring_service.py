from __future__ import annotations

from supabase import Client, create_client
from app.core.config import settings

class LeadScoringService:
    # Point weights for different event types
    SCORING_WEIGHTS = {
        "page_view": 5,
        "scroll_50": 10,
        "scroll_90": 15,
        "time_spent_30s": 5,
        "time_spent_60s": 10,
        "time_spent_120s": 15,
        "brochure_download": 20,
        "video_play": 15,
        "chat_open": 10,
        "visit_request": 40
    }

    def __init__(self):
        self._supabase: Client | None = None

    def _get_supabase(self) -> Client:
        if not settings.has_supabase:
            raise RuntimeError(
                "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set for dynamic lead scoring."
            )
        if self._supabase is None:
            self._supabase = create_client(settings.supabase_url, settings.supabase_service_role_key)
        return self._supabase

    def recalculate_score(self, lead_id: str) -> dict:
        """
        Fetches all events for a given lead_id, calculates the cumulative score,
        updates the 'leads' table in Supabase, and returns the updated fields.
        """
        supabase = self._get_supabase()

        # Fetch all events associated with this lead
        response = (
            supabase.table("analytics_events")
            .select("event_type")
            .eq("lead_id", lead_id)
            .execute()
        )
        events = response.data or []

        # Calculate scores
        unique_events = set()
        chat_message_count = 0

        for event in events:
            event_type = event.get("event_type")
            if event_type == "chat_message_sent":
                chat_message_count += 1
            else:
                unique_events.add(event_type)

        # Base score starts at 50 (when a lead contact details are created)
        total_points = 50

        # Add unique event weights
        for event_type in unique_events:
            weight = self.SCORING_WEIGHTS.get(event_type, 0)
            total_points += weight

        # Add chat message sent points (5 points per message, cap at 20 points)
        chat_points = min(20, chat_message_count * 5)
        total_points += chat_points

        # Cap total score at 100
        final_score = min(100, total_points)

        # Determine CRM status category based on final score
        # Note: If the lead has completed a visit request, we keep their status as 'Site visit'
        # unless their score qualifies them as Hot and they haven't set status yet.
        # Let's fetch current lead row first to respect existing statuses like 'Site visit'
        lead_response = supabase.table("leads").select("status").eq("id", lead_id).maybe_single().execute()
        current_status = (lead_response.data or {}).get("status", "New")

        if current_status == "Site visit":
            new_status = "Site visit"
        else:
            if final_score >= 80:
                new_status = "Hot"
            elif final_score >= 65:
                new_status = "Warm"
            else:
                new_status = "Cold"

        # Update the lead in database
        update_data = {
            "score": final_score,
            "status": new_status,
        }
        supabase.table("leads").update(update_data).eq("id", lead_id).execute()

        return {
            "lead_id": lead_id,
            "score": final_score,
            "status": new_status,
            "events_analyzed": len(events)
        }

    def identify_lead_session(self, session_id: str, lead_id: str) -> dict:
        """
        Maps all anonymous events for a session_id to a new lead_id,
        and triggers a score recalculation.
        """
        supabase = self._get_supabase()

        # Update all events with this session_id to have the lead_id
        supabase.table("analytics_events").update({"lead_id": lead_id}).eq("session_id", session_id).execute()

        # Trigger score recalculation
        return self.recalculate_score(lead_id)
