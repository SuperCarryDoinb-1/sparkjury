#!/usr/bin/env python3
"""消融实验工具：把「云端那两条依赖」的四条对照臂固化成可复现的配置，并在跑完后对比。

背景。这台节点上 StepFun 和 TypeSafe 两个 key 都可能是空的，于是 judge_c 退化成 mock、
分歧仲裁退化成本地 Judge A。这样跑出来的一轮不该当成「完整配置的结果」扔掉——它本身就是一个
现成的对照组，只是它同时动了两个变量，所以不能拿它回答「Jev 到底有没有用」：

    judge_c  mock  vs  真 step-3.7-flash      第三裁判家族在不在
    jev      off   vs  auto（有 key 才是真）   分歧的外部仲裁在不在

两个变量各两种状态，就是这四条臂：

    A 对照组     judge_c=mock  jev=off    云端全关，等于现在节点上这一轮
    B 只开仲裁   judge_c=mock  jev=auto
    C 只开第三裁判 judge_c=真    jev=off
    D 完整配置   judge_c=真      jev=auto

比 Jev 要看 C vs D（judge_c 固定为真），比第三裁判要看 A vs C（仲裁固定关着），A vs D 是两个
一起上的总效果，B 用来在 judge_c 固定为 mock 的情况下单独看仲裁。

省事的地方在于：消融不需要重跑 tau2-bench。基线 trace 只跑一次，四条臂都是拿同一批 trace 重跑
裁判与仲裁那几段，每条几分钟。所以基线一跑完，半小时内四条臂都能出来。

    uv run python scripts/ablation.py configs --traces data/simulations/<基线结果>.json
    uv run python scripts/ablation.py configs --traces <...> --check     # 只校验不写盘
    uv run python scripts/ablation.py report                             # 跑完之后的对比

一条纪律，比脚本本身重要：每条臂跑完先看它自己的 manifest.json 里的 degradations 和 models，
确认它真的是那条臂。配置里 judge_healthcheck 默认开着，跑之前会探一次模型，探不通就把这条臂
悄悄换成 mock——不核对的话，你会在「完整配置」的名义下拿到一条降级数据，整个消融表就是假的。
"""

from __future__ import annotations

import argparse
import glob
import json
import sqlite3
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE_CONFIG = ROOT / "deploy" / "run.node.toml"
DEFAULT_OUT = ROOT / "deploy" / "ablation"

# arm -> (judge_c 用不用真模型, jev 开不开)
ARMS: dict[str, tuple[str, str]] = {
    "A": ("mock", "off"),
    "B": ("mock", "auto"),
    "C": ("real", "off"),
    "D": ("real", "auto"),
}
ARM_NOTE = {
    "A": "对照组：云端全关（judge_c=mock, jev=off），等于节点上那一轮",
    "B": "只开仲裁（judge_c=mock, jev=auto）",
    "C": "只开第三裁判（judge_c=真 step-3.7-flash, jev=off）",
    "D": "完整配置（judge_c=真, jev=auto）",
}

MOCK_BLOCK = (
    '[[panel.judges]]\nname = "judge_c"\nkind = "mock"\nmodel = "mock-step"\njitter = 0.15\n\n'
)


# --------------------------------------------------------------------------- configs


def _rewrite_judge_c(text: str, kind: str) -> str:
    """把 judge_c 那一块换成 mock，或者原样保留真模型那一块。"""
    start = text.index('[[panel.judges]]\nname = "judge_c"')
    end = text.index("[arbiter]", start)
    block = text[start:end]
    if kind == "mock":
        return text[:start] + MOCK_BLOCK + text[end:]
    # 真模型那条：必须确认基线配置里本来就是 openai，否则「真」这条臂会悄悄还是 mock
    if 'kind = "openai"' not in block or "api_key_env" not in block:
        raise SystemExit("基线配置里的 judge_c 不是 openai+api_key_env，没法生成「真模型」那条臂")
    return text


def _rewrite_inputs(text: str, traces: str) -> str:
    start = text.index("[[inputs]]")
    end = text.index("[panel]", start)
    return text[:start] + f'[[inputs]]\npath = "{traces}"\nsource = "tau2"\n' + text[end:]


def _rewrite_identity(text: str, arm: str) -> str:
    rid = f"ablation-{arm.lower()}"
    out = []
    for line in text.splitlines():
        if line.startswith("run_id = "):
            line = f'run_id = "{rid}"'
        elif line.startswith("db = "):
            line = f'db = "runs/{rid}/sparkjury.db"'
        elif line.startswith("title = "):
            line = f'title = "{ARM_NOTE[arm]}"'
        out.append(line)
    return "\n".join(out) + "\n"


