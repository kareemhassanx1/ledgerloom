import hashlib

import pytest

from ledgerloom.serialization.errors import SerializationError
from ledgerloom.serialization.fingerprints import ContentFingerprint


def test_fingerprint_matches_sha256_of_exact_bytes():
    content = b'{"amount":"12.50","currency":"USD"}'

    fingerprint = ContentFingerprint.from_bytes(content)

    assert fingerprint.algorithm == "sha256"
    assert fingerprint.digest == hashlib.sha256(content).hexdigest()


def test_value_fingerprint_uses_canonical_key_order():
    first = ContentFingerprint.from_value({"z": 2, "a": 1})
    second = ContentFingerprint.from_value({"a": 1, "z": 2})

    assert first == second


def test_value_fingerprint_changes_when_content_changes():
    first = ContentFingerprint.from_value({"accepted": 2})
    second = ContentFingerprint.from_value({"accepted": 3})

    assert first != second


def test_text_round_trip_is_stable():
    original = ContentFingerprint.from_bytes(b"ledgerloom")

    parsed = ContentFingerprint.parse(str(original))

    assert parsed == original
    assert str(parsed).startswith("sha256:")


@pytest.mark.parametrize(
    "value,code",
    [
        ("", "fingerprint.format"),
        ("sha256", "fingerprint.format"),
        ("sha1:" + ("a" * 64), "fingerprint.algorithm"),
        ("sha256:" + ("A" * 64), "fingerprint.digest"),
        ("sha256:abc", "fingerprint.digest"),
        ("sha256:" + ("g" * 64), "fingerprint.digest"),
    ],
)
def test_parse_rejects_malformed_fingerprints(value, code):
    with pytest.raises(SerializationError) as captured:
        ContentFingerprint.parse(value)

    assert captured.value.code == code


def test_from_bytes_rejects_text_to_avoid_implicit_encoding():
    with pytest.raises(SerializationError) as captured:
        ContentFingerprint.from_bytes("ledgerloom")  # type: ignore[arg-type]

    assert captured.value.code == "fingerprint.content_type"


def test_matches_bytes_accepts_original_and_rejects_changed_content():
    fingerprint = ContentFingerprint.from_bytes(b"original")

    assert fingerprint.matches_bytes(b"original")
    assert not fingerprint.matches_bytes(b"changed")


def test_matches_value_canonicalizes_before_comparison():
    fingerprint = ContentFingerprint.from_value({"a": [1, 2], "b": True})

    assert fingerprint.matches_value({"b": True, "a": [1, 2]})


def test_require_bytes_raises_stable_mismatch_error():
    fingerprint = ContentFingerprint.from_bytes(b"original")

    with pytest.raises(SerializationError) as captured:
        fingerprint.require_bytes(b"changed")

    assert captured.value.code == "fingerprint.mismatch"
    assert captured.value.path == "$"

