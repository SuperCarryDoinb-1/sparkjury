# 分工与架构优化（五轮迭代）

依据 `anthropic-mind` v1.22.0 知识库（2026-09-27 快照，796 片，521 片 active）里的 active 切片做的推演。正文每一条结论后面跟一个方括号标记，指向文末附录 A 的逐字引文与来源。状态为 needs-review 的切片不能作为结论依据，只放在附录 B 备查。文里所有仓库事实的核查时间是 2026 年 9 月 27 日 03:40，对应 main 的 `6a41cdf`；引用行号按这一版算，仓库再往前走就要重核一遍——第 4 轮里就有一条结论是这么过期的，记在那一段里。

五轮的顺序是：先诊断，再换轴，再改架构，然后自己反驳并请一个全新上下文的评审者独立审一遍，最后按审查结果收口。第四轮推翻了两条、删掉了一条，都记在里面。

## 第 0 轮 · 结论

现在的分工按「谁写哪个模块」来分。而 Anthropic 自己的判断是，agentic coding 铺开之后代码不再是瓶颈，卡住的是 build 两侧那几道还以人的速度跑的环节：计划、评审与测试、部署 [A1]。

所以优化的方向不是重新分配模块，而是换一个分法：谁拥有哪个产出口，这个产出口的通过条件是什么。交付物是产出口，不是代码量；通过条件是能判定的证书，不是「验收人：待确认」。

但第四轮的独立评审把方案往回拽了一把：这个诊断是对的，我原来挑的动作却偏向「把治理写得更完整」，而最后三天真正的风险一个都没进方案——仓库里没有一份用真裁判跑出来的 manifest，两个云端依赖没有 key，提交清单还有八项没勾。修正后的方案以这三件事为主轴，流程类的改动只保留三条。

## 第 1 轮 · 诊断

左边是仓库里量出来的事实，右边是能对应上的原则。

**一、模块 5:4:1:1，九成代码在两个人手里。** 这不是勤奋问题，是分工轴选错了。按模块分等于按「谁写哪个文件」分，而写文件这一层已经不稀缺了：Anthropic 说当代码不再是瓶颈、构建阶段跑得比传统 SDLC 允许的更快时，瓶颈会移到构建两侧，也就是计划、评审与测试、部署，这些还在以人的速度跑；逐行人工评审在代码由人写的时候是合理的，一旦大部分 diff 由 agent 产出就再也跟不上 [A1]。

**二、模块验收的验收人全是「待确认」。** `docs/MODULES.md` 里 M1 到 M11 共 11 条模块验收，验收人一律写着「待确认」（`:37`、`:72`、`:110`、`:147`、`:180`、`:209`、`:243`、`:283`、`:324`、`:365`、`:383`），M12 那条连这一栏都没有。这是整份文档里最贵的一行字。按 Anthropic 对岗位定义的写法，一个岗位要写清四件事：用户、任务、产出，以及一个可度量的质量阈值 [A2]。没有阈值的岗位定义不成立，因为它没法告诉你这一轮到底过没过（这一句出自知识库对原文的归纳，不在逐字引文里）。

做代码现代化那篇里说得更硬：证书是每个改动都必须满足的一组条件，设计上的关键约束是每条条件都能脱离人自动判定，这样 agent 才能自己迭代到通过、把过不了的升级给人 [A3]。11 条验收能写成「待确认」，说明这 11 个模块一条证书都没有。

**三、提交链上看不出第二个人。** `git rev-list --count main` 是 10，全部分支 16，作者名只有一个 VioletScar_Hui，挂了两个邮箱（13 次用 noreply，2 次用 gmail）。AI-native 的做法是让提交链本身变成审计链：谁提的要求、agent 产出了什么、谁批准的 [A4]。现在链上看不出第二个人，说明其余四个人的工作没有以「产出口进版本控制」的方式发生。

**四、节点上所有人共用一棵工作树。** `scripts/node.py:156` 把 arcname 硬编码成 `sparkjury`，`:162` 执行 `cd ~ && tar xzf sparkjury.tar.gz`，`main()` 里的 `sync` 不收参数（`:272` 附近）。两个人同时 sync 就互相覆盖，正在跑的长任务会读到半新半旧的代码。仓库自己已经写过解法：`scripts/node.py:255` 的 `check()` 会打印「要部署就 cp -r ~/sparkjury ~/sparkjury-<你的名字> 落到自己的副本」，`AGENTS.md` 里也有同一条，但它只是一行提示，没人必须照做。Anthropic 对这件事的结论是结构性的：两个 teammate 编辑同一个文件必然导致覆盖，正确的修法是让每个人拥有一组互不相交的文件，不要指望沟通、文件锁或任务排序 [A5]。

**五、只读的活没有并行，写操作没有排队。** SDK 的做法是只读工具并发、改状态的工具串行 [A6]。映射到这里：验收、审计、查资料、读代码这些只读工作五个人可以一起上；sync、deploy、τ²-bench 跑数必须排队。现在正好反过来。

