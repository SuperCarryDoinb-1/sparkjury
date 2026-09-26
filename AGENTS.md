# AGENTS.md

写给所有在这个仓库里干活的 Agent（Claude Code、Codex、Cursor 都读这一份）。`CLAUDE.md` 只是指向这里的入口，内容以本文件为准。

## 这个项目是什么

SparkJury 是给别人的 Agent 做体检的评测 Agent：读入 trace，本地三家模型打分，分歧交云端 Jev 仲裁，badcase 聚类排优先级，出证据卡片让人拍板，改完自动回归对比 pass^3。它跑在团队 80 号 DGX Spark 节点上。节点部署说明见 `deploy/README.md`，不依赖系统 ssh/scp 的连节点小工具是 `scripts/node.py`。

## 第一次上手

队友都在自己的机器上从 GitHub 克隆这个仓库，改完推分支开 PR。节点那台机器只当部署目标用：它连不上 GitHub，代码是 `scripts/node.py sync` 走 SSH 推过去的。

```bash
git clone https://github.com/VioletScar-Hui/sparkjury && cd sparkjury
uv sync --group ops                 # ops 组里有连节点用的 paramiko
git config core.hooksPath .githooks # 每人配一次，提交门禁才会生效
cp deploy/dgx/node.env.example deploy/dgx/node.env   # 填节点密码，找队里要
```

给新组员的、可以直接丢给他们 Agent 的上手提示词在 `docs/ONBOARDING.md`；下面这些规矩它就是摘要。

开工前这三条要能跑通：`uv run pytest` 全绿、`uv run sparkjury run --demo` 两秒出结果（离线 mock 裁判，不碰节点）、`uv run --group ops python scripts/node.py check` 能看到节点状态。

## 硬性规则：每一次主动提 PR，都要在节点上部署一次

你每开一个 PR（修 bug、加模块、改脚本、改部署都算），不要停在 `git push`。提交之后接着做一件事：连上那台 DGX Spark 节点，把这次改动同步过去、部署起来、跑一次确认它真的能跑，然后把结果写进 PR 描述。这是本仓库对 Agent 最硬的一条要求。

原因是节点才是真实环境。它是 aarch64 加 CUDA 13，内存和显存是统一的一整块，上面常驻五个 vLLM 端点和两个云端依赖，还可能有队友的 τ²-bench 在跑。笔记本上 pytest 全绿只说明代码逻辑没坏，不代表节点上起得来。之前踩过的坑就属于这一类：bf16 的 Qwen3-30B 和 Nemotron 同时起，会把内核 OOM killer 招来，整个 tmux 会话被端掉。

完整步骤（拿写权限、推分支、看节点、按需部署、冒烟验证、常见卡点）在 `.agents/skills/sj-push-and-deploy/SKILL.md`，你的 Agent 会自动加载它。自己手动做的话，核心是一条命令：

```bash
uv run --group ops python scripts/node.py check
```

它会把节点通不通、GPU 和内存、tmux 里谁在跑、四个端点和 API 起没起、节点上是哪个 commit、你本地是哪个 commit 一次列出来，最后直接给「需要部署 / 需要起服务 / 不需要部署」的判断，输出可以原样贴进 PR。节点上那份代码的版本由 `~/sparkjury/.synced-from` 记录，`sync` 时自动写入。

### 具体怎么做

第一步，先看节点在忙什么，别撞车。

```bash
ssh -p 6030 asus_gx10@61.172.235.130 'tmux ls; bash deploy/dgx/status.sh'
```

如果 `tau2full`、`loop` 这些会话正在跑，不要 stop 它们，也不要抢 GPU，等它跑完或者跟队友说一声再动手。不想用系统 ssh 的话，同样的命令可以走 `uv run --group ops python scripts/node.py run "<命令>"`，连接参数从环境变量或 `deploy/dgx/node.env` 读。

第二步，把这次改动同步到节点。不要指望节点自己去 GitHub 拉代码：实测那台机器访问 github.com 会超时（连 litellm 拉价格表都超时过），能正常访问的是 gitee 和 hf-mirror。所以从你的笔记本把代码推过去，`scripts/node.py sync` 会打包当前目录、上传、在节点解压到 `~/sparkjury`，全程走 SSH。

```bash
uv run --group ops python scripts/node.py sync
```

如果哪天在码云上建了这个仓库的镜像，节点也可以直接 `git pull` 那份镜像，到码云这条网络是通的。

第三步，部署。只有在依赖变了或者服务没起来的时候才需要前两步，服务本来就活着就别重启它。

```bash
ssh ... 'cd ~/sparkjury && bash deploy/dgx/setup_node.sh'
ssh ... 'cd ~/sparkjury && bash deploy/dgx/start_judges.sh'
ssh ... 'cd ~/sparkjury && bash deploy/dgx/status.sh'
```

第四步，冒烟验证，至少跑这三样。

- `~/.local/bin/uv run pytest`：通过的用例数，有没有失败。节点上的 uv 装在 `~/.local/bin/uv`，非登录 shell 里不在 PATH 上，所以要么写全路径，要么用 `bash -lc '...'` 包一层。另外节点上还有 tau2 的虚拟环境，跑之前留意别把 `.venv-tau2` 里第三方包的文本文件当成仓库文件来扫。
- `bash deploy/dgx/status.sh`：四个 vLLM 端点和 API 是不是都 up，监听地址是不是 127.0.0.1（只有 API 的 9000 允许绑 0.0.0.0）。
- 一条真实 run：`uv run sparkjury run --config deploy/run.toml`，跑完打开 `runs/<run_id>/manifest.json` 看 `degradations` 和 `status`。只想确认链路通不通，用 `uv run sparkjury run --demo` 就够了，离线 mock 裁判两秒跑完。

