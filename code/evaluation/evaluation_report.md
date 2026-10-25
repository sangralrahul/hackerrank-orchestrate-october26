# Evaluation Report — Ghost Data Still Exists deletion-audit agent

All numbers below are produced by `python3 code/evaluation/main.py` (raw values in
`code/evaluation/results.json`). The run is fully deterministic: two runs, one with a
cold OCR cache and one from a clean copy of the repository, produce byte-identical
`output.csv` files.

## 1. Method

| Set | Size | Ground truth |
| --- | --- | --- |
| Public sample (`sample_deletion_requests.csv`) | 32 cases, 193 gold steps | `sample_output.csv` |
| Custom scenarios (`code/evaluation/custom_cases/build.py`) | 5 cases, 13 gold steps | hand-written, in `expected_output.csv` built by the script |
| Full evaluation set (`deletion_requests.csv`) | 518 cases | hidden; checked with contract validation and manual trace review |

Metric definitions (implemented in `code/ghostaudit/scoring.py`):

- **Case-level status accuracy**: `deletion_status` equals gold.
- **Exact row accuracy**: status and the full ordered `steps_json` array equal gold (step objects compared on `phase, system_id, resource_id, operation, state, evidence_id`).
- **Step-level precision / recall / F1**: steps treated as a set of 6-field tuples per case, micro-averaged.
- **Field-level accuracy**: gold steps aligned with predicted steps by position; share of each field that matches.
- **Sequence accuracy**: exact ordered match of the step list; plus a softer **sequence similarity** (difflib ratio of the ordered tuple lists, averaged per case).

## 2. Results on the public sample (final version)

| Metric | Value |
| --- | --- |
| Case-level status accuracy | **100.0 %** (32/32) |
| Exact row accuracy | **87.5 %** (28/32) |
| Sequence exact accuracy | 87.5 % |
| Sequence similarity | 0.982 |
| Step precision | 0.995 |
| Step recall | 0.979 |
| Step F1 | **0.987** |

Field-level accuracy (position-aligned):

| phase | system_id | resource_id | operation | state | evidence_id |
| --- | --- | --- | --- | --- | --- |
| 0.984 | 0.959 | 0.953 | 0.959 | 0.959 | 0.979 |

Remaining sample misses (all four inspected by hand):

| Case | Miss | Why it is not derivable |
| --- | --- | --- |
| case_04 | `VERIFY_RECORD_ABSENT account_004` | `account_004` is not mentioned in any activity row or evidence file. |
| case_18 | `VERIFY_RECORD_PRESENT audit_record_6645` | `audit_record_6645` does not occur anywhere in the participant data. |
| case_20 | `VERIFY_RECORD_PRESENT audit_record_5569` | Same: the id occurs in no participant file. |
| case_23 | `account_410` vs predicted `account_7015` | Both are unlinked `FAILED` profile submissions in the same batch, and three members of that batch reported `NOT_STARTED`. The tie is broken by batch order, and this time it picked the wrong one. |

## 3. Results on the full evaluation set (518 cases)

| | Count |
| --- | --- |
| Rows written / expected | 518 / 518, sorted, header exact |
| `INCOMPLETE` / `COMPLETE` / `NOT_APPLICABLE` | 354 / 154 / 10 |
| Contract validation (`code/main.py::validate_output`) | OK: consecutive step numbers, phase rules, `[]` for N/A, empty `evidence_id` on future steps, every COMPLETE step cited |
| Resources linked by identity evidence / by batch slot / decoys rejected | 1039 / 400 / 302 |

Manual trace review (`python3 code/main.py --explain case_XX`) covered every multimodal showcase case (33–63) and samples of each family.

## 4. Custom evaluation cases (created by us)

Built by `code/evaluation/custom_cases/build.py`. They use a fictional mini-dataset with its own identities, activity, a readback log and two snapshot bundles, and they reuse the real `deletion_rules.csv`.

