import json
import time
from pathlib import Path
from typing import Any


def ensure_parent_dir(path: str | Path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)


def load_json_state(path: str | Path, default: Any = None) -> Any:
    target = Path(path)
    if not target.exists():
        return default
    try:
        with target.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError):
        return default


def save_json_state(path: str | Path, payload: dict[str, Any]) -> None:
    ensure_parent_dir(path)
    data = dict(payload)
    data.setdefault("saved_at", time.time())
    target = Path(path)
    temp_path = target.with_suffix(f"{target.suffix}.tmp")
    with temp_path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2, sort_keys=True)
    temp_path.replace(target)
