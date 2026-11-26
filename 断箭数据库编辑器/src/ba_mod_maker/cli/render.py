r"""`render` 子命令：**一条命令**把一个包在 Unity 里渲出来（渲染闸）。

## 它做什么

    ba-mod-maker render <bundle>              # HDRP 工程 · 真材质（游戏里的观感）
    ba-mod-maker render <bundle> --builtin    # Built-in 工程 · 材质换 Standard（只看 UV/贴图）

    包 -> 对应的 Unity 工程 -> **一趟** batchmode 会话（-executeMethod）
       -> 读数（SRP / 材质 shader / 渲染器数）+ 每个视图一张 PNG
       -> 按**产物 mtime** 判这一轮到底跑没跑

前身是根目录 `tools/unity_render.py`（2026-10-11 收进产品）；那个文件现在是本模块的薄封装。

## 为什么按 mtime 判、不认退出码

Unity batchmode 的退出码**不可靠**（实测 `EditorApplication.Exit(0)` 也会回 1、有时回空，
kb/14 §6）—— 所以「跑没跑成」只看**读数文件有没有变新**、**有没有新 PNG**。
另一半原因是 Unity 是**单实例**：那个工程被编辑器开着时，命令行会**静默交接**给已有实例 ——
这一轮表面上退得干干净净，产物一个没动。所以开工前先探锁文件（见退出码 2）。

## 判据（每条都能红）

| 判据 | 红的样子 | 退出码 |
|---|---|---|
| 读数文件 mtime **变新** | 没变 = 这一轮白跑（多半是工程被编辑器占着） | 1 |
| 至少一张视图 PNG 是**新的** | 读数变了却一张新图都没有 | 1 |
| 读数里读得出「渲染器 N 个」与「视图 … -> ….png」 | 读不出 = 读数形状不对 | 1 |
| 工程**没被**编辑器占着 | `Temp\UnityLockfile` 在 ⇒ 拒绝起（`--force` 可跳过） | 2 |

## 退出码

| 码 | 什么时候 |
|---|---|
| 0 | 读数变新 + 出了新图 + 关键读数读得出 |
| 1 | **有红**：读数没变 ／ 没有新 PNG ／ 读数读不出关键行 ／ 超时 |
| 2 | 用不了：包不在 ／ 工程不在 ／ 找不到 Unity.exe ／ 那个工程的编辑器还开着 |

## ⛔ 它不做什么

* **只读**包、只往 `--out` 里写产物：不写游戏目录、不碰数据库、不改任何包；
* 不自己拼 Unity 命令行 —— 工程名 / 方法名 / 前缀钉在 `MODES` 里；
* **不判「像不像游戏」** —— 那是**用户的眼睛**（AGENTS §0.12.1）。本命令只保证「这一轮真出图了」。
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ba_mod_maker.cli.emit import Emitter
from ba_mod_maker.core.assets import game as game_mod
from ba_mod_maker.core.errors import AssetError, BaModMakerError

#: 退出码。与 `finder.py` / `vt.py` / `vt_bake.py` 同一套语义（0 完成 / 1 有红 / 2 用不了）。
EXIT_OK = 0
EXIT_RED = 1
EXIT_UNUSABLE = 2

#: 仓根（`<工作区>\BA_Mod_Maker_2.0`）与工作区根（仓的上一级）。
REPO = Path(__file__).resolve().parents[3]
WORKSPACE = REPO.parent

#: 一档 = (工程**相对工作区根**的路径, 编辑器方法, 读数/PNG 前缀)。
#: ⚠ 2026-10-17 换代（用户原话「渲染尺子就用新代，抛弃旧代」—— kb/42 §4 C11）：
#: 两档都指 **Unity 6000.3.24f1** 的工程（hdrp = HDRP 17.3.0 / builtin = Built-in），
#: 它们落在**仓内** `_work\` 下 ⇒ 值是**多段相对路径**，不是单个工程名。
#: 旧三件 `unity-hdrp` / `unity-armory` / `unity-probe-builtin`（2022.3 ＋ HDRP 14）
#: 降级为**历史对拍件**：⛔ 不再当尺子，也不删（删产物要单独问用户）。
MODES: dict[str, tuple[str, str, str]] = {
    # ⚠ 2026-11-01：仓目录名**不许写死**（用户 2026-10-30 改成 `BA_Mod_Maker_2.0（数据侧）`）⇒
    #   之前写死的 `BA_Mod_Maker_2.0/_work/hdrp17` 让渲染闸报「Unity 工程不像工程」直接跑不起来。
    #   按 `REPO.name` 现拼（与 `tools/unity_render.py::_repo_src` 同一口径：按前缀找仓）。
    "hdrp": (f"{REPO.name}/_work/hdrp17", "RouteARenderHDRP.Run", "hdrp-render"),
    "builtin": (f"{REPO.name}/_work/u6probe", "RouteARender.Run", "unity-render"),
}

#: 人读的档名。
MODE_LABELS = {
    "hdrp": "HDRP 工程 · 真材质（游戏里的观感）",
    "builtin": "Built-in 工程 · 材质换 Standard（只看 UV/贴图）",
}

#: 工程被编辑器占着的标志。
LOCK_REL = Path("Temp") / "UnityLockfile"

#: 读数里要抬出来的几行 —— 两种读数格式（HDRP / Built-in）的**并集**，
#: 形状见 `tests/test_cli_render.py` 里那两份**真读数**。
_SRP_RE = re.compile(r"^SRP\s*[:：]\s*(.+?)\s*$", re.MULTILINE)
_SHADER_RE = re.compile(r"^[ \t]*着色器\s*[:：]\s*(.+?)\s*$", re.MULTILINE)
_RENDERERS_RE = re.compile(r"渲染器\s*(\d+)\s*个")
_VIEW_RE = re.compile(r"^视图\s+(\S+)\s*->\s*(\S+\.png)", re.MULTILINE)
#: Built-in 那一档没有「着色器:」行，shader 名写在每条材质后面（例：-> Standard · 贴图 …）。
_MATERIAL_RE = re.compile(r"^[ \t]*材质\s+(.+?)\s*->\s*([^\s·]+)", re.MULTILINE)

_DESCRIPTION = r"""渲染闸：把一个包在 Unity 里渲出来，并印出 SRP / 材质 shader / 渲染器数。

