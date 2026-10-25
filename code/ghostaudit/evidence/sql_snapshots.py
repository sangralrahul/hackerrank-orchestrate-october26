"""SQLite-compatible SQL snapshot exports.

Two families exist in the corpus:

* replica dumps: tables ``customer_data_primary`` / ``customer_data_<replica>``
  with (resource_id, origin_system, collection_name, record_json, updated_at);
* typed inventories: one table per collection with explicit columns, plus a
  ``snapshot_scope`` table with the capture time per origin system.

Both are executed in an in-memory SQLite database and read back generically.
"""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Dict, List, Optional

from .. import config as C
from ..observations import Observation, make_observation
from ..timeutil import parse_ts
from .common import observation_from_record

_CAPTURED_RE = re.compile(r"--\s*Captured at:\s*(\S+)")
_TOPOLOGY_RE = re.compile(r"--\s*Database topology:\s*(\S+)")

# Table names that represent a complete customer-profile store: a known account
# that is missing from them is evidence of absence (used as a weak observation).
_FULL_PROFILE_TABLES = {"customer_profiles_primary", "customer_profiles_failover_replica",
                        "customer_profiles_read_replica", "customer_profiles"}


def parse(ctx) -> List[Observation]:
    text = ctx.path.read_text(encoding="utf-8", errors="replace")
    captured = None
    m = _CAPTURED_RE.search(text)
    if m:
        captured = parse_ts(m.group(1))
    topology = ""
    m = _TOPOLOGY_RE.search(text)
    if m:
        topology = m.group(1)
    captured = captured or ctx.evidence_time
    src = f"sql:{ctx.path.name}"

    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript(text)
    except sqlite3.Error:
        # fall back to statement-by-statement execution
        for stmt in text.split(";\n"):
            try:
                conn.execute(stmt)
            except sqlite3.Error:
                continue
    cur = conn.cursor()
    tables = [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    scope_times: Dict[str, object] = {}
    if "snapshot_scope" in tables:
        for origin, ts in cur.execute("SELECT origin_system, captured_at FROM snapshot_scope"):
            scope_times[origin] = parse_ts(ts) or captured

    out: List[Observation] = []
    for table in tables:
        if table == "snapshot_scope":
            continue
        cols = [r[1] for r in cur.execute(f"PRAGMA table_info('{table}')")]
        rows = cur.execute(f'SELECT * FROM "{table}"').fetchall()
        present_ids: List[str] = []
        for row in rows:
            rec = dict(zip(cols, row))
            if "record_json" in rec:
                try:
                    payload = json.loads(rec["record_json"])
                except (TypeError, ValueError):
                    payload = {}
                payload = dict(payload)
                payload.setdefault("resource_id", rec.get("resource_id"))
                obs = observation_from_record(ctx, rec.get("collection_name", ""), payload, captured, src,
                                              system_hint=rec.get("origin_system", ""))
            else:
                payload = rec
                t = scope_times.get(_origin_for_table(table), captured)
                obs = observation_from_record(ctx, table, payload, t, src)
            if obs:
                obs.flags["table"] = table
                obs.flags["topology"] = topology
                out.append(obs)
                present_ids.append(obs.resource_id)
        is_replica_dump = "record_json" in cols
        if table in _FULL_PROFILE_TABLES or is_replica_dump:
            if is_replica_dump:
                present_ids = [i for i in present_ids if i.startswith("account_")]
            # scope marker: lets the linker infer absence of a known account id
            out.append(make_observation(ctx.evidence_id, ctx.evidence_time, scope_times.get("account_service", captured),
                                        "*", C.CAT_USER_PROFILE, "SCOPE", system_id="account_service",
                                        flags={"present_ids": sorted(set(present_ids)), "table": table, "weak": True},
                                        source=src, seq=ctx.next_seq()))
    conn.close()
    return out


def _origin_for_table(table: str) -> str:
    t = table.lower()
    if "profile" in t and "analytics" not in t:
        return "account_service"
    if "search" in t:
        return "search_service"
    if "analytics" in t:
        return "analytics_platform"
    if "support" in t:
        return "support_platform"
    if "export" in t:
        return "export_service"
    if "connector" in t:
        return "integration_service"
    if "backup" in t or "restore" in t:
        return "backup_service"
    if "audit" in t or "security" in t:
        return "security_service"
    if "legal" in t:
        return "legal_service"
    if "tombstone" in t or "deleted" in t:
        return "identity_service"
    return ""
