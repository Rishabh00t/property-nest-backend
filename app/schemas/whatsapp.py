from typing import Literal

from pydantic import BaseModel, Field


class WhatsAppAccountUpdate(BaseModel):
    # Credentials (from Meta Business > WhatsApp > API setup). Leave access_token
    # empty on later saves to keep the stored one.
    phone_number_id: str | None = None
    waba_id: str | None = None
    access_token: str | None = None
    # Only for builders using their own Meta app; "" clears it.
    app_secret: str | None = None

    # Bot behaviour
    bot_enabled: bool | None = None
    bot_name: str | None = Field(default=None, max_length=60)
    welcome_message: str | None = Field(default=None, max_length=1000)
    custom_instructions: str | None = Field(default=None, max_length=4000)
    default_project_id: str | None = None
    auto_create_leads: bool | None = None
    handoff_keywords: list[str] | None = None
    handoff_message: str | None = Field(default=None, max_length=1000)


class AgentMessageRequest(BaseModel):
    body: str = Field(min_length=1, max_length=4096)


class ConversationStatusUpdate(BaseModel):
    status: Literal["bot", "human", "closed"]
