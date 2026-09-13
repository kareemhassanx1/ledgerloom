"""Content fingerprints over canonical documents."""

from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import dataclass
from typing import Self

from ledgerloom.serialization.canonical import canonical_json_bytes
from ledgerloom.serialization.errors import SerializationError

_SHA256_DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True, order=True)
class ContentFingerprint:
    """A lowercase SHA-256 digest with an explicit algorithm label."""

    algorithm: str
    digest: str

    def __post_init__(self) -> None:
        if self.algorithm != "sha256":
            raise SerializationError(
                code="fingerprint.algorithm",
                path="$.algorithm",
                message="only sha256 fingerprints are supported",
            )
        if not isinstance(self.digest, str) or not _SHA256_DIGEST_PATTERN.fullmatch(self.digest):
            raise SerializationError(
                code="fingerprint.digest",
                path="$.digest",
                message="digest must contain exactly 64 lowercase hexadecimal characters",
            )

    @classmethod
    def from_bytes(cls, content: bytes) -> Self:
        if not isinstance(content, bytes):
            raise SerializationError(
                code="fingerprint.content_type",
                path="$",
                message="content must be bytes",
            )
        return cls("sha256", hashlib.sha256(content).hexdigest())

    @classmethod
    def from_value(cls, value: object) -> Self:
        return cls.from_bytes(canonical_json_bytes(value))

    @classmethod
    def parse(cls, value: str) -> Self:
        if not isinstance(value, str):
            raise SerializationError(
                code="fingerprint.text_type",
                path="$",
                message="fingerprint must be text",
            )
        algorithm, separator, digest = value.partition(":")
        if separator != ":":
            raise SerializationError(
                code="fingerprint.format",
                path="$",
                message="fingerprint must use the form algorithm:digest",
            )
        return cls(algorithm, digest)

    def matches_bytes(self, content: bytes) -> bool:
        candidate = type(self).from_bytes(content)
        return hmac.compare_digest(str(self), str(candidate))

    def matches_value(self, value: object) -> bool:
        return self.matches_bytes(canonical_json_bytes(value))

    def require_bytes(self, content: bytes) -> None:
        """Raise a stable error when content does not match this fingerprint."""

        if not self.matches_bytes(content):
            raise SerializationError(
                code="fingerprint.mismatch",
                path="$",
                message="content does not match its declared fingerprint",
            )

    def __str__(self) -> str:
        return f"{self.algorithm}:{self.digest}"