一条命令出图（默认 HDRP 工程 · 真材质）。
两档都跑 **Unity 6000.3.24f1** 的工程（与游戏 1.2.1 同代；kb/42 §4 C11）：
      --hdrp（默认）用 _work\hdrp17（HDRP 17.3.0）、**保留真材质** ⇒ 游戏里的观感；
      --builtin 用 _work\u6probe（Built-in）、材质换 Standard 并绑原贴图 ⇒ 只看 UV/贴图映射。

判据（⛔ 不认 batchmode 的退出码，kb/14 §6）：
  读数文件 mtime **变新** + 出了**新** PNG + 读数里读得出「渲染器 N 个」与「视图 … -> ….png」。
  读数没变 = 这一轮白跑（工程被编辑器占着时 Unity 会**静默交接**）。

退出码：
  0  读数变新，出了新图，关键读数读得出
  1  有红：读数没变 ／ 没有新 PNG ／ 读数读不出关键行 ／ 超时
  2  用不了：包不在 ／ 工程不在 ／ 找不到 Unity.exe ／ 那个工程的编辑器还开着
"""

_EPILOG = r"""产物：<out>\<前缀>.txt（读数）+ <out>\<前缀>-<视图>.png（每个视图一张）
     + <out>\<前缀>.log（Unity 自己的日志）
默认产物目录 = <包所在目录>\render。

例：
  # 一条命令出图（HDRP，真材质）
  ba-mod-maker render _work\g7b\out\g7b_assets_all_84525add423f1efb.bundle

  # 看 UV/花纹对不对（Built-in，材质换 Standard）
  ba-mod-maker render <bundle> --builtin --out _work\uv-check

  # 报红的形状（负控）：包不在 ⇒ 退出码 2 + stderr 一句人话，⛔ 不许静默出一张空图
  ba-mod-maker render _work\没有这个包.bundle

⛔ 跑之前先关掉那个工程的编辑器（Unity 单实例会静默交接 ⇒ 白跑）。
   本命令会先探 <工程>\Temp\UnityLockfile，探到就**拒绝起**（退出码 2）；确实要跑加 --force。
