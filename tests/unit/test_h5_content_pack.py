"""H5 content-script pack contracts and controlled lifecycle tests."""

from __future__ import annotations

import pytest

from harness_everythings.core.content_domain import (
    ContentDomainError,
    create_content_brief,
    derive_content_output_contract,
    make_content_approval,
    make_content_variants,
    review_content,
    select_content_variant,
    transition_content,
    validate_content_approval,
)
from harness_everythings.core.domain_packs import load_domain_pack
from harness_everythings.core.evidence import make_evidence
from harness_everythings.core.identity import content_fingerprint


NOW = "2026-08-16T00:00:00Z"


def brief():
    return create_content_brief(
        {
            "audience": "developers",
            "goals": ["explain the change"],
            "claims": [{"claim_id": "claim:one", "text": "A bounded claim", "source_refs": ["source:one"]}],
            "sources": [{"source_ref": "source:one", "source_fingerprint": "sha256:source", "sensitivity": "public"}],
            "structure": ["opening", "evidence", "cta"],
            "length": {"min_words": 1, "max_words": 80},
            "tone": "clear",
            "platform": "blog",
            "prohibited": ["guaranteed"],
            "cta": "read the docs",
        },
        NOW,
    )


def approved_content(record, variants, owner="owner:content"):
    draft_review = review_content(record, variants, now=NOW)
    supporting_refs = {
        record["entity_id"],
        variants[0]["variant_id"],
        *(claim["claim_id"] for claim in record["claims"]),
        *(source_ref for claim in record["claims"] for source_ref in claim["source_refs"]),
        *(check["check_id"] for check in draft_review["checks"]),
    }
    evidence = make_evidence(
        actor="user", action="content-review", conclusion_kind="user_confirmed",
        supporting_refs=supporting_refs,
        verification_level="fixture_verified", source_ref="fixture:content-review", now=NOW,
    )
    review = review_content(record, variants, now=NOW, evidence_refs=[evidence["entity_id"]])
    approval = make_content_approval(
        variants[0]["variant_id"], brief=record, requester="role:content-compliance", approver="user",
        target_owner=owner, decided_at=NOW, variant=variants[0], review=review,
        evidence_refs=[evidence["entity_id"]], evidence_records=[evidence],
    )
    return review, evidence, approval


