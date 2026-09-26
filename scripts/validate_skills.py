"""Validate skills/<name>/SKILL.md against the Agent Skills specification.

Mirrors the checks of `skills-ref validate` so the repo does not need that tool installed:
  name: 1-64 chars, [a-z0-9-], no leading/trailing/double hyphen, equals directory name
  description: 1-1024 chars
  compatibility: 1-500 chars when present
  metadata: mapping of str -> str when present
  body: SKILL.md under 500 lines
Also requires scripts/run.py and skill-card.md (NVIDIA registry).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def parse_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---\n"):
        raise ValueError("missing frontmatter")
    end = text.find("\n---", 4)
    if end < 0:
        raise ValueError("unterminated frontmatter")
    raw = text[4:end]
    body = text[end + 4:]
    data: dict = {}
    current_map: dict | None = None
    for line in raw.splitlines():
        if not line.strip():
            continue
        if line.startswith("  ") and current_map is not None:
            k, _, v = line.strip().partition(":")
            current_map[k.strip()] = _unquote(v.strip())
            continue
        current_map = None
        k, _, v = line.partition(":")
        k, v = k.strip(), v.strip()
        if v == "":
            current_map = {}
            data[k] = current_map
        else:
            data[k] = _unquote(v)
    return data, body


def _unquote(v: str) -> str:
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    return v


def validate_skill(d: Path) -> list[str]:
    errs: list[str] = []
    sk = d / "SKILL.md"
    if not sk.exists():
        return ["SKILL.md missing"]
    text = sk.read_text(encoding="utf-8")
    try:
        fm, body = parse_frontmatter(text)
    except ValueError as e:
        return [str(e)]
    name = fm.get("name")
    if not isinstance(name, str) or not (1 <= len(name) <= 64) or not NAME_RE.match(name):
        errs.append(f"bad name {name!r}")
    elif name != d.name:
        errs.append(f"name {name!r} != directory {d.name!r}")
    desc = fm.get("description")
    if not isinstance(desc, str) or not (1 <= len(desc) <= 1024):
        errs.append("description missing or not 1-1024 chars")
    comp = fm.get("compatibility")
    if comp is not None and not (isinstance(comp, str) and 1 <= len(comp) <= 500):
        errs.append("compatibility must be 1-500 chars")
    meta = fm.get("metadata")
    if meta is not None and not (isinstance(meta, dict) and all(isinstance(k, str) and isinstance(v, str) for k, v in meta.items())):
        errs.append("metadata must map str -> str")
    if text.count("\n") > 500:
        errs.append("SKILL.md longer than 500 lines")
    if not (d / "scripts" / "run.py").exists():
        errs.append("scripts/run.py missing")
    if not (d / "skill-card.md").exists():
        errs.append("skill-card.md missing (NVIDIA registry)")
    return errs


def main(root: Path) -> int:
    dirs = sorted(p for p in root.iterdir() if p.is_dir() and (p / "SKILL.md").exists())
    bad = 0
    for d in dirs:
        errs = validate_skill(d)
        status = "ok " if not errs else "ERR"
        print(f"{status} {d.name}" + ("" if not errs else ": " + "; ".join(errs)))
        bad += bool(errs)
    print(f"{len(dirs) - bad}/{len(dirs)} skills valid")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1] / "skills"))
