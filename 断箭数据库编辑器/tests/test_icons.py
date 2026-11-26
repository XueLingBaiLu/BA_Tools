"""图标层的守卫：**地址解析**与**降级**。

这个文件测的不是"图标好不好看"，而是三件会让产品出事的事：

1. **实心 / 空心必须按地址取** —— 用户报的 Konkurs 空心就是这里出错的（151 张）；
2. 没有游戏 / 没有缓存时**必须静默降级**，不许抛异常；
3. 旧 cache/icons 只在**没装游戏**时才兜底，不能把权威地址的结果盖回去。

没有 QApplication 时 QPixmap 无法构造，所以用例都要 GUI 平台，
无字体/无显示环境一律 skip（与 test_theme 同一策略：宁可不给结论）。
"""

from __future__ import annotations

import io
import json
import time

import pytest

from ba_mod_maker.core.assets import find_game_root
from ba_mod_maker.core.assets.families import TARGET_ICON_KEYS, split_address
from ba_mod_maker.core.assets.families import family as family_of
from ba_mod_maker.core.assets.resolver import IconResolver

GAME = find_game_root()
needs_game = pytest.mark.skipif(GAME is None, reason="本机没装游戏（找不到 bundle 目录）")
#: 要真读 bundle 的用例慢（全套 1522 个键约 90s），日常迭代用 -m "not slow" 跳过。
slow = pytest.mark.slow


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtGui import QFontDatabase
    from PySide6.QtWidgets import QApplication

    try:
        app = QApplication.instance() or QApplication([])
    except Exception as exc:  # pragma: no cover - 依赖运行环境
        pytest.skip(f"无法创建 QApplication：{exc}")
    if not QFontDatabase.families():
        pytest.skip("该平台没有可用字体，QPixmap 行为不可信")
    return app


def _dead_cache(qapp, tmp_path):
    """一个**三个来源全断**的 IconCache：没游戏、没旧缓存、没运行时缓存。"""
    from ba_mod_maker.app.theme import IconCache

    return IconCache(
        tmp_path / "no-legacy",
        game_root=tmp_path / "no-game",
        runtime_cache=tmp_path / "no-runtime",
    )


# --------------------------------------------------------------------------- #
# 地址语义：实心 / 空心是两条地址（纯函数，不需要游戏）
# --------------------------------------------------------------------------- #


def test_outline_prefix_is_a_separate_address() -> None:
    """实心/空心是**两条独立地址**，不是同一张图的另一个版本。

    实测：catalog 的键表里 X 与 outline 前缀 + X 同时存在（全表 1461 个 outline 键）。
    DB 里存的是裸键 = 实心。
    """
    assert split_address("AMMO_KONKURS_M") == (False, "AMMO_KONKURS_M")
    assert split_address("outline" + chr(92) + "AMMO_KONKURS_M") == (True, "AMMO_KONKURS_M")
    # 立绘是路径式键，取叶子；正反斜杠都要认（DB 里两种都出现过）
    assert split_address("RU" + chr(92) + "BTR_82A" + chr(92) + "BTR_82A") == (False, "BTR_82A")
    assert split_address("DLC2/AS365_DAUPHIN/AS365_DAUPHIN") == (False, "AS365_DAUPHIN")
    assert split_address("SPEC_BALT.png") == (False, "SPEC_BALT")


# --------------------------------------------------------------------------- #
# 降级：没有游戏、没有缓存
# --------------------------------------------------------------------------- #


def test_missing_everything_degrades_quietly(qapp, tmp_path) -> None:
    """三个来源全断 -> 不可用，但不抛异常。"""
    cache = _dead_cache(qapp, tmp_path)
    assert cache.available is False
    assert cache.source == "none"
    assert cache.families() == ()
    assert cache.pixmap("unit_labels", "RU_BTR82-Label") is None


def test_directory_without_manifest_degrades_quietly(qapp, tmp_path) -> None:
    """旧缓存目录在、manifest 不在 -> 同样降级（提取跑到一半的情形）。"""
    from ba_mod_maker.app.theme import IconCache

    (tmp_path / "half").mkdir()
    cache = IconCache(
        tmp_path / "half", game_root=tmp_path / "no-game", runtime_cache=tmp_path / "rt"
    )
    assert cache.available is False
    assert cache.pixmap("unit_labels", "anything") is None


def test_broken_manifest_degrades_quietly(qapp, tmp_path) -> None:
    """manifest 是坏 JSON -> 降级，不许把整个界面带崩。"""
    from ba_mod_maker.app.theme import IconCache

    root = tmp_path / "broken"
    root.mkdir()
    (root / "_manifest.json").write_text("{ this is not json", encoding="utf-8")
    cache = IconCache(root, game_root=tmp_path / "no-game", runtime_cache=tmp_path / "rt")
    assert cache.available is False
    assert cache.pixmap("unit_labels", "anything") is None


