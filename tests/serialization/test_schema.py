from dataclasses import dataclass

import pytest

from ledgerloom.domain.errors import DomainValidationError
from ledgerloom.serialization.errors import SerializationError
from ledgerloom.serialization.fingerprints import ContentFingerprint
from ledgerloom.serialization.schema import (
    FunctionSchemaCodec,
    SchemaDocument,
    SchemaName,
    SchemaRef,
    SchemaRegistry,
    SchemaVersion,
)


@dataclass(frozen=True)
class Widget:
    name: str
    enabled: bool = True


def decode_widget(payload):
    if not isinstance(payload, dict):
        raise SerializationError(code="widget.type", path="$.payload", message="must be object")
    name = payload.get("name")
    enabled = payload.get("enabled", True)
    if not isinstance(name, str):
        raise SerializationError(
            code="widget.name", path="$.payload.name", message="must be text"
        )
    if not isinstance(enabled, bool):
        raise SerializationError(
            code="widget.enabled", path="$.payload.enabled", message="must be boolean"
        )
    return Widget(name, enabled)


def widget_codec(version: str) -> FunctionSchemaCodec[Widget]:
    ref = SchemaRef.parse("example.widget", version)
    if version == "1.0":
        return FunctionSchemaCodec(
            ref,
            Widget,
            lambda widget: {"name": widget.name},
            decode_widget,
        )
    return FunctionSchemaCodec(
        ref,
        Widget,
        lambda widget: {"enabled": widget.enabled, "name": widget.name},
        decode_widget,
    )


@pytest.mark.parametrize(
    "value",
    ["ledgerloom.source_record", "ledgerloom.ledger_entry", "team_1.batch_v2"],
)
def test_schema_name_accepts_dotted_lowercase_identifiers(value):
    assert str(SchemaName(value)) == value


@pytest.mark.parametrize(
    "value",
    ["", "ledgerloom", ".ledgerloom.record", "LedgerLoom.record", "ledgerloom.Record", "a..b"],
)
def test_schema_name_rejects_unstable_formats(value):
    with pytest.raises(SerializationError) as captured:
        SchemaName(value)

    assert captured.value.code == "schema.name_format"


def test_schema_name_enforces_length_limit():
    with pytest.raises(SerializationError) as captured:
        SchemaName("a." + ("b" * 127))

    assert captured.value.code == "schema.name_too_long"


@pytest.mark.parametrize("value,major,minor", [("1.0", 1, 0), ("2.3", 2, 3), ("65535.9", 65535, 9)])
def test_schema_version_parse_round_trip(value, major, minor):
    version = SchemaVersion.parse(value)

    assert version == SchemaVersion(major, minor)
    assert str(version) == value


@pytest.mark.parametrize("value", ["", "1", "v1.0", "01.0", "1.00", "0.1", "1.-1", "1.2.3"])
def test_schema_version_rejects_noncanonical_text(value):
    with pytest.raises(SerializationError) as captured:
        SchemaVersion.parse(value)

    assert captured.value.code == "schema.version_format"


@pytest.mark.parametrize(
    "major,minor,code",
    [
        (0, 0, "schema.version_major_range"),
        (65_536, 0, "schema.version_major_range"),
        (1, -1, "schema.version_minor_range"),
        (1, 65_536, "schema.version_minor_range"),
    ],
)
def test_schema_version_enforces_component_ranges(major, minor, code):
    with pytest.raises(SerializationError) as captured:
        SchemaVersion(major, minor)

    assert captured.value.code == code


def test_schema_versions_have_semantic_tuple_ordering():
    assert SchemaVersion(1, 9) < SchemaVersion(2, 0)
    assert SchemaVersion(2, 0) < SchemaVersion(2, 1)


def test_schema_ref_has_stable_text_form():
    ref = SchemaRef.parse("ledgerloom.source_record", "1.0")

    assert str(ref) == "ledgerloom.source_record@1.0"


def test_document_build_produces_exact_envelope_and_fingerprint():
    ref = SchemaRef.parse("example.widget", "1.0")

    document = SchemaDocument.build(ref, {"name": "alpha", "enabled": True})

    assert bytes(document) == (
        b'{"payload":{"enabled":true,"name":"alpha"},'
        b'"schema":"example.widget","version":"1.0"}'
    )
    assert document.fingerprint == ContentFingerprint.from_bytes(document.content)


def test_document_parse_recovers_reference_and_detached_payload():
    original = SchemaDocument.build(SchemaRef.parse("example.widget", "2.0"), {"name": "a"})

    parsed = SchemaDocument.parse(original.content)
    first_payload = parsed.payload
    assert isinstance(first_payload, dict)
    first_payload["name"] = "changed"

    assert parsed.schema == original.schema
    assert parsed.payload == {"name": "a"}


def test_document_parse_accepts_matching_expected_fingerprint():
    original = SchemaDocument.build(SchemaRef.parse("example.widget", "1.0"), {"name": "a"})

    parsed = SchemaDocument.parse(
        original.content,
        expected_fingerprint=original.fingerprint,
    )

    assert parsed == original


def test_document_parse_rejects_mismatched_expected_fingerprint():
    original = SchemaDocument.build(SchemaRef.parse("example.widget", "1.0"), {"name": "a"})
    other = ContentFingerprint.from_bytes(b"other")

    with pytest.raises(SerializationError) as captured:
        SchemaDocument.parse(original.content, expected_fingerprint=other)

    assert captured.value.code == "fingerprint.mismatch"


