r"""资产目录（core.assets.graph / cli.asset_graph）：纯函数 + 目录定位 + 命令契约。

**判据一行**：.venv\Scripts\python.exe -m pytest tests/test_asset_graph.py -q -n 0
-> 收集用例数 **从 0（本文件之前不存在）变 N**，且全 passed。

| 用例 | 判据 |
|---|---|
| 条目表 | 六类各多少条、每条带 **可读名 + pathID + 出处** |
| 计数口径 | 渲染器/材质/贴图 = **树可达**（不是包内总数）；空 PPtr 不计 |
| **正控（尺子能红）** | 从树里去掉一个渲染器 ⇒ 那个读数**必须变**；
两处引用同一颗材质 ⇒ 材质数**不变**（去重）；读源多给的材质**不算** |
| catalog 定位 | 真格式的 catalog.json（夹具按 Addressables 1.x 紧凑格式编码） ⇒ 资产 + 候选包 |
| **负控** | 键不在 catalog ⇒ AssetError **含「没有这个键」**（⛔ 不是空表） |
| 命令契约 | run()：空键 / 找不到游戏根 / 键不存在 ⇒ **退出码 2**；负控时 **stdout 上什么都没有** |
| --json | 失败时 stdout 是 ok:false 的 JSON 文档（人读那份走 stderr） |
| VT 池 | pool_state_of 三态分得开：目录不在 / 池子缺 / 就位 |
| 分层 | core/assets/graph.py **模块级**不 import asset_kernel（延迟 import 是刻意的） |

⚠️ **真件（units 3.38 GB，只读）不在用例里**：重活一把锁（AGENTS §0 + §8.1），
真读数由 _work/assetgraph/probe_unit_assets.py（独立探针）与真跑对拍，见任务汇报。
本文件**不写游戏目录**：game_root 全是 tmp_path 造出来的。
"""

from __future__ import annotations

import ast
import base64
import json
import struct
from pathlib import Path

import pytest

from ba_mod_maker.cli import asset_graph as cli
from ba_mod_maker.core.assets import game as game_mod
from ba_mod_maker.core.assets import graph as g
from ba_mod_maker.core.errors import AssetError

# --------------------------------------------------------------------------- #
# 夹具：归一化节点（prefab.build_tree 的契约）
# --------------------------------------------------------------------------- #
MAT_BODY = -6340630412728879988
MAT_TRACK = 7952762356820983200
MAT_LOD = 8349683979956743758
TEX_BASE = 1084419494171551337
TEX_TRACK_ALBEDO = 6513056008332064871
TEX_TRACK_NORMAL = 5509599546982847501
PRE_FAB_PATH_ID = -3612445505056865496
VT_GROUP = "f51bbaf7a40a0ab4909c16ff519cc9cf"
VT_ITEM = "b6d28b038f94ef604eb1177da3182bf5"
ASSET_PATH = "Assets/Resources_moved/ModelPrefabs/RU/RU_T72B_Base.prefab"
BUNDLE = "units_assets_all_deadbeef.bundle"


def _smr(tid, comp_id, name, mats, mesh=0, mesh_file=0, parent=100):
    return {
        "name": name,
        "path_id": tid * 10,
        "transform_id": tid,
        "parent_transform": parent,
        "position": (0.0, 0.0, 0.0),
        "rotation": (0.0, 0.0, 0.0, 1.0),
        "scale": (1.0, 1.0, 1.0),
        "components": (
            {
                "kind": "SkinnedMeshRenderer",
                "path_id": comp_id,
                "mesh": mesh,
                "mesh_file": mesh_file,
                "materials": mats,
                "bones": (),
            },
        ),
    }


def _plain(tid, name, parent=100, components=()):
    return {
        "name": name,
        "path_id": tid * 10,
        "transform_id": tid,
        "parent_transform": parent,
        "position": (0.0, 0.0, 0.0),
        "rotation": (0.0, 0.0, 0.0, 1.0),
        "scale": (1.0, 1.0, 1.0),
        "components": tuple(components),
    }


