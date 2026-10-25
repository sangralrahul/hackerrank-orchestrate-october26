"""Builds a small self-contained dataset with hand-written expected answers.

Each scenario exercises one failure mode we hit (or feared) while building the agent.
All people, e-mails and ids are fictional and do not exist in the challenge data.
"""

from __future__ import annotations

import csv
import json
import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent

SCENARIOS = [
    {
        "case_id": "custom_01",
        "title": "Historical e-mail + ghost profile recreated by a sync after deletion",
        "why": "Request uses the OLD address; the readback log only knows the current one through the "
               "trace join, and a replica still holds the profile after the worker reported SUCCESS.",
    },
    {
        "case_id": "custom_02",
        "title": "Evidence captured after audit_at must not change the answer",
        "why": "Readback before audit_at proves absence; a snapshot captured two weeks after audit_at shows "
               "the profile again. The case must stay COMPLETE and cite the pre-audit file.",
    },
    {
        "case_id": "custom_03",
        "title": "Tombstone still exposing direct identifiers + retained security record",
        "why": "ACCOUNT_TOMBSTONE must be REDACTED (not deleted) and the SECURITY_AUDIT_LOG must be "
               "verified PRESENT, never deleted.",
    },
    {
        "case_id": "custom_04",
        "title": "Non-deletion request in German with deletion-looking workflow fields",
        "why": "An export request must be NOT_APPLICABLE with [] even though the workflow says COMPLETE.",
    },
    {
        "case_id": "custom_05",
        "title": "Look-alike decoy e-mail and active connector",
        "why": "A search hit for name.jr@... belongs to someone else and must be ignored; the subject's own "
               "connector is still enabled and must be disabled before verification.",
    },
]


