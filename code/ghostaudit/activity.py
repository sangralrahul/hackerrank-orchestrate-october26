"""Index over ``system_activity.csv``.

Activity rows are *claims*: they tell us which resources a workflow touched
and when, never the real state.  We use them for three things:

1. catalog links (``LINK_RESOURCE_IDENTITY``) that tie a resource or document
   alias to a subject;
2. the deletion-worker batch structure, which reveals the resource ids a
   case's workflow handled even when evidence shows the record without any
   identifier (for example an already anonymised analytics profile);
3. request classification claims (``CLASSIFY_REQUEST``) used only as a
   cross-check for the intent classifier.
"""

from __future__ import annotations

import datetime as dt
import re
from collections import defaultdict
from typing import Dict, Iterable, List, Optional, Set, Tuple

from . import config as C
from .identity import tokens_from_text, token
from .loaders import ActivityRow
from .observations import resource_prefix
from .timeutil import minutes_between

_LINK_RE = re.compile(r"linked\s+(\S+)\s+\(([^)]+)\)\s+to\s+(.+?)\s+and\s+(user_\d+)", re.I)
_ATTACH_RE = re.compile(r"confirmed\s+(\S+)\s+\(([^)]+)\)\s+belongs only to\s+(\S+)\s+and\s+(user_\d+)", re.I)


class ActivityIndex:
    def __init__(self, rows: Iterable[ActivityRow]):
        self.rows: List[ActivityRow] = sorted(rows, key=lambda r: (r.event_time, r.activity_id))
        self.by_resource: Dict[str, List[ActivityRow]] = defaultdict(list)
        for r in self.rows:
            self.by_resource[r.resource_id].append(r)
        self.links: Dict[str, Set[str]] = defaultdict(set)          # resource_id -> identity tokens
        self.link_rows: Dict[str, ActivityRow] = {}
        self.aliases: Dict[str, Tuple[str, str]] = {}               # alias -> (resource_id, system)
        self.only_subject: Set[str] = set()                         # resources confirmed to belong only to subject
        self.classified_non_deletion: Set[str] = set()              # account ids classified as non-deletion
        for r in self.rows:
            if r.event_type == "LINK_RESOURCE_IDENTITY":
                m = _LINK_RE.search(r.details) or _ATTACH_RE.search(r.details)
                if m:
                    rid, alias, email, user = m.groups()
                    toks = tokens_from_text(email) | {token("user", user)}
                    self.links[rid] |= toks
                    self.link_rows[rid] = r
                    if alias and alias != rid:
                        self.aliases[alias] = (rid, r.target_system)
                    if "belongs only to" in r.details:
                        self.only_subject.add(rid)
                else:
                    self.links[r.resource_id] |= tokens_from_text(r.details)
                    self.link_rows[r.resource_id] = r
            elif r.event_type == "CLASSIFY_REQUEST":
                self.classified_non_deletion.add(r.resource_id)
        self.worker_rows = [r for r in self.rows if r.source_system == "deletion_worker"]

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def row_kind(r: ActivityRow) -> str:
        d = r.details
        if "submitted primary profile" in d:
            return "ACCOUNT"
        if "Target resource" in d:
            return "SHOWCASE"
        if "Policy action completed" in d:
            return "POLICY_OK"
        if "received the final result" in d:
            return "PROBLEM"
        if r.event_type in {"CHECK_DATA_FLOW", "CHECK_RESTORE_ACCESS"}:
            return "CHECK"
        return "OTHER"

    def batch_rows(self, reported_at: dt.datetime) -> Dict[str, List[ActivityRow]]:
        """Deletion-worker rows that belong to the batch anchored at ``reported_at``.

        Returns a dict with keys ACCOUNT / SHOWCASE / POLICY_OK / PROBLEM / CHECK.
        """
        out: Dict[str, List[ActivityRow]] = defaultdict(list)
        if reported_at is None:
            return out
        for r in self.worker_rows:
            off = minutes_between(r.event_time, reported_at)
            kind = self.row_kind(r)
            if kind == "ACCOUNT" and C.BATCH_ACCOUNT_WINDOW[0] <= off <= C.BATCH_ACCOUNT_WINDOW[1]:
                out["ACCOUNT"].append(r)
            elif kind == "SHOWCASE" and C.BATCH_SHOWCASE_WINDOW[0] <= off <= C.BATCH_SHOWCASE_WINDOW[1]:
                out["SHOWCASE"].append(r)
            elif kind == "POLICY_OK" and C.BATCH_POLICY_WINDOW[0] <= off <= C.BATCH_POLICY_WINDOW[1]:
                out["POLICY_OK"].append(r)
            elif kind in {"PROBLEM", "CHECK"} and C.BATCH_PROBLEM_WINDOW[0] <= off <= C.BATCH_PROBLEM_WINDOW[1]:
                out[kind].append(r)
        return out

    def history(self, resource_id: str) -> List[ActivityRow]:
        return self.by_resource.get(resource_id, [])

    def recreation_signal(self, resource_id: str, after: dt.datetime, before: dt.datetime) -> Optional[ActivityRow]:
        """A sync / restore / index refresh of the resource after a deletion attempt."""
        for r in self.history(resource_id):
            if r.event_type in {"SYNC_RECORD", "RESTORE_BACKUP", "INDEX_RECORD"} and after <= r.event_time <= before:
                return r
        return None