def test_manifest_entry_pointing_at_a_missing_file(qapp, tmp_path) -> None:
    """manifest 说有、磁盘上没有 -> 返回 None，而不是空 pixmap。"""
    from ba_mod_maker.app.theme import IconCache

    root = tmp_path / "stale"
    root.mkdir()
    (root / "_manifest.json").write_text(
        json.dumps({"families": {"unit_labels": {"files": {"K": "unit_labels/K.png"}}}}),
        encoding="utf-8",
    )
    cache = IconCache(root, game_root=tmp_path / "no-game", runtime_cache=tmp_path / "rt")
    assert cache.available is True
    assert cache.has("unit_labels", "K") is True
    assert cache.pixmap("unit_labels", "K") is None


# --------------------------------------------------------------------------- #
# 命中：本机真的装了游戏 / 跑过提取时
# --------------------------------------------------------------------------- #


def test_legacy_cache_resolves_when_no_game(qapp, tmp_path) -> None:
    """**没装游戏**时旧 cache/icons 仍然可用（降级链的第二环）。"""
    from ba_mod_maker.app.theme import IconCache, find_icon_cache

    root = find_icon_cache()
    if root is None:
        pytest.skip("本机没有 cache/icons（先跑 tools/extract_icons.py）")

    cache = IconCache(root, game_root=tmp_path / "no-game", runtime_cache=tmp_path / "rt")
    assert cache.available is True
    assert cache.source == "cache"
    for family in ("unit_labels", "unit_portraits", "weapon_icons", "ammo_icons"):
        assert family in cache.families(), f"{family} 缺失"
    assert cache.pixmap("unit_labels", "RU_BTR82-Label") is not None


@slow
@needs_game
def test_bundle_resolution_beats_the_legacy_cache(qapp, tmp_path) -> None:
    """装了游戏就必须走 bundle —— 旧缓存里那 151 张空心图不能盖回来。"""
    from ba_mod_maker.app.theme import IconCache

    cache = IconCache(game_root=GAME, runtime_cache=tmp_path / "rt", autostart=False)
    assert cache.source == "bundle"
    # 直连解析器：同步验地址语义，不经过异步队列
    resolver = IconResolver(GAME)
    solid = resolver.resolve("ammo_icons", "AMMO_KONKURS_M")
    hollow = resolver.resolve("ammo_icons", "outline" + chr(92) + "AMMO_KONKURS_M")
    assert solid is not None and hollow is not None
    assert _coverage(solid) > 0.35, f"裸键应当是实心，实测只有 {_coverage(solid):.0%}"
    assert _coverage(hollow) < 0.25, f"outline 键应当是空心，实测 {_coverage(hollow):.0%}"
    assert _coverage(solid) > _coverage(hollow) + 0.2


_ICON_COLUMNS: dict[str, tuple[str, str]] = {
    "unit_labels": ("Units", "ThumbnailFileName"),
    "unit_portraits": ("Units", "PortraitFileName"),
    "weapon_icons": ("Weapons", "HUDIcon"),
    "ammo_icons": ("Ammunitions", "HUDIcon"),
    "flags": ("Countries", "FlagFileName"),
    "spec_icons": ("Specializations", "Icon"),
    "spec_illustrations": ("Specializations", "Illustration"),
}


def _icon_keys(instance, family: str) -> list[str]:
    table, column = _ICON_COLUMNS[family]
    tbl = instance.table(table)
    index = tbl.column_index(column)
    return sorted({r[index] for r in tbl.rows if isinstance(r[index], str) and r[index].strip()})


def test_icon_key_sample_is_big_enough(instance) -> None:
    """样本量守卫：拆成按组跑之后，"全库 1522 个图标键"这个量级本身也要有人守。

    这条**不读游戏**（只数数据表里的键），所以它在日常档也跑 —— 万一哪天
    列名写错、或者取样逻辑退化成空，它会立刻红，而不是让下面那三条变成空转。
    """
    total = sum(len(_icon_keys(instance, family)) for family in _ICON_COLUMNS)
    assert total > 1000, f"全库图标键只剩 {total} 个，取样逻辑坏了"


