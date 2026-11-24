"""Master CV upload, retrieval, and manual editing (ADR-030).

The CV never leaves the machine except as the prompt sent to the provider the
user configured. Responses are ``no-store`` and never include the uploaded file
bytes, only the extracted text the user already owns.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Request, Response, UploadFile
from pydantic import BaseModel

from personal_job_finder_api.ai.errors import AIServiceError
from personal_job_finder_api.api.errors import ApiError
from personal_job_finder_api.api.security import validate_local_request
from personal_job_finder_api.core.time import format_utc
from personal_job_finder_api.cv.models import CandidateProfile
from personal_job_finder_api.cv.pdf import (
    PDFExtractionError,
    extract_text_from_pdf,
    normalize_pasted_text,
)
from personal_job_finder_api.cv.service import CandidateProfileService, StoredCandidateProfile

router = APIRouter(
    prefix="/cv",
    tags=["cv"],
    dependencies=[Depends(validate_local_request)],
)

MAX_UPLOAD_BYTES = 5 * 1024 * 1024


class CVProfileResponse(BaseModel):
    has_profile: bool
    profile: CandidateProfile | None = None
    file_name: str | None = None
    updated_at_utc: str | None = None


def _service(request: Request) -> CandidateProfileService:
    return CandidateProfileService(
        request.app.state.engine,
        timeout=request.app.state.settings.ai_timeout_seconds,
        clock=request.app.state.clock,
    )


def _to_response(stored: StoredCandidateProfile | None) -> CVProfileResponse:
    if stored is None:
        return CVProfileResponse(has_profile=False)
    return CVProfileResponse(
        has_profile=True,
        profile=stored.profile,
        file_name=stored.file_name,
        updated_at_utc=None if stored.updated_at is None else format_utc(stored.updated_at),
    )


def _api_error(error: AIServiceError) -> ApiError:
    """Map a domain failure to the status code the Settings UI expects."""

    status_code = {
        "ai_not_configured": 400,
        "profile_not_found": 404,
    }.get(error.code, 502)
    return ApiError(status_code, error.code, error.message)


async def _resolve_cv_input(file: UploadFile | None, text: str | None) -> tuple[str, str | None]:
    """Turn either input into extracted text plus the original file name."""

    if file is not None:
        filename = (file.filename or "").strip()
        if not filename.casefold().endswith(".pdf"):
            raise ApiError(415, "unsupported_file_type", "Upload the CV as a PDF file.")
        data = await file.read(MAX_UPLOAD_BYTES + 1)
        if len(data) > MAX_UPLOAD_BYTES:
            raise ApiError(413, "file_too_large", "The PDF is larger than 5 MB.")
        try:
            return extract_text_from_pdf(data), filename
        except PDFExtractionError as error:
            raise ApiError(400, error.code, error.message) from None

    if text is not None and text.strip():
        try:
            return normalize_pasted_text(text), None
        except PDFExtractionError as error:
            raise ApiError(400, error.code, error.message) from None

    raise ApiError(
        400,
        "missing_cv_input",
        "Upload a PDF or paste the CV text to continue.",
    )


@router.get("/profile", response_model=CVProfileResponse)
def get_profile(request: Request, response: Response) -> CVProfileResponse:
    response.headers["Cache-Control"] = "no-store"
    return _to_response(_service(request).load())


@router.post("/upload", response_model=CVProfileResponse)
async def upload_cv(
    request: Request,
    response: Response,
    file: Annotated[UploadFile | None, File()] = None,
    text: Annotated[str | None, Form()] = None,
) -> CVProfileResponse:
    """Extract the CV text, parse it with the configured provider, and store it."""

    raw_text, file_name = await _resolve_cv_input(file, text)
    try:
        stored = await _service(request).parse_and_save(
            raw_text=raw_text,
            file_name=file_name,
        )
    except AIServiceError as error:
        raise _api_error(error) from None
    response.headers["Cache-Control"] = "no-store"
    return _to_response(stored)


@router.put("/profile", response_model=CVProfileResponse)
def put_profile(
    payload: CandidateProfile,
    request: Request,
    response: Response,
) -> CVProfileResponse:
    """Save the user's manual corrections to the parsed profile."""

    try:
        stored = _service(request).save_manual(payload)
    except AIServiceError as error:
        raise _api_error(error) from None
    response.headers["Cache-Control"] = "no-store"
    return _to_response(stored)
