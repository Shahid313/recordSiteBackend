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

    # Upload / processing limits
    MAX_VIDEO_SIZE_BYTES: int = int(os.getenv("MAX_VIDEO_SIZE_BYTES", str(2 * 1024 * 1024 * 1024)))  # 2GB
    FRAME_EXTRACTION_INTERVAL_SECONDS: int = int(os.getenv("FRAME_EXTRACTION_INTERVAL_SECONDS", "1"))
    
    # CORS
    BACKEND_CORS_ORIGINS: List[str] = ["http://localhost:3000", "http://localhost:5173"]
    
    class Config:
        case_sensitive = True

settings = Settings()