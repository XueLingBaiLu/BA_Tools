r"""P3-2 · C-3 贴图整链（**产品侧**那一半）的机械判据。

**判据一行**：pytest tests/test_p3tex_chain.py -q -> 通过数从 0 变 N，
且其中至少 6 条是**负控**（喂一个已知违规的点进去必须红，AGENTS §0.9 动作 2）。

## 这一半量什么、不量什么

| 量 | 在哪 |
|---|---|
| build_streaming_texture_fields 的五个口径（路径/size/offset/满 mip 公式/complete） | 本文件 |
| builder.build_unityfs(stream_payload=...) 的**双节点**容器形状 | 本文件 |
| 流式贴图**从 .resS 读回来**逐字节相同（产品的 texture.read_texture_2d） | 本文件 |
| 「共用一个解压流、m_ImageData 空」的自洽（textures.check_texture_streaming） | 本文件 |
| **Unity 真引擎解回来的像素**（2048² 满 12 级） | ⛔ 不在用例里：_work/p3tex/probe.py（起 Unity） |

## 为什么用真包的元数据区当 schema

类型树**内嵌在真包里**（enable_type_tree = True）：字段名一个都不许自己写。
所以这些用例要一个小包当 schema 来源（ammoicons*.bundle，1.08 MB）。
没装游戏就 **skip**（与 tests/test_build_min_bundle.py 同一个姿势），⛔ 不造假 schema。
"""

from __future__ import annotations

import struct
import tempfile
from pathlib import Path

import pytest

from ba_mod_maker.asset_kernel import builder, clone, textures, unityfs
from ba_mod_maker.asset_kernel import metadata_writer as mw
from ba_mod_maker.asset_kernel import texture as tex
from ba_mod_maker.asset_kernel import texture_build as tb
from ba_mod_maker.core.asset import ufs

SOURCE_PREFIX = "ammoicons"
W = H = 64
MIPS = 7
FMT = 12  # DXT5 = BC3
PAYLOAD = textures.dxt_payload_size(W, H, FMT, MIPS)  # 5,488 B
_CAB = "CAB-0123456789abcdef0123456789abcdef"
STREAM_PATH = f"archive:/{_CAB}/{_CAB}.resS"


def _bundle_dir() -> Path | None:
    from ba_mod_maker.core.assets.game import bundle_dir, find_game_root

    root = find_game_root()
    return bundle_dir(root) if root is not None else None


@pytest.fixture(scope="module")
def source_meta() -> bytes:
    base = _bundle_dir()
    hits = sorted(base.glob(SOURCE_PREFIX + "*.bundle")) if base is not None else []
    if not hits:
        pytest.skip("找不到 " + SOURCE_PREFIX + "*.bundle（没装游戏 / BA_MOD_MAKER_GAME 指错了）")
    with unityfs.open_bundle(hits[0]) as reader:
        node = reader.nodes[0]
        head = reader.read_range(node.offset, builder.SF_HEADER_SIZE)
        size = struct.unpack_from(">q", head, 32)[0]
        return bytes(reader.read_range(node.offset, size))


def _rgba() -> bytes:
    """确定性 RGBA（64²，带块内高频 —— 全同色块什么都测不出）。"""
    out = bytearray()
    for y in range(H):
        for x in range(W):
            out += bytes(((x * 4) & 0xFF, (y * 4) & 0xFF, ((x ^ y) * 4) & 0xFF, 255))
    return bytes(out)


def _payload() -> bytes:
    return textures.encode_dxt(_rgba(), W, H, FMT, MIPS)


def _fields(**over: object) -> dict:
    kw: dict = {
        "texture_format": FMT, "mip_count": MIPS, "size": PAYLOAD, "stream_path": STREAM_PATH,
    }
    kw.update(over)
    return tb.build_streaming_texture_fields("p3tex_unit", W, H, **kw)


# --------------------------------------------------------------------------- ① 字段树
def test_streaming_fields_has_exactly_the_24_declared_keys() -> None:
    """24 个顶层键一个不多一个不少（键名只从包内类型树来）。"""
    f = _fields()
    assert set(f) == set(tb.TEXTURE_FIELDS)
    assert len(tb.TEXTURE_FIELDS) == 24


