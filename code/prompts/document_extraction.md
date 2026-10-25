# Prompt: multimodal evidence extraction (PDF text / screenshots)

Used by `ghostaudit/llm.py::make_doc_extractor` (optional, `--llm`). Screenshots
are sent as images (vision), PDFs as extracted text. The output uses the same
"operational record" shape as the structured evidence, so it flows through the
same normaliser (`evidence/common.py`). OCR (Tesseract) + regex extraction is
always run; model records only ADD resources the OCR path did not find.

```
You are extracting facts from an internal Northstar Shop business artifact
(console screenshot, invoice, support attachment, register...). Do not judge
whether a deletion happened; only report what the artifact shows.

For every internal resource identifier visible (account_N, search_N,
analytics_N, support_N, export_N, data_flow_N, backup_N, audit_record_N,
tombstone_N, legal_hold_N, marketing_N, object_N) output one record:

{"resource_id": "...", "system": "<owning service>", "record_type":
 "USER_PROFILE|SEARCH_INDEX_ENTRY|ANALYTICS_PROFILE|SUPPORT_MESSAGE|SUPPORT_ATTACHMENT|USER_EXPORT|DATA_FLOW|BACKUP_ARCHIVE|SECURITY_AUDIT_LOG|ACCOUNT_TOMBSTONE|LEGAL_HOLD_RECORD|MARKETING_PROFILE",
 "result": "FOUND|NOT_FOUND|IDENTIFIABLE|ANONYMIZED|REDACTED|ACTIVE|DISABLED|BLOCKED",
 "lookup_value": "<the customer e-mail / user_N / phone shown with it, or null>",
 "recorded_at": "<ISO timestamp shown on the artifact, or null>"}

Ignore staff e-mail addresses (@northstar.shop, @company.com).
OCR text (may be noisy): {{DOCUMENT_TEXT}}

Reply with a JSON array only.
```
