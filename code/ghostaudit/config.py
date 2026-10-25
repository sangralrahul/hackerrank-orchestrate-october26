"""Constants shared by the pipeline: allowed operations, ordering, categories."""

from __future__ import annotations

# --------------------------------------------------------------------------- #
# Output contract
# --------------------------------------------------------------------------- #
OUTPUT_COLUMNS = ["case_id", "deletion_status", "steps_json"]
STATUS_COMPLETE = "COMPLETE"
STATUS_INCOMPLETE = "INCOMPLETE"
STATUS_NOT_APPLICABLE = "NOT_APPLICABLE"

PHASE_FINDING = "FINDING"
PHASE_REMEDIATION = "REMEDIATION"
PHASE_VERIFICATION = "VERIFICATION"
PHASE_ORDER = {PHASE_FINDING: 0, PHASE_REMEDIATION: 1, PHASE_VERIFICATION: 2}

# Operation order inside each phase follows the table of the problem statement.
OPERATION_ORDER = {
    # findings
    "REPORT_REMAINING_RECORD": 0,
    "REPORT_EXPOSED_PII": 1,
    "REPORT_ACTIVE_DATA_FLOW": 2,
    "REPORT_RESTORE_RISK": 3,
    # remediation
    "DISABLE_DATA_FLOW": 0,
    "BLOCK_RESTORE": 1,
    "DELETE_RECORD": 2,
    "REDACT_RECORD": 3,
    "ANONYMIZE_RECORD": 4,
    # verification
    "VERIFY_RECORD_ABSENT": 0,
    "VERIFY_RECORD_PRESENT": 1,
    "VERIFY_RECORD_REDACTED": 2,
    "VERIFY_RECORD_ANONYMIZED": 3,
    "VERIFY_DATA_FLOW_DISABLED": 4,
    "VERIFY_RESTORE_BLOCKED": 5,
}

OPERATION_STATE = {
    "REPORT_REMAINING_RECORD": "PRESENT",
    "REPORT_EXPOSED_PII": "PRESENT",
    "REPORT_ACTIVE_DATA_FLOW": "ACTIVE",
    "REPORT_RESTORE_RISK": "ACTIVE",
    "DISABLE_DATA_FLOW": "DISABLED",
    "BLOCK_RESTORE": "BLOCKED",
    "DELETE_RECORD": "ABSENT",
    "REDACT_RECORD": "REDACTED",
    "ANONYMIZE_RECORD": "ANONYMIZED",
    "VERIFY_RECORD_ABSENT": "ABSENT",
    "VERIFY_RECORD_PRESENT": "PRESENT",
    "VERIFY_RECORD_REDACTED": "REDACTED",
    "VERIFY_RECORD_ANONYMIZED": "ANONYMIZED",
    "VERIFY_DATA_FLOW_DISABLED": "DISABLED",
    "VERIFY_RESTORE_BLOCKED": "BLOCKED",
}

OPERATION_PHASE = {
    op: (PHASE_FINDING if op.startswith("REPORT_") else PHASE_VERIFICATION if op.startswith("VERIFY_") else PHASE_REMEDIATION)
    for op in OPERATION_ORDER
}

# --------------------------------------------------------------------------- #
# Data categories (the vocabulary of deletion_rules.csv plus a few evidence-only
# labels that map onto those rules).
# --------------------------------------------------------------------------- #
CAT_USER_PROFILE = "USER_PROFILE"
CAT_SECONDARY_IDENTIFIER = "SECONDARY_IDENTIFIER"
CAT_SEARCH_INDEX_ENTRY = "SEARCH_INDEX_ENTRY"
CAT_CACHE_ENTRY = "CACHE_ENTRY"
CAT_SUPPORT_MESSAGE = "SUPPORT_MESSAGE"
CAT_SUPPORT_ATTACHMENT = "SUPPORT_ATTACHMENT"
CAT_ANALYTICS_PROFILE = "ANALYTICS_PROFILE"
CAT_PRODUCT_TELEMETRY = "PRODUCT_TELEMETRY"
CAT_USER_EXPORT = "USER_EXPORT"
CAT_MARKETING_PROFILE = "MARKETING_PROFILE"
CAT_EMAIL_DELIVERY_LOG = "EMAIL_DELIVERY_LOG"
CAT_SECURITY_AUDIT_LOG = "SECURITY_AUDIT_LOG"
CAT_LEGAL_HOLD_RECORD = "LEGAL_HOLD_RECORD"
CAT_BACKUP_ARCHIVE = "BACKUP_ARCHIVE"
CAT_ACCOUNT_TOMBSTONE = "ACCOUNT_TOMBSTONE"
CAT_DATA_FLOW = "DATA_FLOW"  # evidence label used by the operational records

