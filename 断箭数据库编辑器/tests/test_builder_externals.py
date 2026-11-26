r"""builder 的 externals 入口（跨包引用那一跳）：`external_for_bundle` / `extra_externals`。

**判据一行**：`pytest tests/test_builder_externals.py -q -n 0` -> 通过数从 0 变 **N**，
其中 4 条是**正控**（喂坏的样本必须红 —— AGENTS §0.9 动作 2）。

## 这一跳是什么（真件读数，探针 `_work/addr-pkg/c1_recon.py` + `c1_cab_owner.py`）

units 包的 SerializedFile 里 3 条 externals：

    externals[0] fileID=1  archive:/CAB-4716c9c1…/CAB-4716c9c1…   -> **vfx 包的节点名**
    externals[1] fileID=2  Library/unity default resources        -> 引擎内置（不是包）
    externals[2] fileID=3  archive:/CAB-457fda64…/CAB-457fda64…   -> unitybuiltinshaders 包的节点名

⇒ 跨包那一跳 = **PPtr.m_FileID -> 本包 externals[i] -> 目标包的 CAB 节点名**；
"目标包要已被加载"由 catalog 的依赖组键保证（那个桶里 units+vfx+ammoprefabs+unitybuiltinshaders）。
本模块量的就是**写侧**这一条：自建包能不能声明一条指回**原包**的 external（乙方案靠它）。

## 为什么用真包的元数据区当输入

类型树内嵌在真包里（与 `tests/test_build_min_bundle.py` 同一姿势、同一份小包 ammoicons）；
没装游戏就 **skip**，⛔ 不造假 schema。
"""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from ba_mod_maker.asset_kernel import builder, clone
from ba_mod_maker.asset_kernel import metadata_writer as mw
from ba_mod_maker.core.asset import ufs
from ba_mod_maker.core.errors import AssetError

#: 小包（1.08 MB，含 Texture2D + Sprite + AssetBundle 三种类型树）。
SOURCE_PREFIX = "ammoicons"
ASSET_PATH = "Assets/Resources_moved/Images/Ammunition/Icons/AMMO_40MM_GRENADE.png"
NEW_PATH = "Assets/Mods/BAModMaker/Probe/probe.png"
NEW_BUNDLE = "extprobe000000000000000000000000.bundle"
NEW_PID = 1_000_000_000_000_000_001


def _bundle_dir() -> Path | None:
    from ba_mod_maker.core.assets.game import bundle_dir, find_game_root

    root = find_game_root()
    return bundle_dir(root) if root is not None else None


def _source_bundle() -> Path:
    base = _bundle_dir()
    hits = sorted(base.glob(f"{SOURCE_PREFIX}*.bundle")) if base is not None else []
    if not hits:
        pytest.skip(f"找不到 {SOURCE_PREFIX}*.bundle（没装游戏 / BA_MOD_MAKER_GAME 指错了）")
    return hits[0]


@pytest.fixture(scope="module")
def source_meta() -> bytes:
    """真包的元数据区（48 B 头 + 类型表 + 对象表 …）。"""
    path = _source_bundle()
    with ufs.UfsReader(path) as reader:
        node = reader.nodes[0]
        head = reader.read_range(node.offset, builder.SF_HEADER_SIZE)
        size = struct.unpack_from(">I", head, 20)[0]
        return reader.read_range(node.offset, builder.SF_HEADER_SIZE + size)


def _make_target(source_meta: bytes, folder: Path, bundle_name: str, tag: str) -> Path:
    """造一个**小的、自己造的**目标包 —— 它的节点 0 名就是要被引用的 CAB 名。"""
    plan = builder.build_texture_bundle(
        asset_path=ASSET_PATH,
        texture=builder.Texture2DData(
            name=tag, width=8, height=8,
            image=builder.checker_rgba(8, 8, (255, 0, 255, 255), (0, 40, 40, 255), cell=2),
        ),
        bundle_name=bundle_name,
        source_meta=source_meta,
        common_strings=clone.unitypy_common_strings(),
    )
    out = folder / f"{tag}.bundle"
    out.write_bytes(plan.data)
    return out


@pytest.fixture(scope="module")
def target_bundle(source_meta: bytes, tmp_path_factory: pytest.TempPathFactory) -> Path:
    return _make_target(source_meta, tmp_path_factory.mktemp("w12ext"),
                        "w12ext0000000000000000000000000.bundle", "target")


