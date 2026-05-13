import os
from celery import Celery

from app.core.config import settings
import app.db.models  # noqa: F401  (ensure all mappers are registered in worker process)


celery_app = Celery(
    "constellation",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=["app.workers.video_tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
)


