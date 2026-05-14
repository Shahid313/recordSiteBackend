from fastapi import APIRouter
from app.api.v1.endpoints import auth
from app.api.v1.endpoints import projects, videos
from app.api.v1.endpoints import files
from app.api.v1.endpoints import admin
from app.api.v1.endpoints import users
from app.api.v1.endpoints import constellation
from app.api.v1.endpoints import panoramas
from app.api.v1.endpoints import editing
from app.api.v1.endpoints import floorplans
from app.api.v1.endpoints import collaboration

api_router = APIRouter()

api_router.include_router(auth.router, prefix="/auth", tags=["authentication"])
api_router.include_router(projects.router, prefix="/projects", tags=["projects"])
api_router.include_router(videos.router, prefix="/videos", tags=["videos"])
api_router.include_router(files.router, prefix="/files", tags=["files"])
api_router.include_router(admin.router, prefix="/admin", tags=["admin"])
api_router.include_router(users.router, prefix="/users", tags=["users"])
api_router.include_router(constellation.router, tags=["constellation"])
api_router.include_router(panoramas.router, tags=["panoramas"])
api_router.include_router(editing.router, tags=["editing"])
api_router.include_router(floorplans.router, tags=["floorplans"])
api_router.include_router(collaboration.router, tags=["collaboration"])
