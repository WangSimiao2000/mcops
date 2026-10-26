"""最小的 NBT 读取器（只读，大端，Java 版）。

长整型数组直接返回 numpy 视图，避免逐个转换；可以按根级键名跳过不需要的部分
（区块里的 block_entities、实体、计划刻等），只遍历不构造对象。
"""

from __future__ import annotations

import struct
from typing import Any

import numpy as np

_B = struct.Struct(">b")
_H = struct.Struct(">H")
_h = struct.Struct(">h")
_i = struct.Struct(">i")
_q = struct.Struct(">q")
_f = struct.Struct(">f")
_d = struct.Struct(">d")

END, BYTE, SHORT, INT, LONG, FLOAT, DOUBLE, BYTE_ARRAY, STRING, LIST, COMPOUND = range(11)
INT_ARRAY, LONG_ARRAY = 11, 12

_FIXED = {BYTE: 1, SHORT: 2, INT: 4, LONG: 8, FLOAT: 4, DOUBLE: 8}


class NBTError(ValueError):
    pass


class _Reader:
    __slots__ = ("buf", "pos")

    def __init__(self, buf: bytes | memoryview) -> None:
        self.buf = memoryview(buf)
        self.pos = 0

    def _string(self) -> str:
        (n,) = _H.unpack_from(self.buf, self.pos)
        start = self.pos + 2
        self.pos = start + n
        # Java 用的是 modified UTF-8；方块名都是 ASCII，surrogateescape 兜底不报错
        return bytes(self.buf[start : self.pos]).decode("utf-8", "surrogateescape")

    def payload(self, tag: int) -> Any:
        buf, pos = self.buf, self.pos
        if tag == BYTE:
            self.pos = pos + 1
            return _B.unpack_from(buf, pos)[0]
        if tag == SHORT:
            self.pos = pos + 2
            return _h.unpack_from(buf, pos)[0]
        if tag == INT:
            self.pos = pos + 4
            return _i.unpack_from(buf, pos)[0]
        if tag == LONG:
            self.pos = pos + 8
            return _q.unpack_from(buf, pos)[0]
        if tag == FLOAT:
            self.pos = pos + 4
            return _f.unpack_from(buf, pos)[0]
        if tag == DOUBLE:
            self.pos = pos + 8
            return _d.unpack_from(buf, pos)[0]
        if tag == STRING:
            return self._string()
        if tag == BYTE_ARRAY:
            (n,) = _i.unpack_from(buf, pos)
            self.pos = pos + 4 + n
            return bytes(buf[pos + 4 : self.pos])
        if tag == INT_ARRAY:
            (n,) = _i.unpack_from(buf, pos)
            self.pos = pos + 4 + 4 * n
            return np.frombuffer(buf[pos + 4 : self.pos], dtype=">i4")
        if tag == LONG_ARRAY:
            (n,) = _i.unpack_from(buf, pos)
            self.pos = pos + 4 + 8 * n
            return np.frombuffer(buf[pos + 4 : self.pos], dtype=">i8")
        if tag == LIST:
            sub = buf[pos]
            (n,) = _i.unpack_from(buf, pos + 1)
            self.pos = pos + 5
            return [self.payload(sub) for _ in range(n)]
        if tag == COMPOUND:
            return self.compound()
        raise NBTError(f"未知 NBT 标签 {tag}")

    def skip(self, tag: int) -> None:
        buf, pos = self.buf, self.pos
        if tag in _FIXED:
            self.pos = pos + _FIXED[tag]
        elif tag == STRING:
            self.pos = pos + 2 + _H.unpack_from(buf, pos)[0]
        elif tag == BYTE_ARRAY:
            self.pos = pos + 4 + _i.unpack_from(buf, pos)[0]
        elif tag == INT_ARRAY:
            self.pos = pos + 4 + 4 * _i.unpack_from(buf, pos)[0]
        elif tag == LONG_ARRAY:
            self.pos = pos + 4 + 8 * _i.unpack_from(buf, pos)[0]
        elif tag == LIST:
            sub = buf[pos]
            (n,) = _i.unpack_from(buf, pos + 1)
            self.pos = pos + 5
            if sub in _FIXED:
                self.pos += _FIXED[sub] * n
            else:
                for _ in range(n):
                    self.skip(sub)
        elif tag == COMPOUND:
            while True:
                t = buf[self.pos]
                self.pos += 1
                if t == END:
                    return
                self.pos += 2 + _H.unpack_from(buf, self.pos)[0]
                self.skip(t)
        else:
            raise NBTError(f"未知 NBT 标签 {tag}")

    def compound(self, only: frozenset[str] | None = None) -> dict[str, Any]:
        out: dict[str, Any] = {}
        buf = self.buf
        while True:
            t = buf[self.pos]
            self.pos += 1
            if t == END:
                return out
            name = self._string()
            if only is not None and name not in only:
                self.skip(t)
            else:
                out[name] = self.payload(t)


def loads(data: bytes | memoryview, only: frozenset[str] | None = None) -> dict[str, Any]:
    """解析未压缩的 NBT（根必须是 compound）。``only`` 只保留这些根级键。"""
    r = _Reader(data)
    if r.buf[0] != COMPOUND:
        raise NBTError("根标签不是 compound")
    r.pos = 1
    r._string()  # 根名，通常为空
    return r.compound(only)
