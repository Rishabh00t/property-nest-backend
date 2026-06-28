from fastapi import APIRouter, HTTPException
from supabase import create_client

from app.core.config import settings
from app.schemas.analytics import AnalyticsEventCreate, AnalyticsIdentifyRequest
from app.services.lead_scoring_service import LeadScoringService

analytics_router = APIRouter()
scoring_service = LeadScoringService()

@analytics_router.post("/events")
async def create_event(payload: AnalyticsEventCreate):
    """
    Log a user interaction event on a property details page.
    If a lead_id is provided, automatically trigger score recalculation.
    """
    if not settings.has_supabase:
        raise HTTPException(
            status_code=500,
            detail="Supabase credentials are not configured on the backend."
        )

    supabase = scoring_service._get_supabase()

    event_data = {
        "session_id": payload.session_id,
        "lead_id": payload.lead_id,
        "project_id": payload.project_id,
        "event_type": payload.event_type,
        "metadata": payload.metadata or {},
    }

    try:
        # Insert event into Supabase
        result = supabase.table("analytics_events").insert(event_data).execute()
        
        # If lead is identified, recalculate score
        score_info = None
        if payload.lead_id:
            score_info = scoring_service.recalculate_score(payload.lead_id)

        return {
            "success": True,
            "event": (result.data or [event_data])[0],
            "scoring": score_info
        }
    except Exception as e:
        print(f"Error logging analytics event: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to log event: {str(e)}"
        )

@analytics_router.post("/identify")
async def identify_lead(payload: AnalyticsIdentifyRequest):
    """
    Map all past anonymous events for a session_id to a new lead_id
    and recalculate the lead's score.
    """
    if not settings.has_supabase:
        raise HTTPException(
            status_code=500,
            detail="Supabase credentials are not configured on the backend."
        )

    try:
        score_info = scoring_service.identify_lead_session(
            session_id=payload.session_id,
            lead_id=payload.lead_id
        )
        return {
            "success": True,
            "scoring": score_info
        }
    except Exception as e:
        print(f"Error identifying session lead: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to identify lead: {str(e)}"
        )
