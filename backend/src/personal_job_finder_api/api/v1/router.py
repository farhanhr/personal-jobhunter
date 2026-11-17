"""Aggregate router for API version 1."""

from fastapi import APIRouter

from personal_job_finder_api.api.v1.companies import router as companies_router
from personal_job_finder_api.api.v1.health import router as health_router
from personal_job_finder_api.api.v1.opportunities import router as opportunities_router
from personal_job_finder_api.api.v1.search_policy import router as search_policy_router
from personal_job_finder_api.api.v1.search_runs import router as search_runs_router
from personal_job_finder_api.api.v1.settings import router as settings_router
from personal_job_finder_api.api.v1.sheets import router as sheets_router
from personal_job_finder_api.api.v1.sync import router as sync_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(companies_router)
api_router.include_router(opportunities_router)
api_router.include_router(search_policy_router)
api_router.include_router(search_runs_router)
api_router.include_router(settings_router)
api_router.include_router(sheets_router)
api_router.include_router(sync_router)
