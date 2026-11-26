"""合成 DLL 的字节 —— test_dllpatch 与 test_dll_patch_dialog 共用一份。

为什么单独一个文件：这两处原来各写了一遍"26 MB 逐字节生成"，而那个生成式是
纯 Python 循环，**实测 1.6s / 次**，两个文件加起来几乎每条用例都要付一次。
"逻辑只有一份"（AGENTS.md §5），所以提到这里；下面那一行等价性有实测守着。
"""

from __future__ import annotations

#: 地址表里最大的 cave 偏移，再加余量 —— 合成 DLL 至少要这么大。
MIN_DLL_SIZE = 0x1900000

#: (i * 7 + 13) % 251 的**一个周期**。
#: 251 是质数、且 7 不整除它 —— 所以 i 每走 251 步，取值就整段重复一次。
_PATTERN_PERIOD = 251
_PATTERN = bytes((i * 7 + 13) % 251 for i in range(_PATTERN_PERIOD))


def fake_dll_bytes(size: int = MIN_DLL_SIZE) -> bytes:
    """造一份"原版 DLL"字节：长度够、内容确定。

    与逐字节的 bytes((i * 7 + 13) % 251 for i in range(size)) **逐字节等价**
    （已对 0 / 1 / 250 / 251 / 252 / 1000 / 26 MB 七种长度核过）。
    26 MB 实测：约 1.6s -> 约 0.2ms。
    """
    buf = bytearray((_PATTERN * (size // _PATTERN_PERIOD + 1))[:size])
    # 2026-11-12：地址表里除 `sites` 之外还有**单字节改写**（`byte_patches`，如忽略 code 72）。
    # 那几处的"原版字节"必须真的是它声明的原值，否则 `patched_image` 的版本闸会拒 ——
    # 假 DLL 也要照做，否则用例测的是一份现实中不存在的"原版"。
    from ba_mod_maker.core.asset import dllpatch

    for build in dllpatch.BUILDS:
        for _name, va, orig, _new in build.byte_patches:
            off = va - build.base - 0xC00
            if 0 <= off < len(buf):
                buf[off] = orig
    return bytes(buf)


__all__ = ["MIN_DLL_SIZE", "fake_dll_bytes"]
