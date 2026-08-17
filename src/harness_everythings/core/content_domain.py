"""Deterministic content-script brief, variant, review, and handoff contracts."""

from __future__ import annotations

from typing import Any, Iterable

from .approvals_roles import ApprovalRequest, ApprovalError, decide, validate_canonical_approval
from .domain_packs import DomainPackError, load_domain_pack
from .entities import derive_id, make_envelope
from .identity import content_fingerprint
from .schema_registry import validate


CONTENT_STATES = ("brief", "outline", "draft", "variants", "review", "awaiting_approval", "approved", "paused", "partial_success", "failed")
CONTENT_TRANSITIONS: dict[str, frozenset[str]] = {
    "brief": frozenset({"outline", "paused", "failed"}),
    "outline": frozenset({"draft", "paused", "failed"}),
    "draft": frozenset({"variants", "paused", "failed"}),
    "variants": frozenset({"review", "paused", "partial_success", "failed"}),
    "review": frozenset({"awaiting_approval", "paused", "failed"}),
    "awaiting_approval": frozenset({"approved", "paused", "failed"}),
    "approved": frozenset(),
    "paused": frozenset({"outline", "draft", "variants", "review", "failed"}),
    "partial_success": frozenset({"review", "paused", "failed"}),
    "failed": frozenset({"brief", "outline", "draft", "variants", "review"}),
}


class ContentDomainError(ValueError):
    """Invalid content contract, state transition, or approval boundary."""


SUPPORTED_PLATFORMS = frozenset({"blog", "newsletter", "social", "short-video"})


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContentDomainError(f"{field} must be a non-empty string")
    return value


def create_content_brief(fields: dict[str, Any], now: str) -> dict[str, Any]:
    required = ("audience", "goals", "claims", "sources", "structure", "length", "tone", "platform", "prohibited", "cta")
    if any(key not in fields for key in required):
        raise ContentDomainError("content brief is incomplete")
    length = fields["length"]
    if not isinstance(length, dict) or not isinstance(length.get("min_words"), int) or isinstance(length.get("min_words"), bool) or not isinstance(length.get("max_words"), int) or isinstance(length.get("max_words"), bool) or length["min_words"] < 0 or length["max_words"] < length["min_words"]:
        raise ContentDomainError("content length must be a non-negative ordered integer range")
    claims = fields["claims"]
    if not isinstance(claims, list) or any(not isinstance(item, dict) or not item.get("claim_id") or not isinstance(item.get("source_refs"), list) for item in claims):
        raise ContentDomainError("claims must have source_refs")
    if not isinstance(fields["sources"], list) or any(not isinstance(item, dict) or not isinstance(item.get("source_ref"), str) or not item.get("source_ref") for item in fields["sources"]):
        raise ContentDomainError("sources must contain stable source_ref records")
    record = make_envelope(
        "content-brief",
        {"audience": fields["audience"], "goals": sorted(fields["goals"])},
        fields.get("source_ref", "user:content-brief"),
        now,
        fields={
            **{key: fields[key] for key in required},
            "lifecycle_state": "brief",
            "transitions": [],
        },
    ).to_record()
    validate("content-brief", record)
    return record


def transition_content(brief: dict[str, Any], to_state: str, *, actor: str, reason: str, evidence_ref: str, at: str, approval: dict[str, Any] | None = None, variant: dict[str, Any] | None = None, review: dict[str, Any] | None = None, target_owner: str | None = None, current_brief: dict[str, Any] | None = None, evidence_records: Iterable[dict[str, Any]] | None = None) -> dict[str, Any]:
    current = brief.get("lifecycle_state")
    if current not in CONTENT_STATES or to_state not in CONTENT_STATES:
        raise ContentDomainError("unknown content lifecycle state")
    if to_state not in CONTENT_TRANSITIONS[current]:
        raise ContentDomainError(f"illegal content transition: {current} -> {to_state}")
    _text(actor, "actor")
    _text(reason, "reason")
    _text(evidence_ref, "evidence_ref")
    if to_state == "approved":
        if approval is None or variant is None or review is None or not target_owner or current_brief is None:
            raise ContentDomainError("approved content requires canonical approval, variant, review, and owner")
        if evidence_ref != approval.get("entity_id"):
            raise ContentDomainError("approved content evidence_ref must be the actual Approval ID")
        validate_content_approval(approval, current_brief, variant, review, target_owner=target_owner, evidence_records=evidence_records)
    updated = dict(brief)
    history = list(brief.get("transitions", []))
    history.append({"from_state": current, "to_state": to_state, "actor": actor, "reason": reason, "evidence_ref": evidence_ref, "at": at})
    updated["lifecycle_state"] = to_state
    updated["transitions"] = history
    updated["updated_at"] = at
    validate("content-brief", updated)
    return updated