| Case | Scenario | Expected | Result |
| --- | --- | --- | --- |
| custom_01 | Request uses a **historical e-mail**. The readback log keys the lookup on the current e-mail (trace-id join). The profile is still present after a worker `SUCCESS` and a replication sync. | INCOMPLETE: remaining account + delete + verify, audit record verified present | ✅ exact |
| custom_02 | Readback **before** `audit_at` proves absence. A bundle captured 18 days **after** `audit_at` shows the profile again. | COMPLETE, citing the pre-audit file only | ✅ exact |
| custom_03 | `ACCOUNT_TOMBSTONE` still exposes name and e-mail. A security audit record exists. | INCOMPLETE: REDACT the tombstone (do not delete it), VERIFY_PRESENT the audit record | ✅ exact |
| custom_04 | **German** data-copy request ("Bitte nichts löschen…"). The workflow fields look like a completed deletion. | NOT_APPLICABLE, `[]` | ✅ exact (failed before fix, see §5) |
| custom_05 | Look-alike **decoy** e-mail (`mina.s.jr@…`) with a search hit, plus the subject's own connector still enabled. | INCOMPLETE: disable the flow; the decoy is ignored | ✅ exact |

Custom-set metrics: status accuracy 100 %, exact rows 5/5, step F1 1.0.

## 5. Failures found and improvements made

Step F1 on the public sample by development iteration:

| # | Change | Status acc. | Exact rows | Step F1 |
| --- | --- | --- | --- | --- |
| v1 | Parsers + identity linking + naive batch slots (assigned by position) | 1.000 | 0.344 | 0.879 |
| v2 | **Joint batch resolution.** A profile submission's `reported_result` mirrors the case's `reported_status` (COMPLETE↔SUCCESS, PARTIALLY_COMPLETE↔PARTIAL, NOT_STARTED↔FAILED). Resources owned by non-members are rejected as decoys. The finance register (order numbers) no longer fabricates `legal_hold` ids. | 1.000 | 0.625 | 0.953 |
| v3 | **Identification vs. state split.** Ids may come from evidence captured up to 3 days after `audit_at`; the state uses only evidence captured by `audit_at`. Retained records referenced only by later files become future checks. Users with several requests: a resource belongs to the request whose audit window first covers it. | 1.000 | 0.875 | 0.987 |
| v4 | Absence proof for accounts: empty profile collections and replica tables act as "scope" evidence. COMPLETE steps cite the case's own readback file. | 1.000 | 0.875 | 0.987 (fixed 13 uncited COMPLETE rows in the 518 set) |
| v5 | **Multimodal robustness.** List consoles no longer pass the subject's identity onto other rows (staff-owned exports, other customers' backups). An id must be read the same in at least 2 of 3 OCR passes, which removed the phantom `support_9638`. OCR'd e-mails snap to known addresses (`gmai.com` → `gmail.com`). A catalog claim is rejected when the screenshot shows another person's identifiers (`marketing_001`). | 1.000 | 0.875 | 0.987 (fixed cases 34, 52, 55, 63 in the 518 set) |
| v6 | **Multilingual negation.** custom_04 exposed "nichts löschen" being read as a deletion request. Added non-deletion overrides for DE/ES/PT/FR/JA/KO. | 1.000 | 0.875 | 0.987 (custom 4/5 → 5/5) |

Ablations on the public sample, from the final code:

| Variant | Exact rows | Step F1 |
| --- | --- | --- |
| Full system | 0.875 | 0.987 |
| Without status↔result matching | 0.813 | 0.977 |
| Without deletion-worker batch linking | 0.344 | 0.925 |

Other traps we found and handle:

1. Evidence captured after `audit_at` (for example bundle `evidence_02`, captured 09-17, showing re-created profiles for cases audited 09-15) is never used for state.
2. Upper-case e-mails (`BCARVALHO6517@GMAIL.COM`) are normalised; look-alike `.jr` addresses are not merged.
3. Readback logs carry the identity only on the `*_started` line, so lines are joined by `trace_id` before linking.
4. A later sync or restore that re-creates a profile (the "ghost") is caught by preferring the latest pre-audit observation over the worker's `SUCCESS` claim.

## 6. Known limitations

- Primary-profile ids: 246 of the 508 deletion cases are linked by identity evidence, 258 by batch slot and 4 have no profile in the data. For 102 of the slot-linked cases, two or more batch members share the same reported status, so the tie is broken by batch order and can be wrong (as in case_23).
- Showcase cases whose only identifiers appear in evidence captured weeks after `audit_at` are answered only from pre-audit facts, by design. If the hidden key used later evidence for ids, those cases will miss account or audit verification steps.
- The optional model layer (intent and vision extraction) did not run for the submitted output because no API key was available in the build environment (see `usage_report.md`).
