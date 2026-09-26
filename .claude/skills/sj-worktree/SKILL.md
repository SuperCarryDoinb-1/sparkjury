---
name: sj-worktree
description: SparkJury 的开工到收尾流程：从 origin/main 新建 worktree 与分支、在工作区内装依赖、提交前的门禁、收尾释放工作区。当你要在这个仓库里改代码、加模块、改部署脚本，或者用户说开个分支、开个 worktree、收尾、清理工作区时使用。
whenToUse: 准备在本仓开始一项改动、要开分支或 worktree、要提交推送、要收尾释放工作区时。
---

> 这一份和 `.agents/skills/sj-worktree/SKILL.md` 内容相同，改的时候两边一起改。

# 开工到收尾：worktree 隔离

## 为什么这么做

主工作区可能停在任意分支、任意脏状态，也可能正被别的任务用着（节点上跑的 τ²-bench 和 full_loop 就是例子）。把主工作区当成改动来源，等于把你的改动和别人的未完成工作搅在一起，出了问题分不清是谁造成的。所以本仓的规矩是：每一项改动都从 `origin/main` 拿一份干净的工作区，改完提交推送，收尾释放。

## 开工

```bash
bash scripts/worktree.sh new fix/tau2-timeout
```

这一条命令做四件事：取 `origin/main`，用当前那个 commit 建分支 `fix/tau2-timeout`，把工作区放在 `.worktrees/fix-tau2-timeout`，在里面跑 `uv sync --group ops` 装依赖。它还会把 `deploy/dgx/node.env`、`deploy/dgx/.env` 这些不在版本控制里的凭据复制进去，所以在 worktree 里也能连节点。

名字带斜杠就按你写的当分支名，不带前缀默认 `task/<名字>`。只想复现别人报告里的数字、不打算改东西，加 `--detach` 建一个游离工作区。看看现在有哪些：`bash scripts/worktree.sh list`。

进去之后所有命令都在 worktree 里跑。`scripts/node.py` 的路径是按文件自身位置解析的，所以在 worktree 里跑 `check` 和 `sync`，推给节点的是这个 worktree 的代码，不会碰主工作区。

## 提交前的门禁

仓库自带两个 hook，每人配置一次：

```bash
git config core.hooksPath .githooks
```

`pre-commit` 跑 `scripts/check_staged.py`，管三件事：拦住 `.env`、`node.env`、`runs/`、`logs/`、`*.db`、`.worktrees/` 这类不该进仓库的路径；扫明文密钥；对暂存的 `.py` 做语法编译、对 `.sh` 跑 `bash -n`。几秒钟的事，全量测试不放在这里。

`commit-msg` 要求提交信息首行是真的描述，`wip`、`fix`、`update` 这种会被拒。本仓统一用中文写清改了什么、为什么。

验证按风险相称，不要一律全仓。改一个模块就跑对应测试，碰了共享契约、部署脚本或入口才跑全套。跑测试用 `uv run pytest -q`（节点上 uv 在 `~/.local/bin/uv`，需要写全路径或用 `bash -lc`）。改了部署脚本顺手 `bash -n` 过一遍。

## 收尾

推送并开完 PR 之后：

```bash
bash scripts/worktree.sh done fix/tau2-timeout
```

工作区里还有未提交改动时它会拒绝并列出那些文件，这是有意的，别习惯性加 `--force`。失败、回滚或者需要人工排查时不要释放：

```bash
bash scripts/worktree.sh done fix/tau2-timeout --keep
```

然后把绝对路径和原因写进本次报告。每一轮都要交代「已释放 / 保留（原因）」，静默留下的临时工作区就是这么堆积起来的。

## 别做的事

不要在主工作区上 `reset`、`stash`、`checkout`、`switch`、`pull` 来制造一个干净环境；不要把主工作区的 `.venv` 软链进 worktree，那会让「干净工作区」名不副实，依赖要在 worktree 内装；不要复用上一轮的 worktree 代表这一轮的输入。
