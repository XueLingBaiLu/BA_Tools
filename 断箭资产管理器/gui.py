r"""断箭资产管理器 · GUI（PySide6，两页：看包 / 造包）。

## 这一层只管「摆控件 + 转述」 —— 判定一条都不许写在这里

    看包：选一个 .bundle -> 列出每个资产的 key + 资产路径 -> 批量登记到 catalog；
          框里打字即筛（2026-10-27 用户口径：检索就在看包页，不要单独页、也不要全库检索）
    造包：选工程目录 + 真件单位 -> 桩化 / 造 job / 起 Unity / 读 report / 体检
    （撤装/还原 2026-11-03 按用户要求删除：还原用 Steam 的「验证游戏文件完整性」。）

内核全在 `BA_Mod_Maker_2.0/src/ba_mod_maker/`（同一份给 CLI 用）：

| 动作 | 内核 | 说明 |
|---|---|---|
| 读包里有哪些资产 | `core/assets/asset_browser.py::list_bundle_contents` | 队友 task-3 交付 |
| 批量登记地址 | `asset_browser.register_batch` ＋ `write_catalog` | 依赖声明 **own-only** |
| 造包 | `core/assets/build_pipeline.py` | build_job / run_unity_and_report / check_bundle |
| 撤装 | `ba-mod-maker assets restore --project-root <本目录/_patch>` | 补丁柜唯一入口 |

## 三条硬口径（用户 2026-10-27 定，⛔ 别改）

1. bundle 产物落 `断箭资产管理器/bundle/`；临时目录 `断箭资产管理器/_jobs/`（⛔ 不碰 C 盘）；
   补丁柜的**工程根**是 `断箭资产管理器/_patch`，而产品口径把柜子放 `<工程根>/patches`
   ⇒ 柜文件实际在 `_patch/patches/`。这一条由 `reverse_delta.patch_store_root` 唯一决定，
   ⛔ 本文件不另写一份路径（GUI 与 CLI 必须永远是同一个柜）。
2. 资产路径默认 = `Assets/Resources_moved/ModelPrefabs/<名>.prefab`；key 默认 = prefab 名。
   两列都可编辑。
3. 依赖表 = **own-only**（只放自己那个包，hash 0）—— 2026-10-27 真机验过的默认。

## 产品操作集只有 新增 / 删除 / 修改（AGENTS §0.10）

⛔ 本界面**没有**「换 / 替换 / 改成」这类入口；⛔ 不写 Units / Turrets 行
（`ModelFileName` 由用户手工改）；⛔ 不碰游戏根目录 —— 只有「登记到 catalog」与「还原」
这两个**用户亲手点**的动作会经产品入口写盘，各自自带反向 Δ 补丁柜。

## 离屏探针（判据）

    BA_Mod_Maker_2.0/.venv/Scripts/python.exe _work/probe_asset_manager.py
      -> ASSET_MANAGER_OK=<n>
"""
from __future__ import annotations

import ctypes
import importlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

HERE = Path(__file__).resolve().parent
WORKSPACE = HERE.parent
#: 2026-11-04：仓改名成「数据库编辑器」⇒ 按结构找（有 src/ba_mod_maker 的就是），不写死名字。
REPO = next((p for p in sorted(WORKSPACE.iterdir()) if p.is_dir() and (p / "src" / "ba_mod_maker").is_dir()),
            WORKSPACE / "BA_Mod_Maker_2.0（数据侧）")
# 2026-10-30：数据侧目录改名成「（数据侧）」之后，venv 里那条 editable 安装记录还指着旧路径，
# `import ba_mod_maker` 会 ModuleNotFoundError。这里补一条 sys.path，界面不依赖 venv 的安装记录。
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))
VENV_PY = REPO / ".venv" / "Scripts" / "python.exe"

#: 用户口径（2026-10-27，硬）：产物 / 临时 / 补丁柜工程根。
BUNDLE_OUT = HERE / "bundle"
#: 临时目录（job）：⛔ 用户 2026-10-30 明确「严禁动 C 盘」⇒ 一律落在本工具自己的目录里。
JOB_ROOT = HERE / "_jobs"
PATCH_PROJECT_ROOT = HERE / "_patch"
#: 登记 / 还原这类**会写游戏目录**的动作，结果一律也落一份到这里的日志文件。
#: 用户 2026-11-02 报「点了登记没反应，重开还是 US_ACV」—— 本工具用 pythonw 起（没有控制台），
#: 失败只出现在界面那个日志框里，关掉窗口就再也看不到。落盘之后才查得动。
LOG_DIR = HERE / "var" / "logs"

#: 记住「上次开着的目录 / 窗口最大化 / 在哪个页」——用户 2026-11-02：
#: 「每次要点一遍目录、点一遍最大化很烦」。只存 UI 偏好，不存任何数据。
UI_STATE_FILE = HERE / "var" / "ui_state.json"


def load_ui_state() -> dict:
    """读 UI 偏好（读不到 / 坏了都给空 dict，绝不让界面起不来）。"""
    try:
        data = json.loads(UI_STATE_FILE.read_text(encoding="utf-8"))
        return dict(data) if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def save_ui_state(state: dict) -> None:
    try:
        UI_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        UI_STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:  # noqa: BLE001 -- 存不了偏好也不许挡住使用
        pass


def remember(**pairs) -> None:
    """把几项偏好并进去（其余项原样保留）。"""
    state = load_ui_state()
    state.update(pairs)
    save_ui_state(state)


def dep_bundle_names(cbw, raw: bytes, key: str) -> list:
    """这条地址的依赖数组里都有哪些包（名字）。"""
    model = cbw.read_model(raw)
    key_obj = next((k for k in model.keys if k.name == key), None)
    if key_obj is None:
        return []
    for rank in key_obj.ranks:
        entry = model.entries[rank]
        if not entry.dep_ranks:
            continue
        out = []
        for d in entry.dep_ranks:
            dep = model.entries[d]
            out.append(dep.bundle_name or str(dep.internal_id or ""))
        return out
    return []


def bundle_of_key(cbw, raw: bytes, key: str) -> str:
    """key 现在挂在哪个包上（取地址条目的 internalId 末段；没有就返回空串）。"""
    model = cbw.read_model(raw)
    key_obj = next((k for k in model.keys if k.name == key), None)
    if key_obj is None:
        return ""
    for rank in key_obj.ranks:
        entry = model.entries[rank]
        if entry.dep_ranks:
            # 第一条依赖 = **装这个资产的包**（真件实测：US_ACV 地址 deps[0] 就是包 units_assets_all…）。
            first = model.entries[entry.dep_ranks[0]]
            return str(first.bundle_name or "")
    return ""


def repair_deps(cbw, raw: bytes, key: str, own_bundle: str, present: set):
    """把依赖里**盘上已不存在**的包剔掉（2026-11-02 真机事故：依赖还指着已删的 83f3039e，
    游戏报 `Failed to read data for the AssetBundle ...` + `Addressable prefab 没被加载`）。

    返回 (新字节, 保留的依赖, 被剔掉的依赖)。
    """
    deps = dep_bundle_names(cbw, raw, key)
    if not deps:
        return raw, [], []
    keep = [d for d in deps if d == own_bundle or d in present]
    if own_bundle not in keep:
        keep.insert(0, own_bundle)
    dropped = [d for d in deps if d not in keep]
    if not dropped:
        return raw, keep, []
    return cbw.set_dependency_bundles(raw, key=key, bundles=keep).after, keep, dropped

#: 资产路径默认口径（key 默认 = prefab 名，两者都可编辑）。
ASSET_PATH_PREFIX = "Assets/Resources_moved/ModelPrefabs"

#: 换行。⛔ 不写反斜杠转义串（PTC cell 第 0.16 条：落盘正文一律不用转义模板）。
NL = chr(10)

if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

QSS = """
QWidget { background: #17191c; color: #d7dae0;
    font-family: "Microsoft YaHei UI", "Segoe UI"; font-size: 13px; }
QLineEdit, QComboBox, QTableWidget, QPlainTextEdit, QListWidget {
    background: #1f2226; border: 1px solid #2c3036; border-radius: 6px; padding: 4px;
    selection-background-color: #2f6feb; }
QPushButton { background: #2a2e34; border: 1px solid #343941;
    border-radius: 6px; padding: 6px 12px; }
QPushButton:hover { background: #333941; }
QPushButton:disabled { color: #6b7280; background: #212429; }
QPushButton#browse_register_button, QPushButton#build_run_button,
QPushButton#remove_restore_button { background: #2f6feb; border-color: #2f6feb;
    color: #ffffff; font-weight: 600; }
QHeaderView::section { background: #1f2226; color: #9aa3ad; border: none; padding: 6px; }
QTableWidget::item:selected { background: #2f6feb; color: #ffffff; }
QGroupBox { border: 1px solid #2c3036; border-radius: 8px; margin-top: 10px; padding: 10px; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; color: #9aa3ad; }
QLabel#hint { color: #8b949e; }
QLabel#keyline { color: #7ee787; font-weight: 600; }
QLabel#bad { color: #ff7b72; font-weight: 600; }
QTabWidget::pane { border: 1px solid #2c3036; border-radius: 8px; }
QTabBar::tab { background: #1f2226; padding: 8px 18px; border-top-left-radius: 6px;
    border-top-right-radius: 6px; }
QTabBar::tab:selected { background: #2f6feb; color: #ffffff; }
"""


