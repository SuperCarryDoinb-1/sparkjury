# Agent harness：让模型自己把 SparkJury 用起来

这份文档讲 M13 那一层。它解决的问题很具体：SparkJury 有六个技能、一条七段流水线、四个本地端点，
但在此之前，**模型只会被当成「一问一答的打分器」用**——裁判客户端 `judges/client.py` 发一条消息、
收一段文本，没有工具、没有多轮、没有循环。技能是给人用的命令，被评 Agent 跑在 τ²-bench 自己的框架里。
也就是说：SparkJury 自己是个 Agent 系统，但它没有让自己家的模型动过手。

M13 补的就是这一层：一个能挂工具、能多轮、能中断、能回放的 agent harness，模型在哪一层用都一样——
四个本地端点、云端 StepFun、以后换成别的模型，只换一个 `--model` 参数。

参照的是 pi（earendil-works/pi）的 harness 分层，只做最小闭环。

## 一句话

模型读工具清单 → 自己决定调哪个技能 → 看结果再决定下一步 → 干完为止；全过程一行一行写进会话树，
看得见、能中断、能回放。

## 分层

```
   sparkjury agent run -p "……"          ← 人只说一句话，不说用哪个技能
        │
   ┌────▼─────────────────────────────────────────────────────┐
   │ cli.py        命令行：run / tools / replay / endpoints    │
   ├──────────────────────────────────────────────────────────┤
   │ runtime.py    装配与落盘：run 目录、事件流、用量、manifest │
   ├──────────────────────────────────────────────────────────┤
   │ loop.py       循环：消息 → 模型 → 工具 → 结果             │
   │               steering / follow-up / abort                │
   ├────────────────────────────┬─────────────────────────────┤
   │ ai.py 统一模型入口          │ tools.py 工具注册表          │
   │ 8001/8002/8003/8004 + 云端  │ 六个技能 + 两个只读文件工具   │
   ├────────────────────────────┴─────────────────────────────┤
   │ session.py    会话树：只追加的 JSONL，每条带 parent 指针   │
   └──────────────────────────────────────────────────────────┘
```

画成一张图是四层，但真正决定行为的是两条规矩：

- **技能正文按需加载**。system prompt 里只有技能的名字和一句话描述，模型要看细节得自己调
  `load_skill`。六个技能的 SKILL.md 全塞进提示词要两千多字符，而一次 run 通常只用其中两三个。
- **工具失败是消息，不是崩溃**。技能名写错、文件不存在、子进程超时，都变成模型能看到的一句话，
  它自己决定纠正还是换路；同时记一笔失败，收尾时出现在 `manifest.json` 的 `degradations` 里。

## 一次 run 里发生了什么

以「把这批轨迹过一遍体检」为例：

1. harness 写 system prompt：技能清单（只有描述）、工具清单、干活规矩，然后开一条 `system` 记录。
2. 模型这一轮回的是工具调用而不是文本 → harness 记一条 `tool_call`，执行 `load_skill`，把说明书
   记成 `tool_result` 回灌给模型。
3. 模型接着调 `run_skill` 跑清洗、打分、出卡片，每个技能的 stdout 尾部和退出码都回灌。
4. 某一轮模型不再要求调工具，只回一段话 → 这就是这次 run 的结论，run 正常结束（`end_turn`）。

每一步都同时写进两处：会话树（给回放）和事件流 `events.jsonl`（给看板）。事件用的是流水线那套
`EventBus`，`stage` 固定写 `AGENT`，所以 M8 的看板不需要改就能看到 agent 在干什么。

## 三种打断

| 做法 | 什么时候进 | 用途 |
|---|---|---|
| `steer(text)` | 当前 assistant 回合之后、下一个请求之前 | 工具跑着的时候插话（「先别打分，数据源换了」） |
| `followup(text)` | 模型这一轮说完之后，当成新的一轮 | 追加要求（「把结论写进证据卡片」） |
| `abort()` | 立刻 | Ctrl-C 或看板上的停止按钮；已写下的记录一条不删 |

`abort` 之后 run 以 `aborted` 收尾，manifest 里记一条降级，会话里留一条 note 说明中断位置——
不是把这段历史抹掉当作没发生。

## 会话树

`session.jsonl` 一行一条，只追加：

```json
{"id":"e0003","parent":"e0002","kind":"tool_call","role":null,"ts":"…","data":{"calls":[…]}}
```

