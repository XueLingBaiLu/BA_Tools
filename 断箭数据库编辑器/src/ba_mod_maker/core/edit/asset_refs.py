"""资产引用：一处改动**落在哪个文件的哪个对象上**（零 Qt，纯数据）。

## 为什么单独一个模块

[CellRef](refs.py) 管的是**数据库**那一侧（表名 / 行 Id / 列名）；资产侧要的是同一件事的
另一半（文件 / pathID / 字段或字节区间）。它会被 `core/edit/asset_changeset.py` 与将来的
资产写路径同时用到 —— 放进 `app/` 就会让 core 反向依赖界面
（`tests/test_core_layering.py` 用 AST 扫这条纪律）。

## 与 CellRef **同形**（ADR 0008 决定 5）

    container ↔ table      目标文件（bundle / catalog.json / data.unity3d）
    path_id   ↔ row_id     对象在包里的 pathID；**地址级**改动给 None
    field     ↔ column     字段路径 或 字节区间；**整对象级**改动给 None

⛔ **不许把包名塞进 `Change.table` 冒充表名**（ADR 0008 决定 3）：`app/shell/edit_session.py`
的 `_tables_to_write()` 会拿那个字符串去查 24 张表 —— 包名混进去就会被当成一张表。

## `field` 为什么必须分两级

数据库侧「改回原值 ⇒ 整条消失」靠的是 `old == new` 比得出来
（`core/edit/changeset.py:254-258`）。资产侧有两档落点，**值语义不一样**：

- [FieldPath] —— **有值语义**：typetree 路径，能显示旧值 / 新值，能判「改回去了」；
- [ByteSpan] —— **无值语义**：等长档的字节区间（例：材质槽那 8 字节的 PPtr）。它没有
  「值」可比，只能比字节。

混成一级字符串就会让两种口径互相冒充（设计稿 §2 的 ⚠），所以两级各自冻结、
**各自进账本键**（见 `AssetRef.key()`）。

## 三种落点各长什么样

    # 字段级（set）：材质槽那 8 字节
    AssetRef(Path("…/units.bundle"), 58076, ByteSpan(offset=12, size=8))
    # 字段级（set）：typetree 路径
    AssetRef(Path("…/units.bundle"), 58076, FieldPath("m_TexEnvs[Layer_…].second.m_PathID"))
    # 整对象级（add / delete）：field 为 None
    AssetRef(Path("…/units.bundle"), 9001)
    # 地址级（set）：catalog 条目的字段，pathID 为 None（键是地址字符串，见下）
    AssetRef(Path("…/catalog.json"), None, FieldPath("m_InternalIds[9001]"))

⛔ `path_id=None` 且 `field=None` = 「整个容器」，不是某个对象 —— 账本的对象动词
（`add_object` / `delete_object`）会拒绝它。**地址级新增 / 删除**（catalog 的
`add_address` / `remove_address`：键是地址字符串而不是 pathID）的形状**本步不定**，
设计稿 §7 把它排在 CLI 三动词之后。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

__all__ = ["AssetRef", "ByteSpan", "FieldPath"]


@dataclass(frozen=True, slots=True)
class FieldPath:
    """typetree 路径 —— **有值语义**的那一档（能显示旧值 / 新值）。"""

    path: str

    def __post_init__(self) -> None:
        if not isinstance(self.path, str):
            raise TypeError(f"字段路径必须是字符串，拿到 {type(self.path).__name__}")
        if not self.path.strip():
            raise ValueError("字段路径不能是空的：它要回答「这一处落在 typetree 的哪里」")

    def __str__(self) -> str:
        return self.path


@dataclass(frozen=True, slots=True)
class ByteSpan:
    """字节区间 —— **无值语义**的那一档（等长写回，例：材质槽那 8 字节）。"""

    offset: int
    size: int

    def __post_init__(self) -> None:
        for name, value in (("offset", self.offset), ("size", self.size)):
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"ByteSpan.{name} 必须是整数，拿到 {type(value).__name__}")
        if self.offset < 0:
            raise ValueError(f"字节区间的起点不能是负数：{self.offset}")
        if self.size <= 0:
            raise ValueError(f"字节区间的长度必须为正：{self.size}")

    def __str__(self) -> str:
        return f"@0x{self.offset:X}+{self.size}"


def _field_key(field: FieldPath | ByteSpan | None) -> tuple[object, ...] | None:
    """字段那一格进账本键时的**带标签**写法（见模块头「为什么分两级」）。"""
    if field is None:
        return None
    if isinstance(field, FieldPath):
        return ("path", field.path)
    return ("bytes", field.offset, field.size)


@dataclass(frozen=True, slots=True)
class AssetRef:
    """一处资产改动的落点：哪个文件、哪个对象、哪个字段（或哪段字节）。"""

    container: Path
    path_id: int | None = None
    field: FieldPath | ByteSpan | None = None

    def __post_init__(self) -> None:
        # ---- container：两种写法必须归一到**同一个**账本键（否则同一次改动会记成两条）----
        if isinstance(self.container, str):
            if not self.container.strip():
                raise ValueError("container 不能是空路径：它是账本键的第一格（哪个文件）")
            object.__setattr__(self, "container", Path(self.container))
        elif not isinstance(self.container, Path):
            raise TypeError(
                f"container 只能是 Path 或 str，拿到 {type(self.container).__name__}"
                "：两种写法要归一到同一个键，第三种写法会造出第二个键"
            )
        if not str(self.container).strip():
            raise ValueError("container 不能是空路径：它是账本键的第一格（哪个文件）")

        # ---- path_id：Unity 的 m_PathID 是 SInt64，**可以为负**，所以只查类型不查正负 ----
        if self.path_id is not None and (
            isinstance(self.path_id, bool) or not isinstance(self.path_id, int)
        ):
            raise TypeError(
                f"path_id 必须是整数或 None，拿到 {type(self.path_id).__name__}"
                "（bool 不算整数：它是 0/1，会静默指到别的对象上）"
            )

        # ---- field：只认两级里的那两级 ----
        if self.field is not None and not isinstance(self.field, (FieldPath, ByteSpan)):
            raise TypeError(
                f"field 只能是 FieldPath（有值语义）或 ByteSpan（等长档），"
                f"拿到 {type(self.field).__name__}"
            )

    @property
    def is_whole_object(self) -> bool:
        """整对象级（新增 / 删除）—— `field` 为 None。"""
        return self.field is None

    def key(self) -> tuple[Path, int | None, tuple[object, ...] | None]:
        """账本键 = **容器 + path_id + 字段或类别**（ADR 0008 决定 3）。"""
        return (self.container, self.path_id, _field_key(self.field))

    def __str__(self) -> str:
        """人话一行：`units.bundle#58076.m_TexEnvs[…]`。

        〈为什么只报文件名〉账本键用的是**整个 Path**（同一个名字落在两个目录里是两个
        不同的键）；这里只负责给人看，文件名才是用户在界面上认得的那个。
        """
        text = self.container.name or str(self.container)
        if self.path_id is not None:
            text += f"#{self.path_id}"
        if self.field is not None:
            text += f".{self.field}"
        return text
