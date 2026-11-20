"""Agenda and statistics shared by the CLI and the web UI."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional

from .store import SR, STATUSES, Store


@dataclass
class Agenda:
    today: date
    open_count: int
    due: list[SR]
    on_us: list[SR]
    stale: list[SR]
    auto_close: list[SR]


def agenda(store: Store, stale_days: int = 7, now: Optional[datetime] = None) -> Agenda:
    now = now or datetime.now()
    today = now.date()
    open_srs = store.query()
    due = [s for s in open_srs if s.is_due(today)]
    shown = {s.number for s in due}
    on_us = [s for s in open_srs if s.waiting_on == "us" and s.number not in shown]
    shown |= {s.number for s in on_us}
    cutoff = now - timedelta(days=stale_days)
    stale = [
        s for s in open_srs if s.number not in shown and datetime.fromisoformat(s.updated_at) < cutoff
    ]
    auto_close = [s for s in open_srs if s.status == "ACL"]
    return Agenda(today, len(open_srs), due, on_us, stale, auto_close)


def _median(values: list[int]) -> int:
    return sorted(values)[len(values) // 2]


@dataclass
class Stats:
    total: int = 0
    open: int = 0
    closed: int = 0
    waiting_oracle: int = 0
    waiting_us: int = 0
    overdue: int = 0
    open_age_median: Optional[int] = None
    open_age_max: Optional[int] = None
    by_severity: list[tuple[int, int]] = field(default_factory=list)
    by_status: list[tuple[str, str, int]] = field(default_factory=list)
    by_product: list[tuple[str, int]] = field(default_factory=list)
    close_median: Optional[int] = None
    close_mean: Optional[float] = None
    closed_last_30: int = 0


def stats(store: Store, today: Optional[date] = None) -> Stats:
    today = today or date.today()
    srs = store.all()
    open_srs = [s for s in srs if s.is_open]
    closed = [s for s in srs if not s.is_open]
    st = Stats(total=len(srs), open=len(open_srs), closed=len(closed))
    if open_srs:
        waiting = Counter(s.waiting_on for s in open_srs)
        st.waiting_oracle = waiting.get("oracle", 0)
        st.waiting_us = waiting.get("us", 0)
        st.overdue = sum(1 for s in open_srs if s.is_overdue(today))
        ages = [s.age_days(today) for s in open_srs]
        st.open_age_median, st.open_age_max = _median(ages), max(ages)
        sev = Counter(s.severity for s in open_srs)
        st.by_severity = sorted(sev.items())
        status = Counter(s.status for s in open_srs)
        st.by_status = [(k, STATUSES[k][0], status[k]) for k in STATUSES if status.get(k)]
        st.by_product = Counter(s.product or "(none)" for s in open_srs).most_common(10)
    if closed:
        durations = [s.age_days() for s in closed]
        st.close_median = _median(durations)
        st.close_mean = sum(durations) / len(durations)
        since = (today - timedelta(days=30)).isoformat()
        st.closed_last_30 = sum(1 for s in closed if s.closed_at and s.closed_at[:10] >= since)
    return st