def _materials():
    return {
        MAT_BODY: {
            "name": "RU_T72B 1",
            "shader": "Shader Graphs/UnitV3",
            "texture_slots": (("Layer_A97CDC25", TEX_BASE), ("_GlobalDirtNormal", None)),
        },
        MAT_TRACK: {
            "name": "RU_T72B_track",
            "shader": "HDRP/Lit",
            "texture_slots": (
                ("_BaseColorMap", TEX_TRACK_ALBEDO),
                ("_MainTex", TEX_TRACK_ALBEDO),
                ("_NormalMap", TEX_TRACK_NORMAL),
            ),
        },
        MAT_LOD: {"name": "LODs", "shader": "HDRP/Unlit", "texture_slots": ()},
    }


def _nodes():
    """一棵缩到 3 个渲染器的 RU_T72B_Base（真件是 13 个，形状一样）。"""
    lod = _plain(
        103,
        "lod_1x1x1",
        parent=101,
        components=(
            {"kind": "MeshFilter", "path_id": 1030, "mesh": -1, "mesh_file": 2},
            {"kind": "MeshRenderer", "path_id": 9194953101066830632, "materials": (MAT_LOD,)},
        ),
    )
    return (
        _plain(100, "RU_T72B_Base", parent=None),
        _plain(101, "Chassis", parent=100),
        _smr(102, -8003744623352085720, "Chassis", (MAT_BODY,), mesh=-101627207120243360),
        _smr(104, 9133493082246087464, "RU_TanktrakL", (MAT_TRACK,), parent=101),
        lod,
    )


def _tree(nodes=None, materials=None):
    from ba_mod_maker.asset_kernel import prefab

    return prefab.build_tree(nodes or _nodes(), materials or _materials())


def _nodes_of(tree, **kwargs):
    kwargs.setdefault("asset_path", ASSET_PATH)
    kwargs.setdefault("prefab_path_id", PRE_FAB_PATH_ID)
    kwargs.setdefault("bundle_name", BUNDLE)
    kwargs.setdefault("texture_names", {TEX_BASE: "RU_T72B_1_BaseMap"})
    kwargs.setdefault("vt_items", {MAT_BODY: ((VT_GROUP, VT_ITEM),)})
    return g.nodes_from_tree(tree, **kwargs)


def _kinds(nodes):
    out: dict[str, int] = {}
    for node in nodes:
        out[node.kind] = out.get(node.kind, 0) + 1
    return out


# --------------------------------------------------------------------------- #
# 条目表与计数口径
# --------------------------------------------------------------------------- #
def test_six_kinds_all_present() -> None:
    nodes = _nodes_of(_tree())
    assert _kinds(nodes) == {
        "bundle": 1,
        "prefab": 1,
        "renderer": 3,
        "material": 3,
        "texture": 3,
        "vt": 1,
    }


def test_every_entry_carries_name_pathid_and_source() -> None:
    """**可读名 + pathID + 出处在哪个包** —— 三者缺一不可。"""
    for node in _nodes_of(_tree()):
        assert node.name, node
        assert node.source, node
        if node.kind in ("prefab", "renderer", "material", "texture"):
            assert node.path_id != 0, node


def test_prefab_entry_is_the_container_address() -> None:
    nodes = _nodes_of(_tree())
    prefab_node = [n for n in nodes if n.kind == "prefab"][0]
    assert prefab_node.path_id == PRE_FAB_PATH_ID
    assert prefab_node.name == "RU_T72B_Base.prefab"
    assert prefab_node.source == BUNDLE


def test_bundle_entry_reports_container_counts() -> None:
    """容器总条数 与 唯一路径数 都印出来（表允许同一路径登记两次）。"""
    nodes = _nodes_of(_tree(), container_count=1393, container_unique=1362)
    bundle = [n for n in nodes if n.kind == "bundle"][0]
    assert "容器 1393 条地址" in bundle.detail
    assert "唯一路径 1362" in bundle.detail


def test_texture_records_which_material_slot_uses_it() -> None:
    nodes = _nodes_of(_tree())
    base = [n for n in nodes if n.path_id == TEX_BASE][0]
    assert base.name == "RU_T72B_1_BaseMap"
    assert "RU_T72B 1/Layer_A97CDC25" in base.detail


