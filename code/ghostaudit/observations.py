"""Normalised evidence model.

Every evidence parser converts what it reads into ``Observation`` objects:
one observation = "file X says that at time T resource R (in system S, of
category C) was in state ST and carried identifiers I".  The rest of the
pipeline never needs to know which file format the fact came from.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Dict, Optional, Set

from . import config as C
from .identity import norm_email, norm_hash, norm_phone, norm_name, token


@dataclass
class Observation:
    evidence_id: str
    evidence_time: dt.datetime          # manifest captured_at (citation eligibility)
    observed_at: dt.datetime            # most precise time the fact refers to
    system_id: str
    resource_id: str
    category: str                       # one of config.CAT_*
    state: str                          # one of config.ST_*
    exposed: Set[str] = field(default_factory=set)       # identity tokens visible inside the record (PII exposure)
    links: Set[str] = field(default_factory=set)         # identity tokens that link the record to a subject (lookup keys, references)
    names: Set[str] = field(default_factory=set)         # name tokens (weak links)
    flags: Dict[str, object] = field(default_factory=dict)
    source: str = ""
    detail: str = ""
    seq: int = 0                        # tie breaker (order inside the file)

    @property
    def all_tokens(self) -> Set[str]:
        return self.exposed | self.links

    def short(self) -> str:
        return (f"{self.evidence_id}@{self.observed_at:%Y-%m-%dT%H:%M} {self.system_id}/{self.resource_id} "
                f"{self.category} {self.state} exp={sorted(self.exposed)} links={sorted(self.links)}")


def resource_prefix(resource_id: str) -> str:
    rid = resource_id or ""
    if "_" not in rid:
        return rid
    return rid.rsplit("_", 1)[0]


def default_system(resource_id: str, fallback: str = "") -> str:
    info = C.RESOURCE_PREFIX_INFO.get(resource_prefix(resource_id))
    return info[0] if info else fallback


def default_category(resource_id: str, fallback: str = "") -> str:
    info = C.RESOURCE_PREFIX_INFO.get(resource_prefix(resource_id))
    return info[1] if info else fallback


_NULLS = {None, "", "null", "none", "nil", "-", "n/a", "—"}


def is_null(value) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip().lower() in _NULLS
    return False


def identity_tokens(record: Dict[str, object], exposure_keys, link_keys) -> (Set[str], Set[str], Set[str]):
    """Split identity values of a record into exposed / link / name tokens."""
    exposed: Set[str] = set()
    links: Set[str] = set()
    names: Set[str] = set()

    def classify(key: str, value) -> Optional[str]:
        if is_null(value):
            return None
        sval = str(value).strip()
        k = key.lower()
        if "hash" in k:
            return token("hash", norm_hash(sval))
        if "email" in k or "@" in sval:
            return token("email", norm_email(sval))
        if "phone" in k:
            return token("phone", norm_phone(sval))
        if sval.startswith("user_"):
            return token("user", sval)
        if "name" in k:
            names.add(token("name", norm_name(sval)))
            return None
        if "reference" in k or "subject" in k or "selector" in k or "key" in k or "owner" in k or "uploaded" in k:
            # generic selector: decide by shape
            if "@" in sval:
                return token("email", norm_email(sval))
            if sval.startswith("user_"):
                return token("user", sval)
            if sval.startswith("+") or (sval.replace(" ", "").isdigit() and len(sval) >= 8):
                return token("phone", norm_phone(sval))
            if len(sval) == 64 and all(c in "0123456789abcdefABCDEF" for c in sval):
                return token("hash", norm_hash(sval))
        return None

    for key in exposure_keys:
        if key in record:
            t = classify(key, record[key])
            if t:
                exposed.add(t)
    for key in link_keys:
        if key in record:
            t = classify(key, record[key])
            if t:
                links.add(t)
    return exposed, links, names


def make_observation(evidence_id: str, evidence_time: dt.datetime, observed_at: Optional[dt.datetime],
                     resource_id: str, category: str, state: str, system_id: str = "",
                     exposed=None, links=None, names=None, flags=None, source: str = "",
                     detail: str = "", seq: int = 0) -> Observation:
    return Observation(
        evidence_id=evidence_id,
        evidence_time=evidence_time,
        observed_at=observed_at or evidence_time,
        system_id=system_id or default_system(resource_id, "unknown_system"),
        resource_id=resource_id,
        category=category or default_category(resource_id, "UNKNOWN"),
        state=state,
        exposed=set(exposed or ()),
        links=set(links or ()),
        names=set(names or ()),
        flags=dict(flags or {}),
        source=source,
        detail=detail,
        seq=seq,
    )
