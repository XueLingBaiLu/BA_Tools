"""地址 → 图片字节。

对外的全部接口就是 resolve(族, 键) -> PNG 字节 | None。

解析链
------
    地址键（DB 里那一列的值）
      -> catalog.json          Addressables 的权威索引：键 → {(资产路径, 包名)}
      -> bundle 的 container   资产路径 → 对象
      -> Texture2D → PNG 字节

❗ 为什么不能按纹理名找：bundle 里实心图和空心线稿**同名**，
靠资产路径里的 outline 段区分。按名字撞是用户报的 151 张空心图标的根因。

❗ 为什么一个键会对应多个包：Addressables 记录的是一组**依赖包**，
真正的提供者只有一个。实测 DLC2 的一个缩略图有 6 个候选包
（dlc_2 + units + vfx + ammoprefabs + mapsettings + unitybuiltinshaders）。
所以要先按"像不像"排序，再**用 container 验证**——验证过就记住，不重复试。
"""

from __future__ import annotations

import threading
from collections.abc import Iterable, Mapping
from pathlib import Path

from ba_mod_maker.core.assets.bundles import BundleLibrary
from ba_mod_maker.core.assets.catalog import AddressablesCatalog
from ba_mod_maker.core.assets.families import family as family_of
from ba_mod_maker.core.assets.game import bundle_dir, catalog_file

#: 资产路径里的目录 -> 提供它的包名特征。
#: 实测出来的对应关系（数据侧 _external_full.json 的 bundles 字段 + 本机核对）。
_PATH_FAMILY_HINTS: tuple[tuple[str, str], ...] = (
    ("/images/labels/", "unitlabels"),
    ("/images/unitportraits/", "unitportraits"),
    ("/images/weapons/", "unitweaponicons"),
    ("/images/ammunition/", "ammoicons"),
    ("/images/nations & specs/", "nations&specs"),
    ("/images/modifications/", "unitmodifications"),
    # 信息卡的 sprite（目标类型图标…）全在 prefabsgui 里；同一个键在 catalog 里还挂着
    # 十几个依赖包，其中 units_assets_all 有 3.4 GB —— 没有这条提示会白开好几个大包。
    ("/infocard/", "prefabsgui"),
)

#: 这些包是**依赖**，几乎不会是提供者。排在最后，省得白开一个大包。
_DEPENDENCY_ONLY = (
    "unitybuiltinshaders",
    "vfx_assets",
    "mapsettings_",
    "defaultlocalgroup",
    "ammoprefabs",
)

#: **进程级**的解析锁。
#:
#: 为什么需要它：`BundleLibrary` 的注释写着"只由图标加载线程使用"，但那是**每个**
#: IconCache 各一条加载线程。UnityPy 的不安全是**进程级**的（类型树缓存、fsspec 的
#: 文件系统实例都是全局的），所以两条线程同时开包会撞崩。
#:
#: 实测（本仓）：`pytest tests/test_armory_screen.py` —— 加了一族 397 个预取键之后，
#: 两条还活着的旧加载线程与新线程重叠，报 "Windows fatal exception: access violation"，
#: 栈就在 `BundleFile.read_fs` / `parse_preload_table` 里。
#:
#: 对产品是**零成本**（本来就只有一条加载线程），对测试与多窗口是必需的。
_RESOLVE_LOCK = threading.Lock()


