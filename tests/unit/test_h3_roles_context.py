"""H3 contract, lifecycle, routing, and adapter fixtures."""

from __future__ import annotations

from copy import deepcopy

import pytest

from harness_everythings.adapters.capabilities import CapabilityError, CapabilitySet
from harness_everythings.adapters.contracts import adapter_contract, workspace_discovery_contract
from harness_everythings.core.context import ContextRoutingError, build_context_routes
from harness_everythings.core.roles import RolePlanningError, propose_roles, reconcile_roles, transition_role_state
from harness_everythings.core.schema_registry import SchemaError, validate

NOW = "2026-08-16T00:00:00Z"


def profile(approved: bool = True) -> dict:
    return {
        "source_fingerprint": "sha256:profile",
        "records": [
            {"fact_key": "workspace.verification.signals", "fact_value": ["pytest"]},
            {"fact_key": "workspace.risks", "fact_value": []},
        ],
        "plan": {"approval_state": "approved" if approved else "proposed"},
    }


def roles() -> list[dict]:
    return propose_roles(profile(), plan_approval_state="approved", now=NOW)["roles"]


class TestH3Roles:
    def test_plan_approval_blocks_role_generation(self):
        result = propose_roles(profile(False), plan_approval_state="proposed", now=NOW)
        assert result["status"] == "blocked"
        assert result["roles"] == []
        validate("role-registry", result)

    def test_role_contract_is_complete_and_stable(self):
        first = propose_roles(profile(), plan_approval_state="approved", now=NOW)
        second = propose_roles(profile(), plan_approval_state="approved", now="2099-01-01T00:00:00Z")
        required = {"role_id", "role_contract_version", "mission", "permissions", "owns", "forbids", "capabilities", "input_contract_refs", "output_contract_refs", "artifact_obligations", "evidence_obligations", "verification", "stop_conditions", "dependencies", "concurrency_boundaries", "generation_origin", "lifecycle_state", "lifecycle_history", "contract_fingerprint"}
        assert [role["role_id"] for role in first["roles"]] == [role["role_id"] for role in second["roles"]]
        assert all(required <= set(role) for role in first["roles"])
        assert all(role["role_id"] == role["entity_id"] for role in first["roles"])
        assert {role["role_name"] for role in first["roles"]} == {"governance-coordinator", "evidence-reviewer"}
        for role in first["roles"]:
            validate("role", role)

    def test_role_schema_rejects_incomplete_contract(self):
        invalid = deepcopy(roles()[0])
        invalid.pop("permissions")
        with pytest.raises(SchemaError):
            validate("role", invalid)

    def test_role_lifecycle_accepts_only_declared_transitions(self):
        proposed = roles()[0]
        active = transition_role_state(proposed, "active", changed_at=NOW, evidence_ref="evidence:activate")
        assert active["lifecycle_state"] == "active"
        assert active["lifecycle_history"][-1]["evidence_ref"] == "evidence:activate"
        with pytest.raises(RolePlanningError):
            transition_role_state(active, "proposed", changed_at=NOW, evidence_ref="evidence:invalid")
        retired = transition_role_state(
            transition_role_state(active, "deprecated", changed_at=NOW, evidence_ref="evidence:deprecate"),
            "retired", changed_at=NOW, evidence_ref="evidence:retire",
        )
        assert retired["lifecycle_state"] == "retired"

    def test_reconcile_reports_all_categories_and_basis(self):
        generated, evidence = roles()
        stable = deepcopy(generated)
        changed_old = deepcopy(evidence)
        changed_old["role_id"] = "role:2222222222222222"
        changed_old["entity_id"] = changed_old["role_id"]
        changed_old["owns"] = ["shared:domain"]
        changed = deepcopy(changed_old)
        changed["mission"] = "changed contract"
        old_split = deepcopy(evidence)
        old_split["role_id"] = "role:3333333333333333"
        old_split["entity_id"] = old_split["role_id"]
        old_split["owns"] = ["split:a", "split:b"]
        old_deprecated = deepcopy(evidence)
        old_deprecated["role_id"] = "role:4444444444444444"
        old_deprecated["entity_id"] = old_deprecated["role_id"]
        old_deprecated["lifecycle_state"] = "deprecated"
        old_lost = deepcopy(evidence)
        old_lost["role_id"] = "role:5555555555555555"
        old_lost["entity_id"] = old_lost["role_id"]
        old_lost["owns"] = ["lost:basis"]
        old_merge_a = deepcopy(evidence)
        old_merge_a["role_id"] = "role:aaaaaaaaaaaaaaaa"
        old_merge_a["entity_id"] = old_merge_a["role_id"]
        old_merge_a["owns"] = ["merge:domain"]
        old_merge_b = deepcopy(evidence)
        old_merge_b["role_id"] = "role:bbbbbbbbbbbbbbbb"
        old_merge_b["entity_id"] = old_merge_b["role_id"]
        old_merge_b["owns"] = ["merge:domain"]
        proposed_split_a = deepcopy(generated)
        proposed_split_a["role_id"] = "role:6666666666666666"
        proposed_split_a["entity_id"] = proposed_split_a["role_id"]
        proposed_split_a["owns"] = ["split:a"]
        proposed_split_b = deepcopy(generated)
        proposed_split_b["role_id"] = "role:7777777777777777"
        proposed_split_b["entity_id"] = proposed_split_b["role_id"]
        proposed_split_b["owns"] = ["split:b"]
        user = deepcopy(generated)
        user["role_id"] = "role:8888888888888888"
        user["entity_id"] = user["role_id"]
        user["owns"] = ["governance:plan"]
        proposed_conflict = deepcopy(generated)
        proposed_conflict["role_id"] = "role:9999999999999999"
        proposed_conflict["entity_id"] = proposed_conflict["role_id"]
        proposed_conflict["owns"] = ["governance:plan"]
        proposed_merge = deepcopy(generated)
        proposed_merge["role_id"] = "role:cccccccccccccccc"
        proposed_merge["entity_id"] = proposed_merge["role_id"]
        proposed_merge["owns"] = ["merge:domain"]
        report = reconcile_roles([stable, changed_old, old_split, old_deprecated, old_lost, old_merge_a, old_merge_b], [stable, changed, proposed_conflict, proposed_split_a, proposed_split_b, proposed_merge], [user])
        for key in ("retained", "additions", "conflicts", "drift", "merge_candidates", "split_candidates", "deprecations", "lost_basis"):
            assert key in report
        assert report["retained"] and report["additions"] and report["conflicts"] and report["drift"]
        assert report["merge_candidates"] and report["split_candidates"] and report["deprecations"] and report["lost_basis"]
        assert report["user_overlay_unchanged"] is True
        for category in ("retained", "additions", "conflicts", "drift", "merge_candidates", "split_candidates", "deprecations", "lost_basis"):
            assert all(item.get("fingerprint") and item.get("evidence_ref") for item in report[category])


