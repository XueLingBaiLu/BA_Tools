"""W8 · ufs 类型表游标的用例（两条判据 + 两条正控）。

判据一行：

    pytest tests/test_ufs_types_cursor.py -q          -> 日常档全绿（不碰游戏文件）
    pytest tests/test_ufs_types_cursor.py -q -m slow  -> prefabsgui / prefabsme 读得开（482 / 342）
                                                         units 上两条读器 162,010 行 diff 0

被修的缺陷（2026-10-08，W3 报）：core/asset/ufs.py::read_serialized_file 的类型表游标
在 prefabsgui / prefabsme 上跑飞 —— 判据写成了 script_type_index >= 0 才读 16 字节 script_id，
而真判据是 classID == 114。全库 79 包实测：有 6 项 classID=114 且 script_type_index = -1
（prefabsgui 2 / prefabsme 4），按前者判就少读 16 字节 ⇒ 游标错位 ⇒ 下一项 node_count
读成垃圾 ⇒ 跑到 17,063,036（元数据只有 3,583,522 B）⇒ 裸 struct.error。
"""

from __future__ import annotations

import contextlib
import os
import struct
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from ba_mod_maker.asset_kernel import clone as cl
from ba_mod_maker.asset_kernel import metadata_writer as mw
from ba_mod_maker.core.asset import ufs
from ba_mod_maker.core.errors import AssetError

# 工作根：数据已移出仓（2026-11-09，统一入口）。
WORK = Path(os.environ.get("BA_WORK_ROOT") or r"D:\断箭模组制作\_work_db")

REPO_ROOT = Path(__file__).resolve().parents[1]
LOCK_DIR = WORK / "locks"
LOCK_WAIT_SECONDS = float(os.environ.get("BA_MOD_MAKER_MONO_LOCK_WAIT", "900"))

if str(LOCK_DIR) not in sys.path:
    sys.path.insert(0, str(LOCK_DIR))

#: 真件读数（_work/mono-line/ufs-types-census.py 复算）：这两个包的类型表项数。
#: ★ 2026-10-18 重记：Steam 2026-10-17 11:30 更新过游戏 ⇒ `prefabsme` 342 → **339**
#:   （prefabsgui 482 没变）；`units` 对象 162010 → **161525**、类型 51 → 50、
#:   包名 `…ebe39db6…` → `…43d88558…`。复算：`python _work/probe_numbers.py`（单进程）。
PREFABSGUI_TYPES = 482
PREFABSME_TYPES = 339
#: 真件读数：units 的对象数（两条读器必须一样）。
UNITS_OBJECTS = 161525

SENTINEL = 0x0BADF00D  # < 2^31，有符号读回来还是它（_Cursor.i32 走 "<i"）


def _pack(name_prefix: str) -> Path | None:
    from ba_mod_maker.core.assets.game import bundle_dir, find_game_root

    root = find_game_root()
    if root is None:
        return None
    hits = sorted(bundle_dir(root).glob(f"{name_prefix}*.bundle"))
    return hits[0] if hits else None


def _entry(class_id: int, script_index: int, *, with_script_id: bool) -> bytes:
    out = struct.pack("<iBh", class_id, 0, script_index)
    if with_script_id:
        out += b"\xAA" * 16  # script_id
    return out + b"\x00" * 16  # old_type_hash


#: 一段**手搓的类型表**：3 项 + 表后一个哨兵。两套规则在这段上走出的长度**不同** ——
#:   真规则（classID == 114 才有 script_id）：4 + 39 + 23 + 23 = 89
#:   旧规则（script_index >= 0 才有）        ：4 + 23 + 39 + 39 = 105
#: 所以"哨兵能不能读对"这一条断言在旧规则下必然红。
TABLE_BLOB = (
    struct.pack("<i", 3)
    + _entry(114, -1, with_script_id=True)
    + _entry(42, 3, with_script_id=False)
    + _entry(42, 7, with_script_id=False)
    + struct.pack("<I", SENTINEL)
    # 尾巴留 40 字节：旧规则会**多走 16 字节**，没有这块它就越界了 ——
    # 那样子对比就变成"报不报错"，而不是"走到哪"（这里要的是后者）。
    + b"\x00" * 40
)
TABLE_END = 89
TABLE_END_OLD_RULE = 105


def _walk_with_old_rule(blob: bytes) -> int:
    """旧规则（script_type_index >= 0 才读 script_id）—— **只用来证明上面那条断言能红**。"""
    cur = ufs._Cursor(blob, "<", 0)
    count = cur.i32()
    for _ in range(count):
        cur.i32()  # class_id
        cur.u8()  # is_stripped
        if cur.i16() >= 0:
            cur.take(16)
        cur.take(16)
    return cur.pos


