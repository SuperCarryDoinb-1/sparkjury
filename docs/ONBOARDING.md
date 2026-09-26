# 新组员上手提示词

把下面这一段整段发给组员的 Agent（Claude Code、Codex、Cursor 都行），它就会按本仓库的规矩干活：开隔离工作区、走分支加 PR、在节点上部署验证。

节点密码不要放进这段提示词，单独发给本人——提示词里只写「找队里要」。

规则的本体在 `AGENTS.md`，这段是给新人和新 Agent 的入口摘要。改口径时先改 `AGENTS.md`，再回来对齐这里：同一件事两处说法不一致，比只有一处更坏。

```text
你加入 SparkJury 项目的开发：DGX Spark 黑客松参赛作品，9-29 截止，几个人共用一台节点、在 GitHub 上协作。

仓库 https://github.com/VioletScar-Hui/sparkjury

先克隆，然后完整读一遍根目录的 AGENTS.md —— 它是本仓库对 Agent 的唯一行为契约，冲突时以它为准。

## 环境
git clone https://github.com/VioletScar-Hui/sparkjury && cd sparkjury
uv sync --group ops
git config core.hooksPath .githooks
cp deploy/dgx/node.env.example deploy/dgx/node.env   # 节点密码找队里要，别写进任何提交
gh auth login

三条自检全过再开工：uv run pytest 全绿、uv run sparkjury run --demo 两秒出结果、uv run --group ops python scripts/node.py check 能看到节点。
check 报没权限，先确认已被加进协作者：gh api repos/VioletScar-Hui/sparkjury --jq .permissions.push

## 硬要求
1. 每项改动都从 origin/main 开隔离工作区，不在主工作区直接改：
   bash scripts/worktree.sh new fix/<描述>
   cd .worktrees/<名字>
   收尾 bash scripts/worktree.sh done <名字>；要保留现场加 --keep，并说明原因。
2. main 有分支保护，改动只能走分支加 PR：
   gh pr create --title "一句话说清改了什么" --body-file .github/pull_request_template.md
   别用 --fill（会覆盖模板）。PR 上自动跑三平台测试，全绿才能合并。
   检查还没跑完时合并会被拒，等它跑完；合并成功之前不要删分支，删了 PR 会被直接关掉。
3. 每次提 PR 都要在节点上部署验证一次，结果填进 PR 模板的「节点部署验证」栏。核心命令 uv run --group ops python scripts/node.py check，输出原样贴。没过不算失败，隐瞒才算：写清哪一步、哪条命令、原始报错，PR 留草稿。
4. 节点多人共用。动手前先 check，tmux 里有 tau2full、loop 这类会话就别 sync ~/sparkjury（整体覆盖会毁掉正在跑的长任务），做实验就复制一棵自己的树；长任务必须进 tmux，并先跟队里说一声。
5. 提交信息用中文写清改了什么、为什么，wip/fix/update 会被钩子拒。

红线：禁止 reboot/shutdown/poweroff，禁止改系统级配置，禁止探测内网 192.168.110.0/24，超过 1GB 的文件禁止 scp，8888 和 9000 上的服务必须有鉴权。密钥走 deploy/dgx/.env，不提交、不写进 PR、不造假 key。

## 材料
.agents/skills/sj-worktree 与 sj-push-and-deploy（完整流程）、docs/ARCHITECTURE.md（架构）、docs/MODULES.md（模块验收）、docs/ABLATION.md（消融）、deploy/README.md（节点部署）。PR 描述的真实样例看已合并的 #1 和 #2。

当前进度以 node.py check 和 git log 为准：节点正在跑 τ²-bench 基线，跑完才填 README 的 Benchmarks 数字。

开工前先跟队长确认做哪一块，别自己挑一个新的。
```
