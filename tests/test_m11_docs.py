import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_readme_follows_guide_structure_and_length():
    md = (ROOT / "README.md").read_text(encoding="utf-8")
    for h in ["## Problem", "## Demo", "## Why DGX Spark", "## Architecture", "## Agent System", "## Skills / Tools",
              "## Agent Loop", "## Models", "## Evaluation", "## Benchmarks", "## Failure Recovery", "## Quick Start",
              "## Demo Video", "## Screenshots", "## Limitations"]:
        assert h in md, h
    cjk = len(re.findall(r"[一-鿿]", md))
    assert cjk >= 500, f"README has only {cjk} Chinese characters; the competition requires >= 500"
    # required technology-stack mentions
    for kw in ["NeMo Agent Toolkit", "vLLM", "DGX Spark", "step-3.7-flash", "StepFun", "Jev", "τ²-bench"]:
        assert kw in md, kw
    assert "sparkjury run --demo" in md and "deploy/README.md" in md


def test_submission_docs_exist_and_reference_each_other():
    for p in ["docs/VIDEO_SCRIPT.md", "docs/ESSAY_十日谈.md", "docs/SUBMISSION_CHECKLIST.md"]:
        assert (ROOT / p).exists(), p
    video = (ROOT / "docs" / "VIDEO_SCRIPT.md").read_text(encoding="utf-8")
    assert "Problem" in video and "Magic" in video and "Explain" in video and "regress" in video
    essay = (ROOT / "docs" / "ESSAY_十日谈.md").read_text(encoding="utf-8")
    assert "9 月 20 日" in essay and "9 月 29 日" in essay
    check = (ROOT / "docs" / "SUBMISSION_CHECKLIST.md").read_text(encoding="utf-8")
    assert "B 站" in check and "SKILL.md" in check and "25%" in check
