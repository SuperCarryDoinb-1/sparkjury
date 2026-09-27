# prompts/arbitrate.md — 分歧仲裁提示词（Jev 通道 / LLM 降级通道）

三裁判分歧时触发。你的任务不是重新评价整条轨迹，而是**在给定的少数派/多数派意见中做选择**。

## Jev 通道（首选）
Jev 输出形态受限：Choice（≤255 选 1）/ Score / Bool。设计对应：
- 记分分歧 →Score：输入多数派分数与少数派分数+理由，输出应采用的分数值
- 归类分歧 →Choice：输入候选类目（taxonomy 的 F01-F99 枚举），输出唯一类目 id
- 真假分歧 →Bool：输入争议点，输出"多数派是否正确"
Jev 决策不可解释（Simon Willison 已指出）→ 仲裁结论必须带 trace 内步骤引用作为**事后证据**，
证据由本地 lead judge 补写，Jev 只负责选择。

## LLM 降级通道（Jev 不可达，degraded 标注 jev_unreachable）
输入：分歧点 + 各票理由 + 相关 span 摘要。按以下顺序裁决：
1. 引用具体 span 证据的一方胜
2. 都有证据 → 与 rubrics checklist 字面更相符的一方胜
3. 仍无法分辨 → 取 calibrate 画像中该维度 reliability 最高的裁判的票
输出必须写明走了哪一条裁决依据。

## 输出（严格 JSON）
{"decision": "分数值|类目id|布尔", "basis": "evidence|checklist|reliability", "reason": "≤80字",
 "degraded": false}
