r"""W9-B · 从零造最小 UnityFS 包的判据（builder.py）。

**判据一行**：`pytest tests/test_build_min_bundle.py -q -n 0` -> 通过数从 0 变 **N**，
且其中至少 3 条是**负控**（能被喂坏的样本变红才算尺子，AGENTS §0.9 动作 2）。

## 这些用例量的是哪几条（都对得上真机口径）

| # | 判据 | 怎么量 |
|---|---|---|
| K2 | 自造包能被两条独立读路打开 | UfsReader 与 UnityPy 各列一遍 m_Container，路径集合必须相等 |
| K2' | 对象表 `byte_start` 是相对 data_offset 的真值 | 按读回坐标解，第二个 != 第一个 |
| K3 | 块表自洽 | 每块解压长度 == 声明值、Σ == 节点 size、逐块 CRC == builder 算的 m_Crc |
| K3' | 元数据布局 | `data_offset == align16(48 + metadata_size)`、version 22、步长 24 |
| K3'' | 对象表 | pathID 严格升序、byteStart 8 字节对齐 |
| K6 | 类型表是**从真包逐字节照抄**的 | 新包元数据区里能找到源包那两项的原始字节 |
| K6' | 负控 | 喂坏类型树里一个字节 -> 重开必须**红**（实测 `ValueError: subsection not found`） |
| —— | pathID 确定性 | 同一 `(包名, 资产路径)` 两次算出同一个 pathID，且 != 1 |

## 为什么用真包的元数据区当输入

类型树是**内嵌在真包里**的（`enable_type_tree = True`，设计稿 §9.5 第 2 步）：
`builder` 的输入就是一份真元数据区。所以这些用例要一个小包（ammoprefabs 那类）；
没装游戏就 **skip**（与 `tests/test_contracts.py` 同一个姿势），⛔ 不造假 schema。
"""

from __future__ import annotations

import copy
import struct
import zlib
from pathlib import Path

import pytest

from ba_mod_maker.asset_kernel import builder, clone
from ba_mod_maker.asset_kernel import metadata_writer as mw
from ba_mod_maker.core.asset import ufs

#: 小包前缀（1.08 MB，含 Texture2D + Sprite + AssetBundle 三种类型树）。
SOURCE_PREFIX = "ammoicons"
ASSET_PATH = "Assets/Resources_moved/Images/Ammunition/Icons/AMMO_40MM_GRENADE.png"
BUNDLE_NAME = "w9btest0000000000000000000000000.bundle"


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


@pytest.fixture(scope="module")
def plan(source_meta: bytes) -> builder.MinBundlePlan:
    return builder.build_texture_bundle(
        asset_path=ASSET_PATH,
        texture=builder.Texture2DData(
            name="W9B_TEST_TEX",
            width=8,
            height=8,
            image=builder.checker_rgba(8, 8, (255, 0, 255, 255), (0, 40, 40, 255), cell=2),
        ),
        bundle_name=BUNDLE_NAME,
        source_meta=source_meta,
        common_strings=clone.unitypy_common_strings(),
    )


