# scenario-pack/ — 评测标准的唯一事实源（数据，不是代码）

pack 把「随场景变化的判分逻辑」与「不变的内核」解耦：skill 是引擎，pack 是策略。
换场景 = 换 pack，不动 skill 引擎；skill 之间流水线固定，动态只发生在 skill 内部。

```
scenario-pack/
├── pack.manifest.json   DRAFT/FROZEN 状态 + frozen_hash（tools/pack_freeze.py 写）
├── rubrics.yaml         四维评分细则（outcome/process/efficiency/risk）
├── taxonomy.yaml        失败分类体系 F01-F99（cluster/prioritize/report 消费）
├── thresholds.yaml      阈值权重（重复次数/仲裁规则/聚类参数/优先级公式/回归门）
├── judges.yaml          裁判配置（三裁判跨族/embedding/Jev 仲裁/降级路径）
├── checkers/            终态判定器（人定义，红线：禁止 agent 自动生成）
└── prompts/             各裁判通道的提示词（受 pack 版本冻结保护）
```

## 状态机

```
DRAFT ──govern freeze──▶ FROZEN ──用户显式指令──▶ v(N+1) DRAFT ──▶ FROZEN
```

- DRAFT：唯一入口是 clarify skill（自然语言 → pack diff 提案 → 人确认）。
- FROZEN：所有 skill 只读。每次 run 的 manifest 钉 `pack_hash`。
- 回归铁律：前后两轮对比必须同一 `pack_hash`；换了 pack = 新一轮评测，不是回归。
- 解冻理由必须进 event ledger（decision.thaw_pack），不留无据解冻。