class TestContentPack:
    def test_pack_is_versioned_and_has_merged_review_role(self):
        pack = load_domain_pack("content-script")
        assert pack["pack_id"] == "content-script"
        assert {role["role_id"] for role in pack["role_templates"]} >= {"content-fact-check-edit", "content-writing"}
        assert all(role["input_contract_refs"] == ["content-brief@1.0", "content-output-contract@1.0"] for role in pack["role_templates"])

    def test_brief_contract_variants_and_review_are_deterministic(self):
        record = brief()
        contract = derive_content_output_contract(record, NOW)
        variants = make_content_variants(record, [
            {"content": "opening evidence cta A bounded claim read the docs", "claim_refs": ["claim:one"]},
            {"content": "opening evidence cta A bounded claim read the docs again", "claim_refs": ["claim:one"]},
        ])
        selected = select_content_variant(variants, variants[0]["variant_id"])
        review = review_content(record, selected, now=NOW)
        assert len(selected) == 2
        assert sum(item["status"] == "selected" for item in selected) == 1
        assert all(item["status"] in {"selected", "preserved"} for item in selected)
        assert contract["variant_policy"]["preserve_all"] is True
        assert review["status"] == "passed"

    def test_missing_claim_sources_fail_review_basis(self):
        record = brief()
        record["claims"] = [{"claim_id": "claim:missing", "text": "unsupported", "source_refs": []}]
        variants = make_content_variants(record, [{"content": "opening evidence read the docs", "claim_refs": ["claim:missing"]}])
        selected = select_content_variant(variants, variants[0]["variant_id"])
        review = review_content(record, selected, now=NOW)
        assert review["status"] == "failed"
        assert review["claim_checks"][0]["status"] == "failed"

    def test_content_lifecycle_rejects_illegal_transition_and_records_pause(self):
        record = brief()
        with pytest.raises(ContentDomainError):
            transition_content(record, "approved", actor="user", reason="skip", evidence_ref="evidence:bad", at=NOW)
        paused = transition_content(record, "paused", actor="user", reason="wait for source", evidence_ref="evidence:pause", at=NOW)
        resumed = transition_content(paused, "outline", actor="user", reason="resume", evidence_ref="evidence:resume", at=NOW)
        assert resumed["lifecycle_state"] == "outline"
        assert len(resumed["transitions"]) == 2

    def test_creation_and_release_approvals_are_separate(self):
        record = brief()
        variants = make_content_variants(record, [{"content": "opening evidence cta A bounded claim read the docs", "claim_refs": ["claim:one"]}])
        variants = select_content_variant(variants, variants[0]["variant_id"])
        draft_review = review_content(record, variants, now=NOW)
        with pytest.raises(ContentDomainError):
            make_content_approval(variants[0]["variant_id"], brief=record, requester="role:content-compliance", approver="user", target_owner="role:content-compliance", decided_at=NOW, variant=variants[0], review=draft_review, evidence_refs=["evidence:missing"], evidence_records=[])
        review, evidence, approval = approved_content(record, variants, "role:content-compliance")
        assert approval["scope"] == "work_product"
        release = make_content_approval(variants[0]["variant_id"], brief=record, requester="role:content-compliance", approver="user", target_owner="role:content-compliance", decided_at=NOW, scope="external_release", variant=variants[0], review=review, work_product_approval=approval, evidence_refs=[evidence["entity_id"]], evidence_records=[evidence])
        assert release["scope"] == "external_release"

    def test_review_blocks_missing_source_length_prohibited_platform_and_selection(self):
        record = brief()
        content = "opening evidence cta A bounded claim read the docs"
        variant = {"content": content, "claim_refs": ["claim:one"]}
        variants = make_content_variants(record, [variant])
        assert review_content(record, variants, now=NOW)["status"] == "blocked"
        selected = select_content_variant(variants, variants[0]["variant_id"])
        assert review_content(record, selected, now=NOW)["status"] == "passed"
        too_short = dict(record, length={"min_words": 99, "max_words": 100})
        assert review_content(too_short, selected, now=NOW)["status"] == "failed"
        prohibited = dict(record, prohibited=["bounded"])
        assert review_content(prohibited, selected, now=NOW)["status"] == "failed"
        unsupported = dict(record, platform="unknown-platform")
        assert review_content(unsupported, selected, now=NOW)["status"] == "failed"
        missing = dict(record, claims=[{"claim_id": "claim:one", "text": "A bounded claim", "source_refs": ["source:missing"]}])
        assert review_content(missing, selected, now=NOW)["status"] == "failed"
        assert all({"observed", "expected", "status", "evidence_refs", "basis"}.issubset(item) for item in review_content(record, selected, now=NOW)["checks"])

    def test_approval_is_invalidated_by_content_or_review_drift(self):
        record = brief()
        variants = make_content_variants(record, [{"content": "opening evidence cta A bounded claim read the docs", "claim_refs": ["claim:one"]}])
        variants = select_content_variant(variants, variants[0]["variant_id"])
        review, evidence, approval = approved_content(record, variants)
        changed = dict(variants[0], content="opening evidence cta changed claim read the docs")
        with pytest.raises(ContentDomainError):
            validate_content_approval(approval, record, changed, review, target_owner="owner:content")
        changed_review = dict(review, status="failed")
        with pytest.raises(ContentDomainError):
            validate_content_approval(approval, record, variants[0], changed_review, target_owner="owner:content")

    def test_content_approval_rejects_unrelated_or_incomplete_evidence(self):
        record = brief()
        variants = select_content_variant(
            make_content_variants(record, [{"content": "opening evidence cta A bounded claim read the docs", "claim_refs": ["claim:one"]}]),
            make_content_variants(record, [{"content": "opening evidence cta A bounded claim read the docs", "claim_refs": ["claim:one"]}])[0]["variant_id"],
        )
        unrelated = make_evidence(
            actor="user", action="content-review", conclusion_kind="user_confirmed",
            supporting_refs=["artifact:unrelated"], verification_level="fixture_verified",
            source_ref="fixture:unrelated", now=NOW,
        )
        unrelated_review = review_content(record, variants, now=NOW, evidence_refs=[unrelated["entity_id"]])
        with pytest.raises(ContentDomainError, match="support"):
            make_content_approval(
                variants[0]["variant_id"], brief=record, requester="role:content-compliance",
                approver="user", target_owner="owner:content", decided_at=NOW,
                variant=variants[0], review=unrelated_review,
                evidence_refs=[unrelated["entity_id"]], evidence_records=[unrelated],
            )

        incomplete = make_evidence(
            actor="user", action="content-review", conclusion_kind="user_confirmed",
            supporting_refs=[variants[0]["variant_id"], "claim:one"],
            verification_level="fixture_verified", source_ref="fixture:incomplete", now=NOW,
        )
        incomplete_review = review_content(record, variants, now=NOW, evidence_refs=[incomplete["entity_id"]])
        with pytest.raises(ContentDomainError, match="support"):
            make_content_approval(
                variants[0]["variant_id"], brief=record, requester="role:content-compliance",
                approver="user", target_owner="owner:content", decided_at=NOW,
                variant=variants[0], review=incomplete_review,
                evidence_refs=[incomplete["entity_id"]], evidence_records=[incomplete],
            )

    def test_content_approval_rechecks_forged_self_approval_after_refingerprint(self):
        record = brief()
        variants = make_content_variants(record, [{"content": "opening evidence cta A bounded claim read the docs", "claim_refs": ["claim:one"]}])
        variants = select_content_variant(variants, variants[0]["variant_id"])
        review, evidence, approval = approved_content(record, variants)
        forged = dict(approval, requester="role:self", approver="role:self")
        forged["approval_fingerprint"] = content_fingerprint(
            {key: value for key, value in forged.items() if key != "approval_fingerprint"}
        )
        with pytest.raises(ContentDomainError, match="self-authorized"):
            validate_content_approval(
                forged, record, variants[0], review, target_owner="owner:content",
                evidence_records=[evidence],
            )

    def test_content_approval_rejects_cross_brief_internal_review_and_variant_tampering(self):
        first = brief()
        variants = select_content_variant(make_content_variants(first, [{"content": "opening evidence cta A bounded claim read the docs", "claim_refs": ["claim:one"]}]), make_content_variants(first, [{"content": "opening evidence cta A bounded claim read the docs", "claim_refs": ["claim:one"]}])[0]["variant_id"])
        review, evidence, approval = approved_content(first, variants)
        second = dict(first, entity_id="content-brief:0000000000000000")
        with pytest.raises(ContentDomainError):
            validate_content_approval(approval, second, variants[0], review, target_owner="owner:content")
        tampered_review = dict(review, selected_variant_ref="content-variant:0000000000000000")
        with pytest.raises(ContentDomainError):
            validate_content_approval(approval, first, variants[0], tampered_review, target_owner="owner:content")
        tampered_variant = dict(variants[0], content_fingerprint="sha256:tampered")
        with pytest.raises(ContentDomainError):
            validate_content_approval(approval, first, tampered_variant, review, target_owner="owner:content")

    def test_external_release_requires_work_product_binding(self):
        record = brief()
        variants = select_content_variant(make_content_variants(record, [{"content": "opening evidence cta A bounded claim read the docs", "claim_refs": ["claim:one"]}]), make_content_variants(record, [{"content": "opening evidence cta A bounded claim read the docs", "claim_refs": ["claim:one"]}])[0]["variant_id"])
        review = review_content(record, variants, now=NOW)
        with pytest.raises(ContentDomainError):
            make_content_approval(variants[0]["variant_id"], brief=record, requester="role:content-compliance", approver="user", target_owner="owner:content", decided_at=NOW, scope="external_release", variant=variants[0], review=review)

    def test_approved_transition_requires_current_brief_and_actual_approval_evidence(self):
        record = brief()
        variants = make_content_variants(record, [{"content": "opening evidence cta A bounded claim read the docs", "claim_refs": ["claim:one"]}])
        variants = select_content_variant(variants, variants[0]["variant_id"])
        review, evidence, approval = approved_content(record, variants)
        awaiting = dict(record, lifecycle_state="awaiting_approval")
        with pytest.raises(ContentDomainError):
            transition_content(awaiting, "approved", actor="user", reason="approve", evidence_ref="evidence:review", at=NOW, approval=approval, variant=variants[0], review=review, target_owner="owner:content", current_brief=record)
        approved = transition_content(awaiting, "approved", actor="user", reason="approve", evidence_ref=approval["entity_id"], at=NOW, approval=approval, variant=variants[0], review=review, target_owner="owner:content", current_brief=record, evidence_records=[evidence])
        assert approved["lifecycle_state"] == "approved"
