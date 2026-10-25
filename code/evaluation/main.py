#!/usr/bin/env python3
"""Evaluation workflow.

    python3 code/evaluation/main.py

1. runs the agent on dataset/sample_deletion_requests.csv and scores it against
   dataset/sample_output.csv (status, step, field and sequence accuracy);
2. builds the custom scenarios in evaluation/custom_cases and scores them;
3. runs ablations (linking features switched off) to show what each component buys;
4. writes evaluation/results.json and prints a per-case failure list.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CODE = HERE.parent
REPO = CODE.parent
sys.path.insert(0, str(CODE))
sys.path.insert(0, str(HERE / "custom_cases"))

from ghostaudit import linker  # noqa: E402
from ghostaudit.engine import AuditEngine  # noqa: E402
from ghostaudit.loaders import load_dataset  # noqa: E402
from ghostaudit.planner import steps_to_json  # noqa: E402
from ghostaudit.scoring import load_rows, score  # noqa: E402
import build as custom_build  # noqa: E402


def run(dataset: Path, requests: Path, extra=None):
    ds = load_dataset(dataset, requests, extra or [])
    eng = AuditEngine(ds)
    res = eng.run()
    return {r.case_id: {"deletion_status": r.status, "steps": steps_to_json(r.steps)} for r in res}, res


def headline(m):
    return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in m.items() if k != "per_case"} | {
        "field_accuracy": {k: round(v, 4) for k, v in m["field_accuracy"].items()}}


def main() -> int:
    dataset = REPO / "dataset"
    extra = [dataset / "deletion_requests.csv"]
    report = {}

    pred, res = run(dataset, dataset / "sample_deletion_requests.csv", extra)
    m = score(pred, load_rows(dataset / "sample_output.csv"))
    report["sample"] = headline(m)
    report["sample_failures"] = [pc for pc in m["per_case"] if not pc["exact"]]
    print("== sample set (32 cases)")
    print(json.dumps(report["sample"], indent=1))
    for pc in report["sample_failures"]:
        print(f"  {pc['case_id']}: missing={pc['missing']} extra={pc['extra']}")

    cdir = custom_build.build(HERE / "custom_cases" / "dataset")
    cpred, _ = run(cdir, cdir / "deletion_requests.csv")
    cm = score(cpred, load_rows(cdir / "expected_output.csv"))
    report["custom"] = headline(cm)
    report["custom_cases"] = [{"case_id": s["case_id"], "title": s["title"],
                               "exact": next(p["exact"] for p in cm["per_case"] if p["case_id"] == s["case_id"]),
                               "missing": next(p["missing"] for p in cm["per_case"] if p["case_id"] == s["case_id"]),
                               "extra": next(p["extra"] for p in cm["per_case"] if p["case_id"] == s["case_id"])}
                              for s in custom_build.SCENARIOS]
    print("== custom scenarios")
    for c in report["custom_cases"]:
        print(f"  {c['case_id']} exact={c['exact']} {c['title']}  missing={c['missing']} extra={c['extra']}")

    # ---- ablations ------------------------------------------------------------
    original = linker.STATUS_TO_RESULT.copy()
    linker.STATUS_TO_RESULT.clear()  # no reported_status <-> worker-result matching
    p2, _ = run(dataset, dataset / "sample_deletion_requests.csv", extra)
    report["ablation_no_status_matching"] = headline(score(p2, load_rows(dataset / "sample_output.csv")))
    linker.STATUS_TO_RESULT.update(original)
    saved = linker.assign_batches
    linker.assign_batches = lambda *a, **k: {}
    import ghostaudit.engine as eng_mod
    eng_saved = eng_mod.assign_batches
    eng_mod.assign_batches = lambda *a, **k: {}
    p3, _ = run(dataset, dataset / "sample_deletion_requests.csv", extra)
    report["ablation_no_batch_linking"] = headline(score(p3, load_rows(dataset / "sample_output.csv")))
    linker.assign_batches = saved
    eng_mod.assign_batches = eng_saved
    print("== ablations (sample set)")
    for k in ("ablation_no_status_matching", "ablation_no_batch_linking"):
        r = report[k]
        print(f"  {k}: status={r['status_accuracy']} exact={r['exact_row_accuracy']} f1={r['step_f1']}")

    (HERE / "results.json").write_text(json.dumps(report, indent=2, default=str))
    print(f"wrote {HERE / 'results.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
