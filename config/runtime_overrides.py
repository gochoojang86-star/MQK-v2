"""
Runtime override helpers for approved self-improvement changes.

Approved proposals are persisted to a JSON file and loaded by config.settings
on startup. Only explicitly whitelisted sections/keys are applied.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).parent.parent
DEFAULT_OVERRIDE_PATH = BASE_DIR / "data" / "approved_settings.json"

ALLOWED_SECTIONS = {"RISK", "SCANNER", "LLM_CONFIG", "EXECUTION"}


def load_runtime_overrides(path: Path | None = None) -> dict[str, dict[str, Any]]:
    override_path = Path(path or DEFAULT_OVERRIDE_PATH)
    try:
        data = json.loads(override_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}

    if not isinstance(data, dict):
        return {}

    overrides: dict[str, dict[str, Any]] = {}
    for section, values in data.items():
        if section not in ALLOWED_SECTIONS or not isinstance(values, dict):
            continue
        overrides[section] = values
    return overrides


def write_runtime_overrides(
    overrides: dict[str, dict[str, Any]],
    path: Path | None = None,
) -> Path:
    override_path = Path(path or DEFAULT_OVERRIDE_PATH)
    override_path.parent.mkdir(parents=True, exist_ok=True)
    safe_overrides = {
        section: values
        for section, values in overrides.items()
        if section in ALLOWED_SECTIONS and isinstance(values, dict)
    }
    override_path.write_text(
        json.dumps(safe_overrides, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return override_path
