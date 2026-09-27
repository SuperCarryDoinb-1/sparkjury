#!/usr/bin/env bash
# tau2 的 NL-assertion 裁判是唯一一个会调外部 LLM 的评测环节，模型名在 config.py 里写死成
# gpt-4.1。节点连不上 api.openai.com，于是：对话跑完之后 evaluate_simulation 抛异常 ->
# run_with_retry 把整条 simulation（含对话）重跑 4 次 -> 最终存下来的是 termination_reason=
# infrastructure_error、messages 为空的记录。9 月 26 日那批 90 条里丢掉的 26 条，全部且只
# 来自 30 个任务中带 nl_assertions 的那 9 个，另外 21 个一条没丢（见 docs/ABLATION.md）。
#
# 这个脚本把模型名与调用参数改成从环境变量读（默认值不变，所以不改行为）。真正的修复在
# run_tau2.sh：它把这两条变量指向本地端点。用法：
#   bash deploy/dgx/patch_tau2_nl_assertions.sh ensure|apply|check|show|revert
#
# 注意：下面所有变量展开都写成 ${VAR} 带花括号。bash 把非 ASCII 字节也算作标识符字符，
# 于是 "$BACKUP）" 会被当成变量名 BACKUP），在 set -u 下直接报 unbound variable。
cd "$(dirname "$0")/../.." && source deploy/dgx/common.sh
TAU2_HOME="${TAU2_HOME:-$HOME/tau2-bench}"
CONFIG="${TAU2_HOME}/src/tau2/config.py"
BACKUP="${CONFIG}.sparkjury.orig"
MARK="sparkjury-nl-assertions-override"

[[ -f "${CONFIG}" ]] || die "tau2 config.py not found at ${CONFIG} (set TAU2_HOME)"

OLD_LLM='DEFAULT_LLM_NL_ASSERTIONS = "gpt-4.1-2025-04-14"'
OLD_ARGS='DEFAULT_LLM_NL_ASSERTIONS_ARGS = {"temperature": DEFAULT_LLM_NL_ASSERTIONS_TEMPERATURE}'

# 就地 import 并带 _sj_ 前缀，避免和 config.py 自己的导入撞名；那份文件顶部是注释，
# 导入写在别处，不能假设 os / json 已经进来。TEMPERATURE 那行原样留着，这里不重复定义。
NEW_BLOCK="# ${MARK}: NL 裁判模型与调用参数改成从环境变量读（默认值不变）。
# 写死的 gpt-4.1 在这台节点上不可达，会让整条 simulation 连对话一起丢掉（见脚本头注释）。
import json as _sj_json
import os as _sj_os
DEFAULT_LLM_NL_ASSERTIONS = _sj_os.environ.get(\"TAU2_LLM_NL_ASSERTIONS\", \"gpt-4.1-2025-04-14\")
DEFAULT_LLM_NL_ASSERTIONS_ARGS = _sj_json.loads(
    _sj_os.environ.get(\"TAU2_LLM_NL_ASSERTIONS_ARGS\", '{\"temperature\": 0.0}')
)"

applied() { grep -q "${MARK}" "${CONFIG}"; }

# tau2 装在自己的 venv 里（默认在仓库根的 .venv-tau2），系统 python3 import 不到它；
# 共用节点上那份 venv 常常在队友的主树里，所以允许用 TAU2_BIN / TAU2_PY 指。
tau2_python() {
  local cand="${TAU2_PY:-}"
  if [[ -z "${cand}" ]]; then
    for c in "${TAU2_BIN%/tau2}/python" "${HOME}/sparkjury/.venv-tau2/bin/python" "${REPO_ROOT}/.venv-tau2/bin/python"; do
      [[ -x "${c}" ]] && { cand="${c}"; break; }
    done
  fi
  echo "${cand:-python3}"
}

apply_patch() {
  if applied; then log "NL-assertion 补丁已在 ${CONFIG}"; return 0; fi
  grep -qF "${OLD_LLM}" "${CONFIG}" || die "${CONFIG} 里找不到写死的 NL 模型名，tau2 版本对不上，别硬改"
  grep -qF "${OLD_ARGS}" "${CONFIG}" || die "${CONFIG} 里找不到 NL 参数行，tau2 版本对不上"
  cp -n "${CONFIG}" "${BACKUP}" || true
  python3 - "${CONFIG}" "${OLD_LLM}" "${OLD_ARGS}" "${NEW_BLOCK}" <<'PY'
import sys
path, old_llm, old_args, new_block = sys.argv[1:5]
src = open(path, encoding="utf-8").read()
assert old_llm in src and old_args in src, "anchors moved"
src = src.replace(old_llm, new_block)
src = src.replace(old_args + "\n", "")
open(path, "w", encoding="utf-8").write(src)
PY
  applied || die "补丁写完但标记不在文件里，检查 ${CONFIG}"
  log "已给 ${CONFIG} 打上 NL-assertion 补丁（备份：${BACKUP}）"
}

revert_patch() {
  [[ -f "${BACKUP}" ]] || die "没有备份可回滚（${BACKUP}）"
  cp "${BACKUP}" "${CONFIG}" && log "已回滚 ${CONFIG}"
}

# 真的 import 一次 tau2.config，确认环境变量能改变生效值——补丁没打上时这里会如实报出来，
# 而不是让 run_tau2.sh 以为自己设置成功了。
check_effective() {
  local want="${TAU2_LLM_NL_ASSERTIONS:-}" py got
  py="$(tau2_python)"
  # 带上 src 的 PYTHONPATH：tau2 装在 venv 里是 editable 的，但直接拿 python3 跑时不一定能 import。
  got="$(cd "${TAU2_HOME}" && PYTHONPATH="${TAU2_HOME}/src${PYTHONPATH:+:${PYTHONPATH}}" \
        "${py}" -c 'import tau2.config as c; print(c.DEFAULT_LLM_NL_ASSERTIONS)' 2>/dev/null)" \
    || die "import tau2.config 失败（用 ${py} 在 ${TAU2_HOME} 下跑；可用 TAU2_PY 指一个装了 tau2 的解释器）"
  if [[ -z "${want}" ]]; then
    log "tau2 生效的 NL 裁判模型：${got}（未设 TAU2_LLM_NL_ASSERTIONS，走原默认）"
    return 0
  fi
  [[ "${got}" == "${want}" ]] || die "环境变量没生效：想要 ${want}，tau2 里是 ${got}（补丁没打？跑 apply）"
  log "tau2 生效的 NL 裁判模型：${got}"
}

case "${1:-ensure}" in
  ensure) applied || apply_patch; check_effective ;;
  apply)  apply_patch ;;
  revert) revert_patch ;;
  check)  check_effective ;;
  show)
    if applied; then log "已打补丁"; else log "未打补丁"; fi
    grep -n "DEFAULT_LLM_NL_ASSERTIONS" "${CONFIG}" | head -5
    ;;
  *) die "用法: $0 ensure|apply|check|show|revert" ;;
esac
