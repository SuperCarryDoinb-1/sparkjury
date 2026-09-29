# 提交清单（截止 2026-09-29）

来源：wiki《作品提交和评分规则》。以链接形式填组委会表单。

## 1. 开源仓库

- [x] 推到 GitHub，公开：https://github.com/VioletScar-Hui/sparkjury （2026-09-29 复核 visibility=PUBLIC，默认分支 main）
- [x] README ≥ 500 字，含作品特点、核心亮点、技术实现、架构设计、优化方案（`README.md`「项目说明」一节约 970 字，全文中文 5400 余字；顶部「提交材料索引」逐项指向）
- [x] 部署说明：本地算力如何部署智能体、如何优化大模型、如何设计 Agent Skills（`README.md`「部署说明」三小节 + `deploy/README.md` + `skills/README.md`）
- [x] 技术栈说明：NVIDIA SDK（DGX Spark、vLLM、NeMo Agent Toolkit、NVML）、NVIDIA 模型（Nemotron-3.5-Lightning）与 StepFun 模型（step-3.7-flash）（`README.md`「技术栈说明」一表）
- [x] Skill markdown 文件（`skills/*/SKILL.md` 六份 + `skill-card.md`，`README.md`「Skill 说明」一节；`scripts/validate_skills.py` 6/6 通过）
- [x] 删除 `runs/`、`.env`、任何密钥；`git log` 干净（2026-09-29 复核：`git ls-files` 无 runs / .env / node.env / *.db / logs；跟踪文件与提交历史扫描无 API key；`.gitignore` 与 pre-commit 钩子持续拦截）
- [x] 补 Screenshots 段的图（`docs/img/` 六张：看板首页、深色、真裁判卡片、模型裁判、失败聚类、回归对比，2026-09-29 按新看板重拍）

## 2. 演示视频

- [ ] 按 `docs/VIDEO_SCRIPT.md` 录制，3 到 4 分钟（千富；脚本里的界面名已按 9 月 29 日的九页看板更新）
- [ ] 上传 B 站，公开，链接填表单（剑乔）

## 3. 十日谈征文

- [x] `docs/ESSAY_十日谈.md` 补 27 到 29 日实际内容（2026-09-29）
- [x] 发 CSDN，链接填表单：https://blog.csdn.net/qq_47798402/article/details/166849086

## 4. 团队资料

- [ ] 团队合影（万凌统筹）

## 5. 表单里要填的链接（汇总）

| 项 | 链接 | 状态 |
|---|---|---|
| 开源仓库 | https://github.com/VioletScar-Hui/sparkjury | 就绪 |
| 演示视频 | B 站链接 | 待录制上传 |
| 十日谈征文 | https://blog.csdn.net/qq_47798402/article/details/166849086 | 就绪 |
| 团队合影 | 图片 | 待拍 |

## 评分对照自查

| 维度 | 权重 | 我们的证据 |
|---|---|---|
| 实用性、落地价值、创新 | 25% | Problem 段的调研数据；本地 + 开箱即用 + 优先级；千富的真实场景故事 |
| 智能体与模型优化深度 | 25% | 三家族裁判团、Jev 仲裁、5% 审计、三层降级、端云成本模型 |
| 完整性 | 20% | 319 passed、3 skipped（2026-09-29 实测）、一条命令跑通、看板、文档 17 节 |
| 平台适配 | 15% | DGX 显存分配与 MoE 选型、vLLM、NeMo Agent Toolkit 评估器、StepFun 裁判 |
| 演示效果 | 10% | Cockpit 同屏 Agent 与 DGX；断网备选 |
| 征文 | 5% | 十日谈 |
