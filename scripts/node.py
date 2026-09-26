"""Talk to the team's DGX node from Windows, macOS or Linux without needing an ssh/scp binary.

    uv run --group ops python scripts/node.py run "nvidia-smi"
    uv run --group ops python scripts/node.py put local.tar.gz remote.tar.gz
    uv run --group ops python scripts/node.py get sparkjury/runs/x/card/card.html card.html
    uv run --group ops python scripts/node.py sync          # upload this repo (code only) and extract it on the node
    uv run --group ops python scripts/node.py check         # what is the node doing, and does this checkout need deploying there

Connection settings come from environment variables (or deploy/dgx/node.env, same KEY=VALUE format):
    SPARKJURY_NODE_HOST (default 61.172.235.130), SPARKJURY_NODE_PORT (6030), SPARKJURY_NODE_USER (asus_gx10),
    SPARKJURY_NODE_PASSWORD or SPARKJURY_NODE_KEY (path to a private key). You are prompted for the password
    if neither is set. Remote paths are relative to the node home directory.
"""

from __future__ import annotations

import getpass
import os
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / "deploy" / "dgx" / "node.env"
EXCLUDE = {".venv", ".venv-vllm", ".venv-tau2", "runs", "logs", "__pycache__", ".pytest_cache", ".git", "dist",
           ".env", "node.env"}  # .env 与 node.env 绝不进 tar：sync 会覆盖节点上那份，把队里的 key 冲掉
PORT_NAMES = {"8001": "judge_a", "8002": "judge_b", "8003": "embed", "8004": "agent"}
LONG_TASK_HINTS = ("tau2", "loop")


def _settings() -> dict[str, str]:
    cfg: dict[str, str] = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                cfg[k.strip()] = v.strip().strip('"')
    for k in ("SPARKJURY_NODE_HOST", "SPARKJURY_NODE_PORT", "SPARKJURY_NODE_USER", "SPARKJURY_NODE_PASSWORD", "SPARKJURY_NODE_KEY"):
        if os.environ.get(k):
            cfg[k] = os.environ[k]
    cfg.setdefault("SPARKJURY_NODE_HOST", "61.172.235.130")
    cfg.setdefault("SPARKJURY_NODE_PORT", "6030")
    cfg.setdefault("SPARKJURY_NODE_USER", "asus_gx10")
    return cfg


def client():
    import paramiko

    cfg = _settings()
    kwargs: dict = {}
    if cfg.get("SPARKJURY_NODE_KEY"):
        kwargs["key_filename"] = cfg["SPARKJURY_NODE_KEY"]
    else:
        kwargs["password"] = cfg.get("SPARKJURY_NODE_PASSWORD") or getpass.getpass(f"password for {cfg['SPARKJURY_NODE_USER']}@{cfg['SPARKJURY_NODE_HOST']}: ")
    last: Exception | None = None
    for attempt in range(4):  # the gateway occasionally resets the banner; back off and retry
        c = paramiko.SSHClient()
        c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            c.connect(cfg["SPARKJURY_NODE_HOST"], port=int(cfg["SPARKJURY_NODE_PORT"]), username=cfg["SPARKJURY_NODE_USER"],
                      timeout=30, banner_timeout=45, auth_timeout=30, **kwargs)
            return c
        except (paramiko.SSHException, OSError) as e:
            last = e
            time.sleep(5 * (attempt + 1))
    raise SystemExit(f"ssh failed after retries: {last}")


def run(cmd: str) -> int:
    c = client()
    chan = c.get_transport().open_session()
    chan.exec_command("bash -lc " + _q(cmd))
    while True:
        drained = False
        if chan.recv_ready():
            sys.stdout.write(chan.recv(65536).decode("utf-8", "replace")); drained = True
        if chan.recv_stderr_ready():
            sys.stdout.write(chan.recv_stderr(65536).decode("utf-8", "replace")); drained = True
        if drained:
            sys.stdout.flush()
            continue
        if chan.exit_status_ready():
            break
        time.sleep(0.05)
    rc = chan.recv_exit_status()
    c.close()
    return rc


def _q(s: str) -> str:
    return "'" + s.replace("'", "'\"'\"'") + "'"


