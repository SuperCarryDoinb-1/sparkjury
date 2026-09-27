# 消融：云端那两条依赖值多少

节点上 StepFun（第三裁判家族）和 TypeSafe（Jev 仲裁）这两个 key 都可能是空的。一空，链路就往降级方向走：judge_c 退化成 mock，分歧仲裁退化成拿本地 Judge A 顶上。

这样跑出来的一轮，不该被当成「完整配置的结果」打折看，它本身就是一个现成的对照组。但它同时动了两个变量，所以不能直接拿来回答「Jev 到底有没有用」——那正是这篇要拆开的东西。

## 两个变量，四条臂

| 臂 | judge_c | jev | 它在回答什么 |
|---|---|---|---|
| A 对照组 | mock | off | 云端全关时，这套链路还给不给得出结论 |
| B | mock | auto | judge_c 固定为 mock，单独看外部仲裁 |
| C | 真 step-3.7-flash | off | 单独看第三裁判家族 |
| D | 真 | auto | 完整配置 |

比 Jev 要看 C 对 D（judge_c 固定为真，只差仲裁这一项）。比第三裁判家族要看 A 对 C（仲裁固定关着）。A 对 D 是两个一起上的总效果。B 是用来在 judge_c 固定为 mock 的前提下单独看仲裁的。

## 消融不用重跑 τ²-bench

基线那九十次跑一次要七个小时，但消融不是再跑一次它。四条臂吃的是同一批 trace，也就是基线产出的那份结果 JSON，只是重新过一遍裁判与仲裁那几段，每条臂几分钟。所以基线一跑完，四条臂半小时内都能出来，两两之间唯一的差别就是你改的那两个变量。跑长任务的时间不用重复花。

```bash
# 生成四条臂的配置（会先用项目自己的配置模型校验一遍，并打印每条臂解析出来的裁判）
uv run python scripts/ablation.py configs --traces data/simulations/<基线结果>.json --check
uv run python scripts/ablation.py configs --traces data/simulations/<基线结果>.json

uv run sparkjury run --config deploy/ablation/arm-a.toml    # b/c/d 同理
uv run python scripts/ablation.py report
```

## 纪律：每条臂跑完先核对它是不是那条臂

这条比脚本本身重要。配置里 `judge_healthcheck` 默认开着，评测前会探一次每个裁判，探不通时按面板里还剩几个真裁判分两条路：还剩两个真裁判就把它从面板里摘掉（`degradations` 写 `dropped from panel`，效果是三家变两家），剩不到两个才换成 mock（写 `mock judge`）。key 没配、网络不通、端点没起，都会触发。

于是会出现这种情况：你名义上跑的是「完整配置」那条臂，拿到的其实是少一家或掺了 mock 的数据，而除了 manifest 之外没有任何地方会告诉你。所以每条臂跑完先看两处：

- `runs/ablation-<臂>/manifest.json` 的 `degradations`，空数组才说明这条臂真的按配置跑完了
- 同一份 manifest 的 `models`：`judges` 里还剩下几家真裁判（值写成 `mock-fallback-for-<模型>` 的是被换掉的，压根不在表里的是被摘掉的）、`arbiter.jev` 是不是 null、`arbiter.audit` 是不是 null

对不上就把这条臂按它实际的身份记录，别把不同身份的结论放进同一张表。

顺带一个跑臂时的坑：想跑「judge_c 固定为 mock」那条臂，必须在 TOML 里把 judge_c 写成 `kind = "mock"`，不能指望健康检查替你换——现在它会先把 judge_c 摘掉。没有 StepFun 的 key，judge_c 为真的那两条臂根本跑不起来。

## 两个 gold 后门，只堵上了一个

基准算出来的 gold 结果有两个泄漏点。原先这一节只记了第二个。

**prompt 里**：`build_messages` 曾把 `Gold outcome: SUCCESS (reward=1.0)` 拼进每一个裁判的 user prompt（不只是 mock），outcome 的 rubric 原文还写着「金标是权威，你的任务是确认它」。于是 outcome 这一维变成复述金标：2026-09-26 在 13 条真实样例上实测，三家 13/13 完全一致，取值只有 4 和 0 两种，judge_a 与 judge_b 逐条分数 42/52 相同、label 52/52 相同。2026-09-27 修复：`include_gold` 默认关，金标只留给报告算「裁判 vs 金标」的一致率。**修复之前，不含 mock 的臂（C、D）在 outcome 维度上同样失真**——这一节原来写的「只有含 mock 的臂才失真」是错的。

**MockJudge 内部**：启发式裁判的 outcome 判定直接读 trace 的 gold（类在 `src/sparkjury/judges/client.py:126`，判据在 `src/sparkjury/judges/heuristics.py:148`），这跟 prompt 无关，**仍然存在**。gold 说成功就给 4 分 pass，说不成功就给 0 分 fail。

所以看结果之前要记住三条：

含 mock 的臂（A、B）在「与 gold 一致率」上不能和不含 mock 的臂（C、D）直接横向比，原因现在是 MockJudge 内部那个后门。要比 outcome，就只在不含 mock 的臂之间比，也就是 C 对 D，而那恰好是回答「Jev 有没有用」的那个对比。另外三个维度（tool_use、efficiency、safety）的判定不读 gold，分数可以直接横向比。

