"""File browser service handling uploads with normalized results and typed errors."""

from __future__ import annotations

from typing import Any, Dict


class RejectedUploadError(Exception):
    """Raised when an upload is rejected by the server."""
    pass


class InterruptedUploadError(Exception):
    """Raised when an upload is interrupted before completion."""
    pass


def _normalize_result(data: Dict[str, Any]) -> Dict[str, Any]:
    """Return a copy of *data* without credential or JWT information."""
    return {k: v for k, v in data.items() if k not in ("jwt", "credentials", "status")}


def upload_file(file_info: Dict[str, Any]) -> Dict[str, Any]:
    """
    Process an uploaded file description and return a normalized result.

    The *file_info* dict may contain keys such as ``filename``, ``size``,
    ``jwt`` and ``credentials``.  If the upload was rejected or interrupted,
    the function raises a typed error instead of returning a result.

    Returns:
        A dict containing the upload metadata without any credential or JWT
        fields.

    Raises:
        RejectedUploadError: If the upload was rejected.
        InterruptedUploadError: If the upload was interrupted.
    """
    status = file_info.get("status")
    if status == "rejected":
        raise RejectedUploadError("Upload was rejected")
    if status == "interrupted":
        raise InterruptedUploadError("Upload was interrupted")

    return _normalize_result(file_info)


__all__ = [
    "RejectedUploadError",
    "InterruptedUploadError",
    "upload_file",
]