def test_streaming_fields_image_data_is_empty_and_stream_points_at_the_node() -> None:
    """100% 流式的定义：image data 空 + m_StreamData 三项对得上 + complete == size。"""
    f = _fields()
    assert f["image data"] == b""
    assert f["m_StreamData"] == {"offset": 0, "size": PAYLOAD, "path": STREAM_PATH}
    assert f["m_CompleteImageSize"] == PAYLOAD
    assert f["m_MipCount"] == MIPS
    assert f["m_TextureFormat"] == FMT
    assert f["m_TextureSettings"]["m_FilterMode"] == 1, "kb/23 §2：Bilinear"


def test_streaming_fields_offset_is_honoured() -> None:
    assert _fields(offset=4096)["m_StreamData"]["offset"] == 4096


# --------------------------------------------------------------------------- ② 负控（必须红）
@pytest.mark.parametrize(
    ("case", "over"),
    [
        ("空 stream_path", {"stream_path": ""}),
        ("size = 0", {"size": 0}),
        ("size 负数", {"size": -16}),
        ("offset 负数", {"offset": -1}),
        ("mip 级数 = 0", {"mip_count": 0}),
        # ★ 本单点名的负控①：少烘一级 mip（11 级）⇒ 字节数与「满 7 级」对不上
        ("少烘一级 mip", {"size": PAYLOAD - 16}),
        ("m_CompleteImageSize != size", {"complete_image_size": PAYLOAD + 16}),
        ("不认的格式", {"texture_format": 99}),
    ],
)
def test_negative_controls_raise(case: str, over: dict) -> None:
    with pytest.raises(tb.TextureBuildError):
        _fields(**over)


def test_positive_control_mip_ladder_matches_the_formula() -> None:
    """正控：满 7 级的公式值就是 5,488 B（负控那两条是围着它偏 ±16）。"""
    assert PAYLOAD == 5488
    assert textures.dxt_payload_size(W, H, FMT, MIPS - 1) == PAYLOAD - 16


# --------------------------------------------------------------------------- ③ 双节点容器
class _MemReader:
    """把内存里的包写进临时目录再开（UfsReader 只吃路径）。"""

    def __init__(self, data: bytes) -> None:
        self._dir = tempfile.TemporaryDirectory(prefix="p3tex-")
        self._path = Path(self._dir.name) / "probe.bundle"
        self._path.write_bytes(data)
        self._r = ufs.UfsReader(self._path)

    def __enter__(self) -> ufs.UfsReader:
        return self._r

    def __exit__(self, *exc: object) -> None:
        self._r.close()
        self._dir.cleanup()


def test_build_unityfs_makes_two_nodes_with_real_bundle_shape() -> None:
    """给了 stream_payload 就造双节点：node[0] flags 4 / node[1] flags 0 / offset 紧接。"""
    stream = b"S" * 5000
    payload = b"P" * 3000
    plan = builder.build_unityfs(stream, "CAB-abc", stream_payload=payload)
    with _MemReader(plan.data) as r:
        nodes = [(n.offset, n.size, n.flags, n.path) for n in r.nodes]
        assert [n[3] for n in nodes] == ["CAB-abc", "CAB-abc.resS"]
        assert [n[0] for n in nodes] == [0, len(stream)]
        assert [n[1] for n in nodes] == [len(stream), len(payload)]
        assert [n[2] for n in nodes] == [4, 0]
        assert sum(b.uncompressed for b in r.blocks) == len(stream) + len(payload)
        assert r.read_range(0, len(stream)) == stream
        assert r.read_range(len(stream), len(payload)) == payload
    assert plan.stream_node_offset == len(stream)
    assert plan.stream_node_size == len(payload)


def test_build_unityfs_without_stream_payload_is_unchanged() -> None:
    """⛔ 只加不改：不给 stream_payload 时，字节与读数与以前一模一样（单节点）。"""
    stream = b"S" * 5000
    a = builder.build_unityfs(stream, "CAB-abc")
    b = builder.build_unityfs(stream, "CAB-abc", stream_payload=None)
    assert a.data == b.data
    assert a.stream_node_path == "" and a.stream_node_offset == -1 and a.stream_node_size == 0
    with _MemReader(a.data) as r:
        assert [n.path for n in r.nodes] == ["CAB-abc"]


def test_node0_content_is_identical_with_and_without_the_stream_node() -> None:
    """加了 .resS 之后，node[0] 的**内容**逐字节不变（只多挂一个节点）。"""
    stream = b"S" * 5000
    one = builder.build_unityfs(stream, "CAB-abc")
    two = builder.build_unityfs(stream, "CAB-abc", stream_payload=b"P" * 3000)
    with _MemReader(one.data) as r1, _MemReader(two.data) as r2:
        assert r1.read_range(0, len(stream)) == r2.read_range(0, len(stream)) == stream


