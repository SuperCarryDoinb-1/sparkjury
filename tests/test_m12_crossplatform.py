"""仓库约定守卫：跨平台、跨工具，以及那些会悄悄过期的东西。"""

import os
import py_compile
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEXT_EXT = {".py", ".sh", ".md", ".toml", ".yml", ".yaml", ".json", ".html", ".txt"}
SKIP = {".venv", "runs", "__pycache__", ".pytest_cache", "logs", "data", ".git", ".worktrees"}


def _text_files():
    for p in ROOT.rglob("*"):
        # .venv* covers the node's extra environments (.venv-tau2, .venv-vllm): third-party files inside them
        # are not ours to lint, and litellm ships a couple of CRLF files that used to fail this test on the node.
        if p.is_file() and p.suffix in TEXT_EXT and not any(part in SKIP or part.startswith(".venv") for part in p.parts):
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


def _shell_files():
    """shell 脚本与 git 钩子。钩子没有 .sh 后缀，_text_files 扫不到，单独走一遍。"""
    for p in ROOT.rglob("*"):
        if not p.is_file() or any(part in SKIP or part.startswith(".venv") for part in p.parts):
            continue
        if p.suffix == ".sh" or p.parent.name == ".githooks":
            yield p


def test_shell_vars_are_braced_before_non_ascii():
    """变量后面紧跟中文标点时必须写成 ${var}。

    macOS 自带的 bash 3.2 在没有 UTF-8 locale 时按字节解析，会把「，」「）」这类标点的首字节
    吃进变量名：`log "分支 $branch，路径 $path"` 报的是 `branch\\xef: unbound variable`，
    看起来完全不像语法问题。踩过一次，这里立个守卫。
    """
    pattern = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)(?=[^\x00-\x7F])")
    offenders = []
    for p in _shell_files():
        text = p.read_text(encoding="utf-8", errors="replace")
        for i, line in enumerate(text.splitlines(), 1):
            m = pattern.search(line)
            if m:
                offenders.append(f"{p.relative_to(ROOT)}:{i} ${m.group(1)}")
    assert offenders == [], f"变量后紧跟非 ASCII 字符，bash 3.2 下会变成 unbound variable，请写成 ${{var}}：{offenders}"


def test_git_hooks_are_present_and_executable():
    """提交门禁要能真的跑起来：钩子缺失或没有执行位，等于门禁不存在。"""
    for name in ("pre-commit", "commit-msg"):
        hook = ROOT / ".githooks" / name
        assert hook.is_file(), f"缺少 .githooks/{name}"
        assert os.access(hook, os.X_OK), f".githooks/{name} 没有执行位（chmod +x）"
    assert (ROOT / "scripts" / "check_staged.py").is_file(), "pre-commit 依赖 scripts/check_staged.py"


MIRROR_NOTICE = "> 这一份和"


def _without_notice(text):
    """去掉镜像首行那句说明，连带它后面那行空行。"""
    out, skip_blank = [], False
    for line in text.splitlines():
        if line.startswith(MIRROR_NOTICE):
            skip_blank = True
            continue
        if skip_blank and not line.strip():
            skip_blank = False
            continue
        skip_blank = False
        out.append(line)
    return out


def test_skill_mirrors_stay_in_sync():
    """同一条技能放两份：`.agents/skills` 是正文，`.claude/skills` 是给 Claude Code 的镜像。

    两边唯一的允许差异是镜像那句「两处一起改」的说明。改的时候只改一边、另一边的 Agent
    读到旧规矩，是最容易发生也最难发现的事，所以让测试盯着。
    """
    body_root = ROOT / ".agents" / "skills"
    mirror_root = ROOT / ".claude" / "skills"
    assert body_root.is_dir() and mirror_root.is_dir(), "技能目录缺失：.agents/skills 或 .claude/skills"

    names = sorted(p.name for p in body_root.iterdir() if p.is_dir())
    assert names, ".agents/skills 下没有技能"
    assert names == sorted(p.name for p in mirror_root.iterdir() if p.is_dir()), "两处技能目录名不一致"

    for name in names:
        for f in sorted((body_root / name).rglob("*")):
            if not f.is_file():
                continue
            mirror = mirror_root / name / f.relative_to(body_root / name)
            assert mirror.is_file(), f".claude 缺镜像文件：{mirror.relative_to(ROOT)}"
            body = _without_notice(f.read_text(encoding="utf-8"))
            copy = _without_notice(mirror.read_text(encoding="utf-8"))
            assert body == copy, f"镜像与正文不一致，两边要一起改：{f.relative_to(ROOT)}"


# 上手提示词里点名的入口，改名或搬走之后这里会先红，而不是等新人撞墙
ONBOARDING_PATHS = [
    "AGENTS.md",
    "scripts/worktree.sh",
    "scripts/node.py",
    "deploy/dgx/node.env.example",
    ".githooks",
    ".github/pull_request_template.md",
    ".agents/skills/sj-worktree/SKILL.md",
    ".agents/skills/sj-push-and-deploy/SKILL.md",
    "docs/ARCHITECTURE.md",
    "docs/MODULES.md",
    "docs/ABLATION.md",
    "deploy/README.md",
]


def test_onboarding_paths_are_not_stale():
    """上手提示词和它指向的契约文件里点名的入口必须真实存在。

    提示词只有一句话，新人第一次打开它时也正是最不能出错的时候——第一步就撞墙是最坏的首印象。
    """
    onboarding = (ROOT / "docs" / "ONBOARDING.md").read_text(encoding="utf-8")
    contract = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert "AGENTS.md" in onboarding, "上手提示词没有指向 AGENTS.md"
    assert "github.com/VioletScar-Hui/sparkjury" in onboarding, "上手提示词没给仓库地址"
    # 提示词把细节都交给 AGENTS.md，所以那两个入口得由契约文件点名
    for required in ("scripts/worktree.sh", "scripts/node.py"):
        assert required in contract, f"AGENTS.md 漏了 {required}"
    named = [p for p in ONBOARDING_PATHS if p in onboarding or p in contract]
    missing = sorted({p for p in named if not (ROOT / p).exists()})
    assert missing == [], f"点到了不存在的路径：{missing}"
