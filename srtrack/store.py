"""SQLite-backed storage for Oracle Service Requests."""

from __future__ import annotations

import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable, Optional

# My Oracle Support status codes -> (description, who must act next).
# "oracle" = ball is in Oracle's court, "us" = we owe Oracle something,
# "closed" = finished.
STATUSES: dict[str, tuple[str, str]] = {
    "NEW": ("New", "oracle"),
    "WIP": ("Work In Progress", "oracle"),
    "DEV": ("Development Working", "oracle"),
    "RVW": ("Review Update", "oracle"),
    "CUS": ("Customer Working", "us"),
    "SOL": ("Solution Offered", "us"),
    "ACL": ("Auto-Close Pending", "us"),
    "CLO": ("Closed", "closed"),
}
SEVERITIES = (1, 2, 3, 4)

# Accept long names / common aliases for statuses.
_STATUS_ALIASES = {
    "WORKINPROGRESS": "WIP",
    "INPROGRESS": "WIP",
    "DEVELOPMENT": "DEV",
    "DEVELOPMENTWORKING": "DEV",
    "REVIEW": "RVW",
    "REVIEWUPDATE": "RVW",
    "CUSTOMER": "CUS",
    "CUSTOMERWORKING": "CUS",
    "SOLUTION": "SOL",
    "SOLUTIONOFFERED": "SOL",
    "AUTOCLOSE": "ACL",
    "AUTOCLOSEPENDING": "ACL",
    "CLOSED": "CLO",
    "CLOSE": "CLO",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS sr (
    number       TEXT PRIMARY KEY,
    title        TEXT NOT NULL,
    product      TEXT NOT NULL DEFAULT '',
    severity     INTEGER NOT NULL DEFAULT 3,
    status       TEXT NOT NULL DEFAULT 'NEW',
    owner        TEXT NOT NULL DEFAULT '',
    oracle_engineer TEXT NOT NULL DEFAULT '',
    environment  TEXT NOT NULL DEFAULT '',
    next_action  TEXT NOT NULL DEFAULT '',
    follow_up    TEXT,              -- ISO date
    tags         TEXT NOT NULL DEFAULT '',  -- comma separated
    bugs         TEXT NOT NULL DEFAULT '',  -- comma separated bug numbers
    resolution   TEXT NOT NULL DEFAULT '',
    opened_at    TEXT NOT NULL,     -- ISO date
    status_since TEXT NOT NULL,     -- ISO timestamp of last status change
    updated_at   TEXT NOT NULL,     -- ISO timestamp
    closed_at    TEXT
);
CREATE INDEX IF NOT EXISTS sr_status ON sr(status);
CREATE INDEX IF NOT EXISTS sr_follow_up ON sr(follow_up);

CREATE TABLE IF NOT EXISTS event (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    sr_number TEXT NOT NULL REFERENCES sr(number) ON DELETE CASCADE ON UPDATE CASCADE,
    at        TEXT NOT NULL,
    kind      TEXT NOT NULL,        -- note | change | created
    text      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS event_sr ON event(sr_number, at);
"""

EDITABLE_FIELDS = (
    "title",
    "product",
    "severity",
    "status",
    "owner",
    "oracle_engineer",
    "environment",
    "next_action",
    "follow_up",
    "tags",
    "bugs",
    "resolution",
    "opened_at",
)

SR_NUMBER_RE = re.compile(r"^\d-[0-9A-Z]{5,}$")


class SRError(Exception):
    """User-facing error (bad input, unknown SR, ...)."""


@dataclass
class SR:
    number: str
    title: str
    product: str
    severity: int
    status: str
    owner: str
    oracle_engineer: str
    environment: str
    next_action: str
    follow_up: Optional[str]
    tags: str
    bugs: str
    resolution: str
    opened_at: str
    status_since: str
    updated_at: str
    closed_at: Optional[str]

    @property
    def status_name(self) -> str:
        return STATUSES.get(self.status, (self.status, ""))[0]

    @property
    def waiting_on(self) -> str:
        return STATUSES.get(self.status, ("", "oracle"))[1]

    @property
    def is_open(self) -> bool:
        return self.status != "CLO"

    @property
    def tag_list(self) -> list[str]:
        return split_list(self.tags)

    @property
    def bug_list(self) -> list[str]:
        return split_list(self.bugs)

    def age_days(self, today: Optional[date] = None) -> int:
        today = today or date.today()
        end = date.fromisoformat(self.closed_at[:10]) if self.closed_at else today
        return (end - date.fromisoformat(self.opened_at[:10])).days

    def days_in_status(self, now: Optional[datetime] = None) -> int:
        now = now or datetime.now()
        return (now.date() - datetime.fromisoformat(self.status_since).date()).days

    def is_overdue(self, today: Optional[date] = None) -> bool:
        today = today or date.today()
        return bool(self.is_open and self.follow_up and date.fromisoformat(self.follow_up) < today)

    def is_due(self, today: Optional[date] = None) -> bool:
        today = today or date.today()
        return bool(self.is_open and self.follow_up and date.fromisoformat(self.follow_up) <= today)


@dataclass
class Event:
    id: int
    sr_number: str
    at: str
    kind: str
    text: str


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def split_list(value: str) -> list[str]:
    return [v.strip() for v in (value or "").split(",") if v.strip()]


def join_list(values: Iterable[str]) -> str:
    seen: list[str] = []
    for v in values:
        v = v.strip()
        if v and v not in seen:
            seen.append(v)
    return ",".join(seen)


def normalize_status(value: str) -> str:
    key = re.sub(r"[^A-Z]", "", value.upper())
    if key in STATUSES:
        return key
    if key in _STATUS_ALIASES:
        return _STATUS_ALIASES[key]
    raise SRError(
        f"unknown status {value!r}; use one of: "
        + ", ".join(f"{k} ({v[0]})" for k, v in STATUSES.items())
    )


def normalize_severity(value) -> int:
    try:
        sev = int(re.sub(r"^(SEV|S)", "", str(value).strip().upper()).strip())
    except ValueError:
        raise SRError(f"invalid severity {value!r}; use 1-4") from None
    if sev not in SEVERITIES:
        raise SRError(f"invalid severity {value!r}; use 1-4")
    return sev


def normalize_number(value: str) -> str:
    num = value.strip().upper().replace(" ", "")
    if not SR_NUMBER_RE.match(num):
        raise SRError(f"invalid SR number {value!r}; expected format like 3-38123456789")
    return num


_WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def parse_date(value: Optional[str], today: Optional[date] = None) -> Optional[str]:
    """Parse a user date. Supports ISO dates, today/tomorrow, +Nd/+Nw, weekday names.

    Returns an ISO date string, or None for empty / 'none' / 'clear'.
    """
    if value is None:
        return None
    today = today or date.today()
    v = value.strip().lower()
    if v in ("", "none", "clear", "-"):
        return None
    if v in ("today", "now"):
        return today.isoformat()
    if v == "tomorrow":
        return (today + timedelta(days=1)).isoformat()
    if v == "yesterday":
        return (today - timedelta(days=1)).isoformat()
    m = re.fullmatch(r"([+-]?)(\d+)([dwm]?)", v)
    if m and (m.group(1) or m.group(3)):
        n = int(m.group(2)) * (-1 if m.group(1) == "-" else 1)
        unit = m.group(3) or "d"
        days = n * {"d": 1, "w": 7, "m": 30}[unit]
        return (today + timedelta(days=days)).isoformat()
    for i, wd in enumerate(_WEEKDAYS):
        if v.startswith(wd):
            delta = (i - today.weekday()) % 7 or 7
            return (today + timedelta(days=delta)).isoformat()
    try:
        return date.fromisoformat(v).isoformat()
    except ValueError:
        raise SRError(
            f"invalid date {value!r}; use YYYY-MM-DD, today, tomorrow, +3d, +2w or a weekday"
        ) from None


def _now() -> str:
    return datetime.now().replace(microsecond=0).isoformat(sep=" ")


def default_db_path() -> Path:
    env = os.environ.get("SRT_DB")
    if env:
        return Path(env).expanduser()
    base = os.environ.get("XDG_DATA_HOME") or os.path.join(Path.home(), ".local", "share")
    return Path(base) / "srtrack" / "srs.db"


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

class Store:
    def __init__(self, path: Path | str):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- lookup -------------------------------------------------------------

    def resolve(self, ref: str) -> str:
        """Resolve a full SR number or a unique suffix (e.g. last 4 digits)."""
        ref = ref.strip().upper()
        row = self.conn.execute("SELECT number FROM sr WHERE number = ?", (ref,)).fetchone()
        if row:
            return row["number"]
        rows = self.conn.execute(
            "SELECT number FROM sr WHERE number LIKE ? ORDER BY number", ("%" + ref,)
        ).fetchall()
        if not rows:
            raise SRError(f"no SR matching {ref!r}")
        if len(rows) > 1:
            raise SRError(
                f"{ref!r} is ambiguous: " + ", ".join(r["number"] for r in rows[:10])
            )
        return rows[0]["number"]

    def get(self, ref: str) -> SR:
        number = self.resolve(ref)
        row = self.conn.execute("SELECT * FROM sr WHERE number = ?", (number,)).fetchone()
        return SR(**dict(row))

    def exists(self, number: str) -> bool:
        return (
            self.conn.execute("SELECT 1 FROM sr WHERE number = ?", (number,)).fetchone()
            is not None
        )

    # -- writes -------------------------------------------------------------

    def add(
        self,
        number: str,
        title: str,
        *,
        product: str = "",
        severity=3,
        status: str = "NEW",
        owner: str = "",
        oracle_engineer: str = "",
        environment: str = "",
        next_action: str = "",
        follow_up: Optional[str] = None,
        tags: str = "",
        bugs: str = "",
        resolution: str = "",
        opened_at: Optional[str] = None,
        note: str = "",
    ) -> SR:
        number = normalize_number(number)
        if self.exists(number):
            raise SRError(f"SR {number} already exists")
        if not title.strip():
            raise SRError("title must not be empty")
        status = normalize_status(status)
        now = _now()
        opened = parse_date(opened_at) or date.today().isoformat()
        with self.conn:
            self.conn.execute(
                """INSERT INTO sr (number, title, product, severity, status, owner,
                       oracle_engineer, environment, next_action, follow_up, tags, bugs,
                       resolution, opened_at, status_since, updated_at, closed_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    number,
                    title.strip(),
                    product.strip(),
                    normalize_severity(severity),
                    status,
                    owner.strip(),
                    oracle_engineer.strip(),
                    environment.strip(),
                    next_action.strip(),
                    parse_date(follow_up),
                    join_list(split_list(tags)),
                    join_list(split_list(bugs)),
                    resolution.strip(),
                    opened,
                    now,
                    now,
                    now if status == "CLO" else None,
                ),
            )
            self._event(number, "created", f"SR created (Sev {normalize_severity(severity)}, {status})", now)
            if note.strip():
                self._event(number, "note", note.strip(), now)
        return self.get(number)

    def update(self, ref: str, **changes) -> tuple[SR, list[str]]:
        """Apply field changes. Returns the updated SR and human-readable change list."""
        sr = self.get(ref)
        updates: dict[str, object] = {}
        descriptions: list[str] = []
        for field, value in changes.items():
            if value is None:
                continue
            if field not in EDITABLE_FIELDS:
                raise SRError(f"cannot edit field {field!r}")
            if field == "status":
                value = normalize_status(value)
            elif field == "severity":
                value = normalize_severity(value)
            elif field in ("follow_up", "opened_at"):
                value = parse_date(value)
                if field == "opened_at" and value is None:
                    raise SRError("opened date cannot be cleared")
            elif field in ("tags", "bugs"):
                value = join_list(split_list(value))
            else:
                value = str(value).strip()
                if field == "title" and not value:
                    raise SRError("title must not be empty")
            old = getattr(sr, field)
            if old == value:
                continue
            updates[field] = value
            descriptions.append(f"{field}: {old or '-'} -> {value or '-'}")

        if not updates:
            return sr, []

        now = _now()
        if "status" in updates:
            updates["status_since"] = now
            if updates["status"] == "CLO":
                updates["closed_at"] = now
            elif sr.status == "CLO":
                updates["closed_at"] = None
        updates["updated_at"] = now
        cols = ", ".join(f"{k} = ?" for k in updates)
        with self.conn:
            self.conn.execute(
                f"UPDATE sr SET {cols} WHERE number = ?", (*updates.values(), sr.number)
            )
            self._event(sr.number, "change", "; ".join(descriptions), now)
        return self.get(sr.number), descriptions

    def add_tags(self, ref: str, tags: Iterable[str], remove: bool = False) -> SR:
        sr = self.get(ref)
        current = sr.tag_list
        tags = [t.strip() for t in tags if t.strip()]
        new = [t for t in current if t not in tags] if remove else current + tags
        return self.update(sr.number, tags=join_list(new))[0]

    def link_bugs(self, ref: str, bugs: Iterable[str], remove: bool = False) -> SR:
        sr = self.get(ref)
        current = sr.bug_list
        bugs = [b.strip() for b in bugs if b.strip()]
        new = [b for b in current if b not in bugs] if remove else current + bugs
        return self.update(sr.number, bugs=join_list(new))[0]

    def add_note(self, ref: str, text: str) -> Event:
        if not text.strip():
            raise SRError("note must not be empty")
        number = self.resolve(ref)
        now = _now()
        with self.conn:
            self.conn.execute("UPDATE sr SET updated_at = ? WHERE number = ?", (now, number))
            event_id = self._event(number, "note", text.strip(), now)
        return Event(event_id, number, now, "note", text.strip())

    def delete(self, ref: str) -> str:
        number = self.resolve(ref)
        with self.conn:
            self.conn.execute("DELETE FROM sr WHERE number = ?", (number,))
        return number

    def _event(self, number: str, kind: str, text: str, at: Optional[str] = None) -> int:
        cur = self.conn.execute(
            "INSERT INTO event (sr_number, at, kind, text) VALUES (?,?,?,?)",
            (number, at or _now(), kind, text),
        )
        return int(cur.lastrowid)

    # -- queries ------------------------------------------------------------

    def events(self, ref: str, kinds: Optional[Iterable[str]] = None) -> list[Event]:
        number = self.resolve(ref)
        sql = "SELECT * FROM event WHERE sr_number = ?"
        params: list = [number]
        if kinds:
            kinds = list(kinds)
            sql += f" AND kind IN ({','.join('?' * len(kinds))})"
            params += kinds
        sql += " ORDER BY at, id"
        return [Event(**dict(r)) for r in self.conn.execute(sql, params)]

    def last_note(self, number: str) -> Optional[Event]:
        row = self.conn.execute(
            "SELECT * FROM event WHERE sr_number = ? AND kind = 'note' ORDER BY at DESC, id DESC LIMIT 1",
            (number,),
        ).fetchone()
        return Event(**dict(row)) if row else None

    def query(
        self,
        *,
        include_closed: bool = False,
        only_closed: bool = False,
        statuses: Optional[Iterable[str]] = None,
        severities: Optional[Iterable[int]] = None,
        waiting_on: Optional[str] = None,
        product: Optional[str] = None,
        owner: Optional[str] = None,
        tag: Optional[str] = None,
        text: Optional[str] = None,
        due_by: Optional[str] = None,
        sort: str = "priority",
    ) -> list[SR]:
        where: list[str] = []
        params: list = []
        if only_closed:
            where.append("status = 'CLO'")
        elif not include_closed and not statuses:
            where.append("status != 'CLO'")
        if statuses:
            st = [normalize_status(s) for s in statuses]
            where.append(f"status IN ({','.join('?' * len(st))})")
            params += st
        if severities:
            sv = [normalize_severity(s) for s in severities]
            where.append(f"severity IN ({','.join('?' * len(sv))})")
            params += sv
        if waiting_on:
            codes = [k for k, (_, w) in STATUSES.items() if w == waiting_on]
            where.append(f"status IN ({','.join('?' * len(codes))})")
            params += codes
        if product:
            where.append("product LIKE ?")
            params.append(f"%{product}%")
        if owner:
            where.append("owner LIKE ?")
            params.append(f"%{owner}%")
        if tag:
            where.append("(',' || tags || ',') LIKE ?")
            params.append(f"%,{tag},%")
        if due_by:
            where.append("follow_up IS NOT NULL AND follow_up <= ?")
            params.append(due_by)
        if text:
            like = f"%{text}%"
            where.append(
                """(number LIKE ? OR title LIKE ? OR product LIKE ? OR next_action LIKE ?
                    OR environment LIKE ? OR bugs LIKE ? OR tags LIKE ? OR resolution LIKE ?
                    OR number IN (SELECT sr_number FROM event WHERE text LIKE ?))"""
            )
            params += [like] * 9

        order = {
            # Sev first, then who must act (us first), then follow-up date.
            "priority": "severity, CASE status WHEN 'CLO' THEN 2 ELSE "
            "(CASE WHEN status IN ('CUS','SOL','ACL') THEN 0 ELSE 1 END) END, "
            "COALESCE(follow_up, '9999-12-31'), opened_at",
            "follow_up": "COALESCE(follow_up, '9999-12-31'), severity",
            "opened": "opened_at",
            "updated": "updated_at DESC",
            "severity": "severity, opened_at",
            "number": "number",
        }.get(sort)
        if order is None:
            raise SRError(f"unknown sort {sort!r}")
        sql = "SELECT * FROM sr"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY " + order
        return [SR(**dict(r)) for r in self.conn.execute(sql, params)]

    def all(self) -> list[SR]:
        return self.query(include_closed=True, sort="number")
