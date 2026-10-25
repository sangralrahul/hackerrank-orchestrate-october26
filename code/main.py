#!/usr/bin/env python3
"""Ghost Data Still Exists - deletion audit agent (CLI entry point).

    python3 code/main.py                     # audit dataset/deletion_requests.csv -> ./output.csv
    python3 code/main.py --requests dataset/sample_deletion_requests.csv --output /tmp/sample_pred.csv
    python3 code/main.py --llm               # enable optional model layer (needs ANTHROPIC_API_KEY)
    python3 code/main.py --explain case_33   # print the reasoning trace for one case
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))

from ghostaudit.engine import AuditEngine, write_output  # noqa: E402
from ghostaudit.intent import LLMIntentClassifier, RuleIntentClassifier  # noqa: E402
from ghostaudit.llm import LLMClient, make_doc_extractor  # noqa: E402
from ghostaudit.loaders import load_dataset  # noqa: E402


def validate_output(path: Path, expected_ids) -> list:
    import csv
    problems = []
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames != ["case_id", "deletion_status", "steps_json"]:
            problems.append(f"bad header {reader.fieldnames}")
        rows = list(reader)
    ids = [r["case_id"] for r in rows]
    from ghostaudit.engine import case_sort_key
    if ids != sorted(ids, key=case_sort_key):
        problems.append("rows not sorted by case_id")
    if sorted(ids) != sorted(expected_ids):
        problems.append(f"case ids mismatch: {len(ids)} rows vs {len(expected_ids)} requests")
    for r in rows:
        steps = json.loads(r["steps_json"])
        if r["deletion_status"] == "NOT_APPLICABLE" and steps:
            problems.append(f"{r['case_id']}: NOT_APPLICABLE with steps")
        if [s["step_no"] for s in steps] != list(range(1, len(steps) + 1)):
            problems.append(f"{r['case_id']}: step numbers not consecutive")
        if r["deletion_status"] == "COMPLETE" and any(s["phase"] != "VERIFICATION" for s in steps):
            problems.append(f"{r['case_id']}: COMPLETE with non-verification steps")
        if r["deletion_status"] == "INCOMPLETE":
            if any(s["phase"] != "FINDING" and s["evidence_id"] for s in steps):
                problems.append(f"{r['case_id']}: future step with evidence id")
    return problems


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Deletion audit agent")
    ap.add_argument("--dataset", default=str(REPO / "dataset"))
    ap.add_argument("--requests", default=None, help="requests CSV (default: dataset/deletion_requests.csv)")
    ap.add_argument("--output", default=str(REPO / "output.csv"))
    ap.add_argument("--llm", action="store_true", help="enable the optional model layer")
    ap.add_argument("--model", default=None)
    ap.add_argument("--explain", default=None, help="case_id to print a reasoning trace for")
    ap.add_argument("--usage-json", default=str(HERE / "evaluation" / "last_run_usage.json"))
    args = ap.parse_args(argv)

    t0 = time.time()
    dataset = Path(args.dataset)
    requests = Path(args.requests) if args.requests else dataset / "deletion_requests.csv"
    # every request file is used for batch disambiguation (never for answers)
    extra = [dataset / "deletion_requests.csv", dataset / "sample_deletion_requests.csv"]
    ds = load_dataset(dataset, requests, extra)

    client = LLMClient(model=args.model, enabled=args.llm or None)
    intent = RuleIntentClassifier()
    doc_extractor = None
    if client.enabled:
        intent = LLMIntentClassifier(client, (HERE / "prompts" / "intent_classification.md").read_text())
        doc_extractor = make_doc_extractor(client, (HERE / "prompts" / "document_extraction.md").read_text())
    print(f"[main] requests={len(ds.requests)} evidence_files={len(ds.evidence_files)} "
          f"llm={'on (' + client.model + ')' if client.enabled else 'off (deterministic)'}")

    engine = AuditEngine(ds, intent_classifier=intent, doc_extractor=doc_extractor, log=print)
    results = engine.run()
    write_output(results, Path(args.output))

    counts = {}
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1
    problems = validate_output(Path(args.output), [r.case_id for r in ds.requests])
    print(f"[main] wrote {args.output}: {len(results)} rows {counts}")
    print(f"[main] batch linking stats: {engine.batch_stats}")
    print(f"[main] contract validation: {'OK' if not problems else problems[:10]}")

    usage = {"requests": len(ds.requests), "seconds": round(time.time() - t0, 2), "llm_enabled": client.enabled,
             "model": client.model, "rows": client.meter.summary(), "llm_errors": client.errors}
    try:
        Path(args.usage_json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.usage_json).write_text(json.dumps(usage, indent=2))
    except OSError:
        pass

    if args.explain:
        for r in results:
            if r.case_id == args.explain:
                print(f"\n=== {r.case_id}: {r.status}")
                for n in r.notes:
                    print("  ", n)
                print(json.dumps(r.row()["steps_json"] and json.loads(r.row()["steps_json"]), indent=1))
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
