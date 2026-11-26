"""把一个单位（或几颗材质）**绑的所有贴图**导成 PNG ＋ 一份 `manifest.json`（只读）。

## 为什么有这个模块（2026-10-17 用户点名）

> 「你又把贴图导出来了，那我要看别的，你没导出来，还要我让你再导一遍吗？」

⇒ 导图这件事**不应该是每次现写一个脚本**。这里做成产品入口：给单位键（或材质名），
一次性把**这一个件用到的每一张贴图**落到一个目录里，附带 manifest（槽名 / 贴图名 / 尺寸 /
格式 / 颜色空间 / sha256），并可选拼一张接触表。

⛔ 只读：不写游戏目录、不碰包、只写 `--out`。
⛔ 纹素不在包里（流式贴图落在别的包 / 缺失）那种，**如实记 `missing=True`**，不静默给一张黑图。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = ["DumpRow", "DumpReport", "dump_unit_textures"]


@dataclass(frozen=True, slots=True)
class DumpRow:
    """一张导出来的贴图（或一张导不出来的槽，`rgba` 为空）。"""

    material: str
    slot: str
    texture_id: int
    texture_name: str = ""
    width: int = 0
    height: int = 0
    format_name: str = ""
    color_space: str = ""
    sha256: str = ""
    missing: bool = False
    note: str = ""
    rgba: bytes = field(default=b"", repr=False)


@dataclass(frozen=True, slots=True)
class DumpReport:
    unit_key: str
    asset_path: str
    bundle: str
    materials: tuple[str, ...]
    rows: tuple[DumpRow, ...]

    @property
    def dumped(self) -> int:
        return sum(1 for row in self.rows if not row.missing)



def _unit_material_entries(source: Any, asset_path: str) -> tuple[dict, ...]:
    """走 prefab 层级，把每个渲染器槽里的材质读成**导入链同一份 entry**（去重）。

    ⚠ 材质的 PPtr 可能是**跨包**的（`m_FileID != 0`，实测 Yak130 就是）⇒ 不能只记 pathID
    再回本包 `read_typetree`（会 `KeyError`），也不能 `ptr.read()`（不认 cab 索引）。
    这里**复用导入链的 `_collect_material`**：它已经处理好跨包 open ＋ PPtr 解析，
    ⛔ 不另写一套读法（唯一入口原则）。
    """
    entries = dict(source.container_entries())
    holder = source.object_at(int(entries[asset_path]))
    root = holder.read()
    collected: dict[int, dict] = {}

    def visit(game_object: Any) -> None:
        transform = game_object.m_Transform.read()
        for pair in game_object.m_Component or ():
            component = pair.component.read()
            for ptr in getattr(component, "m_Materials", None) or ():
                source.collect_material(ptr, collected)
        for child in transform.m_Children or ():
            visit(child.read().m_GameObject.read())

    visit(root)
    return tuple(collected.values())


def dump_unit_textures(
    game_root: str | Path,
    unit_key: str,
    *,
    only_materials: tuple[str, ...] = (),
) -> DumpReport:
    """导出一个单位 prefab 上**每一颗材质每一张绑定贴图**的像素（返回，不落盘）。"""
    from ba_mod_maker.core.assets.catalog import AddressablesCatalog  # noqa: PLC0415
    from ba_mod_maker.core.assets.game import aa_dir  # noqa: PLC0415

    from . import unitypy_source  # noqa: PLC0415

    catalog = AddressablesCatalog.load(aa_dir(Path(game_root)))
    hits = catalog.lookups(unit_key)
    assets = sorted({asset for asset, _group in hits})
    bundles = tuple(sorted({group for _asset, group in hits if group}))
    if not assets:
        raise ValueError(f"catalog 里没有这个单位键：{unit_key}")
    asset_path = assets[0]
    rows: list[DumpRow] = []
    names: list[str] = []
    pc_dir = aa_dir(Path(game_root)) / "PC"
    with unitypy_source.open_unique_source(pc_dir, bundles, asset_path, deps=True) as (
        source,
        bundle_name,
    ):
        for entry in _unit_material_entries(source, asset_path):
            material_name = str(entry.get("name") or "<material>")
            if only_materials and material_name not in only_materials:
                continue
            names.append(material_name)
            for slot, texture_id in entry.get("texture_slots") or ():
                if not texture_id:
                    continue
                try:
                    info = source.read_texture(texture_id)
                    rgba = source.decode_texture_rgba(texture_id)
                except Exception as exc:  # noqa: BLE001 -- 导不出来如实记，不静默
                    note = f"{type(exc).__name__}: {exc}"
                    rows.append(DumpRow(material_name, slot, texture_id, missing=True, note=note))
                    continue
                if not rgba:
                    rows.append(
                        DumpRow(material_name, slot, texture_id, missing=True, note="纹素不在包里")
                    )
                    continue
                rows.append(
                    DumpRow(
                        material=material_name,
                        slot=slot,
                        texture_id=texture_id,
                        texture_name=str(getattr(info, "name", "") or ""),
                        width=int(getattr(info, "width", 0) or 0),
                        height=int(getattr(info, "height", 0) or 0),
                        format_name=str(getattr(info, "format", "") or ""),
                        color_space=str(getattr(info, "color_space", "") or ""),
                        sha256=hashlib.sha256(rgba).hexdigest(),
                        rgba=rgba,
                    )
                )
    return DumpReport(
        unit_key=unit_key,
        asset_path=asset_path,
        bundle=bundle_name,
        materials=tuple(names),
        rows=tuple(rows),
    )

# --------------------------------------------------------------------------- #
# 出处（tests/test_kb_citations.py 要求的「能找回去的指针」）：
#   kb/21-贴图方向契约与素材管线.md（贴图槽 / 颜色空间 / 导出管线）
# --------------------------------------------------------------------------- #