def derive_content_output_contract(brief: dict[str, Any], now: str) -> dict[str, Any]:
    validate("content-brief", brief)
    pack = load_domain_pack("content-script")
    requirements = [
        {"requirement_id": derive_id("content-requirement", {"brief": brief["entity_id"], "condition": condition}), "condition": condition, "validator": "content-platform"}
        for condition in sorted(set(brief["goals"] + brief["structure"] + [f"length:{brief['length']['min_words']}-{brief['length']['max_words']}", f"tone:{brief['tone']}", f"platform:{brief['platform']}", f"cta:{brief['cta']}"]))
    ]
    claims = [{"claim_id": claim["claim_id"], "required_source_refs": sorted(claim["source_refs"])} for claim in sorted(brief["claims"], key=lambda item: item["claim_id"])]
    envelope = make_envelope(
        "content-output-contract",
        {"brief": brief["entity_id"], "pack": pack["pack_id"], "version": pack["pack_version"]},
        f"h5:content:{brief['entity_id']}",
        now,
        fields={
            "brief_ref": brief["entity_id"],
            "pack_id": pack["pack_id"],
            "pack_version": pack["pack_version"],
            "output_requirements": requirements,
            "claim_source_rules": claims,
            "hard_constraints": {
                "platform": brief["platform"],
                "min_words": brief["length"]["min_words"],
                "max_words": brief["length"]["max_words"],
                "required_structure": sorted(brief["structure"]),
                "prohibited": sorted(brief["prohibited"]),
                "cta": brief["cta"],
            },
            "variant_policy": {"preserve_all": True, "user_selection_required": True},
            "approval_boundaries": {"creation_scope": "work_product", "external_release_scope": "external_release", "separate": True},
        },
    ).to_record()
    envelope["fingerprint"] = content_fingerprint(envelope)
    validate("content-output-contract", envelope)
    return envelope


def make_content_variants(brief: dict[str, Any], contents: Iterable[Any]) -> list[dict[str, Any]]:
    validate("content-brief", brief)
    variants = []
    known_claims = {claim["claim_id"] for claim in brief["claims"]}
    normalized: list[tuple[str, tuple[str, ...]]] = []
    for candidate in contents:
        if isinstance(candidate, str):
            content, claim_refs = candidate, []
        elif isinstance(candidate, dict):
            content = candidate.get("content")
            claim_refs = candidate.get("claim_refs")
            if not isinstance(claim_refs, list) or any(not isinstance(ref, str) or not ref for ref in claim_refs):
                raise ContentDomainError("variant claim_refs must be an explicit string list")
        else:
            raise ContentDomainError("variant must be a string or object")
        _text(content, "variant.content")
        if any(ref not in known_claims for ref in claim_refs):
            raise ContentDomainError("variant claim_refs contain an unknown claim")
        normalized.append((content, tuple(sorted(set(claim_refs)))))
    for content, claim_refs in sorted(set(normalized)):
        _text(content, "variant.content")
        fingerprint = content_fingerprint(content)
        variants.append({
            "schema_version": "1.0",
            "entity_type": "content-variant",
            "variant_id": derive_id("content-variant", {"brief": brief["entity_id"], "content": fingerprint}),
            "brief_ref": brief["entity_id"],
            "content": content,
            "claim_refs": list(claim_refs),
            "status": "candidate",
            "content_fingerprint": fingerprint,
        })
    for variant in variants:
        validate("content-variant", variant)
    return sorted(variants, key=lambda item: item["variant_id"])