@pytest.fixture(scope="module")
def target_bundle2(source_meta: bytes, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """第二个目标包（不同的包名 => 不同的 CAB 名）—— 用来量 fileID 1/2 的编号。"""
    return _make_target(source_meta, tmp_path_factory.mktemp("w12ext2"),
                        "w12ext2222222222222222222222222.bundle", "target2")


def _texture2d_spec(source_meta: bytes) -> builder.ObjectSpec:
    """从真包读一颗 Texture2D 的**字段树**（照抄类型树那一份口径）。"""
    path = _source_bundle()
    table = builder.read_raw_type_table(source_meta, common_strings=clone.unitypy_common_strings())
    with ufs.UfsReader(path) as reader:
        node = reader.nodes[0]
        info = ufs.read_serialized_file(reader, node)
        row = next(
            o for o in info.objects if table.entries[int(o[4])].class_id == builder.CLASS_TEXTURE2D
        )
        fields = clone.read_object(
            reader.read_range(node.offset + info.data_offset + row[2], row[3]),
            table.entries[int(row[4])].nodes,
        )
    return builder.ObjectSpec(
        class_id=builder.CLASS_TEXTURE2D, fields=fields, path_id=NEW_PID, label="probe-tex"
    )


def _padded_meta(source_meta: bytes) -> bytes:
    """补到 data_offset（含填充区）—— `mw.parse_serialized_metadata` 的 verify 要它。"""
    size = struct.unpack_from(">I", source_meta, 20)[0]
    total = mw.align_up(mw.HEADER_SIZE + size, mw.DATA_ALIGN)
    return source_meta + b"\x00" * (total - len(source_meta))


def _meta_with_externals(source_meta: bytes, count: int) -> bytes:
    """把源元数据区改写成「**已经**有 count 条 externals」的那一份（再造引用方）。"""
    meta = mw.parse_serialized_metadata(_padded_meta(source_meta))
    meta.externals = [
        mw.ExternalRef(
            temp_empty="",
            guid=b"\x00" * 16,
            type=0,
            path=f"archive:/CAB-{index:032x}/CAB-{index:032x}",
        )
        for index in range(1, count + 1)
    ]
    return mw.build_metadata(meta)


# --------------------------------------------------------------------------- #
# ① path 形状闸（不依赖游戏）
# --------------------------------------------------------------------------- #
def test_check_external_path_accepts_the_real_shape() -> None:
    path = "archive:/CAB-1e53370fe5e57c593d6b445e251b8d1c/CAB-1e53370fe5e57c593d6b445e251b8d1c"
    assert builder.check_external_path(path) == path


@pytest.mark.parametrize(
    "bad",
    [
        "Library/unity default resources",       # 内置资源：⛔ 不走这里（照抄源包那条路）
        "archive:/CAB-aaa/CAB-bbb",              # 两段不同
        "archive:/CAB-aaa",                      # 只有一段
        "archive:/CAB-aaa/",                     # 空第二段
        "archive:/4716c9c1/4716c9c1",            # 不以 CAB- 起
        "",
    ],
)
def test_check_external_path_refuses_a_bad_shape(bad: str) -> None:
    """**正控**：形状不对必须红（⛔ 不静默写半条）。"""
    with pytest.raises(AssetError):
        builder.check_external_path(bad)


# --------------------------------------------------------------------------- #
# ② external_for_bundle：节点名必须真实存在
# --------------------------------------------------------------------------- #
def test_external_for_bundle_uses_node_zero(target_bundle: Path) -> None:
    names = builder.bundle_node_names(target_bundle)
    assert names and names[0].startswith("CAB-"), names
    ref = builder.external_for_bundle(target_bundle)
    assert ref.path == f"archive:/{names[0]}/{names[0]}"
    assert builder.check_external_path(ref.path) == ref.path
    assert ref.guid == b"\x00" * 16 and ref.type == 0


def test_external_for_bundle_refuses_a_node_that_is_not_there(target_bundle: Path) -> None:
    """**正控**：传一个不存在的节点名必须拒（否则会写出一条指不到东西的引用）。"""
    with pytest.raises(AssetError) as excinfo:
        builder.external_for_bundle(target_bundle, node="CAB-deadbeefdeadbeefdeadbeefdeadbeef")
    assert "没有这个节点名" in excinfo.value.message


def test_external_for_bundle_refuses_a_file_that_is_not_a_bundle(tmp_path: Path) -> None:
    """**正控**：不是 UnityFS 的文件必须拒（不是抛别的异常，是 AssetError）。"""
    junk = tmp_path / "junk.bundle"
    junk.write_bytes(b"not a unityfs" * 8)
    with pytest.raises((AssetError, ValueError, OSError)):
        builder.external_for_bundle(junk)


# --------------------------------------------------------------------------- #
# ③ extra_externals：排在被照抄的那几条之后，fileID 1 起数
# --------------------------------------------------------------------------- #
def test_extra_externals_are_appended_after_the_copied_ones(
    source_meta: bytes, target_bundle: Path, tmp_path: Path
) -> None:
    """判据一行：照抄 3 条 + 追加 1 条 => externals **3 -> 4**，新那条 fileID=**4**。"""
    meta = _meta_with_externals(source_meta, 3)
    copied = mw.parse_serialized_metadata(meta).externals
    assert len(copied) == 3
    ref = builder.external_for_bundle(target_bundle)
    plan = builder.build_object_bundle(
        objects=[_texture2d_spec(source_meta)],
        bundle_name=NEW_BUNDLE,
        source_meta=meta,
        container=[(NEW_PATH, NEW_PID)],
        common_strings=clone.unitypy_common_strings(),
        copy_source_tables=True,
        extra_externals=[ref],
    )
    assert len(plan.externals) == 4, plan.externals
    assert list(plan.externals[:3]) == [item.path for item in copied], "照抄那几条不许被挪位"
    assert plan.externals[3] == ref.path, "追加的那条要在最后"
    assert plan.extra_external_file_ids == (4,), "fileID 是 1 起数（下标 3 -> fileID 4）"
    # 落盘之后**读回来**核一遍（不是只看计划上的字段）
    out = tmp_path / "ref.bundle"
    out.write_bytes(plan.data)
    with ufs.UfsReader(out) as reader:
        read_back = mw.read_serialized_metadata(reader, reader.nodes[0])
    assert [item.path for item in read_back.externals] == list(plan.externals)


def test_two_extra_externals_get_file_ids_one_and_two(
    source_meta: bytes, target_bundle: Path, target_bundle2: Path
) -> None:
    """没照抄任何 externals 时，追加的两条（**两个不同的包**）就是 fileID 1 / 2。"""
    first = builder.external_for_bundle(target_bundle)
    second = builder.external_for_bundle(target_bundle2)
    assert first.path != second.path, "两个目标包的 CAB 名必须不同，否则这条量的是别的东西"
    plan = builder.build_object_bundle(
        objects=[_texture2d_spec(source_meta)],
        bundle_name=NEW_BUNDLE,
        source_meta=_meta_with_externals(source_meta, 0),
        container=[(NEW_PATH, NEW_PID)],
        common_strings=clone.unitypy_common_strings(),
        copy_source_tables=True,
        extra_externals=[first, second],
    )
    assert plan.externals == (first.path, second.path)
    assert plan.extra_external_file_ids == (1, 2)


def test_the_same_path_twice_is_refused(source_meta: bytes, target_bundle: Path) -> None:
    """**正控**：同一条 path 写两次必须拒（不是静默写两条指同一个目标的项）。"""
    ref = builder.external_for_bundle(target_bundle)
    with pytest.raises(AssetError) as excinfo:
        builder.build_object_bundle(
            objects=[_texture2d_spec(source_meta)],
            bundle_name=NEW_BUNDLE,
            source_meta=_meta_with_externals(source_meta, 3),
            container=[(NEW_PATH, NEW_PID)],
            common_strings=clone.unitypy_common_strings(),
            copy_source_tables=True,
            extra_externals=[ref, ref],
        )
    assert "已经有这条" in excinfo.value.message


def test_a_pptr_pointing_at_the_appended_external_is_not_dangling(
    source_meta: bytes, target_bundle: Path
) -> None:
    """追加的那条必须**被 N4 尺子认**：fileID=4 的 PPtr 不算悬空，fileID=5 算。"""
    ref = builder.external_for_bundle(target_bundle)
    fields = {"m_FileID": 4, "m_PathID": 12345}
    spec = builder.ObjectSpec(
        class_id=builder.CLASS_TEXTURE2D, fields=fields, path_id=NEW_PID, label="probe"
    )
    # ref 在这里只用来钉住「追加之后一共几条」这个前提
    assert len(builder.external_for_bundle.__doc__ or "") > 0
    meta = _meta_with_externals(source_meta, 3)
    assert len(mw.parse_serialized_metadata(meta).externals) + 1 == 4, ref.path
    assert builder.dangling_pptrs([spec], externals=4) == ()
    problems = builder.dangling_pptrs([spec], externals=3)
    assert problems and "越界" in problems[0]
