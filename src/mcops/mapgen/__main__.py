"""命令行：python -m mcops.mapgen build --cache DIR --out DIR [--jar client.jar]

由 agent 在低优先级子进程里调用（nice/ionice），渲染完退出，内存随进程释放。
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import asdict
from pathlib import Path

from mcops.mapgen.build import build, preview


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m mcops.mapgen")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="增量渲染瓦片")
    b.add_argument("--cache", type=Path, required=True)
    b.add_argument("--out", type=Path, required=True)
    b.add_argument("--jar", type=Path)
    b.add_argument("--hot-ticks", type=int, default=72000)
    b.add_argument("--workers", type=int, default=2)
    b.add_argument("--world-version")
    p = sub.add_parser("preview", help="把一级瓦片拼成一张图")
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--level", type=int, default=2)
    p.add_argument("dest", type=Path)
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if a.cmd == "build":
        st = build(a.cache, a.out, a.jar, a.hot_ticks, a.workers, a.world_version)
        # 最后一行是 JSON，agent 解析它记录统计
        print(json.dumps(asdict(st), ensure_ascii=False))
    else:
        preview(a.out, a.level, a.dest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