def test_empty_pptrs_are_not_counted() -> None:
    """_GlobalDirtNormal -> None 是空槽：⛔ 不许算成一张贴图。"""
    assert len([n for n in _nodes_of(_tree()) if n.kind == "texture"]) == 3


def test_vt_entry_shows_pool_state() -> None:
    nodes = _nodes_of(_tree(), vt_pool_state={VT_GROUP: "池子就位：1 个 .gts"})
    vt = [n for n in nodes if n.kind == "vt"][0]
    assert vt.name == f"{VT_GROUP}/{VT_ITEM}"
    assert "池子就位" in vt.detail
    assert "RU_T72B 1" in vt.detail


def test_counts_and_document_are_consistent() -> None:
    graph = g.UnitAssetGraph(
        key="RU_T72B",
        asset_path=ASSET_PATH,
        bundle_name=BUNDLE,
        bundle_path="D:/x/" + BUNDLE,
        catalog_path="D:/x/catalog.json",
        nodes=_nodes_of(_tree()),
    )
    assert graph.counts()["renderer"] == 3
    assert graph.document()["counts"] == graph.counts()
    assert any("RU_T72B_Base.prefab" in line for line in graph.lines())


# --------------------------------------------------------------------------- #
# 正控：尺子必须先能红（改一个变量，读数必须跟着变）
# --------------------------------------------------------------------------- #
def test_renderer_count_follows_the_tree() -> None:
    """去掉一个渲染器 ⇒ 渲染器数 **3 -> 2**（尺子看得见）。"""
    nodes = [n for n in _nodes() if n["transform_id"] != 104]
    assert _kinds(_nodes_of(_tree()))["renderer"] == 3
    assert _kinds(_nodes_of(_tree(nodes)))["renderer"] == 2


def test_material_count_is_deduped_not_summed() -> None:
    """两个渲染器引同一颗材质 ⇒ 材质数 **1**（不是 2）—— 去重是判据的一部分。"""
    nodes = (
        _plain(100, "root", parent=None),
        _smr(101, 111, "A", (MAT_BODY,)),
        _smr(102, 222, "B", (MAT_BODY,)),
    )
    got = _kinds(_nodes_of(_tree(nodes)))
    assert got["renderer"] == 2
    assert got["material"] == 1


def test_texture_count_is_deduped_across_slots() -> None:
    """同一张贴图挂在两个槽上（真件 RU_T72B_track 就是）⇒ 只算 1 张。"""
    nodes = (_plain(100, "root", parent=None), _smr(101, 111, "A", (MAT_TRACK,)))
    assert _kinds(_nodes_of(_tree(nodes)))["texture"] == 2  # albedo（两个槽）+ normal


def test_positive_control_on_the_name_lookup() -> None:
    """**正控**：喂空的名字表 ⇒ 贴图那条显示占位名，而不是悄悄用旧的。"""
    nodes = _nodes_of(_tree(), texture_names={})
    base = [n for n in nodes if n.path_id == TEX_BASE][0]
    assert base.name == f"<无名 pathID={TEX_BASE}>"


# --------------------------------------------------------------------------- #
# catalog 夹具：按 Addressables 1.x 紧凑格式真编码一份（喂的是**真解析器**）
# --------------------------------------------------------------------------- #
def _encode_key(text: str) -> bytes:
    raw = text.encode("utf-8")
    return b"\x00" + struct.pack("<i", len(raw)) + raw


