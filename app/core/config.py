import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


@dataclass
class Settings:
    app_name: str = os.getenv("APP_NAME", "PropCore Chatbot API")
    app_env: str = os.getenv("APP_ENV", "development")
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")
    supabase_url: str = os.getenv("SUPABASE_URL", "")
    supabase_service_role_key: str = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
    cors_origins: list[str] = field(
        default_factory=lambda: [
            "http://localhost:8080",
            "http://127.0.0.1:8080",
        ]
    )

    def __post_init__(self) -> None:
        if not self.gemini_api_key:
            raise EnvironmentError("GEMINI_API_KEY not set in environment variables.")

    @property
    def has_supabase(self) -> bool:
        return bool(self.supabase_url and self.supabase_service_role_key)


settings = Settings()
