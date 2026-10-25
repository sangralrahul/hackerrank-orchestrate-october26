"""Timestamp helpers. All timestamps in the dataset are UTC ISO-8601 strings."""

from __future__ import annotations

import datetime as dt
import re
from typing import Optional

_ISO_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2}))?(?:\.\d+)?(Z|[+-]\d{2}:?\d{2})?")

_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}
_HUMAN_RE = re.compile(r"(\d{1,2})\s+([A-Za-z]{3})[a-z]*\s+(\d{4})(?:\s+(\d{1,2}):(\d{2}))?")


def parse_ts(value: Optional[str]) -> Optional[dt.datetime]:
    """Parse an ISO timestamp (or a human form such as ``26 Apr 2026 02:00``)."""
    if value is None:
        return None
    value = str(value).strip()
    if not value or value.lower() in {"null", "none", "-"}:
        return None
    m = _ISO_RE.search(value)
    if m:
        y, mo, d, h, mi, s, tz = m.groups()
        base = dt.datetime(int(y), int(mo), int(d), int(h), int(mi), int(s or 0))
        if tz and tz != "Z":
            sign = 1 if tz[0] == "+" else -1
            digits = tz[1:].replace(":", "")
            off = dt.timedelta(hours=int(digits[:2]), minutes=int(digits[2:4]))
            base = base - sign * off
        return base
    m = _HUMAN_RE.search(value)
    if m:
        d, mon, y, h, mi = m.groups()
        mo = _MONTHS.get(mon.lower()[:3])
        if mo:
            return dt.datetime(int(y), mo, int(d), int(h or 0), int(mi or 0))
    return None


def fmt_ts(value: Optional[dt.datetime]) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ") if value else ""


def minutes_between(a: dt.datetime, b: dt.datetime) -> float:
    return (a - b).total_seconds() / 60.0