def _write_catalog(aa_dir: Path, keys, buckets, entries, internal_ids) -> Path:
    """keys/buckets/entries/internal_ids -> aa/catalog.json（真格式）。

    :param entries: 每条 (internal_ids 下标, 依赖的组 key 下标)
    """
    key_data = bytearray()
    offsets = []
    for key in keys:
        offsets.append(len(key_data))
        key_data += _encode_key(key)
    bucket_data = bytearray(struct.pack("<i", len(keys)))
    for key_index in range(len(keys)):
        bucket_data += struct.pack("<ii", offsets[key_index], len(buckets[key_index]))
        for entry_index in buckets[key_index]:
            bucket_data += struct.pack("<i", entry_index)
    fields = 3
    entry_data = bytearray(struct.pack("<i", len(entries)))
    for internal, group in entries:
        entry_data += struct.pack(f"<{fields}i", internal, 0, group)
    aa_dir.mkdir(parents=True, exist_ok=True)
    path = aa_dir / "catalog.json"
    path.write_text(
        json.dumps(
            {
                "m_KeyDataString": base64.b64encode(bytes(key_data)).decode("ascii"),
                "m_BucketDataString": base64.b64encode(bytes(bucket_data)).decode("ascii"),
                "m_EntryDataString": base64.b64encode(bytes(entry_data)).decode("ascii"),
                "m_InternalIds": list(internal_ids),
            }
        ),
        encoding="utf-8",
    )
    return path


def _game_root(tmp_path: Path, *, keys, buckets, entries, internal_ids) -> Path:
    """造一个够 is_game_root 认的游戏根（catalog 是真格式的）。"""
    aa = tmp_path / "BrokenArrow_Data" / "StreamingAssets" / "aa"
    _write_catalog(aa, keys, buckets, entries, internal_ids)
    return tmp_path


def _one_unit_root(tmp_path: Path, key: str = "RU_T72B") -> Path:
    return _game_root(
        tmp_path,
        keys=[BUNDLE, key],
        buckets=[[0], [1]],
        entries=[(0, 0), (1, 0)],
        internal_ids=[f"aa/PC/{BUNDLE}", ASSET_PATH],
    )


# --------------------------------------------------------------------------- #
# 定位：正控 + 负控
# --------------------------------------------------------------------------- #
def test_locate_returns_asset_and_candidates(tmp_path: Path) -> None:
    asset, candidates, catalog, aa_dir = g.locate(_one_unit_root(tmp_path), "RU_T72B")
    assert asset == ASSET_PATH
    assert candidates == (BUNDLE,)
    assert catalog.endswith("catalog.json")
    assert aa_dir.endswith("PC")


def test_locate_negative_control_says_the_key_is_missing(tmp_path: Path) -> None:
    """**负控（必须报红）**：键不在 catalog ⇒ AssetError 且话里要有「没有这个键」。"""
    with pytest.raises(AssetError) as caught:
        g.locate(_one_unit_root(tmp_path), "NO_SUCH_KEY_T5")
    assert "没有这个键" in str(caught.value)
    assert "NO_SUCH_KEY_T5" in str(caught.value)


def test_locate_rejects_two_assets_for_one_key(tmp_path: Path) -> None:
    """一个键指向两个资产 ⇒ 不是唯一命中（⛔ 不许猜一个）。"""
    root = _game_root(
        tmp_path,
        keys=[BUNDLE, "RU_T72B"],
        buckets=[[0], [1, 2]],
        entries=[(0, 0), (1, 0), (2, 0)],
        internal_ids=[f"aa/PC/{BUNDLE}", ASSET_PATH, "Assets/other.prefab"],
    )
    with pytest.raises(AssetError) as caught:
        g.locate(root, "RU_T72B")
    assert "不是唯一命中" in str(caught.value)


def test_locate_without_catalog_is_an_error_not_an_empty_table(tmp_path: Path) -> None:
    with pytest.raises(AssetError) as caught:
        g.locate(tmp_path, "RU_T72B")
    assert "读不到 catalog" in str(caught.value)


# --------------------------------------------------------------------------- #
# 游戏根探测：catalog 换了名字（2026-10-13 · 1.2.1 openbeta）
# --------------------------------------------------------------------------- #
def _aa(tmp_path: Path) -> Path:
    aa = tmp_path / "BrokenArrow_Data" / "StreamingAssets" / "aa"
    aa.mkdir(parents=True, exist_ok=True)
    return aa