def select_content_variant(variants: Iterable[dict[str, Any]], variant_id: str) -> list[dict[str, Any]]:
    ordered = sorted((dict(item) for item in variants), key=lambda item: item["variant_id"])
    if not any(item.get("variant_id") == variant_id for item in ordered):
        raise ContentDomainError("selected content variant is unknown")
    for item in ordered:
        item["status"] = "selected" if item["variant_id"] == variant_id else "preserved"
        validate("content-variant", item)
    return ordered


def _check(check_id: str, category: str, observed: Any, expected: Any, status: str, basis: str, evidence_refs: tuple[str, ...]) -> dict[str, Any]:
    if status not in {"passed", "failed", "blocked"}:
        raise ContentDomainError(f"invalid review status: {status}")
    return {"check_id": check_id, "category": category, "observed": observed, "expected": expected, "status": status, "evidence_refs": list(evidence_refs), "basis": basis}


def review_content(brief: dict[str, Any], variants: Iterable[dict[str, Any]], *, now: str, selected_variant_id: str | None = None, evidence_refs: Iterable[str] = ()) -> dict[str, Any]:
    validate("content-brief", brief)
    ordered = sorted((dict(item) for item in variants), key=lambda item: item.get("variant_id", ""))
    for variant in ordered:
        validate("content-variant", variant)
    selected = [item for item in ordered if item.get("status") == "selected"]
    if selected_variant_id is not None:
        selected = [item for item in ordered if item.get("variant_id") == selected_variant_id]
    selected_variant = selected[0] if len(selected) == 1 else None
    review_evidence_refs = tuple(sorted(set(evidence_refs)))
    if any(not isinstance(ref, str) or not ref for ref in review_evidence_refs):
        raise ContentDomainError("review evidence_refs must contain non-empty strings")
    source_refs = {source.get("source_ref") for source in brief["sources"]}
    known_claims = {claim["claim_id"] for claim in brief["claims"]}
    claim_checks = []
    checks: list[dict[str, Any]] = []
    for claim in sorted(brief["claims"], key=lambda item: item["claim_id"]):
        refs = sorted(claim["source_refs"])
        missing = sorted(set(refs) - source_refs)
        carried = sorted(set(selected_variant.get("claim_refs", [])) if selected_variant else set())
        status = "passed" if refs and not missing and claim["claim_id"] in carried else "blocked" if selected_variant is None else "failed"
        claim_checks.append({"claim_id": claim["claim_id"], "source_refs": refs, "observed": {"brief_source_refs": refs, "missing_source_refs": missing, "selected_variant_claim_refs": carried}, "expected": {"source_refs_exist": True, "selected_variant_carries_claim": True}, "status": status, "evidence_refs": list(review_evidence_refs), "basis": "brief.sources and selected content-variant claim_refs"})
        checks.append(_check(f"claim:{claim['claim_id']}", "claim-source", {"missing": missing, "carried": claim["claim_id"] in carried}, {"missing": [], "carried": True}, status, "brief.sources and selected variant declaration", review_evidence_refs))
    if selected_variant is None:
        checks.append(_check("variant-selection", "variant-selection", [item.get("variant_id") for item in ordered], "exactly one selected variant", "blocked", "no canonical selected variant", review_evidence_refs))
    else:
        checks.append(_check("variant-selection", "variant-selection", selected_variant["variant_id"], "one selected variant", "passed", "selected variant status and ID", review_evidence_refs))
        content = selected_variant["content"]
        words = len(content.split())
        min_words = brief["length"]["min_words"]
        max_words = brief["length"]["max_words"]
        length_status = "passed" if min_words <= words <= max_words else "failed"
        checks.append(_check("content-length", "length", words, {"min_words": min_words, "max_words": max_words}, length_status, "actual selected variant word count", review_evidence_refs))
        prohibited_hits = sorted({term for term in brief["prohibited"] if term.casefold() in content.casefold()})
        prohibited_status = "passed" if not prohibited_hits else "failed"
        checks.append(_check("prohibited-content", "prohibited", prohibited_hits, [], prohibited_status, "actual selected variant content scan", review_evidence_refs))
        missing_structure = sorted({marker for marker in brief["structure"] if marker.casefold() not in content.casefold()})
        structure_status = "passed" if not missing_structure else "failed"
        checks.append(_check("required-structure", "structure", {"missing": missing_structure}, {"missing": []}, structure_status, "required structure marker scan", review_evidence_refs))
        platform_status = "passed" if brief["platform"] in SUPPORTED_PLATFORMS else "failed"
        checks.append(_check("platform", "platform", brief["platform"], sorted(SUPPORTED_PLATFORMS), platform_status, "deterministic supported-platform set", review_evidence_refs))
        cta_status = "passed" if brief["cta"].casefold() in content.casefold() else "failed"
        checks.append(_check("cta", "cta", brief["cta"] in content or brief["cta"].casefold() in content.casefold(), True, cta_status, "actual selected variant CTA scan", review_evidence_refs))
    length = brief["length"]
    platform_checks = [item for item in checks if item["category"] in {"platform", "length", "structure", "cta", "variant-selection"}]
    prohibited_checks = [item for item in checks if item["category"] == "prohibited"]
    status = "passed" if checks and all(item["status"] == "passed" for item in checks) else "blocked" if selected_variant is None else "failed"
    base = {"schema_version": "1.0", "entity_type": "content-review", "review_id": derive_id("content-review", {"brief": brief["entity_id"], "variant": selected_variant.get("variant_id") if selected_variant else "", "checks": checks}), "brief_ref": brief["entity_id"], "variant_refs": sorted(item["variant_id"] for item in ordered), "selected_variant_ref": selected_variant.get("variant_id") if selected_variant else "", "claim_checks": claim_checks, "platform_checks": platform_checks, "prohibited_checks": prohibited_checks, "checks": checks, "status": status, "evidence_refs": list(review_evidence_refs)}
    result = {**base, "fingerprint": content_fingerprint(base)}
    validate("content-review", result)
    return result


