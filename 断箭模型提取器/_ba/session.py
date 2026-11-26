r"""**唯一**的会话：四个根 ＋ 那次开包 ＋ 那辆车（⛔ 不 import bpy）。

## 这一层收的是什么（交接文档 §2.2 那个「谁是权威没有唯一答案」）

整改前，同一件事各有 2~4 份实现：

| 助手 | 改前 | 现在 |
|---|---|---|
| `game_root_of` | 4 份（effects / mesh / xform / panels_07_install） | **1 份**：本文件 |
| `default_project_root` | 4 份 | **1 份**：本文件 |
| `project_root` | 3 份（out / panels_edit / panels_l10） | **1 份**：本文件 |
| `game_map_dir` | 2 份（materials / panels_edit） | **1 份**：本文件 |
| 开包 | importer / materials / xform_entry / effects / verify /
  panels_skins 各开一次 | **1 次**（`UnitSession.load_graph()` 缓存那次 `AssetSource`） |

判据（能红）：`_work/game-study/grep_criteria.py` -> 四个 `^def <名字>\(` 各 **1**；
`unit_graph.OPEN_COUNT` -> 同一单位连续三个阶段后仍 **1**。

## 三个根各是什么（⛔ 别混）

* `game_root`：游戏根（`aa/` 下有 catalog 的那一层）—— **只读**，`is_game_root` 判；
* `project_root`：补丁柜 `/patches` 落点 —— **必须与游戏同盘**（`reverse_delta.assert_same_drive`
  的机械红线：补丁柜在 C 盘、游戏在 D 盘会当场拒写）；
* `game_map_dir`：VT 抽出来的那两张图在哪（环境变量 `BAMOD_VT_DIR` 优先，
  否则仓内 `_work/vt-extract`）。

## 一句话给下一层

阶段模块（`materials` / `xform_entry` / `effects_entry` / `verify_entry` /
`material_entry` / `turret_entry`）**只吃** `UnitSession`（或它给的 `graph` / `source`）：
⛔ 不再自带「catalog 自动 / project_root 空 / game_root 空」那三档兜底。
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ba_mod_maker.asset_kernel import unitypy_source

from . import unit_graph

__all__ = [
    "PROJECT_DIRNAME",
    "UnitSession",
    "catalog_path_of",
    "default_project_root",
    "game_map_dir",
    "game_root_of",
    "open_count",
    "project_root",
    "reset_open_count",
    "session_for",
]

#: 补丁柜的目录名（用户级兜底 `<base>\BAModMaker\project`）。
PROJECT_DIRNAME = "BAModMaker"
#: VT 抽图目录的环境变量（与 `materials.VT_MAP_DIR_ENV` 同一个字符串，⛔ 别改一处）。
GAME_MAP_DIR_ENV = "BAMOD_VT_DIR"
#: 输出目录根的环境变量（⑦ 出 那一格用的是它）。
OUT_PROJECT_ENV = "BA_OUT_PROJECT_ROOT"


def _repo_root() -> Path:
    """开发树根（`blender_addon/` 与 `src/` 同一个父）。装成插件时它是 `<addons>/…`，靠存在性判。"""
    return Path(__file__).resolve().parent.parent


def _is_dev_tree() -> bool:
    return (_repo_root() / "src" / "ba_mod_maker").is_dir()


# --------------------------------------------------------------------------- #
# 四个根：**每个名字只有这一份实现**（交接文档 §4 判据 1）
# --------------------------------------------------------------------------- #
def game_root_of(bundle: Any) -> Path | None:
    """从包的路径往上找游戏根（`aa/` 下有 catalog 的那一层）；找不到返回 None。

    判据落在产品那一份 `core.assets.game.is_game_root`（⛔ 本插件不自己定义「什么是游戏根」）；
    影子根（用户另建的干净副本）就是靠它认出来的。
    """
    if bundle is None:
        return None
    from ba_mod_maker.core.assets.game import is_game_root

    try:
        parents: Iterable[Path] = Path(str(bundle)).parents
    except (TypeError, ValueError):
        return None
    for parent in parents:
        try:
            if is_game_root(parent):
                return parent
        except Exception:  # noqa: BLE001 -- 判根失败不许把调用方弄崩
            continue
    return None


def find_game_root() -> Path | None:
    """产品那一份自动找根（`BA_MOD_MAKER_GAME` → 注册表 → 常见路径）；找不到 None。"""
    from ba_mod_maker.core.assets.game import find_game_root as _find

    found = _find()
    return Path(str(found)) if found else None


def default_project_root(*, game_root: Any = None) -> Path:
    r"""补丁柜的**确定**落点（`<它>/patches`）—— ⛔ 不许跟着当前目录漂。

    三条（顺序就是优先级）：

    1. **开发树**（`blender_addon/` 与 `src/` 同父）⇒ **仓根**（跑测试 / 自检不变）；
    2. 装成插件 ⇒ **`<游戏所在盘>\.bamod-maker\project`**；
    3. 连游戏根都没有 ⇒ `%LOCALAPPDATA%\BAModMaker\project`。

    ★ 为什么第 2 条必须跟盘：产品有一条机械红线 `reverse_delta.assert_same_drive(target, game_root)`
    —— 「补丁柜在 C 盘、游戏在 D 盘」当场拒写（2026-10-15 真机 rc=2 的原因）。
    """
    if _is_dev_tree():
        return _repo_root()
    game = Path(str(game_root)) if game_root else find_game_root()
    drive = ""
    if game is not None:
        try:
            drive = os.path.splitdrive(str(Path(game).resolve()))[0]
        except OSError:
            drive = os.path.splitdrive(str(game))[0]
    if drive:
        return Path(drive + os.sep) / ".bamod-maker" / "project"
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_CACHE_HOME")
    root = Path(base) if base else Path.home() / ".bamod-maker"
    return root / PROJECT_DIRNAME / "project"


def project_root(
    *,
    env: str | None = None,
    session_override: Any = None,
    game_root: Any = None,
) -> Path:
    """补丁柜落点 ── **三个显式旋钮**盖住原来那三份 `project_root()` 的语义：

    * `env=`：先看这个环境变量（⑦ 出 那一格：`BA_OUT_PROJECT_ROOT`）；
    * `session_override=`：会话里选的工程目录（⑥ 装机那一格记的）；
    * 都没有 ⇒ 找**已有的**那份补丁柜（兼容历史落点，见 `find_project_root`），
      找不到就用 `default_project_root(game_root=…)`。
    """
    if env:
        raw = str(os.environ.get(env) or "").strip()
        if raw:
            return Path(raw)
    if session_override is not None and str(session_override).strip():
        return Path(str(session_override))
    return find_project_root(game_root=game_root)


def find_project_root(*, game_root: Any = None) -> Path:
    """真去找一份**已有的**补丁柜：确定的那个 → 旧默认（当前目录）→ C 盘那份（更早的版本）。

    （2026-10-15 一晚上换了两次落点，每次都留下已经写出去的补丁 ⇒ 换落点也必须**找得到旧的**，
    否则「一键还原」会突然说「没有可还原的目标」，而用户的包已经被改过。）
    """
    stable = default_project_root(game_root=game_root)
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_CACHE_HOME")
    older = (
        (Path(base) if base else Path.home() / ".bamod-maker") / PROJECT_DIRNAME / "project"
    )
    for root in (stable, Path.cwd(), older):
        if (root / "patches" / "install.rmod.json").is_file():
            return root
    return stable


def game_map_dir() -> Path:
    """抽出来的游戏原图在哪 —— 环境变量 `BAMOD_VT_DIR` 优先，否则仓内 `_work/vt-extract`。"""
    override = os.environ.get(GAME_MAP_DIR_ENV)
    if override:
        return Path(override)
    return _repo_root() / "_work" / "vt-extract"


def catalog_path_of(game_root: Any) -> Path | None:
    """catalog 文件在哪 —— **两个名字都认**（`catalog.json` ≤1.2.0 / `catalog.bin` ≥1.2.1）。"""
    if not game_root:
        return None
    from ba_mod_maker.core.assets.game import CATALOG_NAMES

    aa_dir = Path(str(game_root)) / "BrokenArrow_Data" / "StreamingAssets" / "aa"
    for name in CATALOG_NAMES:
        candidate = aa_dir / name
        if candidate.is_file():
            return candidate
    return None


def aa_dir_of(game_root: Any) -> Path | None:
    """`…/BrokenArrow_Data/StreamingAssets/aa`（包的父目录）。"""
    if not game_root:
        return None
    return Path(str(game_root)) / "BrokenArrow_Data" / "StreamingAssets" / "aa"


# --------------------------------------------------------------------------- #
# 开包计数（判据：同一单位连续三个阶段 ⇒ 仍然 == 1）
# --------------------------------------------------------------------------- #
def open_count() -> int:
    """进程内 `AssetSource` 的累计打开次数（计数器在 `asset_kernel.unitypy_source` 里）。"""
    return int(getattr(unitypy_source, "OPEN_COUNT", 0))


def reset_open_count() -> int:
    """把计数器清零并返回清零前的值（探针 / 用例在每个阶段前调一次）。"""
    reset = getattr(unitypy_source, "reset_open_count", None)
    if callable(reset):
        return int(reset())
    return open_count()


# --------------------------------------------------------------------------- #
# 会话
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class UnitSession:
    """一次会话：四个根 + 一次开包 + 那辆车的图（三个阶段看同一份）。

    ⚠ **生命周期**：`graph()` 打开的那个 `AssetSource` **归会话管**（3.4 GB 的包 ≈ 10.9 GB 内存），
    用完必须 `close()`（或用 `with`）；⛔ 别在会话活着的时候再开第二个包。
    """

    game_root: Path | None = None
    project_root: Path = field(default_factory=lambda: Path.cwd())
    aa_dir: Path | None = None
    catalog_path: Path | None = None
    unit_key: str = ""
    asset_path: str = ""
    candidates: tuple[str, ...] = ()
    bundle: str = ""
    bundle_path: Path | None = None
    graph: unit_graph.UnitGraph | None = None
    #: 数据侧那台 instance（给了才读表 / 挂炮塔）。
    instance: Any = None
    #: `True` = 文件路会话（直接开这个文件，⛔ 不走 catalog）。
    direct: bool = False
    #: `False` = 这次**明确不登记** catalog 的 `m_Crc`（旧 `catalog=None` 那一档）。
    #: ⚠ 为什么要它：影子根 / 夹具包上写回时，包在目录里但 catalog 不该被动 ——
    #:   删掉这一档会让「我不想动 catalog」这件事表达不出来（M2 实测）。
    register_catalog: bool = True
    notes: list[str] = field(default_factory=list)
    # ⚠ 下面三个**必须是真字段**：`slots=True` 的 dataclass 里给没声明的属性赋值会
    #   AttributeError（2026-10-18 实测 `'UnitSession' object has no attribute '_source'`）。
    _source: Any = field(default=None, repr=False)
    _graph_flags: tuple[bool, bool] | None = field(default=None, repr=False)
    _db_opened: Any = field(default=None, repr=False)

    # ---- 构造 ----
    @classmethod
    def for_unit(
        cls,
        unit_key: str,
        *,
        game_root: Any = None,
        project_root_override: Any = None,
        with_db: bool = False,
    ) -> UnitSession:
        """按**地址键**建会话（只解根与 catalog，⛔ 不开包 —— 开包在 `graph()` 那一步）。"""
        root = Path(str(game_root)) if game_root else find_game_root()
        aa = aa_dir_of(root)
        return cls(
            game_root=root,
            project_root=project_root(
                session_override=project_root_override, game_root=root
            ),
            aa_dir=aa,
            catalog_path=catalog_path_of(root),
            unit_key=str(unit_key),
            instance=None if not with_db else None,
        )

    @classmethod
    def for_bundle(
        cls,
        bundle_path: Any,
        *,
        asset_path: str = "",
        unit_key: str = "",
        project_root_override: Any = None,
    ) -> UnitSession:
        """按**磁盘上一个 .bundle 文件**建会话（不走 catalog；文件路那条）。"""
        path = Path(str(bundle_path))
        root = game_root_of(path)
        return cls(
            game_root=root,
            project_root=project_root(
                session_override=project_root_override, game_root=root
            ),
            aa_dir=aa_dir_of(root),
            catalog_path=catalog_path_of(root),
            unit_key=str(unit_key),
            asset_path=str(asset_path),
            bundle=path.name,
            bundle_path=path,
            candidates=(path.name,),
            direct=True,
        )

    # ---- 打开 / 关闭 ----
    def __enter__(self) -> UnitSession:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        """关掉那次开包（⛔ 不关就等于把 10 GB 钉在内存里）。"""
        source = getattr(self, "_source", None)
        if source is not None:
            try:
                source.close()
            finally:
                self._source = None  # type: ignore[attr-defined]
        self.graph = None

    @property
    def source(self) -> Any:
        """那次开包的读源（`graph()` 之后再取，拿到的就是**同一个**对象）。"""
        return getattr(self, "_source", None)

    # ---- 图 ----
    def load_graph(
        self,
        unit_key: str | None = None,
        *,
        only_lod0: bool = False,
        with_sha: bool = False,
        with_db: bool = False,
        align_aimable: bool = False,
    ) -> unit_graph.UnitGraph:
        """**一次**开包并把车物化出来（同一个单位重复调用 ⇒ 直接给缓存，⛔ 不再开包）。

        :param with_db: True = 顺手挂炮塔（要 `data.unity3d`，只读）。
        """
        key = str(unit_key or self.unit_key or "")
        wanted = (bool(only_lod0), bool(align_aimable))
        if (
            self.graph is not None
            and (not key or key == self.unit_key)
            and getattr(self, "_graph_flags", None) == wanted
        ):
            return self.graph
        if key:
            self.unit_key = key
        if self._source is None:
            self._open()
        if not self.asset_path:
            # 文件路：容器里**唯一**那条 .prefab（0 条 / 多条都拒，⛔ 不猜）；
            # 只要读对象、不要图时走 `open()`，不会走到这里。
            self.asset_path = _only_prefab(self._source)
        if with_db:
            self.load_db()
        graph = unit_graph.build_graph(
            self._source,
            self.asset_path,
            unit_key=self.unit_key,
            bundle=self.bundle,
            bundle_path=self.bundle_path or "",
            roots=unit_graph.GraphRoots(
                game_root=self.game_root,
                aa_dir=self.aa_dir,
                catalog_path=self.catalog_path,
                project_root=self.project_root,
            ),
            only_lod0=only_lod0,
            instance=self.instance,
            with_sha=with_sha,
            align_aimable=align_aimable,
        )
        # 目标集 / 朝向校正变了 ⇒ 图就得重算（但**包不重开**：`_source` 一直在）
        self._graph_flags = wanted  # type: ignore[attr-defined]
        self.graph = graph
        return graph

    def open(self) -> Any:
        """把这次会话的那个包**打开**并返回读源（**全插件唯一的开包点**）。

        要「只是想读包里某个对象、并不需要一辆车的图」时用它（例：effects 的 vfx 包）；
        ⛔ 阶段模块不要自己 `unitypy_source.open_source`。
        """
        return self._open()

    def _open(self) -> Any:
        """开那个包（`open()` / `load_graph()` 的同一份实现）。"""
        if self._source is not None:
            return self._source
        if self.bundle_path is not None and self.direct:
            self._source = unitypy_source.open_source(self.bundle_path)
            return self._source
        if self.catalog_path is None:
            raise FileNotFoundError(
                f"读不到 catalog：{aa_dir_of(self.game_root)}（catalog.json / catalog.bin 都没有）"
            )
        from ba_mod_maker.core.assets.catalog import AddressablesCatalog

        catalog = AddressablesCatalog.load(Path(str(self.aa_dir)))
        if catalog is None:
            raise FileNotFoundError(f"读不到 catalog：{self.catalog_path}")
        hits = catalog.lookups(self.unit_key)
        if not hits:
            raise KeyError(
                f"catalog 里没有地址键 {self.unit_key!r}（共 {catalog.key_count} 个键）"
            )
        assets = sorted({asset for asset, _bundle in hits})
        if len(assets) != 1:
            raise ValueError(f"地址键 {self.unit_key!r} 指向 {len(assets)} 个资产：{assets}")
        self.asset_path = str(assets[0])
        self.candidates = tuple(sorted({b for _a, b in hits if b}))
        pc = Path(str(self.aa_dir)) / "PC"
        # ★ 2026-10-18 实测：open_unique_bundle 会**逐个构造 AssetSource** 来复核容器表 ——
        #   RU_T72B 的 catalog 候选有 **9 个**
        #   （units / ammo / vfx / dlc_2 / dlc_3 / monoscripts 等），
        #   于是「开一次包」的读数其实是 **9**（8 个开完就关）。判据要求 == 1，所以先用
        #   轻量容器探针（containers_map_via_ufs：只读容器目录、不建 Environment）挑，
        #   只把命中的那一个真开出来；认不出的候选再回退重的老办法（⛔ 不静默跳过）。
        source, name = _open_unique_cheap(pc, self.candidates, self.asset_path)
        self.bundle = str(name)
        self.bundle_path = pc / name
        self._source = source  # type: ignore[attr-defined]
        if getattr(source, "dep_note", ""):
            self.notes.append(str(source.dep_note))
        return source

    # ---- 数据侧 ----
    def load_db(self) -> Any:
        """打开一次数据侧（24 张表，只读）—— 会话内复用，⛔ 不重复开。

        `data.unity3d` 是 7.1 GB 的容器，但只解那一个对象（约 5.8 MB）；
        路径口径与 `turret_entry._open` **同一个**（`core.session.open_source`）。
        """
        if self.instance is not None:
            return self.instance
        from ba_mod_maker.core import session as core_session
        from ba_mod_maker.core.data.from_source import dataset_from_source

        if self.game_root is None:
            raise FileNotFoundError("没有游戏根 ⇒ 读不到数据侧（24 张表）")
        from ba_mod_maker.core.asset import unit_names

        source = unit_names.source_file(Path(str(self.game_root)))
        opened = core_session.open_source(source)
        with opened as src:
            dataset = dataset_from_source(src)
            self.instance = dataset.instance(dataset.default_instance_id)
        self._db_opened = source
        return self.instance


def _open_unique_cheap(
    pc: Path, candidates: Iterable[str], asset_path: str
) -> tuple[Any, str]:
    """找出**唯一**含 asset_path 的候选包并打开它 —— 只开**命中**的那一个。

    ⚠ 为什么不用 unitypy_source.open_unique_bundle：它逐个构造 AssetSource 复核容器表，
    catalog 的 group 字段会过度包含（实测 RU_T72B 给 **9** 个候选包）⇒ 开包计数变成 9。
    这里先用 containers_map_via_ufs（只读容器目录，不建 Environment）筛，再真开。

    :raises FileNotFoundError: 一个都没命中，或命中多个（**不许猜**）。
    """
    hits: list[str] = []
    already: dict[str, Any] = {}
    unknown: list[str] = []
    names = [str(name) for name in candidates]
    for name in names:
        path = Path(pc) / name
        if not path.is_file():
            continue
        try:
            mapping = unitypy_source.containers_map_via_ufs(path)
        except Exception:  # noqa: BLE001 -- 我们的读侧认不出 ⇒ 记下来走回退
            unknown.append(name)
            continue
        if str(asset_path) in {str(key) for key in mapping}:
            hits.append(name)
    if len(hits) != 1 and unknown:
        for name in unknown:
            source = unitypy_source.open_source(Path(pc) / name)
            if str(asset_path) in source.asset_bundle_containers():
                hits.append(name)
                already[name] = source
            else:
                source.close()
    if len(hits) == 1:
        name = hits[0]
        source = already.get(name) or unitypy_source.open_source(
            Path(pc) / name, deps=True
        )
        return source, name
    for source in already.values():
        source.close()
    raise FileNotFoundError(
        f"{asset_path!r} 在 {len(hits)} 个候选包里命中（候选 {len(names)} 个）：{hits}"
    )


def _only_prefab(source: Any) -> str:
    """文件路：容器里**唯一**那条 `.prefab` 地址（0 条 / 多条都拒，⛔ 不猜）。"""
    entries = [str(addr) for addr, _pid in source.container_entries()]
    prefabs = [addr for addr in entries if addr.lower().endswith(".prefab")]
    if len(prefabs) == 1:
        return prefabs[0]
    raise ValueError(
        f"这个包里有 {len(prefabs)} 条 .prefab（共 {len(entries)} 条容器）—— 请显式给 asset_path"
    )


def session_for(
    unit_key: str = "",
    *,
    bundle_path: Any = None,
    asset_path: str = "",
    game_root: Any = None,
    project_root_override: Any = None,
) -> UnitSession:
    """一句话拿一个会话（给了 `bundle_path` 就走文件路，否则按地址键走 catalog 路）。"""
    if bundle_path is not None:
        return UnitSession.for_bundle(
            bundle_path,
            asset_path=asset_path,
            unit_key=unit_key,
            project_root_override=project_root_override,
        )
    return UnitSession.for_unit(
        unit_key,
        game_root=game_root,
        project_root_override=project_root_override,
    )
