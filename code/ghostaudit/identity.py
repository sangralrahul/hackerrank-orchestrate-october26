"""Identity resolution: request e-mail -> subject -> current + historical identifiers."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Set

from .loaders import AuditEntry, UserIdentity

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
HASH_RE = re.compile(r"\b[a-f0-9]{64}\b", re.I)
USER_RE = re.compile(r"\buser_\d+\b")
PHONE_RE = re.compile(r"\+\d[\d\s().\-]{6,}\d")


def norm_email(value: str) -> str:
    return value.strip().lower()


def norm_phone(value: str) -> str:
    digits = re.sub(r"\D", "", value or "")
    return digits


def norm_hash(value: str) -> str:
    return (value or "").strip().lower()


def norm_name(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", value.strip().lower())


def token(kind: str, value: str) -> str:
    return f"{kind}:{value}"


def tokens_from_text(text: str) -> Set[str]:
    """Extract identity tokens (emails, user ids, hashes, phones) from free text."""
    out: Set[str] = set()
    if not text:
        return out
    for m in EMAIL_RE.findall(text):
        out.add(token("email", norm_email(m)))
    for m in HASH_RE.findall(text):
        out.add(token("hash", norm_hash(m)))
    for m in USER_RE.findall(text):
        out.add(token("user", m))
    for m in PHONE_RE.findall(text):
        digits = norm_phone(m)
        if 8 <= len(digits) <= 15:
            out.add(token("phone", digits))
    return out


@dataclass
class Subject:
    """A data subject with every identifier that may appear in evidence."""

    user_id: Optional[str]
    request_email: str
    names: Set[str] = field(default_factory=set)
    emails: Set[str] = field(default_factory=set)
    phones: Set[str] = field(default_factory=set)
    hashes: Set[str] = field(default_factory=set)
    current_email: Optional[str] = None
    resolved_via: str = "unresolved"

    @property
    def tokens(self) -> Set[str]:
        out = {token("email", e) for e in self.emails}
        out |= {token("phone", p) for p in self.phones}
        out |= {token("hash", h) for h in self.hashes}
        if self.user_id:
            out.add(token("user", self.user_id))
        return out

    @property
    def name_tokens(self) -> Set[str]:
        return {token("name", n) for n in self.names}

    def describe(self) -> str:
        return (f"user_id={self.user_id} via={self.resolved_via} emails={sorted(self.emails)} "
                f"phones={sorted(self.phones)} hashes={len(self.hashes)}")


class IdentityGraph:
    """Index of user_identities.csv + user_audit_log.csv."""

    def __init__(self, identities: Iterable[UserIdentity], audit_log: Iterable[AuditEntry]):
        self.by_user: Dict[str, UserIdentity] = {}
        self.email_to_user: Dict[str, str] = {}
        self.hash_to_user: Dict[str, str] = {}
        self.phone_to_user: Dict[str, str] = {}
        self.history: Dict[str, List[AuditEntry]] = {}
        for ident in identities:
            self.by_user[ident.user_id] = ident
            self.email_to_user[norm_email(ident.email)] = ident.user_id
            if ident.email_hash:
                self.hash_to_user[norm_hash(ident.email_hash)] = ident.user_id
            if ident.phone:
                self.phone_to_user[norm_phone(ident.phone)] = ident.user_id
        for entry in audit_log:
            self.history.setdefault(entry.user_id, []).append(entry)
            if entry.field_name == "email":
                for v in (entry.old_value, entry.new_value):
                    self.email_to_user.setdefault(norm_email(v), entry.user_id)
            elif entry.field_name == "email_hash":
                for v in (entry.old_value, entry.new_value):
                    self.hash_to_user.setdefault(norm_hash(v), entry.user_id)
            elif entry.field_name == "phone":
                for v in (entry.old_value, entry.new_value):
                    self.phone_to_user.setdefault(norm_phone(v), entry.user_id)
        # token -> user map for decoy detection
        self.token_owner: Dict[str, str] = {}
        for e, u in self.email_to_user.items():
            self.token_owner[token("email", e)] = u
        for h, u in self.hash_to_user.items():
            self.token_owner[token("hash", h)] = u
        for p, u in self.phone_to_user.items():
            self.token_owner[token("phone", p)] = u
        for u in self.by_user:
            self.token_owner[token("user", u)] = u

    def resolve(self, email: str) -> Subject:
        e = norm_email(email)
        user_id = self.email_to_user.get(e)
        subject = Subject(user_id=user_id, request_email=e)
        subject.emails.add(e)
        if user_id is None:
            subject.resolved_via = "unresolved"
            return subject
        ident = self.by_user.get(user_id)
        if ident:
            subject.names.add(norm_name(ident.name))
            subject.emails.add(norm_email(ident.email))
            subject.current_email = norm_email(ident.email)
            if ident.phone:
                subject.phones.add(norm_phone(ident.phone))
            if ident.email_hash:
                subject.hashes.add(norm_hash(ident.email_hash))
        for entry in self.history.get(user_id, []):
            vals = (entry.old_value, entry.new_value)
            if entry.field_name == "email":
                subject.emails.update(norm_email(v) for v in vals if v)
            elif entry.field_name == "email_hash":
                subject.hashes.update(norm_hash(v) for v in vals if v)
            elif entry.field_name == "phone":
                subject.phones.update(norm_phone(v) for v in vals if v)
        subject.resolved_via = "current_email" if ident and norm_email(ident.email) == e else "historical_email"
        return subject

    def owner_of(self, tok: str) -> Optional[str]:
        return self.token_owner.get(tok)