def _content_review_base(review: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in review.items() if key != "fingerprint"}


def _brief_binding_fingerprint(brief: dict[str, Any]) -> str:
    """Fingerprint stable brief content without mutable lifecycle bookkeeping."""
    return content_fingerprint({
        key: value for key, value in brief.items()
        if key not in {"lifecycle_state", "transitions", "updated_at"}
    })


def _expected_content_review_id(brief: dict[str, Any], review: dict[str, Any]) -> str:
    return derive_id("content-review", {"brief": brief["entity_id"], "variant": review.get("selected_variant_ref", ""), "checks": review.get("checks", [])})


def _validate_content_evidence_bindings(
    evidence_refs: Iterable[str],
    brief: dict[str, Any],
    variant: dict[str, Any],
    review: dict[str, Any],
    evidence_records: Iterable[dict[str, Any]] | None,
) -> None:
    refs = tuple(sorted(set(evidence_refs)))
    if not refs or any(not isinstance(ref, str) or not ref for ref in refs):
        raise ContentDomainError("content approval requires non-empty evidence references")
    if evidence_records is None:
        raise ContentDomainError("content approval requires actual Evidence records")
    by_id: dict[str, dict[str, Any]] = {}
    for record in evidence_records:
        try:
            validate("evidence", record)
        except Exception as exc:
            raise ContentDomainError(f"invalid content Evidence record: {exc}") from exc
        if record.get("record_fingerprint") != content_fingerprint({key: value for key, value in record.items() if key != "record_fingerprint"}):
            raise ContentDomainError(f"content Evidence fingerprint is stale: {record.get('entity_id')}")
        entity_id = record.get("entity_id")
        if entity_id in by_id and content_fingerprint(by_id[entity_id]) != content_fingerprint(record):
            raise ContentDomainError(f"conflicting content Evidence record: {entity_id}")
        by_id[entity_id] = record
    missing = sorted(set(refs) - set(by_id))
    if missing:
        raise ContentDomainError(f"content approval references missing Evidence: {missing}")
    review_refs = tuple(sorted(set(review.get("evidence_refs", []))))
    if not review_refs or set(review_refs) != set(refs) or not set(review_refs).issubset(by_id):
        raise ContentDomainError("content review is not bound to actual Evidence records")

    variant_ref = variant.get("variant_id")
    supporting_by_id = {
        entity_id: set(record.get("supporting_refs", []))
        for entity_id, record in by_id.items()
        if entity_id in refs
    }
    for entity_id, supporting_refs in supporting_by_id.items():
        if variant_ref not in supporting_refs:
            raise ContentDomainError(
                f"content Evidence does not support the current variant: {entity_id}"
            )

    def require_support(check: dict[str, Any], required_refs: set[str]) -> None:
        check_evidence_refs = check.get("evidence_refs")
        if (
            not isinstance(check_evidence_refs, list)
            or not check_evidence_refs
            or any(ref not in supporting_by_id for ref in check_evidence_refs)
        ):
            raise ContentDomainError(
                f"content check is not bound to actual Evidence: {check.get('check_id') or check.get('claim_id')}"
            )
        if not any(required_refs.issubset(supporting_by_id[ref]) for ref in check_evidence_refs):
            raise ContentDomainError(
                f"content Evidence does not support check: {check.get('check_id') or check.get('claim_id')}"
            )

    claims_by_id = {claim["claim_id"]: claim for claim in brief["claims"]}
    for claim_check in review.get("claim_checks", []):
        claim_id = claim_check.get("claim_id")
        claim = claims_by_id.get(claim_id)
        if claim is None:
            raise ContentDomainError(f"content review contains an unknown claim check: {claim_id}")
        require_support(
            claim_check,
            {variant_ref, claim_id, *claim["source_refs"]},
        )
    for check in review.get("checks", []):
        require_support(check, {variant_ref, check.get("check_id")})


