"""JSON evidence: snapshot bundles, service resource exports, ledgers and reports."""

from __future__ import annotations

import json
from typing import Dict, List

from .. import config as C
from ..observations import Observation, make_observation
from ..timeutil import parse_ts
from .common import observation_from_record

# service_resource_export sections -> system id
_EXPORT_SYSTEM = {
    "account_service": "account_service",
    "analytics_platform": "analytics_platform",
    "backup_service": "backup_service",
    "export_service": "export_service",
    "integration_service": "integration_service",
    "search_service": "search_service",
    "security_service": "security_service",
    "support_platform": "support_platform",
}


def parse(ctx) -> List[Observation]:
    with open(ctx.path, encoding="utf-8") as fh:
        data = json.load(fh)
    out: List[Observation] = []
    src = f"json:{ctx.path.name}"
    if not isinstance(data, dict):
        return out

    # 1) records_assurance snapshot bundle
    if isinstance(data.get("snapshots"), list):
        for snap in data["snapshots"]:
            captured = parse_ts(snap.get("captured_at")) or ctx.evidence_time
            store = snap.get("data_store", "")
            coll = snap.get("collection", "")
            if store == "account_service" and coll == "customer_profiles" and not (snap.get("records") or []):
                ids = sorted({str(r.get("resource_id")) for r in snap.get("records") or [] if isinstance(r, dict)})
                out.append(make_observation(ctx.evidence_id, ctx.evidence_time, captured, "*", C.CAT_USER_PROFILE,
                                            "SCOPE", system_id="account_service",
                                            flags={"present_ids": ids, "table": "bundle:customer_profiles"},
                                            source=src, seq=ctx.next_seq()))
            for rec in snap.get("records") or []:
                obs = observation_from_record(ctx, coll, rec, captured, src, system_hint=_system_for_store(store))
                if obs:
                    out.append(obs)
        return out

    # 2) service resource export (readback gateway)
    if isinstance(data.get("service_exports"), dict):
        exported = parse_ts(data.get("exported_at")) or ctx.evidence_time
        for svc, body in data["service_exports"].items():
            if not isinstance(body, dict):
                continue
            for section, records in body.items():
                if not isinstance(records, list):
                    continue
                for rec in records:
                    obs = observation_from_record(ctx, f"{svc}:{section}", rec, exported, src,
                                                  system_hint=_EXPORT_SYSTEM.get(svc, svc))
                    if obs:
                        out.append(obs)
        return out

    # 3) flat record lists (ledgers, reports, manifests)
    if isinstance(data.get("records"), list):
        captured = parse_ts(data.get("captured_at") or data.get("exported_at")) or ctx.evidence_time
        coll = data.get("collection", "") or data.get("origin", "")
        for rec in data["records"]:
            obs = observation_from_record(ctx, coll, rec, captured, src)
            if obs:
                out.append(obs)
        return out
    return out


def _system_for_store(store: str) -> str:
    store = store or ""
    # operational record histories name the real system inside each record
    if store.endswith("_service") or store.endswith("_platform"):
        if store in {"backup_restore_service", "privacy_treatment_service", "support_storage_service",
                     "records_assurance_service"}:
            return ""
        return store
    return ""
