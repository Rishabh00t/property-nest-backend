from pydantic import BaseModel, Field


class PropertyLeadCreateRequest(BaseModel):
    project_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    phone: str = Field(min_length=1)
    email: str | None = None
    budget: str | None = None
    project_name: str | None = None
    builder_name: str | None = None
    location: str | None = None
    city: str | None = None


class PropertyLeadResponse(BaseModel):
    lead_id: str
    project_id: str | None
    project_name: str
    builder_name: str | None = None
    name: str
    phone: str
    email: str | None = None
    source: str
    interest: str | None = None
    budget: str
    score: int | None = None
    status: str
    chat_history: list[dict]


class PropertyChatMessageRequest(BaseModel):
    message: str = Field(min_length=1)


class PropertyChatResponse(BaseModel):
    lead_id: str
    reply: str
    chat_history: list[dict]
