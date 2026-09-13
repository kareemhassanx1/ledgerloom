from decimal import Decimal

import pytest

from ledgerloom.serialization.canonical import (
    canonical_json_bytes,
    canonical_json_text,
    is_canonical_json,
    loads_canonical_json,
)
from ledgerloom.serialization.errors import SerializationError


def test_sorts_object_keys_and_removes_insignificant_whitespace():
    value = {"z": 3, "a": 1, "middle": 2}

    assert canonical_json_bytes(value) == b'{"a":1,"middle":2,"z":3}'


def test_canonicalizes_nested_objects_and_sequences_recursively():
    value = {"items": ({"b": True, "a": None}, [3, 2, 1]), "name": "batch"}

    assert canonical_json_text(value) == ('{"items":[{"a":null,"b":true},[3,2,1]],"name":"batch"}')


def test_emits_unicode_as_utf8_instead_of_ascii_escape_sequences():
    document = canonical_json_bytes({"city": "القاهرة", "symbol": "€"})

    assert document == '{"city":"القاهرة","symbol":"€"}'.encode()


@pytest.mark.parametrize("value", [0, 1, -1, (2**53) - 1, -((2**53) - 1)])
def test_accepts_interoperable_integer_range(value):
    assert loads_canonical_json(canonical_json_bytes(value)) == value


@pytest.mark.parametrize("value", [2**53, -(2**53)])
def test_rejects_integers_outside_interoperable_range(value):
    with pytest.raises(SerializationError) as captured:
        canonical_json_bytes({"value": value})

    assert captured.value.code == "json.integer_range"
    assert captured.value.path == "$.value"


@pytest.mark.parametrize("value", [0.0, 1.25, float("inf"), float("nan")])
def test_rejects_binary_floating_point_values(value):
    with pytest.raises(SerializationError) as captured:
        canonical_json_bytes({"amount": value})

    assert captured.value.code == "json.float_unsupported"
    assert captured.value.path == "$.amount"


@pytest.mark.parametrize("value", [Decimal("1.2"), b"raw", bytearray(b"raw"), {1, 2}])
def test_rejects_values_outside_the_supported_json_model(value):
    with pytest.raises(SerializationError) as captured:
        canonical_json_bytes({"value": value})

    assert captured.value.code == "json.value_type"
    assert captured.value.path == "$.value"


def test_rejects_non_string_object_keys():
    with pytest.raises(SerializationError) as captured:
        canonical_json_bytes({1: "value"})

    assert captured.value.code == "json.key_type"
    assert captured.value.path == "$"


def test_rejects_reference_cycle_and_reports_its_location():
    value: list[object] = []
    value.append({"again": value})

    with pytest.raises(SerializationError) as captured:
        canonical_json_bytes(value)

    assert captured.value.code == "json.cycle"
    assert captured.value.path == "$[0].again"


def test_allows_reusing_an_acyclic_container_in_multiple_locations():
    shared = {"entry": "same"}

    document = canonical_json_bytes({"first": shared, "second": shared})

    assert document == b'{"first":{"entry":"same"},"second":{"entry":"same"}}'


def test_rejects_surrogate_code_point_in_value():
    with pytest.raises(SerializationError) as captured:
        canonical_json_bytes({"text": "\ud800"})

    assert captured.value.code == "json.string_unicode"
    assert captured.value.path == "$.text"


def test_rejects_surrogate_code_point_in_key():
    with pytest.raises(SerializationError) as captured:
        canonical_json_bytes({"\ud800": "text"})

    assert captured.value.code == "json.key_unicode"


def test_loads_canonical_document_to_detached_data():
    document = b'{"batch":{"accepted":2,"rejected":1},"complete":false}'

    decoded = loads_canonical_json(document)

    assert decoded == {"batch": {"accepted": 2, "rejected": 1}, "complete": False}


@pytest.mark.parametrize(
    "document",
    [
        b'{"b":2,"a":1}',
        b'{"a": 1}',
        b'{"a":1}\n',
        b"[1, 2]",
        b'"\\u20ac"',
    ],
)
def test_load_rejects_valid_but_noncanonical_json(document):
    with pytest.raises(SerializationError) as captured:
        loads_canonical_json(document)

    assert captured.value.code == "json.non_canonical"


def test_load_rejects_duplicate_object_keys():
    with pytest.raises(SerializationError) as captured:
        loads_canonical_json(b'{"entry":1,"entry":2}')

    assert captured.value.code == "json.duplicate_key"


@pytest.mark.parametrize("document", [b'{"a":}', b"[1,", b"not-json"])
def test_load_translates_json_syntax_errors(document):
    with pytest.raises(SerializationError) as captured:
        loads_canonical_json(document)

    assert captured.value.code == "json.syntax"
    assert "line" in captured.value.message


@pytest.mark.parametrize("document", [b"1.5", b"1e3", b'{"value":2.0}'])
def test_load_rejects_float_tokens_before_canonicality_check(document):
    with pytest.raises(SerializationError) as captured:
        loads_canonical_json(document)

    assert captured.value.code == "json.float_unsupported"


@pytest.mark.parametrize("document", [b"NaN", b"Infinity", b"-Infinity"])
def test_load_rejects_nonstandard_numeric_constants(document):
    with pytest.raises(SerializationError) as captured:
        loads_canonical_json(document)

    assert captured.value.code == "json.constant_unsupported"


def test_load_rejects_invalid_utf8():
    with pytest.raises(SerializationError) as captured:
        loads_canonical_json(b'"\xff"')

    assert captured.value.code == "json.document_utf8"


def test_load_requires_bytes_instead_of_implicitly_encoding_text():
    with pytest.raises(SerializationError) as captured:
        loads_canonical_json("{}")  # type: ignore[arg-type]

    assert captured.value.code == "json.document_type"


def test_is_canonical_json_is_a_non_raising_probe():
    assert is_canonical_json(b'{"a":1}')
    assert not is_canonical_json(b'{ "a": 1 }')
    assert not is_canonical_json("{}")