@pytest.mark.parametrize(
    "document,code",
    [
        (b'[]', "schema.envelope_type"),
        (b'{"payload":{},"schema":"example.widget"}', "schema.envelope_fields"),
        (
            b'{"extra":0,"payload":{},"schema":"example.widget","version":"1.0"}',
            "schema.envelope_fields",
        ),
        (b'{"payload":{},"schema":3,"version":"1.0"}', "schema.name_type"),
        (b'{"payload":{},"schema":"example.widget","version":1}', "schema.version_type"),
    ],
)
def test_document_parse_rejects_invalid_envelope_contract(document, code):
    with pytest.raises(SerializationError) as captured:
        SchemaDocument.parse(document)

    assert captured.value.code == code


def test_document_constructor_rejects_fingerprint_that_does_not_match_content():
    built = SchemaDocument.build(SchemaRef.parse("example.widget", "1.0"), {"name": "a"})

    with pytest.raises(SerializationError) as captured:
        SchemaDocument(built.schema, built.content, ContentFingerprint.from_bytes(b"other"))

    assert captured.value.code == "fingerprint.mismatch"


def test_document_constructor_rejects_reference_that_disagrees_with_content():
    built = SchemaDocument.build(SchemaRef.parse("example.widget", "1.0"), {"name": "a"})

    with pytest.raises(SerializationError) as captured:
        SchemaDocument(
            SchemaRef.parse("example.widget", "2.0"),
            built.content,
            built.fingerprint,
        )

    assert captured.value.code == "schema.document_ref_mismatch"


def test_registry_encodes_with_latest_registered_version():
    registry = SchemaRegistry()
    registry.register(widget_codec("1.0"))
    registry.register(widget_codec("2.0"))

    document = registry.encode(Widget("alpha", False))

    assert document.schema == SchemaRef.parse("example.widget", "2.0")
    assert document.payload == {"enabled": False, "name": "alpha"}


def test_registry_can_encode_with_explicit_older_version():
    registry = SchemaRegistry()
    old = widget_codec("1.0")
    registry.register(old)
    registry.register(widget_codec("2.0"))

    document = registry.encode(Widget("alpha", False), schema=old.ref)

    assert document.schema == old.ref
    assert document.payload == {"name": "alpha"}


def test_registry_decodes_document_or_raw_bytes():
    registry = SchemaRegistry()
    registry.register(widget_codec("2.0"))
    document = registry.encode(Widget("alpha", False))

    assert registry.decode(document) == Widget("alpha", False)
    assert registry.decode(document.content) == Widget("alpha", False)


def test_registry_checks_expected_fingerprint_when_decoding():
    registry = SchemaRegistry()
    registry.register(widget_codec("2.0"))
    document = registry.encode(Widget("alpha"))

    assert registry.decode(
        document.content,
        expected_fingerprint=document.fingerprint,
    ) == Widget("alpha")


def test_registry_rejects_duplicate_exact_codec():
    registry = SchemaRegistry()
    registry.register(widget_codec("1.0"))

    with pytest.raises(SerializationError) as captured:
        registry.register(widget_codec("1.0"))

    assert captured.value.code == "schema.duplicate_codec"


def test_registry_reports_available_versions_in_order():
    registry = SchemaRegistry()
    registry.register(widget_codec("2.1"))
    registry.register(widget_codec("1.0"))
    registry.register(widget_codec("2.0"))

    assert registry.available_versions("example.widget") == (
        SchemaVersion(1, 0),
        SchemaVersion(2, 0),
        SchemaVersion(2, 1),
    )


def test_registry_rejects_unregistered_python_type():
    registry = SchemaRegistry()

    with pytest.raises(SerializationError) as captured:
        registry.encode("not-registered")

    assert captured.value.code == "schema.unregistered_type"


def test_registry_rejects_unknown_explicit_schema():
    registry = SchemaRegistry()
    registry.register(widget_codec("1.0"))

    with pytest.raises(SerializationError) as captured:
        registry.encode(Widget("alpha"), schema=SchemaRef.parse("example.widget", "9.0"))

    assert captured.value.code == "schema.unknown"


def test_registry_rejects_value_that_does_not_match_explicit_codec():
    registry = SchemaRegistry()
    codec = widget_codec("1.0")
    registry.register(codec)

    with pytest.raises(SerializationError) as captured:
        registry.encode("wrong", schema=codec.ref)

    assert captured.value.code == "schema.value_type"


def test_registry_rejects_document_for_unknown_schema():
    registry = SchemaRegistry()
    document = SchemaDocument.build(SchemaRef.parse("unknown.value", "1.0"), {})

    with pytest.raises(SerializationError) as captured:
        registry.decode(document)

    assert captured.value.code == "schema.unknown"


def test_registry_translates_domain_validation_failure_from_decoder():
    def invalid_decoder(payload):
        raise DomainValidationError(
            code="widget.invalid",
            field="name",
            message="must be usable",
        )

    registry = SchemaRegistry()
    registry.register(
        FunctionSchemaCodec(
            SchemaRef.parse("example.widget", "1.0"),
            Widget,
            lambda widget: {"name": widget.name},
            invalid_decoder,
        )
    )
    document = SchemaDocument.build(SchemaRef.parse("example.widget", "1.0"), {"name": "a"})

    with pytest.raises(SerializationError) as captured:
        registry.decode(document)

    assert captured.value.code == "schema.payload_invalid"
    assert captured.value.path == "$.payload.name"


def test_registry_detects_decoder_returning_wrong_type():
    registry = SchemaRegistry()
    registry.register(
        FunctionSchemaCodec(
            SchemaRef.parse("example.widget", "1.0"),
            Widget,
            lambda widget: {"name": widget.name},
            lambda payload: "not-a-widget",  # type: ignore[arg-type,return-value]
        )
    )
    document = SchemaDocument.build(SchemaRef.parse("example.widget", "1.0"), {"name": "a"})

    with pytest.raises(SerializationError) as captured:
        registry.decode(document)

    assert captured.value.code == "schema.decoder_result_type"

