#!/usr/bin/env python3
"""pack_freeze.py — scenario pack 冻结/解冻（内容寻址）。

DRAFT → FROZEN：计算 pack 全部文件的 sha256，连同文件名得到单一 pack_hash，
写入 pack.manifest.json（state=FROZEN, frozen_hash=<hash>）。
FROZEN 回 DRAFT：--draft（govern 显式解冻时用，必须附理由）。

用法：
  python3 scripts/pack_freeze.py            # 冻结并打印 hash
  python3 scripts/pack_freeze.py --draft    # 退回 DRAFT（需用户显式指令）
  python3 scripts/pack_freeze.py --verify   # 只校验当前内容与 frozen_hash 是否一致
"""
import hashlib
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
# pack 位置两可：根 scenario-pack/（本库原态）或 standards/scenario-pack/（并入 sparkjury 后）。
# 取先存在的一个；两者内容相同时 frozen_hash 一致（hash 只覆盖 pack 内相对路径）。
PACK_DIR = next(
    (p for p in (_ROOT / "scenario-pack", _ROOT / "standards" / "scenario-pack") if p.is_dir()),
    _ROOT / "scenario-pack",
)
MANIFEST = PACK_DIR / "pack.manifest.json"
SKIP = {"pack.manifest.json"}


def compute_hash() -> str:
    h = hashlib.sha256()
    for p in sorted(PACK_DIR.rglob("*")):
        if p.is_dir() or p.name in SKIP:
            continue
        rel = p.relative_to(PACK_DIR).as_posix()
        h.update(rel.encode())
        h.update(p.read_bytes())
    return h.hexdigest()


def main() -> int:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    digest = compute_hash()

    if "--verify" in sys.argv:
        ok = manifest.get("frozen_hash") == digest
        print(("OK " if ok else "MISMATCH ") + f"{manifest.get('state')} hash={digest[:16]}")
        return 0 if ok else 1

    if "--draft" in sys.argv:
        if manifest.get("state") != "FROZEN":
            print("当前已是 DRAFT"); return 0
        manifest.update(state="DRAFT", frozen_hash=None, frozen_at=None)
        MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"已退回 DRAFT（内容 hash={digest[:16]}）。回 DRAFT 后不得用于回归对比。")
        return 0

    manifest.update(
        state="FROZEN",
        frozen_hash=digest,
        frozen_at="2026-09-26",  # 冻结日期由人工确认，脚本不读系统时间，保证可复现
    )
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"FROZEN pack={manifest['pack_id']} v{manifest['version']} hash={digest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
