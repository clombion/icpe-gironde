#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""
build_angles_index.py — Scanne rapports/angles/*.md et produit index.json.

Chaque fichier .md doit avoir un front matter YAML délimité par ``---``
avec au minimum ``title``, ``question``, ``caveat``. Le script extrait
ces 3 champs et produit ``rapports/angles/index.json`` que le client
charge pour lister les angles disponibles.

Usage :
  python3 scripts/build_angles_index.py

Stdlib uniquement.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import PROJECT_ROOT  # noqa: E402

ANGLES_DIR = PROJECT_ROOT / "rapports" / "angles"
INDEX_PATH = ANGLES_DIR / "index.json"


def parse_yaml_front_matter(text: str) -> dict[str, str]:
    """Minimal YAML front matter parser (key: "value" or key: value)."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    result: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        match = re.match(r'^(\w+)\s*:\s*"?(.+?)"?\s*$', line)
        if match:
            result[match.group(1)] = match.group(2)
    return result


def build_index() -> list[dict[str, str]]:
    """Scanne les .md et construit l'index."""
    entries: list[dict[str, str]] = []
    for md_path in sorted(ANGLES_DIR.glob("*.md")):
        text = md_path.read_text(encoding="utf-8")
        fm = parse_yaml_front_matter(text)
        if not fm.get("title"):
            print(
                f"[warn] {md_path.name} : pas de title dans le front matter, skippé",
                file=sys.stderr,
            )
            continue
        entries.append({
            "file": md_path.name,
            "title": fm["title"],
            "question": fm.get("question", ""),
            "caveat": fm.get("caveat", ""),
        })
    return entries


def main() -> int:
    if not ANGLES_DIR.exists():
        print(f"[error] dossier {ANGLES_DIR} absent", file=sys.stderr)
        return 1
    entries = build_index()
    content = json.dumps(entries, ensure_ascii=False, indent=2)
    tmp_path = INDEX_PATH.with_suffix(".json.tmp")
    tmp_path.write_text(content + "\n", encoding="utf-8")
    tmp_path.replace(INDEX_PATH)
    print(f"[build] {len(entries)} angles → {INDEX_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
