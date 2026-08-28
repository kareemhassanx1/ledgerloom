"""Canonical JSON encoding for stable storage and content hashing."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from ledgerloom.serialization.errors import SerializationError

type JsonScalar = bool | int | str | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]

_MAX_SAFE_INTEGER = (2**53) - 1


def _child_path(path: str, key: str) -> str:
    if key.isidentifier():
        return f"{path}.{key}"
    escaped = key.replace("\\", "\\\\").replace('"', '\\"')
    return f'{path}["{escaped}"]'


def _validate_string(value: str, *, path: str, role: str) -> str:
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as error:
        raise SerializationError(
            code=f"json.{role}_unicode",
            path=path,
            message=f"{role} must contain valid Unicode without surrogate code points",
        ) from error
    return value


def _copy_json_value(value: object, *, path: str, active: set[int]) -> JsonValue:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, str):
        return _validate_string(value, path=path, role="string")
    if isinstance(value, int):
        if not -_MAX_SAFE_INTEGER <= value <= _MAX_SAFE_INTEGER:
            raise SerializationError(
                code="json.integer_range",
                path=path,
                message=f"integer must be between {-_MAX_SAFE_INTEGER} and {_MAX_SAFE_INTEGER}",
            )
        return value
    if isinstance(value, float):
        raise SerializationError(
            code="json.float_unsupported",
            path=path,
            message="binary floating-point numbers are not supported; encode exact values as text",
        )

    if isinstance(value, Mapping):
        identity = id(value)
        if identity in active:
            raise SerializationError(
                code="json.cycle",
                path=path,
                message="containers must not contain reference cycles",
            )
        active.add(identity)
        try:
            copied: dict[str, JsonValue] = {}
            for key, item in value.items():
                if not isinstance(key, str):
                    raise SerializationError(
                        code="json.key_type",
                        path=path,
                        message="object keys must be strings",
                    )
                valid_key = _validate_string(key, path=path, role="key")
                copied[valid_key] = _copy_json_value(
                    item,
                    path=_child_path(path, valid_key),
                    active=active,
                )
            return copied
        finally:
            active.remove(identity)

    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        identity = id(value)
        if identity in active:
            raise SerializationError(
                code="json.cycle",
                path=path,
                message="containers must not contain reference cycles",
            )
        active.add(identity)
        try:
            return [
                _copy_json_value(item, path=f"{path}[{index}]", active=active)
                for index, item in enumerate(value)
            ]
        finally:
            active.remove(identity)

    raise SerializationError(
        code="json.value_type",
        path=path,
        message=f"values of type {type(value).__name__} are not supported",
    )


def canonical_json_bytes(value: object) -> bytes:
    """Encode supported JSON data into one deterministic UTF-8 representation.

    Objects are sorted by key, insignificant whitespace is omitted, Unicode is
    emitted directly, and integers are limited to the interoperable JSON safe
    range. Floats are deliberately rejected so callers cannot hash a value that
    already lost decimal precision.
    """

    copied = _copy_json_value(value, path="$", active=set())
    rendered = json.dumps(
        copied,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return rendered.encode("utf-8")


def canonical_json_text(value: object) -> str:
    """Return the canonical JSON representation as Unicode text."""

    return canonical_json_bytes(value).decode("utf-8")


def _reject_float_token(token: str) -> Any:
    raise SerializationError(
        code="json.float_unsupported",
        path="$",
        message=f"floating-point token {token!r} is not supported",
    )


def _reject_constant(token: str) -> Any:
    raise SerializationError(
        code="json.constant_unsupported",
        path="$",
        message=f"non-standard numeric constant {token!r} is not supported",
    )


def _object_from_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise SerializationError(
                code="json.duplicate_key",
                path="$",
                message=f"object key {key!r} occurs more than once",
            )
        result[key] = value
    return result


def loads_canonical_json(document: bytes) -> JsonValue:
    """Decode bytes only when they already use the canonical representation."""

    if not isinstance(document, bytes):
        raise SerializationError(
            code="json.document_type",
            path="$",
            message="document must be bytes",
        )
    try:
        text = document.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise SerializationError(
            code="json.document_utf8",
            path="$",
            message="document must be valid UTF-8",
        ) from error
    try:
        decoded = json.loads(
            text,
            object_pairs_hook=_object_from_pairs,
            parse_float=_reject_float_token,
            parse_constant=_reject_constant,
        )
    except json.JSONDecodeError as error:
        raise SerializationError(
            code="json.syntax",
            path="$",
            message=f"invalid JSON at line {error.lineno}, column {error.colno}",
        ) from error

    copied = _copy_json_value(decoded, path="$", active=set())
    canonical = canonical_json_bytes(copied)
    if canonical != document:
        raise SerializationError(
            code="json.non_canonical",
            path="$",
            message="document does not use the canonical JSON representation",
        )
    return copied


def is_canonical_json(document: object) -> bool:
    """Return whether an object is a canonical JSON byte document."""

    if not isinstance(document, bytes):
        return False
    try:
        loads_canonical_json(document)
    except SerializationError:
        return False
    return True
