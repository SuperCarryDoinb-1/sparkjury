#!/usr/bin/env bash
# verify.sh — 一条命令跑完仓库门禁。顺序是固化的，不要凭记忆调整：
#
#   1. 清 pycache —— Python 每跑一次就写 .pyc；外部安全扫描器（如 NVIDIA SkillSpector）
#      会把二进制 .pyc 判成"可执行文件"，直接把目录打成 CRITICAL。.gitignore 只防
#      git 提交、不防扫描，所以在门禁里硬清。
#   2. pytest —— 全库不变量。代理环境变量必须大小写四件套一起清：本机 SOCKS 代理
#      泄漏进 httpx 会让 9 个用例假失败（socksio not installed），与代码无关。
#   3. 再清一次 —— pytest 刚写的。
#   4. lint --repo-only —— 仓库级门禁：pyc 与禁入路径（.env/node.env/runs/*.db）。
#   5. validate_skills.py —— NVIDIA/skills 规范校验（格式门）。
#   6. pack_freeze.py --verify —— 评测标准内容寻址：当前内容算出的 hash 必须等于
#      standards/scenario-pack/pack.manifest.json 里的 frozen_hash。不一致 = 标准被
#      改过，一切"前后对比"的结论作废。
set -euo pipefail
cd "$(dirname "$0")/.."

clean_pyc() { find . -name "__pycache__" -type d -not -path "./.venv/*" -exec rm -rf {} + 2>/dev/null || true; }
NOPROXY=(env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY)

echo "===[1/6] pytest（全库不变量）==="
clean_pyc
"${NOPROXY[@]}" uv run pytest -q 2>&1 | tail -1

echo "===[2/6] 清 pytest 写出的 pycache==="
clean_pyc

echo "===[3/6] lint --repo-only（pyc / 禁入路径）==="
PYTHONDONTWRITEBYTECODE=1 python3 tools/lint_skills.py --repo-only

echo "===[4/6] validate_skills.py（NVIDIA 规范门）==="
PYTHONDONTWRITEBYTECODE=1 python3 scripts/validate_skills.py skills | tail -1

echo "===[5/6] pack_freeze --verify（标准内容寻址）==="
PYTHONDONTWRITEBYTECODE=1 python3 tools/pack_freeze.py --verify

echo "ALL GATES PASSED"
