"""`DataBaseCompiled` 每张表的**字段级加密**（零 Qt）。

容器对象里，24 张表各自是一个字段，形状完全相同：

    [int32 长度][ base64( magic(8B) + IV(16B) + AES-256-CBC 密文 ) ]

四个细节全部由实测钉死（见 kb/11 与 _work/verify_container_roundtrip.py）：

1. **密钥是硬编码常量**，没有 KDF、没有盐。游戏自己就是这么写的，
   所以这不是“我们选的弱加密”，而是“必须照抄的格式”。
2. **magic 是 8 字节 ASCII** `fhk3s0g3`，解密时必须逐字节核对 ——
   它是判断“这个字段到底是不是加密表”的唯一可靠标志。
3. **IV 跟随字段本身**（就在 magic 后面），所以重新加密不需要任何外部材料。
   反过来：**想让未改动的表逐字节复现原版，就必须把原 IV 存下来**。
4. **填充是 PKCS#7**，实测 pad 取值 1..16（明文长度恰好整除 16 时补整块 16）。

⚠️ 本模块**不负责**长度前缀（那是 `container.py` 的事），也不负责对象头/尾。
"""

from __future__ import annotations

import base64
import binascii
import os

from Crypto.Cipher import AES

from ba_mod_maker.core.errors import DataSourceError

#: **AES-256** 密钥。**这是游戏二进制里的常量，不是本产品的选择**（kb/11 §3）。
#:
#: ⚠️ **别用眼睛数字符**。这串是 **32 个 ASCII 字符 = 32 字节**，
#: pycryptodome 按 len(key) 选 128/192/256，所以这里走的是 **AES-256-CBC**。
#:
#: 踩过一次：有一轮把它数成 16 个字符，于是写下"AES-128、旧知识库记错了"的结论，
#: 还写进了 kb/12。旧知识库写的 AES-256 是**对的**，错的是那次复核。
#: 判据很简单：`len(dbcryp.KEY)` —— 数字节要用 len()，不要数。
KEY = b"09234237536700238099172758697347"

#: 每个加密字段开头的 8 字节标志。
MAGIC = b"fhk3s0g3"

#: AES 分组长度；填充与 IV 都以它为单位。
BLOCK = 16

#: IV 长度。
IV_LEN = 16


def new_iv() -> bytes:
    """生成一个新 IV。"""
    return os.urandom(IV_LEN)


def pad(plain: bytes) -> bytes:
    """PKCS#7 填充到 16 的整数倍。"""
    n = BLOCK - len(plain) % BLOCK
    return plain + bytes([n]) * n


def unpad(padded: bytes) -> bytes:
    """去掉 PKCS#7 填充。填充不合法时抛 `DataSourceError`。"""
    if not padded or len(padded) % BLOCK:
        raise DataSourceError(
            f"密文长度不是 {BLOCK} 的整数倍",
            hint="该字段不是 AES-CBC 加密的，或文件已损坏",
            length=len(padded),
        )
    n = padded[-1]
    if not 1 <= n <= BLOCK or padded[-n:] != bytes([n]) * n:
        raise DataSourceError(
            "PKCS#7 填充不合法",
            hint="密钥不对，或这段字节根本不是加密字段",
            pad_byte=n,
        )
    return padded[:-n]


def encrypt(plain: bytes, iv: bytes | None = None) -> bytes:
    """把明文加密成完整的**字段字节**（含 magic 与 IV）。

    返回的是 base64 **之前**的字节；`container.py` 再把它 base64 并加长度前缀。

    参数
    ----
    plain
        明文（UTF-8 的 JSON 数组字节）。
    iv
        复用已有 IV。传 `None` 表示新生成一个。
        **复用 IV 只有一个理由**：让未改动的表逐字节复现原版。
    """
    iv = iv if iv is not None else new_iv()
    if len(iv) != IV_LEN:
        raise ValueError(f"IV 必须是 {IV_LEN} 字节，收到 {len(iv)}")
    return MAGIC + iv + AES.new(KEY, AES.MODE_CBC, iv).encrypt(pad(plain))


def decrypt(field: bytes) -> tuple[bytes, bytes]:
    """解密**字段字节**，返回 `(明文, IV)`。

    返回 IV 是为了让调用方能在“没改这张表”时原样复用它，
    从而让整份数据做到逐字节可逆。
    """
    if len(field) < len(MAGIC) + IV_LEN + BLOCK:
        raise DataSourceError(
            f"加密字段太短：只有 {len(field)} 字节",
            hint="最小长度是 magic(8) + IV(16) + 一个分组(16)",
        )
    if field[: len(MAGIC)] != MAGIC:
        raise DataSourceError(
            "加密字段开头的 magic 不对",
            hint=f"期望 {MAGIC!r}，实际 {field[: len(MAGIC)]!r}",
        )
    iv = field[len(MAGIC) : len(MAGIC) + IV_LEN]
    body = field[len(MAGIC) + IV_LEN :]
    return unpad(AES.new(KEY, AES.MODE_CBC, iv).decrypt(body)), iv


def encode_b64(field: bytes) -> bytes:
    """字段字节 -> base64（容器里存的就是它）。"""
    return base64.b64encode(field)


def decode_b64(text: bytes) -> bytes:
    """base64 -> 字段字节。非法 base64 转成可展示的错误。"""
    try:
        return base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise DataSourceError(
            "base64 解码失败",
            hint="该字段的内容不是合法 base64，文件可能被截断或改坏",
            error=str(exc),
        ) from None
