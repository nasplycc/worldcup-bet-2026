from __future__ import annotations

import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent


def project_path(path: str | Path) -> Path:
    p = Path(path)
    if p.is_absolute():
        return p
    return ROOT / p


def ensure_parent(path: str | Path) -> Path:
    p = project_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def load_json(path: str | Path, default: Any) -> Any:
    p = project_path(path)
    if not p.exists():
        return default
    with p.open("r", encoding="utf-8-sig") as f:
        return json.load(f)


def save_json(path: str | Path, data: Any) -> Path:
    p = ensure_parent(path)
    with p.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return p


def append_text(path: str | Path, text: str) -> Path:
    p = ensure_parent(path)
    with p.open("a", encoding="utf-8") as f:
        f.write(text)
    return p
