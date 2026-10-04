"""Preset normalization, validation, and filesystem storage."""

from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path


_SLUG = re.compile(r"[a-z0-9-]+\Z")


def _valid_id(value):
    return isinstance(value, str) and bool(_SLUG.fullmatch(value))


def _integer(value):
    return type(value) is int


def _tile_types(registry):
    if not isinstance(registry, dict) or not isinstance(registry.get("tiles"), list):
        return {}
    return {
        tile["type"]: tile
        for tile in registry["tiles"]
        if isinstance(tile, dict) and isinstance(tile.get("type"), str)
    }


def normalize_preset(preset, registry):
    """Return a copy with schema and registered option defaults filled in."""
    result = dict(preset)
    result.setdefault("version", 1)
    result.setdefault("columns", 12)
    result.setdefault("row_height", 80)
    types = _tile_types(registry)
    tiles = []
    for tile in preset.get("tiles", []):
        copied = dict(tile)
        options = dict(tile.get("options") or {})
        definition = types.get(tile.get("type"), {})
        for key, schema in definition.get("options", {}).items():
            if isinstance(schema, dict) and "default" in schema:
                options.setdefault(key, schema["default"])
        copied["options"] = options
        tiles.append(copied)
    result["tiles"] = tiles
    return result


def validate_preset(preset, registry) -> list[str]:
    """Return human-readable violations of the preset schema."""
    if not isinstance(preset, dict):
        return ["Preset must be an object"]
    errors = []
    if not _valid_id(preset.get("id")):
        errors.append("id must be a slug containing only lowercase letters, digits, and hyphens")
    name = preset.get("name")
    if not isinstance(name, str) or not 1 <= len(name) <= 80:
        errors.append("name must be 1-80 characters")
    if preset.get("dashboard") != registry.get("dashboard"):
        errors.append("dashboard must match the registry")
    columns = preset.get("columns")
    if not _integer(columns) or not 1 <= columns <= 24:
        errors.append("columns must be an integer from 1 to 24")
    row_height = preset.get("row_height")
    if not _integer(row_height) or not 20 <= row_height <= 400:
        errors.append("row_height must be an integer from 20 to 400")
    tiles = preset.get("tiles")
    if not isinstance(tiles, list):
        return errors + ["tiles must be a list"]
    if len(tiles) > 100:
        errors.append("tiles may contain at most 100 items")
    definitions = _tile_types(registry)
    seen = set()
    occupied = []
    for index, tile in enumerate(tiles):
        label = f"tiles[{index}]"
        if not isinstance(tile, dict):
            errors.append(f"{label} must be an object")
            continue
        tile_id = tile.get("id")
        if not _valid_id(tile_id):
            errors.append(f"{label}.id must be a slug")
        elif tile_id in seen:
            errors.append(f"{label}.id must be unique")
        else:
            seen.add(tile_id)
        tile_type = tile.get("type")
        definition = definitions.get(tile_type) if isinstance(tile_type, str) else None
        if definition is None:
            errors.append(f"{label}.type must be in the registry")
        x, y, w, h = (tile.get(key) for key in ("x", "y", "w", "h"))
        for key, value in (("x", x), ("y", y)):
            if not _integer(value) or value < 0:
                errors.append(f"{label}.{key} must be a nonnegative integer")
        if definition is not None:
            for key, value in (("w", w), ("h", h)):
                minimum = definition.get("min_" + key)
                maximum = definition.get("max_" + key)
                if (not _integer(value) or not _integer(minimum)
                        or not _integer(maximum) or not minimum <= value <= maximum):
                    errors.append(f"{label}.{key} must be within the tile's min/max")
        elif not _integer(w) or w < 1 or not _integer(h) or h < 1:
            errors.append(f"{label}.w and .h must be positive integers")
        if _integer(x) and _integer(w) and _integer(columns) and x + w > columns:
            errors.append(f"{label} extends beyond columns")
        if all(_integer(value) for value in (x, y, w, h)) and x >= 0 and y >= 0 and w > 0 and h > 0:
            for other_index, ox, oy, ow, oh in occupied:
                if x < ox + ow and ox < x + w and y < oy + oh and oy < y + h:
                    errors.append(f"{label} overlaps tiles[{other_index}]")
            occupied.append((index, x, y, w, h))
        options = tile.get("options", {})
        if not isinstance(options, dict):
            errors.append(f"{label}.options must be an object")
        elif definition is not None:
            schemas = definition.get("options", {})
            for key, value in options.items():
                if key not in schemas:
                    errors.append(f"{label}.options.{key} is unknown")
                    continue
                schema = schemas[key]
                kind = schema.get("type")
                valid = {
                    "string": lambda: isinstance(value, str),
                    "number": lambda: type(value) in (int, float),
                    "boolean": lambda: type(value) is bool,
                    "choice": lambda: value in schema.get("choices", []),
                }.get(kind, lambda: False)()
                if not valid:
                    errors.append(f"{label}.options.{key} must be a valid {kind} value")
    return errors


class PresetStore:
    """Store presets as individual JSON files in a directory."""

    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _check_id(id):
        if not _valid_id(id):
            raise ValueError("preset id must be a slug")

    def _path(self, id):
        self._check_id(id)
        return self.directory / (id + ".json")

    def _write(self, path, value):
        fd, temporary = tempfile.mkstemp(prefix=".forge-", suffix=".tmp", dir=self.directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(value, stream, ensure_ascii=False)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def list(self):
        items = []
        for path in self.directory.glob("*.json"):
            if path.name == "_active.json" or not _valid_id(path.stem):
                continue
            try:
                preset = self.get(path.stem)
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            items.append({key: preset.get(key) for key in ("id", "name", "updated_at")})
        return sorted(items, key=lambda item: (str(item["name"]).casefold(), str(item["id"])))

    def get(self, id):
        path = self._path(id)
        try:
            with path.open(encoding="utf-8") as stream:
                preset = json.load(stream)
        except FileNotFoundError as exc:
            raise KeyError(id) from exc
        except (json.JSONDecodeError, UnicodeDecodeError, RecursionError) as exc:
            raise ValueError("preset file %s.json is unreadable" % id) from exc
        if not isinstance(preset, dict):
            raise ValueError("preset file %s.json is not an object" % id)
        return preset

    def save(self, preset):
        if not isinstance(preset, dict):
            raise ValueError("preset must be an object")
        path = self._path(preset.get("id"))
        result = dict(preset)
        result["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        self._write(path, result)
        return result

    def delete(self, id):
        path = self._path(id)
        try:
            path.unlink()
        except FileNotFoundError as exc:
            raise KeyError(id) from exc
        if self.get_active() == id:
            (self.directory / "_active.json").unlink(missing_ok=True)

    def get_active(self):
        try:
            with (self.directory / "_active.json").open(encoding="utf-8") as stream:
                id = json.load(stream)["id"]
        except (OSError, ValueError, KeyError, TypeError, RecursionError):
            return None  # missing or malformed active file: no active preset
        return id if _valid_id(id) and self._path(id).is_file() else None

    def set_active(self, id):
        path = self._path(id)
        if not path.is_file():
            raise KeyError(id)
        self._write(self.directory / "_active.json", {"id": id})
