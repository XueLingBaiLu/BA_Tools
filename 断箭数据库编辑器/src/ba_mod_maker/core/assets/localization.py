"""游戏本地化包（中文标签的**唯一来源**）——零 Qt。

卡片上的中文必须与游戏内一模一样（用户定下的第 2 条），所以标签不能自己编，
只能读游戏自己的本地化包：

    <游戏>/BrokenArrow_Data/StreamingAssets/aa/PC/localization_assets_all_<hash>.bundle

包里是一堆 TextAsset，**每个语言一个**（keys/chi/eng/fre/... 共 14 个）。
每个 TextAsset 是一个 JSON 数组，首元素是语言名，之后**按下标**与 keys 表一一对应：

    key2chi = dict(zip(tables["keys"], tables["chi"]))     # 实测 5378 条

三条实测坑（都踩过）：

1. m_Script 取出来可能是 str（UnityPy 对非 UTF-8 字节做了有损解码），
   直接 json.loads 会炸 —— 必须先 encode("utf-8", "surrogateescape") 还原字节。
2. 包名带 hash（游戏更新会变），所以**只能 glob**，不能写死文件名。
3. 缓存写 %LOCALAPPDATA%，**不进仓库**（那是游戏素材）。
4. **表里的值可能是 JSON null**（本版 5379 条里 175 条）—— `null` 就是"没翻出来"，
   那 175 个键**不进词典**（见 `entries_from_tables`）。⛔ 不许变成字符串 "None"。

缺游戏 / 缺 UnityPy 时**不报错**，退到产品自带的一套中文列名，并把
is_degraded 置真、note 写清降级原因 —— 界面必须照常能用，但要**明说**这是降级。
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ba_mod_maker.core.assets.bundles import BundleUnavailableError, unitypy
from ba_mod_maker.core.assets.game import bundle_dir, find_game_root

#: bundle 里存"键表"的那个 TextAsset 名。
KEYS_ASSET = "keys"

#: 默认语言。产品界面一律中文（用户定的口径）。
DEFAULT_LANGUAGE = "chi"

#: 包名带 hash，只能按通配找。
BUNDLE_GLOB = "localization_assets_all_*.bundle"

#: 缓存目录名：%LOCALAPPDATA%/BA_Mod_Maker/localization。
CACHE_DIR_NAME = "localization"

#: 缓存格式版本。改了缓存结构就 +1，旧缓存自动作废（不会读到半截东西）。
#:
#: ❗ **v1 -> v2（2026-10-03）**：v1 的缓存是我们拿 `str(value)` 把 JSON null 写成
#: 字符串 "None" 的那一版 —— 盘上那份 %LOCALAPPDATA%\BA_Mod_Maker\localization\chi.json
#: 里就实打实写着 "None"。不抬版本号，旧缓存会被原样读回来、修法在真机上**看不到效果**。
CACHE_VERSION = 2

#: 来源标记。界面上要能一眼看出"这是游戏原文"还是"产品自带的降级名"。
SOURCE_BUNDLE = "bundle"
SOURCE_CACHE = "cache"
SOURCE_BUILTIN = "builtin"

_SOURCE_LABELS = {
    SOURCE_BUNDLE: "游戏本地化包",
    SOURCE_CACHE: "本地缓存",
    SOURCE_BUILTIN: "内置降级名（未读到游戏本地化包）",
}


# --------------------------------------------------------------------------- #
# 路径
# --------------------------------------------------------------------------- #


def default_cache_dir() -> Path:
    """本地化缓存的落点。

    与图标缓存同一约定（见 app/theme/icons.py）：放用户目录，**不进仓库**。
    可用 BA_MOD_MAKER_LOC_CACHE 覆盖（测试用）。
    """
    override = os.environ.get("BA_MOD_MAKER_LOC_CACHE")
    if override:
        return Path(override)
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_CACHE_HOME")
    root = Path(base) if base else Path.home() / ".cache"
    return root / "BA_Mod_Maker" / CACHE_DIR_NAME


def find_localization_bundle(game_root: Path | None = None) -> Path | None:
    """定位本地化 bundle；找不到返回 None（**不是错误**）。

    多个包同时存在时取 **mtime 最新**的那个：游戏更新后旧包常常还留在盘上，
    而真正生效的是新的那份。同 mtime 时按文件名排，保证结果可复现。
    """
    root = game_root if game_root is not None else find_game_root()
    if root is None:
        return None
    directory = bundle_dir(root)
    if not directory.is_dir():
        return None
    candidates = [path for path in directory.glob(BUNDLE_GLOB) if path.is_file()]
    if not candidates:
        return None
    return max(candidates, key=lambda path: (path.stat().st_mtime, path.name))


def cache_file(cache_dir: Path, language: str = DEFAULT_LANGUAGE) -> Path:
    return cache_dir / f"{language}.json"


# --------------------------------------------------------------------------- #
# 读 bundle
# --------------------------------------------------------------------------- #


def decode_script(script: Any) -> Any:
    """TextAsset.m_Script -> JSON 对象。

    ⚠️ 实测坑：m_Script 有时是 str。那是 UnityPy 用 surrogateescape 之类
    的方式**有损解码**出来的，直接 json.loads(str) 会炸在代理字符上。
    先把它按 utf-8 + surrogateescape 还原成字节，再解码就对了。
    """
    raw = script.encode("utf-8", "surrogateescape") if isinstance(script, str) else script
    if isinstance(raw, memoryview):
        raw = raw.tobytes()
    if not isinstance(raw, (bytes, bytearray)):
        raise ValueError(f"TextAsset.m_Script 既不是 str 也不是 bytes，而是 {type(raw).__name__}")
    return json.loads(bytes(raw).decode("utf-8"))


def read_bundle_entries(
    bundle: Path, *, language: str = DEFAULT_LANGUAGE
) -> TableEntries:
    """从 bundle 里读 keys 与语言表，**按下标** zip 成 `TableEntries`。

    异常
    ----
    BundleUnavailableError
        没装 UnityPy。
    ValueError
        包里缺 keys 或该语言，或两者的长度对不上（这时宁可炸，也不要给半份词典）。
    """
    environment = unitypy().load(str(bundle))
    tables: dict[str, Any] = {}
    wanted = {KEYS_ASSET, language}
    for obj in environment.objects:
        if obj.type.name != "TextAsset":
            continue
        try:
            asset = obj.read()
        except Exception:  # noqa: BLE001 - 单个资产读不出来不该毁掉整次加载
            continue
        name = str(getattr(asset, "m_Name", "") or "")
        if name not in wanted or name in tables:
            continue
        try:
            tables[name] = decode_script(getattr(asset, "m_Script", None))
        except (ValueError, json.JSONDecodeError):
            continue
        if len(tables) == len(wanted):
            break

    if KEYS_ASSET not in tables:
        raise ValueError(f"{bundle.name} 里没有 {KEYS_ASSET} 表")
    if language not in tables:
        raise ValueError(f"{bundle.name} 里没有语言 {language!r}")

    keys = tables[KEYS_ASSET]
    values = tables[language]
    if not isinstance(keys, list) or not isinstance(values, list):
        raise ValueError(f"{bundle.name} 的 {KEYS_ASSET}/{language} 不是 JSON 数组")
    if len(keys) != len(values):
        raise ValueError(
            f"{bundle.name} 里 keys={len(keys)} 与 {language}={len(values)} 条数不等，"
            "按下标 zip 会错位"
        )
    return TableEntries(
        entries=entries_from_tables(keys, values),
        nulls=null_keys_from_tables(keys, values),
    )


def entries_from_tables(keys: Sequence[Any], values: Sequence[Any]) -> dict[str, str]:
    r"""keys 表与语言表**按下标** zip 成词典 —— **值是 null 的键干脆不进词典**。

    ❗ 为什么这条是精确判定、不是启发式：游戏包里"没翻出来"就写成 **JSON null**
    （实测本版 5379 条里 175 条：`dlc_3_custom_option_osv96`、`ui_skinpack_desc_air_t1`、
    `me_main_*`、`usm06n_dial_*` …，键里带 "none" 的 0 条）。
    早先那句 `str(value)` 把 null 变成了**字符串 "None"** —— 界面分不清它和真译文，
    于是照画，用户看到的就是「左边显示的是 None」（`Options.UIName Id=3039`）。
    现在 null 一律当**缺失**：`has()` 为假、`text()` 给 default，上层走自己的回退链。

    ⚠️ 「词典里查不到」≠「这格是明文」：查不到就**照实显示格子里的原文**
    （数据格的口径，见 `CardData.display_name`）；给人看的选项标签另有回退链
    （`unit_card._option_label`），它不拿键名当文案。

    复算：`.venv\Scripts\python.exe _work\w9c_probe_loc_null.py`（值类型分布 + null 条数）。
    """
    return {
        str(key): str(value)
        for key, value in zip(keys, values, strict=True)
        if value is not None
    }


def null_keys_from_tables(keys: Sequence[Any], values: Sequence[Any]) -> frozenset[str]:
    """**声明过、但值是 null** 的那些键（= 游戏自己承认「这一条没翻出来」）。

    为什么不干脆丢掉、还要记一份：因为「查不到」有两种意思，显示回退链必须分开 ——
    `Options.UIName` 实测 **1 : 469**（复算 `_work/w9c_probe_after1.py`）：
      · 1 格是这种「声明过、值是 null」（#3039 `dlc_3_Custom_Option_OSV96`）；
      · 469 格是**明文**（'Dragoon' / 'M-SHORAD, Stingers' / 'BMD-2M' …）—— 它们压根不在表里。
    两类混在一起的话，「查不到就回退」会把那 469 个明文选项一起改错。
    所以：**正文进 `entries`，null 的键只记在这个集合里**（既不塞空串、更不许变成 "None"）。
    """
    return frozenset(
        str(key) for key, value in zip(keys, values, strict=True) if value is None
    )


@dataclass(frozen=True, slots=True)
class TableEntries:
    """一份本地化表读出来的两样东西：有正文的键 + 值是 null 的键。"""

    entries: Mapping[str, str]
    nulls: frozenset[str] = frozenset()


# --------------------------------------------------------------------------- #
# 缓存
# --------------------------------------------------------------------------- #


def _bundle_stamp(bundle: Path) -> dict[str, Any]:
    stat = bundle.stat()
    return {"name": bundle.name, "size": stat.st_size, "mtime": round(stat.st_mtime, 3)}


def read_cache(
    cache_dir: Path, *, bundle: Path | None = None, language: str = DEFAULT_LANGUAGE
) -> dict[str, str] | None:
    """读缓存（只要正文那份）。给了 bundle 就**同时校验**它没换过（名字/大小/mtime）。

    校验不过返回 None —— 宁可从 bundle 重读一次，也不要拿旧包的中文去配新包。
    """
    table = read_cache_table(cache_dir, bundle=bundle, language=language)
    return None if table is None else dict(table.entries)


def read_cache_table(
    cache_dir: Path, *, bundle: Path | None = None, language: str = DEFAULT_LANGUAGE
) -> TableEntries | None:
    """读缓存**整份**（正文 + null 键）。校验口径与 `read_cache` 逐条相同。"""
    path = cache_file(cache_dir, language)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("version") != CACHE_VERSION:
        return None
    if payload.get("language") != language:
        return None
    entries = payload.get("entries")
    if not isinstance(entries, dict) or not entries:
        return None
    if bundle is not None and payload.get("bundle") != _bundle_stamp(bundle):
        return None
    raw_nulls = payload.get("nulls")
    if not isinstance(raw_nulls, list):
        # ❗ v2 的格式**必须**带 nulls 列表
        #    （少了它就分不出「没翻出来」和「明文」，显示回退链会走错）：
        #    认不出来的缓存一律作废、从 bundle 重读，别拿半份格式硬撑。
        return None
    nulls = frozenset(str(key) for key in raw_nulls)
    return TableEntries(
        entries={str(key): str(value) for key, value in entries.items()},
        nulls=nulls,
    )


def write_cache(
    cache_dir: Path,
    entries: Mapping[str, str],
    *,
    bundle: Path | None = None,
    language: str = DEFAULT_LANGUAGE,
    nulls: Iterable[str] = (),
) -> Path | None:
    """写缓存。写不进去返回 None（**不是错误**：只读盘上照样能用）。

    `nulls` = 表里声明过、但值是 null 的那些键（见 `null_keys_from_tables`）——
    它们**不进 entries**，但必须跟着缓存走，否则「从缓存起」那条路上又分不出
    「没翻出来」和「明文」。
    """
    path = cache_file(cache_dir, language)
    payload = {
        "version": CACHE_VERSION,
        "language": language,
        "bundle": _bundle_stamp(bundle) if bundle is not None else None,
        "count": len(entries),
        "entries": dict(entries),
        "nulls": sorted(str(key) for key in nulls),
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        return None
    return path


# --------------------------------------------------------------------------- #
# 词典对象
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Localization:
    """键 -> 中文（或该语言）文案。

    label / hint 这一对是给卡片用的：游戏的 tooltip 文案本身就是
    "标题\\n说明" 两段（例：ui_infocard_armor =
    "装甲值\\n当装甲值高于装甲穿透时, 受到的伤害将降低."），
    所以**行内标签取第一行、悬浮提示取剩下的**，一份数据两处用，不会打架。
    """

    entries: Mapping[str, str]
    source: str = SOURCE_BUILTIN
    language: str = DEFAULT_LANGUAGE
    bundle: Path | None = None
    #: 降级原因（人话）。非降级时空串。
    note: str = ""
    #: 小写索引（建一次留着）。见 text() 里"为什么要大小写不敏感"。
    lower: Mapping[str, str] | None = None
    #: 表里**声明过、但值是 null** 的键（= 没翻出来）。见 `null_keys_from_tables`。
    #: ⚠️ 它们**不在 entries 里** —— 这一份是「游戏承认有这条、只是没填」的凭据，
    #: 显示回退链靠它把「没翻出来」和「这格是明文」分开（实测 1 : 469）。
    nulls: frozenset[str] = frozenset()
    #: null 键的小写索引（与 lower 同一个理由：DB 写驼峰、键写全小写）。
    null_lower: frozenset[str] | None = None

    def __post_init__(self) -> None:
        if self.lower is None:
            object.__setattr__(
                self, "lower", {str(key).lower(): value for key, value in self.entries.items()}
            )
        if self.null_lower is None:
            object.__setattr__(self, "null_lower", frozenset(key.lower() for key in self.nulls))

    # -- 查 -------------------------------------------------------------- #

    def has(self, key: str) -> bool:
        """词典里**有没有正文**（值是 null 的那些键不算，见 `declared`）。"""
        return bool(key) and (key in self.entries or key.lower() in (self.lower or {}))

    def is_null(self, key: str) -> bool:
        """本地化表里声明过这个键、**但值是 null**（= 没翻出来）。"""
        if not key:
            return False
        return key in self.nulls or key.lower() in (self.null_lower or frozenset())

    def declared(self, key: str) -> bool:
        """本地化表里**有没有这个键** —— 翻出来了、或值是 null，都算「有」。

        与 `has()` 的分工（这一对是显示回退链的判据，别合并）：

        | 情况 | has | declared | 界面该画什么 |
        |---|---|---|---|
        | 翻出来了 | 真 | 真 | 译文 |
        | 声明过、值是 null | 假 | 真 | **回退链**（内部名/短名）—— 绝不画键名 |
        | 压根不在表里 | 假 | 假 | 这格是**明文**（'Dragoon' / 'M-SHORAD, Stingers'），照实画 |

        实测 `Options.UIName` 上后两类是 **1 : 469**（`_work/w9c_probe_after1.py`）——
        不分开判就会把那 469 个明文选项一起改错。
        """
        return self.has(key) or self.is_null(key)

    def text(self, key: str, default: str = "") -> str:
        """整段文案（**含换行**）。取不到时返回 default。

        ❗ **大小写不敏感**：数据库列的写法与本地化键的写法不一致 —— 实测
        Modifications.UIName = 'Custom_Slot_Squad_loadout'（驼峰），而键是
        custom_slot_squad_loadout（全小写）。先精确命中，再退回小写索引。
        实测本版 5379 条里**小写后无一条冲突**，所以这一步不会串味。

        "没翻出来"在词典里**根本没有这个键**（值是 null 的那 175 条见 `entries_from_tables`），
        所以这里不需要任何"值像不像占位符"的启发式：查不到就是查不到。
        """
        if not key:
            return default
        value = self.entries.get(key)
        if not (isinstance(value, str) and value):
            value = (self.lower or {}).get(key.lower())
        return value if isinstance(value, str) and value else default

    def label(self, key: str, default: str = "") -> str:
        """第一行（行内标签用）。"""
        text = self.text(key, default)
        return text.split("\n", 1)[0].strip() if text else default

    def hint(self, key: str, default: str = "") -> str:
        """除第一行以外的部分（悬浮提示用）。没有就返回 default。"""
        text = self.text(key)
        if "\n" not in text:
            return default
        return text.split("\n", 1)[1].strip()

    def enum(self, prefix: str, member: str, default: str = "") -> str:
        """枚举值 -> 中文。key = ui_enum_<前缀>_<值名小写>。

        ⚠️ 前缀**不是类型名的小写** —— 实测 TrajectoryType 的前缀是 trajectory
        （不是 trajectorytype）、WeaponType 的是 weapontype；值名也不是成员名的小写
        （DirectShot -> direct、LightMachineGun -> machinegun_light）。
        整套对应是 InfoCardLocalization 里的跳转表，抄在
        app/viewmodel/unit_card.py 的 WEAPON_TYPE_KEYS / TRAJECTORY_KEYS。
        """
        return self.label(f"ui_enum_{prefix.lower()}_{member.lower()}", default)

    # -- 状态 ------------------------------------------------------------ #

    @property
    def count(self) -> int:
        return len(self.entries)

    @property
    def is_degraded(self) -> bool:
        """是不是"没读到游戏原文"（用的是内置降级名）。"""
        return self.source == SOURCE_BUILTIN

    @property
    def source_label(self) -> str:
        return _SOURCE_LABELS.get(self.source, self.source)

    def describe(self) -> str:
        """一行状态，给状态栏用。降级时说清为什么。"""
        text = f"本地化：{self.source_label} · {self.count} 条"
        if self.bundle is not None:
            text += f"（{self.bundle.name}）"
        if self.note:
            text += f" · {self.note}"
        return text


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #


def load_localization(
    game_root: Path | None = None,
    *,
    language: str = DEFAULT_LANGUAGE,
    cache_dir: Path | None = None,
    refresh: bool = False,
) -> Localization:
    """加载本地化词典。**任何失败都退化成内置降级名，绝不抛异常。**

    顺序：缓存（且 bundle 未变）→ 读 bundle 并写缓存 → 陈缓存（bundle 没了）→ 内置。
    为什么先看缓存：UnityPy 开包是纯 Python 解析，省下的这几百毫秒
    正好是"打开产品到看见卡片"这段时间里最不值当花的一笔。
    """
    directory = cache_dir if cache_dir is not None else default_cache_dir()
    bundle = find_localization_bundle(game_root)

    if bundle is not None and not refresh:
        cached = read_cache_table(directory, bundle=bundle, language=language)
        if cached is not None:
            return Localization(
                entries=cached.entries,
                nulls=cached.nulls,
                source=SOURCE_CACHE,
                language=language,
                bundle=bundle,
            )

    note = ""
    if bundle is not None:
        try:
            table = read_bundle_entries(bundle, language=language)
        except BundleUnavailableError as exc:
            note = f"没装 UnityPy，读不了本地化包（{exc}）"
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            note = f"本地化包读失败：{exc}"
        else:
            write_cache(
                directory, table.entries, bundle=bundle, language=language, nulls=table.nulls
            )
            return Localization(
                entries=table.entries,
                nulls=table.nulls,
                source=SOURCE_BUNDLE,
                language=language,
                bundle=bundle,
            )

    # 退而求其次：盘上还有一份**陈旧**缓存也比没有强（游戏挪走了、UnityPy 没装）。
    stale = read_cache_table(directory, language=language)
    if stale is not None:
        return Localization(
            entries=stale.entries,
            nulls=stale.nulls,
            source=SOURCE_CACHE,
            language=language,
            bundle=bundle,
            note=note or "游戏目录里没有本地化包，用的是上次的缓存",
        )

    return Localization(
        entries=BUILTIN_LABELS,
        source=SOURCE_BUILTIN,
        language=language,
        bundle=None,
        note=note or "找不到游戏目录（可用 BA_MOD_MAKER_GAME 指定）",
    )


# --------------------------------------------------------------------------- #
# 降级用的内置中文名
# --------------------------------------------------------------------------- #

#: 只在**读不到游戏本地化包**时使用。覆盖卡片上真正会显示的那些键。
#: ⚠️ 这些名字与游戏内文案**可能有出入**，所以 Localization.is_degraded 会为真，
#: 界面必须把"这是降级名"这件事说出来（用户定的第 3 条：显示值必须与游戏内一致）。
#: ⚠️ 键名与取值都是**本版游戏实测**（localization_assets_all_204eaf9d…bundle，5379 条），
#: 不是猜的；游戏更新后以 bundle 为准，本表只是"读不到包"时的兜底。
BUILTIN_LABELS: Mapping[str, str] = {
    # 数值行（键名照 ui_infocard_*；顺序同 UnitInfoCard.SetUnitStatistic）
    "ui_infocard_armor": "装甲值\n当装甲值高于装甲穿透时, 受到的伤害将降低.",
    "ui_infocard_health": "生命值\n单位在被摧毁之前可以承受的伤害.",
    "ui_infocard_squad_count": (
        "小队成员数量\n当一名士兵阵亡时, 其武器会丢失, 因而小队的火力也会下降."
    ),
    "ui_infocard_cargo_capacity": "载重量\n该单位可承载的最大重量.",
    "ui_infocard_cargo_seats": "座位数\n可运送士兵的数量.",
    "ui_infocard_optics": "视野",
    "ui_infocard_stealth": "隐身\n降低敌人的探测范围.",
    "ui_infocard_speed_forward": "前进速度",
    "ui_infocard_speed_back": "倒车速度",
    "ui_infocard_agility": "敏捷",
    "ui_infocard_plane_turn_radius": "转弯半径\n飞机越敏捷, 转弯半径越小.",
    "ui_infocard_fuel": "燃油\n飞机在燃油耗尽之前的滞空时间.",
    "ui_infocard_weight": "重量",
    "ui_infocard_refund": "距离撤回至战斗群",
    "ui_infocard_refund_death": "阵亡后",
    "ui_infocard_refund_unscathed": "毫发无损",
    "ui_infocard_refund_noammo": "弹药耗尽",
    # 武器 / 弹药详情
    "ui_infocard_weapon_aimtime": "瞄准时间",
    "ui_infocard_weapon_reloadtime": "装填时间",
    "ui_infocard_weapon_rate_of_fire": "射速",
    "ui_infocard_weapon_magazinesize": "弹匣容量",
    "ui_infocard_weapon_salvo_length": "齐射时长",
    "ui_infocard_ammo_trajectory": "类型",
    "ui_infocard_ammo_effective_range": "有效射程",
    "ui_infocard_ammo_ground_range": "地面射程",
    "ui_infocard_ammo_lowalt_range": "低空射程",
    "ui_infocard_ammo_highalt_range": "高空射程",
    "ui_infocard_ammo_damage": "伤害",
    "ui_infocard_ammo_damage_per_second": "每秒伤害",
    "ui_infocard_ammo_penetration": "穿深",
    "ui_infocard_ammo_dispersion": "武器散布",
    "ui_infocard_ammo_supply_cost": "补给值",
    "ui_infocard_ammo_kinetic": "动能伤害",
    "ui_infocard_ammo_heat": "爆炸伤害",
    "ui_infocard_ammo_radius_blast": "爆炸半径",
    "ui_infocard_ammo_ignore_cover": "无视掩体",
    "ui_infocard_ammo_resupply_time": "补给时间",
    # 弹药的「能打什么」（AmmoInfoExpansionPanel.SetTargetTraits 那排图标）。
    # 键名取自 InfoCardLocalization 的 TryLocalize（字段偏移 0x220/0x228/0x230/0x238/
    # 0x240/0x248/0x250/0x258/0x260），取值照游戏包原文。
    "ui_infocard_ammo_target_infantry": "目标 - 步兵",
    "ui_infocard_ammo_target_vehicles": "目标 - 载具",
    "ui_infocard_ammo_target_helicopters": "目标 - 直升机",
    "ui_infocard_ammo_target_air": "目标 - 飞机",
    "ui_infocard_ammo_target_missiles": "目标 - 导弹",
    "ui_infocard_ammo_target_projectiles": "目标 - 投射物",
    "ui_infocard_ammo_target_sead": "目标 - 防空压制",
    "ui_infocard_ammo_target_cruise_missiles": "目标 - 巡航导弹",
    "ui_infocard_ammo_target_ballistic_missiles": "目标 - 弹道导弹",
    # 结构
    "ui_infocard_mod_options": "自定义选项",
    "ui_infocard_select_mod_label": "选择一个选项",
    "ui_infocard_no_weapons": "单位无武器",
    "ui_infocard_munitions_header": "弹药:",
    # 能力图标
    "ui_infocard_ability_airdropable": "可空投",
    "ui_infocard_ability_amphibious": "两栖",
    "ui_infocard_ability_aps": "主动防护系统",
    "ui_infocard_ability_autofire": "自动开火",
    "ui_infocard_ability_decoys": "诱饵干扰弹\n每个诱饵都有机会诱导来袭的导弹.",
    "ui_infocard_ability_ecm": "电子对抗\n降低来袭导弹的精度.",
    "ui_infocard_ability_laser": "激光指示",
    "ui_infocard_ability_radar": "雷达",
    "ui_infocard_ability_resupply": "重新补给",
    "ui_infocard_ability_smoke": "烟雾弹",
    "ui_infocard_ability_sneak": "潜行",
    "ui_infocard_ability_sprint": "冲刺",
    # 枚举：武器类型 / 弹药类型（前缀与值名都照抄 InfoCardLocalization 的跳转表）
    "ui_enum_weapontype_rifle": "步枪",
    "ui_enum_weapontype_marksman_rifle": "精确射手步枪",
    "ui_enum_weapontype_sniper_rifle": "狙击步枪",
    "ui_enum_weapontype_antimaterial_rifle": "反器材步枪",
    "ui_enum_weapontype_assault_rifle": "突击步枪",
    "ui_enum_weapontype_battle_rifle": "战斗步枪",
    "ui_enum_weapontype_smg": "冲锋枪",
    "ui_enum_weapontype_machinegun_light": "轻机枪",
    "ui_enum_weapontype_machinegun": "机枪",
    "ui_enum_weapontype_machinegun_heavy": "重机枪",
    "ui_enum_weapontype_minigun": "转轮机枪",
    "ui_enum_weapontype_autorifle": "自动步枪",
    "ui_enum_weapontype_shotgun": "霰弹枪",
    "ui_enum_weapontype_gl": "榴弹发射器",
    "ui_enum_weapontype_rpg": "火箭弹发射器",
    "ui_enum_weapontype_hand_granade": "手榴弹",
    "ui_enum_weapontype_atgm": "反坦克导弹",
    "ui_enum_weapontype_missile_cruise": "巡航导弹",
    "ui_enum_weapontype_missile_ballistic": "弹道导弹",
    "ui_enum_weapontype_missile_sead": "反辐射导弹",
    "ui_enum_weapontype_missile_manpad": "防空导弹",
    "ui_enum_weapontype_missile_sam": "地空导弹",
    "ui_enum_weapontype_missile_a2a_radar": "空空导弹",
    "ui_enum_weapontype_missile_a2a_ir": "空空导弹",
    "ui_enum_weapontype_missile_a2g": "空地导弹",
    "ui_enum_weapontype_main_tankgun": "主炮",
    "ui_enum_weapontype_howitzer": "榴弹炮",
    "ui_enum_weapontype_mortar": "迫击炮",
    "ui_enum_weapontype_mlrs": "多管火箭炮系统",
    "ui_enum_weapontype_autocannon": "机关炮",
    "ui_enum_weapontype_aa_gun": "高炮",
    "ui_enum_weapontype_plane_gun": "航炮",
    "ui_enum_weapontype_pod_rockets": "火箭吊舱",
    "ui_enum_weapontype_pod_gun": "机炮吊舱",
    "ui_enum_weapontype_bomb": "炸弹",
    "ui_enum_weapontype_bomb_highdrag": "高阻炸弹",
    "ui_enum_weapontype_bomb_smart": "智能炸弹",
    "ui_enum_trajectory_direct": "直射弹药",
    "ui_enum_trajectory_artillery": "火炮",
    "ui_enum_trajectory_mortar": "迫击炮",
    "ui_enum_trajectory_mlrs": "多管火箭炮系统",
    "ui_enum_trajectory_missile": "导弹",
    "ui_enum_trajectory_cruise": "巡航导弹",
    "ui_enum_trajectory_ballistic": "弹道导弹",
    "ui_enum_trajectory_bomb": "炸弹",
    "ui_enum_trajectory_bomb_highdrag": "高阻炸弹",
}


def _probe(argv: list[str]) -> int:
    """手动探针：python -m ba_mod_maker.core.assets.localization [键前缀...]

    给"这版游戏里到底有哪些 key"这种问题用，不参与产品运行。
    """
    loc = load_localization()
    print(loc.describe())
    print(f"bundle = {loc.bundle}")
    prefixes = argv or ["ui_infocard"]
    for prefix in prefixes:
        hits = sorted(key for key in loc.entries if key.startswith(prefix))
        print(f"--- {prefix}* : {len(hits)} 条 ---")
        for key in hits:
            print(f"  {key} = {loc.entries[key]!r}")
    return 0


if __name__ == "__main__":  # pragma: no cover - 手动探针
    import sys

    raise SystemExit(_probe(sys.argv[1:]))