def build_arm_text(arm: str, traces: str) -> str:
    judge_c, jev = ARMS[arm]
    text = BASE_CONFIG.read_text(encoding="utf-8")
    text = _rewrite_judge_c(text, judge_c)
    text = _rewrite_inputs(text, traces)
    text = _rewrite_identity(text, arm)
    # [arbiter] 段里的 jev：
    head, _, tail = text.partition("[arbiter]")
    tail = tail.replace('jev = "auto"', f'jev = "{jev}"', 1)
    return head + "[arbiter]" + tail


def validate(arm: str, text: str) -> dict:
    """用项目自己的配置模型过一遍，并把解析出来的裁判列出来——臂有没有真的不同，看这里。"""
    from sparkjury.harness.config import RunConfig
    from sparkjury.judges.panel import build_judges

    cfg = RunConfig.model_validate(tomllib.loads(text))
    judges = build_judges(cfg.panel) if cfg.panel else []
    return {
        "arm": arm,
        "note": ARM_NOTE[arm],
        "run_id": cfg.run_id,
        "db": cfg.db,
        "inputs": [i.path for i in cfg.inputs],
        "jev": cfg.arbiter.jev,
        "judges": [(j.name, j.model) for j in judges],
        "kinds": [type(j).__name__ for j in judges],
    }


def cmd_configs(args: argparse.Namespace) -> int:
    traces = args.traces
    if not args.check and not (ROOT / traces).exists() and not Path(traces).is_absolute():
        print(f"注意：{traces} 现在不存在，配置里会照写这个路径", file=sys.stderr)

    out_dir = Path(args.out) if args.out else DEFAULT_OUT
    if not args.check:
        out_dir.mkdir(parents=True, exist_ok=True)

    for arm in sorted(ARMS):
        text = build_arm_text(arm, traces)
        info = validate(arm, text)
        print(f"臂 {arm}  {info['note']}")
        print(f"    run_id={info['run_id']}  jev={info['jev']}")
        print(f"    裁判: " + ", ".join(f"{n}({m})" for n, m in info["judges"]))
        if args.check:
            continue
        path = out_dir / f"arm-{arm.lower()}.toml"
        path.write_text(text, encoding="utf-8")
        print(f"    写出 {path.relative_to(ROOT)}")

    if not args.check:
        print("\n跑法（每条臂几分钟，不用重跑 tau2）：")
        for arm in sorted(ARMS):
            print(f"    uv run sparkjury run --config deploy/ablation/arm-{arm.lower()}.toml")
        print("\n跑完核对 manifest.json 的 degradations/models，再跑：")
        print("    uv run python scripts/ablation.py report")
    return 0


# --------------------------------------------------------------------------- report


def _q(con: sqlite3.Connection, sql: str) -> list[tuple]:
    return con.execute(sql).fetchall()


def arm_stats(db: Path) -> dict:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        by_source = dict(_q(con, "SELECT source, COUNT(*) FROM arbitration GROUP BY source"))
        n_arb, n_deg = _q(con, "SELECT COUNT(*), COALESCE(SUM(degraded), 0) FROM arbitration")[0]
        rows = _q(
            con,
            """SELECT a.final_label, t.success FROM arbitration a
               JOIN traces t ON t.trace_id = a.trace_id
               WHERE a.dimension = 'outcome' AND a.final_label IS NOT NULL AND t.success IS NOT NULL""",
        )
        match = sum(
            1
            for label, gold in rows
            if (label == "pass" and gold == 1) or (label == "fail" and gold == 0)
        )
        clusters = _q(con, "SELECT rank, label, size, priority FROM clusters ORDER BY rank")
        decisions = {
            (tid, dim): (label, score)
            for tid, dim, label, score in _q(
                con, "SELECT trace_id, dimension, final_label, final_score FROM arbitration"
            )
        }
        n_traces = _q(con, "SELECT COUNT(*) FROM traces")[0][0]
        n_clusters = _q(con, "SELECT COUNT(*) FROM clusters")[0][0]
    finally:
        con.close()
    # 身份以 manifest 为准，不以配置为准：healthcheck 会把探不通的裁判换成 mock
    identity = {}
    man = db.parent / "manifest.json"
    if man.exists():
        m = json.loads(man.read_text(encoding="utf-8"))
        models = m.get("models") or {}
        identity = {
            "status": m.get("status"),
            "judges": models.get("judges") or {},
            "jev": (models.get("arbiter") or {}).get("jev"),
            "degradations": m.get("degradations") or [],
        }
    return {
        "db": str(db),
        "identity": identity,
        "n_traces": n_traces,
        "n_decisions": n_arb,
        "degraded": n_deg,
        "by_source": by_source,
        "gold_n": len(rows),
        "gold_match": match,
        "clusters": clusters,
        "n_clusters": n_clusters,
        "decisions": decisions,
    }


