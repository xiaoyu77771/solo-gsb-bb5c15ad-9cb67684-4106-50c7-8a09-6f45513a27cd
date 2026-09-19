"""Structured error types for the occurrence expansion API."""

from __future__ import annotations

from typing import Any


class APIError(Exception):
    """An error that is rendered to clients as a structured JSON payload."""

    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = 400,
        details: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or []

    def to_dict(self) -> dict[str, Any]:
        body: dict[str, Any] = {"error": {"code": self.code, "message": self.message}}
        if self.details:
            body["error"]["details"] = self.details
        return body


def invalid_timezone(tz_name: str) -> APIError:
    return APIError(
        code="invalid_timezone",
        message=f"{tz_name!r} is not a valid IANA time zone name (e.g. 'Asia/Shanghai').",
    )


def invalid_rrule(message: str) -> APIError:
    return APIError(
        code="invalid_rrule",
        message=f"Invalid RRULE: {message}",
    )


def invalid_interval(message: str) -> APIError:
    return APIError(
        code="invalid_interval",
        message=message,
    )


def invalid_datetime(field: str, value: Any, reason: str) -> APIError:
    return APIError(
        code="invalid_datetime",
        message=f"Invalid datetime for field {field!r}: {reason}",
        details=[{"field": field, "value": value, "reason": reason}],
    )


def invalid_dst_policy(message: str) -> APIError:
    """Bad ``dst_policy`` value; rendered as the unified 422 validation error."""
    return APIError(
        code="validation_error",
        message=f"Invalid dst_policy: {message}",
        status_code=422,
    )