def make_content_approval(target_ref: str, *, brief: dict[str, Any], requester: str, approver: str, target_owner: str, decided_at: str, scope: str = "work_product", variant: dict[str, Any] | None = None, review: dict[str, Any] | None = None, work_product_approval: dict[str, Any] | None = None, evidence_refs: list[str] | tuple[str, ...] = (), evidence_records: Iterable[dict[str, Any]] | None = None) -> dict[str, Any]:
    try:
        if variant is None or review is None:
            raise ContentDomainError("content approval requires variant and review bindings")
        validate_content_approval_inputs(brief, variant, review, target_owner=target_owner)
        _validate_content_evidence_bindings(evidence_refs, brief, variant, review, evidence_records)
        if scope == "external_release" and work_product_approval is None:
            raise ContentDomainError("external release approval requires the approved work-product Approval")
        record = decide(ApprovalRequest(target_ref, scope, requester, approver, target_owner, evidence_refs=tuple(sorted(set(evidence_refs)))), "approved", decided_at)
        record.update({
            "brief_ref": brief["entity_id"],
            "brief_fingerprint": _brief_binding_fingerprint(brief),
            "variant_ref": variant["variant_id"],
            "variant_fingerprint": content_fingerprint(variant),
            "review_ref": review["review_id"],
            "review_fingerprint": review["fingerprint"],
        })
        if work_product_approval is not None:
            record["work_product_approval_ref"] = work_product_approval.get("entity_id", "")
            record["work_product_approval_fingerprint"] = content_fingerprint(work_product_approval)
        record["approval_fingerprint"] = content_fingerprint({key: value for key, value in record.items() if key != "approval_fingerprint"})
        validate("approval", record)
        validate_content_approval(record, brief, variant, review, target_owner=target_owner, work_product_approval=work_product_approval, expected_scope=scope, evidence_records=evidence_records)
        return record
    except ApprovalError as exc:
        raise ContentDomainError(str(exc)) from exc