@slow
@needs_game
def test_every_icon_key_resolves(qapp) -> None:
    """DB 里每一个图标键都要解析得出来 —— 这是"换掉图标管线"的验收线。

    实测 1522/1522（缩略图 464 / 立绘 433 / 武器 472 / 弹药 127 / 国旗 2 / 专精 24）。
    覆盖 DLC：它们的素材在 dlc_2 / dlc_3 包里，主包那份是旧图。

    ❗ 这条**故意不拆**成按族的多条用例。试过（2026-09-30）拆成三组：
    总工作量从 16.7s 涨到 31.8s（14.0 + 9.5 + 8.3）—— 因为每个 IconResolver
    都要独立付一次"开包 + 解析 container"的固定成本（约 5~6 秒），拆三组就付三遍。
    整轮墙钟反而从 35.4s 变 37.5s、峰值内存从 12GB 变 14.4GB。
    **要压这条只能压它的绝对耗时（plan() 已经压掉 75s -> 17s），不能靠拆。**
    """
    from ba_mod_maker.core.data import find_dataset_root, load_dataset

    dataset_root = find_dataset_root()
    if dataset_root is None:
        pytest.skip("未找到 DataBaseCompiled 数据源")

    resolver = IconResolver(GAME)
    instance = load_dataset(dataset_root).instance("52085")
    grouped = {family: _icon_keys(instance, family) for family in _ICON_COLUMNS}

    missing: list[str] = []
    total = 0
    # ❗ 必须走 resolver.plan()：它把键**按「提供它的包」分组**。
    #    按数据表行序取会在主包与 DLC 包之间来回跳，而 BundleLibrary 同时只留得住
    #    2 个包（dlc_3 一个就 724 MB）—— 每跳一次就把大包 UnityPy.load 一遍。
    #    产品侧同一条路实测：45.5s -> 18.6s（见 IconResolver.plan 里那张表）。
    #    这条用例原来 75s，就是这么来的：它用的是坏顺序。
    for family, key in resolver.plan(grouped):
        total += 1
        if resolver.resolve(family, key) is None:
            missing.append(f"{family}:{key}")
    assert total > 1000, f"样本太小（{total}），这条测试没有意义"
    assert not missing, f"{len(missing)}/{total} 个键解析不出来：{missing[:8]}"


# --------------------------------------------------------------------------- #
# 立绘必须是彩色的（这条拦的是一个真踩过的坑）
# --------------------------------------------------------------------------- #


def _coverage(png: bytes) -> float:
    """PNG 字节里 alpha>128 的像素占比。实心图高，线稿低。"""
    pytest.importorskip("PIL", reason="没装 Pillow，无法做像素判据")
    from PIL import Image

    image = Image.open(io.BytesIO(png)).convert("RGBA")
    alpha = image.getchannel("A").histogram()
    return sum(alpha[128:]) / (image.width * image.height)


@slow
@needs_game
def test_portrait_is_colour_artwork_not_the_grey_mask(qapp) -> None:
    """立绘必须是**鲜艳全彩**的原图，不能是 _BASIC 那张灰度遮罩。

    实踩：照着反编译里 IconsLoader.LoadUnitBasicPortrait 的名字去取 "_BASIC"，
    取到的是一张**平均饱和度只有 0.012~0.044、亮度 0.07~0.20 的灰度图**
    —— 它是别处用的遮罩，不是军械库右栏显示的立绘。
    军械库显示的是**裸键**那张（饱和度 0.21~0.66）。
    """
    pytest.importorskip("PIL", reason="没装 Pillow，无法做像素判据")
    from PIL import Image

    from ba_mod_maker.core.data import find_dataset_root, load_dataset

    dataset_root = find_dataset_root()
    if dataset_root is None:
        pytest.skip("未找到 DataBaseCompiled 数据源")

    resolver = IconResolver(GAME)
    instance = load_dataset(dataset_root).instance("52085")
    table = instance.table("Units")
    index = table.column_index("PortraitFileName")
    keys = sorted({r[index] for r in table.rows if isinstance(r[index], str) and r[index].strip()})

    checked = 0
    for key in keys[:25]:
        png = resolver.resolve("unit_portraits", key)
        if png is None:
            continue
        saturation = Image.open(io.BytesIO(png)).convert("RGB").convert("HSV").getchannel("S")
        total = saturation.width * saturation.height
        mean = sum(i * n for i, n in enumerate(saturation.histogram())) / total / 255
        assert mean > 0.10, f"{key} 平均饱和度只有 {mean:.3f}，像是灰度遮罩而非彩色立绘"
        checked += 1
    assert checked > 0, "一张立绘都没检查到"


# --------------------------------------------------------------------------- #
# 预取顺序：**按提供者包分组**（这条守的是 2.4 倍的那个加速）
# --------------------------------------------------------------------------- #


class _StubCatalog:
    """假的 catalog：plan() 只要「键 -> {(资产路径, 包名)}」这张表。

    真的 AddressablesCatalog 要读 3.8 MB 的 catalog.json，还要本机装了游戏；
    排序规则本身不该依赖这两件事。
    """

    def __init__(self, table: dict) -> None:
        self._table = table

    @property
    def key_count(self) -> int:
        return len(self._table)

    def lookups(self, key: str) -> tuple:
        return tuple(sorted(self._table.get(key, ())))