**六、前端为零，M8 却标着已交付。** 仓库里没有任何 `.js/.ts/.vue/.jsx` 源文件（排除 `.worktrees`、`node_modules`、`.venv` 后计数为 0）。这里要替原来的写法更正一句：`src/sparkjury/api/static/index.html` 不是一张敷衍的兜底页，它是一张能跑的看板——195 行，七个阶段状态灯（`:87` 的 `STAGES`）、SSE 加轮询兜底（`:151`、`:149`）、run 选择器（`:121` 拉 `/runs`）、降级事件、DGX 面板（`:181` 的 `loadDgx`）、卡片上还有「PM: fix this first」按钮（`:175`）。它是本方案自带的，不是剑乔的产出口。按 `docs/ARCHITECTURE.md:374`，M8 的后端 API 是「由本方案提供」给他的，他真正要交的前端仍然一行都没有。

**七、跑数发现的问题没有回到流水线。** 闭环的做法是触发器无人介入地唤起模型，它发现的东西以 intent.md 形式重新进入流水线，1σ 只记日志、2σ 以只读方式诊断、3σ 才允许行动且只能开一个进评审门的 PR [A9]。现在节点上跑出来的 badcase、演示踩到的坑，都停在聊天记录里。

**已经做对的地方。** 七个阶段是工作流而不是自治 agent，judges 才是模型自由发挥的地方，对结构化任务这是对的；数据契约先行，M1 把 Trace / Verdict / Card 定死，这正好是「初始化 agent 先把环境和清单建好，后续每次会话从这些 artifact 接着干」那个套路 [A14]；降级要标 degraded 而不是假装成功，这条比多做一个功能值钱。

## 第 2 轮 · 换轴

把「谁负责哪个模块」换成「谁拥有哪个产出口」。四组人，四个产出口，每组写明用户、任务、产出、通过条件。

**A. 规格与场景（千富、人瑜）。** 用户是评委，以及未来真正要用的 PM。任务是把真实营销场景变成可展示的规格与判据。产出是 `data/real/` 下的脱敏 trace（目录目前还不存在）、一份「什么是好」的判据文档（`docs/CRITERIA.md`，同样待创建）、以及视频与征文里的事实与数字。通过条件是：脱敏文件能被 `sparkjury ingest --path <文件> --source otel` 读进去并跑出卡片；判据覆盖四个维度并对对不上的地方明确标注；演示材料里的每个数字都能在仓库里找到出处。监督档位是 reviewed。

这一组的价值不在行数。Anthropic 给创业公司的第一条原则是「所有人都能交付」：agentic coding 降低了门槛，理解问题的人可以自己交付第一版修复 [A10]。他们脑子里的领域判断是稀缺的，不该只用来写文档。

**B. Skill 与路由（万凌）。** 产出是六个 Skill 的三件套、六个端到端用例、本地模型到 Skill 的路由表。通过条件要写得诚实一点：`scripts/validate_skills.py` 只查格式、从不执行 Skill，而 `tests/test_m9_skills_nat.py` 把六个封装里的三个直接 skip 掉、剩下三个只跑 `--help`。也就是说「校验通过加测试全绿」这条今天就是绿的，把它当证书等于自欺。真正的条件是先让它红：按评估先于文档的做法，先拿代表性任务跑出失败模式、建立基线，再写刚好够通过的内容 [A11]。监督档位 automated。

**C. 界面与可见性（剑乔）。** 这一条在第四轮被整个重写过。原来的写法是把「断网可跑、七阶段可见、降级行可见」当成他的通过条件，而这四条那张 195 行的看板今天全都满足，等于把症状六从「待确认」改成「已通过」，前端照样是零。

重新定义之后，他要交的是「把视频里需要切终端才能看的环节搬进看板」：三份裁判理由并排（现在 `docs/VIDEO_SCRIPT.md:27` 的做法是切到终端跑 `sparkjury verdicts <id>`）、前后两次 run 的回归对比（pass^1 与 pass^k 的变化、fixed 与 broken 清单）、以及簇的明细下钻。看板现在有 run 选择器、有簇的计数与「fix this first」按钮，但没有 verdicts 视图，也没有 regress 视图，这两块正是演示里最该被看见的部分。通过条件就是：断网下能跑，且这三块在 1920×1080 下能录进视频。监督档位 sampled。

**D. 环境与产物回收（滨辉）。** 这一条同样被重写。原来写的是「盯 30×3 跑数」，而仓库里 `runs/` 下四个 manifest（`demo-1`、`api-smoke`、`shot-demo`、`run-20260926-193524`）全是本地 mock 跑出来的——judge 的 `mean_latency_ms` 在 0.02 到 0.13 毫秒之间，真裁判是秒级；INGEST 的文件路径还是 Windows 盘符 `D:\AI\claude_code\...`；唯一的 degradation 是 Jev 没有 key 退回本地仲裁。`data/simulations/` 是空目录，而 `deploy/run.toml` 的 `[[inputs]] path = "data/simulations/retail_qwen3-8b_trials3.json"` 指向的文件不在仓库里。`AGENTS.md` 的红线最后一条写着节点在活动结束后会被清空，产物要及时拷出来。

所以 D 组的通过条件改成两件可验证的事：节点上真裁判那一轮的 manifest 与产物拷回仓库或至少拷出节点并写明位置；`deploy/dgx/status.sh` 全绿，或者降级项被逐条列出来。监督档位 reviewed。

