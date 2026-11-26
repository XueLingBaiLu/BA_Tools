"""`pack set-slot-textures`（按材质槽换贴图）的判据 ＋ 负控。

**判据一行**：`.venv\\Scripts\\python.exe -m pytest tests/test_unit_pack_slot_textures.py -q -n 0`

正控（真件夹具 `_work/a2proto/bridge/ptest2/out/bridged2_…bundle`，
2026-10-22 实跑）：
  Chassis 2 子网格 / 2 槽，**两槽原本指向同一张贴图** `RU_T72B_1_BaseMap`
  （pid 1084419494171551337）——
  这正是编辑器桥 `--add-slots` 克隆材质的后遗症。跑完 slot0=红 / slot1=绿：
  槽 0 → 克隆件 `RU_T72B_1_BaseMap_slot0`（新 pathID -2）· 像素 sha256 `33e8dc87…`（期望红）
  槽 1 → 原对象改像素 · sha256 `2d3c5f18…`（期望绿）· 两槽**不再是同一张图**；子网格/槽仍 2/2。
负控：槽号越界 / 目录里没有 `slot<N>_*.png` / 一个槽两张图 —— 全部点名拒。

⛔ 夹具不在就 skip（不造假包）。
"""
from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import pytest
from PIL import Image

from ba_mod_maker.asset_kernel import texture_build
from ba_mod_maker.core.asset.new_asset import flip_rows
from ba_mod_maker.core.assets import unit_pack
from ba_mod_maker.core.errors import AssetError

FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "_work/a2proto/bridge/ptest2/out/bridged2_assets_all_dddddddddddddddd.bundle"
)

pytestmark = pytest.mark.skipif(
    not FIXTURE.is_file(), reason="本机没有 2 槽夹具（bridged2_…bundle）"
)


def _image_bytes(fields: dict) -> bytes:
    got = fields.get("image data")
    if isinstance(got, (bytes, bytearray)):
        return bytes(got)
    if isinstance(got, list):
        return bytes(int(x) & 0xFF for x in got)
    return b""


def _png_sha(png: Path, name: str) -> str:
    with Image.open(png) as img:
        rgba = img.convert("RGBA")
        w, h = rgba.size
        raw = rgba.tobytes()
    fields = texture_build.build_texture_fields(
        name, w, h, flip_rows(raw, w, h), texture_format=4
    )
    return hashlib.sha256(_image_bytes(fields)).hexdigest()


def _copy_bundle(tmp_path: Path) -> Path:
    dst = tmp_path / "slottex.bundle"
    shutil.copyfile(FIXTURE, dst)
    return dst


def _mk_pngs(
    tmp_path: Path, *, slot0: tuple[int, int, int, int], slot1: tuple[int, int, int, int]
) -> Path:
    folder = tmp_path / "tex"
    folder.mkdir()
    Image.new("RGBA", (32, 32), slot0).save(folder / "slot0_red.png")
    Image.new("RGBA", (32, 32), slot1).save(folder / "slot1_green.png")
    return folder


def _slots(bundle: Path) -> list[tuple[str, str, int, str]]:
    """读回：[(材质名, 贴图名, 贴图 pathID, 像素 sha256)]（按槽顺序）。"""
    pack = unit_pack._open_pack(bundle)
    try:
        owners = unit_pack._smr_owners_of_mesh(pack, "Chassis")
        rows = []
        for ptr in owners[0][1].get("m_Materials") or []:
            mat_pid = int((ptr or {}).get("m_PathID", 0))
            mat = unit_pack._fields_of(pack, mat_pid) or {}
            entry, tex_pid = unit_pack._primary_texture_of(pack, mat_pid)
            tex = unit_pack._fields_of(pack, tex_pid) or {}
            rows.append((
                str(mat.get("m_Name")),
                str(tex.get("m_Name")),
                int(tex_pid),
                hashlib.sha256(_image_bytes(tex)).hexdigest(),
            ))
        return rows
    finally:
        pack.raw_reader.close()


def test_shared_texture_is_cloned_so_each_slot_shows_its_own(tmp_path: Path) -> None:
    bundle = _copy_bundle(tmp_path)
    before = _slots(bundle)
    assert len(before) == 2 and before[0][2] == before[1][2], before  # 正控：两槽本来同一张图
    tex = _mk_pngs(tmp_path, slot0=(220, 30, 30, 255), slot1=(30, 200, 60, 255))

    res = unit_pack.set_slot_textures(bundle=bundle, mesh_name="Chassis", tex_dir=tex)

    assert [row["slot"] for row in res["slots"]] == [0, 1]
    assert res["slots"][0]["cloned"] is True   # 先处理的那一槽必须克隆（另一槽还在用它）
    assert res["slots"][1]["cloned"] is False  # 只剩它自己 ⇒ 原地改像素

    after = _slots(bundle)
    assert after[0][1] == "RU_T72B_1_BaseMap_slot0"
    assert after[1][1] == "RU_T72B_1_BaseMap"
    assert after[0][2] != after[1][2], "两槽还指着同一张贴图 —— 就是没做成"
    assert after[0][3] == _png_sha(tex / "slot0_red.png", after[0][1])
    assert after[1][3] == _png_sha(tex / "slot1_green.png", after[1][1])
    assert after[0][3] != after[1][3]


def test_refuses_a_slot_index_that_does_not_exist(tmp_path: Path) -> None:
    bundle = _copy_bundle(tmp_path)
    folder = tmp_path / "tex"
    folder.mkdir()
    Image.new("RGBA", (16, 16), (1, 2, 3, 255)).save(folder / "slot7_x.png")
    with pytest.raises(AssetError) as info:
        unit_pack.set_slot_textures(bundle=bundle, mesh_name="Chassis", tex_dir=folder)
    assert "越界" in str(info.value)


def test_refuses_a_directory_without_slot_files(tmp_path: Path) -> None:
    bundle = _copy_bundle(tmp_path)
    folder = tmp_path / "tex"
    folder.mkdir()
    Image.new("RGBA", (16, 16), (1, 2, 3, 255)).save(folder / "whatever.png")
    with pytest.raises(AssetError) as info:
        unit_pack.set_slot_textures(bundle=bundle, mesh_name="Chassis", tex_dir=folder)
    assert "slot" in str(info.value)


def test_refuses_two_images_for_one_slot(tmp_path: Path) -> None:
    bundle = _copy_bundle(tmp_path)
    folder = tmp_path / "tex"
    folder.mkdir()
    Image.new("RGBA", (16, 16), (1, 2, 3, 255)).save(folder / "slot0_a.png")
    Image.new("RGBA", (16, 16), (4, 5, 6, 255)).save(folder / "slot0_b.png")
    with pytest.raises(AssetError) as info:
        unit_pack.set_slot_textures(bundle=bundle, mesh_name="Chassis", tex_dir=folder)
    assert "两张图" in str(info.value)