"""Versioned schema documents and codec registration."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, cast

from ledgerloom.domain.errors import DomainValidationError
from ledgerloom.serialization.canonical import JsonValue, canonical_json_bytes, loads_canonical_json
from ledgerloom.serialization.errors import SerializationError
from ledgerloom.serialization.fingerprints import ContentFingerprint

_SCHEMA_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")
_SCHEMA_VERSION_PATTERN = re.compile(r"^([1-9][0-9]*)\.(0|[1-9][0-9]*)$")
_MAX_SCHEMA_NAME_LENGTH = 128
_MAX_VERSION_COMPONENT = 65_535
_ENVELOPE_FIELDS = frozenset({"schema", "version", "payload"})


@dataclass(frozen=True, slots=True, order=True)
class SchemaName:
    """A dotted lowercase name that remains stable across Python refactors."""

    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str) or not _SCHEMA_NAME_PATTERN.fullmatch(self.value):
            raise SerializationError(
                code="schema.name_format",
                path="$.schema",
                message="schema name must be a dotted lowercase identifier",
            )
        if len(self.value) > _MAX_SCHEMA_NAME_LENGTH:
            raise SerializationError(
                code="schema.name_too_long",
                path="$.schema",
                message=f"schema name must contain at most {_MAX_SCHEMA_NAME_LENGTH} characters",
            )

    @classmethod
    def parse(cls, value: str) -> SchemaName:
        return cls(value)

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True, order=True)
class SchemaVersion:
    """A comparable major/minor schema version."""

    major: int
    minor: int = 0

    def __post_init__(self) -> None:
        if isinstance(self.major, bool) or not isinstance(self.major, int):
            raise SerializationError(
                code="schema.version_major_type",
                path="$.version",
                message="major version must be an integer",
            )
        if isinstance(self.minor, bool) or not isinstance(self.minor, int):
            raise SerializationError(
                code="schema.version_minor_type",
                path="$.version",
                message="minor version must be an integer",
            )
        if not 1 <= self.major <= _MAX_VERSION_COMPONENT:
            raise SerializationError(
                code="schema.version_major_range",
                path="$.version",
                message=f"major version must be between 1 and {_MAX_VERSION_COMPONENT}",
            )
        if not 0 <= self.minor <= _MAX_VERSION_COMPONENT:
            raise SerializationError(
                code="schema.version_minor_range",
                path="$.version",
                message=f"minor version must be between 0 and {_MAX_VERSION_COMPONENT}",
            )

    @classmethod
    def parse(cls, value: str) -> SchemaVersion:
        if not isinstance(value, str):
            raise SerializationError(
                code="schema.version_type",
                path="$.version",
                message="schema version must be text",
            )
        match = _SCHEMA_VERSION_PATTERN.fullmatch(value)
        if match is None:
            raise SerializationError(
                code="schema.version_format",
                path="$.version",
                message="schema version must use canonical major.minor decimal text",
            )
        return cls(int(match.group(1)), int(match.group(2)))

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}"


@dataclass(frozen=True, slots=True, order=True)
class SchemaRef:
    """Identify one exact version of a serialized contract."""

    name: SchemaName
    version: SchemaVersion

    def __post_init__(self) -> None:
        if not isinstance(self.name, SchemaName):
            raise SerializationError(
                code="schema.ref_name_type",
                path="$.schema",
                message="schema reference name must be a SchemaName",
            )
        if not isinstance(self.version, SchemaVersion):
            raise SerializationError(
                code="schema.ref_version_type",
                path="$.version",
                message="schema reference version must be a SchemaVersion",
            )

    @classmethod
    def parse(cls, name: str, version: str) -> SchemaRef:
        return cls(SchemaName.parse(name), SchemaVersion.parse(version))

    def __str__(self) -> str:
        return f"{self.name}@{self.version}"


def _parse_envelope(content: bytes) -> tuple[SchemaRef, JsonValue]:
    decoded = loads_canonical_json(content)
    if not isinstance(decoded, dict):
        raise SerializationError(
            code="schema.envelope_type",
            path="$",
            message="schema document must contain an object envelope",
        )
    fields = frozenset(decoded)
    if fields != _ENVELOPE_FIELDS:
        missing = sorted(_ENVELOPE_FIELDS - fields)
        extra = sorted(fields - _ENVELOPE_FIELDS)
        details: list[str] = []
        if missing:
            details.append(f"missing {', '.join(missing)}")
        if extra:
            details.append(f"unexpected {', '.join(extra)}")
        raise SerializationError(
            code="schema.envelope_fields",
            path="$",
            message="schema envelope fields are invalid: " + "; ".join(details),
        )

    name = decoded["schema"]
    version = decoded["version"]
    if not isinstance(name, str):
        raise SerializationError(
            code="schema.name_type",
            path="$.schema",
            message="schema name must be text",
        )
    if not isinstance(version, str):
        raise SerializationError(
            code="schema.version_type",
            path="$.version",
            message="schema version must be text",
        )
    return SchemaRef.parse(name, version), decoded["payload"]


@dataclass(frozen=True, slots=True)
class SchemaDocument:
    """Immutable canonical bytes paired with their parsed contract and fingerprint."""

    schema: SchemaRef
    content: bytes
    fingerprint: ContentFingerprint

    def __post_init__(self) -> None:
        if not isinstance(self.schema, SchemaRef):
            raise SerializationError(
                code="schema.document_ref_type",
                path="$.schema",
                message="document schema must be a SchemaRef",
            )
        if not isinstance(self.content, bytes):
            raise SerializationError(
                code="schema.document_content_type",
                path="$",
                message="document content must be bytes",
            )
        if not isinstance(self.fingerprint, ContentFingerprint):
            raise SerializationError(
                code="schema.document_fingerprint_type",
                path="$",
                message="document fingerprint must be a ContentFingerprint",
            )
        self.fingerprint.require_bytes(self.content)
        parsed_ref, _ = _parse_envelope(self.content)
        if parsed_ref != self.schema:
            raise SerializationError(
                code="schema.document_ref_mismatch",
                path="$",
                message="document metadata does not match its declared schema reference",
            )

    @classmethod
    def build(cls, schema: SchemaRef, payload: object) -> SchemaDocument:
        if not isinstance(schema, SchemaRef):
            raise SerializationError(
                code="schema.document_ref_type",
                path="$.schema",
                message="document schema must be a SchemaRef",
            )
        content = canonical_json_bytes(
            {"schema": str(schema.name), "version": str(schema.version), "payload": payload}
        )
        return cls(schema, content, ContentFingerprint.from_bytes(content))

    @classmethod
    def parse(
        cls,
        content: bytes,
        *,
        expected_fingerprint: ContentFingerprint | None = None,
    ) -> SchemaDocument:
        schema, _ = _parse_envelope(content)
        fingerprint = ContentFingerprint.from_bytes(content)
        if expected_fingerprint is not None:
            if not isinstance(expected_fingerprint, ContentFingerprint):
                raise SerializationError(
                    code="schema.expected_fingerprint_type",
                    path="$",
                    message="expected fingerprint must be a ContentFingerprint",
                )
            expected_fingerprint.require_bytes(content)
            fingerprint = expected_fingerprint
        return cls(schema, content, fingerprint)

    @property
    def payload(self) -> JsonValue:
        """Decode and return a new payload tree on each access."""

        _, payload = _parse_envelope(self.content)
        return payload

    def __bytes__(self) -> bytes:
        return self.content


class SchemaCodec[T](Protocol):
    """Translate one Python type to and from one exact schema version."""

    ref: SchemaRef
    python_type: type[T]

    def encode(self, value: T) -> object: ...

    def decode(self, payload: JsonValue) -> T: ...


@dataclass(frozen=True, slots=True)
class FunctionSchemaCodec[T]:
    """A schema codec assembled from explicit encode and decode callables."""

    ref: SchemaRef
    python_type: type[T]
    encoder: Callable[[T], object]
    decoder: Callable[[JsonValue], T]

    def __post_init__(self) -> None:
        if not isinstance(self.ref, SchemaRef):
            raise SerializationError(
                code="schema.codec_ref_type",
                path="$.schema",
                message="codec reference must be a SchemaRef",
            )
        if not isinstance(self.python_type, type):
            raise SerializationError(
                code="schema.codec_python_type",
                path="$",
                message="codec python_type must be a type",
            )
        if not callable(self.encoder) or not callable(self.decoder):
            raise SerializationError(
                code="schema.codec_callable",
                path="$",
                message="codec encoder and decoder must be callable",
            )

    def encode(self, value: T) -> object:
        return self.encoder(value)

    def decode(self, payload: JsonValue) -> T:
        return self.decoder(payload)


class SchemaRegistry:
    """Select codecs by exact reference or latest version for a Python type."""

    def __init__(self) -> None:
        self._codecs: dict[SchemaRef, SchemaCodec[object]] = {}
        self._refs_by_type: dict[type[object], list[SchemaRef]] = {}

    def register[T](self, codec: SchemaCodec[T]) -> None:
        if not isinstance(codec.ref, SchemaRef) or not isinstance(codec.python_type, type):
            raise SerializationError(
                code="schema.codec_contract",
                path="$",
                message="codec must expose a SchemaRef and Python type",
            )
        if codec.ref in self._codecs:
            raise SerializationError(
                code="schema.duplicate_codec",
                path="$",
                message=f"codec for {codec.ref} is already registered",
            )
        self._codecs[codec.ref] = cast(SchemaCodec[object], codec)
        refs = self._refs_by_type.setdefault(codec.python_type, [])
        refs.append(codec.ref)
        refs.sort(key=lambda ref: (ref.version, ref.name))

    def available_versions(self, name: SchemaName | str) -> tuple[SchemaVersion, ...]:
        resolved_name = name if isinstance(name, SchemaName) else SchemaName.parse(name)
        return tuple(sorted(ref.version for ref in self._codecs if ref.name == resolved_name))

    def latest_ref_for(self, python_type: type[object]) -> SchemaRef:
        if not isinstance(python_type, type):
            raise SerializationError(
                code="schema.python_type",
                path="$",
                message="python_type must be a type",
            )
        refs = self._refs_by_type.get(python_type)
        if not refs:
            raise SerializationError(
                code="schema.unregistered_type",
                path="$",
                message=f"no schema codec is registered for {python_type.__name__}",
            )
        return max(refs, key=lambda ref: (ref.version, ref.name))

    def encode(self, value: object, *, schema: SchemaRef | None = None) -> SchemaDocument:
        ref = schema if schema is not None else self.latest_ref_for(type(value))
        if not isinstance(ref, SchemaRef):
            raise SerializationError(
                code="schema.encode_ref_type",
                path="$.schema",
                message="schema must be a SchemaRef",
            )
        codec = self._codecs.get(ref)
        if codec is None:
            raise SerializationError(
                code="schema.unknown",
                path="$.schema",
                message=f"no codec is registered for {ref}",
            )
        if not isinstance(value, codec.python_type):
            raise SerializationError(
                code="schema.value_type",
                path="$.payload",
                message=f"codec {ref} requires {codec.python_type.__name__}",
            )
        try:
            payload = codec.encode(value)
        except SerializationError:
            raise
        except DomainValidationError as error:
            raise SerializationError(
                code="schema.payload_invalid",
                path=f"$.payload.{error.field}",
                message=error.message,
            ) from error
        return SchemaDocument.build(ref, payload)

    def decode(
        self,
        document: SchemaDocument | bytes,
        *,
        expected_fingerprint: ContentFingerprint | None = None,
    ) -> object:
        resolved = (
            SchemaDocument.parse(document, expected_fingerprint=expected_fingerprint)
            if isinstance(document, bytes)
            else document
        )
        if not isinstance(resolved, SchemaDocument):
            raise SerializationError(
                code="schema.document_type",
                path="$",
                message="document must be SchemaDocument or bytes",
            )
        if expected_fingerprint is not None:
            expected_fingerprint.require_bytes(resolved.content)
        codec = self._codecs.get(resolved.schema)
        if codec is None:
            raise SerializationError(
                code="schema.unknown",
                path="$.schema",
                message=f"no codec is registered for {resolved.schema}",
            )
        try:
            value = codec.decode(resolved.payload)
        except SerializationError:
            raise
        except DomainValidationError as error:
            raise SerializationError(
                code="schema.payload_invalid",
                path=f"$.payload.{error.field}",
                message=error.message,
            ) from error
        if not isinstance(value, codec.python_type):
            raise SerializationError(
                code="schema.decoder_result_type",
                path="$.payload",
                message=f"codec {resolved.schema} returned the wrong Python type",
            )
        return value
