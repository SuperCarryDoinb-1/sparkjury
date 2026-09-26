"""Guard rails so the repo stays usable on Windows and macOS alike."""

import py_compile
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEXT_EXT = {".py", ".sh", ".md", ".toml", ".yml", ".yaml", ".json", ".html", ".txt"}
SKIP = {".venv", "runs", "__pycache__", ".pytest_cache", "logs", "data"}


def _text_files():
    for p in ROOT.rglob("*"):
        if p.is_file() and p.suffix in TEXT_EXT and not any(part in SKIP for part in p.parts):
            yield p


def test_no_crlf_in_tracked_text_files():
    bad = [str(p.relative_to(ROOT)) for p in _text_files() if b"\r\n" in p.read_bytes()]
    assert bad == [], f"CRLF line endings would break bash on the node: {bad}"


def test_editorconfig_and_gitattributes_force_lf():
    assert "end_of_line = lf" in (ROOT / ".editorconfig").read_text(encoding="utf-8")
    assert "eol=lf" in (ROOT / ".gitattributes").read_text(encoding="utf-8")


def test_docs_have_no_windows_only_commands():
    docs = [ROOT / "README.md", ROOT / "docs" / "MODULES.md", ROOT / "docs" / "ARCHITECTURE.md", ROOT / "deploy" / "README.md",
            ROOT / "skills" / "README.md", ROOT / "nat" / "README.md", ROOT / "docs" / "CROSS_PLATFORM.md"]
    offenders = []
    for d in docs:
        for i, line in enumerate(d.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("cd D:\\") or stripped.startswith("cd C:\\"):
                offenders.append(f"{d.name}:{i} absolute Windows path")
            if re.match(r"^set [A-Z_]+=", stripped):
                offenders.append(f"{d.name}:{i} cmd.exe 'set' without a POSIX alternative")
            if re.match(r"^(start|type) ", stripped) and "macOS" not in line and "Windows" not in line:
                offenders.append(f"{d.name}:{i} Windows-only command without the macOS form")
            if stripped.startswith("```powershell"):
                offenders.append(f"{d.name}:{i} PowerShell-only code block")
    assert offenders == [], offenders


def test_helper_scripts_compile_and_use_no_shell_specific_calls():
    for name in ("node.py", "screenshot.py", "validate_skills.py", "gen_skills.py", "make_samples.py"):
        p = ROOT / "scripts" / name
        py_compile.compile(str(p), doraise=True)
        src = p.read_text(encoding="utf-8")
        assert "shell=True" not in src and "os.system(" not in src, name


def test_ops_dependency_group_declared():
    py = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "ops = [" in py and "paramiko" in py and "playwright" in py
