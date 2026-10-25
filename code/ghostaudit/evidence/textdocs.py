"""Plain-text evidence (shift handover notes)."""

from __future__ import annotations

import re
from typing import List

from ..observations import Observation
from ..timeutil import parse_ts
from .common import observation_from_record

_ROW_RE = re.compile(
    r"^\s*-\s*(\S+)\s*\|\s*([\w-]+)\s*\|\s*([\w-]+)\s*\|\s*([A-Z_]+)\s*\|\s*([A-Z_]+)\s*\|\s*(.*?)\s*$")
_SNAPSHOT_RE = re.compile(r"Snapshot:\s*(\S+)")


def parse(ctx) -> List[Observation]:
    text = ctx.path.read_text(encoding="utf-8", errors="replace")
    captured = ctx.evidence_time
    m = _SNAPSHOT_RE.search(text)
    if m:
        captured = parse_ts(m.group(1)) or captured
    out: List[Observation] = []
    src = f"txt:{ctx.path.name}"
    for line in text.splitlines():
        m = _ROW_RE.match(line)
        if not m:
            continue
        ts, system, rid, record_type, result, lookup = m.groups()
        rec = {"recorded_at": ts, "system": system, "resource_id": rid, "record_type": record_type,
               "result": result, "lookup_value": lookup if lookup and "no direct identifier" not in lookup else None}
        obs = observation_from_record(ctx, "handover", rec, parse_ts(ts) or captured, src)
        if obs:
            out.append(obs)
    return out
