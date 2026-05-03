import os
from celery import Celery

from app.core.config import settings
import app.db.models  # noqa: F401  (ensure all mappers are registered in worker process)


def _broker_url() -> str:
    # Prefer runtime environment variable if present.
    return os.getenv("REDIS_URL", settings.REDIS_URL)


celery_app = Celery(
    "constellation",
    broker=_broker_url(),
    backend=_broker_url(),
    include=["app.workers.video_tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
)