def validate_content_approval_inputs(brief: dict[str, Any], variant: dict[str, Any], review: dict[str, Any], *, target_owner: str) -> None:
    validate("content-brief", brief)
    validate("content-variant", variant)
    validate("content-review", review)
    if variant.get("brief_ref") != brief.get("entity_id") or review.get("brief_ref") != brief.get("entity_id"):
        raise ContentDomainError("content review and variant must bind the current brief")
    if variant.get("content_fingerprint") != content_fingerprint(variant.get("content")):
        raise ContentDomainError("content variant fingerprint is stale")
    variant_refs = review.get("variant_refs", [])
    if variant_refs != sorted(set(variant_refs)) or variant.get("variant_id") not in variant_refs or review.get("selected_variant_ref") != variant.get("variant_id"):
        raise ContentDomainError("review variant selection is not unique and canonical")
    if review.get("review_id") != _expected_content_review_id(brief, review):
        raise ContentDomainError("content review ID is not derived from its frozen input")
    if review.get("fingerprint") != content_fingerprint(_content_review_base(review)):
        raise ContentDomainError("content review fingerprint is stale")
    if review.get("status") != "passed":
        raise ContentDomainError("content approval requires a passed review")
    expected_claim_ids = sorted(claim["claim_id"] for claim in brief["claims"])
    observed_claim_ids = sorted(item.get("claim_id") for item in review.get("claim_checks", []))
    if observed_claim_ids != expected_claim_ids or any(item.get("status") != "passed" for item in review.get("claim_checks", [])):
        raise ContentDomainError("content review claim checks are incomplete or not passed")
    expected_check_ids = {
        *(f"claim:{claim_id}" for claim_id in expected_claim_ids),
        "variant-selection",
        "content-length",
        "prohibited-content",
        "required-structure",
        "platform",
        "cta",
    }
    checks = review.get("checks", [])
    if {item.get("check_id") for item in checks} != expected_check_ids or any(item.get("status") != "passed" for item in checks):
        raise ContentDomainError("content review checks are incomplete or not passed")


def validate_content_approval(approval: dict[str, Any], brief: dict[str, Any], variant: dict[str, Any], review: dict[str, Any], *, target_owner: str, work_product_approval: dict[str, Any] | None = None, expected_scope: str = "work_product", evidence_records: Iterable[dict[str, Any]] | None = None) -> None:
    validate_content_approval_inputs(brief, variant, review, target_owner=target_owner)
    try:
        validate_canonical_approval(
            approval,
            expected_decision="approved",
            expected_scope=expected_scope,
            expected_target_ref=variant.get("variant_id"),
            expected_target_owner=target_owner,
            evidence_records=evidence_records,
        )
    except ApprovalError as exc:
        raise ContentDomainError(str(exc)) from exc
    _validate_content_evidence_bindings(approval.get("evidence_refs", []), brief, variant, review, evidence_records)
    if approval.get("target_ref") != variant.get("variant_id") or approval.get("variant_ref") != variant.get("variant_id"):
        raise ContentDomainError("content approval variant binding mismatch")
    if approval.get("brief_ref") != brief.get("entity_id") or approval.get("brief_fingerprint") != _brief_binding_fingerprint(brief):
        raise ContentDomainError("content approval brief binding is stale")
    if approval.get("variant_fingerprint") != content_fingerprint(variant):
        raise ContentDomainError("content approval variant fingerprint is stale")
    if approval.get("review_ref") != review.get("review_id") or approval.get("review_fingerprint") != review.get("fingerprint"):
        raise ContentDomainError("content approval review binding is stale")
    if review.get("status") != "passed" or approval.get("target_owner") != target_owner:
        raise ContentDomainError("content approval owner or review status is invalid")
    if expected_scope == "external_release":
        if work_product_approval is None or approval.get("work_product_approval_ref") != work_product_approval.get("entity_id") or approval.get("work_product_approval_fingerprint") != content_fingerprint(work_product_approval):
            raise ContentDomainError("external release approval is not bound to the work-product approval")
        if work_product_approval.get("decision") != "approved" or work_product_approval.get("scope") != "work_product" or work_product_approval.get("target_ref") != variant.get("variant_id"):
            raise ContentDomainError("external release work-product approval is invalid")
        validate_content_approval(
            work_product_approval,
            brief,
            variant,
            review,
            target_owner=target_owner,
            expected_scope="work_product",
            evidence_records=evidence_records,
        )
    elif approval.get("work_product_approval_ref") or approval.get("work_product_approval_fingerprint"):
        raise ContentDomainError("work-product approval binding is not allowed on a creation approval")


__all__ = ["CONTENT_STATES", "ContentDomainError", "create_content_brief", "derive_content_output_contract", "make_content_approval", "make_content_variants", "review_content", "select_content_variant", "transition_content", "validate_content_approval"]