### 交叉验收的环（第四轮修正过）

原来的环把万凌组的验收人派给滨辉。这条被否掉了：滨辉是 CLI 的作者，六个 Skill 全是 CLI 的薄壳，让他验收 Skill 等于自己验自己。修正后每个验收人都是这个产出口的下游使用者：

- A 组（场景与判据）由万凌验收，他懂评测口径，也要拿这些数据跑自己的 Skill。
- B 组（Skill 与路由）由人瑜验收，使用者视角，照着 SKILL.md 能不能把命令跑出来。
- C 组（看板）由滨辉验收，他清楚 API 给了什么。
- D 组（环境与产物）由千富验收，他要在视频里讲节点，必须自己跑一遍 `status.sh`。

## 第 3 轮 · 架构改动

系统架构本身没有大毛病，问题集中在两个单点和一条断掉的链。第四轮之后，改动从六条收敛到三条。

**一、读并行、写串行，队列可见。** 在 `deploy/dgx/` 下放一个 `QUEUE.md`：只读的验收、审计、查资料随便并行；sync、deploy、τ²-bench 跑数一次只允许一个。这是「只读并发、改状态串行」的直接映射 [A6]，同时顺手解掉共用工作树的覆盖问题，而不用先写代码。原来的「按人分区工作树」降级成可选：`scripts/node.py:255` 已经在提示这件事，把提示改成硬性检查即可，不必先做参数化。

**二、评审按爆炸半径分三级，事先写死。** 规则可以直接抄：按爆炸半径和 agent 置信度分级，关键路径保留完整人工评审，同一类 flag 反复出现要在上游修掉而不是逐条重审 [A7]。三级清单本身是唯一要人记住的东西：

- 改 `docs/`、注释、Skill 文案：走 PR，CI 绿就合，不要求人工 review。
- 改单个模块内的代码、本地测试绿：一人 review。
- 改 `judges/`、`arbiter/`（接口契约）、`cli.py`（入口）、`deploy/`（环境）：一人 review 加节点验证。

分支保护在 9 月 27 日凌晨已经落到 main 上：`AGENTS.md:111` 写明非管理员不能直推 main、改动一律走分支加 PR、PR 上跑 ubuntu / macOS / Windows 三平台检查、三个全绿才允许合并，强推和删分支也禁掉了。所以三级里的第一级不是「没人看」，是「CI 看」：改文档走 PR，三个检查绿了就合，不额外要求人工 review。第二、三级仍然要人看，第三级还要在 PR 里附一节「节点部署验证」——模板里有这一栏，仓库对 agent 的硬性要求是每次开 PR 都要把改动同步到节点、跑起来、把实际输出贴回来。

**三、把闭环接在跑数当场，不是赛后复盘。** 第四轮把这条的对象改了：tau2 跑出来的 badcase 当场变成下一轮的输入，而不是等赛后再写复盘。这个项目本身要演示的就是「评测发现的问题回到 Agent 手里」，写在赛后等于把卖点埋掉 [A9]。

### 删掉的那条

原来是六条，删掉的是「每类 artifact 指定唯一真源」 [A8] 那张表。理由很直接：表里两个真源（`docs/CRITERIA.md` 和 `data/real/`）都不存在，指向空路径比不写更糟；而这条在第四轮自己已经承认三天内不要求跑出数据，等于产出的是纸，代价是全队多背一套「这个放哪、那个放哪」的规矩。

只保留其中真正救火的一句：节点不是代码真源，代码真源是 GitHub main，而跑数产物必须从节点拷回来——因为节点活动结束后会被清空，这是 `AGENTS.md` 自己写的红线。

### 两个单点

所有下游阶段都穿过万凌的接口（judges 与 panel 的输出契约），所有环境决策都穿过滨辉。做法不是把人拆开，是给这两个单点各写一条机器可判的契约测试 [A3]。有了证书，单点就从人变成测试。这一条三天内做不到，写在这里是留给赛后的。

## 第 4 轮 · 对抗性检验

这一轮由两部分组成：我自己的反驳，以及一次独立评审。独立评审是让一个全新上下文的评审者只读仓库和本文件、专门找事实错误与站不住的假设，这是 [A3] 里「独立对抗式评审，每次都在全新的上下文窗口里」的直接应用。它报回的十七条我逐条核了一遍，下面只留核实的部分，另附两条它报错而我核不成立的地方。

**被推翻的第一条：剑乔的通过条件今天就是绿的。** 原来的「断网可跑、七阶段可见、降级行可见」四条，那张 195 行的看板全部满足（证据见第 1 轮第六条）。这条如果不改，症状六从「待确认」变成「已通过」，什么都没解决。

**被推翻的第二条：B 组的条件今天也是绿的。** `validate_skills.py` 只查格式，`tests/test_m9_skills_nat.py` 里三个封装被 skip、三个只跑 `--help`。方案自己前一段说「六个 Skill 的测试等于没有基线」，后一段就宣布这一组的条件全机器可判——同两段话互相否掉。改成先让它红。

**被推翻的第三条（我采纳并删掉整条）：真源表。** 理由见上。

