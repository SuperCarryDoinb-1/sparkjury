## 改了什么

（一句话说清这次改动解决的问题。别写「优化」「修复」这种看不出所以然的。）

## 怎么验的

- [ ] `uv run pytest` 全绿，贴用例数
- [ ] 改到的那条链路真跑了一遍，不是只跑单测

## 节点部署验证

> 这是本仓库的硬性要求：每一次主动提 PR 都要在节点上部署一次，结果贴在这里。
> 部署没通过不算失败，隐瞒才算——哪一步没过就原样写清命令和报错。
> 这一次没法部署（节点上有 tau2 长任务在跑、没抢到 GPU、不想覆盖队友的工作树）也照实写，
> 说明原因和打算什么时候补，别空着。

- 分支 / commit：
- `uv run --group ops python scripts/node.py check` 输出：

```
（贴原样输出，尤其是最后那句「需要部署 / 需要起服务 / 不需要部署」）
```

- 冒烟三样（`~/.local/bin/uv run pytest`、`bash deploy/dgx/status.sh`、一条真实 run 或 `--demo`）：
- 降级项：judge_c（StepFun 没配 key 会退化成 mock）、jev（TypeSafe 没配 key 会退化成本地仲裁）

## 影响面

- [ ] 动了共享契约（trace / verdict / manifest 字段、events 事件）
- [ ] 动了部署脚本，或会影响节点上正在跑的长任务
- [ ] 需要队友配合（重装依赖、改 `.env`、重跑基线、重新 sync）
