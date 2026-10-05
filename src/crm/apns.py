from __future__ import annotations

import json
from pathlib import Path


def parse_apns_key_file(content: str, filename: str) -> dict[str, str]:
    """
    Parse APNS key file sent by user.

    Supports:
    - JSON with team_id, key_id, key_p8
    - .p8/.txt with filename TEAMID_KEYID.p8 (legacy fallback)
    """
    stripped = content.strip()
    if filename.lower().endswith(".json") or stripped.startswith("{"):
        return _parse_apns_json(stripped)

    stem = Path(filename).stem
    parts = stem.split("_", 1)
    if len(parts) != 2 or not parts[0].strip() or not parts[1].strip():
        raise ValueError(
            "APNS файл должен быть JSON с полями team_id, key_id, key_p8 "
            "или .p8 с именем TEAMID_KEYID.p8"
        )

    team_id, key_id = parts
    return {
        "team_id": team_id.strip(),
        "key_id": key_id.strip(),
        "key_p8": stripped,
    }


def _parse_apns_json(content: str) -> dict[str, str]:
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError("APNS JSON некорректен") from exc

    if not isinstance(data, dict):
        raise ValueError("APNS JSON должен быть объектом")

    missing = [key for key in ("team_id", "key_id", "key_p8") if not data.get(key)]
    if missing:
        raise ValueError(f"В APNS JSON не хватает полей: {', '.join(missing)}")

    return {
        "team_id": str(data["team_id"]).strip(),
        "key_id": str(data["key_id"]).strip(),
        "key_p8": str(data["key_p8"]).strip(),
    }
