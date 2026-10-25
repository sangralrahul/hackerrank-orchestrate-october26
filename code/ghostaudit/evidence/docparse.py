"""Shared logic for unstructured documents (PDF text and screenshot OCR).

Business documents never state a deletion verdict.  We extract the facts
they contain - which resources are shown, which identifiers they carry and
what operational state the console displays - and turn them into ordinary
observations.  An optional model-backed extractor can supply structured
records; the regex path below is the deterministic fallback.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Dict, Iterable, List, Optional, Set, Tuple

from .. import config as C
from ..identity import token, tokens_from_text, norm_name, EMAIL_RE
from ..observations import Observation, make_observation, resource_prefix, default_category, default_system
from ..timeutil import parse_ts
from .common import observation_from_record

RESOURCE_RE = re.compile(
    r"\b(account|search|analytics|support|export|data_flow|backup|audit_record|tombstone|legal_hold|marketing|object|cache|email_delivery)_(\d{1,6})\b")
ORDER_RE = re.compile(r"\bORD-2026(\d{4})\b")
RETAIN_RE = re.compile(r"[Rr]etain(?:ed)?\s+until\s+(\d{1,2}\s+[A-Za-z]{3}\s+\d{4})")
CAPTURED_RE = re.compile(r"(?:captured|issued|generated|recorded|Executed)\s+(\d{1,2}\s+[A-Za-z]{3}\s+\d{4}(?:\s+\d{1,2}[: ]\s*\d{2})?)", re.I)

STAFF_DOMAINS = ("@northstar.shop", "@company.com")


def console_kind(text: str) -> str:
    t = text.lower()
    checks = [
        ("customer operations", "account_console"),
        ("search console", "search_console"),
        ("bridgeops", "connector_console"),
        ("pulse analytics", "analytics_console"),
        ("download center", "export_console"),
        ("helpline", "support_console"),
        ("vault recovery", "backup_console"),
        ("sentinel", "security_console"),
        ("reference registry", "tombstone_console"),
        ("object storage", "object_storage"),
        ("audience operations", "marketing_console"),
        ("customer statement", "statement"),
        ("address correction", "support_attachment"),
        ("tax records register", "legal_register"),
        ("compliance journal", "security_extract"),
    ]
    for needle, kind in checks:
        if needle in t:
            return kind
    return "unknown"


def _line_tokens(line: str) -> Set[str]:
    toks = tokens_from_text(line)
    return {t for t in toks if not (t.startswith("email:") and t.split(":", 1)[1].endswith(STAFF_DOMAINS))}


def _row_has_foreign_owner(line: str) -> bool:
    """A list row that names a staff / company mailbox belongs to someone else."""
    return any(e.lower().endswith(STAFF_DOMAINS) for e in EMAIL_RE.findall(line))


def _passes(text: str) -> List[str]:
    parts = re.split(r"^### psm \S+\s*$", text, flags=re.M)
    return [p for p in parts if p.strip()] or [text]


def observations_from_text(ctx, text: str, aliases: Dict[str, Tuple[str, str]], src: str) -> List[Observation]:
    """Deterministic extraction from document text."""
    kind = console_kind(text)
    lines = [l for l in text.splitlines() if l.strip()]
    doc_tokens: Set[str] = set()
    for l in lines:
        doc_tokens |= _line_tokens(l)
    observed = ctx.evidence_time
    m = CAPTURED_RE.search(text)
    if m:
        observed = parse_ts(m.group(1)) or observed
    out: List[Observation] = []

    # resources explicitly named in the document. OCR text holds several passes; an id must be
    # read identically by a majority of passes, which removes single-pass misreads.
    passes = _passes(text)
    votes: Dict[str, int] = {}
    for p in passes:
        for rid in {f"{m.group(1)}_{m.group(2)}" for m in RESOURCE_RE.finditer(p)}:
            votes[rid] = votes.get(rid, 0) + 1
    need = 1 if len(passes) < 3 else 2
    found: Dict[str, Set[str]] = {}
    foreign: Set[str] = set()
    for l in lines:
        for rm in RESOURCE_RE.finditer(l):
            rid = f"{rm.group(1)}_{rm.group(2)}"
            if votes.get(rid, 0) < need:
                continue
            found.setdefault(rid, set()).update(_line_tokens(l))
            if _row_has_foreign_owner(l):
                foreign.add(rid)
    # aliases announced by the data catalog (statement ids, file names, user keys)
    alias_hits: Dict[str, Set[str]] = {}
    for alias, (rid, _system) in aliases.items():
        if alias and alias.lower() in text.lower():
            toks: Set[str] = set()
            for l in lines:
                if alias.lower() in l.lower():
                    toks |= _line_tokens(l)
            alias_hits.setdefault(rid, set()).update(toks)
    for rid, toks in alias_hits.items():
        found.setdefault(rid, set()).update(toks)
    # Note: the finance register lists order references only; order numbers are not
    # legal_hold resource ids, so no resource is inferred from them (decoy guard).

    single_record_kinds = {"account_console", "analytics_console", "support_console", "marketing_console",
                           "tombstone_console", "statement", "support_attachment", "security_extract"}
    for rid, toks in found.items():
        prefix = resource_prefix(rid)
        category = default_category(rid, "")
        system_id = default_system(rid, "")
        if rid in {r for r, _ in aliases.values()}:
            for alias, (arid, asys) in aliases.items():
                if arid == rid and asys:
                    system_id = asys
        if rid in foreign and not toks:
            continue  # row owned by another mailbox: not linked to anyone we audit
        if kind in single_record_kinds:
            toks = set(toks) | doc_tokens
        exposed = {t for t in toks if not t.startswith("user:")}
        links = set(toks)
        names: Set[str] = set()
        state = C.ST_PRESENT
        flags: Dict[str, object] = {"document_kind": kind}
        if kind == "security_console" and prefix == "audit_record":
            # only the record opened in the detail panel is an audited record of interest
            if not re.search(rf"{rid}\s*[·\-]\s*Raw event", text):
                continue
        if prefix in {"account"}:
            category, state = C.CAT_USER_PROFILE, C.ST_PRESENT
        elif prefix == "search":
            category, state = C.CAT_SEARCH_INDEX_ENTRY, C.ST_PRESENT
        elif prefix == "analytics":
            category = C.CAT_ANALYTICS_PROFILE
            state = C.ST_EXPOSED if exposed else C.ST_ANONYMIZED
        elif prefix == "support":
            category = C.CAT_SUPPORT_ATTACHMENT if kind in {"support_attachment", "object_storage"} else C.CAT_SUPPORT_MESSAGE
            flags["category_guess"] = kind not in {"support_attachment", "object_storage", "support_console"}
            state = C.ST_PRESENT if category == C.CAT_SUPPORT_ATTACHMENT else (C.ST_EXPOSED if exposed else C.ST_REDACTED)
        elif prefix == "object":
            category, state = C.CAT_SUPPORT_ATTACHMENT, C.ST_PRESENT
        elif prefix == "export":
            category, state = C.CAT_USER_EXPORT, C.ST_PRESENT
            row = next((l for l in lines if rid in l), "")
            flags["download_available"] = ("ready" in row.lower()) or ("expired" not in row.lower())
        elif prefix == "data_flow":
            category = C.CAT_DATA_FLOW
            row = next((l for l in lines if rid in l), "")
            low = row.lower()
            disabled = any(w in low for w in ("disabled", "stopped", "paused", "off"))
            state = C.ST_DISABLED if disabled else C.ST_ACTIVE
            links |= exposed
        elif prefix == "backup":
            category = C.CAT_BACKUP_ARCHIVE
            low = text.lower()
            blocked = ("restore blocked" in low) or ("restore_allowed: false" in low) or ("deny" in low and rid in low)
            state = C.ST_BLOCKED if blocked else C.ST_ACTIVE
            links |= exposed
        elif prefix == "audit_record":
            category, state = C.CAT_SECURITY_AUDIT_LOG, C.ST_RETAINED
            rm = RETAIN_RE.search(text)
            if rm:
                flags["retention_until"] = rm.group(1)
            links |= exposed
            exposed = set()
        elif prefix == "legal_hold":
            category, state = C.CAT_LEGAL_HOLD_RECORD, C.ST_RETAINED
            links |= exposed
            exposed = set()
        elif prefix == "tombstone":
            category = C.CAT_ACCOUNT_TOMBSTONE
            pii = {t for t in exposed if t.startswith(("email:", "phone:", "hash:"))}
            state = C.ST_EXPOSED if pii else C.ST_REDACTED
        elif prefix == "marketing":
            category, state = C.CAT_MARKETING_PROFILE, C.ST_PRESENT
        obs = make_observation(ctx.evidence_id, ctx.evidence_time, observed, rid, category, state, system_id=system_id,
                               exposed=exposed, links=links, names=names, flags=flags, source=src,
                               detail=f"{kind}", seq=ctx.next_seq())
        out.append(obs)
    return out


def observations_from_structured(ctx, records: Iterable[Dict], src: str) -> List[Observation]:
    """Records produced by a model-backed extractor (operational record shape)."""
    out: List[Observation] = []
    for rec in records or []:
        if not isinstance(rec, dict):
            continue
        obs = observation_from_record(ctx, rec.get("collection", "") or rec.get("record_type", ""), rec,
                                      parse_ts(rec.get("recorded_at")) or ctx.evidence_time, src)
        if obs:
            out.append(obs)
    return out
