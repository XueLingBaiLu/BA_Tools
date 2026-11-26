"""`DataBaseCompiled` **容器对象**的结构与序列化（零 Qt）。

一个容器对象在 `data.unity3d` 里长这样（实测，两个实例形状完全相同）：

    [48 B 对象头][ 24 个表字段 ][ 4 B 对象尾]

    对象头 = PPtr(m_Script) + 1 + MonoScript m_PathID + 字符串长度(16) + "DataBaseCompiled"
    表字段 = [int32 长度][ base64( magic + IV + AES-CBC 密文 ) ]，4 字节对齐
    对象尾 = int32 Level（实测恒为 2）

## 为什么对象头/尾是**原样字节**而不是“字段”

因为这个对象的字段布局我们**没有**完整对应的 C# 类定义（只有反汇编里的零散读数），
而“猜字段再拼回去”一旦猜错就是静默产出坏文件。原样存字节则**不可能猜错**，
代价只是 JSON 里多 52 字节的十六进制。

## DataBaseCompiled.json 的形状

```json
{
  "format": "ba-mod-maker/dbc-json",
  "format_version": 1,
  "source_object_sha256": "9f45814f…",
  "object_head": "00000000000000000000000001000000…",
  "object_tail": "02000000",
  "tables": [
    {
      "field": "Units",
      "table": "Units",
      "zip_entry": "Units.json",
      "iv": "22f5fe95ae89452b1aad9f9045fda051",
      "pad": 14,
      "data": [[...], ...]
    }
  ]
}
```

- `field` 是**容器的字段名**（带 Json 后缀那套），同时决定字段顺序；
- `table` 是**数据表名**（zip 条目名的基名）；
- `zip_entry` 是导出 BA_Mod.zip 时的条目名。实测它与 `table + ".json"` 只差一处：
  表名 `FlyPresets` → 真实条目 `PlaneFlyPresets.json`，所以**显式存下来**而不是现推；
- `iv` + `pad` 是“让未改动的表逐字节复现原版”的全部信息。

## 保留 IV 与 pad 的规则（重要）

| 情况 | iv | pad |
|---|---|---|
| 表**没被动过** | 保留原值 → 密文逐字节复现 | 保留原值 |
| 表**被改过** | 换新 IV（防止同 IV 下明文-密文对照） | 按新明文长度重算 |

游戏只解明文，所以两种都能读；差别只在“产物字节是否与原版一致”。
"""

from __future__ import annotations

import base64
import hashlib
import json
import struct
from dataclasses import dataclass

from ba_mod_maker.core.asset import dbcryp
from ba_mod_maker.core.errors import DataSourceError

#: DataBaseCompiled.json 的格式标识与版本。
FORMAT_ID = "ba-mod-maker/dbc-json"
FORMAT_VERSION = 1

#: 字段长度前缀的宽度。
_LEN_PREFIX = 4

#: 对象头长度（1.2.0 实测值，两个实例完全相同），取值见 _work/verify_container_roundtrip.py：
#: [0:16] PPtr(m_Script) 全 0 ／ [16:20] 1 ／ [20:28] m_PathID ／ [28:32] 名字长度 ／ [32:] 名字。
#:
#: ⛔ **别再用它当解析锚点** —— 1.2.1 起这个头是 **52** 字节（实测：5 个对象都是 24 个字段，
#:    尾部恰 4 字节 `02000000`；用 48 解则 0 个字段 ⇒ 报「对象里找不到任何加密表字段」）。
#:    「多出来的那个 int32」= [28:32] 名字长度前面的 PPtr 后半段 —— Unity 跟着引擎版本序列化 PPtr，
#:    **长度会再变** ⇒ 一切靠 `_header_len()` **按名字现推**（见它的 docstring）。
#:    这个常量只剩两个用途：老 JSON 缺 `head_len` 时的兜底、以及老文档/老用例的引用。
HEAD_LEN = 48

#: 探测头长度时最多往后看这么多字节（头只有几十字节，给足余量）。
_HEAD_SCAN = 512


