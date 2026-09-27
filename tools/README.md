# tools/ — 评测标准冻结与提交门禁

三件工具 + 一条命令。解决三件事：**评测标准不能悄悄改**、**编译产物不能混进仓库**、
**门禁顺序不能靠人记**。

| 文件 | 作用 |
|---|---|
| `pack_freeze.py` | scenario-pack 内容寻址冻结。DRAFT→FROZEN 状态机，`frozen_hash` 覆盖 pack 全部文件（相对路径 + 内容双 sha256）。`--verify` 校验当前内容与 manifest 一致——一切"改完重跑看分数涨没涨"的前提，是标准没变过 |
| `_yaml_lite.py` | 全库唯一的 YAML 读取实现。优先 PyYAML，缺失时用内置子集解析器；遇不识别结构显式报错而不是猜。避免多处各写一份、对同一份 pack 解析出不同结果 |
| `lint_skills.py` | 结构门。`--repo-only`：仓库级检查（pyc / 禁入路径），不依赖 skill 文件结构，任何状态下可跑。默认模式：skill 六件套（SKILL.md/skill-card/schemas/evals/references/BENCHMARK）逐项校验 |
| `verify.sh` | 门禁一条命令。顺序固化的原因写在脚本头注释里（pytest 写 pyc、代理四变量） |

## 日常用法

```bash
bash tools/verify.sh                  # 提交前跑一遍，ALL GATES PASSED 再推
python3 tools/pack_freeze.py --verify # 只想确认评测标准没被改过
python3 tools/lint_skills.py --repo-only   # 只想查 pyc 和禁入路径
```

## 评测标准的变更流程

`standards/scenario-pack/` 是评测标准的唯一事实源。改它 = 改评测标准，必须留痕：

1. 改 pack 里的 `*.yaml`（DRAFT 态，`pack.manifest.json` 的 `state` 标注）
2. `python3 tools/pack_freeze.py` → FROZEN，得到新 `frozen_hash`
3. 提交信息里写明 hash 前后值与改动理由
4. 前后两轮回归对比必须同 hash；换 hash = 新一轮评测，不是回归（详见
   `standards/scenario-pack/README.md`）

## 为什么 .pyc 值得一个门禁

真实教训：Python 每执行一次就写 `__pycache__/*.pyc`，`.gitignore` 只保证它不进 git，
但外部安全扫描器（如参赛方 NVIDIA 的 SkillSpector）会把二进制 `.pyc` 判为"可执行文件"，
把 skill 目录直接打成 CRITICAL。`lint --repo-only` 在提交前硬拦这一项。
