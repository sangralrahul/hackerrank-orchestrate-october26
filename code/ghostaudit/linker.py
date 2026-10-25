"""Connect evidence and activity to the subject of each case.

Two linking mechanisms are combined:

* **identity links** - an observation carries e-mails, phones, hashes or
  user references that belong to the subject (current *or* historical);
* **batch links** - the deletion workflow handled a resource in the batch
  anchored at the case's ``reported_at``.  Each reported_at group is resolved
  jointly: resources that evidence ties to one member are assigned to it,
  the remaining ones are distributed only when the assignment is
  unambiguous enough (at most two candidate cases).
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Set, Tuple

from . import config as C
from .activity import ActivityIndex
from .identity import IdentityGraph, Subject
from .loaders import DeletionRequest
from .observations import Observation, resource_prefix


@dataclass
class BatchLink:
    resource_id: str
    kind: str              # ACCOUNT / SHOWCASE / POLICY_OK / PROBLEM / CHECK
    activity_id: str
    event_type: str
    reported_result: str
    confidence: str        # "evidence" (tied by identity) or "slot" (assigned by batch position)


class ResourceIndex:
    """Fast lookups over all observations."""

    def __init__(self, observations: Iterable[Observation], activity: ActivityIndex):
        self.obs_by_resource: Dict[str, List[Observation]] = defaultdict(list)
        self.token_to_resources: Dict[str, Set[str]] = defaultdict(set)
        self.scope_obs: List[Observation] = []
        self.activity = activity
        for o in observations:
            if o.resource_id == "*":
                self.scope_obs.append(o)
                continue
            self.obs_by_resource[o.resource_id].append(o)
            for t in o.all_tokens:
                self.token_to_resources[t].add(o.resource_id)
        for rid, toks in activity.links.items():
            for t in toks:
                self.token_to_resources[t].add(rid)
        for rid in self.obs_by_resource:
            self.obs_by_resource[rid].sort(key=lambda o: (o.observed_at, o.evidence_time, o.seq))

    def resources_for(self, subject: Subject) -> Set[str]:
        out: Set[str] = set()
        for t in subject.tokens:
            out |= self.token_to_resources.get(t, set())
        return out

    def observations(self, resource_id: str) -> List[Observation]:
        return self.obs_by_resource.get(resource_id, [])


STATUS_TO_RESULT = {"COMPLETE": "SUCCESS", "PARTIALLY_COMPLETE": "PARTIAL", "NOT_STARTED": "FAILED"}


def _foreign_owned(rid: str, index: ResourceIndex, identity: IdentityGraph, member_tokens: Set[str]) -> bool:
    """True when evidence ties the resource to an identity that is not a member of the batch."""
    toks: Set[str] = set()
    for o in index.observations(rid):
        toks |= o.all_tokens
    toks |= index.activity.links.get(rid, set())
    toks = {t for t in toks if not t.startswith("name:")}
    return bool(toks) and not (toks & member_tokens)


def assign_batches(requests: List[DeletionRequest], subjects: Dict[str, Subject], intents: Dict[str, bool],
                   index: ResourceIndex, activity: ActivityIndex, stats: Optional[dict] = None,
                   identity: Optional[IdentityGraph] = None) -> Dict[str, List[BatchLink]]:
    """Resolve deletion-worker batches jointly per reported_at group.

    1. a batch resource tied by identity evidence to exactly one member goes to it;
    2. a resource tied to a non-member identity is a decoy and is ignored;
    3. unlinked primary profiles are matched to members whose ``reported_status``
       mirrors the worker's result (COMPLETE<->SUCCESS, PARTIALLY_COMPLETE<->PARTIAL,
       NOT_STARTED<->FAILED); remaining ties follow batch time order vs case order;
    4. unlinked policy-action resources (e.g. already anonymised analytics) go to
       members that have no failed/checked work and no tombstone, in order;
    5. unlinked failed/checked resources are assigned only when a single member can own them.
    """
    groups: Dict[dt.datetime, List[DeletionRequest]] = defaultdict(list)
    for r in requests:
        if r.reported_at is None or not intents.get(r.case_id, True):
            continue
        groups[r.reported_at].append(r)
    out: Dict[str, List[BatchLink]] = defaultdict(list)
    stats = stats if stats is not None else {}
    for k in ("slot_assigned", "slot_skipped", "evidence_assigned", "decoy_skipped"):
        stats.setdefault(k, 0)

    for reported_at, members in groups.items():
        members = sorted(members, key=lambda r: r.case_number)
        rows = activity.batch_rows(reported_at)
        member_resources = {m.case_id: index.resources_for(subjects[m.case_id]) for m in members}
        member_tokens: Set[str] = set()
        for m in members:
            member_tokens |= subjects[m.case_id].tokens
        prefixes: Dict[str, Set[str]] = defaultdict(set)
        kinds: Dict[str, Set[str]] = defaultdict(set)
        for cid, res in member_resources.items():
            for rid in res:
                prefixes[cid].add(resource_prefix(rid))
        unassigned: Dict[str, List[tuple]] = defaultdict(list)
        for kind in ("ACCOUNT", "SHOWCASE", "POLICY_OK", "PROBLEM", "CHECK"):
            for row in rows.get(kind, []):
                rid = row.resource_id
                owners = [cid for cid, res in member_resources.items() if rid in res]
                if len(owners) == 1:
                    out[owners[0]].append(BatchLink(rid, kind, row.activity_id, row.event_type, row.reported_result, "evidence"))
                    prefixes[owners[0]].add(resource_prefix(rid))
                    kinds[owners[0]].add(kind)
                    stats["evidence_assigned"] += 1
                elif len(owners) == 0:
                    if _foreign_owned(rid, index, identity, member_tokens):
                        stats["decoy_skipped"] += 1
                        continue
                    unassigned[kind].append(row)

        def give(member, row, kind):
            out[member.case_id].append(BatchLink(row.resource_id, kind, row.activity_id, row.event_type,
                                                 row.reported_result, "slot"))
            prefixes[member.case_id].add(resource_prefix(row.resource_id))
            kinds[member.case_id].add(kind)
            stats["slot_assigned"] += 1

        # --- primary profiles (ACCOUNT and SHOWCASE accounts) ------------------
        acct_rows = sorted([r for k in ("ACCOUNT", "SHOWCASE") for r in unassigned.get(k, [])
                            if resource_prefix(r.resource_id) == "account"], key=lambda r: (r.event_time, r.resource_id))
        free = [m for m in members if "account" not in prefixes[m.case_id]]
        for result in ("SUCCESS", "PARTIAL", "FAILED"):
            rws = [r for r in acct_rows if r.reported_result == result]
            mem = [m for m in free if STATUS_TO_RESULT.get(m.reported_status) == result]
            for m, r in zip(mem, rws):
                give(m, r, "ACCOUNT")
            used = {r.resource_id for r in rws[:len(mem)]}
            acct_rows = [r for r in acct_rows if r.resource_id not in used]
            free = [m for m in free if "account" not in prefixes[m.case_id]]
        for m, r in zip(free, acct_rows):  # leftovers: best effort
            give(m, r, "ACCOUNT")
        stats["slot_skipped"] += max(0, len(acct_rows) - len(free))

        # --- other showcase rows ----------------------------------------------------
        for row in unassigned.get("SHOWCASE", []):
            if resource_prefix(row.resource_id) == "account":
                continue
            cand = [m for m in members if resource_prefix(row.resource_id) not in prefixes[m.case_id]]
            if len(cand) >= 1:
                give(cand[0], row, "SHOWCASE")

        # --- policy actions that left no identity behind -----------------------------
        by_prefix: Dict[str, List] = defaultdict(list)
        for row in unassigned.get("POLICY_OK", []):
            by_prefix[resource_prefix(row.resource_id)].append(row)
        for prefix, rws in by_prefix.items():
            rws.sort(key=lambda r: (r.event_time, r.resource_id))
            cand = [m for m in members if prefix not in prefixes[m.case_id]
                    and "tombstone" not in prefixes[m.case_id]
                    and not (kinds[m.case_id] & {"PROBLEM", "CHECK"})]
            for m, r in zip(cand, rws):
                give(m, r, "POLICY_OK")
            stats["slot_skipped"] += max(0, len(rws) - len(cand))

        # --- failed / checked work without identity -----------------------------------
        for kind in ("PROBLEM", "CHECK"):
            for row in unassigned.get(kind, []):
                prefix = resource_prefix(row.resource_id)
                cand = [m for m in members if prefix not in prefixes[m.case_id]]
                if len(cand) == 1:
                    give(cand[0], row, kind)
                else:
                    stats["slot_skipped"] += 1
    return out
