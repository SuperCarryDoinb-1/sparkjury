"""Talk to the team's DGX node from Windows, macOS or Linux without needing an ssh/scp binary.

    uv run --group ops python scripts/node.py run "nvidia-smi"
    uv run --group ops python scripts/node.py put local.tar.gz remote.tar.gz
    uv run --group ops python scripts/node.py get sparkjury/runs/x/card/card.html card.html
    uv run --group ops python scripts/node.py sync          # upload this repo (code only) and extract it on the node

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
EXCLUDE = {".venv", ".venv-vllm", ".venv-tau2", "runs", "logs", "__pycache__", ".pytest_cache", ".git", "dist"}


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
    return run("cd ~ && tar xzf sparkjury.tar.gz && cd sparkjury && find . -name '*.sh' -exec sed -i 's/\\r$//' {} + && echo synced into ~/sparkjury")


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
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