**被推翻的第四条：验收人派错了。** 环上有一环是「滨辉验收万凌的 Skill」，而他正是 CLI 的作者。修正后的环见第 2 轮。

**最大的遗漏：仓库里没有一份真裁判的 manifest。** `runs/` 下四个 manifest 全是 mock（judge 的 `mean_latency_ms` 0.02 到 0.13 毫秒，路径是 `D:\...`），`data/simulations/` 空，`deploy/run.toml` 指向的 tau2 输出不存在。而 `docs/ESSAY_十日谈.md:60` 写着 30 任务乘 3 次的完整跑数凌晨启动、一个任务约 8 分钟，按这个算两小时该跑完，产物却不在仓库里。对照 `AGENTS.md` 的红线（节点活动结束后清空），这是最后三天最可能翻车、且翻车后无法补救的地方。原来的方案把它排在 9 月 28 日「盯跑数」，本末倒置。现在的第一优先级是回收已有产物。

**第二大的遗漏：两个云端依赖都没有 key，而它被写进了「已经做对的地方」。** `deploy/dgx/.env` 在 `.gitignore` 里、本地不存在，于是 judge_c（StepFun）永远退化成 mock，Jev 永远退回本地仲裁——计划里最值钱的两个卖点（三家族陪审团、云端分歧仲裁）在演示当天大概率一个都跑不起来。更糟的是 `docs/VIDEO_SCRIPT.md:26` 的旁白还写着「三个裁判，Qwen、Gemma、Step，三个家族」，而 Gemma 早在 9 月 26 日就换成了 Nemotron。降级被标出来是诚实，但它首先是演示风险，不是优点。

**第三大的遗漏：提交清单八项未勾没有指派到人。** `docs/SUBMISSION_CHECKLIST.md` 里未勾的是推到 GitHub 或码云公开、删除 `runs/` 与 `.env`、补 Screenshots 三张图（`docs/img/` 只有两张）、录视频、上传 B 站、ESSAY 补 27 到 29 日、发 CSDN 或知乎、团队合影。原来的三天动作里只有一句「下午录视频、填提交表、推仓库」，没有主人。

**第四大的遗漏：仓库里现成的数字已经是错的。** `README.md:135`、`docs/SUBMISSION_CHECKLIST.md:35`、`docs/ESSAY_十日谈.md:42` 三处当时写着「96 个测试」（这一轮已经改掉），`docs/MODULES.md:379` 另记 98；9 月 27 日凌晨实测是 116 passed、3 skipped——`tests/test_ablation.py` 进来之后总数又变了一次，正好说明没有守数字的检查，这类数字就会一直漂。我原来把「数字可溯源」立成 A 组的通过条件，可这条今天就是红的——按它自己的标准，得先修仓库里的错数字，再拿它要求别人。

**它报的两条我当时都判成不成立，回头核是它对我错。** 第一条，它说 `docs/MODULES.md` 里「验收人」有一行连写两遍。我看到 `:514` 和 `:516` 两行，以为分别属于我新加的两节、中间隔着 `---`，就判它看错了。实际结构是：Skill 库那一节根本没有验收人栏，而「真实场景与判定规则」那一节把验收人连写了两遍——它指的问题真实存在，是我拿行号硬套了自己的印象。第二条，它说 main 已开分支保护、改个错别字也要开 PR 等三平台 CI。我核的时候 main 还停在 `35acaf1`，那里搜「分支保护」确实是 0 次，但几小时后那条 PR 合了，这个判断跟着失效。两条教训是同一条：核实结论要连「核的是哪个版本、有没有看结构本身」一起说清，只报一个命中次数不足以否掉一条结论。

**它的取舍建议我采纳了。** 保留读并行写串行、闭环改成跑数当场、三级评审清单；删掉真源表。一句话总评也对：诊断是准的，但原来的动作偏向把治理写得更完整，而不是把演示素材捞回来。

## 第 5 轮 · 收口

### 四组产出口与验收

| 产出口 | 产出者 | 通过条件 | 验收人 |
|---|---|---|---|
| 真裁判产物回收与环境 | 滨辉 | 真裁判一轮的 manifest 与产物拷出节点并写明位置；`status.sh` 全绿或降级逐条列出 | 千富 |
| 场景与判据 | 千富、人瑜 | `sparkjury ingest --path <文件> --source otel` 出卡片；判据覆盖四维；演示材料里的数字全部可溯源 | 万凌 |
| Skill 与路由 | 万凌 | 六个 Skill 各有会先失败、后转绿的端到端用例；路由表覆盖 8001 到 8004 | 人瑜 |
| 看板 | 剑乔 | 三份裁判理由并排、回归对比、簇明细三块上板，断网可跑，能录屏 | 滨辉 |

### 三天动作

9 月 27 日夜。滨辉把节点上已经跑出来的东西列一份清单（哪些 run、用什么裁判、产物在哪），这一步只查不跑。千富和人瑜把 `docs/CRITERIA.md` 和 `data/real/` 建起来。剑乔确认那三块视图的数据源在 API 里是否都有。万凌把六个 Skill 的失败用例先写出来。

