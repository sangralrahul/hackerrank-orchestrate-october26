# Token Usage and Cost Report — final full-dataset run

This report covers the run that produced the submitted `output.csv`:

```
python3 code/main.py        # 518 requests from dataset/deletion_requests.csv
```

Machine-readable record of the same run: `code/evaluation/last_run_usage.json`.

## Run configuration

| Item | Value |
| --- | --- |
| Requests processed | 518 |
| Evidence files parsed | 94 (24 SQL, 26 logs, 28 JSON, 1 text, 4 PDF, 11 PNG) |
| Observations extracted | ≈2,950 normalised facts |
| Wall-clock time | ≈25 s on one CPU (cold OCR cache), ≈20 s warm |
| LLM layer | **off**. The run was deterministic; no `ANTHROPIC_API_KEY` was available in the build environment. |

## Model usage (final run)

| Provider | Model / engine | Purpose | Calls | Input tokens | Output tokens | Est. cost (USD) |
| --- | --- | --- | --- | --- | --- | --- |
| Anthropic | claude-sonnet-4-5 (optional, disabled) | intent second opinion, vision extraction | 0 | 0 | 0 | 0.00 |
| Local | Tesseract 5.3.4 OCR | screenshot text (11 images × 3 page-segmentation passes) | 33 | n/a | n/a | 0.00 |
| Local | poppler `pdftotext` | PDF text (4 documents) | 4 | n/a | n/a | 0.00 |
| **Overall** | | | **0 LLM calls** | **0** | **0** | **0.00** |

| Per-request figure | Value |
| --- | --- |
| Total tokens | 0 |
| Average tokens per request | 0 |
| Estimated cost per request | $0.00 |

## Why the final run uses no LLM calls

Every decision the grader checks (identity resolution, state at `audit_at`, policy action, step ordering) follows fixed rules, so the core pipeline is code. Code is exact, reproducible and free. Model calls are reserved for two optional, bounded tasks:

1. **Intent second opinion** (`prompts/intent_classification.md`): one short call per request with roughly 230 input and 25 output tokens. The rule classifier already agrees with all 32 sample labels and all 5 custom cases. Explicit "do not delete" overrides always take priority over the model's answer.
2. **Vision extraction** (`prompts/document_extraction.md`): one call per screenshot or PDF (15 files). It returns operational records that can only *add* resources the OCR path missed.

## Projected usage if the model layer is enabled (`--llm`)

| Purpose | Calls | Input tokens (est.) | Output tokens (est.) | Cost at $3 / $15 per M tokens |
| --- | --- | --- | --- | --- |
| Intent | 518 | 518 × 230 ≈ 119,000 | 518 × 25 ≈ 13,000 | ≈ $0.55 |
| Document / vision extraction | 15 | 15 × 2,400 ≈ 36,000 | 15 × 400 ≈ 6,000 | ≈ $0.20 |
| **Total** | **533** | **≈155,000** | **≈19,000** | **≈ $0.75 (≈ $0.0015 per request)** |

Every model response is cached on disk, keyed by model and prompt hash (`code/cache/llm/`), so re-runs cost nothing and return the same answers. Token counts come from the API `usage` field and are written to `last_run_usage.json` per model and purpose.
