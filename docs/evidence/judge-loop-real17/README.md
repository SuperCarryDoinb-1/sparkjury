# 90 条真批跑过判卷环的产物（节点 2026-09-28 01:26 UTC）

这是 SparkJury 在 DGX Spark 节点上对一整批真实 τ²-bench 数据跑完七段流程的原始产物。节点在活动结束后会清空，所以把关键两件拷回仓库留档。

- `manifest.json`：七段各自的数字、三个 vLLM 端点的模型名、三条降级记录（judge_c 401 被摘出面板、Jev 无 key 退本地仲裁、Jev 无 key 退启发式聚类）。
- `card.md`：证据卡片，含 44 个 badcase、14 个簇、每簇的证据片段与排好序的修复建议。

数据来源是 30 个 retail 任务 × 3 个 trial。原始批次里 26 条被 tau2 自己的评测环节丢掉（见 `deploy/README.md` 那一节），补跑后合并成完整 90 条，全部通过预检进裁判。

关键数字（这一份）：90 条全部参评、0 条被预检排除、720 条裁判判定 0 条出错、仲裁 360 维（面板 306 / 本地 54）、84 维被抽审其中 44 维与仲裁结论不一致（全部落在本地仲裁那 54 维里）、44 个 badcase 聚成 14 簇。卡片自己报了与基准的校准：54.4% 一致，裁判判 pass 62.2% 而基准 34.4%——这条差距是这套裁判已知的局限，卡片不藏着。

复现：`uv run sparkjury run --config deploy/run.judgeloop17.toml`（配置在节点副本上，输入是合并后的 `data/simulations/retail_Qwen3-8B_trials3_repaired.json`；那两个文件体积大、不入库）。
