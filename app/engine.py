"""Recurrence expansion engine.

The expansion works entirely with *naive local wall times* in the requested
IANA zone (which is how ``dateutil.rrule`` generates instances) and converts
to real instants only at the boundary, where DST gaps and folds are resolved
according to the optional ``dst_policy`` request field:

* a wall time that does not exist (spring-forward gap) is skipped
  (``gap='skip'``, the default) or moved forward past the gap by the size of
  the offset jump (``gap='shift_forward'``, e.g. 02:30 -> 03:30 in a one-hour
  jump);
* a wall time that happens twice (fall-back fold) yields two occurrences,
  ``fold=0`` (offset before the transition) and ``fold=1`` (offset after),
  unless ``fold='earlier'``/``'later'`` keeps only one pass.

The policy applies to DTSTART, RRULE and RDATE. EXDATE matches the *original*
local wall time per RFC 5545 (even when the occurrence was shifted), and the
UTC instants produced by the RRULE always take precedence over RDATE for the
same instant, including collisions created by a gap shift.

``expand_business_occurrences`` adds a business-day adjustment stage in front
of the DST resolution: each raw wall-clock instance is first moved to a
business day (skipping weekends and holidays) under the ``following``,
``preceding`` or ``modified_following`` convention, keeping its hour, minute,
second and sub-second part. Several raw instances may land on the same UTC
instant after adjustment; such instances are merged and their original wall
times listed in ``originals``. EXDATE still matches the *unadjusted* wall
times.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone, tzinfo
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dateutil.parser.isoparser import isoparser
from dateutil.rrule import rrulestr

from .errors import (
    APIError,
    invalid_business_calendar,
    invalid_datetime,
    invalid_dst_policy,
    invalid_interval,
    invalid_rrule,
    invalid_timezone,
)

_iso = isoparser()

# Safety margin (in local wall time) around the query window so that an
# instance whose wall time sits outside the naive window but whose UTC
# instant falls inside it (around DST / date-line transitions) is not lost.
_WINDOW_MARGIN = timedelta(days=2)

# Guard against pathological rules (e.g. FREQ=SECONDLY over a huge range).
DEFAULT_MAX_OCCURRENCES = 10_000

# Valid dst_policy values; the defaults preserve the historical behaviour.
_GAP_POLICIES = ("skip", "shift_forward")
_FOLD_POLICIES = ("both", "earlier", "later")


def parse_dst_policy(raw: Any) -> tuple[str, str]:
    """Validate the optional ``dst_policy`` mapping; defaults are skip/both.

    The API schema already constrains the values, so this mainly protects
    direct engine callers; it raises the same unified ``validation_error``.
    """
    if raw is None:
        return "skip", "both"
    if not isinstance(raw, dict):
        raise invalid_dst_policy("expected an object like {'gap': 'skip', 'fold': 'both'}")
    gap = raw.get("gap", "skip")
    fold = raw.get("fold", "both")
    if gap not in _GAP_POLICIES:
        raise invalid_dst_policy(f"'gap' must be one of {list(_GAP_POLICIES)}")
    if fold not in _FOLD_POLICIES:
        raise invalid_dst_policy(f"'fold' must be one of {list(_FOLD_POLICIES)}")
    return gap, fold


def get_zone(tz_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError):
        raise invalid_timezone(tz_name) from None


def _parse_compact(text: str) -> datetime:
    """Parse a compact iCalendar value: YYYYMMDD[THHMM[SS]][Z]."""
    body = text
    tz = None
    if body.endswith(("Z", "z")):
        from datetime import timezone as _tz

        body, tz = body[:-1], _tz.utc
    for fmt in ("%Y%m%dT%H%M%S", "%Y%m%dT%H%M", "%Y%m%d"):
        try:
            dt = datetime.strptime(body, fmt)
        except ValueError:
            continue
        return dt.replace(tzinfo=tz)
    raise ValueError(f"unsupported date-time form: {text!r}")


def _parse_iso(value: str, field: str, *, require_time: bool) -> tuple[datetime, bool]:
    """Return ``(datetime, aware)``.

    Date-only input is accepted as local midnight when ``require_time`` is
    False. Raises a structured ``invalid_datetime`` error on bad syntax.
    """
    raw = value.strip()
    try:
        parsed = _iso.isoparse(raw)
    except (ValueError, OverflowError, TypeError):
        try:  # also accept the compact iCal form (YYYYMMDD[ T HHMMSS])
            parsed = _parse_compact(raw)
        except (ValueError, OverflowError, TypeError):
            raise invalid_datetime(field, value, "not a valid ISO-8601 date/time") from None
    if not isinstance(parsed, datetime):
        if require_time:
            raise invalid_datetime(field, value, "a full date-time with an offset is required")
        parsed = datetime(parsed.year, parsed.month, parsed.day)
    return parsed, parsed.tzinfo is not None


def parse_local_wall(value: str, field: str, zone: tzinfo) -> datetime:
    """Parse a wall time in ``zone``. Aware strings are converted to local wall time."""
    dt, aware = _parse_iso(value, field, require_time=False)
    if aware:
        return dt.astimezone(zone).replace(tzinfo=None)
    return dt.replace(tzinfo=None)


def parse_aware_bound(value: str, field: str) -> datetime:
    """Parse a query-window bound; it must carry an explicit UTC offset."""
    dt, aware = _parse_iso(value, field, require_time=True)
    if not aware:
        raise invalid_datetime(field, value, "an explicit UTC offset ('Z' or '+HH:MM') is required")
    return dt.astimezone(timezone.utc)


def _compact(dt: datetime) -> str:
    """Render a naive datetime as the compact iCal form (fractional seconds floored)."""
    return dt.strftime("%Y%m%dT%H%M%S")


def _parse_rrule_datetime(value: str, zone: tzinfo) -> datetime:
    """Parse an UNTIL value: compact iCal, ISO local, or ISO with offset ('Z' = UTC)."""
    text = value.strip()
    for fmt in ("%Y%m%dT%H%M%S", "%Y%m%dT%H%M", "%Y%m%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    dt, aware = _parse_iso(value, "UNTIL", require_time=False)
    if aware:
        return dt.astimezone(zone).replace(tzinfo=None)
    return dt


def build_rrule(raw_rrule: str, dtstart_naive: datetime, zone: tzinfo):
    """Validate and construct a dateutil rrule from an RFC 5545 RRULE string."""
    text = raw_rrule.strip()
    if text.upper().startswith("RRULE:"):
        text = text[6:]

    tokens: dict[str, str] = {}
    for part in text.split(";"):
        if not part:
            continue
        if "=" not in part:
            raise invalid_rrule(f"malformed rule part {part!r}; expected KEY=VALUE")
        key, val = part.split("=", 1)
        key, val = key.strip().upper(), val.strip()
        if not key or not val:
            raise invalid_rrule(f"malformed rule part {part!r}")
        if key in tokens:
            raise invalid_rrule(f"duplicate rule part {key}")
        tokens[key] = val

    if "FREQ" not in tokens:
        raise invalid_rrule("FREQ is required")
    if "UNTIL" in tokens and "COUNT" in tokens:
        raise invalid_rrule("UNTIL and COUNT are mutually exclusive")

    # dateutil accepts INTERVAL=0 (yielding endless duplicates) and does not
    # validate COUNT/INTERVAL sign; enforce RFC 5545 positive-integer rules.
    for int_key in ("COUNT", "INTERVAL"):
        if int_key in tokens:
            try:
                int_val = int(tokens[int_key])
            except ValueError:
                raise invalid_rrule(f"{int_key} must be a positive integer") from None
            if int_val < 1:
                raise invalid_rrule(f"{int_key} must be a positive integer")

    def _int_list(key: str) -> list[int]:
        try:
            return [int(p) for p in tokens[key].split(",") if p != ""]
        except ValueError:
            raise invalid_rrule(f"{key} must be a comma-separated integer list") from None

    # dateutil silently ignores out-of-range values such as BYMONTHDAY=0.
    if "BYMONTHDAY" in tokens:
        for d in _int_list("BYMONTHDAY"):
            if not -31 <= d <= 31 or d == 0:
                raise invalid_rrule("BYMONTHDAY values must be between 1..31 or -1..-31")
    if "BYMONTH" in tokens:
        for m in _int_list("BYMONTH"):
            if not 1 <= m <= 12:
                raise invalid_rrule("BYMONTH values must be between 1 and 12")

    # dateutil compares UNTIL against naive generated instances, so an UNTIL
    # given in UTC ('Z') or any offset is converted to the zone's wall time.
    if "UNTIL" in tokens:
        try:
            tokens["UNTIL"] = _compact(_parse_rrule_datetime(tokens["UNTIL"], zone))
        except APIError as exc:
            raise invalid_rrule(exc.message) from None

    ordered = ["FREQ"] + sorted(k for k in tokens if k != "FREQ")
    rule_text = "RRULE:" + ";".join(f"{k}={tokens[k]}" for k in ordered)
    full_text = f"DTSTART:{_compact(dtstart_naive)}\n{rule_text}"

    try:
        rule = rrulestr(full_text, compatible=False)
    except (ValueError, OverflowError, TypeError) as exc:
        raise invalid_rrule(str(exc) or "could not parse rule") from None
    return rule, dtstart_naive.microsecond


def localize_naive(naive: datetime, zone: tzinfo) -> list[tuple[datetime, int]]:
    """Resolve a naive local wall time to zero, one or two real instants.

    Returns ``[(aware_datetime, fold), ...]``:

    * ``[]`` for a non-existent time (spring-forward gap);
    * two entries otherwise: for a repeated wall time (fall-back fold) they
      are the two distinct instants, ``fold=0`` (offset before the transition)
      and ``fold=1`` (offset after); for a normal time both fold values
      resolve to the *same* instant, which therefore appears twice (dedupe
      with ``_distinct_instants`` when that matters).

    Note: ``aware.replace(tzinfo=None)`` merely strips the tzinfo rather than
    normalizing through the offset, so gap/fold detection is done by building
    both fold candidates, converting them to true UTC instants, and converting
    those instants back with each fold: an instant is valid only when its wall
    time comes back equal to the requested one.
    """
    results: list[tuple[datetime, int]] = []
    for fold in (0, 1):
        candidate = naive.replace(tzinfo=zone, fold=fold)
        instant = candidate.astimezone(timezone.utc)
        # Read the wall time/offset back from the real instant: the returned
        # fold tells us which pass of an ambiguous hour the instant belongs to.
        back = instant.astimezone(zone)
        if back.replace(tzinfo=None) != naive:
            continue
        results.append((back, back.fold))
    return results


def _gap_width(naive: datetime, zone: tzinfo) -> timedelta:
    """Width of the offset jump at a non-existent wall time.

    Per PEP 495, for a gap ``fold=0`` resolves with the offset in effect
    before the transition and ``fold=1`` with the one after it, so the
    difference is the size of the jump (positive for real IANA gaps).
    """
    before = naive.replace(tzinfo=zone, fold=0).utcoffset() or timedelta(0)
    after = naive.replace(tzinfo=zone, fold=1).utcoffset() or timedelta(0)
    return after - before


def _distinct_instants(candidates: list[tuple[datetime, int]]) -> list[tuple[datetime, int]]:
    """Drop duplicate UTC instants, keeping the earliest fold.

    ``localize_naive`` tries both fold values, so a wall time that occurs only
    once comes back as the same instant twice; a genuinely repeated wall time
    (fall-back fold) comes back as two distinct instants.
    """
    seen: set[datetime] = set()
    unique: list[tuple[datetime, int]] = []
    for aware, fold in candidates:
        instant = aware.astimezone(timezone.utc)
        if instant in seen:
            continue
        seen.add(instant)
        unique.append((aware, fold))
    return unique


def resolve_wall(
    naive: datetime,
    zone: tzinfo,
    *,
    gap: str = "skip",
    fold: str = "both",
) -> list[tuple[datetime, datetime, int]]:
    """Resolve a naive wall time to ``[(local_wall, aware, fold), ...]``.

    ``local_wall`` is the wall time that actually occurs: it equals ``naive``
    unless ``gap='shift_forward'`` pushed a non-existent time forward past the
    gap (e.g. 02:30 -> 03:30 in a one-hour spring-forward). ``fold`` selects
    which pass of a repeated wall time to keep; a wall time that occurs only
    once is always kept.
    """
    wall = naive
    candidates = _distinct_instants(localize_naive(wall, zone))
    if not candidates:
        if gap != "shift_forward":
            return []
        width = _gap_width(naive, zone)
        if width <= timedelta(0):
            return []
        wall = naive + width
        candidates = _distinct_instants(localize_naive(wall, zone))
    if fold != "both" and len(candidates) > 1:
        wanted = 0 if fold == "earlier" else 1
        candidates = [(aware, f) for aware, f in candidates if f == wanted]
    return [(wall, aware, f) for aware, f in candidates]


def _format_offset(delta: timedelta) -> str:
    total = int(delta.total_seconds())
    sign = "+" if total >= 0 else "-"
    total = abs(total)
    return f"{sign}{total // 3600:02d}:{(total % 3600) // 60:02d}"


def _make_output(
    naive_local: datetime,
    aware: datetime,
    fold: int,
    source: str,
) -> dict:
    utc_dt = aware.astimezone(timezone.utc)
    offset = aware.utcoffset() or timedelta(0)
    dst_delta = aware.dst()
    return {
        "local": naive_local.isoformat(),
        "utc": utc_dt.replace(tzinfo=None).isoformat() + "Z",
        "utc_offset": _format_offset(offset),
        "dst": bool(dst_delta and dst_delta.total_seconds() != 0),
        "fold": fold,
        "source": source,
    }


def _generate_rule_walls(
    raw_rule: str,
    dtstart: datetime,
    zone: tzinfo,
    lo_wall: datetime,
    hi_wall: datetime,
    max_occurrences: int,
) -> list[datetime]:
    """Generate raw RRULE wall times inside ``[lo_wall, hi_wall]``.

    dateutil generates instances from DTSTART one by one, so a rule starting
    long before the window (e.g. FREQ=SECONDLY in year 2000) would have
    millions of instances to skip. The total number of generated instances is
    capped as well as the number kept.
    """
    rule, dtstart_microsecond = build_rrule(raw_rule, dtstart, zone)
    rule_walls: list[datetime] = []
    scan_budget = max_occurrences * 5
    scanned = 0
    try:
        for wall in rule:
            # rrulestr parses DTSTART in whole seconds; restore the
            # sub-second part of the requested start on every instance.
            wall = wall.replace(microsecond=dtstart_microsecond)
            scanned += 1
            if scanned > scan_budget:
                raise APIError(
                    code="too_many_occurrences",
                    message=(
                        f"RRULE requires scanning more than {scan_budget} instances to "
                        "reach the query window; move start_local closer or add COUNT/UNTIL."
                    ),
                )
            if wall < lo_wall:
                continue
            if wall > hi_wall:
                break
            if len(rule_walls) >= max_occurrences:
                raise APIError(
                    code="too_many_occurrences",
                    message=(
                        f"RRULE expands to more than {max_occurrences} instances in the "
                        "query window; narrow the interval or add COUNT/UNTIL."
                    ),
                )
            rule_walls.append(wall)
    except APIError:
        raise
    except (ValueError, OverflowError, TypeError) as exc:
        raise invalid_rrule(str(exc) or "could not evaluate rule") from None
    return rule_walls


def expand_occurrences(payload: dict, max_occurrences: int = DEFAULT_MAX_OCCURRENCES) -> dict:
    """Expand a recurrence request into occurrences inside the query interval."""
    tz_name = payload["timezone"]
    zone = get_zone(tz_name)

    lo_utc = parse_aware_bound(payload["interval_start"], "interval_start")
    hi_utc = parse_aware_bound(payload["interval_end"], "interval_end")
    if lo_utc >= hi_utc:
        raise invalid_interval("interval_start must be strictly before interval_end")

    dtstart = parse_local_wall(payload["start_local"], "start_local", zone)

    gap_policy, fold_policy = parse_dst_policy(payload.get("dst_policy"))

    exdates = {
        parse_local_wall(v, "exdate", zone)
        for v in payload.get("exdate") or []
    }

    rdates: set[datetime] = set()
    for v in payload.get("rdate") or []:
        wall = parse_local_wall(v, "rdate", zone)
        if wall not in exdates:
            rdates.add(wall)

    # Naive wall-time window with margin; final filtering is done on UTC instants.
    lo_wall = lo_utc.astimezone(zone).replace(tzinfo=None) - _WINDOW_MARGIN
    hi_wall = hi_utc.astimezone(zone).replace(tzinfo=None) + _WINDOW_MARGIN

    # Collect instants keyed by UTC time. RRULE wins over RDATE on collision,
    # including collisions created when a gap shift moves an instance onto an
    # existing instant.
    # Value: (local_wall, aware_datetime, fold, source)
    chosen: dict[datetime, tuple[datetime, datetime, int, str]] = {}

    def add(naive_local: datetime, source: str) -> None:
        # EXDATE matches the *original* requested wall time (RFC 5545), even
        # when the gap policy shifts the occurrence to a later wall time.
        if naive_local in exdates:
            return
        if not (lo_wall <= naive_local <= hi_wall):
            return
        for local_wall, aware, fold in resolve_wall(
            naive_local, zone, gap=gap_policy, fold=fold_policy
        ):
            instant = aware.astimezone(timezone.utc)
            if not (lo_utc <= instant < hi_utc):
                continue
            if instant in chosen:
                if source == "rrule":
                    chosen[instant] = (local_wall, aware, fold, source)
                continue
            chosen[instant] = (local_wall, aware, fold, source)

    raw_rule = payload.get("rrule")
    if raw_rule:
        rule_walls = _generate_rule_walls(
            raw_rule, dtstart, zone, lo_wall, hi_wall, max_occurrences
        )
        for wall in rule_walls:
            add(wall, "rrule")
    else:
        # Without an RRULE the recurrence set still contains DTSTART (RFC 5545).
        add(dtstart, "rrule")

    if len(rdates) > max_occurrences:
        raise APIError(
            code="too_many_occurrences",
            message=f"More than {max_occurrences} RDATE entries were supplied.",
        )
    for wall in rdates:
        add(wall, "rdate")

    ordered = sorted(chosen.items(), key=lambda item: (item[0], item[1][2]))
    occurrences = [
        _make_output(naive_local, aware, fold, source)
        for _instant, (naive_local, aware, fold, source) in ordered
    ]
    return {"timezone": tz_name, "count": len(occurrences), "occurrences": occurrences}


# ---------------------------------------------------------------- business days


_BUSINESS_CONVENTIONS = ("following", "preceding", "modified_following")


def parse_business_calendar(payload: dict, zone: tzinfo) -> tuple[set[date], frozenset[int], str]:
    """Validate and parse the business-calendar fields.

    Returns ``(holidays, weekend_iso_days, convention)``. Holidays may be
    dates or date-times; aware values are converted to the zone's wall date.
    """
    convention = payload.get("business_day_convention", "following")
    if convention not in _BUSINESS_CONVENTIONS:
        raise invalid_business_calendar(
            f"'business_day_convention' must be one of {list(_BUSINESS_CONVENTIONS)}"
        )

    weekend = payload.get("weekend_days")
    if weekend is None:
        weekend = {6, 7}
    if not isinstance(weekend, (list, tuple)) or not weekend:
        raise invalid_business_calendar("'weekend_days' must be a non-empty list of ISO weekdays")
    weekend_days: set[int] = set()
    for day in weekend:
        if not isinstance(day, int) or isinstance(day, bool) or not 1 <= day <= 7:
            raise invalid_business_calendar(
                "'weekend_days' values must be integers between 1 (Monday) and 7 (Sunday)"
            )
        weekend_days.add(day)
    if len(weekend_days) >= 7:
        raise invalid_business_calendar("at least one ISO weekday must remain a business day")

    holidays: set[date] = set()
    for value in payload.get("holidays") or []:
        if not isinstance(value, str):
            raise invalid_business_calendar("'holidays' entries must be ISO-8601 date strings")
        wall = parse_local_wall(value, "holidays", zone)
        holidays.add(wall.date())
    return holidays, frozenset(weekend_days), convention


def _longest_nonbusiness_run(
    holidays: set[date], weekend_days: frozenset[int]
) -> int:
    """Length of the longest consecutive non-business-day run in the calendar.

    Weekends alone produce a fixed run (e.g. 2 for Sat/Sun); holidays can
    bridge or extend such runs. Scans a finite window around every holiday,
    which is sufficient because outside it the weekend pattern repeats without
    any holiday to break or extend it.
    """
    if not holidays:
        # Pure repeating week: scan two weeks so a block wrapping the
        # Sunday/Monday boundary (e.g. weekend [7, 1]) is counted.
        run = longest = 0
        for iso in list(range(1, 8)) * 2:
            run = run + 1 if iso in weekend_days else 0
            longest = max(longest, run)
        return longest

    span = max(len(holidays) + 14, 31)
    lo = min(holidays) - timedelta(days=span)
    hi = max(holidays) + timedelta(days=span)
    run = longest = 0
    day = lo
    while day <= hi:
        if day in holidays or day.isoweekday() in weekend_days:
            run += 1
            longest = max(longest, run)
        else:
            run = 0
        day += timedelta(days=1)
    return longest


def adjust_to_business_day(
    wall: datetime,
    holidays: set[date],
    weekend_days: frozenset[int],
    convention: str,
) -> datetime:
    """Move the *date* of ``wall`` onto a business day, keeping time of day.

    * ``following``: forward to the next business day (no adjustment when the
      raw day is already a business day);
    * ``preceding``: backward to the previous business day;
    * ``modified_following``: like ``following``, but when the forward move
      crosses into a different calendar month it is moved backward instead.
    """
    day = wall.date()

    def is_business(d: date) -> bool:
        return d not in holidays and d.isoweekday() not in weekend_days

    if convention == "following":
        while not is_business(day):
            day += timedelta(days=1)
    elif convention == "preceding":
        while not is_business(day):
            day -= timedelta(days=1)
    else:  # modified_following
        original_month = day.month
        while not is_business(day):
            day += timedelta(days=1)
        if day.month != original_month:
            day = wall.date()
            while not is_business(day):
                day -= timedelta(days=1)
    return datetime.combine(day, wall.time())


def _make_business_output(
    naive_local: datetime,
    aware: datetime,
    fold: int,
    originals: list[str],
) -> dict:
    output = _make_output(naive_local, aware, fold, source="rrule")
    output.pop("source", None)
    output["originals"] = originals
    return output


def expand_business_occurrences(
    payload: dict, max_occurrences: int = DEFAULT_MAX_OCCURRENCES
) -> dict:
    """Expand a recurrence request, adjusting raw instances to business days.

    The raw wall-clock instances come from the same DTSTART/RRULE/RDATE set as
    :func:`expand_occurrences`. Each one is first moved to a business day with
    its time of day preserved, then resolved to a real instant with the
    request's ``dst_policy``. Raw instances that resolve to the same UTC
    instant after adjustment are merged and their original wall times listed.
    EXDATE always matches the *unadjusted* raw wall time.
    """
    tz_name = payload["timezone"]
    zone = get_zone(tz_name)

    lo_utc = parse_aware_bound(payload["interval_start"], "interval_start")
    hi_utc = parse_aware_bound(payload["interval_end"], "interval_end")
    if lo_utc >= hi_utc:
        raise invalid_interval("interval_start must be strictly before interval_end")

    dtstart = parse_local_wall(payload["start_local"], "start_local", zone)

    gap_policy, fold_policy = parse_dst_policy(payload.get("dst_policy"))
    holidays, weekend_days, convention = parse_business_calendar(payload, zone)

    exdates = {
        parse_local_wall(v, "exdate", zone)
        for v in payload.get("exdate") or []
    }

    rdates: set[datetime] = set()
    for v in payload.get("rdate") or []:
        wall = parse_local_wall(v, "rdate", zone)
        if wall not in exdates:
            rdates.add(wall)

    # Adjustment can move an instance several days away from its raw date
    # (long holiday bridges, e.g. Golden Week): widen the raw-instance window
    # by the longest non-business run actually present in this calendar.
    margin = timedelta(days=_longest_nonbusiness_run(holidays, weekend_days) + 2)
    lo_wall = lo_utc.astimezone(zone).replace(tzinfo=None) - _WINDOW_MARGIN - margin
    hi_wall = hi_utc.astimezone(zone).replace(tzinfo=None) + _WINDOW_MARGIN + margin

    raw_walls: list[datetime] = []
    raw_rule = payload.get("rrule")
    if raw_rule:
        raw_walls.extend(
            _generate_rule_walls(raw_rule, dtstart, zone, lo_wall, hi_wall, max_occurrences)
        )
        if len(raw_walls) > max_occurrences:
            raise APIError(
                code="too_many_occurrences",
                message=(
                    f"RRULE expands to more than {max_occurrences} instances in the "
                    "query window; narrow the interval or add COUNT/UNTIL."
                ),
            )
    else:
        # Without an RRULE the recurrence set still contains DTSTART (RFC 5545).
        raw_walls.append(dtstart)

    if len(rdates) > max_occurrences:
        raise APIError(
            code="too_many_occurrences",
            message=f"More than {max_occurrences} RDATE entries were supplied.",
        )
    raw_walls.extend(rdates)

    # instant -> (adjusted wall, aware, fold, set of original raw wall times)
    chosen: dict[datetime, tuple[datetime, datetime, int, set[datetime]]] = {}
    for raw in raw_walls:
        # EXDATE matches the unadjusted original wall time, before the
        # business-day move and before any DST resolution.
        if raw in exdates:
            continue
        if not (lo_wall <= raw <= hi_wall):
            continue
        adjusted = adjust_to_business_day(raw, holidays, weekend_days, convention)
        for local_wall, aware, fold in resolve_wall(
            adjusted, zone, gap=gap_policy, fold=fold_policy
        ):
            instant = aware.astimezone(timezone.utc)
            if not (lo_utc <= instant < hi_utc):
                continue
            if instant in chosen:
                chosen[instant][3].add(raw)
                continue
            chosen[instant] = (local_wall, aware, fold, {raw})

    ordered = sorted(chosen.items(), key=lambda item: (item[0], item[1][2]))
    occurrences = [
        _make_business_output(
            naive_local,
            aware,
            fold,
            sorted(original.isoformat() for original in originals),
        )
        for _instant, (naive_local, aware, fold, originals) in ordered
    ]
    return {"timezone": tz_name, "count": len(occurrences), "occurrences": occurrences}
