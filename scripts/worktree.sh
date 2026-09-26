#!/usr/bin/env bash
# 本仓 worktree 生命周期的唯一入口。规则见 AGENTS.md「工作区隔离」一节。
#
#   bash scripts/worktree.sh new <名字> [--detach] [--no-sync]   从 origin/main 新建工作区
#   bash scripts/worktree.sh list                                列出本仓所有工作区
#   bash scripts/worktree.sh done <名字|路径> [--force|--keep]    收尾：释放（--keep 保留现场）
#   bash scripts/worktree.sh path <名字>                          打印工作区路径
#
# 名字可以带前缀，例如 fix/tau2-timeout；不带前缀时默认 task/<名字>。
# 工作区落在 .worktrees/<名字去斜杠>/，已进 .gitignore，不会污染主工作区。
set -euo pipefail

MAIN="$(cd "$(git rev-parse --git-common-dir)/.." && pwd)"
WT_DIR="$MAIN/.worktrees"

die() { echo "worktree: $*" >&2; exit 1; }
log() { echo "[worktree] $*"; }

# uv 在本机可能是坏的包装脚本（~/.local/bin/uv 指向已删除的解释器），逐个试到能跑为止。
find_uv() {
  local c
  for c in "${UV:-}" uv /opt/homebrew/bin/uv "$HOME/.local/bin/uv"; do
    [[ -n "$c" ]] || continue
    if command -v "$c" >/dev/null 2>&1 && "$c" --version >/dev/null 2>&1; then printf '%s' "$c"; return 0; fi
  done
  return 1
}

dir_of() { printf '%s' "$WT_DIR/$(printf '%s' "$1" | tr '/' '-')"; }

cmd_new() {
  local name="${1:-}"; shift || true
  local detach=false sync=true
  for a in "$@"; do
    case "$a" in
      --detach) detach=true ;;
      --no-sync) sync=false ;;
      *) die "未知参数 $a" ;;
    esac
  done
  [[ -n "$name" ]] || die "用法：worktree.sh new <名字> [--detach] [--no-sync]"
  [[ "$name" =~ ^[A-Za-z0-9._/-]+$ ]] || die "名字只允许字母数字和 . _ / -"
  local branch="$name" path
  [[ "$name" == */* ]] || branch="task/$name"
  path="$(dir_of "$name")"
  [[ -e "$path" ]] && die "$path 已存在，先 done 掉或者换个名字"

  log "取 origin/main"
  git -C "$MAIN" fetch origin main || die "fetch origin main 失败，网络不通就不继续（不要拿本地旧 main 凑合）"
  local base; base="$(git -C "$MAIN" rev-parse origin/main)"
  mkdir -p "$WT_DIR"
  if [[ "$detach" == true ]]; then
    git -C "$MAIN" worktree add --detach "$path" "$base" >/dev/null
    log "已建（detached，只读场景用）：$path"
  else
    git -C "$MAIN" worktree add -b "$branch" "$path" "$base" >/dev/null
    log "已建：分支 ${branch}，路径 $path"
  fi

  # 凭据不在版本控制里，顺手复制一份；不复制 runs/logs 这类产物。
  local f
  for f in deploy/dgx/node.env deploy/dgx/.env deploy/run.toml deploy/judges.toml; do
    if [[ -f "$MAIN/$f" ]]; then mkdir -p "$path/$(dirname "$f")"; cp "$MAIN/$f" "$path/$f"; fi
  done

  if [[ "$sync" == true ]]; then
    local uv; uv="$(find_uv)" || { log "找不到可用的 uv，跳过装依赖"; uv=""; }
    if [[ -n "$uv" ]]; then
      log "在工作区内装依赖（$uv sync --group ops）"
      ( cd "$path" && "$uv" sync --group ops >/dev/null ) || log "装依赖失败，进去手动跑一次：cd $path && uv sync --group ops"
    fi
  fi
  echo "$path"
}

cmd_list() { git -C "$MAIN" worktree list; }

cmd_path() {
  local name="${1:-}"; [[ -n "$name" ]] || die "用法：worktree.sh path <名字>"
  dir_of "$name"
}

cmd_done() {
  local target="${1:-}"; shift || true
  local force=false keep=false
  for a in "$@"; do
    case "$a" in
      --force) force=true ;;
      --keep) keep=true ;;
      *) die "未知参数 $a" ;;
    esac
  done
  [[ -n "$target" ]] || die "用法：worktree.sh done <名字|路径> [--force|--keep]"
  local path
  if [[ -d "$target" ]]; then path="$target"; else path="$(dir_of "$target")"; fi
  [[ -d "$path" ]] || die "找不到工作区：$path"

  if [[ "$keep" == true ]]; then
    log "保留现场（把路径和原因写进本次报告）：$path"
    return 0
  fi
  local dirty branch
  dirty="$(git -C "$path" status --porcelain | wc -l | tr -d ' ')"
  if [[ "$dirty" != "0" && "$force" != true ]]; then
    echo "worktree: $path 还有 $dirty 处未提交改动，先提交并推送，或者显式加 --force：" >&2
    git -C "$path" status --short | head -20 >&2
    exit 1
  fi
  branch="$(git -C "$path" rev-parse --abbrev-ref HEAD 2>/dev/null || echo HEAD)"
  git -C "$MAIN" worktree remove --force "$path"
  git -C "$MAIN" worktree prune
  log "已释放 $path"
  if [[ "$branch" != "HEAD" ]]; then
    log "分支 $branch 还在，确认已合并或已推送后删：git -C \"$MAIN\" branch -d $branch"
  fi
}

case "${1:-}" in
  new)  shift; cmd_new "$@" ;;
  list) cmd_list ;;
  path) shift; cmd_path "$@" ;;
  done) shift; cmd_done "$@" ;;
  *)    sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//' ;;
esac
