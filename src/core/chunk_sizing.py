"""
分块尺寸策略 — 按全文长度自适应，减少「固定窗口」在边界上的漏抽/重复。

说明：
- **主路径**是 Docling 版式流（iterate_items：标题/段落/表/公式分块），段落合并上限随全文长度变化；
- Docling 另有 **HybridChunker**（层次 + token 感知），若直接替换默认分块会丢失独立 table 块，故未默认接入；
- 此处提供「无语义 chunks 时」的字符滑动窗口与段落合并上限的数值策略。
"""

from __future__ import annotations

from typing import Tuple


def adaptive_docling_paragraph_cap(text_len: int) -> int:
    """Docling 段落合并为 chunk 时的单块字符上限（随文档变长略增大，减少切块数与边界）。"""
    if text_len <= 0:
        return 1600
    # 次线性增长，夹在合理区间内
    span = 1100.0 + 2.4 * (float(text_len) ** 0.42)
    return int(max(1200, min(2800, span)))


def adaptive_char_slice_params(text_len: int) -> Tuple[int, int, int]:
    """字符切片回退：返回 (slice_size, overlap, direct_threshold)。

    - direct_threshold：全文短于此则不做字符切片，整段一次模型调用；
    - slice_size / overlap：滑动窗口大小与重叠，随总长调整。
    """
    if text_len <= 0:
        return (2000, 120, 2200)
    # 短文整段处理，长文再切
    direct = min(5200, max(1600, int(800 + text_len * 0.028)))
    lo, hi = 1600, 4800
    span = 1250.0 + 3.4 * (float(text_len) ** 0.38)
    slice_sz = int(max(lo, min(hi, span)))
    overlap = int(max(72, min(520, slice_sz * 0.075)))
    return (slice_sz, overlap, direct)
