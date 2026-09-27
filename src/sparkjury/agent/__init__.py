"""Agent harness：让模型自己读技能说明书、自己调工具，把一条流水线跑完。

这一层补的是项目里原来缺的那块：裁判打分是流水线编排的，被评对象跑在 τ²-bench 自己的
框架里，而 SparkJury 自己的六个技能给谁调？给一个会看工具列表、会按需读说明书的模型。

分四层，各管一段，谁也不知道底下是谁：

    cli.py       命令行入口（agent run / tools / replay）
    runtime.py   装配 + 落盘：run 目录、events.jsonl、session.jsonl、usage.jsonl、manifest.json
    loop.py      循环：消息 → 模型 → 工具 → 结果，外加 steering / follow-up / abort
    ai.py        模型：四个本地端点 + 云端，一次调用拿到文本和工具调用
    tools.py     工具：六个技能按需加载与执行 + 两个只读文件工具
    session.py   会话树：只追加的 JSONL，带 parent 指针，可分支、可回放

参照的是 pi（earendil-works/pi）的 harness 分层，但只做到最小闭环：不压缩历史、不做操作
状态机、不跨进程恢复在跑的 run。要的就是「模型真的调起了技能，全过程看得见、能中断、能回放」。
"""

from sparkjury.agent.ai import (
    NODE_ENDPOINTS,
    OpenAICompatProvider,
    Provider,
    ScriptedProvider,
    ToolCall,
    Turn,
    describe_endpoints,
    resolve_model,
    split_thinking,
    text_turn,
    tool_turn,
)
from sparkjury.agent.loop import (
    STOPPED_ABORTED,
    STOPPED_END_TURN,
    STOPPED_ERROR,
    STOPPED_MAX_TURNS,
    AgentLoop,
    LoopResult,
    default_system_prompt,
)
from sparkjury.agent.runtime import AgentRun, AgentRuntime, new_run_id
from sparkjury.agent.session import Entry, SessionTree
from sparkjury.agent.tools import (
    OfflineSkillExecutor,
    SkillInfo,
    SkillRun,
    SubprocessSkillExecutor,
    ToolRegistry,
    ToolSpec,
    load_skill_tools,
    load_skills,
    parse_skill_md,
)

__all__ = [
    "NODE_ENDPOINTS", "OpenAICompatProvider", "Provider", "ScriptedProvider", "ToolCall", "Turn",
    "describe_endpoints", "resolve_model", "split_thinking", "text_turn", "tool_turn",
    "STOPPED_ABORTED", "STOPPED_END_TURN", "STOPPED_ERROR", "STOPPED_MAX_TURNS",
    "AgentLoop", "LoopResult", "default_system_prompt",
    "AgentRun", "AgentRuntime", "new_run_id",
    "Entry", "SessionTree",
    "OfflineSkillExecutor", "SkillInfo", "SkillRun", "SubprocessSkillExecutor", "ToolRegistry",
    "ToolSpec", "load_skill_tools", "load_skills", "parse_skill_md",
]