class IconResolver:
    """把 (族, 地址键) 解析成 PNG 字节。

    ⚠️ 不是线程安全的：只由图标加载线程使用（见 app/theme/icons.py）。
    """

    def __init__(
        self,
        game_root: Path | None,
        *,
        max_open_bundles: int = 2,
        catalog: AddressablesCatalog | None = None,
    ) -> None:
        self._game_root = game_root
        self._aa_dir = bundle_dir(game_root) if game_root else None
        self._catalog_path = catalog_file(game_root) if game_root else None
        self._library = BundleLibrary(self._aa_dir, max_open=max_open_bundles)
        #: catalog 可以注入：测试要的只是"键 → 候选包"这张表，不该为了它去开 3.8 MB 的
        #: catalog.json，更不该依赖本机装没装游戏。
        self._catalog: AddressablesCatalog | None = catalog
        self._catalog_loaded = catalog is not None
        #: 资产路径 -> 已经验证过能提供它的包。**多候选里选对一次就记住**。
        self._provider: dict[str, str] = {}
        #: 诊断计数（状态栏 / 测试用）
        self.stats = {"hit": 0, "miss": 0, "catalog_keys": 0, "bundles_opened": 0}

    # ------------------------------------------------------------------ #

    @property
    def available(self) -> bool:
        """有游戏目录就算可用（catalog 解析失败会在 resolve 时退化成 miss）。"""
        return self._library.available

    @property
    def game_root(self) -> Path | None:
        return self._game_root

    @property
    def catalog(self) -> AddressablesCatalog | None:
        """惰性解析 catalog（3.8 MB，实测 0.10s，解析一次留着）。"""
        if not self._catalog_loaded:
            self._catalog_loaded = True
            if self._catalog_path is not None:
                self._catalog = AddressablesCatalog.load(self._catalog_path.parent)
                if self._catalog is not None:
                    self.stats["catalog_keys"] = self._catalog.key_count
        return self._catalog

    # ------------------------------------------------------------------ #

    def resolve(self, family: str, key: str) -> bytes | None:
        """(族, 地址键) -> 编码好的图片字节；解析不出来返回 None（调用方去降级）。

        字节可能是 PNG 也可能是 JPEG——**由图片本身决定**（不透明的走 JPEG），
        调用方按魔数判断，别假设扩展名。

        family 只用于诊断与旧缓存回退，**解析本身只认地址**——
        "一个键属于哪一族"在数据里没有权威字段，而地址有。
        """
        if not key or not self._library.available or self._aa_dir is None:
            self.stats["miss"] += 1
            return None
        catalog = self.catalog
        if catalog is None:
            self.stats["miss"] += 1
            return None
        candidates = catalog.lookups(key)
        if not candidates:
            self.stats["miss"] += 1
            return None

        # UnityPy 是**进程级**非线程安全的（见 _RESOLVE_LOCK）：开包这一段必须串行。
        with _RESOLVE_LOCK:
            for asset_path, bundle_name in self._ordered(candidates):
                if not bundle_name:
                    continue
                bundle = self._aa_dir / bundle_name
                if not bundle.is_file():
                    continue
                before = self._library.opened
                payload = self._library.load_image(bundle, asset_path)
                self.stats["bundles_opened"] += self._library.opened - before
                if payload is not None:
                    self._provider[asset_path] = bundle_name
                    self.stats["hit"] += 1
                    return payload
        self.stats["miss"] += 1
        return None

    def _ordered(self, candidates: tuple[tuple[str, str], ...]) -> list[tuple[str, str]]:
        """把候选排成"最可能是提供者"的顺序。

        排序依据（都不花钱，只是排序；最终仍由 container 验证）：

        1. 已经验证过提供者的资产路径 → 直接用（缓存命中）；
        2. 键/路径带 DLC2/DLC3 且包名含 dlc_2/dlc_3 → 最优先
           （**DLC 单位的素材只有 DLC 包里有新的**，主包那份是旧图）；
        3. 资产路径的目录与包名特征对得上（见 _PATH_FAMILY_HINTS）；
        4. 已知的纯依赖包排最后；
        5. 包体积小的先试——失败也只是白开一个小的。
        """

        def score(item: tuple[str, str]) -> tuple:
            asset_path, bundle_name = item
            known = self._provider.get(asset_path)
            if known == bundle_name:
                return (0, 0, 0)
            low_path = asset_path.replace(chr(92), "/").lower()
            low_bundle = bundle_name.lower()
            dlc_rank = 0
            for tag in ("dlc2", "dlc3"):
                if tag in low_path:
                    dlc_rank = 0 if tag.replace("dlc", "dlc_") in low_bundle else 2
                    break
            family_rank = 1
            for fragment, marker in _PATH_FAMILY_HINTS:
                if fragment in low_path:
                    family_rank = 0 if marker in low_bundle else 1
                    break
            noise = 1 if any(n in low_bundle for n in _DEPENDENCY_ONLY) else 0
            return (1, dlc_rank + family_rank, noise)

        return sorted(candidates, key=score)

    # ------------------------------------------------------------------ #
    # 预取计划：**按提供者包分组**
    # ------------------------------------------------------------------ #

    #: 预测不出提供者的键排最后（它们本来就取不到图）。
    _UNKNOWN_RANK = 99

    def plan(self, grouped: Mapping[str, Iterable[str]]) -> list[tuple[str, str]]:
        """要预取的一批键 -> **按提供者包分组**的取图顺序。

        为什么必须重排（实测，本机 1522 个键，空缓存）
        ------------------------------------------------
        按键的原始顺序取（数据表行序），同一族的键会在主包与 DLC 包之间来回跳：
        单位缩略图 464 个键里有 88 个不在主包，而 BundleLibrary 同时只留得住
        2 个包（dlc_3 一个就 724 MB）。于是**每跳一次就把大包 UnityPy.load 一遍**：

        | 顺序 | 总耗时 | 开包耗时 | _env 调用 |
        |---|---|---|---|
        | 按族/行序 | 45.5s | 18.5s | 1533 次（重复开 263 次） |
        | **按包分组** | **18.6s** | **4.3s** | 1524 次（每个包只开一次） |

        组与组之间按族的 priority 排（首屏要用的族先取），组内按包体积从小到大
        ——小包先开完，界面上先有图，大包（DLC）在后面慢慢来。

        预测用的是 _ordered()，与 resolve() 第一次尝试的是同一个判据；
        猜错了 resolve() 自己会退到下一个候选，只是那一个键会多开一次包。
        没有 catalog（没装游戏 / 解析失败）时，原样返回输入顺序。
        """
        if self.catalog is None:
            return [(str(family), str(key)) for family, keys in grouped.items() for key in keys]

        picked: list[tuple[str, str, str, str, int]] = []
        ranks: dict[str, int] = {}
        for family, keys in grouped.items():
            definition = family_of(str(family))
            priority = definition.priority if definition is not None else 50
            for key in keys:
                text = str(key)
                if not text:
                    continue
                candidates = self._ordered(self.catalog.lookups(text))
                if candidates:
                    path, bundle = candidates[0]
                else:
                    path, bundle = "", ""
                picked.append((bundle, path, str(family), text, priority))
                if bundle:
                    known = ranks.get(bundle)
                    if known is None or priority < known:
                        ranks[bundle] = priority

        sizes = {bundle: self._bundle_size(bundle) for bundle in ranks}

        def order(item: tuple[str, str, str, str, int]) -> tuple:
            bundle, path, family, key, _priority = item
            if not bundle:
                return (self._UNKNOWN_RANK, 0, "", path, family, key)
            return (
                ranks.get(bundle, self._UNKNOWN_RANK),
                sizes.get(bundle, 0),
                bundle,
                path,
                family,
                key,
            )

        return [(family, key) for _bundle, _path, family, key, _p in sorted(picked, key=order)]

    def _bundle_size(self, bundle: str) -> int:
        """包体积（排序用）。文件不在就当成无穷大，排到最后。"""
        if self._aa_dir is None:
            return 0
        try:
            return (self._aa_dir / bundle).stat().st_size
        except OSError:
            return 1 << 62

    def describe(self, key: str) -> tuple[str, str] | None:
        """给界面/调试用：这个键最可能落在哪个包的哪个资产。"""
        catalog = self.catalog
        if catalog is None:
            return None
        ordered = self._ordered(catalog.lookups(key))
        return ordered[0] if ordered else None

    def close(self) -> None:
        """放掉已打开的 bundle（内存敏感时用）。"""
        self._library.close_all()


__all__ = ["IconResolver"]
