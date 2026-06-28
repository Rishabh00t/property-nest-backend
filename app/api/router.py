from fastapi import APIRouter

from app.api.endpoints.bot import chat_router
from app.api.endpoints.personalized_bot import personalized_chat_router
from app.api.endpoints.analytics import analytics_router

api_router = APIRouter()

api_router.include_router(chat_router, prefix="", tags=["chatbot"])
api_router.include_router(personalized_chat_router, prefix="", tags=["property-chat"])
api_router.include_router(analytics_router, prefix="/analytics", tags=["analytics"])