def test_game_root_accepts_the_binary_catalog(tmp_path: Path) -> None:
    """**正控**：aa/ 下只有 catalog.bin（1.2.1 的形状）也要认得。

    出处：2026-10-13 真机 —— 1.2.1 把 catalog.json 换成 catalog.bin 之后，
    `find_game_root()` 恒返回 None ⇒ `tools/game_log.py --new-only` 报
    「找不到游戏目录」而 `GameLogs/` 明明在。根因在 `is_game_root` 只认一个文件名。
    """
    _aa(tmp_path).joinpath("catalog.bin").write_bytes(b"\x00\x01\x02")
    assert game_mod.is_game_root(tmp_path) is True
    assert game_mod.catalog_file(tmp_path).name == "catalog.bin"


def test_game_root_still_accepts_the_json_catalog(tmp_path: Path) -> None:
    """**负控的反面**：1.2.0 及以前的形状（catalog.json）不许被这次改坏。"""
    _aa(tmp_path).joinpath("catalog.json").write_text("{}", encoding="utf-8")
    assert game_mod.is_game_root(tmp_path) is True
    assert game_mod.catalog_file(tmp_path).name == "catalog.json"


def test_game_root_negative_control_needs_a_catalog_file(tmp_path: Path) -> None:
    """**负控（尺子必须能红）**：aa/ 在、bundle 在，但**没有 catalog** ⇒ 不是游戏根。"""
    _aa(tmp_path)
    (tmp_path / "BrokenArrow_Data" / "StreamingAssets" / "aa" / "PC").mkdir()
    assert game_mod.is_game_root(tmp_path) is False


def test_catalog_file_prefers_json_when_both_are_present(tmp_path: Path) -> None:
    """两个都在时优先 JSON（那是我们能逐字节读写的那个形状）。"""
    aa = _aa(tmp_path)
    aa.joinpath("catalog.json").write_text("{}", encoding="utf-8")
    aa.joinpath("catalog.bin").write_bytes(b"\x00")
    assert game_mod.catalog_file(tmp_path).name == "catalog.json"


# --------------------------------------------------------------------------- #
# build_unit_graph：注入一个读源（离线，不开真包）
# --------------------------------------------------------------------------- #
class _FakeSource:
    """够 build_unit_graph 用的读源（真件是 unitypy_source.AssetSource）。"""

    def __init__(self, container_path: str) -> None:
        self.path = Path("D:/x") / BUNDLE
        self._types = {
            TEX_BASE: {"m_Name": "RU_T72B_1_BaseMap"},
            TEX_TRACK_ALBEDO: {"m_Name": "RU_T72B_track_Albedo"},
            TEX_TRACK_NORMAL: {"m_Name": "RU_T72B_track_Normal"},
            MAT_BODY: {"m_BuildTextureStacks": ({"groupName": VT_GROUP, "itemName": VT_ITEM},)},
            MAT_TRACK: {"m_BuildTextureStacks": ()},
            MAT_LOD: {},
        }
        self._container = container_path

    def read_prefab_nodes(self, asset_path):
        assert asset_path == self._container
        return _nodes(), _materials()

    def container_entries(self):
        return ((self._container, PRE_FAB_PATH_ID),)

    def read_typetree(self, path_id):
        return self._types[int(path_id)]


def test_build_unit_graph_assembles_the_six_kinds(tmp_path: Path) -> None:
    root = _one_unit_root(tmp_path)
    built = g.build_unit_graph(root, "RU_T72B", cached=_FakeSource(ASSET_PATH))
    counts = built.counts()
    assert counts["renderer"] == 3
    assert counts["material"] == 3
    assert counts["texture"] == 3
    assert counts["vt"] == 1
    names = {n.path_id: n.name for n in built.of_kind("texture")}
    assert names[TEX_TRACK_NORMAL] == "RU_T72B_track_Normal"
    assert "外部 fileID=2" in " ".join(built.notes + tuple(n.detail for n in built.nodes))


# --------------------------------------------------------------------------- #
# 命令契约（cli.asset_graph.run）
# --------------------------------------------------------------------------- #
def _args(*, key: str, game: str | None = None, json_out: bool = False):
    import argparse

    return argparse.Namespace(key=key, game=game, json_out=json_out)


def test_run_without_key_is_unusable(tmp_path: Path, capsys) -> None:
    assert cli.run(_args(key="", game=str(tmp_path))) == cli.EXIT_UNUSABLE
    assert "要给一个地址键" in capsys.readouterr().err