def build(target: Path) -> Path:
    if target.exists():
        shutil.rmtree(target)
    (target / "evidence" / "logs").mkdir(parents=True)
    (target / "evidence" / "records").mkdir(parents=True)
    repo_dataset = HERE.parents[2] / "dataset"
    shutil.copy(repo_dataset / "deletion_rules.csv", target / "deletion_rules.csv")

    w = lambda name, header, rows: _write_csv(target / name, header, rows)
    w("user_identities.csv", ["user_id", "name", "email", "phone", "email_hash", "updated_at"], [
        ["user_9001", "Tove Aaltonen", "tove.new@example.test", "+358 40 111 2233", "a" * 64, "2026-06-01T00:00:00Z"],
        ["user_9002", "Bruno Kessler", "bruno.k@example.test", "+49 151 222 3344", "b" * 64, "2026-01-01T00:00:00Z"],
        ["user_9003", "Ines Duarte", "ines.d@example.test", "+351 91 333 4455", "c" * 64, "2026-01-01T00:00:00Z"],
        ["user_9004", "Lars Weber", "lars.w@example.test", "+49 160 444 5566", "d" * 64, "2026-01-01T00:00:00Z"],
        ["user_9005", "Mina Sato", "mina.s@example.test", "+81 90 555 6677", "e" * 64, "2026-01-01T00:00:00Z"],
    ])
    w("user_audit_log.csv", ["audit_id", "user_id", "event_time", "field_name", "old_value", "new_value"], [
        ["audit_9001", "user_9001", "2026-06-01T00:00:00Z", "email", "tove.old@example.test", "tove.new@example.test"],
    ])
    act = ["activity_id", "event_time", "event_type", "source_system", "target_system", "resource_id",
           "reported_result", "details"]
    w("system_activity.csv", act, [
        ["activity_9001", "2026-08-01T09:00:00Z", "DELETE_RECORD", "deletion_worker", "account_service", "account_9001",
         "SUCCESS", "Deletion worker submitted primary profile account_9001 to account_service."],
        ["activity_9002", "2026-08-01T11:00:00Z", "SYNC_RECORD", "account_service", "account_replication_cluster",
         "account_9001", "SUCCESS", "Replication worker reported its final checkpoint for account_9001."],
    ])
    log = [
        "2026-08-10T08:00:00Z level=INFO service=readback_collector event=export_started target_count=6 mode=point_in_time",
        # custom_01: lookup by CURRENT e-mail; request uses the historical one
        "2026-08-10T08:01:00Z level=DEBUG service=account_service trace_id=rb-1 event=profile_lookup_started resource_id=account_9001 lookup_key=tove.new@example.test",
        "2026-08-10T08:01:00Z level=INFO service=account_service trace_id=rb-1 event=profile_lookup_completed resource_id=account_9001 rows=1 http_status=200 customer_reference=null email=tove.new@example.test phone=null",
        "2026-08-10T08:02:00Z level=DEBUG service=security_service trace_id=rb-2 event=audit_record_read_started resource_id=audit_record_9001 lookup_key=user_9001",
        "2026-08-10T08:02:00Z level=INFO service=security_service trace_id=rb-2 event=audit_record_read_completed resource_id=audit_record_9001 rows=1 subject_reference=user_9001 event_code=ACCOUNT_DELETION_RECORDED retention_until=2031-12-31T23:59:59Z",
        # custom_02: absent before audit
        "2026-08-10T08:03:00Z level=DEBUG service=account_service trace_id=rb-3 event=profile_lookup_started resource_id=account_9002 lookup_key=bruno.k@example.test",
        "2026-08-10T08:03:00Z level=INFO service=account_service trace_id=rb-3 event=profile_lookup_completed resource_id=account_9002 rows=0 http_status=404",
        # custom_05: connector enabled + decoy search hit
        "2026-08-10T08:04:00Z level=DEBUG service=integration_service trace_id=rb-4 event=connector_config_read_started resource_id=data_flow_9005 lookup_key=mina.s@example.test",
        "2026-08-10T08:04:00Z level=INFO service=integration_service trace_id=rb-4 event=connector_config_read_completed resource_id=data_flow_9005 enabled=true last_delivery_status=delivered connector_name=customer_events_connector customer_email=mina.s@example.test customer_reference=null",
        "2026-08-10T08:05:00Z level=DEBUG service=search_service trace_id=rb-5 event=index_query_started resource_id=search_9999 lookup_key=mina.s.jr@example.test",
        "2026-08-10T08:05:00Z level=INFO service=search_service trace_id=rb-5 event=index_query_completed resource_id=search_9999 result=hit total_hits=1 index=customer_directory_v3 email=mina.s.jr@example.test customer_reference=null",
        "2026-08-10T08:06:00Z level=DEBUG service=account_service trace_id=rb-6 event=profile_lookup_started resource_id=account_9005 lookup_key=mina.s@example.test",
        "2026-08-10T08:06:00Z level=INFO service=account_service trace_id=rb-6 event=profile_lookup_completed resource_id=account_9005 rows=0 http_status=404",
        "2026-08-10T08:07:00Z level=INFO service=readback_collector event=export_completed queries=6 dropped=0",
    ]
    (target / "evidence" / "logs" / "readback_custom.log").write_text("\n".join(log) + "\n")
    bundle_late = {"exported_at": "2026-08-30T00:00:00Z", "source": "records_assurance_service", "snapshots": [
        {"captured_at": "2026-08-30T00:00:00Z", "data_store": "account_service", "collection": "customer_profiles",
         "records": [{"resource_id": "account_9002", "customer_reference": "user_9002", "full_name": "Bruno Kessler",
                      "email": "bruno.k@example.test", "phone": "+49 151 222 3344", "account_status": "ACTIVE"}]}]}
    (target / "evidence" / "records" / "late_bundle.json").write_text(json.dumps(bundle_late, indent=1))
    bundle_tomb = {"exported_at": "2026-08-10T09:00:00Z", "source": "records_assurance_service", "snapshots": [
        {"captured_at": "2026-08-10T09:00:00Z", "data_store": "account_service", "collection": "customer_profiles",
         "records": []},
        {"captured_at": "2026-08-10T09:00:00Z", "data_store": "identity_service",
         "collection": "deleted_customer_references",
         "records": [{"resource_id": "tombstone_9003", "subject_reference": "user_9003", "name": "Ines Duarte",
                      "email": "ines.d@example.test", "phone": None, "email_hash": None, "account_status": "DELETED"}]},
        {"captured_at": "2026-08-10T09:00:00Z", "data_store": "security_service", "collection": "security_audit_events",
         "records": [{"resource_id": "audit_record_9003", "subject_reference": "user_9003",
                      "event_name": "account_deletion_reviewed", "actor_type": "automated_privacy_workflow"}]}]}
    (target / "evidence" / "records" / "tombstone_bundle.json").write_text(json.dumps(bundle_tomb, indent=1))
    w("evidence_files.csv", ["file_id", "file_path", "origin", "captured_at"], [
        ["evidence_901", "evidence/logs/readback_custom.log", "service_readback_logs", "2026-08-10T08:07:00Z"],
        ["evidence_902", "evidence/records/late_bundle.json", "records_assurance_snapshot_bundle", "2026-08-30T00:00:00Z"],
        ["evidence_903", "evidence/records/tombstone_bundle.json", "records_assurance_snapshot_bundle", "2026-08-10T09:00:00Z"],
    ])
    req = ["case_id", "email", "requested_at", "reported_status", "reported_at", "audit_at", "request_text"]
    w("deletion_requests.csv", req, [
        ["custom_01", "tove.old@example.test", "2026-07-30T10:00:00Z", "COMPLETE", "2026-08-01T10:00:00Z",
         "2026-08-12T10:00:00Z", "I changed my e-mail some time ago. Please delete my account under both addresses."],
        ["custom_02", "bruno.k@example.test", "2026-07-30T10:00:00Z", "COMPLETE", "2026-07-31T10:00:00Z",
         "2026-08-12T10:00:00Z", "Please erase my account and personal data."],
        ["custom_03", "ines.d@example.test", "2026-07-30T10:00:00Z", "PARTIALLY_COMPLETE", "2026-07-31T11:00:00Z",
         "2026-08-12T10:00:00Z", "Please delete my account; keep only the internal reference you need."],
        ["custom_04", "lars.w@example.test", "2026-07-30T10:00:00Z", "COMPLETE", "2026-07-31T12:00:00Z",
         "2026-08-12T10:00:00Z", "Bitte senden Sie mir eine Kopie meiner Daten. Bitte nichts löschen, ich nutze das Konto weiter."],
        ["custom_05", "mina.s@example.test", "2026-07-30T10:00:00Z", "COMPLETE", "2026-07-31T13:00:00Z",
         "2026-08-12T10:00:00Z", "Delete my account. A similar address with .jr is my brother's, not mine."],
    ])
    s = lambda n, ph, sysid, rid, op, st, ev: {"step_no": n, "phase": ph, "system_id": sysid, "resource_id": rid,
                                               "operation": op, "state": st, "evidence_id": ev}
    expected = {
        "custom_01": ("INCOMPLETE", [
            s(1, "FINDING", "account_service", "account_9001", "REPORT_REMAINING_RECORD", "PRESENT", "evidence_901"),
            s(2, "REMEDIATION", "account_service", "account_9001", "DELETE_RECORD", "ABSENT", ""),
            s(3, "VERIFICATION", "account_service", "account_9001", "VERIFY_RECORD_ABSENT", "ABSENT", ""),
            s(4, "VERIFICATION", "security_service", "audit_record_9001", "VERIFY_RECORD_PRESENT", "PRESENT", "")]),
        "custom_02": ("COMPLETE", [
            s(1, "VERIFICATION", "account_service", "account_9002", "VERIFY_RECORD_ABSENT", "ABSENT", "evidence_901")]),
        "custom_03": ("INCOMPLETE", [
            s(1, "FINDING", "identity_service", "tombstone_9003", "REPORT_EXPOSED_PII", "PRESENT", "evidence_903"),
            s(2, "REMEDIATION", "identity_service", "tombstone_9003", "REDACT_RECORD", "REDACTED", ""),
            s(3, "VERIFICATION", "security_service", "audit_record_9003", "VERIFY_RECORD_PRESENT", "PRESENT", ""),
            s(4, "VERIFICATION", "identity_service", "tombstone_9003", "VERIFY_RECORD_REDACTED", "REDACTED", "")]),
        "custom_04": ("NOT_APPLICABLE", []),
        "custom_05": ("INCOMPLETE", [
            s(1, "FINDING", "integration_service", "data_flow_9005", "REPORT_ACTIVE_DATA_FLOW", "ACTIVE", "evidence_901"),
            s(2, "REMEDIATION", "integration_service", "data_flow_9005", "DISABLE_DATA_FLOW", "DISABLED", ""),
            s(3, "VERIFICATION", "account_service", "account_9005", "VERIFY_RECORD_ABSENT", "ABSENT", ""),
            s(4, "VERIFICATION", "integration_service", "data_flow_9005", "VERIFY_DATA_FLOW_DISABLED", "DISABLED", "")]),
    }
    w("expected_output.csv", ["case_id", "deletion_status", "steps_json"],
      [[k, v[0], json.dumps(v[1], separators=(",", ":"))] for k, v in sorted(expected.items())])
    return target


def _write_csv(path: Path, header, rows):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        wr = csv.writer(fh)
        wr.writerow(header)
        wr.writerows(rows)


if __name__ == "__main__":
    print(build(HERE / "dataset"))