def _integrity_sid() -> str:
    """本进程的完整性 SID（S-1-16-4096=Low / 8192=Medium / 12288=High）；量不到给空串。"""
    try:
        out = subprocess.run(["whoami", "/groups"], capture_output=True)
        text = (out.stdout or b"").decode("mbcs", "replace")
        pos = text.find("S-1-16-")
        if pos >= 0:
            end = pos + len("S-1-16-")
            while end < len(text) and text[end].isdigit():
                end += 1
            return text[pos:end]
    except Exception:  # noqa: BLE001
        pass
    return ""


def _integrity_tag() -> str:
    """本进程的完整性级别（S-1-16-4096=Low / 8192=Medium / 12288=High）+ 是否管理员。

    2026-11-02：用户报「点了登记，写不进 catalog.bin」。同一台机器上**普通权限**的 PowerShell
    能开那个文件的读写句柄，所以必须量出「跑工具的这个进程」到底是什么级别 —— 不能猜。
    """
    tag = "IL=" + (_integrity_sid() or "?")
    try:
        admin = bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:  # noqa: BLE001
        admin = False
    return tag + " admin=" + str(admin)


def _log_startup() -> None:
    """把「谁在跑、什么级别」写进当天的日志（用户不用点任何会写游戏目录的按钮）。"""
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        path = LOG_DIR / ("asset_manager-" + time.strftime("%Y%m%d") + ".log")
        with path.open("a", encoding="utf-8") as fh:
            fh.write(time.strftime("%H:%M:%S") + "  启动 " + _integrity_tag()
                     + " exe=" + sys.executable + NL)
    except Exception:  # noqa: BLE001
        pass


#: 低完整性重开闸：Explorer 常年在 Medium ⇒ 把启动脚本交给它打开，新进程就是 Medium（不需要 UAC）。
RELAUNCH_FLAG = HERE / "var" / "relaunch.flag"


