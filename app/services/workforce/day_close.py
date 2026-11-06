"""
app/services/workforce/day_close.py
────────────────────────────────────
Pure (no I/O) computation of a therapist's day: availability, worked / idle time and the
ordered activity timeline.  Everything is expressed as integer minutes since local midnight
(Asia/Kuwait has no DST) so the maths is exact and trivially testable.

Definitions
───────────
available   = (shift − break − leave)  ∪  approved extra-hours windows
worked      = union of COMPLETED session windows (overlaps merged, clipped to the day)
idle        = available − worked
overtime    = worked − available        (completed work outside any available window)
utilization = |worked ∩ available| / |available| × 100

Only COMPLETED sessions are ever passed in; cancelled / no-show / confirmed bookings are
filtered out by the source, so they never count as work (their time simply stays idle).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from zoneinfo import ZoneInfo

KUWAIT_TZ = ZoneInfo("Asia/Kuwait")
DAY_MINUTES = 24 * 60

Interval = tuple[int, int]


# ── interval algebra ──────────────────────────────────────────────────────────


def merge(intervals: list[Interval]) -> list[Interval]:
    """Sort and merge overlapping / touching intervals; drops empty ones."""
    out: list[Interval] = []
    for s, e in sorted(i for i in intervals if i[1] > i[0]):
        if out and s <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out


def subtract(a: list[Interval], b: list[Interval]) -> list[Interval]:
    """``a − b`` for merged interval lists."""
    a, b = merge(a), merge(b)
    out: list[Interval] = []
    for s, e in a:
        cur = s
        for bs, be in b:
            if be <= cur or bs >= e:
                continue
            if bs > cur:
                out.append((cur, bs))
            cur = max(cur, be)
            if cur >= e:
                break
        if cur < e:
            out.append((cur, e))
    return out


def intersect(a: list[Interval], b: list[Interval]) -> list[Interval]:
    """``a ∩ b`` for interval lists."""
    out: list[Interval] = []
    for s1, e1 in merge(a):
        for s2, e2 in merge(b):
            s, e = max(s1, s2), min(e1, e2)
            if e > s:
                out.append((s, e))
    return merge(out)


def total(intervals: list[Interval]) -> int:
    return sum(e - s for s, e in merge(intervals))


def clip(s: int, e: int) -> Interval:
    return max(0, s), min(DAY_MINUTES, e)


def to_min(t: time | None) -> int | None:
    return None if t is None else t.hour * 60 + t.minute


def parse_time(value: Any) -> time | None:
    if value is None or value == "":
        return None
    if isinstance(value, time):
        return value
    return time.fromisoformat(str(value))


def local_minutes(dt: datetime, day: date) -> int:
    """Minutes from local midnight of ``day`` (may be <0 or >1440 for other days)."""
    local = dt.astimezone(KUWAIT_TZ) if dt.tzinfo else dt.replace(tzinfo=KUWAIT_TZ)
    midnight = datetime.combine(day, time(0, 0), tzinfo=KUWAIT_TZ)
    return int((local - midnight).total_seconds() // 60)


def parse_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=KUWAIT_TZ)


# ── inputs / outputs ──────────────────────────────────────────────────────────


@dataclass
class RosterInput:
    therapist_id: str
    local_date: date
    employee_id: str | None = None
    branch_id: str | None = None
    therapist_name: str | None = None
    therapist_status: str | None = None
    is_day_off: bool = False
    shift_start: time | None = None
    shift_end: time | None = None
    break_start: time | None = None
    break_end: time | None = None
    break_duration_config: int | None = None
    working_hours_id: str | None = None
    leaves: list[dict[str, Any]] = field(default_factory=list)  # approved only
    extra_segments: list[dict[str, Any]] = field(default_factory=list)  # approved only
    data_quality: str = "recorded"

    @classmethod
    def from_payload(cls, payload: dict[str, Any], day: date) -> "RosterInput":
        wh = payload.get("working_hours") or None
        off = wh is None or bool(wh.get("is_weekend_off"))
        return cls(
            therapist_id=str(payload["therapist_id"]),
            local_date=day,
            employee_id=_s(payload.get("employee_id")),
            branch_id=_s(payload.get("branch_id")),
            therapist_name=payload.get("therapist_name"),
            therapist_status=payload.get("therapist_status"),
            is_day_off=off,
            shift_start=None if off else parse_time(wh.get("start_time")),
            shift_end=None if off else parse_time(wh.get("end_time")),
            break_start=None if off else parse_time(wh.get("break_start_time")),
            break_end=None if off else parse_time(wh.get("break_end_time")),
            break_duration_config=None if wh is None else wh.get("break_duration"),
            working_hours_id=None if wh is None else _s(wh.get("id")),
            leaves=list(payload.get("leaves") or []),
            extra_segments=list(payload.get("extra_hours") or []),
        )


@dataclass
class SessionInput:
    session_id: str
    booking_id: str
    therapist_id: str
    start: datetime
    end: datetime
    booking_number: str | None = None
    role: str = "primary"
    branch_id: str | None = None
    customer_id: str | None = None
    booking_type: str | None = None
    services: list[dict[str, Any]] = field(default_factory=list)
    addons_minutes: int = 0
    extra_minutes: int = 0

    @classmethod
    def from_payload(cls, p: dict[str, Any]) -> "SessionInput":
        return cls(
            session_id=str(p["session_id"]),
            booking_id=str(p["booking_id"]),
            therapist_id=str(p["therapist_id"]),
            start=parse_dt(p["start"]),
            end=parse_dt(p["end"]),
            booking_number=p.get("booking_number"),
            role=p.get("role") or "primary",
            branch_id=_s(p.get("branch_id")),
            customer_id=_s(p.get("customer_id")),
            booking_type=p.get("booking_type"),
            services=list(p.get("services") or []),
            addons_minutes=int(p.get("addons_minutes") or 0),
            extra_minutes=int(p.get("extra_minutes") or 0),
        )


@dataclass
class Segment:
    segment_type: str
    start_min: int
    end_min: int
    is_overtime: bool = False
    session: SessionInput | None = None
    service_id: str | None = None
    service_name: str | None = None
    detail: str | None = None

    @property
    def minutes(self) -> int:
        return self.end_min - self.start_min


@dataclass
class DayResult:
    scheduled_minutes: int
    break_minutes: int
    leave_minutes: int
    extra_minutes: int
    available_minutes: int
    worked_minutes: int
    idle_minutes: int
    overtime_minutes: int
    sessions_completed: int
    utilization_pct: Decimal
    on_leave: bool
    segments: list[Segment]


def _s(v: Any) -> str | None:
    return None if v in (None, "") else str(v)


# ── core ──────────────────────────────────────────────────────────────────────


def _roster_windows(r: RosterInput) -> dict[str, list[Interval]]:
    """Resolve shift / break / leave / extra windows (minutes) of a roster."""
    shift: list[Interval] = []
    brk: list[Interval] = []
    leave: list[Interval] = []
    extra: list[Interval] = []

    if not r.is_day_off and r.shift_start is not None and r.shift_end is not None:
        s, e = clip(to_min(r.shift_start), to_min(r.shift_end))
        if e > s:
            shift = [(s, e)]
        if r.break_start is not None and r.break_end is not None:
            brk = intersect([clip(to_min(r.break_start), to_min(r.break_end))], shift)

    for lv in r.leaves:
        if (lv.get("status") or "approved") != "approved":
            continue
        if lv.get("leave_type") == "half_day" and lv.get("start_time") and lv.get("end_time"):
            leave.append(clip(to_min(parse_time(lv["start_time"])), to_min(parse_time(lv["end_time"]))))
        else:  # full day (or half-day without times) → whole shift
            leave.extend(shift)
    leave = intersect(leave, shift) if shift else []

    for ex in r.extra_segments:
        s, e = to_min(parse_time(ex.get("start_time"))), to_min(parse_time(ex.get("end_time")))
        if s is not None and e is not None:
            extra.append(clip(s, e))

    return {"shift": merge(shift), "break": merge(brk), "leave": merge(leave), "extra": merge(extra)}


def _service_segments(sess: SessionInput, s: int, e: int, day: date) -> list[tuple[int, int, str | None, str | None]]:
    """Split a session window sequentially across its services (leftover → add-ons/extra)."""
    out: list[tuple[int, int, str | None, str | None]] = []
    cur = s
    for svc in sess.services:
        dur = int(svc.get("duration_minutes") or 0)
        if dur <= 0 or cur >= e:
            continue
        nxt = min(e, cur + dur)
        out.append((cur, nxt, _s(svc.get("service_id")), svc.get("name") or svc.get("service_name")))
        cur = nxt
    if cur < e:
        if out and not (sess.addons_minutes or sess.extra_minutes):
            # rounding drift: attach the remainder to the last service
            last = out[-1]
            out[-1] = (last[0], e, last[2], last[3])
        else:
            out.append((cur, e, None, "Add-ons / extra time" if (sess.addons_minutes or sess.extra_minutes) else None))
    if not out:
        out.append((s, e, None, None))
    return out


def compute_day(roster: RosterInput, sessions: list[SessionInput]) -> DayResult:
    """Compute minutes totals and the ordered timeline for one therapist-day."""
    day = roster.local_date
    w = _roster_windows(roster)
    shift, brk, leave, extra = w["shift"], w["break"], w["leave"], w["extra"]

    base_available = subtract(subtract(shift, brk), leave)
    available = merge(base_available + extra)

    # Completed sessions → clipped windows
    clipped: list[tuple[SessionInput, int, int]] = []
    for sess in sessions:
        s, e = clip(local_minutes(sess.start, day), local_minutes(sess.end, day))
        if e > s:
            clipped.append((sess, s, e))
    worked = merge([(s, e) for _, s, e in clipped])

    worked_in_avail = intersect(worked, available)
    available_min = total(available)
    worked_min = total(worked)
    in_avail_min = total(worked_in_avail)
    idle = subtract(available, worked)
    overtime_ivs = subtract(worked, available)

    util = Decimal(0)
    if available_min:
        util = (Decimal(in_avail_min) * 100 / Decimal(available_min)).quantize(Decimal("0.01"), ROUND_HALF_UP)

    segments: list[Segment] = []
    if roster.is_day_off or not shift:
        if not extra and not clipped:
            segments.append(Segment("off", 0, DAY_MINUTES, detail="day off"))
    for s, e in brk:
        segments.append(Segment("break", s, e))
    for s, e in leave:
        segments.append(Segment("leave", s, e))
    for s, e in idle:
        segments.append(Segment("idle", s, e))
    for sess, s, e in clipped:
        for ss, se, sid, name in _service_segments(sess, s, e, day):
            if se <= ss:
                continue
            ot = bool(total(intersect([(ss, se)], overtime_ivs)))
            segments.append(Segment("service", ss, se, is_overtime=ot, session=sess, service_id=sid, service_name=name))
    segments.sort(key=lambda x: (x.start_min, x.end_min))

    return DayResult(
        scheduled_minutes=total(shift),
        break_minutes=total(brk),
        leave_minutes=total(leave),
        extra_minutes=total(extra),
        available_minutes=available_min,
        worked_minutes=worked_min,
        idle_minutes=total(idle),
        overtime_minutes=total(overtime_ivs),
        sessions_completed=len({sess.session_id for sess, _, _ in clipped}),
        utilization_pct=util,
        on_leave=bool(leave),
        segments=segments,
    )


def minute_to_dt(day: date, minute: int) -> datetime:
    """Aware Kuwait datetime for ``minute`` minutes after local midnight of ``day``."""
    return datetime.combine(day, time(0, 0), tzinfo=KUWAIT_TZ) + timedelta(minutes=minute)
