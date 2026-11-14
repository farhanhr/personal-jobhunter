"""Health endpoint used by the local dashboard and operators."""

from fastapi import APIRouter
from pydantic import BaseModel

from personal_job_finder_api.core.config import settings

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Return a small, stable payload that proves the API process is alive."""
    return HealthResponse(
        status="ok",
        service=settings.service_name,
        version=settings.version,
    )
