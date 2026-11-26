"""真实图标的加载与缓存。

数据来源（见 docs/设计稿-图标管线.png）
--------------------------------------
图标**直接从游戏自己的 .bundle 里按地址取**，不再依赖预提取的 cache/icons：

- 省空间：cache/icons 有 185 MB（立绘就占 164 MB），现在不用随产品分发；
- 改地址立刻见效：每次都是按键实时解析，改了 DB 里的图标键马上能看到新图；
- 新增图标自动可见：游戏更新/DLC 加的新图不用改产品。

降级链（**一环都不能断**）
--------------------------
    内存 QPixmap
      -> 运行时磁盘缓存（按需生成，不进仓库）
        -> 旧 cache/icons（只在这台机器**没装游戏**时才用）
          -> 程序化剪影（由调用方决定画什么）

❗ 为什么旧缓存要排在"没装游戏"之后：旧提取脚本按纹理名撞，
把 151 张图标取成了 outline 空心版（用户报的 Konkurs）。有游戏时就该走权威地址，
不能让旧缓存把它盖回去。

线程模型
--------
pixmap() 只在 GUI 线程被调用，**绝不阻塞**：
命中就返回，没命中就入队，由后台线程解析，好了发 loaded 信号。
QPixmap 只能在 GUI 线程构造，所以后台线程只传 PNG 字节。
"""

from __future__ import annotations

import contextlib
import json
import os
import queue
import threading
import time
from collections import OrderedDict, deque
from collections.abc import Iterable, Mapping
from pathlib import Path

from PySide6.QtCore import QObject, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QPixmap, QTransform

from ba_mod_maker.core.assets import find_game_root
from ba_mod_maker.core.assets.families import family as family_of
from ba_mod_maker.core.assets.families import family_mirrors, sanitize
from ba_mod_maker.core.assets.resolver import IconResolver

#: 环境变量覆盖，方便把缓存放到别处（CI / 多份数据源）。
ENV_VAR = "BA_MOD_MAKER_ICONS"

#: 运行时缓存目录的环境变量。
ENV_RUNTIME = "BA_MOD_MAKER_ICON_CACHE"

#: 子目录名：<项目根>/cache/icons（旧的一次性提取产物，现在只作兜底）。
_CACHE_PARTS = ("cache", "icons")

_MISSING = object()

#: loaded 信号的合并间隔（毫秒）。图标是一批批到的，逐个发信号会让
#: 每张卡都重画一次——四百张卡就是四百次无谓重绘。
_COALESCE_MS = 80

#: 速率/ETA 的滑动窗口（秒）。开一个大包要 1~2 秒没有任何产出，
#: 窗口太短会把 ETA 算得忽长忽短；太长又跟不上真实速度变化。
_RATE_WINDOW = 6.0

#: 内存里最多留多少图（字节，估计值 = 宽 x 高 x 4）。
#: ❗ 必须有上限：立绘一张 816x550 就要 1.8 MB，433 张全留是 780 MB。
#: 超了就按 LRU 丢——**丢的只是内存副本**，磁盘缓存还在，下次读回来即可。
_MEMORY_BUDGET = 192 * 1024 * 1024


def find_icon_cache() -> Path | None:
    """定位**旧的**预提取缓存目录。找不到返回 None（**不是错误**）。"""
    override = os.environ.get(ENV_VAR)
    if override:
        candidate = Path(override)
        return candidate if candidate.is_dir() else None

    current = Path(__file__).resolve().parent
    for _ in range(8):
        if (current / "pyproject.toml").is_file():
            candidate = current.joinpath(*_CACHE_PARTS)
            return candidate if candidate.is_dir() else None
        if current.parent == current:
            break
        current = current.parent
    return None


def find_runtime_cache() -> Path:
    """运行时缓存目录（按需生成的 PNG 落这里）。

    默认放用户目录而不是仓库里：它是**生成物**，不该跟着产品分发，
    也不该让 git 看见 185 MB 的图。
    """
    override = os.environ.get(ENV_RUNTIME)
    if override:
        return Path(override)
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_CACHE_HOME")
    root = Path(base) if base else Path.home() / ".cache"
    return root / "BA_Mod_Maker" / "icons"


