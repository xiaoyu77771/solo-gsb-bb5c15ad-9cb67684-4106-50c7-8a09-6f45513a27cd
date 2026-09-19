"""HTTP-level tests for POST /business-occurrences."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def post(body: dict):
    return client.post("/business-occurrences", json=body)


# ------------------------------------------------------------- conventions ----

def test_following_default_moves_weekend_and_merges_originals():
    # Thu 2025-01-02 .. Sun 2025-01-05, 09:00 UTC every day.
    resp = post(
        {
            "timezone": "UTC",
            "start_local": "2025-01-02T09:00:00",
            "rrule": "FREQ=DAILY;COUNT=4",
            "interval_start": "2025-01-01T00:00:00Z",
            "interval_end": "2025-01-10T00:00:00Z",
        }
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["business_day_convention"] == "following"
    occ = body["occurrences"]
    assert [(o["local"], o["utc"], o["source"]) for o in occ] == [
        ("2025-01-02T09:00:00", "2025-01-02T09:00:00Z", "rrule"),
        ("2025-01-03T09:00:00", "2025-01-03T09:00:00Z", "rrule"),
        ("2025-01-06T09:00:00", "2025-01-06T09:00:00Z", "rrule"),
    ]
    assert occ[0]["originals"] == ["2025-01-02T09:00:00"]
    assert occ[1]["originals"] == ["2025-01-03T09:00:00"]
    # Saturday and Sunday both move to Monday and merge.
    assert occ[2]["originals"] == [
        "2025-01-04T09:00:00",
        "2025-01-05T09:00:00",
    ]
    assert body["count"] == 3


def test_preceding_moves_backward_and_merges():
    resp = post(
        {
            "timezone": "UTC",
            "start_local": "2025-01-02T09:00:00",
            "rrule": "FREQ=DAILY;COUNT=4",
            "business_day_convention": "preceding",
            "interval_start": "2025-01-01T00:00:00Z",
            "interval_end": "2025-01-10T00:00:00Z",
        }
    )
    assert resp.status_code == 200
    occ = resp.json()["occurrences"]
    assert [(o["local"], o["originals"]) for o in occ] == [
        ("2025-01-02T09:00:00", ["2025-01-02T09:00:00"]),
        (
            "2025-01-03T09:00:00",
            ["2025-01-03T09:00:00", "2025-01-04T09:00:00", "2025-01-05T09:00:00"],
        ),
    ]


def test_modified_following_goes_backward_only_when_crossing_month():
    # 2025-05-31 is Saturday -> following Monday 06-02 crosses months -> back to Fri 05-30.
    # 2025-06-01 is Sunday  -> Monday 06-02 stays in June -> forward.
    # 2025-06-07 is Saturday -> Monday 06-09 stays in June -> forward.
    resp = post(
        {
            "timezone": "UTC",
            "start_local": "2024-12-01T12:00:00",  # outside the query window
            "rdate": [
                "2025-05-31T09:00:00",
                "2025-06-01T09:00:00",
                "2025-06-07T09:00:00",
            ],
            "business_day_convention": "modified_following",
            "interval_start": "2025-05-29T00:00:00Z",
            "interval_end": "2025-06-11T00:00:00Z",
        }
    )
    assert resp.status_code == 200
    assert [o["local"] for o in resp.json()["occurrences"]] == [
        "2025-05-30T09:00:00",
        "2025-06-02T09:00:00",
        "2025-06-09T09:00:00",
    ]


def test_time_of_day_is_preserved_through_adjustment():
    resp = post(
        {
            "timezone": "UTC",
            "start_local": "2025-01-02T09:00:00",
            "rrule": "FREQ=DAILY;COUNT=4",
            "interval_start": "2025-01-01T00:00:00Z",
            "interval_end": "2025-01-10T00:00:00Z",
        }
    )
    assert resp.status_code == 200
    assert all(o["local"].endswith("T09:00:00") for o in resp.json()["occurrences"])


# ----------------------------------------------------------------- holidays ----

def test_holidays_extend_the_walk():
    # Saturday 2025-01-04, Monday 2025-01-06 is a holiday -> Tuesday 2025-01-07.
    resp = post(
        {
            "timezone": "UTC",
            "start_local": "2024-12-01T00:00:00",
            "rdate": ["2025-01-04T09:00:00"],
            "holidays": ["2025-01-06"],
            "interval_start": "2025-01-03T00:00:00Z",
            "interval_end": "2025-01-10T00:00:00Z",
        }
    )
    assert resp.status_code == 200
    occ = resp.json()["occurrences"]
    assert [(o["local"], o["originals"]) for o in occ] == [
        ("2025-01-07T09:00:00", ["2025-01-04T09:00:00"]),
    ]


def test_weekend_abbreviations_and_custom_weekend():
    base = {
        "timezone": "UTC",
        "start_local": "2025-01-02T09:00:00",
        "rrule": "FREQ=DAILY;COUNT=4",
        "interval_start": "2025-01-01T00:00:00Z",
        "interval_end": "2025-01-10T00:00:00Z",
    }
    # iCal abbreviations behave like the numeric default.
    resp = post({**base, "weekend_days": ["SA", "SU"]})
    assert resp.status_code == 200
    assert [o["local"] for o in resp.json()["occurrences"]] == [
        "2025-01-02T09:00:00",
        "2025-01-03T09:00:00",
        "2025-01-06T09:00:00",
    ]
    # Only Sunday is a weekend: Saturday 01-04 stays put, Sunday 01-05 moves.
    resp = post({**base, "weekend_days": [6]})
    assert resp.status_code == 200
    assert [o["local"] for o in resp.json()["occurrences"]] == [
        "2025-01-02T09:00:00",
        "2025-01-03T09:00:00",
        "2025-01-04T09:00:00",
        "2025-01-06T09:00:00",
    ]


def test_empty_weekend_keeps_all_weekdays_business():
    resp = post(
        {
            "timezone": "UTC",
            "start_local": "2025-01-04T09:00:00",  # Saturday
            "weekend_days": [],
            "interval_start": "2025-01-01T00:00:00Z",
            "interval_end": "2025-01-10T00:00:00Z",
        }
    )
    assert resp.status_code == 200
    assert [o["local"] for o in resp.json()["occurrences"]] == ["2025-01-04T09:00:00"]


# ------------------------------------------------------------- merge/source ----

def test_rrule_wins_over_rdate_when_merged_but_originals_list_all():
    # Raw Sat/Sun move to Monday 01-06; the RDATE sits on Monday already.
    resp = post(
        {
            "timezone": "UTC",
            "start_local": "2025-01-02T09:00:00",
            "rrule": "FREQ=DAILY;COUNT=4",
            "rdate": ["2025-01-06T09:00:00"],
            "interval_start": "2025-01-01T00:00:00Z",
            "interval_end": "2025-01-10T00:00:00Z",
        }
    )
    assert resp.status_code == 200
    occ = {o["local"]: o for o in resp.json()["occurrences"]}
    monday = occ["2025-01-06T09:00:00"]
    assert monday["source"] == "rrule"
    assert monday["originals"] == [
        "2025-01-04T09:00:00",
        "2025-01-05T09:00:00",
        "2025-01-06T09:00:00",
    ]


def test_distinct_times_adjusted_to_same_day_remain_separate_instants():
    resp = post(
        {
            "timezone": "UTC",
            "start_local": "2024-12-01T00:00:00",
            "rdate": ["2025-01-04T09:00:00", "2025-01-04T10:00:00"],
            "interval_start": "2025-01-03T00:00:00Z",
            "interval_end": "2025-01-10T00:00:00Z",
        }
    )
    assert resp.status_code == 200
    assert [o["utc"] for o in resp.json()["occurrences"]] == [
        "2025-01-06T09:00:00Z",
        "2025-01-06T10:00:00Z",
    ]


# ------------------------------------------------------------------- EXDATE ----

def test_exdate_matches_pre_adjustment_wall_time():
    base = {
        "timezone": "UTC",
        "start_local": "2025-01-02T09:00:00",
        "rrule": "FREQ=DAILY;COUNT=4",
        "interval_start": "2025-01-01T00:00:00Z",
        "interval_end": "2025-01-10T00:00:00Z",
    }
    # Excluding the raw Saturday instance removes one of Monday's originals.
    resp = post({**base, "exdate": ["2025-01-04T09:00:00"]})
    assert resp.status_code == 200
    occ = resp.json()["occurrences"]
    assert [(o["local"], o["originals"]) for o in occ] == [
        ("2025-01-02T09:00:00", ["2025-01-02T09:00:00"]),
        ("2025-01-03T09:00:00", ["2025-01-03T09:00:00"]),
        ("2025-01-06T09:00:00", ["2025-01-05T09:00:00"]),
    ]
    # Excluding the *adjusted* wall time (Monday) matches nothing.
    resp = post({**base, "exdate": ["2025-01-06T09:00:00"]})
    assert resp.status_code == 200
    assert resp.json()["count"] == 3


# ---------------------------------------------------- query window interaction ----

def test_raw_instance_outside_window_that_moves_in_is_returned():
    # Raw Sunday 2025-01-05 09:00 moves forward to Monday; window starts Monday.
    resp = post(
        {
            "timezone": "UTC",
            "start_local": "2025-01-05T09:00:00",
            "interval_start": "2025-01-06T00:00:00Z",
            "interval_end": "2025-01-10T00:00:00Z",
        }
    )
    assert resp.status_code == 200
    assert [o["utc"] for o in resp.json()["occurrences"]] == ["2025-01-06T09:00:00Z"]


def test_preceding_moving_instance_out_of_window_is_filtered():
    # Sunday 2025-01-05 moves back to Friday 2025-01-03, before the window.
    resp = post(
        {
            "timezone": "UTC",
            "start_local": "2025-01-05T09:00:00",
            "business_day_convention": "preceding",
            "interval_start": "2025-01-05T00:00:00Z",
            "interval_end": "2025-01-10T00:00:00Z",
        }
    )
    assert resp.status_code == 200
    assert resp.json()["count"] == 0


# ------------------------------------------------------------ DST interaction ----

def test_adjusted_instance_landing_in_gap_is_skipped_by_default():
    # Saturday 2025-03-08 02:30 NY; with Sunday the only business day it moves to
    # Sun 03-09 02:30, which is the spring-forward gap -> skipped by default.
    resp = post(
        {
            "timezone": "America/New_York",
            "start_local": "2024-12-01T00:00:00",
            "rdate": ["2025-03-08T02:30:00"],
            "weekend_days": ["MO", "TU", "WE", "TH", "FR", "SA"],
            "interval_start": "2025-03-05T00:00:00Z",
            "interval_end": "2025-03-12T00:00:00Z",
        }
    )
    assert resp.status_code == 200
    assert resp.json()["count"] == 0


def test_adjusted_instance_landing_in_gap_honors_shift_forward():
    resp = post(
        {
            "timezone": "America/New_York",
            "start_local": "2024-12-01T00:00:00",
            "rdate": ["2025-03-08T02:30:00"],
            "weekend_days": ["MO", "TU", "WE", "TH", "FR", "SA"],
            "dst_policy": {"gap": "shift_forward"},
            "interval_start": "2025-03-05T00:00:00Z",
            "interval_end": "2025-03-12T00:00:00Z",
        }
    )
    assert resp.status_code == 200
    (o,) = resp.json()["occurrences"]
    assert o["local"] == "2025-03-09T03:30:00"
    assert o["utc"] == "2025-03-09T07:30:00Z"
    assert o["utc_offset"] == "-04:00"
    assert o["originals"] == ["2025-03-08T02:30:00"]


def test_adjusted_fold_returns_both_passes_with_originals():
    # With no weekend weekdays, Sunday 2025-11-02 01:30 NY is a business day and
    # the repeated wall time yields two fold passes.
    resp = post(
        {
            "timezone": "America/New_York",
            "start_local": "2025-11-02T01:30:00",
            "weekend_days": [],
            "interval_start": "2025-11-02T00:00:00Z",
            "interval_end": "2025-11-03T00:00:00Z",
        }
    )
    assert resp.status_code == 200
    occ = resp.json()["occurrences"]
    assert [(o["utc"], o["fold"]) for o in occ] == [
        ("2025-11-02T05:30:00Z", 0),
        ("2025-11-02T06:30:00Z", 1),
    ]
    assert all(o["originals"] == ["2025-11-02T01:30:00"] for o in occ)


# ----------------------------------------------------------------- validation ----

def test_all_weekdays_as_weekend_is_rejected():
    resp = post(
        {
            "timezone": "UTC",
            "start_local": "2025-01-01T00:00:00",
            "weekend_days": [0, 1, 2, 3, 4, 5, 6],
            "interval_start": "2025-01-01T00:00:00Z",
            "interval_end": "2025-02-01T00:00:00Z",
        }
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


def test_bad_weekend_entry_is_rejected():
    base = {
        "timezone": "UTC",
        "start_local": "2025-01-01T00:00:00",
        "interval_start": "2025-01-01T00:00:00Z",
        "interval_end": "2025-02-01T00:00:00Z",
    }
    for value in (["XX"], [7], [-1]):
        resp = post({**base, "weekend_days": value})
        assert resp.status_code == 422, value
        assert resp.json()["error"]["code"] == "validation_error"


def test_bad_convention_is_rejected():
    resp = post(
        {
            "timezone": "UTC",
            "start_local": "2025-01-01T00:00:00",
            "business_day_convention": "modified_preceding",
            "interval_start": "2025-01-01T00:00:00Z",
            "interval_end": "2025-02-01T00:00:00Z",
        }
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


def test_bad_holiday_datetime_is_rejected():
    resp = post(
        {
            "timezone": "UTC",
            "start_local": "2025-01-01T00:00:00",
            "holidays": ["not-a-date"],
            "interval_start": "2025-01-01T00:00:00Z",
            "interval_end": "2025-02-01T00:00:00Z",
        }
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_datetime"
