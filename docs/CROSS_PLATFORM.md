# Windows 与 macOS 兼容说明

SparkJury 的代码、测试、看板和文档在 Windows、macOS、Linux 上通用。模型推理在 DGX 节点上，本地机器不需要 GPU。

## 三个平台都一样的

| 事项 | 命令 |
|---|---|
| 安装依赖 | `uv sync` |
| 测试 | `uv run pytest` |
| 离线演示 | `uv run sparkjury run --demo` |
| 看板 | `uv run sparkjury serve`，浏览器打开 http://127.0.0.1:9000/ |
| 分步命令 | `uv run sparkjury ingest / precheck / score / arbitrate / cluster / report / regress` |
| 校验 Skills | `uv run python scripts/validate_skills.py` |
| 连节点 | `uv run --group ops python scripts/node.py run "nvidia-smi"`（不依赖系统的 ssh/scp） |
| 截图 | `uv run --group ops python scripts/screenshot.py <url> <out.png>` |

## 只有写法不同的

| 事项 | macOS / Linux | Windows PowerShell |
|---|---|---|
| 装 uv | `curl -LsSf https://astral.sh/uv/install.sh \| sh` | `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 \| iex"` |
| 设环境变量 | `export STEPFUN_API_KEY=...` | `$env:STEPFUN_API_KEY="..."` |
| 打开 HTML | `open runs/card/card.html` | `start runs\card\card.html` |
| 看文件 | `cat skills/sparkjury-score/SKILL.md` | `type skills\sparkjury-score\SKILL.md` 或 `cat` |
| 路径分隔 | `/` | `\` 或 `/` 都可以（Python 和 uv 都接受 `/`） |

## 只在某一边成立的

- `deploy/dgx/*.sh` 是给 Linux 节点的 bash 脚本。macOS 可以直接 `ssh` 过去执行；Windows 用 `scripts/node.py`，或装 Git Bash / WSL。
- Windows 的老式控制台是 GBK 编码，CLI 输出已全部改成 ASCII；如果表格边框仍显示乱码，先执行 `chcp 65001`。
- 看板右栏的本机 GPU 只在有 nvidia-smi 的机器上显示；mac 上显示"no GPU visible"，不影响功能。
- 换行统一为 LF：`.gitattributes` 和 `.editorconfig` 已固定，Windows 上用任何编辑器保存都不会变成 CRLF。

## 已知未实测

macOS 上的完整测试套件没有实机跑过。依赖（pydantic、typer、fastapi、scikit-learn、numpy、openai、httpx）都有 Apple Silicon 轮子，预期直接通过；如有报错请附 `uv run pytest -x` 的输出。
