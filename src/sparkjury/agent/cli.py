"""`sparkjury agent` 子命令：跑一次、看工具清单、回放一次会话。

三个命令都刻意做得薄——真正的逻辑在 runtime / loop / tools 里，命令行只负责组装参数和
把结果摆成人看得懂的样子。

    sparkjury agent tools          # 注册表里有什么：六个技能 + 两个本地工具
    sparkjury agent run --demo     # 离线跑一通（脚本模型 + 离线执行器），两秒完事
    sparkjury agent run -p "……" --model subject
    sparkjury agent replay runs/agent-*/session.jsonl
"""

from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from sparkjury.agent.ai import ScriptedProvider, describe_endpoints, resolve_model, text_turn, tool_turn
from sparkjury.agent.loop import STOPPED_ABORTED, LoopResult
from sparkjury.agent.runtime import AgentRuntime
from sparkjury.agent.session import SessionTree
from sparkjury.agent.tools import OfflineSkillExecutor, load_skill_tools

agent_app = typer.Typer(help="Agent harness: 让模型自己读技能、自己调工具。", no_args_is_help=True)
console = Console()


def print_json(payload) -> None:
    """`--json` 的输出必须能被 json.loads 直接吃下去：不折行、不当成 rich 标记。

    技能描述有三百多字符，走 Console.print_json 会被按终端宽度折行，下游解析当场炸。
    """
    console.print(json.dumps(payload, ensure_ascii=False), soft_wrap=True, markup=False, highlight=False)

DEMO_PROMPT = "把这批轨迹过一遍体检，然后告诉我最该先修什么。"
DEMO_DB = "runs/agent-demo/sparkjury.db"

DEMO_REPLIES = {
    "sparkjury-clean": "导入 14 条轨迹，预检挑出 1 条 503（环境问题，不算 Agent 的错）。",
    "sparkjury-score": "三裁判 4 个维度共 52 个判定，一致率 76.9%，3 条分歧已仲裁（本地兜底，降级）。",
    "sparkjury-report": "证据卡片已生成：5 条 badcase 聚成 2 簇，wrong_tool 排第一。",
}


def demo_provider() -> ScriptedProvider:
    """离线脚本：读说明书 → 清洗 → 打分 → 出卡片 → 一句话结论。不联网、不落地命令。"""
    return ScriptedProvider([
        tool_turn(("load_skill", {"name": "sparkjury-clean"}), text="先读一下清洗技能怎么调。"),
        tool_turn(("run_skill", {"name": "sparkjury-clean",
                                 "args": ["--db", DEMO_DB, "--path", "data/samples/tau2_retail_sample.json",
                                          "--source", "tau2"]})),
        tool_turn(("run_skill", {"name": "sparkjury-score", "args": ["--db", DEMO_DB, "--judges", "mock"]})),
        tool_turn(("run_skill", {"name": "sparkjury-report", "args": ["--db", DEMO_DB]})),
        text_turn("轨迹跑完了：14 条里 1 条是环境问题先摘掉；三裁判有 3 条分歧，本地兜底仲裁（降级）。"
                  "最该先修的是工具选择，退款流程里 5 次里错 3 次。"),
    ])


def build_runtime(prompt: str | None, *, model: str | None, max_turns: int, runs_dir: str,
                  workdir: Path, demo: bool, session: Path | None) -> AgentRuntime:
    executor = OfflineSkillExecutor(DEMO_REPLIES) if demo else None
    provider = demo_provider() if demo else _real_provider(model)
    return AgentRuntime(provider, runs_dir=runs_dir, workdir=workdir, executor=executor,
                        max_turns=max_turns, session_path=session)


def _real_provider(model: str | None):
    from sparkjury.agent.ai import OpenAICompatProvider

    spec = resolve_model(model)
    return OpenAICompatProvider(spec)