LABELS = "unitlabels_assets_all_aaa.bundle"
DLC3 = "dlc_3_defenceforce_assets_all_zzz.bundle"
WEAPONS = "unitweaponicons_assets_all_bbb.bundle"


def _stub_resolver() -> IconResolver:
    """两个包的键交错排（模拟"主包 + DLC 包"的真实分布），外加一个取不到的键。"""
    catalog = _StubCatalog(
        {
            "L1": (("/images/labels/L1.png", LABELS),),
            "L2": (("/images/labels/L2.png", LABELS),),
            "W1": (("/images/weapons/W1.png", WEAPONS),),
            "D1": (("/images/labels/DLC3/D1.png", DLC3), ("/images/labels/DLC3/D1.png", LABELS)),
            "D2": (("/images/labels/DLC3/D2.png", DLC3), ("/images/labels/DLC3/D2.png", LABELS)),
            "NOPE": (),
        }
    )
    return IconResolver(None, catalog=catalog)


def _predicted_bundle(resolver: IconResolver, key: str) -> str:
    described = resolver.describe(key)
    return described[1] if described else ""


def _runs(values: list) -> list:
    """相邻去重后的序列。同一个包只出现一段 = 没来回跳。"""
    out: list = []
    for value in values:
        if not out or out[-1] != value:
            out.append(value)
    return out


def test_plan_keeps_each_bundle_in_one_run() -> None:
    """预取顺序必须**按提供者包分组**，同一个包只能连着取一段。

    实测（本机 1522 个键、空缓存）：按族/数据表行序取 45.5s，其中开包 18.5s、
    重复开同一个包 263 次；按包分组之后 18.6s、开包 4.3s（每个包只开一次）。
    原因是 BundleLibrary 同时只留得住 2 个包（dlc_3 一个就 724 MB），
    而单位缩略图有 88/464 个键不在主包里。
    """
    resolver = _stub_resolver()
    plan = resolver.plan(
        {
            "unit_labels": ["L1", "D1", "L2", "D2"],
            "weapon_icons": ["W1", "NOPE"],
        }
    )

    # 一个键都不能丢，也不能多出来
    assert sorted(key for _family, key in plan) == ["D1", "D2", "L1", "L2", "NOPE", "W1"]

    bundles = _runs([_predicted_bundle(resolver, key) for _family, key in plan])
    assert len(bundles) == len(set(bundles)), f"同一个包被切成了好几段：{bundles}"


def test_plan_puts_the_first_screen_first() -> None:
    """首屏要用的族排前面（用户点过「跳过」时，先看到的必须是该看的图）。"""
    resolver = _stub_resolver()
    plan = resolver.plan({"weapon_icons": ["W1"], "unit_labels": ["L1"]})
    assert [key for _family, key in plan] == ["L1", "W1"]


def test_plan_puts_unresolvable_keys_last() -> None:
    """catalog 里查不到的键排最后——它们本来就取不到图，别挡在前面。"""
    resolver = _stub_resolver()
    plan = resolver.plan({"unit_labels": ["NOPE", "L1"]})
    assert [key for _family, key in plan] == ["L1", "NOPE"]


def test_plan_without_a_catalog_keeps_the_input_order() -> None:
    """没有 catalog（没装游戏）时原样返回，不许抛异常、不许丢键。"""
    resolver = IconResolver(None)
    grouped = {"unit_labels": ["B", "A"], "flags": ["C"]}
    assert resolver.plan(grouped) == [
        ("unit_labels", "B"),
        ("unit_labels", "A"),
        ("flags", "C"),
    ]


# --------------------------------------------------------------------------- #
# 目标类型图标：键**不在数据列里**（InfocardConfig 的 5 个 sprite）
# --------------------------------------------------------------------------- #


def test_stat_icon_family_is_constant_keyed() -> None:
    """数值行图标族：键是 InfocardConfig 的**字段名 -> sprite 地址**，不在数据列里。

    出处：反编译的 InfocardConfig 字段表 + 用户给的实机卡片截图逐格核对（见 families.py 注释）。
    """
    from ba_mod_maker.core.assets.families import STAT_ICON_KEYS

    family = family_of("infocard_stats")
    assert family is not None
    assert family.column == ""
    assert family.bundle_glob == "prefabsgui_assets_all_*.bundle"
    assert len(family.keys) == 18, len(family.keys)
    assert family.keys == tuple(STAT_ICON_KEYS.values())
    # 实机截图上能逐格对上的那 8 个，必须是这几张图（改错了会画错图标）
    for field, leaf in (
        ("Armor", "Armor.png"),
        ("HealthInfantry", "Health points.inf.png"),
        ("SoldierCount", "group.png"),
        ("Optics", "Optics.png"),
        ("Stealth", "Stealth.png"),
        ("SpeedForwardInfantry", "Forward speed.inf.png"),
        ("Weight", "Weight.png"),
        # ❗ 实机那一格画的是"时钟+骷髅"，不是名字更像的 RefundTimer
        ("RefundTime", "DeathTimer.png"),
    ):
        assert STAT_ICON_KEYS[field].endswith(leaf), (field, STAT_ICON_KEYS[field])


