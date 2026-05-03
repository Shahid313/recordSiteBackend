"""
Central place to import all SQLAlchemy models so that mapper configuration
works reliably across different processes (FastAPI app, Celery workers, scripts).

Importing this module must have no side effects besides registering mappers.
"""

from app.models.user import User  # noqa: F401
from app.models.user_profile import UserProfile  # noqa: F401
from app.models.project import Project  # noqa: F401
from app.models.video import Video  # noqa: F401
from app.models.panorama import Panorama  # noqa: F401
from app.models.connection import Connection  # noqa: F401
from app.models.edit_history import EditHistory  # noqa: F401
from app.models.floorplan import Floorplan  # noqa: F401