def test_negative_control_empty_stream_payload_raises() -> None:
    with pytest.raises(Exception, match="stream_payload"):
        builder.build_unityfs(b"S" * 100, "CAB-abc", stream_payload=b"")


# --------------------------------------------------------------------------- ④ 走 .resS 的往返
def _first_texture(reader: ufs.UfsReader) -> tuple[dict, int]:
    node0 = reader.nodes[0]
    info = ufs.read_serialized_file(reader, node0)
    meta = mw.parse_serialized_metadata(bytes(reader.read_range(node0.offset, info.data_offset)))
    cid = {i: t.class_id for i, t in enumerate(meta.types)}
    tid = meta.find_type_ids(28)[0]
    tt = meta.types[tid].type_tree
    blob = struct.pack("<ii", tt.node_count, tt.string_buffer_size) + tt.nodes + tt.strings
    nodes = clone.read_type_nodes(blob, common_strings=clone.unitypy_common_strings())
    obj = [o for o in info.objects if cid[o[4]] == 28][0]
    raw = bytes(reader.read_range(node0.offset + info.data_offset + obj[2], obj[3]))
    return clone.read_object(raw, nodes), int(obj[1])


def _build_texture_bundle(tmp_path: Path, source_meta: bytes, payload: bytes, *, mips: int):
    bundle_name = "p3tex_test_assets_all.bundle"
    node = builder.node_path_for(bundle_name)
    fields = tb.build_streaming_texture_fields(
        "p3tex_test", W, H, texture_format=FMT, mip_count=mips, size=len(payload),
        stream_path=f"archive:/{node}/{node}.resS", color_space=1, filter_mode=1,
    )
    pid = builder.path_id_for("p3tex/test")
    plan = builder.build_object_bundle(
        objects=[builder.ObjectSpec(class_id=28, fields=fields, path_id=pid, label="tex")],
        bundle_name=bundle_name,
        source_meta=source_meta,
        container=[("Assets/P3Tex/test.png", pid)],
        block_type=builder.BLOCK_LZ4HC,
        stream_payload=payload,
    )
    path = tmp_path / bundle_name
    path.write_bytes(plan.data)
    with unityfs.open_bundle(path) as reader:
        tree, tex_pid = _first_texture(reader)
    return path, tree, tex_pid


def test_streamed_texture_roundtrips_through_resS(tmp_path: Path, source_meta: bytes) -> None:
    """造包 -> 用产品的读侧从 .resS 读回来 -> 逐字节相同，且 image data 是空的。"""
    payload = _payload()
    bundle, tree, pid = _build_texture_bundle(tmp_path, source_meta, payload, mips=MIPS)
    with unityfs.open_bundle(bundle) as reader:
        info = tex.read_texture_2d(tree, bundle=reader, path_id=pid)
        assert info.streamed is True, "字节必须来自 .resS（不是内联）"
        assert info.raw == payload
        assert (info.width, info.height, info.mip_count) == (W, H, MIPS)
    assert tree["image data"] == b""
    assert int(tree["m_StreamData"]["size"]) == len(payload)
    assert int(tree["m_CompleteImageSize"]) == len(payload)


def test_check_texture_streaming_is_green_then_red_on_a_tampered_field(
    tmp_path: Path, source_meta: bytes
) -> None:
    """产品的流式自洽尺子：正控绿；把 m_CompleteImageSize 改错 16 字节 -> **必须红**。"""
    _bundle, tree, _pid = _build_texture_bundle(tmp_path, source_meta, _payload(), mips=MIPS)
    good = textures.check_texture_streaming(tree)
    assert good["ok"] is True, good["problems"]
    bad = dict(tree)
    bad["m_CompleteImageSize"] = int(tree["m_CompleteImageSize"]) + 16
    verdict = textures.check_texture_streaming(bad)
    assert verdict["ok"] is False
    assert any("m_CompleteImageSize" in p for p in verdict["problems"])


def test_negative_control_mip_count_mismatch_goes_red(tmp_path: Path, source_meta: bytes) -> None:
    """负控①（判据那一侧）：级数写成 6 而字节是满 7 级 -> check_texture_streaming 必须红。"""
    _bundle, tree, _pid = _build_texture_bundle(tmp_path, source_meta, _payload(), mips=MIPS)
    bad = dict(tree)
    bad["m_MipCount"] = MIPS - 1
    assert textures.check_texture_streaming(bad)["ok"] is False