def capture(cmd: str) -> tuple[int, str]:
    """Like run(), but returns (exit_status, output) instead of streaming to stdout."""
    c = client()
    chan = c.get_transport().open_session()
    chan.exec_command("bash -lc " + _q(cmd))
    chunks: list[str] = []
    while True:
        drained = False
        if chan.recv_ready():
            chunks.append(chan.recv(65536).decode("utf-8", "replace")); drained = True
        if chan.recv_stderr_ready():
            chunks.append(chan.recv_stderr(65536).decode("utf-8", "replace")); drained = True
        if drained:
            continue
        if chan.exit_status_ready():
            break
        time.sleep(0.05)
    rc = chan.recv_exit_status()
    c.close()
    return rc, "".join(chunks)


def _local_head() -> tuple[str, str, int]:
    """(short sha, branch, number of uncommitted files) for this checkout; empty sha when not a git repo."""
    def git(*a: str) -> str:
        return subprocess.run(["git", "-C", str(ROOT), *a], capture_output=True, text=True).stdout.strip()
    try:
        sha = git("rev-parse", "--short", "HEAD")
        branch = git("rev-parse", "--abbrev-ref", "HEAD")
        dirty = git("status", "--porcelain")
    except OSError:
        return "", "", 0
    return sha, branch, len(dirty.splitlines()) if dirty else 0


def put(local: str, remote: str) -> None:
    c = client(); s = c.open_sftp(); s.put(local, remote); s.close(); c.close()  # remote is relative to $HOME
    print(f"uploaded {local} -> {remote}")


def get(remote: str, local: str) -> None:
    c = client(); s = c.open_sftp(); s.get(remote, local); s.close(); c.close()
    print(f"downloaded {remote} -> {local}")


def sync() -> int:
    """Tar the repo (code and docs only), upload, extract over ~/sparkjury. Never touches deploy/dgx/.env on the node."""
    with tempfile.TemporaryDirectory() as td:
        tgz = Path(td) / "sparkjury.tar.gz"
        n = 0
        with tarfile.open(tgz, "w:gz") as tar:
            for p in ROOT.rglob("*"):
                rel = p.relative_to(ROOT)
                if any(part in EXCLUDE for part in rel.parts) or p.suffix == ".db":
                    continue
                if p.is_file():
                    tar.add(p, arcname=str(Path("sparkjury") / rel).replace("\\", "/"))
                    n += 1
        print(f"packed {n} files, {tgz.stat().st_size // 1024} KB")
        put(str(tgz), "sparkjury.tar.gz")
    sha, branch, dirty = _local_head()
    stamp = f"{sha or 'unknown'} {branch or '-'} {time.strftime('%Y-%m-%d %H:%M')} by {os.environ.get('USER') or os.environ.get('USERNAME') or 'unknown'}" + (f" dirty={dirty}" if dirty else "")
    return run("cd ~ && tar xzf sparkjury.tar.gz && cd sparkjury && find . -name '*.sh' -exec sed -i 's/\\r$//' {} + "
               f"&& echo {_q(stamp)} > .synced-from && echo 'synced into ~/sparkjury as:' && cat .synced-from")


PROBE = " ; ".join([
    'echo "@@HOST $(hostname)"',
    'echo "@@GPU $(nvidia-smi --query-gpu=utilization.gpu,temperature.gpu,power.draw'
    ' --format=csv,noheader 2>/dev/null | head -1)"',
    'echo "@@MEM $(free -h 2>/dev/null | sed -n 2p)"',
    'echo "@@TMUX"',
    'tmux ls 2>&1',
    'echo "@@PORTS"',
    'for p in 8001 8002 8003 8004; do curl -fsS -m 5 "http://127.0.0.1:$p/v1/models" >/dev/null 2>&1 '
    '&& echo "$p up" || echo "$p down"; done',
    'echo "@@API $(curl -s -o /dev/null -m 5 -w \'%{http_code}\' http://127.0.0.1:9000/health)"',
    'echo "@@SYNCED $(head -1 ~/sparkjury/.synced-from 2>/dev/null)"',
])