def _header_len(blob: bytes) -> int | None:
    """现推对象头长度（找不到返回 None）。

    **为什么不写死**：头 = PPtr(m_Script) + 1 + MonoScript 的 m_PathID + 字符串(名字)，
    而 PPtr 的长度**跟着引擎版本走**。实测 1.2.0 = 48（m_PathID 13249）· **1.2.1 = 52**
    （m_PathID 13535，多一个 int32）。写死一个数字 = 下次更新再断一次。

    判据（不猜）：头里那个名字是**长度前缀字符串** —— 名字前 4 字节就是它的长度。
    ⇒ 找到名字的落点，往前读 4 字节对不上就整体后移 4 字节（PPtr 增长的那一格），
    两端各自与 :data:`OBJECT_NAME` 逐字节核对过才算数。
    """
    at = blob.find(OBJECT_NAME, 0, _HEAD_SCAN)
    while at >= 0:
        for head_len in (at, at + _LEN_PREFIX):
            if head_len < _LEN_PREFIX or head_len > len(blob):
                continue
            (name_len,) = struct.unpack_from("<i", blob, head_len - _LEN_PREFIX)
            if name_len != len(OBJECT_NAME):
                continue
            if blob[head_len : head_len + len(OBJECT_NAME)] != OBJECT_NAME:
                continue
            if not _looks_like_a_table_field(blob, head_len):
                continue
            return head_len
        # 兜底：夹具/老包的"名字长度前缀 = 16，但真正对齐到 4 的第一格不是 16"（见
        # _looks_like_a_table_field）。挨个往后试，**第一个像样的表字段就是头结束处**。
        for head_len in range(at, min(at + 64, len(blob))):
            if _looks_like_a_table_field(blob, head_len):
                return head_len
        at = blob.find(OBJECT_NAME, at + 1, _HEAD_SCAN)
    return None


def _looks_like_a_table_field(blob: bytes, pos: int) -> bool:
    """`pos` 处是不是一个**像样的表字段**（[int32 长度][base64(magic+…)]，4 字节对齐）。

    用来把"名字碰巧出现在头里"这种巧合剔掉；字节不够也当失败（宁可报错也不产出错位对象）。
    """
    if pos + _LEN_PREFIX > len(blob):
        return False
    (b64_len,) = struct.unpack_from("<i", blob, pos)
    if b64_len <= 0 or b64_len % 4 or pos + _LEN_PREFIX + b64_len > len(blob):
        return False
    body = blob[pos + _LEN_PREFIX : pos + _LEN_PREFIX + b64_len]
    return body.startswith(base64.b64encode(dbcryp.MAGIC)[:4])

#: 对象头里嵌入的名字，用作"这确实是个容器对象"的自检。
OBJECT_NAME = b"DataBaseCompiled"


def _align4(n: int) -> int:
    return n + (-n % 4)


def _raw_text(data: bytes) -> str:
    """把明文字节原样当成字符串交给 json.dumps。

    这样 `DataBaseCompiled.json` 里那张表的 "data" 是一个**字符串**，
    内容是容器里逐字节相同的文本 —— 读回来时再 encode 就还原了。
    """
    return data.decode("utf-8")