9 月 28 日。滨辉回收产物或重跑真裁判，这是当天第一优先级；基线一落地就接着跑 `docs/ABLATION.md` 那四条臂——它吃的是同一批 trace，只重过裁判与仲裁那两段，每条几分钟，不用再花那七个小时。四条臂一出来，「云端那两条依赖到底值多少」就从卖点变成一个带对照组的结论。千富与人瑜交判据第一版，万凌验收。万凌把六个端到端用例补齐，人瑜验收。剑乔接上三块视图，滨辉验收。同时所有人把提交清单上属于自己的那几项认领掉：仓库公开与 `.env` 清理归滨辉，Screenshots 第三张与视频录制归千富，ESSAY 27 到 29 日归人瑜，B 站上传与提交表单归剑乔。

9 月 29 日。上午四条通过条件挨个跑一遍，红的当场修。下午录视频、填表、推仓库。录制前必须先改掉 `docs/VIDEO_SCRIPT.md:26` 的 Gemma，并确认旁白是按「有一个云端裁判降级」的口径讲的，而不是按三家族齐活讲。

### 交付前要跑的检查

交付前把这些命令挨个跑一遍，输出贴进本文件：

- 环境与产物：`bash deploy/dgx/status.sh`，以及 `cat runs/<run_id>/manifest.json` 看 degradations 与 judge 延迟是不是秒级
- 场景：`uv run sparkjury ingest --path data/real/<file>.json --source otel`，然后 `uv run sparkjury run --config deploy/run.node.toml`
- Skill：`uv run python scripts/validate_skills.py` 与 `uv run pytest tests/test_m9_skills_nat.py -v`，另外确认新写的失败用例确实会在修复前红
- 看板：`uv run sparkjury serve` 后 `curl -s http://127.0.0.1:9000/ >/dev/null`，断网下再跑一次
- 文档数字：`README.md` 与 `docs/SUBMISSION_CHECKLIST.md` 里的「96 个测试」已经改成 2026-09-27 实测的 116 passed、3 skipped，征文那一句改成「当晚 96 个」并注明现在的数字。彻底的做法还没做——让 `tests/test_m11_docs.py` 去核文档里的数字和实测是否一致，这样以后漂了会红，而不是靠人偶尔发现

### 这一轮之后仍然没解决的

两个云端裁判没有 key，本地仲裁与 mock 裁判是唯一的兜底；两个单点的契约测试要等赛后；真裁判那一轮如果产物确实丢了，三天内重跑一轮的成本要先把 `data/simulations/` 的输入准备出来。

---

## 附录 A · 逐字引文

英文逐字照抄切片 frontmatter 的 `quote` 字段，紧跟中文翻译。引文里若合并了原文中不连续的多段，段间用 `[... omitted ...]` 标出。原始发布日期与最近核验日期同样取自切片 frontmatter。

### A1 bottleneck-shifts-around-build

> When code is no longer the bottleneck and the build phase runs faster than the traditional SDLC allows for, three things become true:
> The bottleneck moves to the steps to the left and right of the build phase. This is mainly plan, review/test, and deploy, which still run at human speed.
> The controls stop matching reality and become intractable. Reviewing each line by hand made sense when a person had written it, but it can't keep up once agents write most of the diff.
> Governance costs increase because exceptions still route through meetings and committees that meet weekly or monthly.
> Build is no longer the constraint — the human-speed steps around it are. Human-speed stages keep their length while build collapses to hours.

> **中文**：当代码不再是瓶颈、构建阶段跑得比传统 SDLC 所允许的更快时，有三件事同时成立：瓶颈移到了构建阶段两侧的步骤上，主要是计划、评审与测试、部署，这些环节仍然以人的速度运转。控制手段不再与现实匹配，变得难以应付——在代码由人写的时候，逐行人工评审是合理的，但一旦大部分 diff 由 agent 写出来，它就再也跟不上。治理成本上升，因为例外情况仍然要走每周或每月开一次的会议和委员会。构建不再是约束，它周围那些以人的速度运转的步骤才是；人速阶段的长度不变，而构建塌缩到几小时。

