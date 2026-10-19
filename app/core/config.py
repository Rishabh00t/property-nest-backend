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
    razorpay_key_id: str = os.getenv("RAZORPAY_KEY_ID", "")
    razorpay_key_secret: str = os.getenv("RAZORPAY_KEY_SECRET", "")
    razorpay_webhook_secret: str = os.getenv("RAZORPAY_WEBHOOK_SECRET", "")
    # WhatsApp Cloud API (multi-builder chatbot). The verify token / app secret
    # here belong to the *platform's* Meta app; builders who bring their own
    # Meta app store their own per-account values instead.
    whatsapp_graph_version: str = os.getenv("WHATSAPP_GRAPH_VERSION", "v21.0")
    whatsapp_verify_token: str = os.getenv("WHATSAPP_VERIFY_TOKEN", "")
    whatsapp_app_secret: str = os.getenv("WHATSAPP_APP_SECRET", "")
    # Public https URL of this backend, used to show builders their webhook URL.
    public_backend_url: str = os.getenv("PUBLIC_BACKEND_URL", "")
    # Fernet key used to encrypt WhatsApp access tokens at rest. Generate with:
    # python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    encryption_key: str = os.getenv("ENCRYPTION_KEY", "")
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

    @property
    def has_razorpay(self) -> bool:
        return bool(self.razorpay_key_id and self.razorpay_key_secret)


settings = Settings()
