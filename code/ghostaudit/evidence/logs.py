"""Log evidence.

The observability exports come in six syntaxes (key=value, JSON lines,
syslog, bracketed trace, pipe-delimited and cloud-runtime with escaped
quotes) but all describe the same events.  A tolerant tokenizer turns each
line into ``{ts, service, trace_id, event, fields}``; events are then joined
by ``trace_id`` so the identity used to *start* a lookup is attached to the
resource returned by its *completion*.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from typing import Dict, List, Optional, Set, Tuple

from .. import config as C
from ..identity import token, tokens_from_text
from ..observations import Observation, is_null, make_observation, default_category, resource_prefix
from ..timeutil import parse_ts
from .common import truthy, result_to_state, observation_from_record

_TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z)\s*(.*)$")
_KV_RE = re.compile(r'([A-Za-z_][A-Za-z0-9_]*)=("((?:[^"\\]|\\.)*)"|([^\s|]+))')
_BRACKET_RE = re.compile(r"^\[(\w+)\]\s+\[([\w.-]+)\]\s+(?:\[([\w.-]+)\]\s+)?([a-z_]+):\s*(.*)$")
_SYSLOG_RE = re.compile(r"^<\d+>\d+\s+(\S+)\s+(\S+)\s+(\S+)\s+-\s+-\s+(.*)$")

START_EVENT_KEYS = ("lookup_key", "match_key", "subject_key", "scan_key", "owner_key", "subject_ref",
                    "subject_reference", "correlation_key", "query")

SERVICE_TO_SYSTEM = {
    "support_replica": "support_platform",
    "attachment_preview": "support_platform",
}


def tokenize(line: str) -> Optional[Dict]:
    line = line.rstrip("\n")
    m = _TS_RE.match(line)
    if not m:
        return None
    ts, rest = m.group(1), m.group(2).strip()
    fields: Dict[str, str] = {}
    service = trace = event = level = None

    if rest.startswith("{"):
        try:
            obj = json.loads(rest)
        except ValueError:
            obj = None
        if isinstance(obj, dict):
            fields = {k: ("" if v is None else str(v)) for k, v in obj.items()}
            service = fields.get("service") or fields.get("logger")
            trace = fields.get("trace_id")
            event = fields.get("event")
            level = fields.get("severity") or fields.get("level")
            return {"ts": parse_ts(ts), "service": service, "trace_id": trace, "event": event,
                    "level": level, "fields": fields, "raw": line}

    body = rest
    bm = _BRACKET_RE.match(rest)
    sm = _SYSLOG_RE.match(rest)
    if bm:
        level, service, trace, event, body = bm.groups()
        if trace and not trace.startswith("tr-") and not trace.startswith("rb-"):
            # [DEBUG] [log_router] [collector-9] export_started: ...
            trace = None
    elif sm:
        _host, service, trace, body = sm.groups()
        if trace == "-":
            trace = None
    elif rest.startswith("|"):
        parts = [p.strip() for p in rest.split("|")]
        parts = [p for p in parts if p != ""]
        if len(parts) >= 4:
            level, service, trace, event = parts[0], parts[1], parts[2], parts[3]
            body = " | ".join(parts[4:])
            if not (trace.startswith("tr-") or trace.startswith("rb-")):
                trace = None
    # cloud runtime: message="...\"...\"..."
    if "message=" in body:
        mi = body.index("message=")
        head, msg = body[:mi], body[mi + len("message="):]
        msg = msg.strip()
        if msg.startswith('"') and msg.endswith('"'):
            msg = msg[1:-1]
        msg = msg.replace('\\"', '"')
        body = head + " " + msg
    for km in _KV_RE.finditer(body):
        key = km.group(1)
        val = km.group(3) if km.group(3) is not None else km.group(4)
        fields[key] = val
    service = service or fields.get("service") or fields.get("function") or fields.get("logger")
    trace = trace or fields.get("trace_id") or (fields.get("requestId") if str(fields.get("requestId", "")).startswith(("tr-", "rb-")) else None)
    event = event or fields.get("event")
    level = level or fields.get("level")
    return {"ts": parse_ts(ts), "service": service, "trace_id": trace, "event": event,
            "level": level, "fields": fields, "raw": line}


def _identity_from_fields(fields: Dict[str, str], keys=START_EVENT_KEYS) -> Set[str]:
    out: Set[str] = set()
    for k in keys:
        v = fields.get(k)
        if not is_null(v):
            out |= tokens_from_text(str(v))
    return out


def parse(ctx) -> List[Observation]:
    lines = ctx.path.read_text(encoding="utf-8", errors="replace").splitlines()
    records = [tokenize(l) for l in lines]
    records = [r for r in records if r]
    src = f"log:{ctx.path.name}"

    # trace_id -> identity tokens announced by *_started events (or any line of the trace)
    trace_identity: Dict[str, Set[str]] = {}
    for r in records:
        tid = r.get("trace_id")
        if not tid:
            continue
        ids = _identity_from_fields(r["fields"])
        if ids:
            trace_identity.setdefault(tid, set()).update(ids)

    out: List[Observation] = []
    for r in records:
        obs = _event_to_observation(ctx, r, trace_identity, src)
        if obs:
            out.append(obs)
    return out


def _system_for(service: Optional[str], fields: Dict[str, str], rid: str) -> str:
    sysname = fields.get("system") or SERVICE_TO_SYSTEM.get(service or "", service or "")
    if sysname in {"log_router", "readback_collector", "downstream_delivery_service", "search_index_service"}:
        sysname = fields.get("system") or ""
    if not sysname:
        from ..observations import default_system
        sysname = default_system(rid, "")
    return sysname


def _event_to_observation(ctx, r: Dict, trace_identity: Dict[str, Set[str]], src: str) -> Optional[Observation]:
    f = r["fields"]
    event = (r.get("event") or "").strip()
    rid = f.get("resource_id")
    if is_null(rid):
        return None
    rid = str(rid).strip()
    ts = r.get("ts") or ctx.evidence_time
    service = r.get("service") or ""
    system_id = _system_for(service, f, rid)
    trace_ids = trace_identity.get(r.get("trace_id") or "", set())
    own_links = _identity_from_fields(f)
    links = set(trace_ids) | own_links
    exposed: Set[str] = set()
    names: Set[str] = set()
    flags: Dict[str, object] = {"event": event, "level": r.get("level")}
    seq = ctx.next_seq()
    prefix = resource_prefix(rid)

    def make(category, state):
        return make_observation(ctx.evidence_id, ctx.evidence_time, ts, rid, category, state, system_id=system_id,
                                exposed=exposed, links=links, names=names, flags=flags, source=src,
                                detail=r["raw"][:300], seq=seq)

    # ---- generic operational lookups --------------------------------------
    if event == "record_lookup" or ("record_type" in f and "result" in f):
        rec = {"resource_id": rid, "record_type": f.get("record_type"), "result": f.get("result"),
               "subject_reference": f.get("subject_reference") or f.get("lookup_value"), "system": f.get("system"),
               "recorded_at": None}
        obs = observation_from_record(ctx, "operational", rec, ts, src)
        if obs:
            obs.links |= links
            obs.flags.update(flags)
        return obs

    # ---- account profiles ---------------------------------------------------
    if event == "profile_lookup_completed":
        status = f.get("http_status")
        rows = f.get("rows")
        try:
            code = int(status) if status not in (None, "") else None
        except ValueError:
            code = None
        try:
            nrows = int(rows) if rows not in (None, "") else None
        except ValueError:
            nrows = None
        for k in ("email", "phone", "email_hash", "customer_reference", "full_name"):
            v = f.get(k)
            if not is_null(v):
                exposed |= tokens_from_text(str(v)) if k != "full_name" else set()
        absent = (code == 404) or (nrows == 0 and code != 200)
        flags.update({"http_status": code, "rows": nrows})
        return make(C.CAT_USER_PROFILE, C.ST_ABSENT if absent else C.ST_PRESENT)

    # ---- search index ------------------------------------------------------
    if event == "index_query_completed":
        result = (f.get("result") or "").lower()
        hits = f.get("total_hits")
        try:
            nh = int(hits) if hits not in (None, "") else None
        except ValueError:
            nh = None
        present = result == "hit" or (nh is not None and nh > 0)
        for k in ("email", "phone", "email_hash", "customer_reference", "display_name"):
            v = f.get(k)
            if not is_null(v):
                exposed |= tokens_from_text(str(v))
        if present:
            exposed |= {t for t in links if t.startswith("email:")}
        return make(C.CAT_SEARCH_INDEX_ENTRY, C.ST_PRESENT if present else C.ST_ABSENT)

    # ---- support ------------------------------------------------------------
    if event == "message_pii_scan":
        exposed |= tokens_from_text(f.get("matched_value") or "")
        return make(C.CAT_SUPPORT_MESSAGE, C.ST_EXPOSED)
    if event == "attachment_pii_scan":
        exposed |= tokens_from_text(f.get("matched_value") or "")
        flags["file"] = f.get("file")
        return make(C.CAT_SUPPORT_ATTACHMENT, C.ST_PRESENT)
    if event == "message_read":
        text = f.get("message_text") or ""
        exposed |= {t for t in tokens_from_text(text) if not t.startswith("user:")}
        return make(C.CAT_SUPPORT_MESSAGE, C.ST_EXPOSED if exposed else C.ST_REDACTED)
    if event == "support_ticket_read_completed":
        for k in ("requester_email", "requester_phone"):
            v = f.get(k)
            if not is_null(v):
                exposed |= tokens_from_text(str(v))
        if not is_null(f.get("requester_name")):
            names.add(token("name", f.get("requester_name").lower()))
        return make(C.CAT_SUPPORT_MESSAGE, C.ST_EXPOSED if (exposed or names) else C.ST_REDACTED)
    if event == "contact_query_completed":
        rows = f.get("rows")
        try:
            nrows = int(rows) if rows not in (None, "") else 1
        except ValueError:
            nrows = 1
        exposed |= {t for t in links if t.startswith("email:")}
        flags["record_kind"] = "legacy_contact_index"
        return make(C.CAT_SECONDARY_IDENTIFIER, C.ST_PRESENT if nrows > 0 else C.ST_ABSENT)

    # ---- analytics ----------------------------------------------------------
    if event in {"analytics_identity_scan"}:
        exposed |= tokens_from_text(f.get("matched_value") or "")
        return make(C.CAT_ANALYTICS_PROFILE, C.ST_EXPOSED)
    if event in {"analytics_profile_read", "analytics_profile_read_completed"}:
        for k in ("email", "phone", "email_hash", "customer_reference", "user_id"):
            v = f.get(k)
            if not is_null(v):
                exposed |= tokens_from_text(str(v))
        di = f.get("direct_identifiers")
        try:
            ndi = int(di) if di not in (None, "") else None
        except ValueError:
            ndi = None
        state = C.ST_EXPOSED if (exposed or (ndi or 0) > 0) else C.ST_ANONYMIZED
        return make(C.CAT_ANALYTICS_PROFILE, state)
    if event == "contact_lookup_completed":
        # marketing/contact record keyed by a (historical) e-mail hash
        exposed |= {t for t in links if t.startswith(("hash:", "email:", "phone:"))}
        flags["email_opt_in"] = truthy(f.get("email_opt_in"))
        return make(C.CAT_SECONDARY_IDENTIFIER, C.ST_PRESENT)

    # ---- security audit ---------------------------------------------------------
    if event == "audit_record_read_completed":
        flags["retention_until"] = f.get("retention_until")
        for k in ("subject_reference", "correlation_key"):
            v = f.get(k)
            if not is_null(v):
                links |= tokens_from_text(str(v))
        rows = f.get("rows")
        try:
            nrows = int(rows) if rows not in (None, "") else 1
        except ValueError:
            nrows = 1
        return make(C.CAT_SECURITY_AUDIT_LOG, C.ST_RETAINED if nrows > 0 else C.ST_ABSENT)

    # ---- legal holds -----------------------------------------------------------
    if event == "retained_order_loaded":
        return make(C.CAT_LEGAL_HOLD_RECORD, C.ST_RETAINED)

    # ---- data flows ----------------------------------------------------------------
    if event in {"connector_heartbeat", "connector_config_loaded", "connector_config_read_completed"}:
        en = truthy(f.get("enabled"))
        if en is None:
            en = truthy(f.get("scheduler_registered"))
        for k in ("customer_email", "customer_reference"):
            v = f.get(k)
            if not is_null(v):
                links |= tokens_from_text(str(v))
        flags["enabled"] = en
        return make(C.CAT_DATA_FLOW, C.ST_ACTIVE if en else C.ST_DISABLED)

    # ---- backups ---------------------------------------------------------------------
    if event == "restore_candidate_evaluated":
        eligible = truthy(f.get("eligible"))
        blocked = truthy(f.get("deletion_block"))
        active = bool(eligible) and not bool(blocked)
        return make(C.CAT_BACKUP_ARCHIVE, C.ST_ACTIVE if active else C.ST_BLOCKED)
    if event == "restore_authorization_completed":
        decision = (f.get("decision") or "").lower()
        return make(C.CAT_BACKUP_ARCHIVE, C.ST_BLOCKED if decision in {"deny", "denied", "blocked"} else C.ST_ACTIVE)
    if event == "restore_policy_read_completed":
        ra = truthy(f.get("restore_allowed"))
        for k in ("customer_email", "customer_reference"):
            v = f.get(k)
            if not is_null(v):
                links |= tokens_from_text(str(v))
        flags["archive_present"] = truthy(f.get("archive_present"))
        return make(C.CAT_BACKUP_ARCHIVE, C.ST_ACTIVE if ra else C.ST_BLOCKED)

    # ---- exports -----------------------------------------------------------------------
    if event in {"export_download_check_completed", "export_metadata_read_completed"}:
        av = truthy(f.get("available"))
        for k in ("owner_email", "owner_phone"):
            v = f.get(k)
            if not is_null(v):
                exposed |= tokens_from_text(str(v))
        exposed |= {t for t in links if t.startswith("email:")}
        flags["download_available"] = av
        return make(C.CAT_USER_EXPORT, C.ST_PRESENT)

    return None
