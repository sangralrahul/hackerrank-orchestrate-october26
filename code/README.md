# Ghost Data Still Exists: deletion audit agent

For every request in `dataset/deletion_requests.csv` the agent:

1. decides whether the person actually asked for deletion;
2. resolves the person's current and historical identifiers;
3. reconstructs each linked record's state at `audit_at` from 94 evidence files (SQL, JSON, logs, text, PDF, screenshots);
4. applies `deletion_rules.csv`;
5. returns `COMPLETE` / `INCOMPLETE` / `NOT_APPLICABLE` with ordered, evidence-cited steps.

## Setup

Requirements: Python 3.9+ (standard library only), plus two optional system tools for multimodal evidence:

```bash
# Debian/Ubuntu
sudo apt-get install -y tesseract-ocr poppler-utils
# macOS
brew install tesseract poppler
```

No `pip install` is needed. `sqlite3`, `csv`, `json` and `urllib` come with Python. Without Tesseract, screenshots are skipped. Without poppler, a built-in PDF text extractor is used.

## Run

From the repository root (the directory that contains `dataset/`):

```bash
python3 code/main.py                       # writes ./output.csv (518 rows) and validates it
python3 code/evaluation/main.py            # sample + custom-case evaluation, ablations -> code/evaluation/results.json
python3 code/main.py --explain case_34     # reasoning trace for one case
python3 code/main.py --requests dataset/sample_deletion_requests.csv --output /tmp/sample_pred.csv
```

Optional model layer (intent second opinion and vision extraction from screenshots):

```bash
export ANTHROPIC_API_KEY=...               # read from the environment only, never stored
python3 code/main.py --llm                 # GHOSTAUDIT_MODEL overrides the model name
```

The submitted output was produced with the model layer off, so it is fully deterministic (see `evaluation/usage_report.md`).

## Architecture

```
code/
├── main.py                  CLI: load → audit → write output.csv → contract validation
├── ghostaudit/
│   ├── loaders.py           typed readers for the dataset CSVs
│   ├── identity.py          identity graph: current + historical e-mails, phones, hashes, user ids
│   ├── observations.py      normalised fact model: (evidence, time, system, resource, category, state, identifiers)
│   ├── evidence/            one parser per format, all emitting Observations
│   │   ├── json_records.py  snapshot bundles, readback exports, ledgers, reports
│   │   ├── sql_snapshots.py executes SQL dumps in in-memory SQLite (primary / replica / inventory tables)
│   │   ├── logs.py          6 log syntaxes → events, joined by trace_id (identity is on *_started lines)
│   │   ├── textdocs.py      shift-handover notes
│   │   ├── pdfdocs.py       pdftotext + fallback extractor
│   │   ├── images.py        3-pass Tesseract OCR, OCR e-mail correction, optional vision model
│   │   └── docparse.py      business-document fact extraction (console type, rows, aliases, staff filter)
│   ├── activity.py          system_activity index: catalog links and aliases, deletion-worker batches
│   ├── intent.py            multilingual deletion-intent rules (+ optional model second opinion)
│   ├── linker.py            identity linking + joint batch resolution per reported_at group
│   ├── planner.py           deletion_rules.csv policy → FINDING / REMEDIATION / VERIFICATION, contract ordering
│   ├── engine.py            per-case state reconstruction at audit_at, evidence-time rules, citations
│   ├── llm.py               optional Anthropic client (urllib), disk cache, token/cost meter
│   └── scoring.py           status / step / field / sequence metrics
├── prompts/                 model prompts (intent, document extraction)
└── evaluation/              evaluation_report.md, usage_report.md, main.py, custom_cases/
```

### Key decisions

- **Claims vs. proof.** `reported_status` and activity `reported_result` are never used as state. State comes only from evidence captured at or before `audit_at`, and the latest observation wins. This is how re-created "ghost" profiles are caught. Evidence captured shortly after `audit_at` may help *identify* a resource id, but it never sets the state.
- **Linking.** A resource is tied to a case through identity tokens: e-mail (any case, current or historical), phone, e-mail hash or `user_N`. Log lines are joined on `trace_id` so the lookup key reaches the returned resource. Catalog `LINK_RESOURCE_IDENTITY` rows link document aliases such as statement numbers and file names. A catalog claim is rejected when the artifact itself shows a different person.
- **Batch resolution.** Some records keep no identifier, for example already-anonymised analytics or 404 profiles. These are tied to cases through the deletion-worker batch that sits around each `reported_at`. Every group of cases sharing a `reported_at` is resolved together. Each profile submission's result mirrors that case's reported status (COMPLETE↔SUCCESS, PARTIALLY_COMPLETE↔PARTIAL, NOT_STARTED↔FAILED). Resources owned by non-members are rejected as decoys.
- **Policy.** DELETE → remaining record; REDACT / ANONYMIZE → exposed PII; data flows → disable; backups → block restore and retain; SECURITY_AUDIT_LOG and LEGAL_HOLD → verify present. Tombstones are redacted, never deleted.
- **Output.** Steps are sorted by phase, then the operation order of the problem statement, then `system_id`, then `resource_id`. COMPLETE cases cite the proving file for every step. INCOMPLETE remediation and future checks have an empty `evidence_id`.

## Data handling

Only participant-facing files under `dataset/` are read. No labels are hard-coded. `sample_output.csv` is used only by the evaluation script. Secrets are read from environment variables only.