@agent_app.command("tools")
def tools_cmd(
    skills_root: Path | None = typer.Option(None, "--skills-root", help="技能目录，默认仓库里的 skills/"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """列出注册表：每个工具叫什么、给模型看的一句描述、参数长什么样。"""
    registry, skills = load_skill_tools(skills_root, executor=OfflineSkillExecutor())
    if as_json:
        print_json({"tools": registry.payload(),
                    "skills": [{"name": s.name, "description": s.description, "path": str(s.path)}
                               for s in skills]})
        return
    table = Table(title=f"工具注册表（{len(registry.names())} 个）", show_lines=False)
    table.add_column("工具", style="bold")
    table.add_column("来源")
    table.add_column("给模型看的描述", overflow="fold")
    for tool in registry.specs():
        table.add_row(tool.name, tool.source, tool.description)
    console.print(table)
    console.print(f"[dim]技能目录：{skills_root or '仓库 skills/'}｜"
                  f"system prompt 里只放这些技能的一句话描述，正文由 load_skill 按需取[/dim]")


@agent_app.command("run")
def run_cmd(
    prompt: str | None = typer.Option(None, "--prompt", "-p", help="要它干的事；--demo 时可以不给"),
    model: str = typer.Option("subject", "--model", "-m",
                              help="端点短名（judge-a/judge-b/subject/…）或 http://host:port/v1#模型名"),
    max_turns: int = typer.Option(12, "--max-turns", help="最多几轮 assistant 回合"),
    runs_dir: str = typer.Option("runs", "--runs-dir"),
    workdir: Path = typer.Option(Path("."), "--workdir", help="工作目录，文件工具只能在这里面活动"),
    demo: bool = typer.Option(False, "--demo", help="离线跑一通：脚本模型 + 离线执行器，不联网不落地命令"),
    session: Path | None = typer.Option(None, "--session", help="续用一份已有的 session.jsonl"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """跑一次 agent run。结果落在 runs/<run_id>/，manifest.json 里能看降级项。"""
    if not prompt and not demo:
        console.print("[red]要么给 --prompt，要么加 --demo。[/red]")
        raise typer.Exit(code=2)
    runtime = build_runtime(prompt, model=model, max_turns=max_turns, runs_dir=runs_dir,
                            workdir=workdir, demo=demo, session=session)
    if not as_json:
        spec = runtime.provider.spec
        console.print(f"[bold]agent run[/bold] {runtime.run_id}")
        console.print(f"模型 [cyan]{spec.name}[/cyan] / {spec.model} @ {spec.base_url}"
                      f"｜轮数上限 {max_turns}{'｜离线 demo' if demo else ''}")
    try:
        result = runtime.start(prompt or DEMO_PROMPT)
    except KeyboardInterrupt:  # pragma: no cover - 交互时才发生（工具子进程里按 Ctrl-C）
        console.print("\n[yellow]收到中断：已跑完的记录保留，正在收尾。[/yellow]")
        result = LoopResult(stopped=STOPPED_ABORTED, turns=len(runtime.turns),
                            session_id=runtime.session.session_id)
        runtime.write_manifest(result)
    if as_json:
        print_json(json.loads(runtime.run.manifest_path.read_text(encoding="utf-8")))
    else:
        console.print()
        console.print(runtime.session.transcript(), markup=False)
        console.print()
        table = Table(show_header=False, box=None, padding=(0, 1))
        table.add_row("结束方式", result.stopped)
        table.add_row("轮数 / 工具调用", f"{result.turns} / {result.tool_calls}")
        table.add_row("token", str(result.usage or "端点没回"))
        table.add_row("会话", str(runtime.run.session_path))
        table.add_row("事件流", str(runtime.run.events_path))
        table.add_row("manifest", str(runtime.run.manifest_path))
        console.print(table)
    if not result.ok:
        console.print(f"[red]这次 run 没跑完：{result.error or result.stopped}[/red]")
        raise typer.Exit(code=1)


@agent_app.command("replay")
def replay_cmd(
    session: Path = typer.Argument(..., exists=True, readable=True, help="session.jsonl 路径"),
    limit: int | None = typer.Option(None, "--limit", help="只看最后几条"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """回放一次会话：每条记录一行，带 id 和 parent，能看出分支从哪分出去的。"""
    tree = SessionTree.load(session)
    if as_json:
        print_json([{"id": e.id, "parent": e.parent, "kind": e.kind, "role": e.role, "ts": e.ts, "data": e.data}
                    for e in tree.entries])
        return
    console.print(f"[bold]{session}[/bold]｜{len(tree)} 条记录｜分支 {len(tree.leaves())} 条")
    console.print(tree.transcript(limit), markup=False)


@agent_app.command("endpoints")
def endpoints_cmd(as_json: bool = typer.Option(False, "--json")) -> None:
    """看一眼四个本地端点和两个云端端点的短名。"""
    rows = describe_endpoints()
    if as_json:
        print_json([{"name": n, "model": m, "base_url": u, "note": note} for n, m, u, note in rows])
        return
    table = Table(title="模型端点")
    table.add_column("短名", style="bold")
    table.add_column("模型")
    table.add_column("地址")
    table.add_column("说明", overflow="fold")
    for name, model, url, note in rows:
        table.add_row(name, model, url, note)
    console.print(table)


__all__ = ["agent_app", "demo_provider", "DEMO_PROMPT", "DEMO_REPLIES"]
