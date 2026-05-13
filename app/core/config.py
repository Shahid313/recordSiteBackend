from pydantic_settings import BaseSettings
from typing import List
import os
from dotenv import load_dotenv

load_dotenv()

class Settings(BaseSettings):
    # API Settings
    PROJECT_NAME: str = "Constellation API"
    VERSION: str = "1.0.0"
    API_V1_STR: str = "/api/v1"
    
    # Security
    SECRET_KEY: str = os.getenv("SECRET_KEY")
    ALGORITHM: str = os.getenv("ALGORITHM", "HS256")
    ACCESS_TOKEN_EXPIRE_MINUTES: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", 10080))
    
    # Database
    DATABASE_URL: str = os.getenv("DATABASE_URL")

    # Redis / Celery
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")

    # Local storage (filesystem)
    STORAGE_ROOT: str = os.getenv("STORAGE_ROOT", "storage")

    # Object storage backend selector: "local" (default, dev) or "r2" (Cloudflare R2 in prod).
    STORAGE_BACKEND: str = os.getenv("STORAGE_BACKEND", "local").lower()

    # Cloudflare R2 (S3-compatible) configuration. Required when STORAGE_BACKEND="r2".
    R2_ACCOUNT_ID: str = os.getenv("R2_ACCOUNT_ID", "")
    R2_ACCESS_KEY_ID: str = os.getenv("R2_ACCESS_KEY_ID", "")
    R2_SECRET_ACCESS_KEY: str = os.getenv("R2_SECRET_ACCESS_KEY", "")
    R2_BUCKET_NAME: str = os.getenv("R2_BUCKET_NAME", "")
    R2_ENDPOINT_URL: str = os.getenv("R2_ENDPOINT_URL", "")
    # Public r2.dev URL (or custom domain) used to serve files directly to the browser.
    R2_PUBLIC_URL: str = os.getenv("R2_PUBLIC_URL", "")

    # Upload / processing limits
    MAX_VIDEO_SIZE_BYTES: int = int(os.getenv("MAX_VIDEO_SIZE_BYTES", str(2 * 1024 * 1024 * 1024)))  # 2GB
    FRAME_EXTRACTION_INTERVAL_SECONDS: int = int(os.getenv("FRAME_EXTRACTION_INTERVAL_SECONDS", "1"))

    # Cookie / auth behavior. In production with frontend on a different domain,
    # the browser requires SameSite=None and Secure=True for cross-site cookies.
    COOKIE_SECURE: bool = os.getenv("COOKIE_SECURE", "false").lower() == "true"
    COOKIE_SAMESITE: str = os.getenv("COOKIE_SAMESITE", "lax")  # "lax" for dev, "none" for cross-site prod

    # CORS — accepts either a JSON list (e.g. ["https://a", "http://b"]) or a
    # comma-separated string (e.g. "https://a,http://b").
    BACKEND_CORS_ORIGINS: List[str] = []

    class Config:
        case_sensitive = True


def _parse_cors_origins() -> List[str]:
    raw = os.getenv(
        "BACKEND_CORS_ORIGINS",
        "http://localhost:3000,http://localhost:5173",
    ).strip()
    if not raw:
        return []
    if raw.startswith("["):
        import json
        try:
            data = json.loads(raw)
            return [str(x).strip() for x in data if str(x).strip()]
        except Exception:
            pass
    return [o.strip() for o in raw.split(",") if o.strip()]

settings = Settings()
settings.BACKEND_CORS_ORIGINS = _parse_cors_origins()