# --------------------------------------------------------------------------- #
# 判据 ①（日常档）：游标越界必须是**可读的** AssetError
# --------------------------------------------------------------------------- #
def test_cursor_reports_bounds_instead_of_struct_error() -> None:
    buf = b"\x01\x02\x03"
    cur = ufs._Cursor(buf, "<", 0)
    with pytest.raises(AssetError) as info:
        cur.i32()
    assert "越界" in str(info.value)
    assert info.value.context["buffer_size"] == len(buf)
    assert info.value.context["need"] == 4

    with pytest.raises(AssetError):
        ufs._Cursor(buf, "<", 3).i64()
    with pytest.raises(AssetError):
        ufs._Cursor(buf, "<", 0).take(4)
    with pytest.raises(AssetError):
        ufs._Cursor(b"abc", "<", 0).cstr()  # 没有结尾的 0
    with pytest.raises(AssetError):
        ufs._Cursor(buf, "<", 2).align(4)  # 对齐补零放不下

    # 正控：同一个越界，裸 struct.unpack_from 抛的是 struct.error（证明上面那条负控不是空转）
    with pytest.raises(struct.error):
        struct.unpack_from("<i", buf, 0)
    assert not issubclass(struct.error, AssetError)

    # 边界仍然要放行：正好读到末尾不算越界
    assert ufs._Cursor(buf, "<", 0).take(3) == buf
    assert ufs._Cursor(buf, "<", 3).take(0) == b""


# --------------------------------------------------------------------------- #
# 判据 ②（日常档）：script_id 的判据是 classID，不是 script_type_index
# --------------------------------------------------------------------------- #
def test_type_table_script_id_follows_class_id() -> None:
    cur = ufs._Cursor(TABLE_BLOB, "<", 0)
    count = ufs._walk_type_table(cur, 22, False)
    assert count == 3
    assert cur.pos == TABLE_END, (
        f"类型表走完停在 {cur.pos}，按 classID == 114 的规则应当是 {TABLE_END}"
    )
    # 表后那一格必须还读得对：游标错位的话这里读到的就是别的字节
    assert cur.i32() == SENTINEL

    # 这条 fixture **能区分两套规则**（否则上面那条断言是假绿）
    assert _walk_with_old_rule(TABLE_BLOB) == TABLE_END_OLD_RULE != TABLE_END


# --------------------------------------------------------------------------- #
# 判据 ③（日常档）：元数据砍 1 字节 ⇒ 可读的 AssetError（不是裸 struct.error）
# --------------------------------------------------------------------------- #
def _tiny_serialized_file() -> tuple[mw.SerializedMetadata, bytes]:
    """一份最小 SerializedFile（不开类型树）：1 项类型 + 1 个对象。

    ⚠ classID=114 那一项故意带 script_type_index = -1 —— 正是真件里那 6 项的形状。
    """
    meta = mw.SerializedMetadata(
        header=mw.SerializedFileHeader(0, 0, 22, 0, 0, b"\x00" * 3, 0, 0, 0, 0),
        unity_version="2022.3.62f3",
        target_platform=19,
        enable_type_tree=False,
        types=[
            mw.SerializedType(
                class_id=114,
                is_stripped=False,
                script_type_index=-1,
                script_id=b"\xAA" * 16,
                old_type_hash=b"\x00" * 16,
                type_tree=None,
            )
        ],
        objects=[mw.ObjectEntry(path_id=7, byte_start=0, byte_size=4, type_id=0)],
        script_types=[],
        externals=[],
        ref_types=[],
        user_information="",
    )
    # build_metadata 不负责 file_size（它指整个节点）：先造一次拿长度，再带上去重造一次。
    region = mw.build_metadata(meta)
    meta.header.file_size = len(region)
    return meta, mw.build_metadata(meta)


class _StubReader:
    """只实现 read_range 的桩：read_serialized_file 只用到这一个方法。"""

    def __init__(self, blob: bytes) -> None:
        self.blob = blob

    def read_range(self, offset: int, size: int) -> bytes:
        # 故意**不**做区间检查：模拟"调用方手上就是一段短字节"
        return self.blob[offset : offset + size]


def _stub_node(size: int) -> object:
    class _Node:
        offset = 0
        path = "stub"

    _Node.size = size
    return _Node()


def _type_table_end(blob: bytes) -> int:
    """走完类型表之后的游标位置（用来精确地"砍在表里"）。"""
    cur = _type_table_cursor(blob)
    ufs._walk_type_table(cur, 22, False)
    return cur.pos