`kind` 五种：`message`（system/user/assistant 的文本）、`tool_call`、`tool_result`、`note`、
`branch`。当前停在哪条 entry 决定活动分支；`branch(from_id)` 把活动分支切到更早那条，
后面接在这条后面——原来的记录一条不动，也不需要复制文件。翻译成模型吃的消息是 `messages()`
的活：一轮里的多个工具调用合成一条 assistant 消息，工具结果一条对一个 `tool_call_id`。

回放：

```bash
uv run sparkjury agent replay runs/agent-20260927-101530-1a2b/session.jsonl
uv run sparkjury agent replay runs/agent-…/session.jsonl --limit 5
```

## 命令

```bash
uv run sparkjury agent endpoints                    # 六个端点的短名
uv run sparkjury agent tools                        # 注册表：模型能调什么
uv run sparkjury agent run --demo                   # 离线跑一通，两秒，不联网
uv run sparkjury agent run -p "跑一遍体检" --model subject
uv run sparkjury agent run -p "…" --model http://127.0.0.1:8001/v1#Qwen/Qwen3-30B-A3B-Instruct-2507-FP8
uv run sparkjury agent replay <session.jsonl>
```

跑完落在 `runs/<run_id>/`：`session.jsonl`（会话树）、`events.jsonl`（事件流）、`usage.jsonl`
（每轮的 token 与延迟）、`manifest.json`（收尾账：`status` / `stopped` / `turns` / `tool_calls` /
`usage` / `skills` / `degradations`，口径跟流水线的 manifest 对齐）。

## 跟 pi 的对照

| pi | 这里 | 为什么这么办 |
|---|---|---|
| `pi-ai` 多 provider 统一层 | `ai.py` | 四个本地端点 + 云端共用一套 `complete()`，换模型只换参数 |
| `pi-agent-core` 的 agent loop | `loop.py` | 同样的一圈，外加 steering / follow-up / abort 三个打断口 |
| JSONL 会话文件 + parent 指针 | `session.py` | 中断后能接着看，分支不用复制文件 |
| 技能描述进提示词、正文按需加载 | `tools.py` 的 `load_skill` | 六个技能全塞提示词太贵，一次 run 用不到那么多 |
| `pi-durable` 的三个存储 + 原子事务 | `runtime.py` | 只留事件流、会话、用量账本三份落盘，够回放和入账 |
| 扩展注册工具（`registerTool`） | `ToolRegistry.register` | 加工具就是加一条 `ToolSpec` |
| 权限靠容器和沙箱，harness 里没有权限系统 | 同上 | 节点上本来就是共享账号，红线靠 AGENTS.md 和只读文件工具兜 |

## 刻意没做的

- **不压缩历史**（pi 的 compaction）：一次 run 十几个回合，先看到真需要再补。
- **不做操作状态机**（operation = run / compaction / navigation）：中断了就中断了，人重新起一次。
  要做 durable restart（跨进程恢复在跑的 run、不重复已完成的副作用）得先有操作状态机。
- **不做流式**：vLLM 的 token 级流式要处理增量与工具调用分片，这一版按回合返回；
  事件流是回合级的，看板上「第 N 轮、调了哪个工具」照样实时。
- **不做跨进程恢复**：`session.jsonl` 可以接着写（`--session`），但停在半路的 run 不会自动续。

## 验证

```bash
uv run pytest tests/test_m13_agent.py -q     # 29 个用例，全离线
uv run sparkjury agent run --demo            # 端到端：读说明书 → 清洗 → 打分 → 出卡片
```

测试盯的都是可观察的结果——发给模型的请求、会话里的记录、事件流、manifest 里的降级项——
不盯内部实现：技能 frontmatter 解析、注册表给的 schema 是不是合法工具定义、提示词里有没有
混进技能正文、工具结果有没有回灌、steering 有没有在下一个请求里出现、中断之后还会不会继续问模型、
manifest 有没有把失败写成成功。

节点上跑真模型（Qwen3-8B @ 8004）：

```bash
uv run --group ops python scripts/node.py check      # 先看节点在忙什么
uv run --group ops python scripts/node.py sync
ssh -p 6030 asus_gx10@61.172.235.130 'cd ~/sparkjury && \
  ~/.local/bin/uv run sparkjury agent run -p "把这批轨迹过一遍体检" --model subject'
```
