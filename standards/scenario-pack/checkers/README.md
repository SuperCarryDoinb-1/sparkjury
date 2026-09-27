# checkers/ — 终态判定器（human-defined，红线）

## 契约

```
checker:
  id: string            # taxonomy 之外的独立命名空间，如 retail.db_final_state
  task_id_pattern: string
  input:
    task:  Trace.task        # 任务定义（instruction/available_tools）
    env_final_state: object  # 运行方提供的终态观测（如 tau2 DB diff）
    spans: Trace.spans       # 供规则型 checker 参考
  output:
    passed: boolean
    evidence: string         # 哪个事实与期望不符，必须可引用
```

## 为什么红线

**checker 只能由人定义，禁止 agent 自动生成。** 评测标准若可由系统自产，回归对比失去可比性，
产品退化为自评自。clarify skill 可以把自然语言编译成 checker 提案，但必须人批准后才进 pack。

## v0.1 内容

- `tau2_db_final_state`（引用实现）：tau2-bench 自带 DB 终态比对，我们直接复用其结果作为
  `Trace.outcome`，checker 只做透传 + 证据摘录。禁止另写一套等价逻辑——重复实现必然漂移。
- 用户自定义 checker：按上面的契约提供 YAML/JSON，注册进本目录，`expected_outcome_ref` 引用其 id。

## TODO

- 营销域（千富）checker 待真实 trace 到达后定义（owner: 万凌）。
