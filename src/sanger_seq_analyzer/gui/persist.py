"""Preferences stored with QSettings (registry on Windows, plist on macOS).

Values that are missing, of the wrong type or point to paths that no longer
exist fall back to defaults silently (PS-2, PS-3). Lists and dicts are stored
as JSON text so every backend round-trips them the same way.
"""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QByteArray, QSettings

ORGANIZATION = "SangerSeqAnalyzer"
APPLICATION = "SangerSeqAnalyzer"
SCHEMA = 1


class Store:
    """Typed access to the stored preferences; ``Store(path)`` uses an INI file (tests, screenshots)."""

    def __init__(self, path: str | Path | None = None):
        if path is None:
            self.qs = QSettings(ORGANIZATION, APPLICATION)
        else:
            self.qs = QSettings(str(path), QSettings.Format.IniFormat)
        if self._int("schema", 0) != SCHEMA:
            self.qs.clear()
            self.qs.setValue("schema", SCHEMA)

    def _int(self, key: str, default: int) -> int:
        try:
            return int(self.qs.value(key, default))
        except (TypeError, ValueError):
            return default

    def get_str(self, key: str, default: str = "") -> str:
        value = self.qs.value(key, default)
        return value if isinstance(value, str) else default

    def get_bool(self, key: str, default: bool = False) -> bool:
        value = self.qs.value(key, default)
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.lower() in ("true", "false"):
            return value.lower() == "true"
        return default

    def get_int(self, key: str, default: int = 0) -> int:
        return self._int(key, default)

    def get_json(self, key: str, default=None):
        text = self.get_str(key)
        if not text:
            return default
        try:
            return json.loads(text)
        except ValueError:
            return default

    def get_list(self, key: str) -> list[str]:
        value = self.get_json(key, [])
        return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []

    def get_paths(self, key: str) -> list[Path]:
        """Stored paths that still exist."""
        return [Path(p) for p in self.get_list(key) if Path(p).exists()]

    def get_bytes(self, key: str) -> QByteArray | None:
        value = self.qs.value(key)
        return value if isinstance(value, QByteArray) else None

    def set(self, key: str, value) -> None:
        if isinstance(value, (list, dict, tuple)):
            value = json.dumps(list(value) if isinstance(value, tuple) else value)
        elif isinstance(value, Path):
            value = str(value)
        self.qs.setValue(key, value)

    def remove(self, key: str) -> None:
        self.qs.remove(key)

    def clear(self) -> None:
        """Restore factory settings."""
        self.qs.clear()
        self.qs.setValue("schema", SCHEMA)

    def sync(self) -> None:
        self.qs.sync()
