"""H7 local determinism and boundary-audit fixtures."""

from __future__ import annotations

import pytest

from harness_everythings.core.context import ContextRoutingError, build_context_routes
from harness_everythings.core.domain_packs import derive_software_output_contract
from harness_everythings.core.approvals_roles import make_plan_approval
from harness_everythings.core.entities import make_envelope
from harness_everythings.core.evidence import make_evidence
from harness_everythings.core.identity import canonical_bytes, content_fingerprint
from harness_everythings.storage.paths import PathBoundaryError, normalize_rel_path


NOW = "2026-08-16T00:00:00Z"


def approved_plan() -> dict:
    return make_envelope(
        "plan",
        {"fixture": "h7"},
        "fixture:h7",
        NOW,
        fields={
            "goals": ["one goal", "two goal"],
            "scope": {"domain_pack": "software-engineering"},
            "decisions": [],
            "risks": [],
            "stages": ["requirements"],
            "acceptance_strategy": {"required_evidence": ["tests"]},
            "approval_state": "approved",
        },
    ).to_record()


def test_three_runs_have_identical_contract_bytes_and_fingerprints():
    plan = approved_plan()
    evidence = make_evidence(actor="user", action="plan-approval", conclusion_kind="user_confirmed", supporting_refs=[plan["entity_id"]], verification_level="fixture_verified", source_ref="fixture:h7", now=NOW)
    approval = make_plan_approval(plan, requester="role:governance", approver="user", target_owner="workspace:h7", evidence_refs=[evidence["entity_id"]], decided_at=NOW)
    outputs = [derive_software_output_contract(plan, NOW, approval=approval, target_owner="workspace:h7", evidence_records=[evidence]) for _ in range(3)]
    assert [canonical_bytes(item) for item in outputs] == [canonical_bytes(outputs[0])] * 3
    assert len({content_fingerprint(item) for item in outputs}) == 1


def test_context_audit_rejects_private_history_and_unowned_sources():
    roles = [{"role_id": "role:one", "role_name": "one"}, {"role_id": "role:two", "role_name": "two"}]
    sources = [
        {"source_ref": "profile:one", "source_fingerprint": "sha256:one", "sensitivity": "public", "estimated_tokens": 2, "authorized_role_ids": ["role:one"], "content": "not embedded"},
        {"source_ref": "history:private", "source_fingerprint": "sha256:history", "sensitivity": "public", "estimated_tokens": 2, "authorized_role_ids": ["role:one"]},
        {"source_ref": "unowned:source", "source_fingerprint": "sha256:unowned", "sensitivity": "public", "estimated_tokens": 2},
    ]
    routes = build_context_routes(roles, list(reversed(sources)), max_tokens=4)
    first = routes["routes"][0]
    assert first["source_refs"] == ["profile:one"]
    assert "not embedded" not in str(routes)
    assert any(item["reason"] == "forbidden_history_private_or_credential_source" for item in routes["rejected_sources"])
    assert any(item["reason"] == "source_has_no_explicit_role_or_purpose_authorization" for item in routes["rejected_sources"])
    with pytest.raises(ContextRoutingError):
        build_context_routes(roles, [{"source_ref": "x", "source_fingerprint": "sha256:x", "sensitivity": "public", "estimated_tokens": -1, "authorized_role_ids": ["role:one"]}])


def test_path_boundary_rejects_escape_and_absolute_targets():
    for value in ("../outside.txt", "C:\\outside.txt", "/outside.txt"):
        with pytest.raises(PathBoundaryError):
            normalize_rel_path(value)