— [The AI-Native SDLC playbook](https://claude.com/blog/the-ai-native-sdlc-playbook) · 原始发布：2026-08-21 · 最近核验：2026-09-01 · 切片：`product-philosophy/bottleneck-shifts-around-build--principle.md`

### A2 ai-job-definition

> Seven considerations to be settled in chronological order: before the pilot begins, during the pilot phase, and in production
> [... omitted ...]
> A four-part definition of the job the AI will do (user, task, output, and a measurable quality threshold), and a lightweight total cost of ownership model to build before the pilot
> A four-tier oversight model (automated, sampled, reviewed, and advisory) that matches human review to the risk of each output, with example tasks and a review cadence for each tier
> A transition blueprint laying out what to decide, when to decide it, and who needs to own each decision

> **中文**：七个需要考虑的点，按时间先后排定：试点开始前、试点期间、以及进入生产之后。……用四个部分定义这个 AI 要做的工作（用户、任务、产出，以及一个可度量的质量阈值），并在试点开始前建一个轻量的总拥有成本模型。一个四档的监督模型（自动、抽样、评审、顾问），让人工评审与每一类产出的风险相匹配，每档都配示例任务和评审节奏。一份过渡蓝图，写清要决定什么、什么时候决定、以及每个决定需要有谁来负责。

— [Deploying AI from pilot to production](https://claude.com/blog/deploying-ai-from-pilot-to-production) · 原始发布：2026-09-14 · 最近核验：2026-09-27 · 切片：`agent-design/ai-job-definition-user-task-output-threshold--technique.md`

### A3 modernization-certificate

> The certificate is the set of conditions or tests that every modernization change must meet. Pick the conditions that give the strongest cumulative evidence that the change is correct against the target.
> Each condition should be checkable without a human in the loop, so the agentic workflow can iterate on a change until it meets the certificate or flag it for human review if it can’t.
> [... omitted ...]
> Independent adversarial reviews by Claude, each in a fresh context window, find no blocking issues
> [... omitted ...]
> Write the certificate with the people who will review and promote changes into production. Bring in the developers, user groups, and business leads who depend on the codebase now, while the certificate and agentic workflow are still being designed.

> **中文**：证书是每一次现代化改动都必须满足的一组条件或测试。挑选那些能对「改动相对目标状态是正确的」提供最强累积证据的条件。每条条件都应当能够脱离人在环外被检查，这样 agent 工作流就能自己反复迭代一个改动直到它满足证书，满足不了才升级给人评审。……由 Claude 做的独立对抗式评审，每次都在全新的上下文窗口里，没有发现阻断性问题。……证书要和那些将要评审并把改动推进到生产的人一起写。把现在依赖这套代码库的开发者、用户群体和业务负责人拉进来，趁证书和 agent 工作流还在设计阶段。

— [How to prepare for AI-driven code modernization projects](https://claude.com/blog/how-to-prepare-for-ai-driven-code-modernization-projects) · 原始发布：2026-09-23 · 最近核验：2026-09-27 · 切片：`evaluation/modernization-certificate-machine-checkable--principle.md`

### A4 committed-artifact-audit-trail

> The thread running through the right-hand column is the committed artifact. Each stage ends by writing one to version control (including intent.md, spec.md, plan.md, the diff and its tests, the PR with its review findings, and the incident record) and the next stage begins by reading it. For the early stages, .md files are the predominant artifact because a product owner and an agent can both read and act on the same file. From Build onward, the artifact is code and its records. The chain of commits is also the audit trail: who asked for what, what the agent produced, and who approved it.

> **中文**：贯穿整条流程的线索是提交进版本控制的产出口。每个阶段都以把一个产出口写进版本控制作为结束（包括 intent.md、spec.md、plan.md、diff 与它的测试、带评审结论的 PR，以及事故记录），下一个阶段以读它作为开始。在早期阶段，Markdown 文件是主要的产出口形态，因为产品负责人和 agent 都能读同一个文件并据此行动。从构建往后，产出口就是代码及其记录。提交链同时就是审计链：谁要求了什么、agent 产出了什么、谁批准了它。

— [The AI-Native SDLC playbook](https://claude.com/blog/the-ai-native-sdlc-playbook) · 原始发布：2026-08-21 · 最近核验：2026-09-01 · 切片：`product-philosophy/committed-artifact-audit-trail--principle.md`

### A5 avoid-file-conflicts

> Two teammates editing the same file leads to overwrites. Break the work so each teammate owns a different set of files.

> **中文**：两个 teammate 编辑同一个文件会导致互相覆盖。把工作拆开，让每个 teammate 拥有一组不同的文件。

— [Agent teams](https://code.claude.com/docs/en/agent-teams.md) · 持续更新文档（原始发布日期未标明）· 最近核验：2026-05-26 · 切片：`agent-design/avoid-file-conflicts-in-agent-teams--antipattern.md`

### A6 parallel-readonly-sequential-mutating

> When Claude requests multiple tool calls in a single turn, both SDKs can run them concurrently or sequentially depending on the tool. Read-only tools (like `Read`, `Glob`, `Grep`, and MCP tools marked as read-only) can run concurrently. Tools that modify state (like `Edit`, `Write`, and `Bash`) run sequentially to avoid conflicts. Custom tools default to sequential execution. To enable parallel execution for a custom tool, set `readOnlyHint` in its annotations.

> **中文**：当 Claude 在一个回合里请求多个工具调用时，两个 SDK 都可以按工具类型决定并发还是串行执行。只读工具（如 `Read`、`Glob`、`Grep`，以及被标记为只读的 MCP 工具）可以并发运行。修改状态的工具（如 `Edit`、`Write`、`Bash`）串行运行以避免冲突。自定义工具默认串行执行；要让某个自定义工具并发，就在它的注解里设置 `readOnlyHint`。

— [Agent loop](https://code.claude.com/docs/en/agent-sdk/agent-loop.md) · 持续更新文档（原始发布日期未标明）· 最近核验：2026-05-26 · 切片：`agent-design/parallel-readonly-sequential-mutating--principle.md`

### A7 promotion-policy

> Agents will produce changes far faster than any human team can review them diff-by-diff. The promotion policy is a tiered review path–written down and agreed in advance–that sets the depth of human review for a change, so the modernization can finish on an acceptable timeline.
> [... omitted ...]
> Tier changes by blast radius and agent confidence. Use your organization's own change or risk classification if it has one. Keep full human review for critical paths.
> [... omitted ...]
> Fix recurring flags at the source. Group and analyze the flagged changes over time. When the same kind of flag keeps recurring, fix the cause in the agentic workflow or the certificate rather than reviewing each one.
> [... omitted ...]
> Allocate SME time effectively . SMEs won't read every final diff, but their judgment is still the scarce input. Make it easy for them to go straight to the changes in the highest-risk tiers, and to the flagged agent decisions within each one, without wading through large diffs.

> **中文**：agent 产出改动的速度会远超任何人类团队逐 diff 评审的速度。晋升策略是一条分级的评审路径——事先写下来并达成一致——它决定一次改动需要多深的人工评审，好让现代化工作能在可接受的时间线上完成。……按爆炸半径和 agent 置信度给改动分级，如果你所在组织已有变更或风险分级就用它自己的；关键路径保留完整的人工评审。……把反复出现的 flag 在源头修掉：把被标记的改动按时间归组分析，当同一类 flag 反复出现时，去修 agent 工作流或证书里的根因，而不是逐条重审。……有效分配专家时间：专家不会读完每一份最终 diff，但他们的判断仍然是稀缺输入；让他们能直接跳到风险最高那几档的改动、以及其中被标记的 agent 决策，而不必在大段 diff 里跋涉。

— [How to prepare for AI-driven code modernization projects](https://claude.com/blog/how-to-prepare-for-ai-driven-code-modernization-projects) · 原始发布：2026-09-23 · 最近核验：2026-09-27 · 切片：`product-philosophy/promotion-policy-tiers-by-blast-radius--technique.md`

### A8 legacy-systems-source-of-truth（引用于被删掉的那条，保留备查）

> When transitioning to the AI-native SDLC, for every artifact the process produces, name one system as the source of truth, with everything else holding a copy or a link to the original.

> **中文**：在转向 AI-native SDLC 时，对于流程产出的每一样 artifact，都要指定一个系统作为真源，其余地方只保留副本或指向原始的链接。

— [The AI-Native SDLC playbook](https://claude.com/blog/the-ai-native-sdlc-playbook) · 原始发布：2026-08-21 · 最近核验：2026-09-01 · 切片：`product-philosophy/legacy-systems-source-of-truth--principle.md`

### A9 stage-6-closing-the-loop

> The loop closes. A trigger invokes Claude with no person in the invocation path, and what it finds re-enters the pipeline as intent.md.
> [... omitted ...]
> A deterministic script watches production and invokes Claude when a control band is breached.
> [... omitted ...]
> Response tiers are defined in version-controlled config (bands.yaml below). At 1σ the script only logs, at 2σ it invokes Claude read-only to diagnose, and at 3σ Claude may act, though only by opening a PR into the review gate or triggering a pre-approved runbook.

> **中文**：环闭合了。一个触发器在调用路径上没有人的情况下唤起 Claude，它发现的东西以 intent.md 的形式重新进入流水线。……一个确定性脚本盯着生产环境，当控制带被突破时唤起 Claude。……响应档位定义在受版本控制的配置里（下文的 bands.yaml）：1σ 时脚本只记日志，2σ 时它以只读方式唤起 Claude 做诊断，3σ 时 Claude 可以行动，但仅限于开一个进入评审门的 PR，或触发一份预先批准的 runbook。

— [The AI-Native SDLC playbook](https://claude.com/blog/the-ai-native-sdlc-playbook) · 原始发布：2026-08-21 · 最近核验：2026-09-01 · 切片：`agent-design/stage-6-closing-the-loop--technique.md`

### A10 startups-everyone-ships

> Agentic coding lowers the barrier to entry, so the person who understands the problem can ship the first version of the fix.
> Agentic coding lowers the barrier to entry for non-technical employees to build products. With Claude Code, you can create functional features without being fluent in a coding language or how to use an IDE.

> **中文**：agentic coding 降低了进入门槛，所以理解问题的人可以自己交付第一版修复。它也为非技术岗位的员工降低了做产品的门槛：用 Claude Code，你不需要精通编程语言或者会用 IDE，就能做出可用的功能。

（该切片正文另有一句「它压掉了传话链条（想法 → 产品 → 设计 → 工程），让领域专家自己发出 PR」，属于知识库对原文的归纳，不在上面的逐字引文里。）

— [Claude Code guide for startups](https://claude.com/blog/claude-code-guide-for-startups) · 原始发布：2026-08-20 · 最近核验：2026-09-01 · 切片：`claude-code/startups-everyone-ships--principle.md`

### A11 skills-build-evals-before-docs

> **Create evaluations BEFORE writing extensive documentation.** This ensures your Skill solves real problems rather than documenting imagined ones.

> **中文**：**在写大量文档之前先建立评估。** 这能确保你的 Skill 解决的是真实问题，而不是在记录想象中的问题。

（该切片正文列了五步做法——找缺口、建三个评估场景、测基线、只写最小必要指令、迭代——同属知识库归纳，不是逐字引文。）

— [Agent Skills best practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices) · 持续更新文档（原始发布日期未标明）· 最近核验：2026-05-25 · 切片：`evaluation/skills-build-evals-before-docs--principle.md`

### A12 human-evaluation-catches-what-automation-misses

> Human evaluation catches what automation misses. People testing agents find edge cases that evals miss. These include hallucinated answers on unusual queries, system failures, or subtle source selection biases. In our case, human testers noticed that our early agents consistently chose SEO-optimized content farms over authoritative but less highly-ranked sources like academic PDFs or personal blogs. Adding source quality heuristics to our prompts helped resolve this issue. Even in a world of automated evaluations, manual testing remains essential.

> **中文**：人工评估能抓住自动化漏掉的东西。测试 agent 的人会发现评估漏掉的边角情况，包括不常见查询上的幻觉回答、系统故障，或细微的来源选择偏好。在我们的例子里，人工测试者发现早期 agent 总是选择 SEO 优化过的内容农场，而不是学术 PDF 或个人博客这类权威但排名较低的来源；在提示里加入来源质量启发式规则解决了这个问题。即使在自动化评估已经很成熟的环境里，手工测试依然不可替代。

— [How we built our multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system) · 原始发布：2025-06-13 · 最近核验：2026-05-26 · 切片：`evaluation/human-evaluation-catches-what-automation-misses--principle.md`

### A13 evaluation-becomes-bottleneck

> In turn, this means that the core bottleneck in alignment research could become evaluation (making sure that experiments are set up sufficiently well that we're confident in their results), rather than generation (relying on human researchers to propose promising ideas).

> **中文**：反过来，这意味着核心瓶颈可能从「生成」（依赖人类研究者提出有前景的想法）变成「评估」（确保实验设置得足够好，让我们对结果有信心）。

— [Automated Alignment Researchers](https://www.anthropic.com/research/automated-alignment-researchers) · 原始发布：2026-04-14 · 最近核验：2026-05-25 · 切片：`evaluation/evaluation-becomes-bottleneck--principle.md`

### A14 initializer-vs-coding-agent-split

> We developed a two-fold solution to enable the Claude Agent SDK to work effectively across many context windows: an initializer agent that sets up the environment on the first run, and a coding agent that is tasked with making incremental progress in every session, while leaving clear artifacts for the next session.

> **中文**：我们做了一套两段式的方案，让 Claude Agent SDK 能跨多个上下文窗口有效工作：一个初始化 agent，在第一次运行时把环境搭好；以及一个编码 agent，负责每次会话中做增量推进，同时为下一次会话留下清楚的 artifact。

— [Effective harnesses for long-running agents](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents) · 原始发布：2025-11-26 · 最近核验：2026-05-25 · 切片：`agent-design/initializer-vs-coding-agent-split--technique.md`

### A15 stage-4-continuous-evals

> Evals are the AI-native equivalent of stage-gate QA. In practice that means a suite that runs whenever the agent's configuration changes. When a new model is swapped in or a prompt is rewritten, the eval suite says whether the agent still does the work to the same standard.
> [... omitted ...]
> The platform engineer collects 20 to 50 real tasks from recent work with its expected/accepted outcome.
> [... omitted ...]
> Each production incident gets an eval, written by the team that owned the incident, and stays in the suite as a regression test.

> **中文**：评估在 AI-native 里等价于阶段门禁式的 QA。实践中这意味着有一套在 agent 配置发生变化时就跑的测试集：换了一个新模型、或者重写了提示，这套评估会告诉你 agent 是否还在以同样的标准完成工作。……平台工程师从最近的工作里收集 20 到 50 个真实任务，连同它们预期或被接受的结果。……每一次生产事故都会得到一个评估，由拥有那次事故的团队来写，并留在测试集里作为回归测试。

— [The AI-Native SDLC playbook](https://claude.com/blog/the-ai-native-sdlc-playbook) · 原始发布：2026-08-21 · 最近核验：2026-09-01 · 切片：`evaluation/stage-4-continuous-evals--technique.md`

## 附录 B · 没有采用的切片

- `product-philosophy/everyone-codes-roles-converge--context.md`、`product-philosophy/engineering-role-shift-to-orchestration--context.md`、`product-philosophy/occupation-matters-less-than-expertise--context.md`、`agent-design/agent-team-size-3-to-5--principle.md`、`agent-design/multi-agent-when-to-use--context.md`、`agent-design/workflows-vs-agents--principle.md`、`agent-design/agent-teams-vs-subagents--context.md`：状态是 needs-review，按知识库自己的规则不能作为当前结论的依据。「角色在融合」「3 到 5 人是合适规模」「工作流优先于自治 agent」这几条与本案高度相关，等复核通过后可以补进来。
- `agent-design/agent-teams-specialization-roles--technique.md`、`agent-design/agent-teams-known-good-oracle--technique.md`：来自 C 编译器那个项目，场景是长期运行的大型代码库，与三天黑客松的时间尺度对不上，本轮只在思路上参考，没有写进交付要求。
- `agent-design/agent-teams-research-and-review--context.md`：状态 active，结论是「agent 团队的第一次使用应该挑只读任务」。本轮把它当成了独立评审那一轮的做法依据，没有单独引用。

Sources cited：附录 A 的 A1 至 A15，去重后 10 个唯一来源，各自的原始发布日期与最近核验日期见每条末尾。
