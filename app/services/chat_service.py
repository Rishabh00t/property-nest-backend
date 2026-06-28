from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI

from app.core.config import settings
from app.core.prompts import PROP_CORE_SYSTEM_PROMPT


class ChatService:
    def __init__(self):
        if settings.gemini_provider == "vertex":
            model_name = settings.gemini_model or "gemini-2.5-flash"
            self.model = ChatGoogleGenerativeAI(
                model=model_name,
                api_key=settings.gemini_api_key or None,
                vertexai=True,
                project=settings.google_cloud_project or None,
                location=settings.google_cloud_location or None,
            )
        else:
            model_name = settings.gemini_model or "models/gemini-2.5-flash"
            self.model = ChatGoogleGenerativeAI(
                api_key=settings.gemini_api_key,
                model=model_name,
            )
        self.system_message = SystemMessage(content=PROP_CORE_SYSTEM_PROMPT)
        self.history: list[SystemMessage | HumanMessage | AIMessage] = []

    def get_response(self, query: str) -> str:
        messages = [self.system_message, *self.history, HumanMessage(content=query)]
        try:
            response = self.model.invoke(messages)
            reply = response.content
        except Exception as error:
            error_text = str(error)
            if "RESOURCE_EXHAUSTED" in error_text or "quota" in error_text.lower():
                reply = (
                    "PropCore AI is temporarily unavailable because the Gemini quota is exhausted. "
                    "Please try again later, or ask me for a property-specific fallback answer."
                )
            else:
                reply = (
                    "PropCore AI ran into an unexpected issue. "
                    "Please try again in a moment."
                )
            print(f"Chat service error: {error}")

        self.history.extend([HumanMessage(content=query), AIMessage(content=reply)])
        return reply

    def clear_memory(self) -> None:
        self.history.clear()
