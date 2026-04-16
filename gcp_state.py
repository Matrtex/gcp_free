import json
import time
from pathlib import Path


def ensure_parent_dir(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)


def load_json_state(path, default=None):
    target = Path(path)
    if not target.exists():
        return default
    with target.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def save_json_state(path, payload):
    ensure_parent_dir(path)
    data = dict(payload)
    data.setdefault("saved_at", time.time())
    with Path(path).open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2, sort_keys=True)
