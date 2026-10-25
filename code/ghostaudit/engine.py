"""Orchestration: load everything once, audit every case, write output.csv."""

from __future__ import annotations

import csv
import datetime as dt
import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set

from . import config as C
from .activity import ActivityIndex
from .evidence import load_all_observations
from .identity import IdentityGraph, Subject
from .intent import RuleIntentClassifier, LLMIntentClassifier, IntentResult
from .linker import ResourceIndex, assign_batches, BatchLink
from .loaders import Dataset, DeletionRequest, load_dataset
from .observations import Observation, default_category, default_system, resource_prefix
from .planner import Policy, ResourceState, Step, plan, steps_to_json


@dataclass
class CaseResult:
    case_id: str
    status: str
    steps: List[Step]
    subject: Optional[Subject] = None
    intent: Optional[IntentResult] = None
    states: List[ResourceState] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def row(self) -> Dict[str, str]:
        return {"case_id": self.case_id, "deletion_status": self.status,
                "steps_json": json.dumps(steps_to_json(self.steps), ensure_ascii=False, separators=(",", ":"))}


class AuditEngine:
    def __init__(self, ds: Dataset, intent_classifier=None, doc_extractor=None, log=None):
        self.ds = ds
        self.log = log or (lambda *_: None)
        self.identity = IdentityGraph(ds.identities, ds.audit_log)
        self.activity = ActivityIndex(ds.activity)
        self.policy = Policy(ds.rules)
        self.log(f"[engine] parsing {len(ds.evidence_files)} evidence files")
        known_emails = set(self.identity.email_to_user) | {r.email.lower() for r in ds.all_requests}
        self.observations = load_all_observations(ds.evidence_files, ds.root, doc_extractor=doc_extractor,
                                                  aliases=self.activity.aliases, known_emails=known_emails)
        self.log(f"[engine] {len(self.observations)} observations extracted")
        self.index = ResourceIndex(self.observations, self.activity)
        self.intent = intent_classifier or RuleIntentClassifier()
        self.batch_stats: dict = {}

    # ------------------------------------------------------------------ public
    def run(self, requests: Optional[List[DeletionRequest]] = None) -> List[CaseResult]:
        requests = requests if requests is not None else self.ds.requests
        everyone = self.ds.all_requests
        subjects = {r.case_id: self.identity.resolve(r.email) for r in everyone}
        intents = {}
        intent_results = {}
        for r in everyone:
            res = self.intent.classify(r.request_text) if r.case_id in {q.case_id for q in requests} \
                else RuleIntentClassifier().classify(r.request_text)
            intent_results[r.case_id] = res
            intents[r.case_id] = res.authorizes_deletion
        batches = assign_batches(everyone, subjects, intents, self.index, self.activity, self.batch_stats,
                                 identity=self.identity)
        out = []
        for r in sorted(requests, key=lambda q: q.case_id):
            out.append(self.audit_case(r, subjects[r.case_id], intent_results[r.case_id], batches.get(r.case_id, [])))
        return out

    # ------------------------------------------------------------------ per case
    def audit_case(self, req: DeletionRequest, subject: Subject, intent: IntentResult,
                   batch: List[BatchLink]) -> CaseResult:
        if not intent.authorizes_deletion:
            return CaseResult(req.case_id, C.STATUS_NOT_APPLICABLE, [], subject, intent,
                              notes=[f"intent: {intent.reason}"])
        notes: List[str] = [f"subject: {subject.describe()}", f"intent: {intent.reason}"]
        candidates: Dict[str, str] = {}
        for rid in self.index.resources_for(subject):
            if self._belongs_to_other_request(rid, req, subject):
                notes.append(f"{rid}: skipped (belongs to another request of the same subject)")
                continue
            candidates[rid] = "identity"
        for bl in batch:
            candidates[bl.resource_id] = f"batch:{bl.kind}:{bl.confidence}" if bl.resource_id not in candidates \
                else f"identity+batch:{bl.kind}"
        states: List[ResourceState] = []
        for rid, why in sorted(candidates.items()):
            rs = self.reconstruct(rid, req, subject, why)
            if rs is not None:
                states.append(rs)
                notes.append(f"{rid}: {rs.category} {rs.state} ev={rs.evidence_id} ({why}; {rs.basis})")
        # accounts without direct proof: cite the profile-scope evidence co-located with the case's evidence
        case_files = {s.evidence_id for s in states if s.evidence_id}
        for rs in states:
            if rs.category == C.CAT_USER_PROFILE and rs.basis.startswith("scope:"):
                rs.evidence_id = self._scope_evidence(rs.resource_id, req, case_files) or rs.evidence_id
        status, steps = plan(states, self.policy)
        return CaseResult(req.case_id, status, steps, subject, intent, states, notes)

    def eligible(self, obs: Observation, req: DeletionRequest) -> bool:
        return obs.evidence_time <= req.audit_at and obs.observed_at <= req.audit_at

    def _requests_of(self, subject: Subject) -> List[DeletionRequest]:
        if not hasattr(self, "_req_by_user"):
            self._req_by_user = defaultdict(list)
            for r in self.ds.all_requests:
                u = self.identity.resolve(r.email).user_id
                if u:
                    self._req_by_user[u].append(r)
        return self._req_by_user.get(subject.user_id, []) if subject.user_id else []

    def _belongs_to_other_request(self, rid: str, req: DeletionRequest, subject: Subject) -> bool:
        reqs = [r for r in self._requests_of(subject) if RuleIntentClassifier().classify(r.request_text).authorizes_deletion]
        if len(reqs) < 2:
            return False
        obs = self.index.observations(rid)
        first = min((o.observed_at for o in obs), default=None)
        if first is None:
            return False
        later = sorted((r for r in reqs if r.audit_at >= first), key=lambda r: r.audit_at)
        return bool(later) and later[0].case_id != req.case_id

    def reconstruct(self, rid: str, req: DeletionRequest, subject: Subject, why: str) -> Optional[ResourceState]:
        all_obs = self.index.observations(rid)
        obs = [o for o in all_obs if self.eligible(o, req)]
        prefix = resource_prefix(rid)
        # identification may use evidence captured shortly after audit_at (resource ids of the
        # same review cycle); the state itself only uses evidence available at audit_at.
        horizon = req.audit_at + dt.timedelta(days=C.LATER_EVIDENCE_HORIZON_DAYS)
        ident_obs = [o for o in all_obs if o.evidence_time <= horizon]
        if why == "identity":
            if not ident_obs or not self._involves_subject(ident_obs, subject, rid):
                return None
        all_obs = ident_obs if why == "identity" else all_obs
        if obs:
            latest = max(obs, key=lambda o: (o.observed_at, o.evidence_time, o.seq))
            category = latest.category
            if latest.flags.get("category_guess"):
                firm = [o.category for o in all_obs if not o.flags.get("category_guess") and o.category]
                if firm:
                    category = firm[-1]
            state = latest.state
            if category != latest.category and category == C.CAT_SUPPORT_ATTACHMENT and state == C.ST_EXPOSED:
                state = C.ST_PRESENT
            return ResourceState(rid, latest.system_id or default_system(rid), category, state,
                                 latest.evidence_id, f"latest {latest.source} @ {latest.observed_at:%Y-%m-%d}")
        system_id = default_system(rid, "unknown_system")
        category = default_category(rid, "")
        later = sorted(all_obs, key=lambda o: (o.observed_at, o.seq))
        if later and later[-1].category in (C.CAT_SECURITY_AUDIT_LOG, C.CAT_LEGAL_HOLD_RECORD):
            # a policy-required retained record is known; its availability is a future check
            return ResourceState(rid, later[-1].system_id or system_id, later[-1].category, C.ST_RETAINED, "",
                                 "retained record referenced only by later evidence")
        if prefix == "account":
            return ResourceState(rid, system_id, C.CAT_USER_PROFILE, C.ST_ABSENT, "", "scope:inferred from workflow")
        return None

    def _scope_evidence(self, rid: str, req: DeletionRequest, case_files: Set[str]) -> str:
        scope = [o for o in self.index.scope_obs if self.eligible(o, req) and o.observed_at >= req.reported_at
                 and rid not in o.flags.get("present_ids", [])]
        if not scope:
            return ""
        preferred = [o for o in scope if o.evidence_id in case_files]
        pick = max(preferred or scope, key=lambda o: (o.observed_at, o.seq))
        return pick.evidence_id

    def _involves_subject(self, obs: List[Observation], subject: Subject, rid: str) -> bool:
        toks = subject.tokens
        if any(o.all_tokens & toks for o in obs):
            return True
        if not (self.activity.links.get(rid, set()) & toks):
            return False
        # A catalog link is a claim. If the record itself shows direct identifiers and
        # none of them is the subject's, the evidence contradicts the claim: not the subject's data.
        shown = {t for o in obs for t in o.exposed if t.startswith(("email:", "phone:"))}
        return not shown


def case_sort_key(case_id: str):
    """Natural order: case_33 < case_100 (same order as deletion_requests.csv)."""
    import re
    m = re.match(r"^(.*?)(\d+)$", case_id)
    return (m.group(1), int(m.group(2))) if m else (case_id, 0)


def write_output(results: List[CaseResult], path: Path) -> None:
    path = Path(path)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=C.OUTPUT_COLUMNS)
        w.writeheader()
        for r in sorted(results, key=lambda x: case_sort_key(x.case_id)):
            w.writerow(r.row())