def _relaunch_at_medium() -> bool:
    """本进程若是**低完整性**，借资源管理器把自己重开一次；返回 True = 当前这个是替身，该退出。

    2026-11-02 实证：用户那次 pythonw 拿到的是 `S-1-16-4096`(Low)，连 ACL 写着 FullControl 的
    `catalog.bin` 都打不开写句柄（CreateFileW → err=5）；同一个文件用 Medium 进程打开读写是 OK 的。
    Low 进程自己升不了级，只能请一个 Medium 进程代劳 —— 常驻的 Explorer 正好是。
    """
    if _integrity_sid() != "S-1-16-4096":
        return False
    if os.environ.get("ASSET_MANAGER_NO_RELAUNCH"):
        return False
    # ⛔ 2026-11-02 用户要求：.cmd 全部删掉 ⇒ 自愈重开只认 gui.py，不再依赖任何脚本启动器。
    target = HERE / "gui.py"
    if not target.is_file():
        return False
    if os.environ.get("ASSET_MANAGER_DRYRUN"):
        print("RELAUNCH_WOULD=runas " + sys.executable + " gui.py")
        return True
    try:
        if RELAUNCH_FLAG.is_file() and (time.time() - RELAUNCH_FLAG.stat().st_mtime) < 120:
            return False        # 刚重开过还没起来 ⇒ 别再来一圈
        RELAUNCH_FLAG.parent.mkdir(parents=True, exist_ok=True)
        RELAUNCH_FLAG.write_text(str(time.time()), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    # 借 Windows 的提权中介：会弹一次 UAC（点「是」即可）。
    # ⛔ 不试 explorer.exe 那条：本机实测低完整性进程递给 Explorer 的启动请求**没有起来**
    #    （日志无新「启动」行、无新进程）—— 不留这种看起来能成的空路径。
    try:
        rc = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", sys.executable, '"' + str(target) + '"', str(HERE), 1)
        return int(rc) > 32        # >32 = 成功把请求递出去了
    except Exception:  # noqa: BLE001 -- 递不出去就照常跑，写前预检会给出人话
        return False


def core(name: str):
    """延迟导入产品内核（`ba_mod_maker.` + name）。

    点按钮时才 import —— 建界面 / 跑离屏探针不需要游戏、不需要 Unity、不需要 catalog。
    """
    return importlib.import_module("ba_mod_maker." + name)


def build_kernel():
    """造包编排 —— 2026-10-31 起归本工具所有（kernel/build_pipeline.py）。

    不再从 core.assets.build_pipeline 取：那个模块已从数据侧删除。
    """
    if str(HERE) not in sys.path:
        sys.path.insert(0, str(HERE))
    return importlib.import_module("kernel.build_pipeline")


def asset_browser():
    """队友 task-3 的 `core/assets/asset_browser.py`；还没落地就返回 None。

    本文件**不另造一份读包列表的实现** —— 没落地时页面上直说「等 task-3 交付」，
    落地之后这里自动接上（调用点全都走 `list_bundle_contents` / `register_batch`）。
    """
    try:
        return core("core.assets.asset_browser")
    except ModuleNotFoundError:
        return None


def python_exe() -> str:
    return str(VENV_PY) if VENV_PY.is_file() else sys.executable


def cli_argv(args: list[str]) -> list[str]:
    return [python_exe(), "-m", "ba_mod_maker.cli", *args]


def display_command(argv: list[str]) -> str:
    """把 argv 摆成一条可复制的命令（含空格 / 中文的参数加引号）。"""
    out = ["python", "-m", "ba_mod_maker.cli"]
    for arg in argv:
        text = str(arg)
        plain = all(ch.isalnum() or ch in "_-./:@" for ch in text)
        out.append(text if plain else chr(34) + text + chr(34))
    return " ".join(out)


def run_cli(args: list[str], timeout_s: float = 1800.0) -> tuple[int, str]:
    """跑一条产品 CLI，返回 (退出码, 合并输出)。⛔ 不吞错误。"""
    try:
        done = subprocess.run(
            cli_argv(args), cwd=str(REPO), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout_s,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 2, "跑不起来：" + type(exc).__name__ + ": " + str(exc)
    return done.returncode, (done.stdout or "") + (done.stderr or "")


def game_root() -> Path | None:
    """游戏根：**产品自己的探测器**（`core.assets.game.find_game_root`；⛔ GUI 不猜路径）。"""
    try:
        return core("core.assets.game").find_game_root()
    except Exception:  # noqa: BLE001 -- 探测不到是读数，不是崩溃
        return None


def patch_cabinet_root() -> Path:
    """补丁柜目录 = `<工程根>/patches` —— 路径口径只有产品那一份，GUI 不另写。"""
    return Path(core("asset_kernel.reverse_delta").patch_store_root(PATCH_PROJECT_ROOT))


def prefab_stem(name: str) -> str:
    text = str(name or "").strip()
    if text.lower().endswith(".prefab"):
        return text[: -len(".prefab")]
    return text


def default_key(asset_name: str) -> str:
    """key 默认 = prefab 名（用户口径）。优先用 task-3 的纯函数，没落地就退回同一条口径。"""
    module = asset_browser()
    if module is not None and hasattr(module, "suggest_key"):
        try:
            return str(module.suggest_key(asset_name))
        except Exception:  # noqa: BLE001 -- 建议值算不出来不是致命错
            pass
    return prefab_stem(asset_name)


def canonical_asset_path(asset_name: str) -> str:
    """包里的资产名**一律小写**（Unity 打包时的规范行为）—— 这里按工程里真实文件名恢复规范大小写。

    为什么必须恢复：catalog 登记的是「游戏去要的那个地址」，而游戏里单位行写的是规范大小写
    （例 `Assets/Resources_moved/ModelPrefabs/US_ACV.prefab`）。⛔ 不能把包内的小写直接登记上去。
    找不到真件（新模型）就退回默认拼法。
    """
    try:
        up = core("core.assets.unity_prefab")
        unity = up.unity_project_path(None)
        found = up.find_prefab(unity, prefab_stem(asset_name))
        if found:
            # ⚠ 只借**文件名的大小写**，目录固定用包里的那个前缀 —— 真件在子目录
            #   （实测 ACV 真件在 ModelPrefabs/US/US_ACV.prefab），而我们这个包的容器
            #   是 ModelPrefabs/US_ACV.prefab；登记成真件那条路径会指向包内**不存在**的地址。
            return ASSET_PATH_PREFIX + "/" + Path(str(found)).name
    except Exception:  # noqa: BLE001 -- 找不到工程 / 没同名真件都退回默认，不拦装机
        pass
    return default_asset_path(asset_name)


def default_asset_path(asset_name: str) -> str:
    """资产路径默认 = `Assets/Resources_moved/ModelPrefabs/<名>.prefab`（用户口径）。"""
    module = asset_browser()
    if module is not None and hasattr(module, "suggest_asset_path"):
        try:
            return str(module.suggest_asset_path(prefab_stem(asset_name)))
        except Exception:  # noqa: BLE001 -- 同上
            pass
    return ASSET_PATH_PREFIX + "/" + prefab_stem(asset_name) + ".prefab"


#: ★ 2026-11-02：从零造时「材质来源」的自动查找根（AssetRipper 导出的 Unity 工程）。
EXPORTED_PROJECT = WORKSPACE / "参考件" / "Broken Arrow Assets v1.2.2.1" / "ExportedProject"


def find_reference_prefab(name: str) -> str:
    """按件名在导出工程里找真件 prefab，返回 `Assets/...prefab` 相对路径；找不到返回空串。

    判据：`<ExportedProject>/Assets/Resources_moved/ModelPrefabs/**/<name>.prefab` 恰好命中 1 个。
    ⛔ 不猜：命中 0 个或 >1 个都返回空串（宁可让用户手填，也不给一个错的材质来源）。
    """
    root = EXPORTED_PROJECT / "Assets" / "Resources_moved" / "ModelPrefabs"
    key = str(name or "").strip()
    if not key or not root.is_dir():
        return ""
    hits = sorted(root.rglob(key + ".prefab"))
    if len(hits) != 1:
        return ""
    return hits[0].relative_to(EXPORTED_PROJECT).as_posix()


def scan_projects(root: Path) -> list[dict]:
    """扫一遍工程目录：每个子目录 = 一个工程。只读，⛔ 不改盘。

    目录里放什么由用户决定 —— 不绑死在提取器的输出上（提取器只是默认值）。
    """
    found: list[dict] = []
    if not root.is_dir():
        return found
    for item in sorted(root.iterdir()):
        if not item.is_dir():
            continue
        info = {"dir": str(item), "name": item.name, "mesh": ""}
        for name in ("model.glb", "model.obj"):
            if (item / name).is_file():
                info["mesh"] = name
                break
        found.append(info)
    return found


def run_build_chain(*, from_dir, base_unit="", out_dir, bundle_out, unity_project=None,
                    job_only=False, timeout_s=None, emit=None, on_job=None,
                    create_missing=True, create_parent="", material_from="",
                    material_source="", prefab_path="", new_root="") -> dict:
    """一条链：造 job（桩化 ＋ shader 绑定）-> 起 Unity -> 读 report -> 四把尺子体检。

    ⛔ 判定全部交给内核：这里只编排、只转述，不猜、不吞。
    返回 `{ok, code, reason, lines, job, report, bundle, problems}`。
    """
    lines: list[str] = []

    def say(text: str = "") -> None:
        lines.append(text)
        if emit is not None:
            emit(text)

    def fail(reason: str, code: int = 1) -> dict:
        say("✗ " + reason)
        return {"ok": False, "code": code, "reason": reason, "lines": lines,
                "job": "", "report": None, "bundle": "", "problems": []}

    src = Path(str(from_dir)).expanduser()
    if not src.is_dir():
        return fail("工程目录不存在：" + str(src), 2)
    # ★ 2026-10-30：**制造**只有一个入口 —— 有底包就在它上面造（改装），没有就从零造。
    if not (str(base_unit).strip() or str(prefab_path).strip() or str(new_root).strip()):
        return fail("既没有底包（单位键 / prefab）、也没有新根名 —— 不知道要造什么", 2)

    say("① 造 job（桩化 ＋ 材质 shader 绑定 ＋ job.json —— 这一步不起 Unity）")
    _how = ("底包 " + str(prefab_path).strip()) if str(prefab_path).strip() else (
        "从零造（根 = " + str(new_root).strip() + "）")
    say("   工程 " + str(src) + " · " + _how)
    say("   产物目录 " + str(bundle_out) + " · job 目录 " + str(out_dir))
    try:
        res = build_kernel().build_job(
            src, base_unit=str(base_unit).strip(), out_dir=str(out_dir),
            bundle_out_dir=str(bundle_out), unity_project=unity_project,
            prefab_path=str(prefab_path or "").strip(),
            new_root=str(new_root or "").strip(),
            create_missing=bool(create_missing),
            create_parent=str(create_parent or "").strip(),
            material_from=str(material_from or "").strip(),
            material_source=str(material_source or "").strip(),
        )
    except Exception as exc:  # noqa: BLE001 -- 内核的错必须原样给用户看
        return fail(type(exc).__name__ + ": " + str(exc), 2)
    say("   桩化 " + str(res.get("stub_note") or ""))
    say("   真件 prefab " + str(res.get("prefab") or ""))
    say("   job " + str(res.get("job") or ""))
    job = str(res.get("job") or "")
    if on_job is not None:
        on_job(job)
    if job_only:
        say("② 只造 job：把下面这条拿去 PowerShell 手跑，跑完把 report.json 路径填回本页，")
        say("   再点「读 report 并体检」—— 本页不会替你判断 Unity 有没有跑成功。")
        for row in str(res.get("command") or "").splitlines():
            say("   " + row)
        say("③ 跳过（没起 Unity）")
        return {"ok": True, "code": 0, "reason": "", "lines": lines, "job": job,
                "report": None, "bundle": "", "problems": []}

    say("② 起 Unity（batchmode，等它退）；Unity 自己的日志尾部会实时贴到下面")
    try:
        outcome = build_kernel().run_unity_and_report(
            res, timeout_s=(None if timeout_s is None else float(timeout_s)))
    except Exception as exc:  # noqa: BLE001
        return fail("Unity 起不来 / report 读不出来：" + type(exc).__name__ + ": " + str(exc), 2)
    detail = outcome.get("outcome") or {}
    state = "超时" if detail.get("timed_out") else "退出码 " + str(detail.get("returncode"))
    say("   " + state + " · 用时 " + str(detail.get("elapsed_s")) + " s · 日志 "
        + str(detail.get("log")) + "（" + format(int(detail.get("log_bytes") or 0), ",") + " B）")
    for row in (detail.get("log_tail") or [])[-12:]:
        say("   [unity] " + str(row))
    report = outcome.get("report") or {}
    say("③ 读 report.json（退出码不是判据 —— report.ok 才是，规格 §5 第 7 条）")
    for row in (outcome.get("lines") or []):
        say("   " + str(row))
    if not report.get("ok"):
        return fail("report.ok=False —— 造包失败：" + str(report.get("error") or ""), 1)
    bundle = str(outcome.get("bundle") or "")
    if not bundle:
        return fail("report 里没有可用的 bundle 路径", 1)
    say("④ 体检（四把尺子：类型表 / 悬空 PPtr / 命名契约 / 材质 shader 形状）")
    asset_path = str(report.get("prefab_source") or "")
    try:
        verdict = build_kernel().check_bundle(
            bundle, asset_path=asset_path, unit_prefab=True)
    except Exception as exc:  # noqa: BLE001
        return fail("体检跑不起来：" + type(exc).__name__ + ": " + str(exc), 2)
    for row in (verdict.get("lines") or []):
        say("   " + str(row))
    problems = list(verdict.get("problems") or [])
    for row in problems:
        say("   ⛔ " + str(row))
    if problems:
        return fail("体检有红 " + str(len(problems)) + " 条 —— ⛔ 别装机", 1)
    say("完成：包 " + bundle)
    say("   【下一步】勾选「登记到 catalog」那一步在「看包」页做；")
    say("   ⛔ 本工具不写 Units / Turrets —— 那一行 ModelFileName 由你手工改。")
    return {"ok": True, "code": 0, "reason": "", "lines": lines, "job": job,
            "report": report, "bundle": bundle, "problems": problems}


def read_report_and_check(*, report_path, emit=None) -> dict:
    """只读 report.json ＋ 四把尺子体检（Unity 由用户手跑时走这一条）。"""
    lines: list[str] = []

    def say(text: str = "") -> None:
        lines.append(text)
        if emit is not None:
            emit(text)

    target = Path(str(report_path)).expanduser()
    if not target.is_file():
        say("✗ 找不到 report.json：" + str(target))
        return {"ok": False, "code": 2, "lines": lines, "bundle": "", "problems": []}
    unity_build = core("core.assets.unity_build")
    pipeline = build_kernel()
    try:
        report = unity_build.read_report(target)
    except Exception as exc:  # noqa: BLE001
        say("✗ report 读不出来：" + type(exc).__name__ + ": " + str(exc))
        return {"ok": False, "code": 2, "lines": lines, "bundle": "", "problems": []}
    for row in unity_build.report_lines(report):
        say(str(row))
    if not report.get("ok"):
        say("✗ report.ok=False —— 造包失败：" + str(report.get("error") or ""))
        return {"ok": False, "code": 1, "lines": lines, "bundle": "", "problems": []}
    bundle = str(report.get("bundle") or "")
    try:
        norm = unity_build.normalise_bundle(bundle)
    except Exception as exc:  # noqa: BLE001
        say("✗ 包名归一失败：" + type(exc).__name__ + ": " + str(exc))
        return {"ok": False, "code": 1, "lines": lines, "bundle": "", "problems": []}
    bundle = str(norm["path"])
    say("包 " + bundle)
    verdict = pipeline.check_bundle(bundle, asset_path=str(report.get("prefab_source") or ""),
                                    unit_prefab=True)
    for row in (verdict.get("lines") or []):
        say("   " + str(row))
    problems = list(verdict.get("problems") or [])
    for row in problems:
        say("   ⛔ " + str(row))
    return {"ok": not problems, "code": 0 if not problems else 1, "lines": lines,
            "bundle": bundle, "problems": problems}


class CallWorker(QtCore.QThread):
    """把一次阻塞调用放到后台线程（界面不卡）。`line` 逐行回传，`done` 回 (code, 结果)。"""

    line = QtCore.Signal(str)
    done = QtCore.Signal(int, object)

    def __init__(self, func) -> None:
        super().__init__()
        self._func = func

    def run(self) -> None:
        try:
            code, payload = self._func(self.line.emit)
        except Exception as exc:  # noqa: BLE001 -- 后台线程里兜住，界面必须知道
            self.done.emit(3, {"ok": False, "reason": type(exc).__name__ + ": " + str(exc)})
            return
        self.done.emit(int(code or 0), payload)


class BuildWorker(QtCore.QThread):
    """造包链：`build_job` -> Unity -> report -> 体检。跑完把 job 目录回给界面（贴 Unity 日志）。"""

    line = QtCore.Signal(str)
    unity_started = QtCore.Signal(str)
    done = QtCore.Signal(int, object)

    def __init__(self, params: dict) -> None:
        super().__init__()
        self._params = dict(params)

    def run(self) -> None:
        try:
            result = run_build_chain(
                emit=self.line.emit, on_job=self.unity_started.emit, **self._params)
        except Exception as exc:  # noqa: BLE001
            self.done.emit(3, {"ok": False, "reason": type(exc).__name__ + ": " + str(exc)})
            return
        self.done.emit(int(result.get("code") or 0), result)


def register_entries(emit, *, bundle, entries, game, browser) -> tuple[int, dict]:
    """批量登记：读 catalog -> register_batch（own-only）-> 写 catalog.bin ＋ 同步 hash。

    写盘只走产品入口 write_catalog（基线核对 ＋ 反向 Δ 进补丁柜 ＋ 写后 sha 核对），
    ⛔ 本文件不手改一个字节。两次写都进**同一个**补丁柜条目（merge）⇒ 撤装一次撤干净。
    """
    game_mod = core("core.assets.game")
    address_edit = core("cli.address_edit")
    catalog_path = game_mod.catalog_file(game)
    hash_path = game_mod.catalog_hash_file(game)
    if not catalog_path.is_file():
        emit("✗ 找不到 catalog：" + str(catalog_path))
        return 2, {"ok": False, "reason": "catalog 不在"}
    before = catalog_path.read_bytes()
    emit("catalog " + str(catalog_path) + " · " + format(len(before), ",") + " B")
    try:
        result = browser.register_batch(before, entries, source_note="断箭资产管理器 看包页")
    except Exception as exc:  # noqa: BLE001
        emit("✗ register_batch 失败：" + type(exc).__name__ + ": " + str(exc))
        return 2, {"ok": False, "reason": str(exc)}
    for row in (result.get("lines") or ()):
        emit(str(row))
    added = int(result.get("added") or 0)
    skipped = int(result.get("skipped") or 0)
    failed = int(result.get("failed") or 0)
    for row in list(result.get("results") or ()):
        status = str(row.get("status") or "")
        mark = "✓" if status == "added" else ("·" if status == "skipped" else "✗")
        tail = "" if not row.get("reason") else "（" + str(row.get("reason")) + "）"
        emit("  " + mark + " " + str(row.get("key")) + " · " + status + tail)
    counts_before = tuple(result.get("counts_before") or ())
    counts_after = tuple(result.get("counts_after") or ())
    if not result.get("changed"):
        emit("没有一条要登记：新增 0 / 跳过 " + str(skipped) + " / 失败 " + str(failed))
        return (0 if failed == 0 else 1), {"ok": failed == 0, "added": added,
                                           "skipped": skipped, "failed": failed}
    after = bytes(result["catalog_bytes"])
    plan = address_edit.AddressPlan(
        action="add", key=bundle, before=before, after=after,
        counts_before=counts_before, counts_after=counts_after,
        lines=tuple(result.get("lines") or ()), bundle_name=bundle)
    patch, size = address_edit.write_catalog(
        plan, catalog_path=catalog_path, project_root=PATCH_PROJECT_ROOT, game_root=game,
        label="地址：批量登记 " + str(added) + " 条（包 " + bundle + "）")
    emit("catalog 已写：新增 " + str(added) + " 条 · 补丁柜 " + str(patch)
         + "（" + format(size, ",") + " B）")
    if hash_path.is_file():
        hash_before = hash_path.read_bytes()
        hash_after = core("core.asset.new_asset").catalog_hash_bytes(after)
        if hash_before != hash_after:
            hash_plan = address_edit.AddressPlan(
                action="add", key="catalog.hash", before=hash_before, after=hash_after,
                counts_before=counts_before, counts_after=counts_after)
            address_edit.write_catalog(
                hash_plan, catalog_path=hash_path, project_root=PATCH_PROJECT_ROOT,
                game_root=game, label="地址：同步 catalog.hash")
            emit("catalog.hash 已同步：" + hash_after.decode("ascii"))
    else:
        emit("⚠ 盘上没有 catalog.hash（⛔ 不凭空造一份）—— 需要就用手工入口补")
    emit("【下一步】⛔ 本工具不写 Units / Turrets —— 那一行 ModelFileName 由你手工改。")
    return 0, {"ok": failed == 0, "added": added, "skipped": skipped, "failed": failed}


def mod_dir_of(game) -> Path:
    """`<游戏>/BA_Mod` —— DLL 补丁只认这个目录（2026-10-27 起数据侧改读它）。"""
    new_asset = core("core.asset.new_asset")
    return Path(game) / new_asset.MOD_DIR_NAME


def copy_bundle_into_mod(bundle_path, game) -> tuple[Path, bool]:
    """把包**复制**进 `<游戏>/BA_Mod/`；返回 (目标路径, 是否真复制了)。

    ⛔ 只新增：不删任何东西；同名同大小 ⇒ 视为已在，跳过复制（重复点按钮不会反复写）。
    """
    source = Path(bundle_path)
    if not source.is_file():
        raise FileNotFoundError("包不存在：" + str(source))
    target_dir = mod_dir_of(game)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / source.name
    same = target.is_file() and target.stat().st_size == source.stat().st_size
    if not same:
        shutil.copy2(source, target)
    return target, not same


class BrowsePage(QtWidgets.QWidget):
    """页 1 · 看包：一个 .bundle 里的每个资产 -> key ＋ 资产路径 -> 批量登记到 catalog。"""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("page_browse")
        self._busy = False
        self._key_overrides = {}   # 用户手改过的 key；下一行从磁盘读回（关界面也不丢）
        self._loading = False
        self._worker = None
        self._build()

    def _build(self) -> None:
        outer = QtWidgets.QVBoxLayout(self)

        top = QtWidgets.QHBoxLayout()
        self.bundle_edit = QtWidgets.QLineEdit("")
        self.bundle_edit.setObjectName("browse_bundle_edit")
        self.bundle_edit.setPlaceholderText("一个 .bundle 的路径（或点右边从 BA_Mod 列表里挑）…")
        # 上次读过的包：下次打开直接把它填回去（用户 2026-11-02：不想每次点一遍目录）
        self.bundle_edit.setText(str(load_ui_state().get("last_bundle") or ""))
        btn_pick = QtWidgets.QPushButton("选包…")
        btn_pick.setObjectName("browse_pick_button")
        btn_pick.clicked.connect(self._pick_bundle)
        self.btn_load = QtWidgets.QPushButton("读包列资产")
        self.btn_load.setObjectName("browse_load_button")
        self.btn_load.clicked.connect(self._on_load)
        top.addWidget(self.bundle_edit, 1)
        top.addWidget(btn_pick)
        top.addWidget(self.btn_load)
        outer.addLayout(top)


        flt = QtWidgets.QHBoxLayout()
        self.filter_edit = QtWidgets.QLineEdit("")
        self.filter_edit.setObjectName("browse_filter_edit")
        self.filter_edit.setPlaceholderText(
            "筛选这个包里的资产（名 / key / 资产路径，忽略大小写）…")
        self.filter_edit.textChanged.connect(self._apply_filter)
        flt.addWidget(QtWidgets.QLabel("筛选："))
        flt.addWidget(self.filter_edit, 1)
        outer.addLayout(flt)

        body = QtWidgets.QHBoxLayout()
        self.table = QtWidgets.QTableWidget(0, 3)
        self.table.setObjectName("browse_asset_table")
        self.table.itemChanged.connect(self._on_item_changed)
        self.table.setHorizontalHeaderLabels(
            ["资产（只读）", "key（可编辑）", "资产路径（可编辑）"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 220)
        self.table.setColumnWidth(1, 240)
        self.table.setSelectionBehavior(
            QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        body.addWidget(self.table, 1)

        outer.addLayout(body, 1)

        row = QtWidgets.QHBoxLayout()
        self.count_label = QtWidgets.QLabel("0 条（key 默认 = prefab 名）")
        self.count_label.setObjectName("browse_count_label")
        row.addWidget(self.count_label)
        row.addStretch(1)
        self.btn_register = QtWidgets.QPushButton("登记到 catalog（批量，已登记的跳过）")
        self.btn_register.setObjectName("browse_register_button")
        self.btn_register.clicked.connect(self._on_register)
        row.addWidget(self.btn_register)
        outer.addLayout(row)

        self.log = QtWidgets.QPlainTextEdit()
        self.log.setObjectName("browse_log")
        self.log.setReadOnly(True)
        outer.addWidget(self.log, 1)

    # ---- 读数 / 动作 ----
    def bundle_name(self) -> str:
        text = self.bundle_edit.text().strip()
        return Path(text).name if text else ""

    def entries(self) -> list[dict]:
        """表格里**要登记的那些行**（key / 资产路径 都非空）。⛔ 不看只读那一列。"""
        bundle = self.bundle_name()
        rows: list[dict] = []
        for index in range(self.table.rowCount()):
            key_item = self.table.item(index, 1)
            path_item = self.table.item(index, 2)
            key = (key_item.text() if key_item is not None else "").strip()
            asset_path = (path_item.text() if path_item is not None else "").strip()
            if not key or not asset_path:
                continue
            rows.append({"key": key, "asset_path": asset_path, "bundle": bundle})
        return rows

    def add_rows(self, rows: list[dict]) -> int:
        """把 `list_bundle_contents` 的每一条摊进表格；返回**可登记**的条数。

        ⚠ 发布包里 Mesh / Texture2D / Shader / MonoScript 没有类型树 ⇒ `asset_name` 是空串
        （真包实测 341/643 有名字）。这种行 key 与资产路径**都留空**，等用户手填 ——
        ⛔ 不给它编一个 `ModelPrefabs/.prefab` 那样的假默认值。
        """
        self._loading = True
        registered = self._catalog_keys()
        self.table.setRowCount(len(rows))
        for index, item in enumerate(rows):
            name = str(item.get("asset_name") or "")
            filled = bool(name)
            asset_path = str(item.get("asset_path") or "")
            # ★ 包内资产名全小写；登记要用游戏侧那份规范大小写 ⇒ 一律按工程真件名恢复。
            if filled:
                asset_path = canonical_asset_path(name)
            default = default_key(name) if filled else ""
            # key 的优先级（高→低）：本次会话手改 → **catalog 里已登记的那条** → 包里默认 prefab 名。
            # 中间那条是用户 2026-11-02 报的「登记完重开还是显示 US_ACV」：key 的唯一账本是 catalog，
            # 界面必须从它读回来，否则登记成功看起来像没生效。
            key = ""
            if filled:
                key = (self._key_overrides.get(name)
                       or registered.get(asset_path.replace("\\", "/"), "")
                       or default)
            self.table.setItem(index, 0, QtWidgets.QTableWidgetItem(name))
            kitem = QtWidgets.QTableWidgetItem(key)
            # 默认 key（= 真件名）留作「依赖声明抄自哪条」的依据；用户改 key 不改它
            kitem.setData(QtCore.Qt.ItemDataRole.UserRole, default)
            self.table.setItem(index, 1, kitem)
            self.table.setItem(index, 2, QtWidgets.QTableWidgetItem(asset_path))
        named = sum(1 for item in rows if str(item.get("asset_name") or ""))
        # 包里那条**容器** prefab 才是用户要装的那一条（其余 34 条是内部网格/材质）。
        # 认法：包名 = <slug>_assets_all_<hash>.bundle ⇒ 容器 prefab 名就是 <slug>。
        # ⛔ 不能按 `.prefab` 后缀认：每个网格资产也被推导成 ModelPrefabs/<名>.prefab。
        self._prefab_row = -1
        slug = self.bundle_name().split("_assets_all_")[0].lower()
        for index, item in enumerate(rows):
            if str(item.get("asset_name") or "").lower() == slug:
                self._prefab_row = index
                break
        self._named_rows = named
        self._apply_filter(self.filter_edit.text())
        self._loading = False
        return len(self.entries())

    def load_bundle(self, path: str) -> None:
        """外部（检索页双击）让本页读一颗包。"""
        self.bundle_edit.setText(str(path or ""))
        self._on_load()

    def set_filter_text(self, text: str) -> None:
        """外部（例如检索页双击）把筛选框设成某个值。"""
        self.filter_edit.setText(str(text or ""))

    def _apply_filter(self, text: str) -> None:
        """只隐藏行，不重建控件（AGENTS §0.7 就地更新）。"""
        needle = str(text or "").strip().lower()
        shown = 0
        for index in range(self.table.rowCount()):
            hay = ""
            for col in range(self.table.columnCount()):
                item = self.table.item(index, col)
                hay += (item.text() if item is not None else "") + "\n"
            hit = not needle or needle in hay.lower()
            self.table.setRowHidden(index, not hit)
            shown += 1 if hit else 0
        self.count_label.setText(
            "显示 " + str(shown) + " / " + str(self.table.rowCount()) + " 行"
            + "（有名字 " + str(getattr(self, "_named_rows", 0)) + "）· key 默认 = prefab 名")

    def _log_line(self, text: str) -> None:
        r"""把一行追加进 `<本工具>\var\logs\asset_manager-YYYYMMDD.log`（写不了也不许影响界面）。"""
        try:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            path = LOG_DIR / ("asset_manager-" + time.strftime("%Y%m%d") + ".log")
            with path.open("a", encoding="utf-8") as fh:
                fh.write(time.strftime("%H:%M:%S") + "  " + str(text).rstrip() + NL)
        except Exception:  # noqa: BLE001 -- 日志写不了不能挡住主流程
            pass

    def _can_write_file(self, path) -> bool:
        """**不写一个字节**地判断这个文件能不能写：以 r+b 打开一次再关。

        ⛔ 不用 os.access：Windows 上它对 ACL 不可靠 —— 实测 catalog.bin 报 True、真写却是
        PermissionError 13（本机 2026-11-02 实测）。判据只能是「真打开一次」。
        """
        try:
            fh = open(str(path), "r+b")
        except OSError:
            return False
        try:
            fh.close()
        except OSError:
            pass
        return True

    def _say(self, text: str) -> None:
        self.log.appendPlainText(str(text).rstrip())
        self.log.verticalScrollBar().setValue(self.log.verticalScrollBar().maximum())
        self._log_line(text)

    def _pick_bundle(self) -> None:
        state = load_ui_state()
        last = str(state.get("last_dir") or "")
        start = last if last and Path(last).is_dir() else str(BUNDLE_OUT if BUNDLE_OUT.is_dir() else HERE)
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "选一个 .bundle", start, "AssetBundle (*.bundle);;所有文件 (*)")
        if path:
            self.bundle_edit.setText(path)
            remember(last_dir=str(Path(path).parent), last_bundle=str(path))
            self._on_load()

    def _on_load(self) -> None:
        module = asset_browser()
        if module is None:
            self._say("✗ core/assets/asset_browser.py 还没落地（task-3 未交付）——")
            self._say("   本页已经接在 list_bundle_contents 上，模块一落地就能读包。")
            return
        path = self.bundle_edit.text().strip()
        if not path:
            self._say("✗ 先选一个 .bundle")
            return
        self._say("读包 " + path)
        try:
            rows = module.list_bundle_contents(path)
        except Exception as exc:  # noqa: BLE001 -- 内核 fail-closed 的原话要给用户看
            self._say("✗ 读包失败：" + type(exc).__name__ + ": " + str(exc))
            return
        ready = self.add_rows(list(rows))
        self._say("   对象 " + str(len(rows)) + " 个 · 可登记 " + str(ready) + " 条")

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        for button in (self.btn_load, self.btn_register):
            button.setEnabled(not busy)

    def _commit_editors(self) -> None:
        """结束正在进行的单元格编辑，让 item.text() 读到刚输入的值。

        不要调 QAbstractItemView.commitData：它在 PySide6 下抛异常，而异常被 Qt 事件循环吞掉
        => 按钮表现成「点了没反应」（2026-10-31 真机踩过）。
        """
        try:
            self.table.setCurrentItem(None)
        except Exception:  # noqa: BLE001 -- 清选中失败不影响后面的取值
            pass
        QtWidgets.QApplication.processEvents()

    def _on_item_changed(self, item) -> None:
        """记住用户手改的 key（重读同一颗包时不冲掉）。"""
        if getattr(self, "_loading", False):
            return
        if item is None or item.column() != 1:
            return
        name_item = self.table.item(item.row(), 0)
        name = (name_item.text() if name_item is not None else "").strip()
        if name:
            self._key_overrides[name] = item.text().strip()
            # 不另存文件：key 的保存 = 点「登记到 catalog」写进 catalog 那条地址（唯一账本）

    def _first_filled_row(self) -> int:
        for index in range(self.table.rowCount()):
            k = self.table.item(index, 1)
            p = self.table.item(index, 2)
            if k is not None and p is not None and k.text().strip() and p.text().strip():
                return index
        return -1

    def _remember_current_row(self) -> None:
        """记下用户正在编辑的那一行（⛔ 必须在 _commit_editors() 之前调）。

        _commit_editors() 会 setCurrentItem(None) ⇒ currentRow() 变 -1。实测（_work/probe_key_transfer2.py
        的 B_other 场景）：先提交再取行号 ⇒ 只拿到容器行 ⇒ 用户在别的行手打的 key 被丢掉（key_ok=0）。
        """
        row = self.table.currentRow()
        if row >= 0:
            self._last_row = row

    def _target_row(self) -> int:
        """要登记的那一行：用户最后碰过的行 → 包里那条 .prefab 容器行 → 第一条填了的行。"""
        row = getattr(self, "_last_row", -1)
        if 0 <= row < self.table.rowCount():
            return row
        row = getattr(self, "_prefab_row", -1)
        if 0 <= row < self.table.rowCount():
            return row
        return self._first_filled_row()

    def _key_for_row(self, row: int) -> str:
        """这一行 key 列里**用户手打的值**（空 ⇒ 空串，⛔ 不拿 prefab 名顶替）。"""
        item = self.table.item(row, 1) if row >= 0 else None
        return (item.text() if item is not None else "").strip()

    def _catalog_keys(self) -> dict[str, str]:
        """catalog 里**已经登记**的键：资产路径 → key（只读；读不到给空表，绝不抛）。

        用户口径（2026-11-02）：**key 的唯一账本是 catalog** —— 所以重开界面时 key 列要显示
        catalog 里那一条，而不是包里默认的 prefab 名（否则「登记成功」看起来像没生效）。
        """
        game = game_root()
        if game is None:
            return {}
        try:
            gm = core("core.assets.game")
            cbw = core("core.assets.catalog_bin_write")
            path = gm.catalog_file(game)
            if not path.is_file():
                return {}
            model = cbw.read_model(path.read_bytes())
        except Exception:  # noqa: BLE001 -- 读不到就退回默认 key，不让界面挂
            return {}
        out: dict[str, str] = {}
        for entry in model.entries:
            key = getattr(entry, "primary_key", None)
            iid = getattr(entry, "internal_id", None)
            prov = getattr(entry, "provider", None) or ""
            if key and iid and str(prov).endswith("BundledAssetProvider"):
                # 同一条资产路径可能有多条键（别名）⇒ **后写的赢**：新登记的那条显示出来
                out[str(iid).replace("\\", "/")] = str(key)
        return out

    def _register_mod_bundles(self, emit) -> tuple[int, dict]:
        """把 `<游戏>\\BA_Mod\\*.bundle` 逐个登记进 catalog。

        用户口径（2026-11-02）：只登记 BA_Mod 里**已经存在**的包；⛔ 不复制、不移动。
        """
        na = core("core.asset.new_asset")
        game_mod = core("core.assets.game")
        up = core("core.assets.unit_pack")
        rd = core("asset_kernel.reverse_delta")
        browser = asset_browser()
        if browser is None:
            emit("✗ asset_browser 模块不在，读不了包内容")
            return 2, {"ok": False, "reason": "asset_browser 缺失"}
        # 只枚举已有文件；⛔ 这里不许出现 copy / move / rename（用户要亲自放包）。
        landing_dir = na.install_landing(game_root())
        bundles = sorted(landing_dir.glob("*.bundle"))
        if not bundles:
            emit("✗ " + str(landing_dir) + " 里没有 .bundle")
            return 2, {"ok": False, "reason": "没有包"}
        emit("BA_Mod 里共 " + str(len(bundles)) + " 个包")
        catalog_path = game_mod.catalog_file(game_root())
        hash_path = game_mod.catalog_hash_file(game_root())
        ok = 0
        for src in bundles:
            slug = src.name.split("_assets_all_")[0].lower()
            prefab = ""
            try:
                for item in browser.list_bundle_contents(str(src)):
                    if str(item.get("asset_name") or "").lower() == slug:
                        prefab = str(item["asset_name"])
                        break
            except Exception as exc:  # noqa: BLE001 -- 一个包读不开不拦其它包
                emit("✗ 读包失败 " + src.name + "：" + type(exc).__name__ + ": " + str(exc))
                continue
            if not prefab:
                emit("✗ " + src.name + "：包里没找到 " + slug + " 那条 prefab，跳过")
                continue
            # key：用户手改过的优先（表格 key 列），没改过才用 prefab 名。
            key = self._key_overrides.get(prefab, prefab)
            asset_path = ASSET_PATH_PREFIX + "/" + prefab + ".prefab"
            emit("· " + src.name + "  key=" + key)
            try:
                reg = up._catalog_bundle_internal_id(catalog_path, src.name)
            except Exception:  # noqa: BLE001
                reg = None
            if reg:
                emit("  已在 catalog：" + str(reg) + " => 跳过（不动包）")
                ok += 1
                continue
            before = catalog_path.read_bytes()
            actual, _d, _s = na.bundle_facts_of_file(str(src))
            after, _r = na.add_catalog_entries(
                before, key=key, asset_path=asset_path, facts=dict(actual),
                kind="model", dependency_source_key=prefab, dependency_policy="auto")
            hash_before = hash_path.read_bytes() if hash_path.is_file() else b""
            hash_after = na.catalog_hash_bytes(after)
            store = rd.PatchStore(rd.patch_store_root(PATCH_PROJECT_ROOT), game_root=game_root())
            rec = store.begin("资产落包 " + key)
            rec.capture_file(catalog_path, before=before, after=after)
            rec.capture_file(hash_path, before=hash_before or None, after=hash_after)
            rec.commit()
            na._write_verified(catalog_path, after, what="catalog")
            na._write_verified(hash_path, hash_after, what="catalog.hash")
            emit("  ✓ 已登记 " + key + "（包没动）")
            ok += 1
        emit("合计：成功 " + str(ok) + " / 共 " + str(len(bundles)))
        return (0 if ok else 3), {"ok": ok > 0, "added": ok, "skipped": 0,
                                    "failed": len(bundles) - ok}
    def _on_register(self) -> None:
        """登记到 catalog：把**已经放进** `<游戏>\\BA_Mod\\` 的包登记成一条模型地址。

        ⛔ 2026-11-02 用户口径（硬）：**工具绝不复制 / 移动包** ——
        原话「我要亲自将bundle包放进BA_Mod文件夹，绝不能让工具自己来！」。
        包不在 BA_Mod ⇒ 直接拒绝并说清，一个字节都不写。
        包在 ⇒ 只写 catalog + catalog.hash（包本身一个字节都不动）。
        """
        if self._busy:
            return
        # ★ 2026-11-02 修复（判据 probe_key_transfer2：B_other key_ok 0→1）：
        #   `_commit_editors()` 会把选中的单元格清掉（currentRow 变 -1）。
        #   若提交之后再取行号 ⇒ 只能回落到容器行 ⇒ 用户在**别的行**手打的 key 被丢掉。
        #   ⇒ 提交前先把「他正在编辑的那一行」记进 _last_row，行号一律走 _target_row()。
        self._remember_current_row()
        self._commit_editors()
        bundle_path = self.bundle_edit.text().strip()
        if not bundle_path:
            self._say("✗ 先读一颗包")
            return
        row = self._target_row()   # 内含回落：容器行 → 第一条填了的行
        if row < 0:
            # 没读包 / 表里没有可登记行 => 直接把 BA_Mod 里的包全部登记（用户口径）
            self._set_busy(True)
            self._say("===== 登记 BA_Mod 里的全部包 =====")
            worker = CallWorker(self._register_mod_bundles)
            worker.line.connect(self._say)
            worker.done.connect(self._on_done)
            self._worker = worker
            worker.start()
            return
        key_item = self.table.item(row, 1)
        path_item = self.table.item(row, 2)
        # ★ key 必须用表格 key 列里**用户手打的值**（⛔ 不许回退成 prefab 名）。
        #   依赖声明仍抄默认 prefab 名那条真件地址：dep = 表格 UserRole 里存的默认 key。
        key = self._key_for_row(row)
        asset_path = (path_item.text() if path_item is not None else "").strip()
        dep = str(key_item.data(QtCore.Qt.ItemDataRole.UserRole) or "") if key_item else ""
        if not key or not asset_path:
            self._say("✗ 这一行的 key / 资产路径 是空的")
            return
        if not dep:
            self._say("✗ 这一行没有默认 key（依赖声明没有依据）")
            return
        game = game_root()
        if game is None:
            self._say("✗ 探测不到游戏根目录（BA_MOD_MAKER_GAME 可指定）")
            return
        self._set_busy(True)
        self._say("===== 登记到 catalog（装包）· " + key + " =====")

        def job(emit):
            na = core("core.asset.new_asset")
            game_mod = core("core.assets.game")
            up = core("core.assets.unit_pack")
            rd = core("asset_kernel.reverse_delta")

            src = Path(bundle_path)
            landed = na.install_landing(game) / src.name

            # 1) 用户口径（2026-11-02，硬）：登记只认 BA_Mod 里**已经存在**的包。
            #    ⛔ 工具绝不复制 / 移动包 —— 用户原话「我要亲自将bundle包放进
            #    BA_Mod文件夹，绝不能让工具自己来！」。
            if not landed.is_file():
                emit("✗ 这个包不在 <游戏>\\BA_Mod：" + str(landed))
                emit("   ⛔ 工具不会自己复制包。请先自己把包放进 BA_Mod 文件夹，再点登记。")
                emit("   本次一个字节都没写。")
                return 2, {"ok": False, "reason": "包不在 BA_Mod，请先自己把包放进去"}
            emit("包已在 BA_Mod（不动它）：" + str(landed))

            catalog_path = game_mod.catalog_file(game)
            hash_path = game_mod.catalog_hash_file(game)
            # 1b) 写前先**真打开一次**判可写（不写一个字节）。不可写就别做完 2.7 MB 再死在最后一步。
            for target in (catalog_path, hash_path):
                if not self._can_write_file(target):
                    emit("✗ 写不进：" + str(target))
                    emit("   以 r+b 打开被拒（PermissionError）⇒ 权限不够。本次一个字节都没写。")
                    emit("   办法：右键 资产管理器.cmd → 以管理员身份运行一次；或先给该目录写权限。")
                    return 3, {"ok": False, "reason": "写不进 " + str(target)}
            # 2) 盘上现有哪些包（依赖只许指这些；2026-11-02 真机事故就是依赖指着已删的包）
            present = set()
            cbw = core("core.assets.catalog_bin_write")
            for folder in (na.install_landing(game),
                           game / "BrokenArrow_Data" / "StreamingAssets" / "aa" / "PC"):
                try:
                    if folder.is_dir():
                        present |= {p.name for p in folder.glob("*.bundle")}
                except OSError:
                    pass
            emit("盘上现有 .bundle：" + str(len(present)) + " 个（依赖只许指这些）")

            # 3) 只写 catalog + catalog.hash（包一个字节都不写）
            before = catalog_path.read_bytes()
            try:
                keys_now = {k.name for k in cbw.read_model(before).keys}
            except Exception:  # noqa: BLE001
                keys_now = set()
            actual, _data, _size = na.bundle_facts_of_file(str(landed))
            if key in keys_now:
                current_bundle = bundle_of_key(cbw, before, key)
                if current_bundle and current_bundle != landed.name:
                    # ★ 2026-11-03：产品操作集里没有「换」—— 换包 = 删除旧地址 + 新增（AGENTS §0.10）。
                    emit("catalog 里已有 key=" + key + "，但挂在旧包上：" + current_bundle)
                    emit("   ⇒ 拆成两步：【删除旧地址】→【新增新包 + 地址】")
                    after = cbw.delete_address(before, key=key).after
                    emit("   ① 旧地址已删除")
                    names_now = {k.name for k in cbw.read_model(after).keys}
                    if landed.name in names_now:
                        after = cbw.add_address(
                            after, key=key, asset_path=asset_path, bundle_name=landed.name,
                            resource_type=1, dependency_source_key=dep,
                            dependency_policy="auto").after
                    else:
                        after, _registration = na.add_catalog_entries(
                            after, key=key, asset_path=asset_path, facts=dict(actual),
                            kind="model", dependency_source_key=dep, dependency_policy="auto")
                    emit("   ② 新包已登记 + 地址已新增：" + landed.name)
                else:
                    emit("catalog 里已有 key=" + key + " ⇒ 不新增，改成【修依赖】")
                    after = before
            else:
                after, _registration = na.add_catalog_entries(
                    before, key=key, asset_path=asset_path, facts=dict(actual),
                    kind="model", dependency_source_key=dep, dependency_policy="auto")
                emit("已新增：包登记 + 地址 " + key)
            after, keep, dropped = repair_deps(cbw, after, key, landed.name, present)
            if dropped:
                emit("✗ 依赖里这些包盘上已不存在，已剔掉：" + ", ".join(dropped))
                emit("   保留 " + str(len(keep)) + " 条依赖（含 " + landed.name + "）")
            if after == before:
                emit("依赖本来就干净 —— 本次不动盘。")
                return 0, {"ok": True, "added": 0, "skipped": 1, "failed": 0}
            hash_before = hash_path.read_bytes() if hash_path.is_file() else b""
            hash_after = na.catalog_hash_bytes(after)

            store = rd.PatchStore(rd.patch_store_root(PATCH_PROJECT_ROOT), game_root=game)
            rec = store.begin("资产落包 " + key)
            rec.capture_file(catalog_path, before=before, after=after)
            rec.capture_file(hash_path, before=hash_before or None, after=hash_after)
            rec.commit()
            na._write_verified(catalog_path, after, what="catalog")
            na._write_verified(hash_path, hash_after, what="catalog.hash")
            # ★ 写后读回：磁盘上真的搜得到这条键才算登记成功（⛔ 不信内存里那份 after）。
            disk = catalog_path.read_bytes()
            if disk != after:
                raise RuntimeError("写后读回不一致：磁盘 %d B ≠ 预期 %d B" % (len(disk), len(after)))
            if key.encode("utf-8") not in disk:
                raise RuntimeError("写后读回：catalog 里搜不到 key=" + key)
            emit("✓ 写后读回：catalog 里已有 key=" + key + "（磁盘 " + str(len(disk)) + " B）")
            emit("✅ 只写 catalog + catalog.hash；包没动：" + landed.name)
            emit("   key=" + key + " → " + asset_path)
            emit("   包条目 internalId 指 ..\\..\\..\\BA_Mod\\" + landed.name)
            return 0, {"ok": True, "added": 1, "skipped": 0, "failed": 0}

        worker = CallWorker(job)
        worker.line.connect(self._say)
        worker.done.connect(self._on_done)
        self._worker = worker
        worker.start()

    def _on_done(self, code: int, payload: object) -> None:
        self._set_busy(False)
        data = payload if isinstance(payload, dict) else {}
        if code == 0:
            self._say("完成（退出码 0）：新增 " + str(data.get("added") or 0)
                      + " · 跳过 " + str(data.get("skipped") or 0)
                      + " · 失败 " + str(data.get("failed") or 0))
            return
        reason = str(data.get("reason") or "")
        self._say("✗ 结束（退出码 " + str(code) + "）：" + reason)
        self._say("   日志：" + str(LOG_DIR))
        # 失败必须**看得见**：pythonw 没有控制台，日志框会被关掉也会被忽略 —— 弹一次框。
        QtWidgets.QMessageBox.warning(self, "登记没有完成", reason + NL + NL + "日志目录：" + str(LOG_DIR))


class BuildPage(QtWidgets.QWidget):
    """页 2 · 造包：工程目录 ＋ 真件单位 -> 桩化 / job / Unity / report / 体检。"""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("page_build")
        self._busy = False
        self._worker = None
        self._tail_dir = ""
        self._tail_lines = 0
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tail_tick)
        self._build()

    def _build(self) -> None:
        outer = QtWidgets.QVBoxLayout(self)

        form = QtWidgets.QFormLayout()
        row_src = QtWidgets.QHBoxLayout()
        self.src_dir_edit = QtWidgets.QLineEdit(
            str(WORKSPACE / "断箭模型提取器" / "输出"))
        self.src_dir_edit.setObjectName("build_src_dir_edit")
        btn_src = QtWidgets.QPushButton("选目录…")
        btn_src.setObjectName("build_src_pick")
        btn_src.clicked.connect(self._pick_src)
        btn_refresh = QtWidgets.QPushButton("刷新工程")
        btn_refresh.setObjectName("build_refresh_button")
        btn_refresh.clicked.connect(self.reload_projects)
        row_src.addWidget(self.src_dir_edit, 1)
        row_src.addWidget(btn_src)
        row_src.addWidget(btn_refresh)
        form.addRow("工程目录", row_src)

        self.project_combo = QtWidgets.QComboBox()
        self.project_combo.setObjectName("build_project_combo")
        self.project_combo.currentTextChanged.connect(self._on_project)
        form.addRow("工程（子目录）", self.project_combo)


        self.bundle_out_edit = QtWidgets.QLineEdit(str(BUNDLE_OUT))
        self.bundle_out_edit.setObjectName("build_bundle_out_edit")
        form.addRow("产物目录（bundle）", self._dir_row(self.bundle_out_edit))

        self.job_root_edit = QtWidgets.QLineEdit(str(JOB_ROOT))
        self.job_root_edit.setObjectName("build_job_root_edit")
        form.addRow("临时目录（job）", self._dir_row(self.job_root_edit))

        # ★ 2026-10-30：**制造**（唯一入口）。留空 = 从零造一个 prefab（根名取工程子目录名），
        #   填了 = 在那个 prefab 上造（= 改装）。⛔ 不再另开「新建缺失节点」这种同级开关 ——
        #   缺的节点按名字建是**制造**的本分，一直开。判定仍在内核 unity_prefab.build_job。
        self.base_prefab_edit = QtWidgets.QLineEdit("")
        self.base_prefab_edit.setObjectName("build_base_prefab_edit")
        self.base_prefab_edit.setPlaceholderText(
            "留空 = 从零造（根名取上面那个工程子目录名）；填 Assets/ 下的路径 = 在它上面造")

        # ⛔ 2026-11-02 用户裁定：「高级」里的东西是废物功能，删掉。
        #   材质来源**全自动**：按工程名在导出工程里找唯一同名真件 prefab（find_reference_prefab）；
        #   找不到就**拒绝造包**（不会再静默出白模）。界面上不再有任何需要手填的内核参数。
        outer.addLayout(form)

        row_go = QtWidgets.QHBoxLayout()
        self.btn_run = QtWidgets.QPushButton("造包（起 Unity，等它退）")
        self.btn_run.setObjectName("build_run_button")
        self.btn_run.clicked.connect(lambda: self.start_build(job_only=False))
        self.btn_job_only = QtWidgets.QPushButton("只造 job（不启 Unity）")
        self.btn_job_only.setObjectName("build_job_only_button")
        self.btn_job_only.clicked.connect(lambda: self.start_build(job_only=True))
        btn_open = QtWidgets.QPushButton("打开产物目录")
        btn_open.setObjectName("build_open_dir_button")
        btn_open.clicked.connect(self._open_out_dir)
        row_go.addWidget(self.btn_run)
        row_go.addWidget(self.btn_job_only)
        row_go.addWidget(btn_open)
        row_go.addStretch(1)
        outer.addLayout(row_go)

        row_rep = QtWidgets.QHBoxLayout()
        self.report_edit = QtWidgets.QLineEdit("")
        self.report_edit.setObjectName("build_report_edit")
        self.report_edit.setPlaceholderText("留空 = 用本次 job 目录里的 report.json")
        btn_rep = QtWidgets.QPushButton("读 report 并体检")
        btn_rep.setObjectName("build_report_button")
        btn_rep.clicked.connect(self._on_report)
        row_rep.addWidget(self.report_edit, 1)
        row_rep.addWidget(btn_rep)
        outer.addLayout(row_rep)

        self.log = QtWidgets.QPlainTextEdit()
        self.log.setObjectName("build_log")
        self.log.setReadOnly(True)
        outer.addWidget(self.log, 1)

    def _say(self, text: str) -> None:
        self.log.appendPlainText(str(text).rstrip())
        self.log.verticalScrollBar().setValue(self.log.verticalScrollBar().maximum())

    def _dir_row(self, edit: QtWidgets.QLineEdit) -> QtWidgets.QWidget:
        """一格输入 ＋ 一个「选目录…」按钮（2026-10-30：用户要求产物/临时目录也能手选）。"""
        box = QtWidgets.QWidget()
        lay = QtWidgets.QHBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(edit, 1)
        btn = QtWidgets.QPushButton("选目录…")
        btn.setObjectName("build_pick_dir_button")
        btn.clicked.connect(lambda: self._pick_dir_into(edit))
        lay.addWidget(btn)
        return box

    def _pick_dir_into(self, edit: QtWidgets.QLineEdit) -> None:
        start = edit.text().strip() or str(load_ui_state().get("last_build_dir") or "")
        path = QtWidgets.QFileDialog.getExistingDirectory(self, "选目录", start)
        if path:
            edit.setText(path)
            remember(last_build_dir=str(path))

    def _pick_src(self) -> None:
        start = self.src_dir_edit.text().strip() or str(load_ui_state().get("last_src_dir") or "")
        path = QtWidgets.QFileDialog.getExistingDirectory(self, "选工程目录", start)
        if path:
            self.src_dir_edit.setText(path)
            remember(last_src_dir=str(path))
            self.reload_projects()

    def reload_projects(self) -> None:
        root = Path(self.src_dir_edit.text().strip())
        self.project_combo.clear()
        for item in scan_projects(root):
            label = item["name"] + ("" if item["mesh"] else "（没有网格）")
            self.project_combo.addItem(label, item["dir"])

    def _on_project(self, _text: str) -> None:
        """选工程时：若材质来源还空着，按 <工程名>.prefab 在导出工程里找一条自动填上。"""
        name = Path(str(self.project_combo.currentData() or "")).name
        if not name:
            return
        found = find_reference_prefab(name)
        self._say("材质来源：" + (found if found else "✗ 找不到 " + name + ".prefab（造包会被拒绝）"))

    def _open_out_dir(self) -> None:
        target = Path(self.bundle_out_edit.text().strip() or str(BUNDLE_OUT))
        if not target.is_dir():
            self._say("还没有产物目录：" + str(target))
            return
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(target)))

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        for button in (self.btn_run, self.btn_job_only):
            button.setEnabled(not busy)
        if busy:
            return
        self._timer.stop()

    def start_build(self, *, job_only: bool) -> None:
        if self._busy:
            return
        # ⚠ 造 job 要的是**工程目录本身**（里面才有 model.fbx / model.glb），
        #   不是它上面那层「工程目录」。选中的那个子目录就是它。
        project_dir = Path(str(self.project_combo.currentData() or ""))
        if str(project_dir) in ("", "."):
            self._say("✗ 先在「工程（子目录）」里选一个工程")
            return
        from_dir = str(project_dir)
        # ★ 2026-10-30：工程名 = 造出来的 prefab 根名；底包留空就从零造（根 = 这个名字）。
        base_unit = project_dir.name
        # ⛔ 2026-11-02 用户裁定：「改装」这条路不要了 —— 造包只有**从零造**一种。
        #   内核里 base_unit/prefab_path 那两个参数还在（CLI 老路），但 GUI **不给入口**。
        prefab_path = ""
        new_root = base_unit
        # ★ 2026-11-02：材质来源**自动解析**；解析不出来就拒绝造包（⛔ 不再静默出白模）。
        material_source = ""
        if not prefab_path:
            material_source = find_reference_prefab(base_unit)
            if material_source:
                self._say("材质来源：" + material_source)
        if not material_source and not prefab_path:
            self._say("✗ 解析不出材质来源：导出工程里没有唯一的 " + base_unit + ".prefab")
            self._say("   判据：ModelPrefabs 下 <工程名>.prefab 要恰好 1 个（0 个 = 工程名与 prefab 不同名；"
                       "> 1 个 = 重名，本页不给手填入口）。")
            self._say("   ⛔ 不给材质来源造出来是白模（kernel/build_pipeline.py:140-142）。")
            return
        stamp = QtCore.QDateTime.currentDateTime().toString("yyyyMMdd-hhmmss")
        out_dir = Path(self.job_root_edit.text().strip() or str(JOB_ROOT)) / (
            Path(from_dir).name + "-" + stamp)
        # ⛔ base_unit 是**改装**的输入：底包留空时绝不能把它当单位键传下去，
        #   否则内核会走 find_prefab 找现成 prefab（2026-10-30 实测：正是这一条把从零造顶回改装）。
        params = {"from_dir": from_dir,
                  "base_unit": ("" if new_root else base_unit),
                  "out_dir": str(out_dir),
                  "bundle_out": self.bundle_out_edit.text().strip() or str(BUNDLE_OUT),
                  "job_only": bool(job_only),
                  "prefab_path": prefab_path,
                  "new_root": new_root,
                  "material_source": material_source,
                  "create_missing": True}
        self._set_busy(True)
        self._say("===== 制造 · " + base_unit
                  + (" · 底包 " + prefab_path if prefab_path else " · 从零造")
                  + (" · 只造 job" if job_only else ""))
        worker = BuildWorker(params)
        worker.line.connect(self._say)
        worker.unity_started.connect(self._watch_unity)
        worker.done.connect(self._on_build_done)
        self._worker = worker
        worker.start()

    def _watch_unity(self, job: str) -> None:
        """Unity 自己的日志（`-logFile` 写的那份）增量贴到界面 —— 起 Unity 与判定都在内核里。"""
        self._tail_dir = str(Path(job).parent) if job else ""
        self._tail_lines = 0
        if self._tail_dir:
            self._timer.start()

    def _tail_tick(self) -> None:
        if not self._tail_dir:
            return
        files = sorted(Path(self._tail_dir).glob("unity-*.log"))
        if not files:
            return
        try:
            text = files[-1].read_text(encoding="utf-8", errors="replace")
        except OSError:
            return
        rows = text.replace(chr(13), "").split(NL)
        fresh = rows[self._tail_lines:]
        self._tail_lines = len(rows)
        for row in fresh:
            if row.strip():
                self._say("   [unity] " + row)

    def _on_build_done(self, code: int, payload: object) -> None:
        self._set_busy(False)
        data = payload if isinstance(payload, dict) else {}
        if code == 0:
            self._say("完成（退出码 0）· 包 " + str(data.get("bundle") or "（只造 job）"))
            return
        self._say("✗ 结束（退出码 " + str(code) + "）：" + str(data.get("reason") or ""))

    def _on_report(self) -> None:
        if self._busy:
            return
        path = self.report_edit.text().strip()
        if not path:
            self._say("✗ 先把 report.json 的路径填进来")
            return
        self._say("===== 读 report ＋ 体检 · " + path + " =====")
        result = read_report_and_check(report_path=path, emit=self._say)
        self._say("完成" if result.get("ok") else "✗ 体检有红 / 读不出来")


