"""合同测试：CLI 骨架、Schema 封套与视图合同。"""

from __future__ import annotations

import json

import pytest

from harness_everythings.cli.errors import ERROR_CATEGORIES, ExitCode
from harness_everythings.cli.main import COMMAND_RISK, build_parser, main
from harness_everythings.core.entities import make_envelope
from harness_everythings.core.identity import content_fingerprint
from harness_everythings.core.schema_registry import validate


def envelope_record(entity_type, fields, seed_extra=None):
    seed = {"t": entity_type, "x": seed_extra or 1}
    e = make_envelope(entity_type, seed, "user:contract-test", "2026-08-16T00:00:00Z", fields=fields)
    return e.to_record()


class TestCliContract:
    def test_doctor_schemas_ok(self, capsys):
        code = main(["doctor-schemas"])
        out = capsys.readouterr().out
        assert code == 0
        payload = json.loads(out)
        assert payload["ok"] is True
        assert payload["schemas_checked"] == 48

    def test_doctor_schemas_markdown_format(self, capsys):
        code = main(["doctor-schemas", "--format", "markdown"])
        out = capsys.readouterr().out
        assert code == 0
        assert out.startswith("```json")

    def test_uninitialized_status_is_safe(self, capsys):
        code = main(["status", "--workspace", "."])
        assert code == int(ExitCode.OK)
        payload = json.loads(capsys.readouterr().out)
        assert payload["initialized"] is False

    def test_write_commands_default_dry_run(self):
        for cmd in ("init", "reconcile", "upgrade", "retire"):
            meta = COMMAND_RISK[cmd]
            assert meta["dry_run_default"] is True, cmd
            assert meta["risk"] == "write", cmd

    def test_read_commands_no_write(self):
        for cmd in ("inspect", "diff", "doctor", "doctor-schemas", "status"):
            assert COMMAND_RISK[cmd]["risk"] == "read", cmd

    def test_exit_codes_unique(self):
        codes = [int(c) for c in ERROR_CATEGORIES.values()]
        assert len(codes) == len(set(codes))
        assert int(ExitCode.OK) == 0

    def test_init_requires_new_or_existing_mode(self):
        parser = build_parser()
        assert parser.parse_args(["init", "new"]).mode == "new"
        assert parser.parse_args(["init", "existing"]).mode == "existing"
        with pytest.raises(SystemExit):
            parser.parse_args(["init"])


class TestEntitySchemaContract:
    def test_workspace_valid(self):
        record = envelope_record("workspace", {
            "workspace_name": "demo",
            "workspace_kind": "new",
            "lifecycle_state": "proposed",
            "enabled_domain_packs": [],
            "config_version": "1.0",
        })
        validate("workspace", record)

    def test_plan_valid(self):
        record = envelope_record("plan", {
            "goals": ["g1"],
            "scope": {},
            "decisions": [],
            "risks": [],
            "stages": ["s1"],
            "acceptance_strategy": {},
            "approval_state": "proposed",
        })
        validate("plan", record)

    def test_output_contract_valid(self):
        record = envelope_record("output-contract", {
            "derived_from_plan": "plan:1",
            "observable_requirements": [{"req": "r"}],
            "acceptance_conditions": [{"cond": "c"}],
        })
        validate("output-contract", record)

    def test_role_valid(self):
        record = envelope_record("role", {
            "role_id": "role:e42d44ac93594fe",
            "role_contract_version": "1.0",
            "role_name": "implementer",
            "mission": "实现修改",
            "permissions": ["read:source"],
            "lifecycle_state": "proposed",
            "owns": ["src/"],
            "forbids": ["secrets/"],
            "capabilities": ["deterministic"],
            "input_contract_refs": ["workspace-profile@1.0"],
            "output_contract_refs": ["artifact@1.0"],
            "artifact_obligations": [{"kind": "artifact", "contract_ref": "artifact@1.0", "required": True}],
            "evidence_obligations": [{"kind": "evidence", "contract_ref": "evidence@1.0", "required": True}],
            "verification": {"methods": ["schema"], "independent": True, "evidence_required": True},
            "stop_conditions": ["missing-evidence"],
            "dependencies": [],
            "concurrency_boundaries": {"shared": [], "exclusive": ["src/"]},
            "generation_origin": "kernel-default",
            "lifecycle_history": [{"from_state": "none", "to_state": "proposed", "changed_at": "2026-08-16T00:00:00Z", "evidence_ref": "evidence:proposal", "transition_fingerprint": "sha256:transition"}],
            "contract_fingerprint": "sha256:contract",
        })
        validate("role", record)

    def test_task_valid(self):
        record = envelope_record("task", {
            "state": "proposed",
            "owner_role": "role:x",
            "budget": {"max_tokens": 100},
            "retry_policy": "safe_auto",
            "idempotency_key": "sha256:k",
            "transitions": [],
        })
        validate("task", record)

    def test_artifact_valid(self):
        record = envelope_record("artifact", {
            "artifact_kind": "text",
            "content_fingerprint": "sha256:x",
            "sensitivity": "public",
        })
        record["record_fingerprint"] = content_fingerprint({key: value for key, value in record.items() if key != "record_fingerprint"})
        validate("artifact", record)

    def test_evidence_valid(self):
        record = envelope_record("evidence", {
            "actor": "role:x",
            "action": "verify",
            "conclusion_kind": "observed",
            "supporting_refs": ["artifact:1"],
            "verification_level": "unit_tested",
            "user_confirmed": False,
        })
        record["record_fingerprint"] = content_fingerprint({key: value for key, value in record.items() if key != "record_fingerprint"})
        validate("evidence", record)

    def test_approval_valid(self):
        record = envelope_record("approval", {
            "target_ref": "plan:1",
            "scope": "work_product",
            "requester": "role:x",
            "approver": "user",
            "decision": "approved",
            "decided_at": "2026-08-16T00:00:00Z",
        })
        record["approval_fingerprint"] = content_fingerprint(
            {key: value for key, value in record.items() if key != "approval_fingerprint"}
        )
        validate("approval", record)

    def test_handoff_valid(self):
        record = envelope_record("handoff", {
            "checkpoint": {},
            "incomplete_items": ["item"],
            "resume_preconditions": ["pre"],
            "receiver": "role:y",
        })
        record["fingerprint"] = content_fingerprint(
            {key: value for key, value in record.items() if key != "fingerprint"}
        )
        validate("handoff", record)

    def test_governance_proposal_valid(self):
        record = envelope_record("governance-proposal", {
            "proposed_change": {},
            "evidence_refs": ["evidence:1"],
            "risks": [],
            "rollback_plan": {},
            "approval_state": "proposed",
        })
        validate("governance-proposal", record)

    def test_profile_record_valid(self):
        record = envelope_record("profile-record", {
            "status": "observed",
            "sensitivity": "public",
            "fact_key": "workspace.kind",
            "fact_value": "directory",
        })
        validate("profile-record", record)

    def test_application_manifest_schema_valid(self):
        record = {
            "schema_version": "1.0",
            "entity_type": "application-manifest",
            "workspace_fingerprint": "sha256:a",
            "idempotency_key": "sha256:b",
            "created_at": "2026-08-16T00:00:00Z",
            "writes": [
                {"rel": "a.json", "exclusive": False, "target_fingerprint": "sha256:c"}
            ],
        }
        validate("application-manifest", record)