class TestH3ContextAndAdapters:
    def test_routes_are_role_specific_and_default_deny(self):
        governance, evidence = roles()
        sources = [
            {"source_ref": "profile:governance", "source_fingerprint": "sha256:a", "sensitivity": "internal", "estimated_tokens": 40, "authorized_role_ids": [governance["role_id"]]},
            {"source_ref": "profile:evidence", "source_fingerprint": "sha256:b", "sensitivity": "internal", "estimated_tokens": 40, "authorized_role_ids": [evidence["role_id"]]},
            {"source_ref": "profile:unbound", "source_fingerprint": "sha256:c", "sensitivity": "public", "estimated_tokens": 40},
        ]
        routes = build_context_routes([governance, evidence], sources, max_tokens=100)
        by_role = {route["role_id"]: route for route in routes["routes"]}
        assert by_role[governance["role_id"]]["source_refs"] == ["profile:governance"]
        assert by_role[evidence["role_id"]]["source_refs"] == ["profile:evidence"]
        assert "profile:unbound" not in sum((route["source_refs"] for route in routes["routes"]), [])
        assert routes["rejected_sources"]

    def test_routes_reject_private_history_and_invalid_budgets(self):
        role = roles()[0]
        routes = build_context_routes([role], [
            {"source_ref": "history/evolution/private-trace", "source_fingerprint": "sha256:h", "sensitivity": "internal", "estimated_tokens": 10, "authorized_role_ids": [role["role_id"]]},
            {"source_ref": "file:.env", "source_fingerprint": "sha256:s", "sensitivity": "secret", "estimated_tokens": 10, "authorized_role_ids": [role["role_id"]]},
        ], max_tokens=100)
        assert "history/evolution" not in str(routes)
        assert "file:.env" not in str(routes)
        with pytest.raises(ContextRoutingError):
            build_context_routes([role], [], max_tokens=-1)
        with pytest.raises(ContextRoutingError):
            build_context_routes([role], [{"source_ref": "safe", "source_fingerprint": "sha256:x", "sensitivity": "public", "estimated_tokens": "10", "authorized_role_ids": [role["role_id"]]}])

    def test_route_budget_and_canonical_order_are_stable(self):
        role = roles()[0]
        sources = [
            {"source_ref": "b", "source_fingerprint": "sha256:b", "sensitivity": "public", "estimated_tokens": 60, "authorized_role_ids": [role["role_id"]]},
            {"source_ref": "a", "source_fingerprint": "sha256:a", "sensitivity": "public", "estimated_tokens": 60, "authorized_role_ids": [role["role_id"]]},
        ]
        first = build_context_routes([role], sources, max_tokens=100)
        second = build_context_routes([role], list(reversed(sources)), max_tokens=100)
        assert first == second
        assert all(route["estimated_tokens"] <= route["max_tokens"] for route in first["routes"])

    def test_adapter_contract_is_truthful_and_workspace_is_separate(self):
        report = adapter_contract(CapabilitySet("minimal", "runtime", frozenset()))
        assert report["execution"] == "serial"
        assert report["degradation"]["hooks"] == "manual"
        assert report["state_contract"]["canonical_states"] == "unchanged"
        workspace = workspace_discovery_contract()
        assert workspace["executes_project_scripts"] is False
        assert workspace["network"] is False
        assert workspace["credentials"] is False
        assert workspace["discovery"]["read_only"] is True
        assert workspace["write"]["requires_user_approval"] is True

    def test_adapter_rejects_contradictions(self):
        with pytest.raises(CapabilityError):
            CapabilitySet("bad", "runtime", frozenset({"subagents"}))
        with pytest.raises(CapabilityError):
            CapabilitySet("bad", "runtime", frozenset({"unknown"}))
