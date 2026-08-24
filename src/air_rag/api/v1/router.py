from __future__ import annotations

from fastapi import APIRouter

from air_rag.api.v1 import retrieval, system

api_router = APIRouter()
api_router.include_router(system.router)
api_router.include_router(retrieval.router)