pass^1 与 pass^3 不受影响，它们是从 gold 直接算的（`src/sparkjury/regress/passk.py:14`），跟裁判怎么判无关。README 里那两个数字与 mock 无关，可以照用。

还有个反直觉的地方：outcome 维度要求各家 label 完全一致才算一致（`src/sparkjury/judges/panel.py:97`），所以 mock 这个「知道答案的裁判」不只是多投一票，它会把真裁判集体判错的情况顶出来，从而改变进入仲裁的频率；而仲裁在降级臂里是面板里的第一个真裁判做的。看「降级判定条数」这个指标时要记得这一层。

## 真批上的判准校准（2026-09-27 实测）

拿真批 90 条里可评分的 42 条只跑 outcome 一维，三种判准各跑一遍，都与同一份基准对照（judge_a，本地 Qwen3-30B）：

| 跑法 | 与基准一致率 | 基准 fail 被判 pass | 基准 pass 被判 fail | 判 pass 占比 | 均分 |
|---|---|---|---|---|---|
| 原始 rubric | 50.0% | 21 条 | 0 条 | 88% | 3.71 |
| 逐条台账 rubric | 47.6% | 21 条 | 1 条 | 86% | 3.45 |
| 台账 rubric + 任务原始要求进 prompt | 52.4% | 17 条 | 3 条 | 71% | 3.19 |

n=42，一致率的标准误约 7.7 个百分点，三次的差别都在噪声里：**不能宣称哪一版判准更准**。收紧判准确实把 4 条误判纠了过来（task 6/12/18/23，都能逐条说清是哪一步没做成），代价是新添 3 条误杀（task 11/26 等，参考解能做到的终态裁判在对话里认不出来）。能确认的是两件事。

一是裁判系统性地偏松：原始判准下判 pass 约 88%，基准只有 38%；42 条里漏判为 0，误判 21 条，方向单一，不是随机误差。

二是这个偏差不是改 prompt 能补的，原因在结构上。τ²-bench 的成败是一次数据库终态比对，参照的是参考解自己那套写操作——换哪个商品、退哪几个条目、用哪个支付方式。对话里不含参照解，用户模拟器还会把任务原始要求说窄、说糊（任务写「如果没有 clicky+RGB+full-size 的键盘就只换恒温器」，对话里说成一句既能读成「那就换个没有背光的」也能读成「只换恒温器」的话）。裁判读到的是「看起来办成了」，基准要的是「终态和参考解一致」，两者本来就不是同一件事。把 21 条误判按 trace 里记录的参考写操作和模型实际写操作逐条比过：15 条是动作类型对、参数不同，5 条是做多做少，1 条是参考解什么写操作都不做。

结论写进产品输出而不是只写在文档里：证据卡片现在自带这一行校准（`Outcome vs benchmark`，同时给裁判通过率与基准通过率），跑批的阶段摘要也带 `gold_agreement_rate` / `judge_pass_rate` / `gold_pass_rate`。判卷环的结论要跟被测 Agent 的成绩单一起看，别把「裁判说 pass」当成「基准说 pass」。

## 比什么

`scripts/ablation.py report` 把每条臂的这几样并列出来：

判定来源分布（panel / jev / local / panel_fallback）和降级判定的条数，直接告诉你这条臂里有多少判断是兜底出来的。

outcome 维度与 gold 的一致率。τ²-bench 自带数据库终态比对，任务成败是客观的，这是目前唯一能检验「裁判判得准不准」的外部尺子。

badcase 聚类的簇数与优先级。同一批 trace 换个配置，聚类还给不给出一样的行动建议。

两条臂之间翻案的判定，也就是同一条 trace 的同一个维度两次给了不同结论。这是「换个配置结论就变」的直接证据，也是判断降级链路能不能信的关键。

## 只能拿到 A 臂的话

现成的那一轮（14 条样本，`runs/node-real-samples`）就是 A 臂，不需要任何 key。但消融实验里只有对照组是不成立的：对照组要有对手才叫对照。四条臂里「真」的那部分分别需要：

- B 需要 TypeSafe 的 key（能开 Jev）
- C 需要 StepFun 的 key（能开第三裁判家族）
- D 两个都要

所以至少得拿到其中一个 key，这个消融才做得起来。两个都拿不到的话，A 臂只能当「云端全挂时链路仍然给出结论并如实标注降级」的鲁棒性演示，不能说成消融——这两件事的说服力差得很远，写进材料时要分清。

## 判读口径先定下来

结果出来之后再定口径，容易各说各话，所以先写在这里：

四条臂与 gold 的一致率都差不多、聚类建议也一致，说明降级链路可信，「没 key 也能出结论」本身就是一个能拿出去讲的结论。只有 A 明显更差，说明云端依赖是真的影响判断质量，降级只保证不断链、不保证结论可比。A 与 D 不同而 C 与 D 接近，影响主要来自第三裁判家族的缺失；A 与 D 不同而 B 与 D 接近，则主要来自仲裁。
