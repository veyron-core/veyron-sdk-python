"""SDK error types, mirroring veyron-wire's `WireError` enum.

Rust `WireError` is a single enum with payload-carrying variants; Python gets
a hierarchy instead — one exception class per variant, all under `VeyronError`
so callers can catch the base or a specific variant.
"""


class VeyronError(Exception):
    """Base class for every Veyron SDK error (mirrors `WireError`)."""


class VeyronIoError(VeyronError):
    """Underlying I/O failure (mirrors `WireError::Io`)."""


class VeyronProtoError(VeyronError):
    """Protobuf encode/decode failure (mirrors `WireError::Proto`)."""


class VeyronFrameMagicMismatch(VeyronError):
    """Frame magic != 0x5652 (mirrors `WireError::FrameMagicMismatch`)."""


class VeyronFrameCrcMismatch(VeyronError):
    """Frame CRC32 mismatch (mirrors `WireError::FrameCrcMismatch`)."""


class VeyronFrameReadTimeout(VeyronError):
    """Timed out reading a frame body once it started (mirrors
    `WireError::FrameReadTimeout`)."""


class VeyronPayloadTooLarge(VeyronError):
    """Payload exceeds the protocol limit (mirrors `WireError::PayloadTooLarge`)."""

    def __init__(self, size: int):
        self.size = size
        super().__init__(f"payload too large: {size} bytes")


class VeyronTimeout(VeyronError):
    """Operation timed out (mirrors `WireError::Timeout`)."""

    def __init__(self, message: str = "operation timed out"):
        super().__init__(message)


class VeyronPermissionDenied(VeyronError):
    """Permission denied; message carries the reason (mirrors
    `WireError::PermissionDenied`)."""

    def __init__(self, message: str):
        super().__init__(f"permission denied: {message}")


class VeyronInternal(VeyronError):
    """Internal/protocol error; message carries details (mirrors
    `WireError::Internal`)."""

    def __init__(self, message: str):
        super().__init__(f"internal error: {message}")


__all__ = [
    "VeyronError",
    "VeyronIoError",
    "VeyronProtoError",
    "VeyronFrameMagicMismatch",
    "VeyronFrameCrcMismatch",
    "VeyronFrameReadTimeout",
    "VeyronPayloadTooLarge",
    "VeyronTimeout",
    "VeyronPermissionDenied",
    "VeyronInternal",
]
