"""按地址从 bundle 里取图。

这一层只管两件事：**把 bundle 的地址表读出来**、**按地址取出一张图**。
地址语义（实心 / outline 空心）在 families.split_address 里，这里不重复判断。
"""

from __future__ import annotations

import io
from pathlib import Path

from ba_mod_maker.core.assets.families import IconFamily
from ba_mod_maker.core.assets.families import family as family_of


class BundleUnavailableError(RuntimeError):
    """UnityPy 没装。**降级用**，不是崩溃。"""


#: 磁盘缓存里 PNG 的压缩级别。
#: 缓存是**运行时生成物**（读回去的是 QPixmap），体积换时间划算：
#: 实测 level 1 比 level 6 快一半（0.87ms -> 0.46ms/张），体积涨 40%
#: （整份缓存 106 MB -> 147 MB，反正写在 %LOCALAPPDATA%）。冷启动省约 3 秒。
PNG_COMPRESS_LEVEL = 1


def unitypy():
    """惰性 import UnityPy。

    为什么惰性：import 要 0.65s，而一台没装游戏（或没装 UnityPy）的机器
    根本用不到它——不能让它拖慢启动。
    """
    try:
        import UnityPy
    except ImportError as exc:  # pragma: no cover - 取决于运行环境
        raise BundleUnavailableError("没有安装 UnityPy，无法从 bundle 读取图标") from exc
    return UnityPy