def test_option_pictures_family_comes_from_the_database() -> None:
    """改型选项的图片：键来自 Options.OptionPicture（**这条是数据列，不是 sprite 偏移**）。

    实测 2046 行 / 397 个键，提供者是 unitweaponicons（与武器图标同一批素材）。
    游戏「自定义选项」那一排画的就是它。
    """
    family = family_of("option_pictures")
    assert family is not None
    assert family.column == "Options.OptionPicture"
    assert family.bundle_glob == "unitweaponicons_assets_all_*.bundle"


def test_shared_icon_cache_is_single_and_shuts_down(qapp, tmp_path) -> None:
    """共享缓存：一个进程只该有一条加载线程。

    实踩：每个 ArmoryScreen 各建一个 IconCache = 各起一条加载线程，线程里的 resolver
    会按住最多两个已打开的 bundle。连续建 14 个屏就有 15 条线程，最后在 UnityPy 里
    撞出 "Windows fatal exception: access violation"（pytest tests/test_armory_screen.py 复现）。
    """
    import threading

    from ba_mod_maker.app.theme import reset_shared_icon_cache, shared_icon_cache

    reset_shared_icon_cache()
    before = threading.active_count()
    first = shared_icon_cache()
    second = shared_icon_cache()
    assert first is second, "两次拿到的必须是同一个实例"
    # 没有游戏时不会起线程；有游戏时**只多一条**。
    assert threading.active_count() - before <= 1
    reset_shared_icon_cache()
    third = shared_icon_cache()
    assert third is not first, "reset 之后应当新建"
    reset_shared_icon_cache()


def test_shutdown_actually_joins_the_loader_thread(qapp, tmp_path) -> None:
    """shutdown() 必须**真把线程收掉**（只是置标志位不算）。

    ❗ 这里也钉住了另一个坑：_Loader 曾经把停止标志命名成 `self._stop`，
    而那是 threading.Thread 自己的方法 —— 一 join 就 "Event object is not callable"。
    """
    import threading

    from ba_mod_maker.app.theme.icons import IconCache

    cache = IconCache(runtime_cache=tmp_path / "rt", autostart=False)
    thread = cache._thread
    cache.shutdown()
    if thread is not None:
        assert not thread.is_alive(), "shutdown 之后加载线程还活着"
        assert thread not in threading.enumerate()
    cache.shutdown()  # 幂等：再来一次不许炸


def test_target_icon_family_is_constant_keyed() -> None:
    """弹药的目标类型图标族：键来自 TARGET_ICON_KEYS，不是某一列。

    数据表里压根没有这一列 —— 游戏是在 SetTargetTraits 里按 TargetType 的位现挑 sprite 的。
    """
    family = family_of("infocard_targets")
    assert family is not None, "families.FAMILIES 里没有 infocard_targets"
    assert family.column == "", "这一族的键不来自数据列"
    assert family.bundle_glob == "prefabsgui_assets_all_*.bundle"
    assert family.keys == tuple(TARGET_ICON_KEYS.values())
    assert len(family.keys) == 5, "InfocardConfig 里就是 5 个（步兵/载具/直升机/固定翼/导弹）"


def test_icon_keys_collects_constant_keys_too() -> None:
    """预取清单必须带上常量键 —— 否则那 5 张图永远不进磁盘缓存（只在用到时现取）。"""
    from ba_mod_maker.core.assets.keys import icon_keys

    class _Table:
        def __init__(self, rows):
            self.rows = rows

        def column_index(self, name):
            if name != "ThumbnailFileName":
                raise KeyError(name)
            return 0

    class _Instance:
        def table(self, name):
            if name != "Units":
                raise KeyError(name)
            return _Table([["A"], ["A"], ["B"], [None]])

    grouped = icon_keys(_Instance())
    assert grouped["unit_labels"] == ("A", "B")  # 去重、丢掉空值
    assert len(grouped["infocard_targets"]) == 5
    assert set(grouped["infocard_targets"]) == set(TARGET_ICON_KEYS.values())
    # 没有这一族的数据表时不许炸（原来的行为：那一族直接不出现）
    assert "weapon_icons" not in grouped


