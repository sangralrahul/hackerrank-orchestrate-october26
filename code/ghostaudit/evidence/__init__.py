"""Evidence parsers. ``load_all_observations`` dispatches on file type."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Tuple

from ..loaders import EvidenceFile
from ..observations import Observation
from . import json_records, sql_snapshots, logs, textdocs, pdfdocs, images


class EvidenceContext:
    """Per-file context handed to the parsers."""

    def __init__(self, ev: EvidenceFile, path: Path, dataset_root: Path,
                 aliases: Optional[Dict[str, Tuple[str, str]]] = None, known_emails=None):
        self.known_emails = known_emails
        self.evidence_id = ev.file_id
        self.evidence_time = ev.captured_at
        self.origin = ev.origin
        self.path = path
        self.dataset_root = dataset_root
        self.aliases = aliases or {}
        self.seq = 0

    def next_seq(self) -> int:
        self.seq += 1
        return self.seq


def parse_evidence_file(ev: EvidenceFile, dataset_root: Path, doc_extractor=None,
                        aliases: Optional[Dict[str, Tuple[str, str]]] = None, known_emails=None) -> List[Observation]:
    path = Path(dataset_root) / ev.file_path
    ctx = EvidenceContext(ev, path, Path(dataset_root), aliases, known_emails)
    if not path.exists():
        return []
    suffix = path.suffix.lower()
    if suffix == ".json":
        return json_records.parse(ctx)
    if suffix == ".sql":
        return sql_snapshots.parse(ctx)
    if suffix == ".log":
        return logs.parse(ctx)
    if suffix == ".txt":
        return textdocs.parse(ctx)
    if suffix == ".pdf":
        return pdfdocs.parse(ctx, doc_extractor)
    if suffix in {".png", ".jpg", ".jpeg"}:
        return images.parse(ctx, doc_extractor)
    return []


def load_all_observations(evidence_files: List[EvidenceFile], dataset_root: Path,
                          doc_extractor=None, log=None,
                          aliases: Optional[Dict[str, Tuple[str, str]]] = None, known_emails=None) -> List[Observation]:
    out: List[Observation] = []
    for ev in sorted(evidence_files, key=lambda e: e.number):
        obs = parse_evidence_file(ev, dataset_root, doc_extractor, aliases, known_emails)
        if log:
            log(f"  {ev.file_id:<12} {ev.file_path:<70} -> {len(obs):4d} observations")
        out.extend(obs)
    return out