def test_run_without_game_root_is_unusable(tmp_path: Path, capsys) -> None:
    assert cli.run(_args(key="RU_T72B", game=str(tmp_path))) == cli.EXIT_UNUSABLE
    assert "找不到游戏根目录" in capsys.readouterr().err


def test_run_negative_control_prints_nothing_on_stdout(tmp_path: Path, capsys) -> None:
    """**J-T5-2**：键不存在 ⇒ 退出码 2 ＋ stderr 一句「没有这个键」＋ **stdout 空**。

    ⛔ 空表是最坏的结果：它会被读成「这台车没有资产」。
    """
    code = cli.run(_args(key="NO_SUCH_KEY_T5", game=str(_one_unit_root(tmp_path))))
    captured = capsys.readouterr()
    assert code == cli.EXIT_UNUSABLE
    assert "没有这个键" in captured.err
    assert captured.out == ""


def test_run_negative_control_in_json_mode_still_has_a_document(tmp_path: Path, capsys) -> None:
    code = cli.run(_args(key="NO_SUCH_KEY_T5", game=str(_one_unit_root(tmp_path)), json_out=True))
    captured = capsys.readouterr()
    assert code == cli.EXIT_UNUSABLE
    payload = json.loads(captured.out)  # stdout 上永远只有 JSON
    assert payload["ok"] is False
    assert "没有这个键" in payload["error"]["message"]


def test_run_ok_writes_the_table_and_a_document(tmp_path: Path, capsys, monkeypatch) -> None:
    """把开包那一步换掉（离线）：命令本身照跑，人读表与 --json 两份都要出。"""
    root = _one_unit_root(tmp_path)

    def _fake(game_root, key, **_kw):
        return g.UnitAssetGraph(
            key=key,
            asset_path=ASSET_PATH,
            bundle_name=BUNDLE,
            bundle_path=str(root),
            catalog_path=str(root),
            nodes=_nodes_of(_tree()),
        )

    monkeypatch.setattr(g, "build_unit_graph", _fake)
    assert cli.run(_args(key="RU_T72B", game=str(root))) == cli.EXIT_OK
    assert "RU_T72B_Base.prefab" in capsys.readouterr().out

    assert cli.run(_args(key="RU_T72B", game=str(root), json_out=True)) == cli.EXIT_OK
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["ok"] is True
    assert payload["counts"]["renderer"] == 3
    assert "RU_T72B_Base.prefab" in captured.err  # 人读那份改走 stderr


# --------------------------------------------------------------------------- #
# VT 池三态 + 分层
# --------------------------------------------------------------------------- #
def test_pool_state_of_three_states(tmp_path: Path) -> None:
    streaming = tmp_path / "BrokenArrow_Data" / "StreamingAssets"
    assert "StreamingAssets 不在" in g.pool_state_of(streaming, VT_GROUP)
    streaming.mkdir(parents=True)
    assert "池子缺" in g.pool_state_of(streaming, VT_GROUP)
    (streaming / f"{VT_GROUP}.gts").write_bytes(b"GRPG" + bytes(8))
    (streaming / f"{VT_GROUP}0_1.gtp").write_bytes(b"GRAP" + bytes(16))
    got = g.pool_state_of(streaming, VT_GROUP)
    assert "池子就位" in got
    assert "1 个 .gts" in got and "1 个 .gtp" in got


def test_core_graph_does_not_import_asset_kernel_at_module_level() -> None:
    """分层：core/assets/graph.py 只在**函数内**拉 asset_kernel（延迟 import 是刻意的）。

    理由：asset_kernel 反过来要 core.assets.game（writer / skin_install / vt_stack 都这样），
    模块级互引就成环；而且 --help 不该付 UnityPy 的启动代价。
    """
    path = Path(g.__file__)
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    module_level = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            module_level.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module_level.append(node.module or "")
    bad = [name for name in module_level if name.startswith("ba_mod_maker.asset_kernel")]
    assert not bad, f"{path.name} 的模块级 import 里出现了 asset_kernel：{module_level}"
