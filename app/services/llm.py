from langchain_google_genai import ChatGoogleGenerativeAI

from app.core.config import settings

_model: ChatGoogleGenerativeAI | None = None


def get_chat_model() -> ChatGoogleGenerativeAI:
    """Shared Gemini chat model (same provider config as the website chat)."""
    global _model
    if _model is not None:
        return _model

    if settings.gemini_provider == "vertex":
        _model = ChatGoogleGenerativeAI(
            model=settings.gemini_model or "gemini-2.5-flash",
            api_key=settings.gemini_api_key or None,
            vertexai=True,
            project=settings.google_cloud_project or None,
            location=settings.google_cloud_location or None,
        )
    else:
        _model = ChatGoogleGenerativeAI(
            api_key=settings.gemini_api_key,
            model=settings.gemini_model or "models/gemini-2.5-flash",
        )
    return _model