class MainWindow(QtWidgets.QMainWindow):
    """两页：看包 / 造包。判定都在产品内核里，这里只摆控件与转述。"""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("断箭资产管理器")
        self.resize(1360, 880)
        self.setStyleSheet(QSS)
        self.page_browse = BrowsePage()
        self.page_build = BuildPage()
        self.tabs = QtWidgets.QTabWidget()
        self.tabs.setObjectName("main_tabs")
        self.tabs.addTab(self.page_browse, "看包")
        self.tabs.addTab(self.page_build, "造包")
        # 2026-11-03：撤装页删掉后 `_on_tab` 也不再需要（它只负责切到撤装页时 reload）。
        #   ⛔ 这一行曾经留着 → MainWindow 构造时 AttributeError → 双击 .pyw 静默打不开。
        self.setCentralWidget(self.tabs)
        self.statusBar().showMessage("就绪 · 补丁柜 " + str(patch_cabinet_root()))
        self.page_build.reload_projects()
        # 恢复上次的窗口几何 / 停在哪一页（用户 2026-11-02：每次点一遍最大化很烦）
        state = load_ui_state()
        geo = state.get("geometry")
        if isinstance(geo, str) and geo and not state.get("maximized"):
            try:
                self.restoreGeometry(QtCore.QByteArray.fromHex(geo.encode("ascii")))
            except Exception:  # noqa: BLE001 -- 几何恢复失败就用默认尺寸
                pass
        tab = state.get("tab")
        if isinstance(tab, int) and 0 <= tab < self.tabs.count():
            self.tabs.setCurrentIndex(tab)

    def closeEvent(self, event) -> None:
        """关窗时记住：最大化 / 几何 / 停在哪一页。"""
        state = load_ui_state()
        state["maximized"] = bool(self.windowState() & QtCore.Qt.WindowState.WindowMaximized)
        try:
            state["geometry"] = bytes(self.saveGeometry().toHex()).decode("ascii")
        except Exception:  # noqa: BLE001
            pass
        state["tab"] = int(self.tabs.currentIndex())
        save_ui_state(state)
        super().closeEvent(event)




def main() -> int:
    _log_startup()
    if _relaunch_at_medium():
        return 0
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication(sys.argv)
    window = MainWindow()
    if load_ui_state().get("maximized"):
        window.showMaximized()
    else:
        window.show()
    return int(app.exec())


if __name__ == "__main__":
    raise SystemExit(main())
