"""Metrics comparing predicted rows with expected rows (sample_output.csv or custom cases)."""

from __future__ import annotations

import json
from difflib import SequenceMatcher
from typing import Dict, List

FIELDS = ["phase", "system_id", "resource_id", "operation", "state", "evidence_id"]


def _key(s: dict) -> tuple:
    return tuple(s.get(f, "") for f in FIELDS)


def score(pred: Dict[str, dict], gold: Dict[str, dict]) -> dict:
    """pred/gold: case_id -> {"deletion_status": str, "steps": list}."""
    n = len(gold)
    status_ok = 0
    exact_ok = 0
    seq_ok = 0
    tp = fp = fn = 0
    field_hits = {f: 0 for f in FIELDS}
    field_total = 0
    seq_ratio_sum = 0.0
    per_case = []
    for cid, g in sorted(gold.items()):
        p = pred.get(cid, {"deletion_status": "MISSING", "steps": []})
        s_ok = p["deletion_status"] == g["deletion_status"]
        status_ok += s_ok
        gk = [_key(s) for s in g["steps"]]
        pk = [_key(s) for s in p["steps"]]
        gs, ps = set(gk), set(pk)
        tp += len(gs & ps)
        fp += len(ps - gs)
        fn += len(gs - ps)
        exact = s_ok and gk == pk
        exact_ok += exact
        seq_ok += (gk == pk)
        seq_ratio_sum += SequenceMatcher(a=gk, b=pk, autojunk=False).ratio() if (gk or pk) else 1.0
        # field-level: align by position
        for i, gst in enumerate(g["steps"]):
            field_total += 1
            pst = p["steps"][i] if i < len(p["steps"]) else {}
            for f in FIELDS:
                field_hits[f] += (pst.get(f, None) == gst.get(f))
        per_case.append({"case_id": cid, "gold_status": g["deletion_status"], "pred_status": p["deletion_status"],
                         "status_ok": s_ok, "exact": exact, "missing": sorted(gs - ps), "extra": sorted(ps - gs)})
    prec = tp / (tp + fp) if tp + fp else 1.0
    rec = tp / (tp + fn) if tp + fn else 1.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    return {
        "cases": n,
        "status_accuracy": status_ok / n if n else 0,
        "exact_row_accuracy": exact_ok / n if n else 0,
        "sequence_exact_accuracy": seq_ok / n if n else 0,
        "sequence_similarity": seq_ratio_sum / n if n else 0,
        "step_precision": prec, "step_recall": rec, "step_f1": f1,
        "field_accuracy": {f: (field_hits[f] / field_total if field_total else 0) for f in FIELDS},
        "per_case": per_case,
    }


def load_rows(path) -> Dict[str, dict]:
    import csv
    out = {}
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            out[r["case_id"]] = {"deletion_status": r["deletion_status"], "steps": json.loads(r["steps_json"] or "[]")}
    return out