@pytest.fixture(scope="module")
def bundle_path(plan: builder.MinBundlePlan, tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("w9b") / "min.bundle"
    out.write_bytes(plan.data)
    return out


# --------------------------------------------------------------------------- #
# K2 · 两条独立读路
# --------------------------------------------------------------------------- #
def test_k2_ufs_reader_lists_the_container(bundle_path: Path, plan: builder.MinBundlePlan) -> None:
    info = builder.reopen_container(bundle_path)
    assert info["object_count"] == 2, "只该有 AssetBundle + Texture2D 两个对象"
    assert list(info["container"]) == [ASSET_PATH], "容器里必须是被改指那条 entry 的资产路径"
    assert info["preload_count"] == 1
    assert info["bundle_name"] == BUNDLE_NAME
    assert info["node_path"] == info["nodes"][0][0] == plan.node_path


def test_k2_unitypy_lists_the_same_container(bundle_path: Path) -> None:
    unitypy = pytest.importorskip("UnityPy")
    env = unitypy.load(str(bundle_path))
    kinds = sorted(obj.type.name for obj in env.objects)
    assert kinds == ["AssetBundle", "Texture2D"]
    container = {}
    for obj in env.objects:
        if obj.type.name == "AssetBundle":
            container = {str(k): v for k, v in obj.read().m_Container}
    assert list(container) == [ASSET_PATH]
    ours = builder.checker_rgba(8, 8, (255, 0, 255, 255), (0, 40, 40, 255), cell=2)
    # ① 写侧自证：我们自己序列化进去的像素必须**逐字节**等于 ours
    with ufs.UfsReader(bundle_path) as reader:
        node = reader.nodes[0]
        info = ufs.read_serialized_file(reader, node)
        head = reader.read_range(node.offset, builder.SF_HEADER_SIZE)
        meta_size = struct.unpack_from(">I", head, 20)[0]
        table = builder.read_raw_type_table(
            reader.read_range(node.offset, builder.SF_HEADER_SIZE + meta_size),
            common_strings=clone.unitypy_common_strings(),
        )
        tex_row = next(row for row in info.objects if row[1] != 1)
        blob = reader.read_range(
            node.offset + info.data_offset + tex_row[2], tex_row[3]
        )
        assert clone.read_object(blob, table.entries[tex_row[4]].nodes)["image data"] == ours
    # ② 跨实现对拍：UnityPy 解出来的像素（Unity 贴图原点在左下 => 可能整体上下翻转）
    row_bytes = 8 * 4
    flipped = b"".join(ours[y * row_bytes : (y + 1) * row_bytes] for y in reversed(range(8)))
    for obj in env.objects:
        if obj.type.name == "Texture2D":
            tex = obj.read()
            assert (tex.m_Width, tex.m_Height, tex.m_TextureFormat) == (8, 8, 4)
            assert tex.image.tobytes() in (ours, flipped), "UnityPy 解出来的像素对不上"


def test_k2_negative_control_second_object_is_not_the_first(
    bundle_path: Path, plan: builder.MinBundlePlan
) -> None:
    """负控：对象表里 `byte_start = 0` 的写法会让第二个对象读到第一个的字节（真踩过）。

    尺子 = 「按读回来的坐标解第二个对象，它必须解得开、且 pathID/类型对得上」——
    这个 bug 真出现过（我在装配时漏写 byte_start），当时容器照样「列得出来」，所以
    **必须有一条专门的用例**：只列容器抓不住它。
    """
    with ufs.UfsReader(bundle_path) as reader:
        node = reader.nodes[0]
        info = ufs.read_serialized_file(reader, node)
        head = reader.read_range(node.offset, builder.SF_HEADER_SIZE)
        meta_size = struct.unpack_from(">I", head, 20)[0]
        table = builder.read_raw_type_table(
            reader.read_range(node.offset, builder.SF_HEADER_SIZE + meta_size),
            common_strings=clone.unitypy_common_strings(),
        )
        blob_by_path_id = {}
        for _i, path_id, stored, size, type_id in info.objects:
            blob = reader.read_range(node.offset + info.data_offset + stored, size)
            blob_by_path_id[path_id] = (blob, table.entries[type_id].class_id)
    assert sorted(blob_by_path_id) == [1, plan.texture_path_id]
    ab_blob, ab_class = blob_by_path_id[1]
    tex_blob, tex_class = blob_by_path_id[plan.texture_path_id]
    assert (ab_class, tex_class) == (builder.CLASS_ASSETBUNDLE, builder.CLASS_TEXTURE2D)
    assert ab_blob != tex_blob
    # 两个对象都要能按各自的类型树解回来（解不开 = 坐标错位）
    assert clone.roundtrip(ab_blob, table.entry(builder.CLASS_ASSETBUNDLE).nodes) == ab_blob
    assert clone.roundtrip(tex_blob, table.entry(builder.CLASS_TEXTURE2D).nodes) == tex_blob


# --------------------------------------------------------------------------- #
# K3 · 块表 + 元数据布局
# --------------------------------------------------------------------------- #
def test_k3_block_table_is_self_consistent(
    bundle_path: Path, plan: builder.MinBundlePlan
) -> None:
    with ufs.UfsReader(bundle_path) as reader:
        total_u, total_c = builder.block_self_check(plan.unityfs)
        assert total_u == plan.unityfs.node_size == plan.serialized_file_size
        assert reader.header.data_start + total_c == reader.file_size, (
            "数据起点 + Σ压缩长度必须等于文件长"
        )
        assert [(b.uncompressed, b.compressed, b.flags) for b in reader.blocks] == [
            (b.uncompressed, b.compressed, b.flags) for b in plan.unityfs.blocks
        ]
        crc = 0
        for i, blk in enumerate(reader.blocks):
            dec = reader.read_range(reader.block_dstart[i], blk.uncompressed)
            assert len(dec) == blk.uncompressed, f"第 {i} 块解压长度与声明不符"
            crc = zlib.crc32(dec, crc)
        assert crc & 0xFFFFFFFF == plan.crc32, "逐块解压拼接的 CRC 必须等于 builder 算的 m_Crc"
        assert reader.header.declared_size == reader.file_size == bundle_path.stat().st_size


def test_k3_metadata_layout_follows_measured_rules(
    bundle_path: Path, plan: builder.MinBundlePlan
) -> None:
    with ufs.UfsReader(bundle_path) as reader:
        node = reader.nodes[0]
        head = reader.read_range(node.offset, builder.SF_HEADER_SIZE)
        version = struct.unpack_from(">I", head, 8)[0]
        metadata_size = struct.unpack_from(">I", head, 20)[0]
        file_size = struct.unpack_from(">q", head, 24)[0]
        data_offset = struct.unpack_from(">q", head, 32)[0]
        assert version == builder.SF_VERSION == 22
        assert head[16] == 0, "endian 标志 0 = 小端"
        assert metadata_size == plan.serialized.metadata_size
        assert data_offset == builder.align(builder.SF_HEADER_SIZE + metadata_size, 16)
        assert file_size == node.size == plan.serialized_file_size
        info = ufs.read_serialized_file(reader, node)
        assert info.object_entry_stride == 24
        path_ids = [row[1] for row in info.objects]
        assert path_ids == sorted(path_ids), "对象表必须 pathID 严格升序（Unity 二分查找）"
        for _i, _pid, stored, _size, _tid in info.objects:
            assert stored % 8 == 0, "对象起点必须 8 字节对齐"


def test_k3_lz4hc_block_variant_also_round_trips(
    source_meta: bytes, tmp_path: Path
) -> None:
    plan = builder.build_texture_bundle(
        asset_path=ASSET_PATH,
        texture=builder.Texture2DData(
            name="W9B_TEST_TEX", width=8, height=8,
            image=builder.solid_rgba(8, 8, (255, 0, 255, 255)),
        ),
        bundle_name=BUNDLE_NAME,
        source_meta=source_meta,
        common_strings=clone.unitypy_common_strings(),
        block_type=builder.BLOCK_LZ4HC,
    )
    assert {b.compress_type for b in plan.unityfs.blocks} == {builder.BLOCK_LZ4HC}
    out = tmp_path / "lz4.bundle"
    out.write_bytes(plan.data)
    info = builder.reopen_container(out)
    assert list(info["container"]) == [ASSET_PATH]
    assert plan.bundle_size < plan.serialized_file_size, "LZ4HC 该把它压小"


# --------------------------------------------------------------------------- #
# K6 · 类型表逐字节照抄 + 负控（喂坏必须红）
# --------------------------------------------------------------------------- #
def test_k6_type_table_is_copied_verbatim(source_meta: bytes, plan: builder.MinBundlePlan) -> None:
    common = clone.unitypy_common_strings()
    source = builder.read_raw_type_table(source_meta, common_strings=common)
    ours = builder.read_raw_type_table(plan.serialized.blob, common_strings=common)
    assert [e.class_id for e in ours.entries] == [
        builder.CLASS_TEXTURE2D,
        builder.CLASS_ASSETBUNDLE,
    ]
    for mine in ours.entries:
        theirs = source.entry(mine.class_id)
        assert mine.raw == theirs.raw, f"classID={mine.class_id} 的类型表项不是逐字节照抄的"
        assert [n.name for n in mine.nodes] == [n.name for n in theirs.nodes]
        # 更强的一条：**编码器**（metadata_writer）重新编出来的字节 == 源包那一项的原始字节
        # —— 证明新包的类型表不是「长得像」，而是逐字节同一段
        assert mine.wire is not None
        assert mw.encode_serialized_type(mine.wire, "<", builder.SF_VERSION, True) == theirs.raw


def test_k6_negative_control_a_broken_type_tree_turns_red(
    plan: builder.MinBundlePlan, tmp_path: Path
) -> None:
    """负控：把类型表里**一个字节**改掉 -> 重新打开必须报错（不是静默读出一堆垃圾）。"""
    good = tmp_path / "good.bundle"
    good.write_bytes(plan.data)
    assert list(builder.reopen_container(good)["container"]) == [ASSET_PATH]

    bad = bytearray(plan.data)
    with ufs.UfsReader(good) as reader:
        node = reader.nodes[0]
        assert node.offset == 0, "单节点从 0 起，解压流偏移 == 文件偏移"
    ours = builder.read_raw_type_table(
        plan.serialized.blob, common_strings=clone.unitypy_common_strings()
    )
    entry = ours.entry(builder.CLASS_TEXTURE2D)
    # 类型树 blob 起点 = 项起点 + classID(4) + stripped(1) + scriptIndex(2) + oldTypeHash(16)
    blob_start = entry.start + 4 + 1 + 2 + 16
    # ⚠ 解压流偏移 != 文件偏移：块类型 0 + 单块 ⇒ 文件偏移 = data_start + 流偏移
    node_stream_start = 0
    assert (node_stream_start, plan.unityfs.block_count) == (0, 1)
    # blob = [i32 节点数][i32 串池字节数][32*节点数 的节点数组][串池]
    # 第 1 个节点（从 0 数）= m_Name，它的 name_off（u32，节点内 +8）最高字节
    victim = plan.unityfs.data_start + blob_start + 8 + 32 + 8 + 3
    bad[victim] ^= 0xFF
    broken = tmp_path / "broken.bundle"
    broken.write_bytes(bytes(bad))
    with pytest.raises(Exception):  # noqa: B017 -- AssetError / struct.error / KeyError 都算红
        builder.reopen_container(broken)


def _first_object_of(kind: int) -> dict:
    """从源包里读第一条该 classID 的对象（字段树）——Stage 2 要拿一条真 Sprite 当模板。"""
    path = _source_bundle()
    common = clone.unitypy_common_strings()
    with ufs.UfsReader(path) as reader:
        node = reader.nodes[0]
        head = reader.read_range(node.offset, builder.SF_HEADER_SIZE)
        meta = reader.read_range(node.offset, struct.unpack_from(">q", head, 32)[0])
        table = builder.read_raw_type_table(meta, common_strings=common)
        info = ufs.read_serialized_file(reader, node)
        type_id = next(i for i, e in enumerate(table.entries) if e.class_id == kind)
        row = next(r for r in info.objects if r[4] == type_id)
        blob = reader.read_range(node.offset + info.data_offset + row[2], row[3])
        return clone.read_object(blob, table.entry(kind).nodes)


def test_stage2_texture_plus_sprite_shares_one_container_path(
    source_meta: bytes, tmp_path: Path
) -> None:
    """Stage 2（Lead 定）：同一条容器路径**两条条目**（Texture2D + Sprite），Sprite 指我们的贴图。

    真件同形（unitmodifications / ARMOR1）：两条条目的 preloadIndex/preloadSize 都是 (130, 2)。
    游戏 UI 的图是 Image/Sprite ⇒ 只改 Texture2D 那条 entry 的话，Sprite 仍旧从老包拿、再顺
    包内 PPtr 拿老纹理 ⇒ 什么都看不见。这条用例把「两条条目 + Sprite 指向本包贴图」钉死。
    """
    sprite_template = _first_object_of(builder.CLASS_SPRITE)
    assert isinstance(sprite_template.get("m_RD"), dict), "真 Sprite 的字段树里必须有 m_RD"
    plan = builder.build_texture_bundle(
        asset_path=ASSET_PATH,
        texture=builder.Texture2DData(
            name="W9B_TEST_TEX", width=32, height=16,
            image=builder.solid_rgba(32, 16, (255, 0, 255, 255)),
        ),
        bundle_name=BUNDLE_NAME,
        source_meta=source_meta,
        common_strings=clone.unitypy_common_strings(),
        sprite_template=sprite_template,
    )
    assert plan.sprite_path_id is not None and plan.sprite_path_id != plan.texture_path_id
    rows0 = plan.serialized.object_rows
    assert [r.path_id for r in rows0] == sorted(r.path_id for r in rows0), "对象表必须 pathID 升序"
    assert {r.class_id for r in rows0} == {
        builder.CLASS_ASSETBUNDLE,
        builder.CLASS_TEXTURE2D,
        builder.CLASS_SPRITE,
    }, "三个对象都要在表里"

    out = tmp_path / "stage2.bundle"
    out.write_bytes(plan.data)
    info = builder.reopen_container(out)
    rows = info["container_rows"]
    assert len(rows) == 2, "同一条容器路径必须有两条条目"
    assert {row[0] for row in rows} == {ASSET_PATH}
    assert [row[1] for row in rows] == [plan.texture_path_id, plan.sprite_path_id]
    assert len({(row[2], row[3]) for row in rows}) == 1, "两条条目共享同一个 preload 窗口"
    assert (rows[0][2], rows[0][3]) == (0, 2)

    # 我们自己的读法：Sprite 的 m_RD.texture 必须指向本包的 Texture2D
    ours = builder.read_raw_type_table(
        plan.serialized.blob, common_strings=clone.unitypy_common_strings()
    )
    sprite_row = next(
        r for r in plan.serialized.object_rows if r.class_id == builder.CLASS_SPRITE
    )
    with ufs.UfsReader(out) as reader:
        node = reader.nodes[0]
        blob = reader.read_range(
            node.offset + plan.serialized.data_offset + sprite_row.byte_start,
            sprite_row.byte_size,
        )
        sprite = clone.read_object(blob, ours.entry(builder.CLASS_SPRITE).nodes)
    assert sprite["m_RD"]["texture"] == {"m_FileID": 0, "m_PathID": plan.texture_path_id}
    # 除指向外，其余字段必须与模板逐字段相同（不是重造一条 Sprite）
    patched = dict(sprite)
    reference = dict(sprite_template)
    patched["m_RD"] = dict(patched["m_RD"])
    reference["m_RD"] = dict(reference["m_RD"])
    patched["m_RD"].pop("texture")
    reference["m_RD"].pop("texture")
    for key in ("m_Name",):
        patched.pop(key, None)
        reference.pop(key, None)
    assert patched == reference, "Sprite 除了指向，别的字段一个都不许动"


def test_type_tree_string_offsets_are_all_resolvable(plan: builder.MinBundlePlan) -> None:
    """A 组复核器 ④b 那条尺子：每条类型树字符串偏移要么「池内且带 NUL」、要么**最高位置 1**。

    这条只靠「读回来逐字节相同」发现不了：把 name_str_offset 的高字节翻掉，字节长度一个不少，
    但已解出来的字符串指到池外 —— UnityPy 会当场抛 `String not terminated`。
    负控就在下面（同一把尺子必须能红）。
    """
    ours = builder.read_raw_type_table(
        plan.serialized.blob, common_strings=clone.unitypy_common_strings()
    )
    assert ours.model is not None
    trees = 0
    for wire in ours.model.types:
        assert wire.type_tree is not None
        assert mw.type_tree_string_offsets_ok(wire.type_tree, "<") is True
        trees += 1
    assert trees == 2, "两份类型树（Texture2D + AssetBundle）都要查"

    # ---- 负控：把 Texture2D 树第 1 个节点（m_Name）的 name_str_offset 改成池外偏移 ----
    src = ours.entry(builder.CLASS_TEXTURE2D).wire
    assert src is not None and src.type_tree is not None
    tree = copy.deepcopy(src.type_tree)
    raw = bytearray(tree.nodes)
    struct.pack_into("<I", raw, mw.TYPE_TREE_NODE_STRIDE + 8, 21_000_000)
    tree.nodes = bytes(raw)
    assert mw.type_tree_string_offsets_ok(tree, "<") is False, "尺子红不了 = 不算尺子"


def test_crc_ruler_catches_one_byte(bundle_path: Path, plan: builder.MinBundlePlan) -> None:
    """CRC 尺子的正控：改一个像素字节，'逐块解压拼接' 的读数必须变。"""
    data = bytearray(bundle_path.read_bytes())
    data[-1] ^= 0x01  # 最后一个字节 = 贴图像素的最后一字节（块类型 0、单块）
    with ufs.UfsReader(bundle_path) as reader:
        pass
    tmp = bundle_path.parent / "flip.bundle"
    tmp.write_bytes(bytes(data))
    with ufs.UfsReader(tmp) as reader:
        crc = 0
        for i, blk in enumerate(reader.blocks):
            crc = zlib.crc32(reader.read_range(reader.block_dstart[i], blk.uncompressed), crc)
    assert crc & 0xFFFFFFFF != plan.crc32


# --------------------------------------------------------------------------- #
# pathID 口径
# --------------------------------------------------------------------------- #
def test_path_id_is_deterministic_and_never_collides_with_the_bundle_object() -> None:
    a = builder.path_id_for(f"{BUNDLE_NAME}|{ASSET_PATH}")
    b = builder.path_id_for(f"{BUNDLE_NAME}|{ASSET_PATH}")
    assert a == b and a not in (0, 1)
    assert builder.path_id_for("other") != a


def test_internal_bundle_name_drops_the_suffix() -> None:
    assert builder.internal_bundle_name_of("e6f1330cd83077a5213853617d6c54c5.bundle") == (
        "e6f1330cd83077a5213853617d6c54c5"
    )
    assert builder.internal_bundle_name_of("no-suffix") == "no-suffix"