def _sections(text: str) -> dict[str, list[str]]:
    """Split the probe output on @@KEY markers, so no parsing has to survive shell quoting."""
    out: dict[str, list[str]] = {}
    key = "TOP"
    for line in text.splitlines():
        if line.startswith("@@"):
            head, _, rest = line[2:].partition(" ")
            key = head
            out[key] = [rest] if rest else []
        else:
            out.setdefault(key, []).append(line)
    return out


def check() -> int:
    """Look at the node: what is running, which services are up, and does this checkout need deploying there."""
    sha, branch, dirty = _local_head()
    try:
        rc, text = capture(PROBE)
    except ImportError:
        print("缺 paramiko：先跑 `uv sync --group ops` 再试。")
        return 3
    except SystemExit as e:
        print(f"判断：节点不可达 —— {e}")
        print("这一步不能跳过，也不算通过：把失败原因写进 PR 描述并标成未验证，不要合并。")
        return 2

    s = _sections(text)
    one = lambda k: (s.get(k) or [""])[0].strip()  # noqa: E731
    tmux = [l for l in s.get("TMUX", []) if l.strip() and "no server running" not in l]
    ports = {}
    for line in s.get("PORTS", []):
        parts = line.split()
        if len(parts) == 2 and parts[0].isdigit():
            ports[parts[0]] = parts[1]

    long_running = [t.split(":")[0] for t in tmux if any(h in t for h in LONG_TASK_HINTS)]
    synced = one("SYNCED")
    synced_sha = synced.split()[0] if synced else ""
    api = one("API")
    mem = one("MEM").split()
    mem_s = f"统一内存 已用 {mem[2]} / {mem[1]}" if len(mem) >= 3 else "统一内存读不到"
    down = [p for p in sorted(ports) if ports[p] != "up"]

    print(f"== DGX 节点检查 · {one('HOST') or '?'} ==")
    print("连通      61.172.235.130:6030 通")
    print(f"GPU       {one('GPU') or '读不到'} · {mem_s}")
    print(f"tmux      {'; '.join(tmux) if tmux else '没有会话'}")
    print("服务      " + (" · ".join(f"{PORT_NAMES.get(p, p)} {ports[p]}" for p in sorted(ports)) or "没探到"))
    print(f"API       9000 /health -> {api}（返回 401 也算活着：进程在、鉴权在）")
    local = f"本地 {sha or '未知'} ({branch or '-'})" + (f" · {dirty} 个文件未提交" if dirty else " · 工作区干净")
    print(f"代码      节点 {synced or '没有同步记录'} / {local}")
    if long_running:
        print(f"长任务    {', '.join(long_running)} 正在跑")

    reasons = []
    if not synced:
        reasons.append("节点上没有同步记录")
    elif sha and synced_sha and synced_sha != sha:
        reasons.append(f"节点代码是 {synced_sha}，你本地是 {sha}")
    if dirty:
        reasons.append(f"本地有 {dirty} 个文件没提交")
    if down:
        reasons.append("端点没起来：" + ", ".join(f"{PORT_NAMES.get(p, p)}({p})" for p in down))

    print()
    code_stale = bool([r for r in reasons if "节点代码是" in r or "没提交" in r or "同步记录" in r])
    if not reasons:
        print("判断：不需要部署。节点代码与你本地一致，服务都在跑。")
    elif down and not code_stale:
        print("判断：需要起服务，代码不用重推。跑 `bash deploy/dgx/start_judges.sh`，再 `bash deploy/dgx/status.sh` 确认。")
    else:
        print("判断：需要部署。原因：" + "；".join(reasons) + "。")
    if long_running:
        print(f"注意：主树上有 {'、'.join(long_running)} 在跑，别直接往 ~/sparkjury 覆盖。")
        print("      要部署就 `cp -r ~/sparkjury ~/sparkjury-<你的名字>` 落到自己的副本，或者等它跑完。")
    return 0


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    op, args = argv[0], argv[1:]
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if op == "run":
        return run(" ".join(args))
    if op == "put":
        put(args[0], args[1]); return 0
    if op == "get":
        get(args[0], args[1]); return 0
    if op == "sync":
        return sync()
    if op == "check":
        return check()
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
