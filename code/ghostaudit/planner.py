"""Policy engine + step planner.

For every resource linked to a case we reconstruct its state at ``audit_at``
(latest eligible observation wins), look up the treatment required by
``deletion_rules.csv`` for its data category and emit:

* a FINDING + REMEDIATION when the state violates the rule (or a process can
  recreate data), and
* a VERIFICATION describing the required final state.

Ordering follows the contract: phase, operation order, system_id, resource_id.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from . import config as C
from .loaders import DeletionRule
from .observations import Observation


@dataclass
class ResourceState:
    resource_id: str
    system_id: str
    category: str
    state: str
    evidence_id: str            # evidence proving the state ("" if only inferred)
    basis: str                  # short explanation


@dataclass
class Step:
    phase: str
    system_id: str
    resource_id: str
    operation: str
    state: str
    evidence_id: str

    def sort_key(self):
        return (C.PHASE_ORDER[self.phase], C.OPERATION_ORDER[self.operation], self.system_id, self.resource_id)


class Policy:
    """deletion_rules.csv lookup: data_category -> required action."""

    def __init__(self, rules: List[DeletionRule]):
        self.action: Dict[str, str] = {r.data_category: r.required_action.upper() for r in rules}
        self.rule_id: Dict[str, str] = {r.data_category: r.rule_id for r in rules}

    def required(self, category: str) -> str:
        if category == C.CAT_DATA_FLOW:
            return "DISABLE"
        if category in self.action:
            return self.action[category]
        return "DELETE"


def plan(states: List[ResourceState], policy: Policy) -> Tuple[str, List[Step]]:
    findings: List[Step] = []
    remediation: List[Step] = []
    verification: List[Step] = []
    for rs in states:
        req = policy.required(rs.category)
        sysid, rid, ev = rs.system_id, rs.resource_id, rs.evidence_id
        st = rs.state
        if rs.category == C.CAT_DATA_FLOW:
            if st == C.ST_ACTIVE:
                findings.append(Step(C.PHASE_FINDING, sysid, rid, "REPORT_ACTIVE_DATA_FLOW", "ACTIVE", ev))
                remediation.append(Step(C.PHASE_REMEDIATION, sysid, rid, "DISABLE_DATA_FLOW", "DISABLED", ""))
            verification.append(Step(C.PHASE_VERIFICATION, sysid, rid, "VERIFY_DATA_FLOW_DISABLED", "DISABLED", ev))
        elif rs.category == C.CAT_BACKUP_ARCHIVE:  # RETAIN, restore access must be blocked
            if st == C.ST_ACTIVE:
                findings.append(Step(C.PHASE_FINDING, sysid, rid, "REPORT_RESTORE_RISK", "ACTIVE", ev))
                remediation.append(Step(C.PHASE_REMEDIATION, sysid, rid, "BLOCK_RESTORE", "BLOCKED", ""))
            verification.append(Step(C.PHASE_VERIFICATION, sysid, rid, "VERIFY_RESTORE_BLOCKED", "BLOCKED", ev))
        elif req == "RETAIN":
            verification.append(Step(C.PHASE_VERIFICATION, sysid, rid, "VERIFY_RECORD_PRESENT", "PRESENT", ev))
        elif req == "DELETE":
            if st in (C.ST_PRESENT, C.ST_EXPOSED):
                findings.append(Step(C.PHASE_FINDING, sysid, rid, "REPORT_REMAINING_RECORD", "PRESENT", ev))
                remediation.append(Step(C.PHASE_REMEDIATION, sysid, rid, "DELETE_RECORD", "ABSENT", ""))
            verification.append(Step(C.PHASE_VERIFICATION, sysid, rid, "VERIFY_RECORD_ABSENT", "ABSENT", ev))
        elif req == "REDACT":
            if st in (C.ST_PRESENT, C.ST_EXPOSED):
                findings.append(Step(C.PHASE_FINDING, sysid, rid, "REPORT_EXPOSED_PII", "PRESENT", ev))
                remediation.append(Step(C.PHASE_REMEDIATION, sysid, rid, "REDACT_RECORD", "REDACTED", ""))
            verification.append(Step(C.PHASE_VERIFICATION, sysid, rid, "VERIFY_RECORD_REDACTED", "REDACTED", ev))
        elif req == "ANONYMIZE":
            if st in (C.ST_PRESENT, C.ST_EXPOSED):
                findings.append(Step(C.PHASE_FINDING, sysid, rid, "REPORT_EXPOSED_PII", "PRESENT", ev))
                remediation.append(Step(C.PHASE_REMEDIATION, sysid, rid, "ANONYMIZE_RECORD", "ANONYMIZED", ""))
            verification.append(Step(C.PHASE_VERIFICATION, sysid, rid, "VERIFY_RECORD_ANONYMIZED", "ANONYMIZED", ev))
    status = C.STATUS_INCOMPLETE if findings else C.STATUS_COMPLETE
    if status == C.STATUS_INCOMPLETE:
        for s in verification:
            s.evidence_id = ""  # future checks
        steps = sorted(findings, key=Step.sort_key) + sorted(remediation, key=Step.sort_key) + \
            sorted(verification, key=Step.sort_key)
    else:
        steps = sorted(verification, key=Step.sort_key)
    return status, steps


def steps_to_json(steps: List[Step]) -> List[dict]:
    return [
        {"step_no": i, "phase": s.phase, "system_id": s.system_id, "resource_id": s.resource_id,
         "operation": s.operation, "state": s.state, "evidence_id": s.evidence_id}
        for i, s in enumerate(steps, start=1)
    ]
