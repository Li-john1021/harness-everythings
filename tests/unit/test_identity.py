"""单元测试：确定性序列化、指纹、ID 与封套。"""

from __future__ import annotations

import pytest

from harness_everythings.core.entities import (
    Envelope,
    IdentityError,
    derive_id,
    make_envelope,
    validate_id,
)
from harness_everythings.core.identity import (
    bytes_fingerprint,
    canonical_bytes,
    canonical_json,
    content_fingerprint,
    load_canonical,
)


class TestCanonicalJson:
    def test_key_order_does_not_matter(self):
        a = {"b": 1, "a": 2}
        b = {"a": 2, "b": 1}
        assert canonical_bytes(a) == canonical_bytes(b)

    def test_fingerprint_stable(self):
        value = {"k": ["v", 3, True, None], "unicode": "中文não"}
        assert content_fingerprint(value) == content_fingerprint(value)

    def test_raw_byte_fingerprint_detects_line_ending_change(self):
        assert bytes_fingerprint(b"a\r\nb") != bytes_fingerprint(b"a\nb")

    def test_nan_rejected(self):
        with pytest.raises(ValueError):
            canonical_bytes(float("nan"))

    def test_roundtrip(self):
        value = {"x": [1, 2, {"y": "z"}], "s": "ünïcode"}
        assert load_canonical(canonical_bytes(value)) == value

    def test_duplicate_keys_rejected(self):
        with pytest.raises(ValueError):
            load_canonical(b'{"a":1,"a":2}')

    def test_non_ascii_not_escaped(self):
        assert "中文" in canonical_json({"k": "中文"})


class TestDeriveId:
    def test_same_seed_same_id(self):
        assert derive_id("task", {"a": 1}) == derive_id("task", {"a": 1})

    def test_different_seed_different_id(self):
        assert derive_id("task", {"a": 1}) != derive_id("task", {"a": 2})

    def test_namespace_validated(self):
        with pytest.raises(IdentityError):
            derive_id("BadNamespace", {})

    def test_validate_id_roundtrip(self):
        entity_id = derive_id("task", {"n": 1})
        validate_id("task", entity_id)

    def test_validate_id_rejects_mismatch(self):
        role_id = derive_id("role", {"n": 1})
        with pytest.raises(IdentityError):
            validate_id("task", role_id)


class TestEnvelope:
    def test_make_envelope_deterministic(self):
        now = "2026-08-16T00:00:00Z"
        e1 = make_envelope("plan", {"goal": "x"}, "user:approval-1", now)
        e2 = make_envelope("plan", {"goal": "x"}, "user:approval-1", now)
        assert e1 == e2

    def test_envelope_record_shape(self):
        now = "2026-08-16T00:00:00Z"
        e = make_envelope("plan", {"goal": "x"}, "user:approval-1", now)
        record = e.to_record()
        assert record["schema_version"] == "1.0"
        assert record["entity_type"] == "plan"
        assert record["source_ref"] == "user:approval-1"
