"""刃口照片：本地文件存储，不依赖任何外部服务。

实拍走 save_bytes()（Web 上传或采集端写入）；种子与演示用 synthesize_edge()
以纯标准库生成一张刃口示意 PNG（不锈钢底色 + 刃线，缺口表现为锯齿/崩口）。
"""
from __future__ import annotations

import struct
import zlib
from pathlib import Path

# 不锈钢白 / 炭灰 / 安全黄 / 警示红
_BG = (232, 234, 236)
_STEEL = (206, 212, 218)
_STEEL_DARK = (150, 156, 162)
_CHAR = (62, 66, 72)
_YELLOW = (242, 196, 47)
_RED = (200, 70, 58)


def _png(width: int, height: int, pixels: list[list[tuple[int, int, int]]]) -> bytes:
    raw = b""
    for row in pixels:
        raw += b"\x00" + b"".join(struct.pack("BBB", *p) for p in row)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def synthesize_edge(edge_state: str, width: int = 300, height: int = 96) -> bytes:
    """生成刃口示意照片：上半为刀身，底边为刃线。"""
    px = [[_BG for _ in range(width)] for _ in range(height)]
    blade_bottom = height - 26
    for y in range(10, blade_bottom):
        for x in range(8, width - 8):
            shade = _STEEL if (y + x) % 3 else _STEEL_DARK
            px[y][x] = shade
    # 刃线
    for x in range(8, width - 8):
        px[blade_bottom][x] = _CHAR
        px[blade_bottom + 1][x] = _CHAR

    if edge_state == "sharp":
        for x in range(8, width - 8, 6):  # 平直细刃
            px[blade_bottom + 2][x] = _STEEL_DARK
    elif edge_state == "acceptable":
        for x in range(8, width - 8, 9):  # 轻微起伏
            px[blade_bottom + 2][x] = _STEEL_DARK
            if (x // 9) % 4 == 0:
                px[blade_bottom - 1][x] = _STEEL_DARK
    elif edge_state == "dull":
        for x in range(8, width - 8):      # 圆钝、毛边
            if x % 3 == 0:
                px[blade_bottom - 2][x] = _STEEL_DARK
            px[blade_bottom + 2][x] = _STEEL_DARK
    elif edge_state == "chipped":
        # 两处明显崩口（安全黄标出），刃线缺失
        for cx in (95, 205):
            for dx in range(-7, 8):
                for dy in range(0, 7 - abs(dx) + 3):
                    yy = blade_bottom - dy
                    xx = cx + dx
                    if 0 <= xx < width and 0 <= yy < height:
                        px[yy][xx] = _YELLOW if (abs(dx) + dy) % 2 else _RED
        for x in range(cx - 10 if False else 8, width - 8, 11):
            px[blade_bottom + 3][x] = _RED
    return _png(width, height, px)


def photo_root(base_dir: str | Path) -> Path:
    root = Path(base_dir) / "photos"
    root.mkdir(parents=True, exist_ok=True)
    return root


def save_synthetic(base_dir: str | Path, knife_id: str, edge_state: str,
                   tag: str = "edge") -> str:
    root = photo_root(base_dir)
    safe = knife_id.replace("/", "_")
    path = root / f"{safe}_{tag}_{edge_state}.png"
    path.write_bytes(synthesize_edge(edge_state))
    return str(path.relative_to(Path(base_dir)))


def save_bytes(base_dir: str | Path, knife_id: str, data: bytes,
               tag: str = "edge", ext: str = "png") -> str:
    root = photo_root(base_dir)
    safe = knife_id.replace("/", "_")
    idx = len(list(root.glob(f"{safe}_{tag}_*.{ext}"))) + 1
    path = root / f"{safe}_{tag}_{idx}.{ext}"
    path.write_bytes(data)
    return str(path.relative_to(Path(base_dir)))