def _type_table_cursor(blob: bytes) -> ufs._Cursor:
    """把游标停在类型表开头（越过元数据体的前三个字段）。"""
    cur = ufs._Cursor(blob, "<", 48)
    cur.cstr()  # unity_version
    cur.i32()  # target_platform
    cur.u8()  # enable_type_tree
    return cur


def test_truncated_metadata_raises_readable_asset_error() -> None:
    """整条 read_serialized_file 的路径上，被砍过的字节必须给可读的 AssetError。

    ⚠ 这里用一个**只实现 read_range 的桩读器**（真 UfsReader 在"区间越界"时就先抛了，
    到不了游标这一层）—— 桩返回的是**短缓冲区**，正是"调用方拿到一段被截断的字节"的形状。
    """
    _meta, region = _tiny_serialized_file()
    header = mw.parse_header(region)
    body_end = 48 + header.metadata_size
    node_size = header.file_size
    assert node_size == len(region)

    # 完整的那份先要能走通（不然下面的负控是"本来就坏"）
    info = ufs.read_serialized_file(_StubReader(region), _stub_node(node_size))
    assert info.object_count == 1
    assert info.objects[0][1] == 7  # pathID

    # ① 类型表里那一项自己就被砍掉 1 字节
    table_end = _type_table_end(region)
    with pytest.raises(AssetError) as cut_table:
        ufs._walk_type_table(_type_table_cursor(region[: table_end - 1]), 22, False)
    assert "越界" in str(cut_table.value)
    assert cut_table.value.context["buffer_size"] == table_end - 1

    # ② 从尾巴上一路砍（含"砍 1 字节"）：每一刀要么解析得下去、要么给可读的 AssetError，
    #    ⛔ 绝不许冒裸 struct.error。这是这条尺子的"全扫"形态，比只砍一刀更难糊弄。
    bare: list[tuple[int, str]] = []
    raised = 0
    for cut in range(1, 41):
        chopped = region[: body_end - cut]
        try:
            ufs.read_serialized_file(_StubReader(chopped), _stub_node(node_size))
        except AssetError:
            raised += 1
        except struct.error as exc:
            bare.append((cut, str(exc)))
    assert bare == [], f"这些刀冒出的是裸 struct.error：{bare}"
    assert raised >= 1, "砍了 40 刀没有一刀报错 —— 这条负控是空转"

    # 正控：同样这段截断，裸 struct.unpack_from 给的是 struct.error（不可读、也 catch 不住）
    chopped = region[: body_end - 1]
    assert len(chopped) == body_end - 1
    with pytest.raises(struct.error):
        struct.unpack_from("<i", chopped, body_end - 4)


# --------------------------------------------------------------------------- #
# 判据 ③b（日常档）：clone 读对象时，被砍的字节也必须是可读的 AssetError
#   —— W7 在 _work/unity-factory/_diag_trunc.py 记的那一条（同类问题的另一头）。
# --------------------------------------------------------------------------- #
#: 一棵最小的类型树（int + string）：手搓对象用它，不依赖真包与公共串表。
OBJECT_TREE = (
    cl.TypeNode(level=0, type="MiniScript", name="Base", byte_size=-1, meta_flag=0, type_flags=0,
                version=1),
    cl.TypeNode(level=1, type="int", name="m_Value", byte_size=4, meta_flag=0x10, type_flags=0,
                version=1),
    cl.TypeNode(level=1, type="string", name="m_Name", byte_size=-1, meta_flag=0x8010,
                type_flags=0, version=1),
)
OBJECT_FIELDS = {"m_Value": 7, "m_Name": "abc"}


def test_clone_truncated_object_raises_readable_asset_error() -> None:
    """对象字节砍 4 字节 ⇒ AssetError（带 cursor/need/buffer_size），不是裸 struct.error。"""
    blob = cl.write_object(OBJECT_FIELDS, OBJECT_TREE)
    # 正控的一半：完整的字节必须原样读得回来（改了的话下面的负控就没意义了）
    assert cl.read_object(blob, OBJECT_TREE) == OBJECT_FIELDS
    assert cl.roundtrip(blob, OBJECT_TREE) == blob

    short = blob[:-4]
    assert len(short) == len(blob) - 4
    with pytest.raises(AssetError) as info:
        cl.read_object(short, OBJECT_TREE)
    ctx = info.value.context
    assert (ctx["cursor"], ctx["need"], ctx["buffer_size"]) == (len(short), 3, len(short))
    assert "不够读" in str(info.value)

    # 砍到标量上也是 AssetError（不是只有字符串那一条分支有护栏）
    with pytest.raises(AssetError) as info2:
        cl.read_object(blob[:4], OBJECT_TREE)
    assert info2.value.context["need"] == 4
    assert info2.value.context["buffer_size"] == 4

    # 正控：同一段截断，裸 struct.unpack_from 给的是 struct.error（可读的 AssetError 不是白来的）
    with pytest.raises(struct.error):
        struct.unpack_from("<i", short, len(short))