@dataclass(slots=True)
class TableSlot:
    """容器里的一个表字段。`data` 是**已解密的明文**（就是磁盘上那个 JSON 文件的内容）。"""

    field: str
    table: str
    zip_entry: str
    data: bytes
    iv: bytes
    pad: int
    #: 它在原对象里的字段偏移。只读信息，重建时按顺序算出，不用它定位。
    offset: int = -1
    #: 这张表是否被编辑过。**只有 True 的表才会重新序列化**，见 set_payload。
    dirty: bool = False

    @property
    def plain_len(self) -> int:
        return len(self.data)

    def json_payload(self) -> object:
        """明文解析成 Python 对象（给界面与校验器用）。"""
        try:
            return json.loads(self.data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DataSourceError(
                f"表 {self.table} 的明文不是合法 UTF-8 JSON",
                hint="文件可能被截断或改坏",
                error=str(exc),
            ) from None

    def set_payload(self, payload: object) -> None:
        """用编辑后的数据替换明文，并标脏。

        ⚠️ **为什么要有"脏"这个标志**（实测得出的必要设计）：

        容器的明文 JSON 不是 Python 的规范排版。实测 `Mobility` 里有
        `"LoiteringTime":1.5E+08`，而 Python 的 `json` 只会输出
        `150000000.0` —— 长度从 117140 变 117150。值没变，**字节变了**。

        于是：没被编辑过的表，明文按原样字节留着（逐字节可逆）；
        只有真的改过的表才重新序列化（长度本来就要变）。两者互不干扰。
        """
        self.data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.dirty = True


def slot_with_data(tpl: TableSlot, data: bytes) -> TableSlot:
    """按模板形状造一个 slot —— **保留 IV 的规则只此一份**（见模块头那张表）。

    表没被动过 → 复用原 IV（密文逐字节复现原版）；被改过 → 换新 IV。
    `pad` 只是照抄模板用于报告与对账：真正的 pad 由明文长度决定（见 :meth:`ObjectMeta.to_bytes`）。

    ⚠️ 这个函数存在的唯一理由是**消除重复**：从前 session.merge_into_template 与
    dbzip.DbZip.to_object 各写了一份一模一样的判断，改一处忘一处就会产出
    「部分表沿用旧 IV」的容器 —— 它不报错，只是逐字节复现静默失效，最难查。
    """
    same = data == tpl.data
    return TableSlot(
        field=tpl.field,
        table=tpl.table,
        zip_entry=tpl.zip_entry,
        data=data,
        iv=tpl.iv if same else dbcryp.new_iv(),
        pad=tpl.pad,
        dirty=not same,
    )


@dataclass(slots=True)
class ObjectMeta:
    """一个 `DataBaseCompiled` 容器对象的**全部**内容。

    这是本产品的核心数据模型：`data.unity3d` / `DataBaseCompiled.json` 都是它的
    不同序列化形式，`BA_Mod.zip` 是它去掉加密与元数据后的那 24 张表。
    """

    object_head: bytes
    object_tail: bytes
    tables: list[TableSlot]
    #: 头长度（1.2.0 = 48 · 1.2.1 = 52）。**解析时现推**（见 `_header_len`），
    #: 写回时按它切 `object_head` —— 不写死一个数字，游戏下次改了头也不会写坏。
    #: 老 `DataBaseCompiled.json`（没有这一格）读回来是 None，届时按 `len(object_head)` 走。
    head_len: int | None = None
    #: 它来自哪个 pathID（用于“取 pathID 最大的那个”）。
    path_id: int | None = None
    #: 对象头部在容器节点里的偏移（写回 data.unity3d 时要用）。
    byte_start: int | None = None
    #: 原对象的 sha256。用于“写前核对”与报告“这份数据被改过没有”。
    source_sha256: str = ""

    # ---------------- 读取 ----------------

    @classmethod
    def parse(cls, blob: bytes, *, path_id: int | None = None) -> ObjectMeta:
        """把一个完整的容器对象字节解成 `ObjectMeta`。

        结构是**边解边验**的：每一步都断言自己落在预期位置，
        宁可在这里报错，也不要产出一个“看起来对、其实错位”的对象。
        """
        if len(blob) < HEAD_LEN + 8:
            raise DataSourceError(
                f"容器对象太短：{len(blob)} 字节",
                hint="DataBaseCompiled 对象至少几百 KB，这么短说明取错了区段",
                path_id=path_id,
            )
        # ★ 头长度**现推**，不写死（1.2.0 = 48 · 1.2.1 = 52，见 _header_len）。
        head_len = _header_len(blob)
        if head_len is None:
            raise DataSourceError(
                "这个对象不是 DataBaseCompiled",
                hint=f"前 {_HEAD_SCAN} 字节里找不到带长度前缀的 {OBJECT_NAME!r}，" 
                     "或它后面不是一个表字段",
                path_id=path_id,
                head=blob[:HEAD_LEN].hex(),
            )

        # 从头上之后**连续**解字段，直到再也解不动为止。
        pos = head_len
        slots: list[TableSlot] = []
        while pos + _LEN_PREFIX <= len(blob):
            (b64_len,) = struct.unpack_from("<i", blob, pos)
            # 合法字段：b64 长度为 4 的整数倍，且真的装得下。
            if b64_len <= 0 or b64_len % 4 or pos + _LEN_PREFIX + b64_len > len(blob):
                break
            body = blob[pos + _LEN_PREFIX : pos + _LEN_PREFIX + b64_len]
            # 快速否掉：真字段的 base64 一定以 magic 的开头开头。
            if not body.startswith(base64.b64encode(dbcryp.MAGIC)[:4]):
                break
            raw = dbcryp.decode_b64(body)
            plain, iv = dbcryp.decrypt(raw)
            slots.append(
                TableSlot(
                    field="",  # 由调用方按 manifest 填
                    table="",
                    zip_entry="",
                    data=plain,
                    iv=iv,
                    pad=len(raw) - len(dbcryp.MAGIC) - dbcryp.IV_LEN - len(plain),
                    offset=pos,
                )
            )
            pos += _align4(_LEN_PREFIX + b64_len)

        if not slots:
            raise DataSourceError(
                "对象里找不到任何加密表字段",
                hint="字段形状与预期不符；可能游戏版本变了",
                path_id=path_id,
            )

        return cls(
            object_head=blob[:head_len],
            head_len=head_len,
            object_tail=blob[pos:],
            tables=slots,
            path_id=path_id,
            source_sha256=hashlib.sha256(blob).hexdigest(),
        )

    # ---------------- 写出 ----------------

    def to_bytes(self) -> bytearray:
        """按字段顺序序列化回容器对象字节。

        未改动的表（`iv` 保留原值、`pad` 与明文长度自洽）会**逐字节复现**原版。
        """
        out = bytearray(self.object_head)
        for slot in self.tables:
            # pad 是明文长度的**函数**（PKCS#7 无参数），所以不需要"用哪个 pad"的决策：
            # 明文没变 → 算出来就等于原 pad，逐字节复现；明文变了 → 自动跟着变。
            # slot.pad 只用于报告与对账。
            ct = dbcryp.encrypt(slot.data, iv=slot.iv)
            b64 = dbcryp.encode_b64(ct)
            out += struct.pack("<i", len(b64)) + b64
            pad_bytes = -len(out) % 4
            if pad_bytes:
                out += bytes(pad_bytes)
        out += self.object_tail
        return out

    # ---------------- JSON 序列化（DataBaseCompiled.json）----------------

    def to_json_text(self) -> str:
        """序列化成 `DataBaseCompiled.json` 的内容。"""
        doc = {
            "format": FORMAT_ID,
            "format_version": FORMAT_VERSION,
            "source_object_sha256": self.source_sha256,
            "path_id": self.path_id,
            "head_len": len(self.object_head),
            "object_head": self.object_head.hex(),
            "object_tail": self.object_tail.hex(),
            "tables": [
                {
                    "field": s.field,
                    "table": s.table,
                    "zip_entry": s.zip_entry,
                    "iv": s.iv.hex(),
                    "pad": s.pad,
                    # 没编辑过的表：原样保留容器里的**文本**（见 TableSlot.set_payload 的说明）。
                    # 编辑过的表：用紧凑 JSON 重新序列化。
                    "data": s.json_payload() if s.dirty else _raw_text(s.data),
                }
                for s in self.tables
            ],
        }
        return json.dumps(doc, ensure_ascii=False, indent=1)

    @classmethod
    def from_json_text(cls, text: str) -> ObjectMeta:
        """从 `DataBaseCompiled.json` 的内容还原。"""
        try:
            doc = json.loads(text)
        except json.JSONDecodeError as exc:
            raise DataSourceError(
                "DataBaseCompiled.json 不是合法 JSON",
                hint="文件可能被截断",
                error=f"第 {exc.lineno} 行：{exc.msg}",
            ) from None
        if not isinstance(doc, dict) or doc.get("format") != FORMAT_ID:
            raise DataSourceError(
                "这不是本产品的 DataBaseCompiled.json",
                hint=f"它应当带 {FORMAT_ID!r} 标记",
                got=doc.get("format") if isinstance(doc, dict) else type(doc).__name__,
            )
        ver = doc.get("format_version")
        if ver != FORMAT_VERSION:
            raise DataSourceError(
                f"不支持的格式版本：{ver}",
                hint=f"本产品只认版本 {FORMAT_VERSION}",
            )
        tables = doc.get("tables")
        if not isinstance(tables, list) or not tables:
            raise DataSourceError("DataBaseCompiled.json 里没有表", hint="tables 为空或不是数组")

        slots: list[TableSlot] = []
        for i, item in enumerate(tables):
            if not isinstance(item, dict):
                raise DataSourceError(f"第 {i} 个表不是对象", hint="tables 的元素必须是对象")
            try:
                raw = item["data"]
                if isinstance(raw, str):
                    # 未编辑过的表：原样文本（见 TableSlot.set_payload）。
                    plain = raw.encode("utf-8")
                    dirty = False
                else:
                    # 已编辑过的表：结构化的数据，重新序列化。
                    text = json.dumps(raw, ensure_ascii=False, separators=(",", ":"))
                    plain = text.encode("utf-8")
                    dirty = True
                slots.append(
                    TableSlot(
                        field=str(item["field"]),
                        table=str(item["table"]),
                        zip_entry=str(item["zip_entry"]),
                        data=plain,
                        iv=bytes.fromhex(str(item["iv"])),
                        pad=int(item["pad"]),
                        dirty=dirty,
                    )
                )
            except (KeyError, ValueError, TypeError) as exc:
                raise DataSourceError(
                    f"第 {i} 个表的字段不完整或类型不对：{exc}",
                    hint="必需字段：field / table / zip_entry / iv / pad / data",
                ) from None

        head = bytes.fromhex(str(doc["object_head"]))
        return cls(
            object_head=head,
            head_len=int(doc.get("head_len") or len(head)),
            object_tail=bytes.fromhex(str(doc["object_tail"])),
            tables=slots,
            path_id=doc.get("path_id"),
            source_sha256=str(doc.get("source_object_sha256") or ""),
        )