# Resource-id prefix -> (default system_id, default category)
RESOURCE_PREFIX_INFO = {
    "account": ("account_service", CAT_USER_PROFILE),
    "search": ("search_service", CAT_SEARCH_INDEX_ENTRY),
    "analytics": ("analytics_platform", CAT_ANALYTICS_PROFILE),
    "support": ("support_platform", CAT_SUPPORT_MESSAGE),
    "export": ("export_service", CAT_USER_EXPORT),
    "data_flow": ("integration_service", CAT_DATA_FLOW),
    "backup": ("backup_service", CAT_BACKUP_ARCHIVE),
    "audit_record": ("security_service", CAT_SECURITY_AUDIT_LOG),
    "tombstone": ("identity_service", CAT_ACCOUNT_TOMBSTONE),
    "legal_hold": ("legal_service", CAT_LEGAL_HOLD_RECORD),
    "cache": ("cache_service", CAT_CACHE_ENTRY),
    "email_delivery": ("email_delivery_service", CAT_EMAIL_DELIVERY_LOG),
    "marketing": ("marketing_platform", CAT_MARKETING_PROFILE),
    "object": ("object_storage", CAT_SUPPORT_ATTACHMENT),
}

# Evidence "record_type" labels used by operational records / handover notes.
RECORD_TYPE_TO_CATEGORY = {
    "USER_PROFILE": CAT_USER_PROFILE,
    "SEARCH_INDEX_ENTRY": CAT_SEARCH_INDEX_ENTRY,
    "ANALYTICS_PROFILE": CAT_ANALYTICS_PROFILE,
    "PRODUCT_TELEMETRY": CAT_PRODUCT_TELEMETRY,
    "SUPPORT_MESSAGE": CAT_SUPPORT_MESSAGE,
    "SUPPORT_ATTACHMENT": CAT_SUPPORT_ATTACHMENT,
    "USER_EXPORT": CAT_USER_EXPORT,
    "DATA_FLOW": CAT_DATA_FLOW,
    "BACKUP_ARCHIVE": CAT_BACKUP_ARCHIVE,
    "SECURITY_AUDIT_LOG": CAT_SECURITY_AUDIT_LOG,
    "ACCOUNT_TOMBSTONE": CAT_ACCOUNT_TOMBSTONE,
    "LEGAL_HOLD_RECORD": CAT_LEGAL_HOLD_RECORD,
    "MARKETING_PROFILE": CAT_MARKETING_PROFILE,
    "CACHE_ENTRY": CAT_CACHE_ENTRY,
    "EMAIL_DELIVERY_LOG": CAT_EMAIL_DELIVERY_LOG,
    "SECONDARY_IDENTIFIER": CAT_SECONDARY_IDENTIFIER,
}

# Observed states (normalised vocabulary used by the policy engine).
ST_PRESENT = "PRESENT"          # record exists and is not treated
ST_ABSENT = "ABSENT"            # record does not exist
ST_EXPOSED = "EXPOSED"          # record exists and still carries prohibited identifiers
ST_REDACTED = "REDACTED"        # record exists, prohibited identifiers removed
ST_ANONYMIZED = "ANONYMIZED"    # record exists, unlinked from the subject
ST_ACTIVE = "ACTIVE"            # data flow enabled / restore allowed
ST_DISABLED = "DISABLED"        # data flow disabled
ST_BLOCKED = "BLOCKED"          # restore blocked
ST_RETAINED = "RETAINED"        # policy-required record present

# Deletion-worker batch timing (minutes relative to the workflow's reported_at).
BATCH_ACCOUNT_WINDOW = (-85, -35)
BATCH_SHOWCASE_WINDOW = (-15, 15)
BATCH_POLICY_WINDOW = (-35, -1)
BATCH_PROBLEM_WINDOW = (15, 160)

DEFAULT_LLM_MODEL = "claude-sonnet-4-5"

# Evidence captured after audit_at may identify resources of the same review cycle (never their state).
LATER_EVIDENCE_HORIZON_DAYS = 3
