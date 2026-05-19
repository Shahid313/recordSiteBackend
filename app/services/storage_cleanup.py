import logging
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models.floorplan import Floorplan
from app.models.panorama import Panorama
from app.models.video import Video
from app.services.storage import storage

logger = logging.getLogger(__name__)


class StorageCleanupError(RuntimeError):
    pass


@dataclass(frozen=True)
class StorageCleanupPlan:
    objects: tuple[tuple[str, str], ...]
    prefixes: tuple[tuple[str, str], ...] = ()


def _delete_object(category: str, path: str | None, deleted: set[tuple[str, str]], errors: list[str]) -> None:
    if not path:
        return
    key = (category, path)
    if key in deleted:
        return
    try:
        storage.delete_file(category, path)
        deleted.add(key)
    except Exception as exc:
        logger.exception("Failed to delete storage object %s/%s", category, path)
        errors.append(f"{category}/{path}: {exc}")


def _delete_prefix(category: str, prefix: str, deleted: set[tuple[str, str]]) -> None:
    try:
        files = storage.list_files(category, prefix=prefix)
    except Exception as exc:
        logger.exception("Failed to list storage prefix %s/%s", category, prefix)
        return

    for path in files:
        try:
            _delete_object(category, path, deleted, [])
        except Exception:
            logger.exception("Failed best-effort prefix cleanup for %s/%s", category, path)


def cleanup_storage_plan(plan: StorageCleanupPlan) -> None:
    deleted: set[tuple[str, str]] = set()
    errors: list[str] = []

    for category, path in plan.objects:
        _delete_object(category, path, deleted, errors)

    for category, prefix in plan.prefixes:
        _delete_prefix(category, prefix, deleted)

    if errors:
        logger.error("Storage cleanup finished with errors: %s", "; ".join(errors))


def build_video_cleanup_plan(db: Session, video: Video) -> StorageCleanupPlan:
    objects: list[tuple[str, str]] = []
    prefixes: list[tuple[str, str]] = []

    if video.storage_path:
        objects.append(("videos", video.storage_path))

    panoramas = db.query(Panorama).filter(Panorama.video_id == video.id).all()
    for pano in panoramas:
        if pano.storage_path:
            objects.append(("panoramas", pano.storage_path))
        if pano.thumbnail_path:
            objects.append(("thumbnails", pano.thumbnail_path))

    prefix = f"{video.project_id}/{video.id}/"
    prefixes.append(("panoramas", prefix))
    prefixes.append(("thumbnails", prefix))

    return StorageCleanupPlan(objects=tuple(objects), prefixes=tuple(prefixes))


def build_project_cleanup_plan(db: Session, project_id: int) -> StorageCleanupPlan:
    objects: list[tuple[str, str]] = []
    prefixes: list[tuple[str, str]] = []

    for video in db.query(Video).filter(Video.project_id == project_id).all():
        video_plan = build_video_cleanup_plan(db, video)
        objects.extend(video_plan.objects)
        prefixes.extend(video_plan.prefixes)

    for floorplan in db.query(Floorplan).filter(Floorplan.project_id == project_id).all():
        if floorplan.image_path:
            objects.append(("floorplans", floorplan.image_path))
        if floorplan.thumbnail_path:
            objects.append(("floorplans", floorplan.thumbnail_path))

    for category in ("videos", "panoramas", "thumbnails", "floorplans", "exports"):
        prefixes.append((category, f"{project_id}/"))

    return StorageCleanupPlan(objects=tuple(dict.fromkeys(objects)), prefixes=tuple(dict.fromkeys(prefixes)))


def cleanup_video_storage(db: Session, video: Video, deleted: set[tuple[str, str]] | None = None) -> None:
    deleted = deleted if deleted is not None else set()
    errors: list[str] = []

    _delete_object("videos", video.storage_path, deleted, errors)

    panoramas = db.query(Panorama).filter(Panorama.video_id == video.id).all()
    for pano in panoramas:
        _delete_object("panoramas", pano.storage_path, deleted, errors)
        _delete_object("thumbnails", pano.thumbnail_path, deleted, errors)

    # Best effort for orphaned/generated objects under the standard video prefix.
    prefix = f"{video.project_id}/{video.id}/"
    _delete_prefix("panoramas", prefix, deleted)
    _delete_prefix("thumbnails", prefix, deleted)

    if errors:
        raise StorageCleanupError("Failed to delete one or more video files: " + "; ".join(errors))


def cleanup_project_storage(db: Session, project_id: int) -> None:
    deleted: set[tuple[str, str]] = set()
    errors: list[str] = []

    for video in db.query(Video).filter(Video.project_id == project_id).all():
        try:
            cleanup_video_storage(db, video, deleted=deleted)
        except StorageCleanupError as exc:
            errors.append(str(exc))

    for floorplan in db.query(Floorplan).filter(Floorplan.project_id == project_id).all():
        _delete_object("floorplans", floorplan.image_path, deleted, errors)
        _delete_object("floorplans", floorplan.thumbnail_path, deleted, errors)

    # Best effort for any orphaned project-level objects not represented in DB rows.
    for category in ("videos", "panoramas", "thumbnails", "floorplans", "exports"):
        _delete_prefix(category, f"{project_id}/", deleted)

    if errors:
        raise StorageCleanupError("Failed to delete one or more project files: " + "; ".join(errors))
