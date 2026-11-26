"""「导入到 data.unity3d」的测试。

导入天然需要**两个**文件（来源 + 目标），所以它有独立的对话框。
这里守两件事：

1. **预演与执行走同一套合并逻辑** —— 各写一套会让预演说假话，
   而"预演说 A、实际做 B"是最难查的一类 bug；
2. 来源形状的校验（BA_Mod.zip 需要模板、容器不能导入容器）。

⚠️ 不直接测 `preview_import` / `import_into_container` 的全流程：
它们要求目标是**真容器**（`open_source` 按文件内容判断，不认合成的 JSON），
造真容器要 6.6 GB。那两层由离屏冒烟与 CLI 的 --dry-run 覆盖。
这里测它们**共用的那几层**，也就是真正有逻辑的地方。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ba_mod_maker.core import session
from ba_mod_maker.core.asset import container, dbzip
from ba_mod_maker.core.asset.tables import TABLES
from ba_mod_maker.core.errors import UserFacingError


def _meta(cost: int, *, path_id: int = 52085) -> container.ObjectMeta:
    """一个合成的容器对象（内存里，不落盘）。"""
    name = b"DataBaseCompiled"
    head = (
        bytes(16)
        + bytes(4)
        + (13249).to_bytes(8, "little")
        + len(name).to_bytes(4, "little")
        + name
    )
    slots = []
    for naming in TABLES:
        payload = [{"Id": 1, "Cost": cost}] if naming.table == "Units" else [{"Id": 1}]
        slots.append(
            container.TableSlot(
                field=naming.field,
                table=naming.table,
                zip_entry=naming.zip_entry,
                data=json.dumps(payload, separators=(",", ":")).encode(),
                iv=bytes(range(16)),
                pad=0,
            )
        )
    return container.ObjectMeta(
        object_head=head, object_tail=bytes(4), tables=slots, path_id=path_id
    )


def _write_json(tmp_path: Path, meta: container.ObjectMeta, name: str = "in.json") -> Path:
    path = tmp_path / name
    path.write_text(meta.to_json_text(), encoding="utf-8")
    return path


def _write_zip(tmp_path: Path, meta: container.ObjectMeta, name: str = "in.zip") -> Path:
    path = tmp_path / name
    dbzip.DbZip.from_object(meta).write(path)
    return path


# --------------------------------------------------------------------------- #
# load_incoming：来源的形状要求
# --------------------------------------------------------------------------- #


def test_load_incoming_accepts_dbc_json_without_template(tmp_path) -> None:
    """DataBaseCompiled.json **自带形状**，不需要模板。"""
    path = _write_json(tmp_path, _meta(55))
    incoming, kind = session.load_incoming(path)
    assert kind is session.SourceKind.DBC_JSON
    assert len(incoming.tables) == 24
    assert incoming.object_head[:4] == bytes(4)


def test_load_incoming_requires_template_for_dbzip(tmp_path) -> None:
    """**这条是防"拿 BA_Mod.zip 直接写容器"的**：它没有对象头/尾与 IV，必须给模板。"""
    path = _write_zip(tmp_path, _meta(55))
    with pytest.raises(UserFacingError) as excinfo:
        session.load_incoming(path, template=None)
    assert "形状模板" in str(excinfo.value)
    # 给了模板就能用
    incoming, kind = session.load_incoming(path, template=_meta(55))
    assert kind is session.SourceKind.DB_ZIP
    assert len(incoming.tables) == 24


def test_load_incoming_rejects_container(tmp_path) -> None:
    """不能把容器导入容器 —— 要说人话。"""
    target = tmp_path / "game.unity3d"
    target.write_bytes(b"UnityFS\x00" + bytes(64))
    with pytest.raises(UserFacingError) as excinfo:
        session.load_incoming(target)
    # 这一步先撞在 detect_kind 的"认不出"上，措辞不同但同样是 UserFacingError
    assert "认不出" in str(excinfo.value) or "不能把容器导入容器" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# merge_into_template：只取数据、形状用模板的
# --------------------------------------------------------------------------- #


def test_merge_takes_shape_from_template_and_data_from_incoming() -> None:
    template = _meta(55, path_id=52085)
    incoming = _meta(7777, path_id=99999)
    merged = session.merge_into_template(template, incoming)
    assert merged.path_id == 52085, "pathID 必须用模板的"
    assert merged.object_head == template.object_head
    units = next(s for s in merged.tables if s.table == "Units")
    assert json.loads(units.data)[0]["Cost"] == 7777, "数据必须来自 incoming"


def test_merge_keeps_original_bytes_for_unchanged_tables() -> None:
    """内容没变的表保留**原始字节**，密文才能逐字节复现原版。"""
    template = _meta(55)
    incoming = _meta(55)
    merged = session.merge_into_template(template, incoming)
    for tpl, got in zip(template.tables, merged.tables, strict=True):
        assert got.data == tpl.data
        assert got.iv == tpl.iv, f"{tpl.table} 的 IV 不该换"
        assert got.dirty is False, f"{tpl.table} 不该被标脏"


def test_merge_marks_and_re_ivs_only_the_changed_table() -> None:
    template = _meta(55)
    incoming = _meta(7777)
    merged = session.merge_into_template(template, incoming)
    changed = [s for s in merged.tables if s.dirty]
    assert [s.table for s in changed] == ["Units"]
    units = changed[0]
    assert units.iv != next(s for s in template.tables if s.table == "Units").iv, (
        "改过的表要换新 IV"
    )
    # 没改的表 IV 必须原样
    other = next(s for s in merged.tables if s.table == "Weapons")
    other_tpl = next(s for s in template.tables if s.table == "Weapons")
    assert other.iv == other_tpl.iv


def test_merge_output_is_a_writable_container_object() -> None:
    """合并结果要能被序列化回容器字节（长度不变、可再解析）。"""
    template = _meta(55)
    incoming = _meta(7777)
    merged = session.merge_into_template(template, incoming)
    blob = bytes(merged.to_bytes())
    back = container.ObjectMeta.parse(blob)
    assert len(back.tables) == 24
    assert json.loads(back.tables[0].data)[0]["Cost"] == 7777
