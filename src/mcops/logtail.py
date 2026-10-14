"""实时跟随 latest.log，处理服务端重启时的文件轮转（重命名为 .gz 后新建）与截断。"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from pathlib import Path


async def follow(path: Path, poll_s: float = 0.25, from_start: bool = False) -> AsyncIterator[str]:
    """逐行产出 path 的新增内容。文件不存在时等待；inode 变化或变短时从头重新读取。"""
    fh = None
    inode: int | None = None
    buf = b""
    try:
        while True:
            if fh is None:
                try:
                    fh = path.open("rb")
                except FileNotFoundError:
                    from_start = True  # 文件是之后才创建的：新文件要从头读
                    await asyncio.sleep(poll_s)
                    continue
                inode = os.fstat(fh.fileno()).st_ino
                if not from_start:
                    fh.seek(0, os.SEEK_END)
                from_start = True  # 之后的轮转都从新文件开头读
                buf = b""

            chunk = fh.read(65536)
            if chunk:
                buf += chunk
                *lines, buf = buf.split(b"\n")
                for ln in lines:
                    yield ln.decode("utf-8", errors="replace").rstrip("\r")
                continue

            # 没有新数据：检查是否轮转或截断
            st: os.stat_result | None
            try:
                st = path.stat()
            except FileNotFoundError:
                st = None
            pos = fh.tell()
            if st is None or st.st_ino != inode or st.st_size < pos:
                fh.close()
                fh = None
                continue
            await asyncio.sleep(poll_s)
    finally:
        if fh is not None:
            fh.close()
