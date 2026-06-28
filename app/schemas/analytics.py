from pydantic import BaseModel, Field

class AnalyticsEventCreate(BaseModel):
    session_id: str = Field(..., description="Unique temporary session ID for tracking guests")
    lead_id: str | None = Field(None, description="UUID of the converted lead, if identified")
    project_id: str = Field(..., description="UUID of the property project")
    event_type: str = Field(..., description="Type of user action (e.g., scroll_50, brochure_download)")
    metadata: dict | None = Field(default_factory=dict, description="Additional context parameters")

class AnalyticsIdentifyRequest(BaseModel):
    session_id: str = Field(..., description="The guest session ID to merge from")
    lead_id: str = Field(..., description="The newly created lead ID to map events to")