#: 磁盘缓存里可能出现的扩展名。**两种都要认**：不透明的图存 JPEG（快 47 倍、小 8.5 倍），
#: 有真透明的必须 PNG。判据是图片本身，不是族名。
_CACHE_SUFFIXES = (".png", ".jpg")


def cache_file(directory: Path, key: str) -> Path | None:
    """磁盘缓存里这张图的路径；没有返回 None。"""
    stem = sanitize(key)
    for suffix in _CACHE_SUFFIXES:
        candidate = directory / (stem + suffix)
        if candidate.is_file():
            return candidate
    return None


def scale_for_widget(pixmap: QPixmap, size: QSize, widget) -> QPixmap:
    """把原图缩到**控件要画的逻辑尺寸**，并按屏幕缩放设好 devicePixelRatio。

    ❗ 为什么不能直接 pixmap.scaled(逻辑尺寸)：在 150% 缩放的屏上（用户那台就是），
    Qt 会把 22 逻辑像素的图**再放大 1.5 倍**去画 —— 于是图标发虚。
    正确做法是先缩到**物理像素**（逻辑 x dpr），再 setDevicePixelRatio，
    这样 Qt 按 1:1 贴上去（实测：专精图标 96x96 原图 -> 33x33 物理，边缘干净）。
    """
    ratio = 1.0
    if widget is not None:
        ratio = float(widget.devicePixelRatioF() or 1.0)
    width = max(1, round(size.width() * ratio))
    height = max(1, round(size.height() * ratio))
    out = pixmap.scaled(
        QSize(width, height),
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    out.setDevicePixelRatio(ratio)
    return out


def prepare_icon(
    pixmap: QPixmap, family: str, size: QSize, widget, key: str = ""
) -> QPixmap:
    """界面侧**唯一**的图标准备入口：先按族（+键）决定水平翻，再缩到控件尺寸（带 DPR）。

    为什么翻转住在这里：原图朝左、游戏在 UI 里翻了一次 —— 这条知识只有一份出处
    （`core/assets/families.py` 的 `IconFamily.mirrored` / `family_mirrors()`）。
    用户口径：「武器在 GUI 里是朝左边的，应该朝右边才是」；判据 `INF_AK74RMO.png`
    （231×82）枪口朝左、实机卡上朝右。⚠️ **弹药那族本来就朝右，别一起翻**。

    界面**不许**自己 `scaled()` / `transformed(scale(-1,1))` —— 曾经这份逻辑被抄在
    三个地方，于是新加的中栏抬头漏了翻转，用户看到武器又朝左（「还是老问题」）。

    ❗ `key` = 这张图的地址键，**必须一路传到底**：`option_pictures` 那一族是
    **逐资产**判的（同一族里 `INF_*` 要翻、`MOD_*` 不能翻 —— 出处与实测见
    `core/assets/families.py::OPTION_PICTURE_UNMIRRORED_PREFIXES`）。
    漏传 = 那处按族默认翻，症状正是用户 2026-10-03 报的「改装的图标跟游戏是反的」。
    """
    if family_mirrors(family, key):
        pixmap = pixmap.transformed(QTransform().scale(-1, 1))
    return scale_for_widget(pixmap, size, widget)


def cache_target(directory: Path, key: str, payload: bytes) -> Path:
    """该往哪写：按**魔数**定扩展名（JPEG 以 FFD8 开头，其余按 PNG）。"""
    suffix = ".jpg" if payload[:2] == bytes((0xFF, 0xD8)) else ".png"
    return directory / (sanitize(key) + suffix)


def format_duration(seconds: float | None) -> str:
    """秒 -> 中文时长（"32 秒" / "1 分 05 秒"）。None / 负数返回空串（= 估不出来）。"""
    if seconds is None or seconds < 0:
        return ""
    if seconds < 1:
        return "不到 1 秒"
    if seconds < 60:
        return f"{int(seconds + 0.5)} 秒"
    minutes, rest = divmod(int(seconds + 0.5), 60)
    return f"{minutes} 分 {rest:02d} 秒"


def prefetch_status_text(
    cache,
    *,
    done: int | None = None,
    total: int | None = None,
    current: bool = False,
) -> str:
    """一行人类可读的预取状态：640/1519 · 42% · 剩约 32 秒 · 正在取：单位立绘。

    门禁对话框与状态栏**共用同一份文案**，免得两处各说各的。
    没有预取（缓存已就绪）时返回空串；已经跑完时返回"全部就绪"。

    done/total 可以用信号里带来的那两个值覆盖：进度是**事件**说了算，
    不要再去读一次缓存的可变状态（两者在别的线程里更新时会打架）。
    """
    if total is None:
        total = getattr(cache, "prefetch_total", 0)
    if done is None:
        done = getattr(cache, "prefetch_done", 0)
    if total <= 0:
        return ""
    if done >= total:
        return f"{total} 张全部就绪"

    parts = [f"{done}/{total}", f"{done * 100 // total}%"]
    eta = getattr(cache, "prefetch_eta", None)
    if eta is None:
        parts.append("正在估算剩余时间…")
    else:
        rate = getattr(cache, "prefetch_rate", 0.0) or 0.0
        tail = f"（约 {rate:.0f} 张/秒）" if rate >= 1 else ""
        parts.append(f"剩约 {format_duration(eta)}{tail}")
    if current:
        label = getattr(cache, "prefetch_current_label", "")
        if label:
            parts.append(f"正在取：{label}")
    return " · ".join(parts)


class _Loader(threading.Thread):
    """后台解析线程：取 (族, 键)，解析成图片字节（PNG 或 JPEG）。

    只有它碰 IconResolver（UnityPy 不是线程安全的）。

    ❗ **两条队列**：界面上要用的（_jobs）优先于预加载的（_bulk）。
    否则用户滚到一张新卡时，请求会排在 1500 张预取后面，等于是"越预取越卡"。
    """

    def __init__(
        self,
        resolver: IconResolver,
        jobs: queue.Queue,
        bulk: queue.Queue,
        out: queue.Queue,
        disk: Path,
    ) -> None:
        super().__init__(name="icon-loader", daemon=True)
        self._resolver = resolver
        self._jobs = jobs
        self._bulk = bulk
        self._out = out
        self._disk = disk
        # ❗ **不能叫 _stop**：那是 threading.Thread 自己的内部方法，
        # join() 会去调它。覆盖成 Event 之后一 join 就炸：
        # "TypeError: Event object is not callable"（实测踩过）。
        self._stopped = threading.Event()
        #: 预加载的暂停闸。用户在拖动/输入时拉起来，别跟界面抢 GIL。
        self.paused = threading.Event()
        #: 正在解析的 (族, 键)；闲着时是 None。**只给界面显示用**——
        #: 开一个大包要一两秒没有产出，进度条不动的时候得让用户看见"在忙什么"。
        #: 单次赋值是原子的，读端读到上一张也无所谓。
        self.current: tuple[str, str] | None = None

    def stop(self) -> None:
        self._stopped.set()
        # 队列无界，这个异常实际不会发生；用 suppress 是为了不写一个空 except。
        with contextlib.suppress(queue.Full):
            self._jobs.put_nowait("STOP")

    def _next(self):
        """先看优先队列；批量任务在"用户正在操作"时**让路**。

        ❗ 必须让路：UnityPy 解析 bundle 有大量**纯 Python** 帧（实测一次拖动里
        12 万次 tell、24 万次 read），它们全程占着 GIL，界面就会一顿一顿。
        界面要用的那张（_jobs）永远不暂停。
        """
        try:
            return self._jobs.get_nowait(), False
        except queue.Empty:
            pass
        if self.paused.is_set():
            self._stopped.wait(0.05)
            self.current = None
            return None, False
        try:
            return self._bulk.get(timeout=0.2), True
        except queue.Empty:
            self.current = None
            return None, False

    def run(self) -> None:  # pragma: no cover - 线程体，靠集成测试覆盖
        while not self._stopped.is_set():
            job, bulk = self._next()
            if job is None:
                continue
            if job == "STOP":
                break
            family, key = job
            self.current = (family, key)
            if bulk and cache_file(self._disk / family, key) is not None:
                # 这张已经被优先路径解过了（界面插队），批量任务直接作废：
                # 空 payload 当"跳过"，既不计入进度也不算失败。
                self._out.put((family, key, b"", True))
                continue
            # 取不到图一律当"没图"：解析器可能抛 OSError / KeyError / UnityPy 内部异常，
            # 这里没有可区分的处置（缺一张图标不该打断整个预加载）。
            try:
                payload = self._resolver.resolve(family, key)
            except Exception:  # noqa: BLE001
                payload = None
            if payload is not None:
                # 落盘：**预加载的价值就在这里** —— 磁盘就绪之后
                # pixmap() 是同步读盘，界面一秒都不用等，也不用把 1500 张
                # 全塞进内存（立绘一张就 1.8 MB）。
                # 落盘失败不影响本次显示（缓存只是加速，不是前提）。
                try:
                    target = cache_target(self._disk / family, key, payload)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(payload)
                except OSError:
                    pass
            self._out.put((family, key, payload, bulk))


class IconCache(QObject):
    """按 (族, 地址键) 取 QPixmap。取不到就返回 None，由调用方决定画什么。

    ❗ **不许抛异常**：一台没装游戏（或没装 UnityPy）的机器上，
    界面必须照常能用，只是回落成程序化剪影。
    """

    #: 有新的图标到位了（已合并，不是每张一次）。
    #: 订阅者收到后重画自己——但只该在"自己那张还没拿到"时才重画。
    loaded = Signal()

    #: 预加载进度（已完成, 总数）。0 总数表示没有预加载在跑。
    progress = Signal(int, int)

    #: 预取**跑完了**（已完成追平总数）。状态栏据此把进度条收起来。
    #: 与 progress 分开是因为"最后一格"和"结束了"是两件事：
    #: 进度条要停在 100%，而 ETA 文字要立刻消失。
    prefetch_finished = Signal()

    def __init__(
        self,
        root: Path | None = None,
        *,
        game_root: Path | None = None,
        runtime_cache: Path | None = None,
        resolver: IconResolver | None = None,
        autostart: bool = True,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._root = root if root is not None else find_icon_cache()
        self._runtime = runtime_cache if runtime_cache is not None else find_runtime_cache()
        self._files: dict[str, dict[str, str]] = {}
        #: (族, 键) -> QPixmap。**有序**：超预算时从头丢（LRU）。
        self._pixmaps: OrderedDict[tuple[str, str], QPixmap] = OrderedDict()
        self._memory_bytes = 0
        self._pending: set[tuple[str, str]] = set()
        #: 已经**插过队**的键。没有它就会洪水：卡片每重绘一次就再排一次，
        #: 优先队列被同一批键灌满，预加载永远轮不上（实测预取卡在 1/1519）。
        self._promoted: set[tuple[str, str]] = set()
        self._failed: set[tuple[str, str]] = set()
        self._disk_hits: set[tuple[str, str]] = set()
        #: 预加载：总数与已完成数（含失败的），只为显示进度。
        self._preload_total = 0
        self._preload_done = 0
        #: 预取清单里**还没到**的键。进度按「这张图到没到」算，不按「是不是批量任务」算
        #: —— 界面上要用的那张会走优先队列先被取走，它同样是清单里的一张。
        self._prefetch_keys: set[tuple[str, str]] = set()
        #: 速率估计用的采样：(时刻, 已完成数)，窗口 _RATE_WINDOW 秒。
        self._samples: deque[tuple[float, int]] = deque()
        self._rate = 0.0
        self._finished_emitted = True
        self._load_manifest()

        if resolver is not None:
            self._resolver: IconResolver | None = resolver
        else:
            detected = game_root if game_root is not None else find_game_root()
            self._resolver = IconResolver(detected) if detected is not None else None

        self._jobs: queue.Queue = queue.Queue()  # 界面上要用的，优先
        self._bulk: queue.Queue = queue.Queue()  # 预加载的，让路
        self._out: queue.Queue = queue.Queue()
        self._thread: _Loader | None = None
        self._timer: QTimer | None = None
        self._pause_timer: QTimer | None = None
        if autostart and self._resolver is not None and self._resolver.available:
            self._thread = _Loader(self._resolver, self._jobs, self._bulk, self._out, self._runtime)
            self._thread.start()
            self._timer = QTimer(self)
            self._timer.setInterval(_COALESCE_MS)
            self._timer.timeout.connect(self._drain)
            self._timer.start()

    # ------------------------------------------------------------------ #
    # 只读
    # ------------------------------------------------------------------ #

    def _load_manifest(self) -> None:
        if self._root is None:
            return
        manifest = self._root / "_manifest.json"
        if not manifest.is_file():
            return
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        for family, entry in (payload.get("families") or {}).items():
            files = entry.get("files") if isinstance(entry, dict) else None
            if files:
                self._files[str(family)] = dict(files)

    @property
    def root(self) -> Path | None:
        """旧的预提取缓存目录（可能是 None）。"""
        return self._root

    @property
    def runtime_cache(self) -> Path:
        return self._runtime

    @property
    def source(self) -> str:
        """图标当前从哪来（状态栏用）。"""
        if self._resolver is not None and self._resolver.available:
            return "bundle"
        if self._files:
            return "cache"
        return "none"

    @property
    def game_root(self) -> Path | None:
        return self._resolver.game_root if self._resolver is not None else None

    @property
    def available(self) -> bool:
        """有没有任何可用的图标来源。"""
        return self.source != "none"

    def families(self) -> tuple[str, ...]:
        return tuple(sorted(set(self._files) | {f for f, _ in self._pixmaps}))

    def has(self, family: str, key: str) -> bool:
        """这个键**解析得出来**吗（可能是刚入队、还没解析完）。"""
        if key in self._files.get(family, {}):
            return True
        if (family, key) in self._pixmaps:
            return True
        if self._resolver is not None and self._resolver.available:
            return self._resolver.describe(key) is not None
        return False

    def family_files(self, family: str) -> dict[str, str]:
        """旧缓存里某一族的 {原始键: 相对路径}。给测试与统计用。"""
        return dict(self._files.get(family, {}))

    # ------------------------------------------------------------------ #
    # 取图
    # ------------------------------------------------------------------ #

    def pixmap(self, family: str, key: str) -> QPixmap | None:
        """取图标。**不阻塞**：没有就入队，返回 None，好了发 loaded。

        预加载把图灌进磁盘缓存之后，这里就是**同步读盘**——界面一秒都不用等。
        """
        if not key:
            return None
        cache_key = (family, key)
        cached = self._pixmaps.get(cache_key)
        if cached is not None:
            self._pixmaps.move_to_end(cache_key)
            return cached

        result = self._from_disk(cache_key)
        if result is not None:
            self._remember(cache_key, result)
        elif cache_key not in self._failed:
            self._request(family, key)
        return result

    def _remember(self, cache_key: tuple[str, str], pixmap: QPixmap) -> None:
        """放进内存；超预算就从最久没用的开始丢。

        丢掉的只是**内存副本**，磁盘缓存还在，下次读回来即可。
        """
        self._pixmaps[cache_key] = pixmap
        self._pixmaps.move_to_end(cache_key)
        self._memory_bytes += pixmap.width() * pixmap.height() * 4
        while self._memory_bytes > _MEMORY_BUDGET and len(self._pixmaps) > 1:
            _, victim = self._pixmaps.popitem(last=False)
            self._memory_bytes -= victim.width() * victim.height() * 4
        if self._memory_bytes < 0:  # 估算漂了也不该变成负数
            self._memory_bytes = 0

    def _from_disk(self, cache_key: tuple[str, str]) -> QPixmap | None:
        """内存之外的两级磁盘来源。"""
        family, key = cache_key
        # 1) 运行时缓存：**有游戏时优先**，因为它是按权威地址解析出来的
        if self._resolver is not None and self._resolver.available:
            path = cache_file(self._runtime / family, key)  # 没有就是 None
            if cache_key not in self._disk_hits and path is not None:
                pixmap = QPixmap(str(path))
                if not pixmap.isNull():
                    self._disk_hits.add(cache_key)
                    return pixmap
            return None
        # 2) 旧缓存：只在**没装游戏**时用（装了游戏就该走权威地址，
        #    否则那 151 张 outline 空心图会把正确的盖回去）
        relative = self._files.get(family, {}).get(key)
        if relative and self._root is not None:
            path = self._root / relative
            if path.is_file():
                pixmap = QPixmap(str(path))
                return pixmap if not pixmap.isNull() else None
        return None

    def _request(self, family: str, key: str) -> None:
        """界面要用的图标：进**优先**队列（同一个键只插一次队）。

        ❗ 已经在预加载队列里要**再排一次**（提升优先级）——第一版在这里 `return` 了，
        于是"预取正跑着，用户滚到的那张排在 1500 张后面"（实测第 29 位）。
        但也不能每帧都排：卡片重绘一次就排一次的话，优先队列会被同一批键灌满，
        预加载直接饿死（实测预取卡在 1/1519）。所以用 _promoted 去重。
        """
        if self._thread is None:
            return
        cache_key = (family, key)
        if cache_key in self._promoted:
            return
        self._promoted.add(cache_key)
        self._pending.add(cache_key)
        self._jobs.put((family, key))

    def prefetch(self, family: str, keys) -> None:
        """把一批键排进**优先**队列（界面上屏前预取用）。"""
        for key in keys:
            if key:
                self._request(family, str(key))

    @property
    def prefetch_total(self) -> int:
        """本次会话排进预加载的总数（0 = 缓存已就绪，没什么要取的）。"""
        return self._preload_total

    @property
    def prefetch_done(self) -> int:
        return self._preload_done

    @property
    def prefetch_active(self) -> bool:
        """还有预取没跑完（界面据此显示/收起进度条）。"""
        return self._preload_total > 0 and self._preload_done < self._preload_total

    @property
    def prefetch_current(self) -> tuple[str, str] | None:
        """后台线程**正在解析**的 (族, 键)；闲着时 None。"""
        return self._thread.current if self._thread is not None else None

    @property
    def prefetch_current_label(self) -> str:
        """正在解析的那张属于哪一族（中文名，直接显示）。"""
        current = self.prefetch_current
        if current is None:
            return ""
        definition = family_of(current[0])
        return definition.label if definition is not None else current[0]

    @property
    def prefetch_rate(self) -> float:
        """最近 _RATE_WINDOW 秒的平均速率（张/秒）；样本不够是 0。"""
        return self._rate

    @property
    def prefetch_eta(self) -> float | None:
        """还要几秒（估算）。估不出来时返回 None —— 宁可不说，也别瞎说。

        两种"估不出来"：
        1. 速率还是 0（样本没铺开）；
        2. **进度还太浅**（不到 5%）。刚开始那几秒全在开包、没有任何产出，
           这时候按窗口速率外推会离谱地长——【实测】23/1519 时算出"剩约 3 分 11 秒"，
           实际 19 秒就跑完了。界面这时显示"正在估算剩余时间…"。
        """
        remaining = self._preload_total - self._preload_done
        if remaining <= 0 or self._rate <= 0.02:
            return None
        if self._preload_done * 20 < self._preload_total:
            return None
        return remaining / self._rate

    def pause_preload(self, milliseconds: int = 250) -> None:
        """用户正在操作（拖滑条/滚轮）时，让预加载先歇一会儿。

        `milliseconds` 之后自动恢复 —— 调用方只管每次交互时喊一声。
        """
        if self._thread is None:
            return
        self._thread.paused.set()
        if self._pause_timer is None:
            self._pause_timer = QTimer(self)
            self._pause_timer.setSingleShot(True)
            self._pause_timer.timeout.connect(self._resume_preload)
        self._pause_timer.start(max(50, milliseconds))

    def _resume_preload(self) -> None:
        if self._thread is not None:
            self._thread.paused.clear()

    def preload_all(self, grouped: Mapping[str, Iterable[str]]) -> int:
        """把全量图标排进后台队列，**一次把图都取好**。返回排进去的数量。

        ❗ 预加载只灌**磁盘缓存**（不占内存）：磁盘就绪之后 pixmap() 是同步读盘，
        界面不再等；而且立绘一张 1.8 MB，1500 张全塞内存是 780 MB。

        已经在磁盘缓存里的直接跳过——第二次启动这里基本是空转（1522 次 stat，约 10ms）。
        走的是**低优先级队列**：用户滚到的卡永远排在预加载前面。
        """
        if self._thread is None:
            return 0
        queued = 0
        for family, key in self._plan(grouped):
            if not key:
                continue
            cache_key = (family, key)
            if cache_key in self._pending or cache_key in self._failed:
                continue
            if self._cached_on_disk(cache_key):
                continue
            self._pending.add(cache_key)
            self._prefetch_keys.add(cache_key)
            self._bulk.put((family, key))
            queued += 1
        if queued:
            self._preload_total += queued
            # 新的一批：速率重新采样，否则 ETA 会拿上一批的速率去估这一批。
            self._samples.clear()
            self._rate = 0.0
            self._finished_emitted = False
        self.progress.emit(self._preload_done, self._preload_total)
        return queued

    def _plan(self, grouped: Mapping[str, Iterable[str]]) -> list[tuple[str, str]]:
        """预取排队顺序：**按提供者包分组**（IconResolver.plan）。

        为什么必须分组（实测，本机 1522 个键、空缓存）：

        | 排队顺序 | 总耗时 | 开包耗时 |
        |---|---|---|
        | 按族 / 数据表行序 | 45.5s | 18.5s（重复开包 263 次） |
        | **按提供者包分组** | **18.6s** | **4.3s**（每个包只开一次） |

        原因是 BundleLibrary 同时只留得住 2 个包（dlc_3 一个就 724 MB），
        而单位缩略图有 88/464 个键不在主包里——按键的原始顺序取，
        就会在主包与 DLC 包之间来回跳，每跳一次 UnityPy.load 一遍大包。

        解析器没有 plan()（测试替身、或没装游戏）时退回原顺序，不是错误。
        """
        naive = [(str(family), str(key)) for family, keys in grouped.items() for key in keys]
        planner = getattr(self._resolver, "plan", None)
        if planner is None:
            return naive
        try:
            planned = planner(grouped)
        except Exception:  # noqa: BLE001 - 排序只是加速；出错也必须能预取
            return naive
        return [(str(family), str(key)) for family, key in planned] or naive

    def _cached_on_disk(self, cache_key: tuple[str, str]) -> bool:
        family, key = cache_key
        if self._resolver is not None and self._resolver.available:
            return cache_file(self._runtime / family, key) is not None
        relative = self._files.get(family, {}).get(key)
        return bool(relative and self._root is not None and (self._root / relative).is_file())

    def invalidate(self, family: str, key: str) -> None:
        """丢掉一张图的缓存并重新解析。

        **映射地址变了只动这一张** —— 不用整批重来。
        （编数据表时改 `ThumbnailFileName` / `PortraitFileName` / `HUDIcon` 就是这条路径。）
        """
        if not key:
            return
        cache_key = (family, key)
        old = self._pixmaps.pop(cache_key, None)
        if old is not None:
            self._memory_bytes -= old.width() * old.height() * 4
        self._pending.discard(cache_key)
        self._promoted.discard(cache_key)
        self._failed.discard(cache_key)
        self._disk_hits.discard(cache_key)
        with contextlib.suppress(OSError):
            # 两种扩展名都要删：换了编码格式之后旧的还在，不删就会读到旧图。
            for suffix in _CACHE_SUFFIXES:
                (self._runtime / family / (sanitize(key) + suffix)).unlink(missing_ok=True)
        self._request(family, key)

    # ------------------------------------------------------------------ #
    # 后台结果回收
    # ------------------------------------------------------------------ #

    def _drain(self) -> None:
        """把后台已经解析好的图标搬进内存（GUI 线程）。

        预加载来的（bulk=True）**不搬内存**：它的价值在于把图写进磁盘缓存，
        界面真要用的时候同步读盘即可。
        """
        arrived = 0
        progressed = 0
        while True:
            try:
                family, key, payload, bulk = self._out.get_nowait()
            except queue.Empty:
                break
            cache_key = (family, key)
            self._pending.discard(cache_key)
            self._promoted.discard(cache_key)
            # ❗ 进度按「这张图**到没到**」算，不按「是不是批量任务」算。
            # 实踩：只在 bulk 分支 +1 时，窗口一开卡片就通过优先队列把可见的几十张
            # 要走（正是该先给的），批量任务还在后面排队，于是进度条长时间停在 0/1519
            # ——用户报的"看不到进度条"就是这么来的。
            if cache_key in self._prefetch_keys:
                self._prefetch_keys.discard(cache_key)
                progressed += 1
            if payload is None:
                self._failed.add(cache_key)
                continue
            if bulk:
                continue
            pixmap = QPixmap()
            if not pixmap.loadFromData(payload, "PNG"):
                self._failed.add(cache_key)
                continue
            self._remember(cache_key, pixmap)
            arrived += 1
        if progressed:
            self._preload_done += progressed
        self._sample_rate()
        # 没进展也要发：ETA 和"正在取哪一族"要一直刷新，
        # 不然开大包的那两秒界面完全静止，看着像卡死（12.5 次/秒，代价可忽略）。
        if self._preload_total > 0 and (progressed or self.prefetch_active):
            self.progress.emit(self._preload_done, self._preload_total)
        if not self.prefetch_active and not self._finished_emitted:
            self._finished_emitted = True
            self._samples.clear()
            self._rate = 0.0
            self.prefetch_finished.emit()
        if arrived:
            self.loaded.emit()

    def _sample_rate(self) -> None:
        """按滑动窗口估速率（张/秒），给 ETA 用。

        为什么用窗口而不是「总数 / 总耗时」：开一个大包要 1~2 秒没有任何产出，
        总量平均会把 ETA 算成忽长忽短的锯齿；窗口取 6 秒正好把一次开包摊平。
        """
        if not self.prefetch_active:
            return
        now = time.monotonic()
        self._samples.append((now, self._preload_done))
        while len(self._samples) > 2 and now - self._samples[0][0] > _RATE_WINDOW:
            self._samples.popleft()
        start_time, start_done = self._samples[0]
        span = now - start_time
        if span < 0.5:
            self._rate = 0.0  # 窗口还没铺开，先不估（界面显示"正在估算"）
            return
        self._rate = max(0.0, (self._preload_done - start_done) / span)

    def wait_for_idle(self, timeout: float = 5.0) -> bool:
        """等后台队列排空（测试与"另存为"这类同步场景用）。"""
        if self._thread is None:
            return True
        waited = 0.0
        step = 0.02
        while waited < timeout:
            self._drain()
            if not self._pending:
                return True
            time.sleep(step)
            waited += step
        self._drain()
        return not self._pending

    def shutdown(self) -> None:
        """停掉后台线程、放掉已打开的 bundle（窗口关闭 / 测试收尾时调）。**幂等**。

        为什么不 join 不行：一条加载线程 = 一个 resolver = 最多两个已打开的 bundle
        （立绘包 304 MB、dlc_3 724 MB）。只置标志位就返回，线程还会把手上那一张解完，
        而这期间 bundle 一直挂着。实测：连续建 14 个 ArmoryScreen 就有 15 条线程活着。
        """
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.stop()
            thread.join(timeout=2.0)
        if self._resolver is not None:
            self._resolver.close()


# --------------------------------------------------------------------------- #
# 进程内共享的图标缓存
# --------------------------------------------------------------------------- #

#: 共享实例。None = 还没建过。
_SHARED: IconCache | None = None


def shared_icon_cache() -> IconCache:
    """拿进程内唯一那份 IconCache（没有就建一个）。

    为什么共享：**每建一个 IconCache 就起一条加载线程**，而线程里的 resolver 会按住
    最多两个已打开的 bundle。原先每个 ArmoryScreen 各建一个 —— 产品里只有一个军械库屏
    （看不出来），测试里一屏一个，十几条线程 + 几个 GB，最后在 UnityPy 里撞出
    "Windows fatal exception: access violation"（实测踩过，见 _RESOLVE_LOCK 的注释）。
    图标缓存本来就是**进程级**资源（同一份磁盘缓存、同一个游戏目录），共享才是它的正确形态。
    """
    global _SHARED
    if _SHARED is None:
        _SHARED = IconCache()
    return _SHARED


def reset_shared_icon_cache() -> None:
    """关掉并丢掉共享实例（下一个 shared_icon_cache() 会新建）。测试收尾用。"""
    global _SHARED
    if _SHARED is not None:
        _SHARED.shutdown()
        _SHARED = None
