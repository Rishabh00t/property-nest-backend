import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

from app.utils.credentials import setup_google_credentials

load_dotenv()


@dataclass
class Settings:
    app_name: str = os.getenv("APP_NAME", "PropCore Chatbot API")
    app_env: str = os.getenv("APP_ENV", "development")
    gemini_provider: str = os.getenv("GEMINI_PROVIDER", "google").lower()
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")
    gemini_model: str = os.getenv("GEMINI_MODEL", "")
    google_cloud_project: str = os.getenv("GOOGLE_CLOUD_PROJECT", os.getenv("GCP_PROJECT", ""))
    google_cloud_location: str = os.getenv("GOOGLE_CLOUD_LOCATION", os.getenv("GCP_LOCATION", "us-central1"))
    supabase_url: str = os.getenv("SUPABASE_URL", "")
    supabase_service_role_key: str = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
    cors_origins: list[str] = field(
        default_factory=lambda: [
            "http://localhost:8080",
            "http://127.0.0.1:8080",
            "*"
        ]
    )

    def __post_init__(self) -> None:
        if self.gemini_provider == "google" and not self.gemini_api_key:
            raise EnvironmentError("GEMINI_API_KEY must be set in environment variables when GEMINI_PROVIDER is 'google'.")

        setup_google_credentials()

    @property
    def has_supabase(self) -> bool:
        return bool(self.supabase_url and self.supabase_service_role_key)


settings = Settings()