"""


@dataclass(frozen=True)
class Readings:
    """编辑器写的那份读数里，我们抬出来的几个字段。读不出的留 `None`（⛔ 不拿 0 / 空串冒充）。"""

    srp: str | None = None
    shader: str | None = None
    renderers: int | None = None
    views: tuple[tuple[str, str], ...] = ()
    raw: str = ""


@dataclass(frozen=True)
class RenderPlan:
    """一趟渲染的全部坐标 —— `run` 只按它办事，判据全落在 `readings` / `out_dir` 上。"""

    bundle: Path
    project: Path
    unity: Path
    mode: str
    method: str
    prefix: str
    out_dir: Path
    readings: Path
    log: Path
    argv: tuple[str, ...]
    before_mtime: float
    editor_note: str
    timeout: float


# --------------------------------------------------------------------------- #
# 参数
# --------------------------------------------------------------------------- #


def _build(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    """`render` 的全部参数 —— 子命令与根 `tools/unity_render.py` 的独立入口**共用这一份**。"""
    parser.add_argument("bundle", help="要渲的 .bundle")
    parser.add_argument("--hdrp", action="store_true", help="用 HDRP 工程渲真材质（默认）")
    parser.add_argument(
        "--builtin", action="store_true", help="用 Built-in 工程渲 UV/贴图（材质换 Standard）"
    )
    parser.add_argument(
        "--look",
        action="store_true",
        help="材质换成工程的 HDRP/Lit 并绑上贴图（车体主材质是 ShaderGraph＋Granite VT，"
        "工程里没有 VT 全局 ⇒ 默认渲出来是黑的；这一档看的是**真贴图 ＋ 工程光照**）",
    )
    parser.add_argument(
        "--skin",
        default=None,
        metavar="ID",
        help="套上 `UnitPrefabRoot.Skins` 里的那条运行期覆盖（给 Id，或 `first` 用第一条）；"
        "常与 --look 一起用，渲染图才接近游戏里的涂装",
    )
    parser.add_argument(
        "--skin-material",
        dest="skin_material",
        default=None,
        help="涂装变体材质名（例 `RU_T72B 5`）；要配 --extra-bundle 指到它所在的包",
    )
    parser.add_argument(
        "--extra-bundle",
        dest="extra_bundle",
        default=None,
        help="额外加载一个包（涂装材质常在 units 包里，3.4 GB —— 加载约 1 分钟）",
    )
    parser.add_argument("--out", default=None, help=r"产物目录（默认 <包所在目录>\render）")
    parser.add_argument(
        "--project", default=None, help=r"Unity 工程根（默认 <工作区>\<那一档的工程>）"
    )
    parser.add_argument(
        "--unity", default=None, help="Unity.exe（默认与 asset_kernel/vt_bake.py 同一个）"
    )
    parser.add_argument(
        "--timeout", type=float, default=1800.0, help="一趟 Unity 的硬超时秒（默认 1800）"
    )
    parser.add_argument(
        "--force", action="store_true", help="那个工程的编辑器开着时也照跑（⛔ 多半白跑）"
    )
    parser.add_argument(
        "--json",
        dest="json_out",
        action="store_true",
        help="只输出一个 JSON 文档（人读那份改走 stderr）",
    )
    return parser


def add_parser(sub: Any) -> argparse.ArgumentParser:
    """注册 `render` 子命令（`__main__.py` 只调这一句）。"""
    parser = sub.add_parser(
        "render",
        help="渲染闸：把一个包在 Unity 里渲出来，并印出 SRP / shader / 渲染器数"
        "（退出码 0 出图 / 1 有红 / 2 用不了）",
        description=_DESCRIPTION,
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    return _build(parser)


def main(argv: list[str] | None = None) -> int:
    """独立入口 —— 根 `tools/unity_render.py` 那个薄封装调的就是它。

    ⛔ 它不是第二个实现：参数表与 `run` 都是上面那两份（AGENTS §5「逻辑只有一份」）。
    """
    from ba_mod_maker.cli.emit import force_utf8_streams

    #: `--help` 不走 Emitter，而帮助文本里有 ⛔/⇒ ⇒ 先在 argparse 之前把标准流掰成 UTF-8
    #: （与 `__main__.py:969` 同一个动作、同一份实现）。
    force_utf8_streams()
    parser = argparse.ArgumentParser(
        prog="unity_render.py",
        description=_DESCRIPTION,
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _build(parser)
    return run(parser.parse_args(argv))


# --------------------------------------------------------------------------- #
# 读数
# --------------------------------------------------------------------------- #


def _first(pattern: re.Pattern[str], text: str) -> str | None:
    """第一个命中的第 1 组；没命中给 `None`。"""
    found = pattern.search(text)
    return found.group(1).strip() if found is not None else None


def parse_readings(text: str) -> Readings:
    """把读数里我们关心的几行抬出来。

    `shader` 有两处来源：HDRP 那份有独立的「着色器:」行；Built-in 那份没有，
    shader 名写在每条材质后面（`材质 <名> -> Standard · 贴图 …`）—— 这时按**去重后的 shader 名**拼。
    """
    srp = _first(_SRP_RE, text)
    shader = _first(_SHADER_RE, text)
    materials = _MATERIAL_RE.findall(text)
    if shader is None and materials:
        joined = "、".join(sorted({name for _, name in materials}))
        shader = f"{joined}（{len(materials)} 个材质槽）"
    renderers = None
    found = _RENDERERS_RE.search(text)
    if found is not None:
        renderers = int(found.group(1))
    return Readings(
        srp=srp,
        shader=shader,
        renderers=renderers,
        views=tuple(_VIEW_RE.findall(text)),
        raw=text,
    )


def _read_text(path: Path) -> str:
    """读编辑器写的读数；读不开当作没有（读数不在 = 白跑，那本来就是要报的红）。"""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _stamp(mtime: float) -> str:
    """mtime 的人读样子（0.0 = 之前没有这个文件）。"""
    if not mtime:
        return ""
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(mtime))


# --------------------------------------------------------------------------- #
# 坐标
# --------------------------------------------------------------------------- #


def _default_unity() -> Path:
    """本机默认的 Unity 编辑器 —— 与 `asset_kernel/vt_bake.py::DEFAULT_UNITY` **同源**。

    延迟 import：`--help` 与别的子命令不该为一个 VT 模块付代价
    （`pyproject.toml` 给 `src/ba_mod_maker/cli/**` 免掉 PLC0415 的就是这个理由）。
    """
    try:
        from ba_mod_maker.asset_kernel.vt_bake import DEFAULT_UNITY
    except ImportError as exc:
        raise AssetError(
            f"读不到默认的 Unity.exe（{exc}）",
            hint=r"用 --unity 直接指到编辑器，例：--unity D:\Unity\6000.3.24f1\Editor\Unity.exe",
        ) from exc
    return Path(DEFAULT_UNITY)


def _unity_processes() -> int | None:
    """本机 Unity.exe 进程数。⛔ 探不到给 `None`，不许把「探不到」说成 0。"""
    if sys.platform != "win32":
        return None
    try:
        done = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq Unity.exe", "/NH"],
            capture_output=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    # ⛔ 不许 text=True：中文 Windows 的 tasklist 按 OEM 码页输出，UTF-8 解会崩（见
    # core/assets/game.py::decode_console_output 那段注释与 tests/test_game_running.py）。
    out = game_mod.decode_console_output(done.stdout or b"")
    return sum(1 for line in out.splitlines() if "Unity.exe" in line)


def _editor_probe(project: Path) -> tuple[bool, str]:
    r"""「那个工程的编辑器开着没」—— 返回 (看着开着, 给用户看的一句话)。

    主信号是**那个工程自己的** `Temp\UnityLockfile`（Unity 单实例是按工程交接的）；
    本机 Unity 进程数只当旁证 —— 别的工程开着 Unity 是正常事，不当依据。
    """
    locked = (project / LOCK_REL).exists()
    running = _unity_processes()
    tail = "本机 Unity 进程数探不到" if running is None else f"本机 Unity 进程 {running} 个"
    return locked, f"{project.name}\\{LOCK_REL} {'在' if locked else '不在'} · {tail}"


def plan_render(
    *,
    bundle: str | Path,
    mode: str = "hdrp",
    out_dir: str | Path | None = None,
    project: str | Path | None = None,
    unity: str | Path | None = None,
    force: bool = False,
    timeout: float = 1800.0,
    look: bool = False,
    skin: str | None = None,
    skin_material: str | None = None,
    extra_bundle: str | Path | None = None,
) -> RenderPlan:
    """把坐标验一遍、把命令行拼好。⛔ 除了 `mkdir` 产物目录，一个字节都不写。

    验不过就 `AssetError` —— `run` 把它变成**退出码 2 + stderr 一句人话**（负控就在这条路上）。
    """
    if mode not in MODES:
        raise AssetError(f"不认识的档：{mode!r}", hint="只能是 hdrp 或 builtin")
    project_name, method, prefix = MODES[mode]

    bundle_path = Path(bundle).expanduser()
    if not bundle_path.exists():
        raise AssetError(
            f"包不在：{bundle_path}",
            hint="给一个真实存在的 .bundle；⛔ 包不在时**不许**静默出一张空图，也不许退出码 0",
        )
    if bundle_path.is_dir():
        raise AssetError(f"那是个目录，不是包：{bundle_path}", hint="给 .bundle 文件本身")
    bundle_path = bundle_path.resolve()

    unity_path = Path(unity).expanduser() if unity else _default_unity()
    if not unity_path.is_file():
        raise AssetError(
            f"找不到 Unity.exe：{unity_path}",
            hint=r"用 --unity 指到编辑器；本机是 D:\Unity\6000.3.24f1\Editor\Unity.exe",
        )

    project_path = Path(project).expanduser() if project else WORKSPACE / project_name
    if not (project_path / "ProjectSettings").is_dir():
        raise AssetError(
            f"Unity 工程不像工程：{project_path}",
            hint=r"要含 ProjectSettings\ 与 Assets\；"
            f"{MODE_LABELS[mode]} 用的是 {project_name}（相对工作区根；kb/14 §6）",
        )
    project_path = project_path.resolve()

    editor_open, editor_note = _editor_probe(project_path)
    if editor_open and not force:
        raise AssetError(
            f"{project_name} 的编辑器还开着（{editor_note}）",
            hint="先关掉那个工程的 Unity 窗口 —— 单实例会把这一轮**静默交接**过去（白跑）；"
            "确实要跑就加 --force",
        )
    if editor_open:
        editor_note += "（--force：照样起了）"

    out_path = (Path(out_dir).expanduser() if out_dir else bundle_path.parent / "render").resolve()
    out_path.mkdir(parents=True, exist_ok=True)
    readings = out_path / f"{prefix}.txt"
    before = readings.stat().st_mtime if readings.exists() else 0.0
    argv = (
        str(unity_path),
        "-batchmode",
        "-quit",
        "-projectPath",
        str(project_path),
        "-executeMethod",
        method,
        "-routeBundle",
        str(bundle_path),
        "-routeOut",
        str(out_path),
        "-logFile",
        str(out_path / f"{prefix}.log"),
    ) + (("-routeLook", "1") if look else ()) + (("-routeSkin", str(skin)) if skin else ())
    if skin_material and extra_bundle:
        argv = argv + ("-routeExtraBundle", str(extra_bundle), "-routeSkinMat", str(skin_material))
    return RenderPlan(
        bundle=bundle_path,
        project=project_path,
        unity=unity_path,
        mode=mode,
        method=method,
        prefix=prefix,
        out_dir=out_path,
        readings=readings,
        log=out_path / f"{prefix}.log",
        argv=argv,
        before_mtime=before,
        editor_note=editor_note,
        timeout=float(timeout),
    )


def _invoke(plan: RenderPlan) -> tuple[int | None, bool]:
    """真起一趟 Unity。返回 (退出码, 有没有超时)。

    ⛔ 退出码**不可靠**（kb/14 §6）—— 它只写进 JSON 当线索，**判据是产物 mtime**。
    用例把这个函数换掉，就能把整条 `run` 跑到而不真起编辑器。
    """
    try:
        done = subprocess.run(list(plan.argv), check=False, timeout=plan.timeout)
    except subprocess.TimeoutExpired:
        return None, True
    return done.returncode, False


# --------------------------------------------------------------------------- #
# 跑
# --------------------------------------------------------------------------- #


def run(args: argparse.Namespace) -> int:
    """跑一遍。返回退出码（0 / 1 / 2）。"""
    emitter = Emitter(json_mode=bool(getattr(args, "json_out", False)))
    #: 两个开关同给时 HDRP 优先 —— 与前身 unity_render.py 的 --builtin 同口径。
    mode = "builtin" if (args.builtin and not args.hdrp) else "hdrp"
    try:
        plan = plan_render(
            bundle=args.bundle,
            mode=mode,
            out_dir=args.out,
            project=args.project,
            unity=args.unity,
            force=bool(getattr(args, "force", False)),
            timeout=float(getattr(args, "timeout", 1800.0)),
            look=bool(getattr(args, "look", False)),
            skin=getattr(args, "skin", None),
            skin_material=getattr(args, "skin_material", None),
            extra_bundle=getattr(args, "extra_bundle", None),
        )
    except BaModMakerError as exc:
        emitter.failure(exc.message, hint=exc.hint)
        return EXIT_UNUSABLE

    emitter.field("包", plan.bundle)
    emitter.field("档", MODE_LABELS[plan.mode])
    emitter.field("工程", plan.project)
    emitter.field("编辑器", plan.unity)
    emitter.field("产物目录", plan.out_dir)
    emitter.field("编辑器探测", plan.editor_note)
    emitter.line("跑：" + " ".join(plan.argv[:6]) + " …")

    started = time.time()
    rc, timed_out = _invoke(plan)
    took = time.time() - started

    fresh = plan.readings.exists() and plan.readings.stat().st_mtime > plan.before_mtime
    raw = _read_text(plan.readings) if plan.readings.exists() else ""
    readings = parse_readings(raw) if raw else None
    pngs = tuple(sorted(plan.out_dir.glob(plan.prefix + "-*.png")))
    new_pngs = tuple(path for path in pngs if path.stat().st_mtime > plan.before_mtime)

    problems: list[str] = []
    if timed_out:
        problems.append(f"超时（>{plan.timeout:.0f} 秒）—— 已经把它杀了，去看 {plan.log.name}")
    if not fresh:
        problems.append("读数文件没变新 —— 这一轮没真跑（多半是工程被编辑器占着；kb/14 §6）")
    elif not new_pngs:
        problems.append("读数变了但一张新 PNG 都没有 —— 没出图")
    if fresh and readings is not None:
        if readings.renderers is None:
            problems.append("读数里读不出「渲染器 N 个」")
        if not readings.views:
            problems.append("读数里没有「视图 … -> ….png」行")

    emitter.blank()
    was = _stamp(plan.before_mtime)
    emitter.line(
        f"用时 {took:.0f} 秒 · 读数文件{'已更新 ✔' if fresh else '**没更新 ✗**'}"
        + (f"（上一次 {was}）" if was else "（之前没有读数文件）")
    )
    if readings is not None:
        if not fresh:
            emitter.line("⚠️ 下面是**上一轮**留下的旧读数，不是这一轮的结果：")
        emitter.field("SRP", readings.srp or "（这一档的读数里没有 SRP 行）")
        emitter.field("材质 shader", readings.shader or "（读数里没有着色器行）")
        emitter.field(
            "渲染器", f"{readings.renderers} 个" if readings.renderers is not None else "读不出"
        )
        emitter.field("视图", f"{len(readings.views)} 个")
        for view, png in readings.views:
            emitter.line(f"    {view:<14}-> {png}")
    emitter.field("产物 PNG", f"{len(new_pngs)} 张新的 / 共 {len(pngs)} 张")
    for path in new_pngs:
        emitter.line(f"    {path}  {path.stat().st_size} bytes")

    document = _document(
        plan,
        readings=readings,
        fresh=fresh,
        took=took,
        rc=rc,
        timed_out=timed_out,
        pngs=pngs,
        new_pngs=new_pngs,
        problems=problems,
    )
    if not problems:
        emitter.document(document)
        return EXIT_OK

    emitter.document({**document, "ok": False})
    emitter.error(
        "有红：" + "；".join(problems),
        hint="看上面的读数；⛔ 别拿 batchmode 的退出码当判据（kb/14 §6）",
    )
    return EXIT_RED


def _document(
    plan: RenderPlan,
    *,
    readings: Readings | None,
    fresh: bool,
    took: float,
    rc: int | None,
    timed_out: bool,
    pngs: tuple[Path, ...],
    new_pngs: tuple[Path, ...],
    problems: list[str],
) -> dict[str, Any]:
    """`--json` 的那份文档（字段名与 `--help` 里写的对得上）。"""
    return {
        "bundle": str(plan.bundle),
        "mode": plan.mode,
        "project": str(plan.project),
        "unity": str(plan.unity),
        "method": plan.method,
        "out": str(plan.out_dir),
        "readings_file": str(plan.readings),
        "readings_updated": fresh,
        "seconds": round(took, 1),
        #: 只当线索：batchmode 的退出码不可靠（kb/14 §6）
        "rc": rc,
        "timed_out": timed_out,
        "srp": readings.srp if readings is not None else None,
        "shader": readings.shader if readings is not None else None,
        "renderers": readings.renderers if readings is not None else None,
        "views": [
            {
                "view": view,
                "png": png,
                "path": str(plan.out_dir / png),
                "exists": (plan.out_dir / png).exists(),
            }
            for view, png in (readings.views if readings is not None else ())
        ],
        "pngs": [
            {"path": str(path), "bytes": path.stat().st_size, "fresh": path in new_pngs}
            for path in pngs
        ],
        "log": str(plan.log),
        "problems": list(problems),
    }


__all__ = [
    "EXIT_OK",
    "EXIT_RED",
    "EXIT_UNUSABLE",
    "MODES",
    "MODE_LABELS",
    "Readings",
    "RenderPlan",
    "add_parser",
    "main",
    "parse_readings",
    "plan_render",
    "run",
]
