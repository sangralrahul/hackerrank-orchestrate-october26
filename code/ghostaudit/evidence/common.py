"""Shared mapping from structured records (JSON / SQL rows) to observations.

The dataset uses a handful of record shapes.  We recognise them by their
field names rather than by file name so the same code serves snapshot
bundles, service exports, inventory tables, replica dumps and ad-hoc reports.
"""

from __future__ import annotations

import datetime as dt
import json
from typing import Dict, Iterable, List, Optional, Set

from .. import config as C
from ..identity import token, tokens_from_text, norm_email, norm_name
from ..observations import (Observation, identity_tokens, is_null, make_observation,
                            resource_prefix, default_category)
from ..timeutil import parse_ts

IDENTITY_EXPOSURE_KEYS = [
    "email", "email_hash", "phone", "full_name", "display_name", "name",
    "requester_email", "requester_name", "requester_phone", "owner_email", "owner_phone",
    "user_id", "customer_email", "uploaded_by", "matched_value",
]
IDENTITY_LINK_KEYS = [
    "customer_reference", "subject_reference", "subject_selector", "lookup_value", "lookup_key",
    "subject_key", "scan_key", "owner_key", "match_key", "correlation_key", "subject_ref", "query",
]

_TRUE = {"1", "true", "yes", "y", "t", "enabled", "allowed"}
_FALSE = {"0", "false", "no", "n", "f", "disabled", "stopped", "blocked"}


def truthy(value) -> Optional[bool]:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    s = str(value).strip().lower()
    if s in _TRUE:
        return True
    if s in _FALSE:
        return False
    return None


def result_to_state(record_type: str, result: str) -> (str, str):
    """Map operational ``record_type`` + ``result`` labels to (category, state)."""
    cat = C.RECORD_TYPE_TO_CATEGORY.get((record_type or "").upper(), "")
    r = (result or "").upper()
    state = {
        "FOUND": C.ST_PRESENT,
        "PRESENT": C.ST_PRESENT,
        "NOT_FOUND": C.ST_ABSENT,
        "ABSENT": C.ST_ABSENT,
        "MISSING": C.ST_ABSENT,
        "IDENTIFIABLE": C.ST_EXPOSED,
        "EXPOSED": C.ST_EXPOSED,
        "ANONYMIZED": C.ST_ANONYMIZED,
        "REDACTED": C.ST_REDACTED,
        "ACTIVE": C.ST_ACTIVE,
        "ENABLED": C.ST_ACTIVE,
        "RESTORABLE": C.ST_ACTIVE,
        "DISABLED": C.ST_DISABLED,
        "BLOCKED": C.ST_BLOCKED,
        "RETAINED": C.ST_RETAINED,
    }.get(r, C.ST_PRESENT if r else C.ST_PRESENT)
    if cat in {C.CAT_SECURITY_AUDIT_LOG, C.CAT_LEGAL_HOLD_RECORD} and state == C.ST_PRESENT:
        state = C.ST_RETAINED
    return cat, state


def exposure_state(record: Dict, keys: Iterable[str]) -> bool:
    """True when any of the given identity keys carries a non-null value."""
    for k in keys:
        if k in record and not is_null(record.get(k)):
            return True
    return False