def test_infocard_path_hint_picks_prefabsgui() -> None:
    """同一个 sprite 在 catalog 里还挂着十几个依赖包（units_assets_all 有 3.4 GB）。

    没有 _PATH_FAMILY_HINTS 里那条 /infocard/ 提示的话，解析会按字母序一路开小包
    （ammoicons -> campaigns -> fonts -> …），每张图白开好几个包。
    """
    key = TARGET_ICON_KEYS["vehicle"]
    catalog = _StubCatalog(
        {
            key: (
                (key, "ammoicons_assets_all_add.bundle"),
                (key, "prefabsgui_assets_all_146.bundle"),
                (key, "units_assets_all_1e6.bundle"),
            ),
        }
    )
    resolver = IconResolver(None, catalog=catalog)
    assert resolver.describe(key)[1] == "prefabsgui_assets_all_146.bundle"


@slow
@needs_game
def test_target_icons_resolve_from_the_game(qapp) -> None:
    """5 个 sprite 都要真的从 prefabsgui 包里取出来（并且只开这一个包）。

    实测：5/5 命中，bundles_opened == 1（有 /infocard/ 提示才做得到）。
    """
    from ba_mod_maker.core.assets.families import TARGET_ICON_KEYS as KEYS

    resolver = IconResolver(GAME)
    for name, key in KEYS.items():
        payload = resolver.resolve("infocard_targets", key)
        assert payload, f"{name} 取不到图：{key}"
        assert len(payload) > 500, f"{name} 的图太小了（{len(payload)} 字节）"
    assert resolver.stats["bundles_opened"] == 1, "只该开 prefabsgui 一个包"


# --------------------------------------------------------------------------- #
# 预加载：一次把图都取好，但**不许吃内存**
# --------------------------------------------------------------------------- #


class _StubResolver:
    """假的解析器：不碰游戏、不碰 bundle，几毫秒跑完。

    真的 IconResolver 也能用，但一条用例要开 300 MB 的包，不该进日常测试。
    """

    def __init__(self, keys, delay: float = 0.0):
        self.keys = set(keys)
        self.calls = []
        self.stats = {}
        self.available = True
        self.delay = delay
        from pathlib import Path

        self.game_root = Path("X:/fake")

    def describe(self, key):
        return ("/assets/" + key + ".png", "fake.bundle") if key in self.keys else None

    def resolve(self, family, key):
        self.calls.append((family, key))
        if self.delay:
            time.sleep(self.delay)
        return _png_bytes() if key in self.keys else None

    def close(self):
        pass


def _png_bytes() -> bytes:
    """一张 4x4 的真 PNG（QPixmap 编码）。"""
    from PySide6.QtCore import QBuffer, QIODevice
    from PySide6.QtGui import QPixmap

    pixmap = QPixmap(4, 4)
    pixmap.fill()
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    pixmap.save(buffer, "PNG")
    return bytes(buffer.data())


def _stub_cache(qapp, tmp_path, keys, delay: float = 0.0):
    from ba_mod_maker.app.theme import IconCache

    resolver = _StubResolver(keys, delay=delay)
    cache = IconCache(
        tmp_path / "no-legacy",
        runtime_cache=tmp_path / "rt",
        resolver=resolver,
    )
    return cache, resolver


def test_preload_fills_the_disk_cache_without_hoarding_memory(qapp, tmp_path) -> None:
    """预加载**只灌磁盘缓存**，不把图堆在内存里。

    为什么这条重要：立绘一张 816x550 就是 1.8 MB，1500 张全留是 780 MB。
    预加载的价值是"磁盘就绪后 pixmap() 同步读盘"，不是"全塞进内存"。
    """
    keys = [f"K{i}" for i in range(12)]
    cache, resolver = _stub_cache(qapp, tmp_path, keys)

    assert cache.preload_all({"unit_labels": keys}) == 12
    assert cache.wait_for_idle(timeout=5.0)

    assert sorted(k for _, k in resolver.calls) == sorted(keys)
    assert len(list((tmp_path / "rt" / "unit_labels").glob("*.png"))) == 12
    # 关键：内存里**一张都不该留**
    assert len(cache._pixmaps) == 0
    assert cache._memory_bytes == 0


def test_pixmap_is_synchronous_once_preloaded(qapp, tmp_path) -> None:
    """预加载完成之后，pixmap() 必须**当场**给图（同步读盘），不再等一轮。"""
    cache, _ = _stub_cache(qapp, tmp_path, ["K"])
    cache.preload_all({"unit_labels": ["K"]})
    assert cache.wait_for_idle(timeout=5.0)

    assert cache.pixmap("unit_labels", "K") is not None