# --------------------------------------------------------------------------- #
# 判据 ④（慢档）：真包上两条读器对账
# --------------------------------------------------------------------------- #
@contextlib.contextmanager
def _units_lock() -> Iterator[None]:
    """重活锁：拿不到就**失败**（⛔ 不 skip —— 静默跳过的绿不算绿）。"""
    if LOCK_DIR.joinpath("lock.py").is_file():
        # _work 不在包里：只能运行时挂 sys.path（tests/** 已豁免 PLC0415）
        from lock import acquire, release

        try:
            acquire("units", ttl=3600, wait=LOCK_WAIT_SECONDS, note="pytest test_ufs_types_cursor")
        except TimeoutError as exc:
            pytest.fail(f"units 锁等超过 {LOCK_WAIT_SECONDS:.0f}s：{exc}")
        try:
            yield
        finally:
            with contextlib.suppress(PermissionError):
                release("units")
        return
    path = LOCK_DIR / "units.ufs-fallback.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.time() + LOCK_WAIT_SECONDS
    handle: int | None = None
    while handle is None:
        try:
            handle = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
        except FileExistsError:
            if time.time() > deadline:
                pytest.fail(f"重活锁 {path} 被占着超过 {LOCK_WAIT_SECONDS:.0f}s")
            time.sleep(1.0)
    try:
        yield
    finally:
        os.close(handle)
        path.unlink(missing_ok=True)


def _both_readers(pack: Path) -> tuple[object, object]:
    """同一个包，两条读器各读一遍：ufs.read_serialized_file 与 metadata_writer。"""
    reader = ufs.UfsReader(pack)
    try:
        node = [n for n in reader.nodes if not n.path.endswith(".resS")][0]
        info = ufs.read_serialized_file(reader, node)
        meta = mw.read_serialized_metadata(reader, node)
    finally:
        reader.close()
    return info, meta


@pytest.mark.slow
def test_prefabsgui_and_prefabsme_types_parse() -> None:
    """这两个包从前读不开（游标跑飞）。现在两条读器给出同一张表。"""
    expected = {"prefabsgui": PREFABSGUI_TYPES, "prefabsme": PREFABSME_TYPES}
    checked = 0
    with _units_lock():
        for prefix, type_count in expected.items():
            pack = _pack(prefix)
            if pack is None:
                continue
            info, meta = _both_readers(pack)
            assert len(meta.types) == type_count
            assert info.object_count == len(meta.objects) == len(info.objects)
            # 逐行对账：对象表的 (pathID, stored, size, type_id) 两条读器必须一致
            rows = {o.path_id: (o.byte_start, o.byte_size, o.type_id) for o in meta.objects}
            assert len(rows) == info.object_count
            for _i, pid, stored, size, tid in info.objects:
                assert rows[pid] == (stored, size, tid)
            # 那 6 项 114/index=-1 确实在这个包里（这正是老代码踩的那个形状）
            neg = [
                i
                for i, t in enumerate(meta.types)
                if t.class_id == 114 and t.script_type_index < 0
            ]
            assert neg, f"{pack.name} 里应当有 classID=114 且 script_type_index=-1 的项"
            checked += 1
    if checked == 0:
        pytest.skip("找不到 prefabsgui / prefabsme 包（没装游戏 / BA_MOD_MAKER_GAME 指错了）")


@pytest.mark.slow
def test_units_object_table_agrees_with_metadata_writer() -> None:
    """判据 ① 的用例形态：units 上两条读器 162,010 行逐行相等。"""
    pack = _pack("units_assets_all")
    if pack is None:
        pytest.skip("找不到 units_assets_all*.bundle（没装游戏 / BA_MOD_MAKER_GAME 指错了）")
    with _units_lock():
        info, meta = _both_readers(pack)
    assert info.object_count == UNITS_OBJECTS
    assert len(meta.objects) == UNITS_OBJECTS
    ufs_map = {pid: (stored, size, tid) for _i, pid, stored, size, tid in info.objects}
    mw_map = {o.path_id: (o.byte_start, o.byte_size, o.type_id) for o in meta.objects}
    assert set(ufs_map) == set(mw_map)
    diff = [pid for pid in ufs_map if ufs_map[pid] != mw_map[pid]]
    assert diff == []
    assert info.data_offset == meta.header.data_offset
