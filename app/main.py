import time
import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.exc import OperationalError
from app.core.config import settings
from app.api.v1.api import api_router
from app.db.init_db import init_db

logger = logging.getLogger(__name__)

app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    openapi_url=f"{settings.API_V1_STR}/openapi.json"
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.BACKEND_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API router
app.include_router(api_router, prefix=settings.API_V1_STR)

@app.on_event("startup")
def startup_event():
    """
    Initialize database on startup, with retries to handle Railway's
    private-network DNS taking a moment to come up after container start.
    """
    max_attempts = 10
    delay_seconds = 3
    last_err = None
    for attempt in range(1, max_attempts + 1):
        try:
            init_db()
            return
        except OperationalError as e:
            last_err = e
            logger.warning(
                "DB not reachable yet (attempt %d/%d): %s",
                attempt, max_attempts, e.orig if hasattr(e, "orig") else e,
            )
            time.sleep(delay_seconds)
    # Exhausted retries — re-raise so the platform restarts the container
    raise last_err

@app.get("/")
def root():
    return {
        "message": f"{settings.PROJECT_NAME} v{settings.VERSION}",
        "docs": "/docs"
    }

@app.get("/health")
def health_check():
    return {"status": "healthy"}