def test_second_preload_is_a_no_op(qapp, tmp_path) -> None:
    """磁盘缓存已经有了就不再排 —— 第二次启动必须是空转。"""
    keys = [f"K{i}" for i in range(5)]
    cache, resolver = _stub_cache(qapp, tmp_path, keys)
    cache.preload_all({"unit_labels": keys})
    cache.wait_for_idle(timeout=5.0)
    before = len(resolver.calls)

    assert cache.preload_all({"unit_labels": keys}) == 0
    assert len(resolver.calls) == before


def test_invalidate_refetches_only_that_one(qapp, tmp_path) -> None:
    """映射地址变了**只重取那一张**，不许整批重来。"""
    keys = [f"K{i}" for i in range(6)]
    cache, resolver = _stub_cache(qapp, tmp_path, keys)
    cache.preload_all({"unit_labels": keys})
    cache.wait_for_idle(timeout=5.0)
    cache.pixmap("unit_labels", "K3")
    before = len(resolver.calls)

    cache.invalidate("unit_labels", "K3")
    assert cache.wait_for_idle(timeout=5.0)
    assert resolver.calls[before:] == [("unit_labels", "K3")]
    assert cache.pixmap("unit_labels", "K3") is not None


def test_preload_uses_the_low_priority_queue(qapp, tmp_path) -> None:
    """预加载走低优先级队列：用户滚到的卡不能被上千张预取堵在后面。

    ❗ 解析器要**故意慢一点**才测得出来：不慢的话预取瞬间就跑完了，
    插队这件事根本没机会发生（第一版就是这么假绿的）。
    """
    keys = [f"K{i}" for i in range(30)]
    cache, resolver = _stub_cache(qapp, tmp_path, keys, delay=0.02)
    cache.preload_all({"unit_labels": keys})
    time.sleep(0.05)  # 让 worker 先吃掉几个预取，这才有"排队"可言

    cache.pixmap("unit_labels", "K29")  # 界面上要用的那一张
    assert cache.wait_for_idle(timeout=15.0)
    assert resolver.calls.index(("unit_labels", "K29")) < 10, "界面请求没有插到预取前面"


# --------------------------------------------------------------------------- #
# 进度模型：速率 / ETA / 结束信号（用户："我希望在加载的时候能看到进度条"）
# --------------------------------------------------------------------------- #


def test_prefetch_reports_rate_eta_and_finishes(qapp, tmp_path) -> None:
    """进度不只是 done/total：**速率与剩余时间**要真的算得出来。

    只有一根不动的条等于没说。这里钉住三件事：
    1. 样本没铺开时 ETA 是 None（宁可不说，也不要瞎报一个数）；
    2. 跑起来之后速率 > 0、ETA > 0，状态文案里有"剩约"；
    3. 跑完发 prefetch_finished，且只发一次。
    """
    from ba_mod_maker.app.theme.icons import prefetch_status_text

    keys = [f"K{i}" for i in range(40)]
    cache, _resolver = _stub_cache(qapp, tmp_path, keys, delay=0.05)
    finished: list[int] = []
    cache.prefetch_finished.connect(lambda: finished.append(1))

    assert cache.preload_all({"unit_labels": keys}) == 40
    assert cache.prefetch_active
    assert cache.prefetch_eta is None, "刚开始就报 ETA 是编的"

    deadline = time.monotonic() + 1.2
    while time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.02)

    assert cache.prefetch_active
    assert cache.prefetch_rate > 0, "速率没算出来，状态栏就只能干等着"
    eta = cache.prefetch_eta
    assert eta is not None and eta > 0
    assert "剩约" in prefetch_status_text(cache)

    assert cache.wait_for_idle(timeout=15.0)
    for _ in range(5):
        qapp.processEvents()
        time.sleep(0.02)

    assert not cache.prefetch_active
    assert cache.prefetch_done == cache.prefetch_total == 40
    assert finished == [1]
    assert prefetch_status_text(cache) == "40 张全部就绪"


def test_progress_counts_icons_served_through_the_priority_queue(qapp, tmp_path) -> None:
    """界面先要走的那几张，**同样算预取进度**。

    实踩（用户报"看不到进度条"的直接原因）：窗口一开，卡片就通过优先队列把可见的
    几十张要走（这正是设计要的），而批量任务还在后面排队。早先只在批量分支 +1，
    于是进度条长时间停在 0/1519 —— 明明在干活，界面却说没动。

    这里直接把「非批量的结果」塞进回收队列，绕开线程调度，语义才测得准。
    """
    keys = [f"K{i}" for i in range(5)]
    # 解析器故意慢：批量结果 5 秒内到不了，进度只能是"优先路径那几张"贡献的
    cache, _resolver = _stub_cache(qapp, tmp_path, keys, delay=5.0)
    assert cache.preload_all({"unit_labels": keys}) == 5

    for key in keys:  # 优先路径：bulk=False（界面插队拿到的就是这种）
        cache._out.put(("unit_labels", key, _png_bytes(), False))
    cache._drain()

    assert cache.prefetch_done == 5, "优先路径拿到的图没算进进度"


