"""守卫消融脚本：四条臂必须只在该变的地方变，而且不能悄悄生成一条假的「真裁判」臂。

背景见 docs/ABLATION.md。这里盯三件事：臂之间的差异是否被限制在设计好的两个变量上、
每条臂的配置能不能被项目自己的配置模型接受、以及基线配置已经被改成 mock 时会不会
还傻乎乎地生成一条名义上是「真模型」的臂。
"""

import importlib.util
import tomllib
from pathlib import Path

import pytest

from sparkjury.harness.config import RunConfig
from sparkjury.judges.panel import build_judges

ROOT = Path(__file__).resolve().parents[1]


def _load_ablation():
    spec = importlib.util.spec_from_file_location("sparkjury_ablation", ROOT / "scripts" / "ablation.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


AB = _load_ablation()
TRACES = "data/simulations/baseline.json"


def _arm_cfg(arm: str) -> RunConfig:
    return RunConfig.model_validate(tomllib.loads(AB.build_arm_text(arm, TRACES)))


def _judge_c(cfg: RunConfig):
    return next(j for j in cfg.panel.judges if j.name == "judge_c")


def test_arms_differ_only_where_intended():
    cfgs = {arm: _arm_cfg(arm) for arm in AB.ARMS}

    assert [_judge_c(cfgs[a]).kind for a in "AB"] == ["mock", "mock"]
    assert [_judge_c(cfgs[a]).kind for a in "CD"] == ["openai", "openai"]
    assert [cfgs[a].arbiter.jev for a in "AC"] == ["off", "off"]
    assert [cfgs[a].arbiter.jev for a in "BD"] == ["auto", "auto"]

    # judge_a 与 judge_b 在四条臂里必须完全一致，否则消融里混进了别的变量
    for arm in "BCD":
        assert cfgs[arm].panel.judges[:2] == cfgs["A"].panel.judges[:2]

    # 四条臂各自独立的 run_id 与 db，吃的却是同一批 trace
    assert len({c.run_id for c in cfgs.values()}) == 4
    assert len({c.db for c in cfgs.values()}) == 4
    assert {c.inputs[0].path for c in cfgs.values()} == {TRACES}


def test_every_arm_builds_a_three_judge_panel():
    for arm in AB.ARMS:
        judges = build_judges(_arm_cfg(arm).panel)
        assert [j.name for j in judges] == ["judge_a", "judge_b", "judge_c"]


def test_real_arm_refuses_to_be_generated_from_a_mocked_base():
    """基线配置的 judge_c 已经是 mock 时，不许再生成「真模型」那条臂。

    否则 C、D 会静默等于 A，而消融表上还写着「完整配置」——这比脚本报错难查得多。
    """
    mocked = AB._rewrite_judge_c(AB.BASE_CONFIG.read_text(encoding="utf-8"), "mock")
    with pytest.raises(SystemExit):
        AB._rewrite_judge_c(mocked, "real")