第五步，把结果贴回 PR。PR 描述里加一段「节点部署验证」，写清部署的是哪个 commit 或分支、上面三样各自的实际输出摘要、以及有没有降级或失败项。数字要贴真实跑出来的，不要写「预期通过」。

### 什么算部署失败

部署没通过不算失败，隐瞒才算。任何一步没过，就在 PR 里原样写清是第几步、哪条命令、原始报错（别只写「报错了」），然后把 PR 留在草稿状态等人看。降级要照实标：第三裁判 StepFun 没配 key 会退化成 mock，Jev 没配 key 会退化成本地仲裁，这两种都会写进 manifest 的 `degradations`，转述时不要省略。

### PR 的完成定义

代码改完、本地测试通过、节点部署并冒烟通过、结果贴进 PR 描述，四样齐了才算完成。缺任何一样都别标 ready for review。

## 多人共用一台节点

节点只有一个 SSH 账号，所有队友都用它，`~/sparkjury` 也是大家共用的同一棵工作树，而 `loop`、`tau2full` 这些无人值守任务就跑在这棵树上。这意味着两件必须小心的事。

一是别覆盖别人的工作树。`scripts/node.py sync` 永远解压到 `~/sparkjury`，两个人同时 sync 就是互相覆盖，正在跑的长任务会读到半新半旧的代码。所以动手前先看清楚：`tmux ls` 和 `bash deploy/dgx/status.sh` 各看一眼，确认主树上没有别人在跑东西。要做自己的实验，就把树复制一份再改，比如 `cp -r ~/sparkjury ~/sparkjury-<你的名字>`，在自己的副本里折腾。

二是排队用 GPU。五个 vLLM 端点已经占掉约 92GB 显存，一轮 τ²-bench 三十个任务要跑七个小时左右，两个一起跑只会互相拖死。跑长任务之前在队里说一声，让人知道这块卡什么时候空出来。

## 工作区隔离：开工先开 worktree

主工作区可能停在任意分支、任意脏状态，也可能正被别的任务用着。所以每一项改动都从 `origin/main` 拿一份干净的工作区，不要在「碰巧打开的那个工作区」上直接改。

```bash
bash scripts/worktree.sh new fix/tau2-timeout    # 取 origin/main、建分支、落在 .worktrees/、装依赖、复制凭据
cd .worktrees/fix-tau2-timeout
```

四条规矩。主工作区永远不作为改动或构建来源，禁止对它 `reset`、`stash`、`clean`、`checkout`、`switch`、`pull` 或者覆盖它的文件。依赖在 worktree 内装，不要把主工作区的 `.venv` 软链进去，那会让「干净工作区」名不副实。收尾即释放，`bash scripts/worktree.sh done <名字>`；失败、回滚或需要人工排查时用 `--keep` 保留现场，并把绝对路径和原因写进报告。每一轮都要交代「已释放 / 保留（原因）」，静默留下的临时工作区就是这么堆积起来的。

在 worktree 里跑 `scripts/node.py` 的 `check` 和 `sync` 时，推给节点的是这个 worktree 的代码，不会碰主工作区。

## 提交规范与门禁

每人配置一次钩子：

```bash
git config core.hooksPath .githooks
```

`pre-commit` 拦三类问题：不该进仓库的路径（`.env`、`node.env`、`runs/`、`logs/`、`*.db`、`.worktrees/`）、明文密钥、暂存文件里的语法错误（`.py` 编译、`.sh` 跑 `bash -n`）。`commit-msg` 要求提交信息首行是真的描述，`wip`、`fix`、`update` 这类会被拒，本仓统一用中文写清改了什么、为什么。

main 开了分支保护：非管理员的直接推送会被拒，改动一律走分支加 PR；PR 上会自动跑三平台测试（ubuntu、macOS、Windows），三个检查全绿才允许合并，强推和删除分支也禁掉了。PR 描述用 `.github/pull_request_template.md` 的模板，里面「节点部署验证」那一栏就是上面那条硬性规则的落地位置。管理员保留绕过权限，是留给节点出事时紧急处置用的，不是日常通道。

验证按风险相称，不要一律全仓：改一个模块就跑对应测试，碰了共享契约、部署脚本或入口才跑全套。确有理由绕过钩子时用 `--no-verify`，并在提交信息里写明理由。完整流程在 `.agents/skills/sj-worktree/SKILL.md`。

## 红线（来自节点使用手册，违反会影响全队）

- 禁止 `reboot`、`shutdown`、`poweroff`，禁止改系统级配置（密码、SSH 配置、防火墙、路由、用户权限）。
- 禁止探测内网 `192.168.110.0/24`。
- 超过 1GB 的文件禁止 `scp`，模型一律在节点内下载，上行带宽是 50 支队共用的。
- 长任务必须跑在 `tmux` 里，占着前台会被断连带走。
- 8888 和 9000 上对外提供的服务必须有鉴权。
- 节点在活动结束后会被清空：代码要及时 push，跑出来的产物要及时拷出来。

## 密钥

`deploy/dgx/.env`（StepFun、TypeSafe、API token）和 `deploy/dgx/node.env`（节点密码）都在 `.gitignore` 里，不要提交，也不要写进 PR 描述或截图。要新 key 就找队里要，不要造一个假的塞进去冒充接通。