def observation_from_record(ctx, collection: str, record: Dict, observed_at: Optional[dt.datetime],
                            source: str, extra_links: Optional[Set[str]] = None,
                            system_hint: str = "") -> Optional[Observation]:
    """Convert one structured record into an observation (or None if not a resource)."""
    if not isinstance(record, dict):
        return None
    rid = record.get("resource_id")
    if is_null(rid):
        return None
    rid = str(rid).strip()
    rec = dict(record)
    # nested search document
    doc = rec.get("document")
    if isinstance(doc, dict):
        for k, v in doc.items():
            rec.setdefault(k, v)
    collection = (collection or "").lower()
    prefix = resource_prefix(rid)
    category = default_category(rid, "")
    state = C.ST_PRESENT
    flags: Dict[str, object] = {}
    exposed, links, names = identity_tokens(rec, IDENTITY_EXPOSURE_KEYS, IDENTITY_LINK_KEYS)
    if extra_links:
        links |= set(extra_links)
    system_id = system_hint or rec.get("origin_system") or rec.get("system") or ""

    # ---- operational records (system / record_type / result) -------------
    if "record_type" in rec and "result" in rec:
        cat, state = result_to_state(rec.get("record_type"), rec.get("result"))
        category = cat or category
        lookup = rec.get("lookup_value") or rec.get("subject_reference") or rec.get("lookup_key")
        if not is_null(lookup):
            t = tokens_from_text(str(lookup))
            links |= t
            if state in {C.ST_PRESENT, C.ST_EXPOSED} and category not in {C.CAT_SECURITY_AUDIT_LOG, C.CAT_LEGAL_HOLD_RECORD, C.CAT_BACKUP_ARCHIVE, C.CAT_DATA_FLOW}:
                exposed |= {x for x in t if not x.startswith("user:")}
            if state == C.ST_ACTIVE:
                exposed |= {x for x in t if x.startswith("email:")}
        obs_time = parse_ts(rec.get("recorded_at")) or observed_at
        return make_observation(ctx.evidence_id, ctx.evidence_time, obs_time, rid, category, state,
                                system_id=rec.get("system") or system_id, exposed=exposed, links=links,
                                names=names, flags=flags, source=source, detail=json.dumps(record, ensure_ascii=False)[:300],
                                seq=ctx.next_seq())

    # ---- account profile lookups (service export) --------------------------
    if "status_code" in rec or "profile" in rec:
        status = rec.get("status_code")
        profile = rec.get("profile")
        if isinstance(profile, dict):
            e2, l2, n2 = identity_tokens(profile, IDENTITY_EXPOSURE_KEYS, IDENTITY_LINK_KEYS)
            exposed |= e2 | l2
            names |= n2
        try:
            code = int(status) if status is not None else None
        except (TypeError, ValueError):
            code = None
        state = C.ST_ABSENT if (code == 404 or (profile is None and code != 200)) else C.ST_PRESENT
        flags["status_code"] = code
        category = category or C.CAT_USER_PROFILE
        return make_observation(ctx.evidence_id, ctx.evidence_time, parse_ts(rec.get("observed_at")) or observed_at,
                                rid, category, state, system_id=system_id or "account_service", exposed=exposed,
                                links=links, names=names, flags=flags, source=source, seq=ctx.next_seq())

    # ---- search index queries (service export) -----------------------------
    if "hits" in rec:
        hits = rec.get("hits") or {}
        docs = hits.get("documents") or []
        total = hits.get("total")
        try:
            total = int(total) if total is not None else len(docs)
        except (TypeError, ValueError):
            total = len(docs)
        for d in docs:
            if isinstance(d, dict):
                e2, l2, n2 = identity_tokens(d, IDENTITY_EXPOSURE_KEYS, IDENTITY_LINK_KEYS)
                exposed |= e2 | l2
                names |= n2
        state = C.ST_PRESENT if total > 0 else C.ST_ABSENT
        return make_observation(ctx.evidence_id, ctx.evidence_time, parse_ts(rec.get("observed_at")) or observed_at,
                                rid, C.CAT_SEARCH_INDEX_ENTRY, state, system_id=system_id or "search_service",
                                exposed=exposed, links=links, names=names, flags=flags, source=source, seq=ctx.next_seq())

    # ---- category specific handling ------------------------------------------
    if prefix == "account" or "account_status" in rec and prefix != "tombstone":
        category = C.CAT_USER_PROFILE
        flags["account_status"] = rec.get("account_status")
        state = C.ST_PRESENT
        # the tombstone collection also carries account_status; handled below
    if prefix == "tombstone" or "deleted_customer" in collection:
        category = C.CAT_ACCOUNT_TOMBSTONE
        state = C.ST_EXPOSED if (exposed or names) else C.ST_REDACTED
    elif prefix == "analytics" or "analytics" in collection:
        category = C.CAT_ANALYTICS_PROFILE
        # any identifier (including the stable reference) keeps the record attributable
        exposed |= {t for t in links if t.startswith("user:")}
        state = C.ST_EXPOSED if (exposed or names) else C.ST_ANONYMIZED
    elif prefix == "support" or "support" in collection or "ticket" in collection:
        if "file_name" in rec or "attachment" in collection or "storage_class" in rec:
            category = C.CAT_SUPPORT_ATTACHMENT
            state = C.ST_PRESENT
        else:
            category = C.CAT_SUPPORT_MESSAGE
            text = rec.get("message_text") or rec.get("message_excerpt") or ""
            text_tokens = tokens_from_text(str(text)) if not is_null(text) else set()
            text_tokens = {t for t in text_tokens if not t.startswith("user:")}
            exposed |= text_tokens
            state = C.ST_EXPOSED if (exposed or names) else C.ST_REDACTED
    elif prefix == "data_flow" or "connector" in collection:
        category = C.CAT_DATA_FLOW
        en = truthy(rec.get("enabled"))
        if en is None:
            mode = str(rec.get("mode") or rec.get("last_delivery_status") or "").lower()
            en = ("continuous" in mode or "delivered" in mode or "active" in mode) if mode else True
        state = C.ST_ACTIVE if en else C.ST_DISABLED
        flags["enabled"] = en
        links |= exposed  # selectors/emails on a connector link it to the subject
    elif prefix == "backup" or "backup" in collection or "restore" in collection:
        category = C.CAT_BACKUP_ARCHIVE
        ra = truthy(rec.get("restore_allowed"))
        if ra is None:
            ra = truthy(rec.get("restorable"))
        state = C.ST_ACTIVE if ra else C.ST_BLOCKED
        flags["restore_allowed"] = ra
        links |= exposed
    elif prefix == "audit_record" or "audit" in collection:
        category = C.CAT_SECURITY_AUDIT_LOG
        state = C.ST_RETAINED
        flags["retention_until"] = rec.get("retention_until")
        links |= exposed
    elif prefix == "legal_hold" or "retained_order" in collection or "legal" in collection:
        category = C.CAT_LEGAL_HOLD_RECORD
        state = C.ST_RETAINED
        flags["retained_until"] = rec.get("retained_until")
        links |= exposed
    elif prefix == "export" or "export" in collection:
        category = C.CAT_USER_EXPORT
        state = C.ST_PRESENT
        da = truthy(rec.get("download_available"))
        if da is None:
            da = truthy(rec.get("available"))
        flags["download_available"] = da
    elif prefix == "search" or "search" in collection:
        category = C.CAT_SEARCH_INDEX_ENTRY
        state = C.ST_PRESENT
    elif prefix == "marketing":
        category = C.CAT_MARKETING_PROFILE
        state = C.ST_PRESENT
    elif category == C.CAT_USER_PROFILE:
        state = C.ST_PRESENT
    obs_time = parse_ts(rec.get("observed_at")) or observed_at
    return make_observation(ctx.evidence_id, ctx.evidence_time, obs_time,
                            rid, category, state, system_id=system_id, exposed=exposed, links=links, names=names,
                            flags=flags, source=source, detail=json.dumps(record, ensure_ascii=False)[:300],
                            seq=ctx.next_seq())