class BundleLibrary:
    """bundle 的打开与缓存。

    ❗ **已打开的 env 长期持有**：UnityPy.load 是惰性的（304 MB 的立绘包
    0.76s 开索引），但重复开同一个包是纯浪费；取一张纹理只要 0.0~0.1s。

    ⚠️ 不是线程安全的：只由图标加载线程使用（见 app/theme/icons.py）。
    """

    def __init__(self, aa_dir: Path | None, *, max_open: int = 3) -> None:
        self._aa_dir = aa_dir
        self._envs: dict[Path, object] = {}
        #: 已建好的 container（地址表）。见 _container() 的注释：UnityPy 的
        #: Environment.container 是属性，每访问一次就重建一个，必须自己存。
        self._containers: dict[Path, object] = {}
        self._addresses: dict[Path, dict[str, list[str]]] = {}
        self._order: list[Path] = []
        #: 同时最多持有几个已打开的包。
        #: ❗ 必须有上限：DLC 包是 428 MB / 724 MB，全留着会把内存吃光。
        self._max_open = max(1, max_open)

    @property
    def root(self) -> Path | None:
        return self._aa_dir

    @property
    def available(self) -> bool:
        return self._aa_dir is not None and self._aa_dir.is_dir()

    @property
    def opened(self) -> int:
        """已经打开的 bundle 数（诊断用）。"""
        return len(self._envs)

    def bundles_for(self, family: IconFamily, extra: tuple[str, ...] = ()) -> list[Path]:
        """这一族可能住在哪些包里，**主包排最前**。

        补充包来自数据侧的 _external_full.json——实测单位缩略图有 88/464 个键
        不在主包里，只 glob 主包会漏。
        """
        if self._aa_dir is None:
            return []
        ordered: list[Path] = []
        for pattern in (family.bundle_glob, *extra):
            for path in sorted(self._aa_dir.glob(pattern)):
                if path.is_file() and path not in ordered:
                    ordered.append(path)
        return ordered

    def _env(self, bundle: Path):
        env = self._envs.get(bundle)
        if env is None:
            env = unitypy().load(str(bundle))
            self._envs[bundle] = env
            self._evict(bundle)
        if bundle in self._order:
            self._order.remove(bundle)
        self._order.append(bundle)
        return env

    def _evict(self, keep: Path) -> None:
        """超出上限就丢掉最久没用的那个（地址表也一起丢，避免指向已释放的对象）。"""
        while len(self._envs) > self._max_open:
            victim = next((p for p in self._order if p != keep), None)
            if victim is None:
                return
            self._order.remove(victim)
            self._envs.pop(victim, None)
            self._containers.pop(victim, None)
            self._addresses.pop(victim, None)

    def close_all(self) -> None:
        """放掉全部已打开的包。"""
        self._envs.clear()
        self._containers.clear()
        self._addresses.clear()
        self._order.clear()

    def addresses(self, bundle: Path) -> dict[str, list[str]]:
        """bundle 的地址表：叶子名(小写) -> [资产路径, ...]。

        一个叶子可能对应两条路径（Icons/X.png 与 Icons/outline/X.png），
        两条都留着——**选哪条由键决定**，不是由遍历顺序决定。
        """
        cached = self._addresses.get(bundle)
        if cached is not None:
            return cached
        table: dict[str, list[str]] = {}
        container = self._container(bundle)
        if container is None:
            self._addresses[bundle] = table
            return table
        for asset_path in container:
            leaf = asset_path.replace("\\", "/").split("/")[-1]
            if "." in leaf:
                leaf = leaf.rsplit(".", 1)[0]
            table.setdefault(leaf.lower(), []).append(asset_path)
        self._addresses[bundle] = table
        return table

    def _container(self, bundle: Path):
        """拿 bundle 的 container，**建一次就留着**。

        ⚠️ UnityPy 建 container 索引时会惰性加载同组的 cab，
        直接迭代它的 keys() 会撞上 "dictionary changed size during iteration"
        （实测踩过）。所以先取一次逼它建完，再整体物化成 list。

        ❗ **必须自己缓存返回值**：UnityPy 的 Environment.container 是**属性**，
        每访问一次就重新拼一个 ContainerHelper（实测 2140 条的包 0.2ms/次），
        而一次 load_image 里我们要访问三次。1522 个键就是上千次白干。
        """
        cached = self._containers.get(bundle)
        if cached is not None:
            return cached
        env = None
        for _ in range(4):
            try:
                env = self._env(bundle)
                container = env.container  # 触发建立索引（并拿到这个实例）
                list(container.keys())  # 物化，避免迭代中被改
                self._containers[bundle] = container
                return container
            except RuntimeError:
                # UnityPy 建索引时会**惰性加载同组的 cab**，而它自己正在遍历 cabs
                # —— 于是抛 "dictionary changed size during iteration"。
                # 这是它的实现问题，不是数据问题：多试几次，cab 加载完就稳定了。
                # ❗ 不能把 env 丢掉重来，否则每次都要从头解析（实测 dlc_2 要 9s）。
                continue
            except Exception:  # noqa: BLE001 - UnityPy 的实现问题（见上），逐类列举会漏
                break
        # 真的打不开：清掉缓存，别让它一直占着内存
        self._envs.pop(bundle, None)
        self._containers.pop(bundle, None)
        self._order = [p for p in self._order if p != bundle]
        self._addresses.pop(bundle, None)
        return None

    def load_image(self, bundle: Path, asset_path: str) -> bytes | None:
        """按资产路径取一张图，编码成 PNG 或 JPEG 字节。取不到返回 None。

        ❗ **全不透明的图走 JPEG**：立绘（816x550，实测 100% 不透明）
        PNG 编码要 65ms / 600KB，JPEG 只要 1.4ms / 70KB —— 快 47 倍、小 8.5 倍。
        433 张立绘因此省下约 28 秒和 230 MB。
        有真透明的图（缩略图剪影、国旗、专精图标）必须 PNG，否则边缘会糊。
        """
        container = self._container(bundle)
        if container is None:
            return None
        # ⚠️ UnityPy 的 ContainerHelper 不是 dict：它有 __getitem__/__contains__，
        # 但没有 .get()（实测踩过）。
        if asset_path not in container:
            return None
        pptr = container[asset_path]
        try:
            image = pptr.read().image
        except Exception:  # noqa: BLE001 - 解不出图就当没有（UnityPy 抛什么都有，缺图不该中断）
            return None
        if image is None:
            return None
        image = image.convert("RGBA")
        buffer = io.BytesIO()
        if image.getchannel("A").getextrema()[0] >= 250:
            # 全不透明：没有 alpha 要保，JPEG 又快又小。
            image.convert("RGB").save(buffer, format="JPEG", quality=90)
        else:
            image.save(buffer, format="PNG", compress_level=PNG_COMPRESS_LEVEL)
        return buffer.getvalue()


__all__ = ["BundleLibrary", "BundleUnavailableError", "family_of", "unitypy"]
