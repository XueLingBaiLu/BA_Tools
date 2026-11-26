r"""整包 PPtr 普查 (`unit_bundle.audit_package_pptrs`) 的判据。

**判据一行**：pytest tests/test_pptr_audit.py -q -n 0 -> 通过数 0 -> N，其中 **1 条是负控**。

它补的是模块头里如实写过的那个洞：`builder.dangling_pptrs` 只普查有字段树的对象，
**payload 对象（Shader / Mesh / 贴图）一直是盲区**。夹具 =
`_work/w10_ref/units_meta.bin`（真件 units 的元数据区）。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from ba_mod_maker.asset_kernel import builder, clone, prefab_build, unit_bundle

import os
# 工作根：数据已移出仓（2026-11-09，统一入口）。
WORK = Path(os.environ.get("BA_WORK_ROOT") or r"D:\断箭模组制作\_work_db")

W10_REF = WORK / "w10_ref"
UNITS_META = W10_REF / "units_meta.bin"
ASSET = "Assets/Resources_moved/ModelPrefabs/RouteA/AuditProbe.prefab"


@pytest.fixture(scope="module")
def meta() -> bytes:
    if not UNITS_META.is_file():
        pytest.skip("缺 _work/w10_ref/units_meta.bin")
    return UNITS_META.read_bytes()


def _mini(tmp_path: Path, meta: bytes, *, bogus_ref: bool) -> Path:
    table = builder.read_raw_type_table(meta, common_strings=clone.unitypy_common_strings())
    go_pid = builder.path_id_for("audit|go")
    tr_pid = builder.path_id_for("audit|tr")
    go = prefab_build.default_fields(table.entry_for(1).nodes)
    go["m_Name"] = "AuditRoot"
    comps = [{"component": prefab_build.pptr(tr_pid)}]
    if bogus_ref:
        # 指向一个**包里没有**的 pathID —— 这就是"悬空"的形状（游戏那边取到空指针）
        comps.append({"component": prefab_build.pptr(builder.path_id_for("audit|missing"))})
    go["m_Component"] = comps
    tr = prefab_build.default_fields(table.entry_for(4).nodes)
    tr["m_GameObject"] = prefab_build.pptr(go_pid)
    plan = builder.build_object_bundle(
        objects=[builder.ObjectSpec(1, go, go_pid, label="go"),
                 builder.ObjectSpec(4, tr, tr_pid, label="tr")],
        bundle_name="auditprobe_assets_all_0000000000000002.bundle", source_meta=meta,
        container=[(ASSET, go_pid)], preload_all=True)
    out = tmp_path / ("audit_bogus.bundle" if bogus_ref else "audit_clean.bundle")
    out.write_bytes(bytes(plan.data))
    return out


def test_audit_clean_package(tmp_path: Path, meta: bytes) -> None:
    rep = unit_bundle.audit_package_pptrs(_mini(tmp_path, meta, bogus_ref=False))
    assert rep.ok, rep.dangling
    assert rep.decoded == rep.objects, (rep.decoded, rep.objects)
    assert rep.pptr_total >= 2, rep.lines()


def test_audit_flags_reference_to_missing_object(tmp_path: Path, meta: bytes) -> None:
    rep = unit_bundle.audit_package_pptrs(_mini(tmp_path, meta, bogus_ref=True))
    assert not rep.ok
    assert any(str(builder.path_id_for("audit|missing")) in line for line in rep.dangling), \
        rep.dangling
