"""HTTP-level tests for POST /occurrences, /business-occurrences and /health."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def post(body: dict):
    return client.post("/occurrences", json=body)


def post_business(body: dict):
    return client.post("/business-occurrences", json=body)


@pytest.fixture(scope="module")
def api():
    return client


# ---------------------------------------------------------------- health ----

def test_health(api):
    resp = api.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "version" in body


# --------------------------------------------------------------- basics ----

def test_simple_daily_rule_sorted_by_utc(api):
    resp = post(
        {
            "timezone": "UTC",
            "start_local": "2025-01-01T09:00:00",
            "rrule": "FREQ=DAILY;COUNT=3",
            "interval_start": "2025-01-01T00:00:00Z",
            "interval_end": "2025-01-10T00:00:00Z",
        }
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 3
    utcs = [o["utc"] for o in body["occurrences"]]
    assert utcs == sorted(utcs)
    assert utcs == [
        "2025-01-01T09:00:00Z",
        "2025-01-02T09:00:00Z",
        "2025-01-03T09:00:00Z",
    ]
    assert all(o["utc_offset"] == "+00:00" and o["fold"] == 0 for o in body["occurrences"])


def test_interval_is_half_open(api):
    resp = post(
        {
            "timezone": "UTC",
            "start_local": "2025-01-01T00:00:00",
            "rrule": "FREQ=DAILY;COUNT=3",
            "interval_start": "2025-01-01T00:00:00Z",  # includes first
            "interval_end": "2025-01-02T00:00:00Z",    # excludes 2nd/3rd
        }
    )
    assert resp.status_code == 200
    assert [o["utc"] for o in resp.json()["occurrences"]] == ["2025-01-01T00:00:00Z"]


def test_aware_local_inputs_converted_to_zone_wall(api):
    resp = post(
        {
            "timezone": "Asia/Shanghai",
            "start_local": "2025-01-01T08:00:00+08:00",
            "rrule": "FREQ=DAILY;COUNT=1",
            "interval_start": "2024-12-31T16:00:00Z",
            "interval_end": "2025-01-02T16:00:00Z",
        }
    )
    assert resp.status_code == 200
    (o,) = resp.json()["occurrences"]
    assert o["local"] == "2025-01-01T08:00:00"
    assert o["utc"] == "2025-01-01T00:00:00Z"
    assert o["utc_offset"] == "+08:00"


# ------------------------------------------------------------ month-end ----

def test_last_day_of_month_rule_handles_february(api):
    resp = post(
        {
            "timezone": "UTC",
            "start_local": "2025-01-31T12:00:00",
            "rrule": "FREQ=MONTHLY;BYMONTHDAY=-1;COUNT=3",
            "interval_start": "2025-01-01T00:00:00Z",
            "interval_end": "2025-04-01T00:00:00Z",
        }
    )
    assert resp.status_code == 200
    locals_ = [o["local"] for o in resp.json()["occurrences"]]
    assert locals_ == [
        "2025-01-31T12:00:00",
        "2025-02-28T12:00:00",  # non-leap year
        "2025-03-31T12:00:00",
    ]


def test_second_to_last_day_with_offset_zone(api):
    resp = post(
        {
            "timezone": "Asia/Kolkata",  # +05:30, non-hour offset, no DST
            "start_local": "2025-02-27T23:30:00",
            "rrule": "FREQ=MONTHLY;BYMONTHDAY=-2;COUNT=2",
            "interval_start": "2025-02-01T00:00:00Z",
            "interval_end": "2025-05-01T00:00:00Z",
        }
    )
    assert resp.status_code == 200
    locals_ = [o["local"] for o in resp.json()["occurrences"]]
    assert locals_ == ["2025-02-27T23:30:00", "2025-03-30T23:30:00"]
    assert all(o["utc_offset"] == "+05:30" for o in resp.json()["occurrences"])


# ----------------------------------------------------------- DST gap/fold --

def test_nonexistent_local_time_is_skipped(api):
    # 02:30 does not exist on 2025-03-09 in America/New_York (jump 02:00 -> 03:00).
    resp = post(
        {
            "timezone": "America/New_York",
            "start_local": "2025-03-08T02:30:00",
            "rrule": "FREQ=DAILY;COUNT=3",
            "interval_start": "2025-03-01T00:00:00Z",
            "interval_end": "2025-03-20T00:00:00Z",
        }
    )
    assert resp.status_code == 200
    locals_ = [o["local"] for o in resp.json()["occurrences"]]
    assert locals_ == ["2025-03-08T02:30:00", "2025-03-10T02:30:00"]


def test_repeated_local_time_returns_both_folds(api):
    # On 2025-11-02 in New York, 01:30 exists at 05:30Z (EDT) and 06:30Z (EST).
    resp = post(
        {
            "timezone": "America/New_York",
            "start_local": "2025-11-02T01:30:00",
            "interval_start": "2025-11-02T00:00:00Z",
            "interval_end": "2025-11-03T00:00:00Z",
        }
    )
    assert resp.status_code == 200
    occ = resp.json()["occurrences"]
    assert len(occ) == 2
    assert [(o["utc"], o["utc_offset"], o["fold"], o["dst"]) for o in occ] == [
        ("2025-11-02T05:30:00Z", "-04:00", 0, True),
        ("2025-11-02T06:30:00Z", "-05:00", 1, False),
    ]
    # Both keep the same local wall time.
    assert {o["local"] for o in occ} == {"2025-11-02T01:30:00"}


def test_rdate_in_fold_returns_two_occurrences(api):
    resp = post(
        {
            "timezone": "America/New_York",
            "start_local": "2025-11-02T00:00:00",
            "rdate": ["2025-11-02T01:45:00"],
            "interval_start": "2025-11-02T00:00:00Z",
            "interval_end": "2025-11-03T00:00:00Z",
        }
    )
    assert resp.status_code == 200
    occ = resp.json()["occurrences"]
    rdates = [o for o in occ if o["source"] == "rdate"]
    assert len(rdates) == 2
    assert [o["fold"] for o in rdates] == [0, 1]


# ------------------------------------------------------------- dst_policy --

def test_gap_shift_forward_moves_wall_time_past_gap(api):
    # 02:30 does not exist on 2025-03-09 in New York (jump 02:00 -> 03:00);
    # shift_forward moves it past the one-hour gap to 03:30.
    resp = post(
        {
            "timezone": "America/New_York",
            "start_local": "2025-03-08T02:30:00",
            "rrule": "FREQ=DAILY;COUNT=3",
            "dst_policy": {"gap": "shift_forward"},
            "interval_start": "2025-03-01T00:00:00Z",
            "interval_end": "2025-03-20T00:00:00Z",
        }
    )
    assert resp.status_code == 200
    occ = resp.json()["occurrences"]
    assert [(o["local"], o["utc"], o["utc_offset"], o["dst"]) for o in occ] == [
        ("2025-03-08T02:30:00", "2025-03-08T07:30:00Z", "-05:00", False),
        ("2025-03-09T03:30:00", "2025-03-09T07:30:00Z", "-04:00", True),  # shifted
        ("2025-03-10T02:30:00", "2025-03-10T06:30:00Z", "-04:00", True),
    ]


def test_gap_shift_forward_with_sub_hour_gap(api):
    # Australia/Lord_Howe springs forward only 30 minutes (02:00 -> 02:30).
    resp = post(
        {
            "timezone": "Australia/Lord_Howe",
            "start_local": "2025-10-05T02:15:00",
            "dst_policy": {"gap": "shift_forward"},
            "interval_start": "2025-10-04T00:00:00Z",
            "interval_end": "2025-10-06T00:00:00Z",
        }
    )
    assert resp.status_code == 200
    (o,) = resp.json()["occurrences"]
    assert o["local"] == "2025-10-05T02:45:00"  # 02:15 + 30-minute gap
    assert o["utc"] == "2025-10-04T15:45:00Z"
    assert o["utc_offset"] == "+11:00"
    assert o["dst"] is True


def test_gap_default_still_skips_when_policy_present(api):
    resp = post(
        {
            "timezone": "Australia/Lord_Howe",
            "start_local": "2025-10-05T02:15:00",
            "dst_policy": {"fold": "earlier"},  # gap defaults to 'skip'
            "interval_start": "2025-10-04T00:00:00Z",
            "interval_end": "2025-10-06T00:00:00Z",
        }
    )
    assert resp.status_code == 200
    assert resp.json()["count"] == 0


def test_gap_shift_forward_applies_to_rdate(api):
    resp = post(
        {
            "timezone": "America/New_York",
            "start_local": "2025-03-07T12:00:00",  # outside the query window
            "rdate": ["2025-03-09T02:30:00"],
            "dst_policy": {"gap": "shift_forward"},
            "interval_start": "2025-03-08T00:00:00Z",
            "interval_end": "2025-03-10T00:00:00Z",
        }
    )
    assert resp.status_code == 200
    occ = resp.json()["occurrences"]
    assert [(o["local"], o["utc"], o["source"]) for o in occ] == [
        ("2025-03-09T03:30:00", "2025-03-09T07:30:00Z", "rdate"),
    ]


def test_fold_earlier_and_later_select_single_pass(api):
    base = {
        "timezone": "America/New_York",
        "start_local": "2025-11-02T01:30:00",
        "interval_start": "2025-11-02T00:00:00Z",
        "interval_end": "2025-11-03T00:00:00Z",
    }
    resp = post({**base, "dst_policy": {"fold": "earlier"}})
    assert resp.status_code == 200
    occ = resp.json()["occurrences"]
    assert [(o["utc"], o["utc_offset"], o["fold"], o["dst"]) for o in occ] == [
        ("2025-11-02T05:30:00Z", "-04:00", 0, True),
    ]

    resp = post({**base, "dst_policy": {"fold": "later"}})
    assert resp.status_code == 200
    occ = resp.json()["occurrences"]
    assert [(o["utc"], o["utc_offset"], o["fold"], o["dst"]) for o in occ] == [
        ("2025-11-02T06:30:00Z", "-05:00", 1, False),
    ]


def test_fold_policy_applies_to_rdate(api):
    resp = post(
        {
            "timezone": "America/New_York",
            "start_local": "2025-11-02T00:00:00",
            "rdate": ["2025-11-02T01:45:00"],
            "dst_policy": {"fold": "later"},
            "interval_start": "2025-11-02T00:00:00Z",
            "interval_end": "2025-11-03T00:00:00Z",
        }
    )
    assert resp.status_code == 200
    rdates = [o for o in resp.json()["occurrences"] if o["source"] == "rdate"]
    assert [(o["utc"], o["utc_offset"], o["fold"]) for o in rdates] == [
        ("2025-11-02T06:45:00Z", "-05:00", 1),
    ]


def test_fold_policy_keeps_unambiguous_wall_times(api):
    # A wall time that occurs only once is kept regardless of the fold side.
    resp = post(
        {
            "timezone": "America/New_York",
            "start_local": "2025-11-02T09:30:00",
            "dst_policy": {"fold": "later"},
            "interval_start": "2025-11-02T00:00:00Z",
            "interval_end": "2025-11-03T00:00:00Z",
        }
    )
    assert resp.status_code == 200
    assert resp.json()["count"] == 1


def test_exdate_matches_original_wall_time_even_when_shifted(api):
    base = {
        "timezone": "America/New_York",
        "start_local": "2025-03-09T02:30:00",
        "dst_policy": {"gap": "shift_forward"},
        "interval_start": "2025-03-01T00:00:00Z",
        "interval_end": "2025-03-20T00:00:00Z",
    }
    # Excluding the *original* wall time removes the shifted occurrence.
    resp = post({**base, "exdate": ["2025-03-09T02:30:00"]})
    assert resp.status_code == 200
    assert resp.json()["count"] == 0
    # Excluding the *shifted* wall time does not match anything.
    resp = post({**base, "exdate": ["2025-03-09T03:30:00"]})
    assert resp.status_code == 200
    assert resp.json()["count"] == 1


def test_shifted_rdate_colliding_with_rrule_keeps_rrule(api):
    # RDATE 02:30 shifts onto 03:30 EDT (07:30Z), where DTSTART already sits.
    resp = post(
        {
            "timezone": "America/New_York",
            "start_local": "2025-03-09T03:30:00",
            "rdate": ["2025-03-09T02:30:00"],
            "dst_policy": {"gap": "shift_forward"},
            "interval_start": "2025-03-09T00:00:00Z",
            "interval_end": "2025-03-10T00:00:00Z",
        }
    )
    assert resp.status_code == 200
    occ = resp.json()["occurrences"]
    assert [(o["local"], o["utc"], o["source"]) for o in occ] == [
        ("2025-03-09T03:30:00", "2025-03-09T07:30:00Z", "rrule"),
    ]


def test_shifted_rrule_instance_wins_over_rdate(api):
    # DTSTART 02:30 shifts onto 03:30 EDT (07:30Z), colliding with the RDATE.
    resp = post(
        {
            "timezone": "America/New_York",
            "start_local": "2025-03-09T02:30:00",
            "rdate": ["2025-03-09T03:30:00"],
            "dst_policy": {"gap": "shift_forward"},
            "interval_start": "2025-03-09T00:00:00Z",
            "interval_end": "2025-03-10T00:00:00Z",
        }
    )
    assert resp.status_code == 200
    occ = resp.json()["occurrences"]
    assert [(o["local"], o["utc"], o["source"]) for o in occ] == [
        ("2025-03-09T03:30:00", "2025-03-09T07:30:00Z", "rrule"),
    ]


def test_invalid_dst_policy_values_are_validation_error(api):
    base = {
        "timezone": "UTC",
        "start_local": "2025-01-01T00:00:00",
        "interval_start": "2025-01-01T00:00:00Z",
        "interval_end": "2025-02-01T00:00:00Z",
    }
    for policy in (
        {"gap": "drop"},
        {"fold": "first"},
        {"gap": "skip", "fold": "yesterday"},
        "shift_forward",
        42,
    ):
        resp = post({**base, "dst_policy": policy})
        assert resp.status_code == 422, policy
        assert resp.json()["error"]["code"] == "validation_error"


# --------------------------------------------------------- EXDATE / RDATE --

def test_exdate_removes_and_rdate_adds(api):
    resp = post(
        {
            "timezone": "UTC",
            "start_local": "2025-01-01T09:00:00",
            "rrule": "FREQ=DAILY;COUNT=3",
            "exdate": ["2025-01-02T09:00:00"],
            "rdate": [
                "2025-01-03T09:00:00",  # duplicates an rrule instance
                "2025-01-04T12:00:00",
            ],
            "interval_start": "2025-01-01T00:00:00Z",
            "interval_end": "2025-01-10T00:00:00Z",
        }
    )
    assert resp.status_code == 200
    occ = resp.json()["occurrences"]
    assert [(o["utc"], o["source"]) for o in occ] == [
        ("2025-01-01T09:00:00Z", "rrule"),
        ("2025-01-03T09:00:00Z", "rrule"),  # rrule wins over duplicate rdate
        ("2025-01-04T12:00:00Z", "rdate"),
    ]


def test_exdate_also_removes_matching_rdate(api):
    resp = post(
        {
            "timezone": "UTC",
            "start_local": "2024-12-01T00:00:00",  # outside the query window
            "rdate": ["2025-02-01T08:00:00"],
            "exdate": ["2025-02-01T08:00:00"],
            "interval_start": "2025-01-01T00:00:00Z",
            "interval_end": "2025-03-01T00:00:00Z",
        }
    )
    assert resp.status_code == 200
    assert resp.json()["count"] == 0


def test_no_rrule_still_returns_dtstart(api):
    resp = post(
        {
            "timezone": "Europe/Berlin",
            "start_local": "2025-06-01T12:00:00",
            "rdate": ["2025-06-02T12:00:00"],
            "interval_start": "2025-06-01T00:00:00Z",
            "interval_end": "2025-06-03T00:00:00Z",
        }
    )
    assert resp.status_code == 200
    assert [o["local"] for o in resp.json()["occurrences"]] == [
        "2025-06-01T12:00:00",
        "2025-06-02T12:00:00",
    ]


def test_rrule_prefix_and_until_accepted(api):
    resp = post(
        {
            "timezone": "UTC",
            "start_local": "2025-01-01T00:00:00",
            "rrule": "RRULE:FREQ=DAILY;UNTIL=20250103T000000Z",
            "interval_start": "2025-01-01T00:00:00Z",
            "interval_end": "2025-02-01T00:00:00Z",
        }
    )
    assert resp.status_code == 200
    assert [o["utc"] for o in resp.json()["occurrences"]] == [
        "2025-01-01T00:00:00Z",
        "2025-01-02T00:00:00Z",
        "2025-01-03T00:00:00Z",
    ]


# --------------------------------------------------------------- errors ---

def test_invalid_timezone_returns_structured_error(api):
    resp = post(
        {
            "timezone": "Not/AZone",
            "start_local": "2025-01-01T00:00:00",
            "interval_start": "2025-01-01T00:00:00Z",
            "interval_end": "2025-02-01T00:00:00Z",
        }
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"]["code"] == "invalid_timezone"
    assert "message" in body["error"]


def test_missing_freq_is_invalid_rrule(api):
    resp = post(
        {
            "timezone": "UTC",
            "start_local": "2025-01-01T00:00:00",
            "rrule": "INTERVAL=1",
            "interval_start": "2025-01-01T00:00:00Z",
            "interval_end": "2025-02-01T00:00:00Z",
        }
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_rrule"


def test_garbage_rrule_is_invalid(api):
    resp = post(
        {
            "timezone": "UTC",
            "start_local": "2025-01-01T00:00:00",
            "rrule": "FREQ=WAT",
            "interval_start": "2025-01-01T00:00:00Z",
            "interval_end": "2025-02-01T00:00:00Z",
        }
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_rrule"


def test_zero_interval_in_rule_is_invalid(api):
    resp = post(
        {
            "timezone": "UTC",
            "start_local": "2025-01-01T00:00:00",
            "rrule": "FREQ=DAILY;INTERVAL=0",
            "interval_start": "2025-01-01T00:00:00Z",
            "interval_end": "2025-02-01T00:00:00Z",
        }
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_rrule"


def test_count_and_until_together_is_invalid(api):
    resp = post(
        {
            "timezone": "UTC",
            "start_local": "2025-01-01T00:00:00",
            "rrule": "FREQ=DAILY;COUNT=3;UNTIL=20250105T000000Z",
            "interval_start": "2025-01-01T00:00:00Z",
            "interval_end": "2025-02-01T00:00:00Z",
        }
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_rrule"


def test_malformed_local_datetime_is_invalid(api):
    resp = post(
        {
            "timezone": "UTC",
            "start_local": "not-a-date",
            "interval_start": "2025-01-01T00:00:00Z",
            "interval_end": "2025-02-01T00:00:00Z",
        }
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_datetime"


def test_microsecond_precision_preserved(api):
    resp = post(
        {
            "timezone": "UTC",
            "start_local": "2025-01-01T00:00:00.250000",
            "rrule": "FREQ=DAILY;COUNT=2",
            "interval_start": "2024-12-31T00:00:00Z",
            "interval_end": "2025-01-03T00:00:00Z",
        }
    )
    assert resp.status_code == 200
    assert [o["local"] for o in resp.json()["occurrences"]] == [
        "2025-01-01T00:00:00.250000",
        "2025-01-02T00:00:00.250000",
    ]


def test_reversed_interval_rejected(api):
    resp = post(
        {
            "timezone": "UTC",
            "start_local": "2025-01-01T00:00:00",
            "rrule": "FREQ=DAILY",
            "interval_start": "2025-02-01T00:00:00Z",
            "interval_end": "2025-01-01T00:00:00Z",
        }
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_interval"


def test_interval_without_offset_rejected(api):
    resp = post(
        {
            "timezone": "UTC",
            "start_local": "2025-01-01T00:00:00",
            "interval_start": "2025-01-01T00:00:00",
            "interval_end": "2025-02-01T00:00:00",
        }
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_datetime"


def test_missing_body_field_is_validation_error(api):
    resp = api.post("/occurrences", json={"timezone": "UTC"})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"
    assert resp.json()["error"]["details"]


def test_malformed_json_is_422(api):
    resp = api.post("/occurrences", content="{not json", headers={"content-type": "application/json"})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


def test_too_many_occurrences_rejected(api):
    resp = post(
        {
            "timezone": "UTC",
            "start_local": "2025-01-01T00:00:00",
            "rrule": "FREQ=MINUTELY",
            "interval_start": "2025-01-01T00:00:00Z",
            "interval_end": "2026-01-01T00:00:00Z",
        }
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "too_many_occurrences"


def test_dense_rule_starting_far_before_window_rejected_quickly(api):
    # 50,000 scan budget exhausted almost immediately; the request must not
    # walk millions of seconds from the year 2000 DTSTART.
    resp = post(
        {
            "timezone": "UTC",
            "start_local": "2000-01-01T00:00:00",
            "rrule": "FREQ=SECONDLY",
            "interval_start": "2025-01-01T00:00:00Z",
            "interval_end": "2025-01-02T00:00:00Z",
        }
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "too_many_occurrences"


# --------------------------------------------- POST /business-occurrences ----


_BUSINESS_BASE = {
    "timezone": "UTC",
    "start_local": "2025-01-02T09:00:00",  # Thursday
    "rrule": "FREQ=DAILY;COUNT=4",         # Thu 2, Fri 3, Sat 4, Sun 5
    "interval_start": "2025-01-01T00:00:00Z",
    "interval_end": "2025-01-10T00:00:00Z",
}


def test_business_following_merges_weekend_onto_next_monday(api):
    resp = post_business(dict(_BUSINESS_BASE))
    assert resp.status_code == 200
    occ = resp.json()["occurrences"]
    assert [(o["local"], o["utc"], o["originals"]) for o in occ] == [
        ("2025-01-02T09:00:00", "2025-01-02T09:00:00Z", ["2025-01-02T09:00:00"]),
        ("2025-01-03T09:00:00", "2025-01-03T09:00:00Z", ["2025-01-03T09:00:00"]),
        (
            "2025-01-06T09:00:00",
            "2025-01-06T09:00:00Z",
            ["2025-01-04T09:00:00", "2025-01-05T09:00:00"],
        ),
    ]
    assert all(set(o) == {"local", "utc", "utc_offset", "dst", "fold", "originals"} for o in occ)
    assert all(o["local"].endswith("T09:00:00") for o in occ)


def test_business_preceding_merges_weekend_back_to_friday(api):
    resp = post_business({**_BUSINESS_BASE, "business_day_convention": "preceding"})
    assert resp.status_code == 200
    occ = resp.json()["occurrences"]
    assert [(o["local"], o["originals"]) for o in occ] == [
        ("2025-01-02T09:00:00", ["2025-01-02T09:00:00"]),
        (
            "2025-01-03T09:00:00",
            ["2025-01-03T09:00:00", "2025-01-04T09:00:00", "2025-01-05T09:00:00"],
        ),
    ]


def test_business_modified_following_goes_backward_on_month_cross(api):
    # Fri May 30, Sat May 31, Sun Jun 1: the weekend raw instances would move
    # forward into June, so modified_following moves them backward instead.
    resp = post_business(
        {
            "timezone": "UTC",
            "start_local": "2025-05-30T09:00:00",
            "rrule": "FREQ=DAILY;COUNT=3",
            "business_day_convention": "modified_following",
            "interval_start": "2025-05-29T00:00:00Z",
            "interval_end": "2025-06-03T00:00:00Z",
        }
    )
    assert resp.status_code == 200
    occ = resp.json()["occurrences"]
    assert [(o["local"], o["originals"]) for o in occ] == [
        (
            "2025-05-30T09:00:00",
            ["2025-05-30T09:00:00", "2025-05-31T09:00:00"],
        ),
        # Sunday Jun 1 rolls forward to Mon Jun 2: no month crossing for that raw date.
        ("2025-06-02T09:00:00", ["2025-06-01T09:00:00"]),
    ]


def test_business_modified_following_stays_forward_within_month(api):
    # Fri Feb 14 + weekend + Mon Feb 17 holiday: all three non-business days
    # move forward to Tue Feb 18 (same month as each raw date).
    resp = post_business(
        {
            "timezone": "UTC",
            "start_local": "2025-02-14T09:00:00",
            "rrule": "FREQ=DAILY;COUNT=4",
            "business_day_convention": "modified_following",
            "holidays": ["2025-02-17"],
            "interval_start": "2025-02-13T00:00:00Z",
            "interval_end": "2025-02-20T00:00:00Z",
        }
    )
    assert resp.status_code == 200
    occ = resp.json()["occurrences"]
    assert [(o["local"], o["originals"]) for o in occ] == [
        ("2025-02-14T09:00:00", ["2025-02-14T09:00:00"]),
        (
            "2025-02-18T09:00:00",
            ["2025-02-15T09:00:00", "2025-02-16T09:00:00", "2025-02-17T09:00:00"],
        ),
    ]


def test_business_holidays_are_skipped_like_weekends(api):
    resp = post_business(
        {
            "timezone": "UTC",
            "start_local": "2025-01-02T09:00:00",  # Thursday
            "rrule": "FREQ=DAILY;COUNT=2",         # Thu 2, Fri 3 (holiday)
            "holidays": ["2025-01-03"],
            "interval_start": "2025-01-01T00:00:00Z",
            "interval_end": "2025-01-10T00:00:00Z",
        }
    )
    assert resp.status_code == 200
    occ = resp.json()["occurrences"]
    assert [(o["local"], o["originals"]) for o in occ] == [
        ("2025-01-02T09:00:00", ["2025-01-02T09:00:00"]),
        # Friday holiday + the weekend all land on Monday Jan 6.
        ("2025-01-06T09:00:00", ["2025-01-03T09:00:00"]),
    ]


def test_business_custom_weekend_days(api):
    # Friday/Saturday weekend (e.g. parts of the Middle East): Thursday is a
    # business day, Fri Jan 3 and Sat Jan 4 both move to Sunday Jan 5.
    resp = post_business(
        {
            "timezone": "UTC",
            "start_local": "2025-01-02T09:00:00",
            "rrule": "FREQ=DAILY;COUNT=3",  # Thu 2, Fri 3, Sat 4
            "weekend_days": [5, 6],
            "interval_start": "2025-01-01T00:00:00Z",
            "interval_end": "2025-01-10T00:00:00Z",
        }
    )
    assert resp.status_code == 200
    occ = resp.json()["occurrences"]
    assert [(o["local"], o["originals"]) for o in occ] == [
        ("2025-01-02T09:00:00", ["2025-01-02T09:00:00"]),
        (
            "2025-01-05T09:00:00",
            ["2025-01-03T09:00:00", "2025-01-04T09:00:00"],
        ),
    ]


def test_business_holiday_date_time_uses_zone_wall_date(api):
    resp = post_business(
        {
            "timezone": "Asia/Shanghai",
            "start_local": "2025-01-01T09:00:00",  # holiday
            "holidays": ["2025-01-01T09:00:00+08:00"],
            "interval_start": "2025-01-01T00:00:00Z",
            "interval_end": "2025-01-03T00:00:00Z",
        }
    )
    assert resp.status_code == 200
    assert [(o["local"], o["originals"]) for o in resp.json()["occurrences"]] == [
        ("2025-01-02T09:00:00", ["2025-01-01T09:00:00"]),
    ]


def test_business_exdate_matches_unadjusted_wall_time(api):
    # Excluding the raw Saturday instance removes it from the Monday merge;
    # excluding the adjusted Monday wall time does not.
    resp = post_business({**_BUSINESS_BASE, "exdate": ["2025-01-04T09:00:00"]})
    assert resp.status_code == 200
    monday = [o for o in resp.json()["occurrences"] if o["local"] == "2025-01-06T09:00:00"]
    assert monday[0]["originals"] == ["2025-01-05T09:00:00"]

    resp = post_business({**_BUSINESS_BASE, "exdate": ["2025-01-06T09:00:00"]})
    assert resp.status_code == 200
    locals_ = [o["local"] for o in resp.json()["occurrences"]]
    assert "2025-01-06T09:00:00" in locals_


def test_business_rdate_is_adjusted_and_merges_with_rrule(api):
    # RDATE Friday Jan 3 09:00 duplicates an rrule instance after adjustment
    # trivially; here an RDATE on Saturday merges into the Monday instant.
    resp = post_business(
        {
            "timezone": "UTC",
            "start_local": "2025-01-02T09:00:00",
            "rrule": "FREQ=DAILY;COUNT=2",  # Thu 2, Fri 3
            "rdate": ["2025-01-04T09:00:00", "2025-01-05T09:00:00"],
            "interval_start": "2025-01-01T00:00:00Z",
            "interval_end": "2025-01-10T00:00:00Z",
        }
    )
    assert resp.status_code == 200
    occ = resp.json()["occurrences"]
    assert [(o["local"], o["originals"]) for o in occ] == [
        ("2025-01-02T09:00:00", ["2025-01-02T09:00:00"]),
        ("2025-01-03T09:00:00", ["2025-01-03T09:00:00"]),
        (
            "2025-01-06T09:00:00",
            ["2025-01-04T09:00:00", "2025-01-05T09:00:00"],
        ),
    ]


def test_business_no_rrule_still_adjusts_dtstart(api):
    resp = post_business(
        {
            "timezone": "UTC",
            "start_local": "2025-01-04T09:00:00",  # Saturday
            "interval_start": "2025-01-01T00:00:00Z",
            "interval_end": "2025-01-10T00:00:00Z",
        }
    )
    assert resp.status_code == 200
    assert [(o["local"], o["originals"]) for o in resp.json()["occurrences"]] == [
        ("2025-01-06T09:00:00", ["2025-01-04T09:00:00"]),
    ]


def test_business_preserves_subsecond_time_of_day(api):
    resp = post_business(
        {
            "timezone": "UTC",
            "start_local": "2025-01-04T09:00:00.250000",  # Saturday
            "interval_start": "2025-01-01T00:00:00Z",
            "interval_end": "2025-01-10T00:00:00Z",
        }
    )
    assert resp.status_code == 200
    (o,) = resp.json()["occurrences"]
    assert o["local"] == "2025-01-06T09:00:00.250000"
    assert o["utc"] == "2025-01-06T09:00:00.250000Z"


def test_business_dst_resolution_runs_after_adjustment(api):
    # New York: Saturday Mar 8 moves (weekend_days=[6]) onto Sunday Mar 9;
    # 02:30 does not exist there. Default gap policy skips it...
    resp = post_business(
        {
            "timezone": "America/New_York",
            "start_local": "2025-03-08T02:30:00",
            "weekend_days": [6],
            "interval_start": "2025-03-07T00:00:00Z",
            "interval_end": "2025-03-11T00:00:00Z",
        }
    )
    assert resp.status_code == 200
    assert resp.json()["count"] == 0

    # ...and shift_forward pushes the adjusted wall time past the gap.
    resp = post_business(
        {
            "timezone": "America/New_York",
            "start_local": "2025-03-08T02:30:00",
            "weekend_days": [6],
            "dst_policy": {"gap": "shift_forward"},
            "interval_start": "2025-03-07T00:00:00Z",
            "interval_end": "2025-03-11T00:00:00Z",
        }
    )
    assert resp.status_code == 200
    assert [(o["local"], o["utc"], o["originals"]) for o in resp.json()["occurrences"]] == [
        ("2025-03-09T03:30:00", "2025-03-09T07:30:00Z", ["2025-03-08T02:30:00"]),
    ]


def test_business_fold_returns_both_passes_after_adjustment(api):
    # Saturday Nov 1 01:30 moves (weekend_days=[6]) to Sunday Nov 2, where
    # 01:30 is the repeated fall-back wall time: both folds are returned.
    resp = post_business(
        {
            "timezone": "America/New_York",
            "start_local": "2025-11-01T01:30:00",
            "weekend_days": [6],
            "interval_start": "2025-11-01T00:00:00Z",
            "interval_end": "2025-11-03T00:00:00Z",
        }
    )
    assert resp.status_code == 200
    occ = resp.json()["occurrences"]
    assert [(o["utc"], o["fold"], o["originals"]) for o in occ] == [
        ("2025-11-02T05:30:00Z", 0, ["2025-11-01T01:30:00"]),
        ("2025-11-02T06:30:00Z", 1, ["2025-11-01T01:30:00"]),
    ]


def test_business_long_holiday_bridge_near_window_edge(api):
    # Fri Jan 3 is a holiday, followed by the weekend and Mon-Fri holidays
    # (Jan 3..10, then Jan 11/12 weekend): the raw instance moves all the way
    # to Mon Jan 13 - ten days away, beyond the fixed 2-day window margin,
    # which the adaptive calendar margin must cover.
    holidays = [f"2025-01-{d:02d}" for d in range(3, 11)]

    resp = post_business(
        {
            "timezone": "UTC",
            "start_local": "2025-01-03T09:00:00",
            "holidays": holidays,
            "interval_start": "2025-01-01T00:00:00Z",
            "interval_end": "2025-01-04T00:00:00Z",
        }
    )
    assert resp.status_code == 200
    # The adjusted instant (Jan 13) falls outside the UTC query window.
    assert resp.json()["count"] == 0

    resp = post_business(
        {
            "timezone": "UTC",
            "start_local": "2025-01-03T09:00:00",
            "holidays": holidays,
            "interval_start": "2025-01-01T00:00:00Z",
            "interval_end": "2025-01-14T00:00:00Z",
        }
    )
    assert resp.status_code == 200
    assert [(o["local"], o["originals"]) for o in resp.json()["occurrences"]] == [
        ("2025-01-13T09:00:00", ["2025-01-03T09:00:00"]),
    ]


def test_business_invalid_weekend_days_rejected(api):
    base = {
        "timezone": "UTC",
        "start_local": "2025-01-01T00:00:00",
        "interval_start": "2025-01-01T00:00:00Z",
        "interval_end": "2025-02-01T00:00:00Z",
    }
    for bad in ([], [0], [8], [1, 2, 3, 4, 5, 6, 7]):
        resp = post_business({**base, "weekend_days": bad})
        assert resp.status_code in (400, 422), bad
        assert resp.json()["error"]["code"] in (
            "invalid_business_calendar",
            "validation_error",
        )


def test_business_unknown_convention_is_validation_error(api):
    resp = post_business(
        {
            "timezone": "UTC",
            "start_local": "2025-01-01T00:00:00",
            "business_day_convention": "later",
            "interval_start": "2025-01-01T00:00:00Z",
            "interval_end": "2025-02-01T00:00:00Z",
        }
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"
