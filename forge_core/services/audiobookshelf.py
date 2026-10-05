"""Normalization of Audiobookshelf library items (audiobooks and podcasts)."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

__all__ = ["normalize_item"]


def _as_mapping(value: Any) -> Mapping:
    """Return ``value`` when it is a mapping, otherwise an empty mapping."""
    return value if isinstance(value, Mapping) else {}


def _clean_str(value: Any) -> str:
    """Return a stripped string, and ``""`` for missing/None/non-string values."""
    if isinstance(value, str):
        return value.strip()
    return ""


def _duration_seconds(value: Any) -> int | None:
    """Return a non-negative whole-second duration, or ``None`` if invalid."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value) or value < 0:
        return None
    return int(value)


def _episode_count(value: Any) -> int | None:
    """Return the episode count when ``value`` is a list, otherwise ``None``."""
    if isinstance(value, list):
        return len(value)
    return None


def normalize_item(raw: Mapping) -> dict | None:
    """Normalize an Audiobookshelf library item into a flat dictionary.

    The returned mapping always contains exactly the keys ``id``, ``kind``,
    ``title``, ``author``, ``duration_seconds`` and ``episode_count``.

    Returns ``None`` when ``raw`` is not a mapping, when ``mediaType`` is not
    ``"book"`` or ``"podcast"``, or when ``id`` is missing. Malformed nested
    input (missing/non-mapping ``media`` or ``metadata``, non-list ``episodes``,
    non-numeric durations) is tolerated and never raises.
    """
    if not isinstance(raw, Mapping):
        return None

    media_type = raw.get("mediaType")
    if media_type not in ("book", "podcast"):
        return None

    item_id = raw.get("id")
    if item_id is None:
        return None

    media = _as_mapping(raw.get("media"))
    metadata = _as_mapping(media.get("metadata"))

    title = _clean_str(metadata.get("title"))

    if media_type == "book":
        return {
            "id": item_id,
            "kind": "audiobook",
            "title": title,
            "author": _clean_str(metadata.get("authorName")),
            "duration_seconds": _duration_seconds(media.get("duration")),
            "episode_count": None,
        }

    return {
        "id": item_id,
        "kind": "podcast",
        "title": title,
        "author": _clean_str(metadata.get("author")),
        "duration_seconds": None,
        "episode_count": _episode_count(media.get("episodes")),
    }
