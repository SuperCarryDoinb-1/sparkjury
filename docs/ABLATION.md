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

这条比脚本本身重要。配置里 `judge_healthcheck` 默认开着，评测前会探一次每个裁判，探不通就把这条臂悄悄换成 mock。key 没配、网络不通、端点没起，都会触发这个替换。

于是会出现这种情况：你名义上跑的是「完整配置」那条臂，拿到的其实是降级数据，而除了 manifest 之外没有任何地方会告诉你。所以每条臂跑完先看两处：

- `runs/ablation-<臂>/manifest.json` 的 `degradations`，空数组才说明这条臂真的按配置跑完了
- 同一份 manifest 的 `models`，看 judge_c 是不是 `mock-fallback-for-step-3.7-flash`、`arbiter.jev` 是不是 null

对不上就把这条臂按它实际的身份记录，别把不同身份的结论放进同一张表。

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
