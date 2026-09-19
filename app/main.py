"""FastAPI application: recurring occurrence expansion."""

from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from . import __version__
from .engine import DEFAULT_MAX_OCCURRENCES, expand_occurrences
from .errors import APIError
from .schemas import OccurrenceRequest, OccurrenceResponse, HealthResponse

app = FastAPI(
    title="Recurrence Expansion API",
    description=(
        "Expand iCalendar RRULE / RDATE / EXDATE recurrence sets into concrete "
        "occurrences inside a UTC query window, with correct IANA time-zone, "
        "DST gap and repeated-wall-time (fold) handling. DST resolution is "
        "configurable per request via `dst_policy`: `gap` = `skip` (default) or "
        "`shift_forward`; `fold` = `both` (default), `earlier` or `later`."
    ),
    version=__version__,
)


def _max_occurrences() -> int:
    raw = os.getenv("APP_MAX_OCCURRENCES")
    if not raw:
        return DEFAULT_MAX_OCCURRENCES
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_MAX_OCCURRENCES
    return value if value > 0 else DEFAULT_MAX_OCCURRENCES


@app.exception_handler(APIError)
async def api_error_handler(_request: Request, exc: APIError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content=exc.to_dict())


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
    details: list[dict[str, Any]] = []
    for err in exc.errors():
        details.append(
            {
                "field": ".".join(str(p) for p in err.get("loc", [])) or None,
                "type": err.get("type"),
                "message": err.get("msg"),
            }
        )
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "validation_error",
                "message": "Request body failed validation.",
                "details": details,
            }
        },
    )


@app.get("/health", response_model=HealthResponse, tags=["health"])
async def health() -> HealthResponse:
    return HealthResponse(status="ok", version=__version__)


@app.post(
    "/occurrences",
    response_model=OccurrenceResponse,
    tags=["occurrences"],
    summary="Expand a recurrence rule into instances within a query interval",
    description=(
        "Expands DTSTART/RRULE/RDATE into concrete occurrences, minus EXDATE. "
        "DST handling is configurable via `dst_policy`: `gap` = `skip` (default) "
        "or `shift_forward` (non-existent wall times move past the gap, e.g. "
        "02:30 -> 03:30); `fold` = `both` (default), `earlier` or `later` for "
        "repeated wall times. EXDATE always matches the original local wall time."
    ),
)
async def occurrences(request: OccurrenceRequest) -> OccurrenceResponse:
    result = expand_occurrences(request.model_dump(), max_occurrences=_max_occurrences())
    return OccurrenceResponse(**result)
