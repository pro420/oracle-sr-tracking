"""CSV / JSON import and export."""

from __future__ import annotations

import csv
import json
from dataclasses import asdict
from typing import IO

from .store import SR, SRError, Store, normalize_number

CSV_FIELDS = [
    "number",
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
    "status_since",
    "updated_at",
    "closed_at",
]

# Header names commonly seen in spreadsheets / MOS exports -> our field names.
HEADER_ALIASES = {
    "sr": "number",
    "sr number": "number",
    "sr #": "number",
    "sr#": "number",
    "service request": "number",
    "problem summary": "title",
    "summary": "title",
    "abstract": "title",
    "sev": "severity",
    "priority": "severity",
    "engineer": "oracle_engineer",
    "support engineer": "oracle_engineer",
    "contact": "owner",
    "primary contact": "owner",
    "opened": "opened_at",
    "open date": "opened_at",
    "date opened": "opened_at",
    "follow up": "follow_up",
    "followup": "follow_up",
    "next action": "next_action",
    "bug": "bugs",
}


def export_csv(srs: list[SR], out: IO[str]) -> None:
    writer = csv.DictWriter(out, fieldnames=CSV_FIELDS, extrasaction="ignore")
    writer.writeheader()
    for sr in srs:
        writer.writerow({k: ("" if v is None else v) for k, v in asdict(sr).items()})


def export_json(store: Store, srs: list[SR], out: IO[str], with_events: bool = True) -> None:
    data = []
    for sr in srs:
        item = asdict(sr)
        if with_events:
            item["events"] = [
                {"at": e.at, "kind": e.kind, "text": e.text} for e in store.events(sr.number)
            ]
        data.append(item)
    json.dump(data, out, indent=2)
    out.write("\n")


def _canon(header: str) -> str:
    h = header.strip().lower().replace("_", " ")
    if h in HEADER_ALIASES:
        return HEADER_ALIASES[h]
    return h.replace(" ", "_")


def import_csv(store: Store, src: IO[str], update_existing: bool = False) -> tuple[int, int, list[str]]:
    """Import SRs from CSV. Returns (added, updated, errors)."""
    reader = csv.DictReader(src)
    if not reader.fieldnames:
        raise SRError("CSV file has no header row")
    mapping = {h: _canon(h) for h in reader.fieldnames}
    if "number" not in mapping.values():
        raise SRError("CSV needs an SR number column (e.g. 'number' or 'SR Number')")
    added = updated = 0
    errors: list[str] = []
    editable = set(CSV_FIELDS) - {"number", "status_since", "updated_at", "closed_at"}
    for lineno, raw in enumerate(reader, start=2):
        row = {mapping[k]: (v or "").strip() for k, v in raw.items() if k in mapping}
        fields = {k: v for k, v in row.items() if k in editable and v != ""}
        try:
            number = normalize_number(row.get("number", ""))
            if store.exists(number):
                if update_existing:
                    _, changes = store.update(number, **fields)
                    if changes:
                        updated += 1
                continue
            title = fields.pop("title", "") or "(no title)"
            store.add(number, title, **fields)
            added += 1
        except SRError as exc:
            errors.append(f"line {lineno}: {exc}")
    return added, updated, errors
