"""Pydantic request/response models."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class DstPolicy(BaseModel):
    """How to resolve local wall times that hit a DST transition."""

    gap: Literal["skip", "shift_forward"] = Field(
        default="skip",
        description=(
            "Policy for a local wall time that does not exist (spring-forward gap): "
            "'skip' drops the occurrence; 'shift_forward' moves it forward past the "
            "gap by the size of the offset jump (e.g. 02:30 becomes 03:30 in a "
            "one-hour jump)."
        ),
    )
    fold: Literal["both", "earlier", "later"] = Field(
        default="both",
        description=(
            "Policy for a local wall time that occurs twice (fall-back fold): "
            "'both' returns one occurrence per pass; 'earlier' keeps only the first "
            "pass (fold=0); 'later' keeps only the second pass (fold=1)."
        ),
    )


class OccurrenceRequest(BaseModel):
    """Body of ``POST /occurrences``.

    ``start_local``, ``rdate`` and ``exdate`` values are *wall-clock* times in
    the requested ``timezone`` (naive ISO-8601 strings, e.g.
    ``2025-03-30T02:30:00``). Strings carrying an explicit offset (``Z`` or
    ``+08:00``) are also accepted and converted to the target zone's wall
    time before expansion.
    """

    timezone: str = Field(
        ...,
        min_length=1,
        description="IANA time zone name, e.g. 'America/New_York'.",
        examples=["America/New_York"],
    )
    start_local: str = Field(
        ...,
        min_length=1,
        description="DTSTART as local wall time in the given time zone.",
        examples=["2025-03-09T02:30:00"],
    )
    rrule: str | None = Field(
        default=None,
        description=(
            "RFC 5545 recurrence rule, with or without the 'RRULE:' prefix. "
            "FREQ is required. Optional; only RDATE instances are returned "
            "when omitted."
        ),
        examples=["FREQ=MONTHLY;BYMONTHDAY=-1;COUNT=5"],
    )
    interval_start: str = Field(
        ...,
        min_length=1,
        description="Inclusive query window start. Must carry an offset (e.g. 'Z' or '+08:00').",
        examples=["2025-01-01T00:00:00Z"],
    )
    interval_end: str = Field(
        ...,
        min_length=1,
        description="Exclusive query window end. Must carry an offset (e.g. 'Z' or '+08:00').",
        examples=["2026-01-01T00:00:00Z"],
    )
    exdate: list[str] = Field(
        default_factory=list,
        description="Local wall times to exclude (RFC 5545 EXDATE semantics).",
    )
    rdate: list[str] = Field(
        default_factory=list,
        description="Additional local wall times to add (RFC 5545 RDATE semantics).",
    )
    dst_policy: DstPolicy | None = Field(
        default=None,
        description=(
            "Optional DST resolution policy applied to DTSTART, RRULE and RDATE "
            "(EXDATE always matches the original local wall time). "
            "Defaults to {'gap': 'skip', 'fold': 'both'}."
        ),
    )


class BusinessOccurrenceRequest(OccurrenceRequest):
    """Body of ``POST /business-occurrences``.

    Adds the business-day calendar to :class:`OccurrenceRequest`: ``holidays``
    are non-business dates, ``weekend_days`` the weekly non-business weekdays
    (0=Monday … 6=Sunday, also accepts iCal abbreviations such as ``"SA"``),
    and ``business_day_convention`` selects how each raw wall-clock instance
    is moved onto a business day.
    """

    holidays: list[str] = Field(
        default_factory=list,
        description=(
            "Non-business dates (and/or date-times; only the date part in the "
            "requested timezone is used), e.g. ['2025-01-01']."
        ),
    )
    weekend_days: list[int | str] = Field(
        default_factory=lambda: [5, 6],
        description=(
            "Non-business weekdays: integers 0 (Monday) … 6 (Sunday), or iCal "
            "abbreviations 'MO'..'SU'. Defaults to [5, 6] (Saturday, Sunday)."
        ),
    )
    business_day_convention: Literal["following", "preceding", "modified_following"] = Field(
        default="following",
        description=(
            "How to move a raw instance onto a business day, keeping its "
            "hour/minute/second: 'following' moves forward; 'preceding' moves "
            "backward; 'modified_following' moves forward unless that crosses "
            "into a different calendar month, in which case it moves backward "
            "instead. The time-of-day is preserved by every convention."
        ),
    )


class Occurrence(BaseModel):
    local: str = Field(..., description="Wall-clock time in the requested zone (ISO-8601).")
    utc: str = Field(..., description="UTC instant with trailing 'Z' (ISO-8601).")
    utc_offset: str = Field(
        ...,
        description="UTC offset at that instant, formatted as '+HH:MM'/'-HH:MM'.",
    )
    dst: bool = Field(..., description="Whether daylight-saving time is in effect at that instant.")
    fold: Literal[0, 1] = Field(
        ...,
        description="0 for a regular/earlier instant; 1 for the second pass of a repeated wall time.",
    )
    source: Literal["rrule", "rdate"] = Field(
        ...,
        description="Whether the instance comes from the RRULE or an RDATE entry."
    )


class OccurrenceResponse(BaseModel):
    timezone: str
    count: int
    occurrences: list[Occurrence]


class BusinessOccurrence(Occurrence):
    originals: list[str] = Field(
        default_factory=list,
        description=(
            "Original local wall-clock instances (before the business-day "
            "adjustment) that were merged into this UTC instant, ISO-8601, "
            "sorted and de-duplicated. Contains the adjusted wall time itself "
            "when no other raw instance merged into it."
        ),
    )


class BusinessOccurrenceResponse(BaseModel):
    timezone: str
    count: int
    business_day_convention: Literal["following", "preceding", "modified_following"]
    occurrences: list[BusinessOccurrence]


class HealthResponse(BaseModel):
    status: Literal["ok"]
    version: str