def test_eta_is_withheld_until_there_is_enough_progress(qapp, tmp_path) -> None:
    """进度太浅时不许报 ETA。

    实踩：刚开始那几秒全在开包、没有产出，按窗口速率外推会算出"剩约 3 分 11 秒"，
    而实际 19 秒就跑完了。这种数字比不说更糟——用户会以为卡住了。
    """
    cache, _resolver = _stub_cache(qapp, tmp_path, ["K"])
    cache._preload_total = 1000
    cache._rate = 50.0

    cache._preload_done = 5
    assert cache.prefetch_eta is None

    cache._preload_done = 100
    assert cache.prefetch_eta == pytest.approx(18.0)


def test_format_duration_speaks_chinese() -> None:
    """剩余时间的文案规则（跨过 1 分钟要换单位，估不出来时不许编）。"""
    from ba_mod_maker.app.theme.icons import format_duration

    assert format_duration(None) == ""
    assert format_duration(0.2) == "不到 1 秒"
    assert format_duration(32.4) == "32 秒"
    assert format_duration(65) == "1 分 05 秒"


def test_prefetch_status_text_is_empty_without_prefetch() -> None:
    """缓存已就绪时不许在状态栏留一句"0/0"。"""
    from ba_mod_maker.app.theme.icons import prefetch_status_text

    class _Idle:
        prefetch_total = 0
        prefetch_done = 0

    assert prefetch_status_text(_Idle()) == ""


# --------------------------------------------------------------------------- #
# 「翻不翻」只有一份：families.family_mirrors + icons.prepare_icon
# --------------------------------------------------------------------------- #


def _half_white_source():
    """一张**不对称**的假图：左半边白、右半边透明（翻没翻一眼看得出维度）。"""
    from PySide6.QtGui import QColor, QPainter, QPixmap

    pixmap = QPixmap(100, 40)
    pixmap.fill(QColor(0, 0, 0, 0))
    painter = QPainter(pixmap)
    painter.fillRect(0, 0, 50, 40, QColor("#ffffff"))
    painter.end()
    return pixmap


def _alpha_at(pixmap, side: str) -> int:
    """图里左/右那半边的 alpha（0 = 透明）。"""
    image = pixmap.toImage()
    x = int(image.width() * (0.2 if side == "left" else 0.8))
    return image.pixelColor(x, image.height() // 2).alpha()


def test_only_weapon_and_option_families_are_mirrored() -> None:
    """要翻的只有**武器**与**改型选项**两族（用户原话「武器能不能让它们朝右」）。

    判据（docs/交接-单位卡片GUI.md:420）：原图 `INF_AK74RMO.png` 枪口朝左、
    实机卡上朝右；⚠️ **弹药那族本来就朝右，别一起翻**。
    """
    from ba_mod_maker.core.assets.families import family_mirrors, family_names

    assert family_mirrors("weapon_icons") is True
    assert family_mirrors("option_pictures") is True
    assert family_mirrors("ammo_icons") is False
    # 其余族一律不翻；没登记的族也不许翻（不猜）。
    for name in family_names():
        if name not in ("weapon_icons", "option_pictures"):
            assert family_mirrors(name) is False, name
    assert family_mirrors("没有这一族") is False


def test_prepare_icon_mirrors_by_family_and_keeps_dpr(qapp) -> None:
    """`prepare_icon` = 按族翻 + 按 DPR 缩；界面侧不许自己 scaled / transformed。"""
    from PySide6.QtCore import QSize
    from PySide6.QtWidgets import QWidget

    from ba_mod_maker.app.theme.icons import prepare_icon, scale_for_widget

    class _Widget(QWidget):
        def devicePixelRatioF(self):
            return 2.0

    widget = _Widget()
    size = QSize(50, 20)
    source = _half_white_source()

    # 武器族：翻 —— 白的跑到右边去。
    mirrored = prepare_icon(source, "weapon_icons", size, widget)
    assert _alpha_at(mirrored, "left") == 0, "武器图标没翻（枪口还在左边）"
    assert _alpha_at(mirrored, "right") > 0

    # 弹药族：**不翻** —— 白的还在左边（翻了就错）。
    plain = prepare_icon(source, "ammo_icons", size, widget)
    assert _alpha_at(plain, "left") > 0, "弹药图标被翻了（它本来就朝右）"
    assert _alpha_at(plain, "right") == 0

    # DPR 那段行为与 scale_for_widget 完全一致（没有第二份缩放规则）。
    assert mirrored.devicePixelRatio() == 2.0
    assert mirrored.size() == scale_for_widget(source, size, widget).size()
    widget.deleteLater()
