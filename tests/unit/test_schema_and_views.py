"""单元测试：Schema 注册表、能力注册合同与 Markdown 视图。"""

from __future__ import annotations

import pytest

from harness_everythings.adapters.capabilities import (
    CapabilityError,
    CapabilitySet,
    ensure_contract_intact,
    lookup,
    register,
    reset_registry,
)
from harness_everythings.core.schema_registry import (
    SchemaError,
    load_schema,
    migrate,
    register_migration,
    validate,
)
from harness_everythings.core.entities import make_envelope
from harness_everythings.views.markdown import render_entity, render_manifest, render_task


def valid_task_record():
    e = make_envelope(
        "task",
        {"n": 1},
        "user:test",
        "2026-08-16T00:00:00Z",
        fields={
            "state": "proposed",
            "owner_role": "role:x",
            "budget": {},
            "retry_policy": "safe_auto",
            "idempotency_key": "k",
            "transitions": [],
        },
    )
    return e.to_record()


def test_declared_schema_constraints_are_enforced():
    record = {
        "schema_version": "1.0",
        "entity_id": "approval:test",
        "entity_type": "approval",
        "created_at": "t",
        "updated_at": "t",
        "source_ref": "source:test",
        "target_ref": "target:test",
        "scope": "work_product",
        "requester": "requester:test",
        "approver": "user:test",
        "decision": "approved",
        "decided_at": "t",
        "plan_fingerprint": "not-a-sha256",
        "evidence_refs": [],
    }
    with pytest.raises(SchemaError):
        validate("approval", record)
    record["plan_fingerprint"] = "sha256:" + "0" * 64
    record["evidence_refs"] = ["evidence:test"]
    record["target_ref"] = ""
    with pytest.raises(SchemaError):
        validate("approval", record)


class TestSchemaRegistry:
    def test_all_entity_schemas_load(self):
        for et in (
            "workspace", "profile-record", "plan", "output-contract",
            "role", "task", "artifact", "evidence", "approval",
            "handoff", "governance-proposal", "application-manifest",
        ):
            schema = load_schema(et)
            assert schema["type"] == "object"

    def test_unknown_version_rejected(self):
        with pytest.raises(SchemaError):
            load_schema("task", "9.9")

    def test_unknown_entity_rejected(self):
        with pytest.raises(SchemaError):
            load_schema("nonexistent")

    def test_valid_task_validates(self):
        validate("task", valid_task_record())

    def test_missing_required_rejected(self):
        record = valid_task_record()
        del record["state"]
        with pytest.raises(SchemaError):
            validate("task", record)

    def test_bad_enum_rejected(self):
        record = valid_task_record()
        record["state"] = "flying"
        with pytest.raises(SchemaError):
            validate("task", record)

    def test_unknown_version_in_record_rejected(self):
        record = valid_task_record()
        record["schema_version"] = "0.0"
        with pytest.raises(SchemaError):
            validate("task", record)

    def test_unexpected_field_rejected(self):
        record = valid_task_record()
        record["mystery"] = 1
        with pytest.raises(SchemaError):
            validate("task", record)


class TestMigrations:
    def setup_method(self):
        # 每次测试重新注册，避免跨测试污染
        from harness_everythings.core import schema_registry as sr

        sr._MIGRATIONS.clear()
        sr._REVERSE_ALLOWED.clear()

    def test_registered_forward_migration(self):
        register_migration(
            "1.0", "1.1", lambda r: {**r, "schema_version": "1.1"}
        )
        out = migrate({"schema_version": "1.0"}, "1.1")
        assert out["schema_version"] == "1.1"

    def test_reverse_requires_explicit_allow(self):
        register_migration(
            "1.0", "1.1", lambda r: {**r, "schema_version": "1.1"}
        )
        with pytest.raises(SchemaError):
            migrate({"schema_version": "1.1"}, "1.0")

    def test_reverse_when_allowed(self):
        register_migration(
            "1.0", "1.1", lambda r: {**r, "schema_version": "1.1"},
            allow_reverse=True,
        )
        register_migration(
            "1.1", "1.0", lambda r: {**r, "schema_version": "1.0"},
        )
        out = migrate({"schema_version": "1.1"}, "1.0")
        assert out["schema_version"] == "1.0"

    def test_no_path(self):
        with pytest.raises(SchemaError):
            migrate({"schema_version": "1.0"}, "2.0")


class TestCapabilities:
    def test_register_and_lookup(self):
        reset_registry()
        cs = CapabilitySet(
            adapter_id="runtime-minimal",
            adapter_kind="runtime",
            capabilities=frozenset({"structured_questions"}),
        )
        register(cs)
        assert lookup("runtime-minimal") is cs

    def test_unknown_capability_rejected(self):
        with pytest.raises(CapabilityError):
            CapabilitySet("x", "runtime", frozenset({"teleport"}))

    def test_missing_capabilities_have_degradation(self):
        cs = CapabilitySet("empty", "runtime", frozenset())
        plan = cs.degraded_plan()
        assert "subagents" in plan and plan["subagents"] == "serial"
        assert "model_calls" in plan and plan["model_calls"] == "manual"

    def test_no_capability_disguise(self):
        cs = CapabilitySet("honest", "runtime", frozenset())
        assert cs.has("hooks") is False

    def test_contract_intact_after_degradation(self):
        reset_registry()
        cs = CapabilitySet("serial-only", "runtime", frozenset())
        register(cs)
        assert ensure_contract_intact("serial-only") is True

    def test_bad_kind_rejected(self):
        with pytest.raises(CapabilityError):
            CapabilitySet("x", "quantum", frozenset())


class TestMarkdownViews:
    def test_render_entity_deterministic(self):
        record = valid_task_record()
        assert render_entity(record) == render_entity(record)

    def test_render_entity_contains_id_and_fingerprint(self):
        record = valid_task_record()
        md = render_entity(record)
        assert record["entity_id"] in md
        assert "canonical-fingerprint" in md

    def test_render_manifest_lists_writes(self):
        record = {
            "entity_type": "application-manifest",
            "workspace_fingerprint": "sha256:abc",
            "idempotency_key": "sha256:def",
            "created_at": "2026-08-16T00:00:00Z",
            "writes": [
                {"rel": "a.json", "exclusive": True, "target_fingerprint": "sha256:1"},
            ],
        }
        md = render_manifest(record)
        assert "a.json" in md
        assert "dry-run" in md

    def test_render_task_includes_transitions(self):
        task = {
            "task_id": "task:1",
            "state": "running",
            "transitions": [
                {
                    "from_state": "proposed",
                    "to_state": "ready",
                    "actor": "role:x",
                    "at": "2026-08-16T00:00:00Z",
                }
            ],
        }
        md = render_task(task)
        assert "proposed -> ready" in md
