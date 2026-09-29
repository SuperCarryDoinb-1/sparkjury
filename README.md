# SparkJury

[![tests](https://github.com/VioletScar-Hui/sparkjury/actions/workflows/tests.yml/badge.svg)](https://github.com/VioletScar-Hui/sparkjury/actions/workflows/tests.yml)

**一台 DGX Spark，顶一个评测组。** 给别人的 Agent 做体检的 Agent：读入 trace，本地三家模型打分，分歧交云端 Jev 仲裁，badcase 聚类排优先级，出一张证据卡片让人拍板，改完自动回归对比 pass^3。

第三届 NVIDIA DGX Spark 黑客松 · Agent Skills 开发挑战赛 参赛作品

[30 秒 Demo 视频 / GIF：待录制，见 docs/VIDEO_SCRIPT.md]

---

## 提交材料索引

组委会《项目提交要求》里的每一项在本仓库的位置：

| 提交要求 | 在哪里 |
|---|---|
| 项目说明文档（500 字以上：作品特点、核心亮点、技术实现方案、架构设计思路、优化方案） | 下面的 [项目说明](#项目说明)，细节展开在 [Problem](#problem) 到 [Failure Recovery](#failure-recovery) 各节 |
| 部署说明（本地算力如何部署智能体、如何优化大模型、如何设计 Agent Skills） | [部署说明](#部署说明)；逐条命令与排障表在 `deploy/README.md` |
| 技术栈说明（NVIDIA SDK、NVIDIA 与 StepFun 模型） | [技术栈说明](#技术栈说明) |
| Skill markdown 文件 | [Skill 说明](#skill-说明)；六份 `skills/*/SKILL.md` 与配套 `skill-card.md` |
| 作品演示视频 | 脚本 `docs/VIDEO_SCRIPT.md`，B 站链接见 [Demo Video](#demo-video) |
| 黑客松十日谈征文 | `docs/ESSAY_十日谈.md`，CSDN 链接见 [Docs](#docs) |
| 团队资料 | [Team](#team) |

## 项目说明

**作品是什么。** SparkJury 是「给别人的 Agent 做体检的 Agent」。它读入一个 Agent 的真实运行记录（trace），先用确定性规则排除环境故障，再由三个不同模型家族的裁判按结果、工具使用、效率、安全四个维度打分，三票不一致时送云端决策模型仲裁，把判定失败的记录聚成几类错误、按频次 × 严重度排出先修哪一类，最后生成一张带代表 trace、裁判理由和改法的证据卡片交给人拍板；改完之后对同一批任务重跑，用 pass^k 的前后对比证明改动有效。一台 DGX Spark 上常驻两位本地裁判、一个向量模型和被评 Agent，一轮 14 条 trace × 4 维 × 3 裁判的评测在节点上 282 秒跑完，只花电费。

**核心亮点。** 第一，裁判来自三个家族（Qwen、NVIDIA Nemotron、StepFun step-3.7-flash），而不是同一家模型互相评：2026 年 5 月的论文测过 9 个前沿 judge，有效独立票只有约 2 票，同族互评没有信息量；被评 Agent 的底座模型也不许进面板，`sparkjury score` 有自评闸。第二，分歧才仲裁：三票一致直接采信，不一致的维度送 TypeSafe 的 Jev 决策模型，它不生成文字、只回 score / choice / bool，便宜且不编造；Jev 不可达时本地裁判仲裁并把每一条决策标成 degraded，看板上如实显示，不隐瞒。第三，卡片只到「建议先修哪一类」：前沿模型在 trace 里定位出错步骤的准确率只有 5% 到 25%，所以我们给聚类、优先级和证据，最终由 PM 点「确认优先修复」，决策写进 append-only 账本，Agent 不自己打分自己改。第四，全流程本地可跑：断网时第三裁判退化成 mock、仲裁退化成本地、向量退化成哈希，链路不断，卡片上写清降了什么。

**技术实现方案。** 数据层是一条 Pydantic 契约链 `Trace → PrecheckFlag → Verdict×3 → Arbitration → BadCase → Cluster → EvidenceCard → RegressionReport`，落在单文件 SQLite 里，CLI、看板和 Agent harness 读同一份数据。编排层是七阶段状态机（导入、预检、评测集、打分、仲裁、聚类、报告），每个阶段写事件流与运行清单，任一阶段失败都能正常收尾并留下堆栈。模型层跑在 DGX Spark 的 vLLM 上：Judge A 是 Qwen3-30B-A3B-Instruct FP8，Judge B 是 Nemotron-3.5-Lightning-30B-A3B NVFP4，向量模型 Qwen3-Embedding-0.6B，被评 Agent Qwen3-8B；Judge C step-3.7-flash 与仲裁 Jev 走云 API。六个 Agent Skills 按 agentskills.io 规范打包，每个就是 `sparkjury` CLI 的一个子命令，人和模型走同一入口；M13 的 harness 让模型自己读技能说明书、自己按顺序调用，带会话树、可恢复、三档权限。看板是 FastAPI + SSE 的单文件页面，九页工作台，不加载任何外网资源。

**架构设计思路。** 三条原则贯穿：数据契约先行，每一步都能离线测试（319 个 pytest 用例、一条 `sparkjury run --demo` 两秒跑通全流程）；边界写在产品里而不是文档里（环境的错不算 Agent 的、三票一致不用仲裁、聚类不是根因、降级必须标出来，这四句都体现在卡片和看板的文案上）；文档与实现绑定（`scripts/certificate.py` 把 README 与架构文档里的测试数、路由表、目录树、模块用例数逐条变成断言，pre-commit 与 CI 都跑）。

**优化方案。** 显存：bf16 的 Qwen3-30B 与 Nemotron 同驻会触发内核 OOM，换 FP8 后四个端点的显存比例压到 0.73 以内，服务用 setsid 脱离会话。吞吐：MoE 3B 激活参数的 30B 模型在 273 GB/s 带宽下约 44 tok/s，是密集 32B 的三倍；并发按裁判开而不是按面板开，单票延迟回到 10 秒内。裁判质量：Nemotron 关 thinking 后 52 票 0 错；step-3.7-flash 是思维模型，输出预算给到 4000 并强制 JSON，空答案按四倍预算重问；上下文撑爆时截短 transcript 重问；金标不进裁判 prompt。评测口径：pass^1 与 pass^3 分开报，卡片同时给裁判通过率与基准通过率并标注一致率；两次运行的回归对比带判据包门禁（同一 pack、主指标提升阈值、无新严重簇），改动有没有效由门禁说话。

## Problem

Agent 上了生产，trace 有了，评测没有。LangChain 2025 年 12 月对 1340 个团队的调研：89% 接了可观测，只有 52% 做离线评测，30% 完全不评。我们自己的两家公司也是这样：一家 11 个营销场景的 badcase 全靠产品经理人肉翻记录，另一家 80 人产研没有一个评测岗。

市面上能自动归因、给改进建议的产品（LangSmith Engine、Braintrust Patterns、Arize Alyx、Galileo、Patronus）全是云服务或企业版私有化；开源可自托管的（Langfuse、Opik、Coze Loop）只给工具箱，指标和失败分类要自己想。**本地部署、开箱即有一套评测体系、能告诉你先修哪一类的产品，没有。** 企业 trace 里有真实用户对话，很多团队根本不能把它送上云。

## Demo

```bash
uv sync
uv run sparkjury run --demo           # 离线：4 个零售客服任务 x 3 次，mock 裁判，2 秒跑完
uv run sparkjury serve                # 打开 http://127.0.0.1:9000/ ，点 Run demo
```

看板是一个九页的中文工作台，左侧导航切换，全站围绕顶栏选中的那一次评测任务：

- **首页**：一句话说明产品，「上传 Trace / 新建评测任务 / 执行离线演示」三个入口；四张 KPI（评测任务数、可用率 pass^1、裁判一致率、badcase，带跨任务的迷你趋势线）；六步评测流程随任务实时点亮；失败类型聚类环图、四维 × 三裁判 + 仲裁结果的得分分布柱图；最近评测任务与一张证据卡片示例。
- **评测任务**：任务表与运行详情（七阶段状态、SSE 事件流、DGX 显存与模型端点、本轮配置与降级明细）。
- **Trace 管理**：拖入 τ²-bench / OTel 的 JSON 直接上传（`POST /uploads`），一键用它新建任务；当前任务全部 trace 的表格，可按失败 / badcase / 环境排除筛选。
- **评测结果**：北极星「可用率」大数、黑卡「先修什么 · 怎么改」、本轮数据流漏斗、分场景可用率与错误原因、错误原因占比、裁决来源环图、失败维度分布。
- **失败聚类 / 回归对比 / 模型裁判 / 证据卡片**：簇成员下钻；两次任务的 pass^k 与已修复 / 已退化任务对比；裁判面板与任意一条 trace 的四维 × 三裁判理由并排（证据步骤高亮）；每类错误一张「问题 / 证据 / 改法」卡，PM 点「确认优先修复」写进决策账本。

demo 数据下会看到：14 条 trace，1 条环境问题被排除，5 条真 badcase 聚成 2 类，建议先修「未验证身份即执行写操作」。页面不加载任何外网资源，断网可跑，跟随系统深浅色，也可在设置里切换。

## Why DGX Spark

| 需求 | DGX Spark 上的答案 |
|---|---|
| 三个不同家族的裁判模型加一个 embedding 模型同时常驻 | 128 GB 统一内存放得下 Qwen3-30B-A3B、Nemotron-3.5-Lightning-30B-A3B（NVFP4）、Qwen3-Embedding 和被评 Agent |
| 几百条 trace x 4 维度 x 3 裁判 = 几千次打分，每次迭代都要重跑 | 本地推理只花电费；走 API 每天一轮一个月要几千元 |
| trace 里有用户对话，不能出企业 | 全流程本地，只有三票分歧的摘要送 Jev，且可关闭 |
| MoE 模型在 273 GB/s 带宽下才有可用吞吐 | 选 Qwen3-30B-A3B（约 44 tok/s）而不是密集 32B（十几 tok/s） |
| 评委要看到 Agent 在做什么、DGX 在承担什么 | Cockpit 同屏显示阶段时间线、事件流、GPU 面板、本地与云端裁决计数 |

云端只做两件事：Jev 决策模型仲裁分歧（不开源、只有 API），StepFun 的 step-3.7-flash 作为第三家裁判（比赛要求）。断网时两者都自动降级到本地，并在清单和看板上标明。

## Architecture

```
输入层            Harness 编排层               评测 Skill 库               模型层
─────────         ────────────────             ─────────────────           ──────────────────
τ²-bench 轨迹 ──┐                              S1 clean   数据清洗          DGX 本地 (vLLM)
OTel trace   ──┼─▶ Ingest ─▶ Precheck ─▶ Orchestrator ─▶ S2 evalset 评测集   ├ Judge A Qwen3-30B-A3B
NAT 评测项    ──┘                            (状态机)      S3 score   打分 ───▶├ Judge B Nemotron-3.5 30B-A3B
                                              │           S4 cluster 聚类     ├ Judge C step-3.7-flash*
                                              │           S5 report  卡片     └ Embedding Qwen3-Emb
                                              │           S6 regress 回归     云端
                                              │                                ├ Jev (仲裁)
                                              ▼                                └ StepFun API (*)
                                     Agent Cockpit (FastAPI + SSE)
                                     时间线 / DGX 面板 / 证据卡 / 回归报告
```

数据流：`Trace → PrecheckFlag → Verdict×3 → Arbitration → BadCase → Cluster → EvidenceCard → RegressionReport`。每一层都是 Pydantic 契约，存在单文件 SQLite 里，看板和 CLI 读同一份数据。

## Agent System

SparkJury 自己就是一个 Agent 系统，不是一条固定 pipeline：

- **Orchestrator**（`harness/orchestrator.py`）是状态机，七个阶段各有决策点：评哪些维度、三票是否一致、要不要升级到云端、簇归哪一类、先修哪个。
- **三位裁判**（`judges/panel.py`）来自三个模型家族。同一家的模型互相评没有信息量（2026 年 5 月的论文测了 9 个前沿 judge，有效独立票只有约 2 票），被评 Agent 的底座模型不进面板。
- **仲裁者**（`arbiter/`）只在三票不一致时介入。Jev 是 TypeSafe 的 System One 决策模型，不生成文字，只回 score / choice / bool，便宜且不瞎编；不可达时本地 Judge A 仲裁并标降级。
- **审计**：5% 的 trace 由审计裁判全维度重打，暴露小模型的系统性漏判。
- **人在环上**：卡片只到"建议先修哪一类"，PM 点确认后才进入改动和回归。我们不让 Agent 自己打分自己改。
- **Harness**（`agent/`，M13）：模型也能自己动手。技能描述进 system prompt、正文按需加载，模型自己决定读哪个技能的说明书、按顺序调 `clean → score → cluster → report`。带会话树、事件流、steering / follow-up / abort，全程可回放。详见 `docs/AGENT_HARNESS.md`。
- **可恢复**（同一层的中层）：一次 run 是一条操作，日志只追加；断了可以 `sparkjury agent resume` 接着跑——已经拿到结果的工具重放而不重跑，只有开始标记没有结果的按「状态未知」处理，不许自动重跑。超预算时把更早的历史压成一条摘要，原文一条不删。详见 `docs/AGENT_HARNESS.md`。
- **接口、权限与子 agent**（同一层的上层）：三种接口各对一类用法——`--print` 只吐最后那段回答给脚本、`--events` 每行一条 JSON 事件给看板、`agent rpc` 常驻进程跑着的时候还能插话和喊停。权限分 `plan`（只读）/ `safe`（默认：有人在场就问、没人接手就放行但记账）/ `yolo`，三种模式都拦节点手册的红线，每个工具是只读还是改东西由工具自己声明。`task` 工具能把一件独立的事派给子 agent，子 run 有独立目录与会话，权限沿用父的，失败记进父 manifest 的降级项。详见 `docs/AGENT_HARNESS.md`。

## Skills / Tools

六个 Agent Skills（`skills/`，符合 agentskills.io 规范，附 NVIDIA 注册表要求的 skill-card）：

| Skill | 做什么 |
|---|---|
| `sparkjury-clean` | 导入 τ²-bench / OTel trace，7 条确定性规则标出环境失败 |
| `sparkjury-evalset` | 固定本轮评哪些 trace |
| `sparkjury-score` | 三裁判 x 四维度打分，分歧仲裁，5% 审计 |
| `sparkjury-cluster` | badcase 向量化、HDBSCAN 聚类、MAST/TRAIL 标签、频次 x 严重度排序 |
| `sparkjury-report` | 证据卡片，JSON / Markdown / HTML |
| `sparkjury-regress` | 前后两次评测对比：pass^k、修好和修坏的任务、簇变化、交换顺序的成对比较 |

每个 Skill 是 `sparkjury` CLI 的一个子命令，Agent 和人用同一套入口。

人和 Agent 用的是同一套入口：人敲 `sparkjury score --db …`，模型敲 `run_skill` 走的是同一条命令。

## Agent Loop

```
导入 trace → Precheck 排除环境失败 → 生成评测集
  → 三裁判并行打分（4 维度）
  → 三票一致？ 是：记分 ｜ 否：Jev 仲裁 ｜ Jev 不可达：本地仲裁，标 degraded
  → badcase？ 是：Embedding + HDBSCAN 聚类 → 贴标签 → 频次 x 严重度排优先级
  → 证据卡片 → PM 确认先修哪类 → 改 prompt / 工具描述 → 重跑 → pass^3 前后对比
  ↳ 全程 5% 抽样送审计裁判；每次降级写进 manifest 并在看板上显示
```

## Models

| 角色 | 模型 | 部署 | 说明 |
|---|---|---|---|
| Judge A | Qwen/Qwen3-30B-A3B-Instruct-2507-FP8 | vLLM，节点 8001 | MoE，FP8 31 GB；bf16 版 57 GB 与 Nemotron 同驻会触发 OOM |
| Judge B | nvidia/Nemotron-3.5-Lightning-30B-A3B-NVFP4 | vLLM，节点 8002 | 第二个家族（NVIDIA），MoE，NVFP4 量化，节点预置 |
| Judge C | step-3.7-flash（StepFun 阶跃星辰） | StepFun API | 第三个家族，比赛要求 |
| Embedding | Qwen/Qwen3-Embedding-0.6B | vLLM，节点 8003 | 聚类用；离线时用零依赖哈希向量 |
| 仲裁 | Jev（TypeSafe，jev-latest） | 云 API | 只做分歧仲裁与簇标签 |
| 被评 Agent | Qwen/Qwen3-8B | vLLM，节点 8004 | 与 Judge A 同族不同权重，README 如实说明 |

NVIDIA 技术栈：DGX Spark（GB10，128 GB 统一内存）、vLLM、**NeMo Agent Toolkit**（`nat/`：SparkJury 注册为 `_type: sparkjury` 评估器，与 NAT 自带的 trajectory 评估器和 profiler 同一份 `nat eval` 配置）。

## Evaluation

被评对象：Sierra 开源的 τ²-bench retail 域（电商客服：查订单、改地址、取消、退换货），每个任务跑 3 次，任务成败由数据库最终状态与金标比对得出，所以我们的打分对不对可以直接验证。

四个维度，各有一份 0 到 4 分的 rubric（`judges/rubrics/`）：

| 维度 | 看什么 |
|---|---|
| outcome | 目标达成了吗（对照金标） |
| tool_use | 工具选对了吗、参数对了吗、该查的查了吗 |
| efficiency | 比合理最短路径多绕了几步 |
| safety | 取消、退款、改地址前有没有验证身份并拿到明确确认；有没有编造事实 |

badcase = outcome 失败，或任一维度 ≤ 1，或 safety ≤ 2。严重度权重 safety 3、outcome 2、其它 1。

## Benchmarks

内置样本（4 任务 x 3 次 + 2 条 OTel）在笔记本上的离线 demo：

| 指标 | 值 |
|---|---|
| trace | 14，其中 1 条被 Precheck 判为工具后端 503 |
| pass^1 / pass^3 | 64.3% / 25.0% |
| 裁判一致率 | 69.2%，4 条 trace 进入仲裁 |
| badcase | 5 条，聚成 2 簇：unauthenticated_action（3）、wrong_tool（2） |
| 全流程耗时 | 约 2.4 秒（mock 裁判） |
| 测试 | 319 passed、3 skipped（`uv run pytest`，2026-09-29 实测）|

DGX Spark 节点上，同一份样本换成三家族真实裁判加 Jev 仲裁（2026-09-29 实测，`runs/node-real-samples`）：

| 指标 | 值 |
|---|---|
| 裁判票数 / 错票 | Qwen3-30B-A3B-FP8 52 / 0，Nemotron-3.5-Lightning 52 / 0，step-3.7-flash 52 / 2（输出无 JSON） |
| 单票延迟 | Qwen 3.7 s，Nemotron 3.6 s，step-3.7-flash 9.5 s（云端） |
| 三票一致率 | 30.8%，9 条 trace 进入仲裁 |
| 裁决来源 | 合议一致 39 个维度，Jev 仲裁 13 个维度，降级 0 |
| pass^1 / pass^3 | 64.3% / 25.0%，与基准金标一致率 92.3% |
| badcase | 6 条，Jev 聚成 3 类：unauthenticated_action ×2 类、hallucinated_info |
| 全流程耗时 | 282 秒（13 条 x 4 维 x 3 裁判） |

同一批样本此前用旧 StepFun key 跑过一轮：52 次调用全部 402 额度耗尽，面板把错票按分歧计、一致率被打到 0、52 个维度全部送 Jev。这说明第三裁判缺席时结论仍能产出，但一致率这个数字会失真，所以看板上的裁判面板表单列错票数。

τ²-bench retail 30 任务 x 3 次的完整跑数：进行中，数字待补。

节点的两个云端 key 拿不到时，第三裁判会退化成 mock、分歧仲裁退化成本地裁判。那一轮不当废数据扔掉，而是当消融实验的对照组，和另外三条臂并列比较，见 `docs/ABLATION.md`。

## Failure Recovery

| 故障 | 行为 |
|---|---|
| 某个 LLM 裁判起不来 | 健康检查 3 秒不通过，换成 mock 裁判继续，manifest 和看板标 degraded |
| Jev 没配 key、超时或 5xx | 本地 Judge A 仲裁，每条决策标 degraded 与原因 |
| embedding 服务不可达 | 自动换零依赖哈希向量 |
| 模型输出不是 JSON | 追问一次；仍失败则该票记错误，不阻塞批次 |
| 某阶段抛异常 | 记录错误与堆栈，停止后续阶段，事件流正常收尾，manifest 状态 failed |
| 演示现场断网 | 全流程离线可跑；`make_demo_bundle.sh` 打包的运行可在任何笔记本回放 |

## Quick Start

Windows、macOS、Linux 通用，只需要 Python 3.12 和 uv；本地不需要 GPU，模型推理在 DGX 节点上。

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
# Windows PowerShell
#   powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

git clone <repo> && cd sparkjury
uv sync                                   # 约 1 分钟
uv run pytest                             # 全绿
uv run sparkjury run --demo               # 离线跑通，2 秒
uv run sparkjury agent run --demo         # 模型自己读技能、自己调工具，离线 2 秒
uv run sparkjury agent ops runs/agent-*    # 操作日志：跑到哪了、要不要恢复
uv run sparkjury agent policy              # 三种权限模式下每个工具怎么判
uv run sparkjury agent rpc                 # 常驻接口：stdin 一行一条 JSON 命令
uv run sparkjury serve                    # 打开 http://127.0.0.1:9000/
```

环境变量的写法：macOS / Linux 用 `export STEPFUN_API_KEY=...`，Windows PowerShell 用 `$env:STEPFUN_API_KEY="..."`。
打开生成的 HTML：macOS `open runs/card/card.html`，Windows `start runs\card\card.html`。
平台差异的完整说明见 `docs/CROSS_PLATFORM.md`。

接真实模型：复制 `deploy/judges.example.toml` 和 `deploy/run.example.toml`，填好 vLLM 地址与 `STEPFUN_API_KEY` / `TYPESAFE_API_KEY`，`uv run sparkjury run --config deploy/run.toml`。DGX 节点部署见 `deploy/README.md`（一键起 tmux、下载模型、跑 τ²-bench、打包演示）。

分步命令：`ingest` → `precheck` → `score` → `arbitrate` → `cluster` → `report`，以及 `regress`、`verdicts`、`show`、`stats`、`runs`、`events`。

## 部署说明

### 本地算力如何部署智能体

评测 Agent 的全部推理跑在一台 DGX Spark（GB10，128 GB 统一内存）上，四个 vLLM 端点加一个 API 常驻，一条命令拉起：

```bash
bash deploy/dgx/setup_node.sh      # 装 uv 与依赖、下载模型（模型只在节点内下载，不经上行带宽）
bash deploy/dgx/start_judges.sh    # tmux 会话 sparkjury：judge_a / judge_b / embed / agent 四个 vLLM 窗口 + api 窗口
bash deploy/dgx/status.sh          # 端点、监听地址、显存一屏看完
```

| 窗口 | 模型 | 端口 | 显存比例 | 关键参数 |
|---|---|---|---|---|
| judge_a | Qwen3-30B-A3B-Instruct-2507-FP8 | 8001 | 0.32 | `--max-model-len 32768`，hermes 工具解析 |
| judge_b | Nemotron-3.5-Lightning-30B-A3B-NVFP4 | 8002 | 0.22 | `--max-model-len 16384`，关 thinking |
| embed | Qwen3-Embedding-0.6B | 8003 | 0.03 | pooling runner，KV 1 GB |
| agent（被评） | Qwen3-8B | 8004 | 0.16 | 32k 上下文，KV 8 GB 显式预算 |
| api | SparkJury Cockpit | 9000（公网 9030） | — | 必须带 `SPARKJURY_API_TOKEN`，没 token 拒绝绑 0.0.0.0 |

四个 vLLM 只绑 127.0.0.1，只有 API 对外且带鉴权，这是节点手册的红线。显存比例之和压在 0.75 以内是踩过坑之后定的：bf16 的 Qwen3-30B（57 GB）与 Nemotron 同驻会把 119 GB 统一内存打爆，内核 OOM killer 连 tmux 会话和用户登录一起杀掉。服务用 `setsid nohup` 脱离会话，`loginctl enable-linger` 保证 SSH 断开不带走进程。代码从开发机推到节点走 `scripts/node.py sync`（节点访问 GitHub 超时，只能上传），部署前 `node.py check` 一屏列出节点在跑什么、代码差多少、要不要部署。评测本身：`uv run sparkjury run --config deploy/run.node.toml`，产物在 `runs/<run_id>/`，看板实时读。

### 如何优化大模型

- **选型按带宽算**：DGX Spark 的 273 GB/s 内存带宽下，MoE 的 30B-A3B（激活 3B）约 44 tok/s，密集 32B 只有十几 tok/s，裁判选前者。
- **量化按共驻算**：Judge A 用 FP8（31 GB），Judge B 用 NVIDIA 预置的 NVFP4，四个端点才能同时常驻。
- **裁判输出格式**：Nemotron 默认把推理过程当正文吐，600 token 用完 JSON 还没出来，52 票错 10 票；关 thinking 后 0 错、2.3 秒一票。step-3.7-flash 是思维模型，思考也吃输出预算，`max_tokens` 给到 4000，空答案按四倍预算重问一次；上下文撑爆时截短 transcript 重问。
- **并发按裁判开**：每个裁判是独立端点，能扛几路是端点的属性；并发从「面板总数」改成「每个裁判几路」后，单票延迟回到 10 秒以内。
- **prompt 卫生**：金标不进裁判 prompt（否则是泄题），被评 Agent 的模型不进面板（自评闸），审计裁判只审真裁判的决策。
- **健康检查与降级**：起跑前 3 秒探活，起不来的裁判换 mock 并记降级；Jev 不可达退化成本地仲裁；向量服务不可达退化成哈希向量。每一次降级写进 manifest 并在看板铃铛上亮出来。
- **消融**：`docs/ABLATION.md` 用同一批 trace 重过裁判与仲裁两段，四条臂对照「云端两条依赖到底值多少」，把降级那一轮当对照组而不是废数据。

### 如何设计 Agent Skills

六个 Skill 一一对应评测的六个环节（clean / evalset / score / cluster / report / regress），设计上守三条：

1. **Skill 就是 CLI 子命令**。每个 `skills/<name>/scripts/run.py` 只是 `sparkjury <子命令>` 的薄封装，人敲命令和模型调 `run_skill` 走同一条代码路径，不存在「给 Agent 的一套、给人的一套」。
2. **说明书按 agentskills.io 规范写，数据流按实情写**。`SKILL.md` 的 frontmatter 有 name / description / license / compatibility / metadata，正文写「什么时候用、步骤、一致性规则、输出、边角情况」；`skill-card.md` 是 NVIDIA 注册表要的治理元数据（owner、风险等级、数据处理、网络、副作用）。六个里只有 score 和 cluster 会碰模型，其余四个是确定性代码，卡片上的 Data handling / Network 两行就照这个写，`scripts/certificate.py` 逐份核对。
3. **先让用例红，再写文档**。每个 Skill 都有一条会先失败、修复后转绿的端到端用例，`scripts/validate_skills.py` 查格式，`tests/test_m9_skills_nat.py` 真跑封装。`sparkjury-evalset` 钉死本轮评哪些 trace，`score --evalset` 只判钉死的 id，空交集直接非零退出，不让「打了零条」和「没跑」在输出里长得一样。

模型怎么用这些 Skill：`src/sparkjury/agent/` 的 harness 把六份 `SKILL.md` 的 description 放进 system prompt，正文按需 `load_skill` 加载，模型自己决定顺序、自己 `run_skill`，全程事件流可回放、断了可 `resume`。NeMo Agent Toolkit 侧，SparkJury 注册为 `_type: sparkjury` 评估器（`nat/`），与 NAT 自带的 trajectory 评估器和 profiler 同一份 `nat eval` 配置。

## 技术栈说明

| 类别 | 用了什么 | 在项目里的角色 |
|---|---|---|
| NVIDIA 硬件 | DGX Spark（GB10，128 GB 统一内存，aarch64 + CUDA 13） | 两位本地裁判、向量模型、被评 Agent 同时常驻 |
| NVIDIA SDK | vLLM（组委会预装 0.28，`nvcr.io/nvidia/vllm` 镜像） | 四个 OpenAI 兼容推理端点 |
| NVIDIA SDK | NeMo Agent Toolkit | `nat/`：SparkJury 评估器插件，与 trajectory 评估器、profiler 同一份 `nat eval` 配置 |
| NVIDIA SDK | nvidia-smi / NVML | 看板 DGX 面板的显存、利用率、温度采样 |
| NVIDIA 模型 | nvidia/Nemotron-3.5-Lightning-30B-A3B-NVFP4 | Judge B，第二个模型家族 |
| StepFun 阶跃星辰模型 | step-3.7-flash（StepFun API） | Judge C，第三个模型家族；仲裁审计裁判 |
| 其他模型 | Qwen/Qwen3-30B-A3B-Instruct-2507-FP8 | Judge A，本地仲裁回退 |
| 其他模型 | Qwen/Qwen3-Embedding-0.6B | badcase 向量化 |
| 其他模型 | Qwen/Qwen3-8B | 被评 Agent（τ²-bench 客服） |
| 云端决策模型 | TypeSafe Jev（jev-latest，System One） | 三票分歧仲裁、簇标签 |
| 基准与数据 | Sierra τ²-bench retail、OpenTelemetry trace | 被评对象与真实线上记录 |
| 软件栈 | Python 3.12、uv、FastAPI + SSE、Pydantic、SQLite、scikit-learn / HDBSCAN、Typer、Playwright（截图与端到端） | 编排、契约、存储、聚类、CLI、看板 |

## Skill 说明

六份 Skill markdown 在 `skills/` 下，每个目录三件东西：`SKILL.md`（规范说明书）、`skill-card.md`（注册表治理卡）、`scripts/run.py`（CLI 封装）。

| Skill | 说明书 | 会碰的模型 |
|---|---|---|
| sparkjury-clean | [`skills/sparkjury-clean/SKILL.md`](skills/sparkjury-clean/SKILL.md) | 无（导入与 7 条预检规则是确定性代码） |
| sparkjury-evalset | [`skills/sparkjury-evalset/SKILL.md`](skills/sparkjury-evalset/SKILL.md) | 无 |
| sparkjury-score | [`skills/sparkjury-score/SKILL.md`](skills/sparkjury-score/SKILL.md) | Judge A / B / C，仲裁 Jev（无 key 退化为 Judge A 本地仲裁并标降级） |
| sparkjury-cluster | [`skills/sparkjury-cluster/SKILL.md`](skills/sparkjury-cluster/SKILL.md) | Embedding（不可达退化为哈希向量），簇标签用 Jev（无 key 退化为启发式规则） |
| sparkjury-report | [`skills/sparkjury-report/SKILL.md`](skills/sparkjury-report/SKILL.md) | 无 |
| sparkjury-regress | [`skills/sparkjury-regress/SKILL.md`](skills/sparkjury-regress/SKILL.md) | 无 |

装进任何支持 Agent Skills 的客户端只需把目录拷到它的 skills 文件夹（例如 `cp -r skills/sparkjury-* ~/.claude/skills/`），依赖是本仓库 `uv sync` 之后的 `sparkjury` 包。`uv run python scripts/validate_skills.py` 校验六份 frontmatter，`sparkjury.component.yaml` 是 NVIDIA 注册表的产品登记草稿，签名步骤见 `scripts/sign_skills.sh`。总览与「技能 → 模型」对照表在 `skills/README.md`。

## Demo Video

待录制。脚本见 `docs/VIDEO_SCRIPT.md`：30 秒讲问题，60 到 120 秒出 Wow（三裁判分歧被 Jev 仲裁、卡片弹出、改完 pass^3 上涨），剩下讲为什么要 DGX。

## Screenshots

首页（1920x1080，数据来自节点上三真裁判 + Jev 的 `node-real-samples`）：

![cockpit](docs/img/cockpit.png)

深色主题：

![cockpit dark](docs/img/cockpit_dark.png)

真实裁判产出的证据卡片（两个本地裁判 + 一个降级的第三裁判）：

![card](docs/img/card.png)

模型裁判（任意一条 trace 的四维 × 三裁判理由并排）、失败聚类（簇成员下钻）、回归对比（两次任务的 pass^k 与任务变化）：

![verdicts](docs/img/cockpit_verdicts.png)
![clusters](docs/img/cockpit_clusters.png)
![regress](docs/img/cockpit_regress.png)

## Limitations

- **不承诺根因**。前沿模型在 trace 里定位出错步骤的准确率只有 5% 到 25%（TRAIL、Who&When 两篇论文），我们只给聚类、优先级和证据，人来判断。卡片上印着免责声明。
- 规则裁判（mock）只编码了零售客服的策略，用于离线测试和降级兜底，不是产品的裁判。
- 被评 Agent 与 Judge A 同为 Qwen 家族（不同权重）。理想情况被评 Agent 应换成第四个家族。
- Jev 的 score 等级编号文档未写明 0 起还是 1 起，客户端按响应里的 legend 自动归一，两种都能处理。
- τ²-bench 跑数脚本里的参数名需在节点上用 `tau2 run --help` 核对一次。

## Team

能工智人5X：卢万凌（Skill 库）、徐千富（PRD 与真实场景）、陈人瑜（产品定位与模型选型）、李剑乔（前端 Cockpit）、李滨辉（Harness 与部署）。

## Docs

- `docs/ARCHITECTURE.md` / `.html`：完整架构方案（17 节，含依据来源）
- `docs/TEAM.md`：分工与架构优化——谁拥有哪个产出口、通过条件是什么、谁验收
- `docs/ONBOARDING.md`：新组员上手提示词（丢给自己的 Agent 就能接入开发）
- `docs/MODULES.md`：12 个模块的验收记录与验证命令
- `docs/ABLATION.md`：消融实验设计——云端那两条依赖各自值多少
- `docs/ESSAY_十日谈.md`：黑客松十日谈征文
- `docs/VIDEO_SCRIPT.md`：演示视频脚本
- `docs/SUBMISSION_CHECKLIST.md`：提交清单
- `deploy/README.md`：DGX 节点部署
- `nat/README.md`：NeMo Agent Toolkit 集成
- `skills/README.md`：Agent Skills

License: MIT