def cmd_report(args: argparse.Namespace) -> int:
    dbs = sorted(Path(p) for p in glob.glob(str(ROOT / args.db_glob)))
    if not dbs:
        print(f"没有找到 {args.db_glob} —— 四条臂还没跑，或者跑在别的地方了")
        return 1

    stats = {p.parent.name: arm_stats(p) for p in dbs}

    print("## 每条臂的概况\n")
    print("| 臂 | trace | 判定 | 降级判定 | 判定来源 | outcome 对得上 gold | 簇 |")
    print("|---|---|---|---|---|---|---|")
    for name, s in stats.items():
        src = ", ".join(f"{k}={v}" for k, v in sorted(s["by_source"].items()))
        gold = f"{s['gold_match']}/{s['gold_n']}" if s["gold_n"] else "—"
        pct = f" ({s['gold_match'] / s['gold_n']:.0%})" if s["gold_n"] else ""
        print(
            f"| {name} | {s['n_traces']} | {s['n_decisions']} | {s['degraded']} | {src} | {gold}{pct} | {s['n_clusters']} |"
        )

    if args.json:
        print("\n" + json.dumps({k: {kk: vv for kk, vv in v.items() if kk != "decisions"} for k, v in stats.items()}, ensure_ascii=False, indent=2))

    print("\n注意：outcome 与 gold 的一致率对含 mock 裁判的臂有 gold 后门——mock 的 outcome")
    print("判定直接读 gold（heuristics.py 的 _outcome），所以这条指标只能在不含 mock 的臂之间横比；")
    print("pass^k 是从 gold 直接算的，不受影响。另见 docs/ABLATION.md。")

    print("\n## 每条臂的真实身份（以 manifest 为准，不是以配置为准）")
    print("healthcheck 会把探不通的裁判换成 mock，所以配置说是真、实际可能是 mock；")
    print("下面这节对不上的话，这条臂就按它实际的身份记录，别和别的臂放进同一张表。\n")
    for name, s in stats.items():
        ident = s.get("identity") or {}
        if not ident:
            print(f"- {name}: 没有 manifest.json，身份不明")
            continue
        judges = ", ".join(f"{k}={v}" for k, v in ident["judges"].items()) or "—"
        degs = ident["degradations"]
        deg_txt = "无降级" if not degs else f"降级 {len(degs)} 处"
        print(f"- {name}: status={ident['status']} · jev={ident['jev']} · {judges} · {deg_txt}")
        for d in degs:
            print(f"    {d.get('stage')}/{d.get('component')}: {d.get('reason')} → {d.get('fallback')}")

    print("\n## 聚类")
    for name, s in stats.items():
        if not s["clusters"]:
            print(f"- {name}: 没有簇")
            continue
        items = "; ".join(f"#{r} {lab}({size}, 优先级 {prio})" for r, lab, size, prio in s["clusters"])
        print(f"- {name}: {items}")

    names = list(stats)
    if len(names) > 1:
        print("\n## 两条臂之间翻案的判定")
        print("（同一条 trace 的同一个维度，两次给了不同的 label 或分数；这是「换个配置结论就变」的直接证据）")
        for i, a in enumerate(names):
            for b in names[i + 1 :]:
                da, db_ = stats[a]["decisions"], stats[b]["decisions"]
                shared = set(da) & set(db_)
                flips = sorted(k for k in shared if da[k] != db_[k])
                print(f"- {a} vs {b}: 共同判定 {len(shared)} 条，翻案 {len(flips)} 条")
                for tid, dim in flips[: args.max_flips]:
                    print(f"    {tid} / {dim}: {a}={da[(tid, dim)]} → {b}={db_[(tid, dim)]}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="SparkJury 云端依赖消融工具")
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("configs", help="生成四条臂的 run 配置")
    c.add_argument("--traces", required=True, help="基线结果 JSON（四条臂共用同一批 trace）")
    c.add_argument("--out", default=None, help=f"输出目录，默认 {DEFAULT_OUT.relative_to(ROOT)}")
    c.add_argument("--check", action="store_true", help="只校验并打印，不写盘")
    c.set_defaults(func=cmd_configs)

    r = sub.add_parser("report", help="对比跑完的臂")
    r.add_argument("--db-glob", default="runs/ablation-*/sparkjury.db")
    r.add_argument("--json", action="store_true", help="附带 JSON 汇总")
    r.add_argument("--max-flips", type=int, default=20, help="每条臂对最多列几条翻案")
    r.set_defaults(func=cmd_report)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
