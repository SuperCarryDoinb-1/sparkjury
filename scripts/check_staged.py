"""提交前门禁：检查暂存区里不该进仓库的文件、明文密钥和语法错误。

由 .githooks/pre-commit 调用，也可以手动跑：

    python3 scripts/check_staged.py

只读，不改任何东西。故意做得快（几秒内）：全量测试按 AGENTS.md 的「最小充分」原则另行运行，
不要把这个脚本变成每次提交都跑全仓的借口。
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 这些路径永远不该进版本库，写了 .gitignore 也还是有人在 -f 之后把 .env 提交上来。
FORBIDDEN = [
    (re.compile(r"(^|/)\.env$"), "本机环境文件"),
    (re.compile(r"(^|/)node\.env$"), "节点凭据"),
    (re.compile(r"\.db$"), "评测产生的数据库"),
    (re.compile(r"^runs/"), "评测产物"),
    (re.compile(r"^logs/"), "运行日志"),
    (re.compile(r"^\.venv"), "虚拟环境"),
    (re.compile(r"^\.worktrees/"), "worktree 副本"),
    (re.compile(r"\.tar\.gz$"), "打包产物"),
]

SECRET_PATTERNS = [
    (re.compile(r"sk-[A-Za-z0-9]{20,}"), "OpenAI 风格的 key"),
    (re.compile(r"gho_[A-Za-z0-9]{20,}"), "GitHub token"),
    (re.compile(r"ghp_[A-Za-z0-9]{20,}"), "GitHub token"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "AWS access key id"),
    (re.compile(r"(?i)(api[_-]?key|secret|password|passwd|token)\s*[=:]\s*[\"']?[A-Za-z0-9_+\-]{12,}"), "疑似明文密钥"),
]

SKIP_FILES = {"scripts/check_staged.py", ".githooks/pre-commit", ".githooks/commit-msg"}
TEXT_SUFFIX = {".py", ".sh", ".md", ".toml", ".yml", ".yaml", ".json", ".html", ".txt", ".cfg", ".ini", ".env", ""}


def _git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True).stdout


def staged_files() -> list[str]:
    out = _git("diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z")
    return [p for p in out.split("\0") if p]


def blob(path: str) -> str:
    """暂存区里的内容，而不是工作区那份：门禁要拦的是即将提交的字节。"""
    r = subprocess.run(["git", "-C", str(ROOT), "show", f":{path}"], capture_output=True)
    return r.stdout.decode("utf-8", "replace")


def main() -> int:
    files = staged_files()
    if not files:
        return 0
    problems: list[str] = []

    for path in files:
        for pattern, why in FORBIDDEN:
            if pattern.search(path):
                problems.append(f"{path}：{why}，不该进仓库（.gitignore 里有写，检查是不是 -f 强加的）")
                break

    for path in files:
        if path in SKIP_FILES or Path(path).suffix not in TEXT_SUFFIX:
            continue
        text = blob(path)
        for pattern, why in SECRET_PATTERNS:
            m = pattern.search(text)
            if m:
                line = text[: m.start()].count("\n") + 1
                problems.append(f"{path}:{line}：{why}（{m.group(0)[:24]}…），密钥走 deploy/dgx/.env，不要进仓库")
                break

    for path in files:
        if not path.endswith(".py"):
            continue
        try:
            compile(blob(path), path, "exec")
        except SyntaxError as e:
            problems.append(f"{path}:{e.lineno}：Python 语法错误：{e.msg}")

    for path in files:
        if not path.endswith(".sh"):
            continue
        f = ROOT / path
        if not f.is_file():
            continue
        r = subprocess.run(["bash", "-n", str(f)], capture_output=True, text=True)
        if r.returncode != 0:
            problems.append(f"{path}：bash 语法检查没过：{r.stderr.strip().splitlines()[0] if r.stderr.strip() else '见 bash -n'}")

    if problems:
        print("提交被 pre-commit 拦住：\n", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        print("\n修完重新 git add；确实要绕过用 git commit --no-verify，并在提交信息里说明理由。", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
