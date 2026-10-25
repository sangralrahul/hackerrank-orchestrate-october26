"""Readers for the participant-facing CSV files under ``dataset/``."""

from __future__ import annotations

import csv
import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from .timeutil import parse_ts


@dataclass
class DeletionRequest:
    case_id: str
    email: str
    requested_at: dt.datetime
    reported_status: str
    reported_at: dt.datetime
    audit_at: dt.datetime
    request_text: str
    source_file: str = ""

    @property
    def case_number(self) -> int:
        digits = "".join(ch for ch in self.case_id if ch.isdigit())
        return int(digits) if digits else 0


@dataclass
class UserIdentity:
    user_id: str
    name: str
    email: str
    phone: str
    email_hash: str
    updated_at: Optional[dt.datetime]


@dataclass
class AuditEntry:
    audit_id: str
    user_id: str
    event_time: Optional[dt.datetime]
    field_name: str
    old_value: str
    new_value: str


@dataclass
class ActivityRow:
    activity_id: str
    event_time: dt.datetime
    event_type: str
    source_system: str
    target_system: str
    resource_id: str
    reported_result: str
    details: str


@dataclass
class DeletionRule:
    rule_id: str
    data_category: str
    condition: str
    required_action: str


@dataclass
class EvidenceFile:
    file_id: str
    file_path: str
    origin: str
    captured_at: dt.datetime

    @property
    def number(self) -> int:
        digits = "".join(ch for ch in self.file_id if ch.isdigit())
        return int(digits) if digits else 0


@dataclass
class Dataset:
    root: Path
    requests: List[DeletionRequest]
    identities: List[UserIdentity]
    audit_log: List[AuditEntry]
    activity: List[ActivityRow]
    rules: List[DeletionRule]
    evidence_files: List[EvidenceFile]
    extra_requests: List[DeletionRequest] = field(default_factory=list)

    @property
    def all_requests(self) -> List[DeletionRequest]:
        """Requests to audit plus any additional request lists used only for
        deletion-worker batch disambiguation (never for answers)."""
        seen = set()
        out: List[DeletionRequest] = []
        for r in list(self.requests) + list(self.extra_requests):
            if r.case_id in seen:
                continue
            seen.add(r.case_id)
            out.append(r)
        return out


def _read_csv(path: Path) -> List[Dict[str, str]]:
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def load_requests(path: Path) -> List[DeletionRequest]:
    rows = _read_csv(path)
    out = []
    for r in rows:
        out.append(
            DeletionRequest(
                case_id=r["case_id"].strip(),
                email=r["email"].strip(),
                requested_at=parse_ts(r["requested_at"]),
                reported_status=r.get("reported_status", "").strip(),
                reported_at=parse_ts(r.get("reported_at")),
                audit_at=parse_ts(r["audit_at"]),
                request_text=r.get("request_text", ""),
                source_file=str(path.name),
            )
        )
    return out


def load_dataset(root: Path, requests_file: Optional[Path] = None,
                 extra_request_files: Optional[List[Path]] = None) -> Dataset:
    root = Path(root)
    requests_file = Path(requests_file) if requests_file else root / "deletion_requests.csv"
    identities = [
        UserIdentity(r["user_id"], r["name"], r["email"], r["phone"], r["email_hash"], parse_ts(r.get("updated_at")))
        for r in _read_csv(root / "user_identities.csv")
    ]
    audit_log = [
        AuditEntry(r["audit_id"], r["user_id"], parse_ts(r["event_time"]), r["field_name"], r["old_value"], r["new_value"])
        for r in _read_csv(root / "user_audit_log.csv")
    ]
    activity = [
        ActivityRow(r["activity_id"], parse_ts(r["event_time"]), r["event_type"], r["source_system"],
                    r["target_system"], r["resource_id"], r["reported_result"], r["details"])
        for r in _read_csv(root / "system_activity.csv")
    ]
    rules = [
        DeletionRule(r["rule_id"], r["data_category"], r["condition"], r["required_action"])
        for r in _read_csv(root / "deletion_rules.csv")
    ]
    evidence_files = [
        EvidenceFile(r["file_id"], r["file_path"], r["origin"], parse_ts(r["captured_at"]))
        for r in _read_csv(root / "evidence_files.csv")
    ]
    requests = load_requests(requests_file)
    extra: List[DeletionRequest] = []
    for p in extra_request_files or []:
        p = Path(p)
        if p.exists() and p.resolve() != requests_file.resolve():
            extra.extend(load_requests(p))
    return Dataset(root=root, requests=requests, identities=identities, audit_log=audit_log,
                   activity=activity, rules=rules, evidence_files=evidence_files, extra_requests=extra)
