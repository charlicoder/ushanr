"""Unit tests for the pure workforce day computation."""
from datetime import date, datetime, time

from app.services.workforce.day_close import (
    KUWAIT_TZ,
    RosterInput,
    SessionInput,
    compute_day,
    intersect,
    merge,
    subtract,
)

D = date(2026, 11, 5)


def sess(sid, h1, m1, h2, m2, services=None, **kw):
    return SessionInput(
        session_id=sid,
        booking_id=f"b-{sid}",
        therapist_id="t1",
        start=datetime(2026, 11, 5, h1, m1, tzinfo=KUWAIT_TZ),
        end=datetime(2026, 11, 5, h2, m2, tzinfo=KUWAIT_TZ),
        services=services or [],
        **kw,
    )


def roster(**kw):
    base = dict(therapist_id="t1", local_date=D, shift_start=time(9), shift_end=time(17))
    base.update(kw)
    return RosterInput(**base)


def test_interval_algebra():
    assert merge([(5, 10), (8, 12), (20, 30)]) == [(5, 12), (20, 30)]
    assert subtract([(0, 100)], [(10, 20), (50, 60)]) == [(0, 10), (20, 50), (60, 100)]
    assert intersect([(0, 50)], [(40, 80)]) == [(40, 50)]


def test_basic_worked_idle_with_break():
    r = roster(break_start=time(13), break_end=time(14))
    res = compute_day(r, [sess("s1", 10, 0, 11, 0), sess("s2", 15, 0, 16, 30)])
    assert res.scheduled_minutes == 480
    assert res.break_minutes == 60
    assert res.available_minutes == 420
    assert res.worked_minutes == 150
    assert res.idle_minutes == 270
    assert res.overtime_minutes == 0
    assert res.sessions_completed == 2
    assert float(res.utilization_pct) == 35.71
    types = [s.segment_type for s in res.segments]
    assert types.count("service") == 2 and "break" in types and "idle" in types
    # timeline fully covers the shift exactly once (service + idle + break)
    assert sum(s.minutes for s in res.segments) == 480


def test_leave_half_day_and_full_day():
    half = roster(leaves=[{"leave_type": "half_day", "status": "approved", "start_time": "13:00", "end_time": "17:00"}])
    res = compute_day(half, [])
    assert res.leave_minutes == 240 and res.available_minutes == 240 and res.on_leave
    full = roster(leaves=[{"leave_type": "full_day", "status": "approved"}])
    res = compute_day(full, [])
    assert res.leave_minutes == 480 and res.available_minutes == 0
    pending = roster(leaves=[{"leave_type": "full_day", "status": "pending"}])
    assert compute_day(pending, []).leave_minutes == 0


def test_extra_hours_and_overtime():
    r = roster(extra_segments=[{"start_time": "17:00", "end_time": "19:00"}])
    res = compute_day(r, [sess("s1", 18, 0, 19, 30)])
    assert res.extra_minutes == 120 and res.available_minutes == 600
    assert res.worked_minutes == 90
    assert res.overtime_minutes == 30  # 19:00-19:30 outside any available window
    svc = [s for s in res.segments if s.segment_type == "service"]
    assert svc and svc[0].is_overtime


def test_overlapping_sessions_not_double_counted():
    res = compute_day(roster(), [sess("a", 10, 0, 11, 0), sess("b", 10, 30, 11, 30)])
    assert res.worked_minutes == 90


def test_day_off_and_unscheduled_work():
    off = roster(is_day_off=True, shift_start=None, shift_end=None)
    res = compute_day(off, [])
    assert res.available_minutes == 0 and [s.segment_type for s in res.segments] == ["off"]
    res = compute_day(off, [sess("s", 10, 0, 11, 0)])
    assert res.worked_minutes == 60 and res.overtime_minutes == 60 and res.idle_minutes == 0


def test_service_split_and_addons():
    services = [
        {"service_id": "11111111-1111-1111-1111-111111111111", "name": "Massage", "duration_minutes": 45},
        {"service_id": "22222222-2222-2222-2222-222222222222", "name": "Scrub", "duration_minutes": 30},
    ]
    res = compute_day(roster(), [sess("s", 10, 0, 11, 30, services, extra_minutes=15)])
    svc = [s for s in res.segments if s.segment_type == "service"]
    assert [(s.service_name, s.minutes) for s in svc] == [("Massage", 45), ("Scrub", 30), ("Add-ons / extra time", 15)]


def test_session_crossing_midnight_is_clipped():
    s = SessionInput(
        session_id="x", booking_id="b", therapist_id="t1",
        start=datetime(2026, 11, 5, 23, 30, tzinfo=KUWAIT_TZ),
        end=datetime(2026, 11, 6, 0, 30, tzinfo=KUWAIT_TZ),
    )
    assert compute_day(roster(is_day_off=True), [s]).worked_minutes == 30
