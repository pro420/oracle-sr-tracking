"""Command-line interface: `srt`."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from typing import Optional, Sequence

from . import __version__
from .io import export_csv, export_json, import_csv
from .reports import agenda, stats
from .store import STATUSES, SR, SRError, Store, default_db_path, parse_date

# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

_COLOR = False


def _c(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _COLOR else text


def bold(t: str) -> str:
    return _c(t, "1")


def dim(t: str) -> str:
    return _c(t, "2")


def red(t: str) -> str:
    return _c(t, "31")


def yellow(t: str) -> str:
    return _c(t, "33")


def green(t: str) -> str:
    return _c(t, "32")


def cyan(t: str) -> str:
    return _c(t, "36")


SEV_COLOR = {1: lambda t: _c(t, "1;31"), 2: red, 3: yellow, 4: dim}


def _visible_len(s: str) -> int:
    import re

    return len(re.sub(r"\033\[[0-9;]*m", "", s))


def _truncate(s: str, width: int) -> str:
    return s if len(s) <= width else s[: max(width - 1, 0)] + "…"


def print_table(headers: list[str], rows: list[list[str]], max_widths: Optional[dict[int, int]] = None) -> None:
    max_widths = max_widths or {}
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], _visible_len(cell))
    for i, mw in max_widths.items():
        widths[i] = min(widths[i], mw)
    print("  ".join(bold(h.ljust(widths[i])) for i, h in enumerate(headers)).rstrip())
    for row in rows:
        cells = []
        for i, cell in enumerate(row):
            if _visible_len(cell) > widths[i]:
                cell = _truncate(cell, widths[i])
            cells.append(cell + " " * (widths[i] - _visible_len(cell)))
        print("  ".join(cells).rstrip())


def fmt_follow_up(sr: SR, today: date) -> str:
    if not sr.follow_up:
        return dim("-")
    d = date.fromisoformat(sr.follow_up)
    delta = (d - today).days
    label = sr.follow_up
    if not sr.is_open:
        return dim(label)
    if delta < 0:
        return red(f"{label} ({-delta}d late)")
    if delta == 0:
        return yellow(f"{label} (today)")
    if delta == 1:
        return f"{label} (tmrw)"
    return label


def fmt_waiting(sr: SR) -> str:
    w = sr.waiting_on
    if w == "us":
        return yellow("US")
    if w == "closed":
        return dim("-")
    return cyan("Oracle")


def sr_rows(srs: list[SR], today: date) -> list[list[str]]:
    rows = []
    for sr in srs:
        rows.append(
            [
                sr.number,
                SEV_COLOR[sr.severity](f"S{sr.severity}"),
                sr.status,
                fmt_waiting(sr),
                f"{sr.age_days(today)}d",
                fmt_follow_up(sr, today),
                sr.title,
                sr.next_action or dim("-"),
            ]
        )
    return rows


SR_HEADERS = ["SR", "SEV", "STAT", "WAITING", "AGE", "FOLLOW-UP", "TITLE", "NEXT ACTION"]
SR_WIDTHS = {6: 45, 7: 40}


def print_srs(srs: list[SR], today: Optional[date] = None, empty: str = "No SRs found.") -> None:
    today = today or date.today()
    if not srs:
        print(dim(empty))
        return
    print_table(SR_HEADERS, sr_rows(srs, today), SR_WIDTHS)


def print_sr_detail(store: Store, sr: SR, show_all_events: bool = True) -> None:
    today = date.today()
    print(bold(f"{sr.number}  {sr.title}"))
    lines = [
        ("Status", f"{sr.status} - {sr.status_name}  ({sr.days_in_status()}d in status)"),
        ("Waiting on", fmt_waiting(sr)),
        ("Severity", SEV_COLOR[sr.severity](str(sr.severity))),
        ("Product", sr.product),
        ("Environment", sr.environment),
        ("Owner", sr.owner),
        ("Oracle engineer", sr.oracle_engineer),
        ("Opened", f"{sr.opened_at}  ({sr.age_days(today)}d old)"),
        ("Closed", sr.closed_at or ""),
        ("Follow-up", fmt_follow_up(sr, today) if sr.follow_up else ""),
        ("Next action", sr.next_action),
        ("Tags", ", ".join(sr.tag_list)),
        ("Bugs", ", ".join(sr.bug_list)),
        ("Resolution", sr.resolution),
        ("Updated", sr.updated_at),
    ]
    for label, value in lines:
        if value:
            print(f"  {dim(label.ljust(16))}{value}")
    events = store.events(sr.number)
    if events:
        print()
        print(bold("History"))
        for e in events if show_all_events else events[-10:]:
            marker = {"note": "•", "change": "~", "created": "+"}.get(e.kind, " ")
            text = e.text if e.kind == "note" else dim(e.text)
            prefix = f"  {dim(e.at[:16])} {marker} "
            first, *rest = text.splitlines() or [""]
            print(prefix + first)
            for line in rest:
                print(" " * (len(e.at[:16]) + 5) + line)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def _read_text(value: Optional[str]) -> Optional[str]:
    """'-' means read from stdin (handy for pasting long updates)."""
    if value == "-":
        return sys.stdin.read()
    return value


def cmd_add(store: Store, a: argparse.Namespace) -> int:
    sr = store.add(
        a.number,
        a.title,
        product=a.product or "",
        severity=a.sev,
        status=a.status,
        owner=a.owner or os.environ.get("SRT_OWNER", ""),
        oracle_engineer=a.engineer or "",
        environment=a.env or "",
        next_action=a.next or "",
        follow_up=a.follow_up,
        tags=a.tags or "",
        bugs=a.bugs or "",
        opened_at=a.opened,
        note=_read_text(a.note) or "",
    )
    print(green(f"Added {sr.number}") + f"  S{sr.severity} {sr.status}  {sr.title}")
    return 0


def _list_filters(a: argparse.Namespace) -> dict:
    return dict(
        include_closed=getattr(a, "all", False),
        only_closed=getattr(a, "closed", False),
        statuses=a.status,
        severities=a.sev,
        waiting_on=a.waiting,
        product=a.product,
        owner=a.owner,
        tag=a.tag,
        sort=a.sort,
    )


def cmd_list(store: Store, a: argparse.Namespace) -> int:
    filters = _list_filters(a)
    if a.due:
        filters["due_by"] = date.today().isoformat()
    srs = store.query(**filters)
    if a.limit:
        srs = srs[: a.limit]
    print_srs(srs)
    if srs:
        print(dim(f"\n{len(srs)} SR(s)"))
    return 0


def cmd_search(store: Store, a: argparse.Namespace) -> int:
    srs = store.query(text=a.text, include_closed=not a.open_only, sort="updated")
    print_srs(srs, empty=f"Nothing matches {a.text!r}.")
    return 0


def cmd_show(store: Store, a: argparse.Namespace) -> int:
    for i, ref in enumerate(a.refs):
        print_sr_detail(store, store.get(ref), show_all_events=not a.recent)
        if i < len(a.refs) - 1:
            print("\n" + dim("─" * 60) + "\n")
    return 0


def _apply_update(store: Store, ref: str, a: argparse.Namespace, extra: Optional[dict] = None) -> SR:
    changes = dict(
        title=a.title,
        product=a.product,
        severity=a.sev,
        status=a.status,
        owner=a.owner,
        oracle_engineer=a.engineer,
        environment=a.env,
        next_action=a.next,
        follow_up=a.follow_up,
        tags=a.tags,
        bugs=a.bugs,
        resolution=getattr(a, "resolution", None),
        opened_at=a.opened,
    )
    changes.update(extra or {})
    sr, descriptions = store.update(ref, **changes)
    note = _read_text(getattr(a, "note", None))
    if note:
        store.add_note(sr.number, note)
    if descriptions:
        print(green(f"Updated {sr.number}"))
        for d in descriptions:
            print(f"  {d}")
    if note:
        print(green(f"Note added to {sr.number}"))
    if not descriptions and not note:
        print(dim(f"{sr.number}: nothing changed"))
    return sr


def cmd_update(store: Store, a: argparse.Namespace) -> int:
    _apply_update(store, a.ref, a)
    return 0


def cmd_note(store: Store, a: argparse.Namespace) -> int:
    text = _read_text(" ".join(a.text) if a.text != ["-"] else "-")
    ev = store.add_note(a.ref, text or "")
    extra = {}
    if a.status:
        extra["status"] = a.status
    if a.follow_up:
        extra["follow_up"] = a.follow_up
    if a.next:
        extra["next_action"] = a.next
    print(green(f"Note added to {ev.sr_number}"))
    if extra:
        _, descriptions = store.update(ev.sr_number, **extra)
        for d in descriptions:
            print(f"  {d}")
    return 0


def cmd_status(store: Store, a: argparse.Namespace) -> int:
    """Quick status change: `srt st 1234 cus`."""
    extra = {"status": a.status}
    if a.follow_up:
        extra["follow_up"] = a.follow_up
    if a.next:
        extra["next_action"] = a.next
    sr, descriptions = store.update(a.ref, **extra)
    if a.note:
        store.add_note(sr.number, _read_text(a.note) or "")
    print(green(f"{sr.number}: {sr.status} ({sr.status_name})") if descriptions else dim(f"{sr.number}: nothing changed"))
    return 0


def cmd_close(store: Store, a: argparse.Namespace) -> int:
    extra = {"status": "CLO", "follow_up": "none", "next_action": ""}
    if a.resolution:
        extra["resolution"] = _read_text(a.resolution)
    sr, _ = store.update(a.ref, **extra)
    print(green(f"Closed {sr.number}") + f" after {sr.age_days()}d")
    return 0


def cmd_reopen(store: Store, a: argparse.Namespace) -> int:
    sr, _ = store.update(a.ref, status=a.status)
    if a.note:
        store.add_note(sr.number, a.note)
    print(green(f"Reopened {sr.number}") + f" as {sr.status}")
    return 0


def cmd_tag(store: Store, a: argparse.Namespace) -> int:
    sr = store.add_tags(a.ref, a.tags, remove=a.remove)
    print(f"{sr.number} tags: {', '.join(sr.tag_list) or '-'}")
    return 0


def cmd_bug(store: Store, a: argparse.Namespace) -> int:
    sr = store.link_bugs(a.ref, a.bugs, remove=a.remove)
    print(f"{sr.number} bugs: {', '.join(sr.bug_list) or '-'}")
    return 0


def cmd_delete(store: Store, a: argparse.Namespace) -> int:
    sr = store.get(a.ref)
    if not a.yes:
        if not sys.stdin.isatty():
            raise SRError("refusing to delete without --yes when not interactive")
        answer = input(f"Delete {sr.number} '{sr.title}' and all its history? [y/N] ")
        if answer.strip().lower() not in ("y", "yes"):
            print("Aborted.")
            return 1
    store.delete(sr.number)
    print(f"Deleted {sr.number}")
    return 0


def cmd_today(store: Store, a: argparse.Namespace) -> int:
    """Daily agenda: what needs attention right now."""
    ag = agenda(store, a.stale)
    print(bold(f"SR agenda for {ag.today.isoformat()}") + dim(f"  ({ag.open_count} open)"))
    if ag.auto_close:
        print(red(f"\n⚠ {len(ag.auto_close)} SR(s) pending auto-close — respond to Oracle or they will close."))
    print(bold("\nFollow-ups due"))
    print_srs(ag.due, ag.today, "  Nothing due.")
    print(bold("\nWaiting on us"))
    print_srs(ag.on_us, ag.today, "  Nothing waiting on us.")
    print(bold(f"\nNo activity in {a.stale}+ days"))
    print_srs(ag.stale, ag.today, "  Nothing stale.")
    return 0


def cmd_stats(store: Store, a: argparse.Namespace) -> int:
    st = stats(store)
    print(bold("Summary"))
    print(f"  Open: {st.open}   Closed: {st.closed}   Total: {st.total}")
    if st.open:
        print(f"  Waiting on Oracle: {st.waiting_oracle}   Waiting on us: {st.waiting_us}")
        print(f"  Overdue follow-ups: {st.overdue}")
        print(f"  Open age: median {st.open_age_median}d, oldest {st.open_age_max}d")
        print(bold("\nOpen by severity"))
        for k, n in st.by_severity:
            print(f"  {SEV_COLOR[k](f'S{k}')}  {n:>4}  {'█' * n}")
        print(bold("\nOpen by status"))
        for code, name, n in st.by_status:
            print(f"  {code}  {n:>4}  {name}")
        print(bold("\nOpen by product"))
        for k, n in st.by_product:
            print(f"  {n:>4}  {k}")
    if st.closed:
        print(bold("\nClosed"))
        print(f"  Time to close: median {st.close_median}d, mean {st.close_mean:.1f}d")
        print(f"  Closed in last 30 days: {st.closed_last_30}")
    return 0


def cmd_export(store: Store, a: argparse.Namespace) -> int:
    srs = store.query(include_closed=not a.open_only, sort="number")
    out = open(a.output, "w", newline="", encoding="utf-8") if a.output else sys.stdout
    try:
        fmt = a.format or ("json" if a.output and a.output.endswith(".json") else "csv")
        if fmt == "json":
            export_json(store, srs, out)
        else:
            export_csv(srs, out)
    finally:
        if a.output:
            out.close()
    if a.output:
        print(f"Exported {len(srs)} SR(s) to {a.output}", file=sys.stderr)
    return 0


def cmd_import(store: Store, a: argparse.Namespace) -> int:
    with open(a.file, newline="", encoding="utf-8-sig") as f:
        added, updated, errors = import_csv(store, f, update_existing=a.update)
    print(green(f"Imported: {added} added, {updated} updated"))
    for e in errors:
        print(red(f"  {e}"), file=sys.stderr)
    return 1 if errors else 0


def cmd_web(store: Store, a: argparse.Namespace) -> int:
    try:
        from .web import create_app
    except ImportError:
        raise SRError("the web UI needs Flask: pip install 'srtrack[web]'") from None
    if a.host not in ("127.0.0.1", "localhost", "::1") and not os.environ.get("SRT_PASSWORD"):
        raise SRError("set SRT_PASSWORD before serving on a non-local address")
    db = store.path
    store.close()
    app = create_app(db)
    print(f"srtrack web UI on http://{a.host}:{a.port}  (Ctrl+C to stop)")
    app.run(host=a.host, port=a.port, debug=False)
    return 0


def cmd_statuses(store: Store, a: argparse.Namespace) -> int:
    for code, (name, who) in STATUSES.items():
        print(f"  {code}  {name.ljust(22)} waiting on: {who}")
    return 0


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def _add_sr_fields(p: argparse.ArgumentParser, for_update: bool) -> None:
    p.add_argument("-s", "--sev", help="severity 1-4")
    p.add_argument("--status", default=None if for_update else "NEW", help="status code (see `srt statuses`)")
    p.add_argument("-p", "--product", help="Oracle product, e.g. 'Oracle Database 19c'")
    p.add_argument("-o", "--owner", help="internal owner (default for add: $SRT_OWNER)")
    p.add_argument("-e", "--engineer", help="Oracle support engineer")
    p.add_argument("--env", help="environment, e.g. PROD / host / version")
    p.add_argument("-n", "--next", help="next action")
    p.add_argument("-f", "--follow-up", help="follow-up date: YYYY-MM-DD, today, tomorrow, +3d, +1w, fri, none")
    p.add_argument("--tags", help="comma-separated tags")
    p.add_argument("--bugs", help="comma-separated Oracle bug numbers")
    p.add_argument("--opened", help="date the SR was opened (default today)")
    p.add_argument("-m", "--note", help="add a note ('-' reads stdin)")
    if for_update:
        p.add_argument("-t", "--title", help="new title")
        p.add_argument("-r", "--resolution", help="resolution summary")


def _add_filters(p: argparse.ArgumentParser) -> None:
    p.add_argument("--status", action="append", help="filter by status (repeatable)")
    p.add_argument("-s", "--sev", action="append", help="filter by severity (repeatable)")
    p.add_argument("-w", "--waiting", choices=["us", "oracle"], help="who must act next")
    p.add_argument("-p", "--product", help="product contains")
    p.add_argument("-o", "--owner", help="owner contains")
    p.add_argument("-t", "--tag", help="has tag")
    p.add_argument(
        "--sort",
        default="priority",
        choices=["priority", "follow_up", "opened", "updated", "severity", "number"],
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="srt",
        description="Track Oracle Support Service Requests. SRs can be referenced by a "
        "unique suffix of their number (e.g. the last 4 digits).",
    )
    parser.add_argument("--db", help=f"database file (default $SRT_DB or {default_db_path()})")
    parser.add_argument("--no-color", action="store_true", help="disable colored output")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    p = sub.add_parser("add", help="add a new SR")
    p.add_argument("number", help="SR number, e.g. 3-38123456789")
    p.add_argument("title", help="problem summary")
    _add_sr_fields(p, for_update=False)
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("list", aliases=["ls"], help="list SRs (open by default)")
    _add_filters(p)
    g = p.add_mutually_exclusive_group()
    g.add_argument("-a", "--all", action="store_true", help="include closed SRs")
    g.add_argument("-c", "--closed", action="store_true", help="only closed SRs")
    p.add_argument("-d", "--due", action="store_true", help="only follow-ups due today or earlier")
    p.add_argument("-l", "--limit", type=int, help="max rows")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("today", aliases=["agenda"], help="what needs attention now")
    p.add_argument("--stale", type=int, default=7, help="days without activity to count as stale (default 7)")
    p.set_defaults(func=cmd_today)

    p = sub.add_parser("show", aliases=["s"], help="show SR details and history")
    p.add_argument("refs", nargs="+", metavar="SR")
    p.add_argument("--recent", action="store_true", help="only last 10 history entries")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("update", aliases=["up"], help="change SR fields")
    p.add_argument("ref", metavar="SR")
    _add_sr_fields(p, for_update=True)
    p.set_defaults(func=cmd_update)

    p = sub.add_parser("note", aliases=["n"], help="add a note / update to an SR")
    p.add_argument("ref", metavar="SR")
    p.add_argument("text", nargs="+", help="note text ('-' reads stdin)")
    p.add_argument("--status", help="also change status")
    p.add_argument("-f", "--follow-up", help="also set follow-up date")
    p.add_argument("-n", "--next", help="also set next action")
    p.set_defaults(func=cmd_note)

    p = sub.add_parser("st", help="quick status change: srt st 1234 cus")
    p.add_argument("ref", metavar="SR")
    p.add_argument("status")
    p.add_argument("-f", "--follow-up")
    p.add_argument("-n", "--next")
    p.add_argument("-m", "--note")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("close", help="close an SR")
    p.add_argument("ref", metavar="SR")
    p.add_argument("-r", "--resolution", help="resolution summary ('-' reads stdin)")
    p.set_defaults(func=cmd_close)

    p = sub.add_parser("reopen", help="reopen a closed SR")
    p.add_argument("ref", metavar="SR")
    p.add_argument("--status", default="WIP")
    p.add_argument("-m", "--note")
    p.set_defaults(func=cmd_reopen)

    p = sub.add_parser("tag", help="add (or --remove) tags")
    p.add_argument("ref", metavar="SR")
    p.add_argument("tags", nargs="+")
    p.add_argument("--remove", action="store_true")
    p.set_defaults(func=cmd_tag)

    p = sub.add_parser("bug", help="link (or --remove) Oracle bug numbers")
    p.add_argument("ref", metavar="SR")
    p.add_argument("bugs", nargs="+")
    p.add_argument("--remove", action="store_true")
    p.set_defaults(func=cmd_bug)

    p = sub.add_parser("search", aliases=["find"], help="full-text search incl. notes")
    p.add_argument("text")
    p.add_argument("--open-only", action="store_true")
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("stats", help="summary dashboard")
    p.set_defaults(func=cmd_stats)

    p = sub.add_parser("export", help="export SRs to CSV or JSON")
    p.add_argument("-o", "--output", help="output file (default stdout)")
    p.add_argument("--format", choices=["csv", "json"])
    p.add_argument("--open-only", action="store_true")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("import", help="import SRs from CSV")
    p.add_argument("file")
    p.add_argument("--update", action="store_true", help="update SRs that already exist")
    p.set_defaults(func=cmd_import)

    p = sub.add_parser("delete", aliases=["rm"], help="delete an SR and its history")
    p.add_argument("ref", metavar="SR")
    p.add_argument("-y", "--yes", action="store_true")
    p.set_defaults(func=cmd_delete)

    p = sub.add_parser("web", help="run the browser UI locally (needs Flask)")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.set_defaults(func=cmd_web)

    p = sub.add_parser("statuses", help="list status codes")
    p.set_defaults(func=cmd_statuses)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    global _COLOR
    parser = build_parser()
    args = parser.parse_args(argv)
    _COLOR = sys.stdout.isatty() and not args.no_color and "NO_COLOR" not in os.environ
    if not args.command:
        args = parser.parse_args([*(["--db", args.db] if args.db else []), "today"])
    try:
        with Store(args.db or default_db_path()) as store:
            return args.func(store, args)
    except SRError as exc:
        print(f"srt: error: {exc}", file=sys.stderr)
        return 2
    except BrokenPipeError:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
