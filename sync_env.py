from __future__ import annotations

from pathlib import Path


EXAMPLE = Path(".env.example")
TARGET = Path(".env")


def parse_key(line: str) -> str | None:
    stripped = line.lstrip("\ufeff").strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return None
    key = stripped.split("=", 1)[0].strip()
    return key or None


def main() -> None:
    if not EXAMPLE.exists():
        raise SystemExit(".env.example not found")

    example_lines = EXAMPLE.read_text(encoding="utf-8").splitlines()
    if TARGET.exists():
        target_text = TARGET.read_text(encoding="utf-8")
        target_lines = target_text.splitlines()
    else:
        target_text = ""
        target_lines = []

    existing = {key for line in target_lines if (key := parse_key(line))}
    additions: list[str] = []
    pending_comments: list[str] = []

    for line in example_lines:
        key = parse_key(line)
        if key is None:
            pending_comments.append(line)
            continue
        if key in existing:
            pending_comments = []
            continue
        if additions and additions[-1] != "":
            additions.append("")
        additions.extend(pending_comments)
        additions.append(line)
        existing.add(key)
        pending_comments = []

    if not additions:
        print(".env is already up to date")
        return

    prefix = "\n" if target_text and not target_text.endswith("\n") else ""
    block = "\n".join(additions).rstrip() + "\n"
    with TARGET.open("a", encoding="utf-8", newline="\n") as f:
        f.write(prefix)
        f.write("\n# Added from .env.example by sync_env.py\n")
        f.write(block)
    print(f"Added {sum(1 for line in additions if parse_key(line))} missing keys to .env")


if __name__ == "__main__":
    main()
