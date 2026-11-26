"""游戏安装目录的探测。

图标要从游戏自己的 bundle 里读（见 docs/设计稿-图标管线.png），
所以第一步是找到游戏在哪。**找不到不是错误**——界面必须照常能用，只是回落成剪影。
"""

from __future__ import annotations

import os
from pathlib import Path

#: 环境变量覆盖，优先级最高（多份安装 / 非默认盘符）。
ENV_VAR = "BA_MOD_MAKER_GAME"

#: 游戏根目录候选。顺序即探测顺序。
CANDIDATES: tuple[Path, ...] = (
    Path(r"D:\Steam\steamapps\common\broken_arrow"),
    Path(r"C:\Program Files (x86)\Steam\steamapps\common\broken_arrow"),
    Path(r"D:\SteamLibrary\steamapps\common\broken_arrow"),
    Path(r"E:\SteamLibrary\steamapps\common\broken_arrow"),
    Path(r"F:\SteamLibrary\steamapps\common\broken_arrow"),
)

#: Addressables 的 bundle 目录（相对游戏根）。
AA_SUBDIR = Path("BrokenArrow_Data") / "StreamingAssets" / "aa" / "PC"

#: 认定一个目录是不是游戏根：看 catalog 在不在。
#: ⚠️ catalog 在 aa/ 下，不在 aa/PC/ 下——两个位置别搞混（实测踩过）。
#:
#: ★ 2026-10-13（1.2.1 openbeta）改口径：Addressables 从 JSON 明文换成了**二进制 catalog.bin**
#:   （同目录还有 catalog.hash / settings.json，settings 里 m_AddressablesVersion = 2.9.1）。
#:   只认 catalog.json 会让 find_game_root() 在**装了新游戏的机器上**返回 None ——
#:   实测症状：tools/game_log.py --new-only 报「找不到游戏目录」，而 GameLogs/ 明明在。
#:   ⇒ 两个名字都认；谁在盘上用谁（见 catalog_file）。
CATALOG_JSON_NAME = "catalog.json"
CATALOG_BIN_NAME = "catalog.bin"
CATALOG_NAMES: tuple[str, ...] = (CATALOG_JSON_NAME, CATALOG_BIN_NAME)

#: 旧名保留（有代码/文档按这个名字引用它）。
_MARKER = Path("BrokenArrow_Data") / "StreamingAssets" / "aa" / CATALOG_JSON_NAME


def aa_dir(game_root: Path) -> Path:
    """游戏根 -> aa/（catalog 与 bundle 目录的父层）。"""
    return game_root / "BrokenArrow_Data" / "StreamingAssets" / "aa"


def is_game_root(path: Path) -> bool:
    """这个目录是不是游戏根（bundle 目录 + catalog 都在）。

    catalog **两个名字都算**：1.2.0 及以前是 catalog.json，1.2.1 起是 catalog.bin。
    """
    if not path.is_dir():
        return False
    aa = aa_dir(path)
    return any((aa / name).is_file() for name in CATALOG_NAMES)


def find_game_root() -> Path | None:
    """定位游戏安装目录。找不到返回 None（**不是错误**）。"""
    override = os.environ.get(ENV_VAR)
    if override:
        candidate = Path(override)
        return candidate if is_game_root(candidate) else None
    for candidate in CANDIDATES:
        if is_game_root(candidate):
            return candidate
    return None


#: catalog.json 所在目录（相对游戏根）。**比 bundle 目录高一层**。
CATALOG_SUBDIR = Path("BrokenArrow_Data") / "StreamingAssets" / "aa"


def bundle_dir(game_root: Path) -> Path:
    """游戏根 -> bundle 目录（aa/PC）。"""
    return game_root / AA_SUBDIR


def resolve_bundle_path(game_root: Path, value: str | None) -> Path | None:
    """catalog 里的 bundle 字段 -> **盘上真路径**（找不到返回 None，⛔ 不猜）。

    ★ 2026-10-20（用户需求：**要从我们新造的包里提取模型**）：
    我们自己装机时把 catalog 的包路径写成 `<aa/PC>/../../../BA_Mod/<包>`；
    没装机时它就是裸文件名（原版包在 `aa\\PC`）。两种都要能定位：

    1. 含路径分隔符 / 以 `..` 开头 ⇒ **相对 `aa\\PC` 解析**（我们装机写的就是这个形状），
       也接受绝对路径；
    2. 裸文件名 ⇒ 先 `aa\\PC`（原版），再 `aa`；
    3. 都找不到 ⇒ None（调用方自己报错）。
    """
    text = str(value or "").strip().replace("\\", "/")
    if not text:
        return None
    aa = game_root / CATALOG_SUBDIR
    pc = aa / "PC"
    if "/" in text:
        # ⚠ 我们装机写的形状是 **相对 `aa`**（`<aa>\..\..\..\BA_Mod\<包>`，
        #   三级正好回到游戏根）—— 先按 `aa` 解，再按 `aa/PC` 兜一次，最后才是绝对路径。
        candidates = [(aa / text), (pc / text), Path(text)]
        for cand in candidates:
            try:
                if cand.is_file():
                    return cand.resolve()
            except OSError:
                continue
    name = text.rsplit("/", 1)[-1]
    for folder in (pc, aa):
        cand = folder / name
        if cand.is_file():
            return cand.resolve()
    return None

def original_bundle_name(value: str | None) -> str:
    """catalog 里的 bundle 字段 -> **原版包的文件名**（纯函数，零 IO）。

    ★ 2026-10-20（用户报「提取器有时候会从我们新造的 bundle 里提取模型」）：
    我们自己装机时会把 catalog 的包路径改写成 `..\\..\\..\\BA_Mod\\<包>`
    （STATUS 2026-10-19 20:0x 那条 B 方案）—— 提取器若照字面跟过去，
    导出的"游戏原版"其实是我们改过的包。

    ⇒ 这里统一**砍成文件名**：上层只在 `aa\\PC` 里按同名找，天然只拿原版；
    我们"新增"的资产本来就没有原版同名件 ⇒ 找不到就报错（⛔ 不猜、不跟去 BA_Mod）。
    """
    text = str(value or "").strip().replace("\\", "/")
    if not text:
        return ""
    return text.rsplit("/", 1)[-1]

def catalog_file(game_root: Path) -> Path:
    """游戏根 -> **盘上那份** catalog。

    ⚠️ 它在 aa/ 下，**不在 aa/PC/ 下**——bundle 与 catalog 不同层，实测踩过一次。

    1.2.0 及以前是 `catalog.json`（JSON 明文）；**1.2.1 起是 `catalog.bin`**（二进制）。
    两个都在时**优先 JSON**（那是我们能逐字节读写的那个形状）。
    ⛔ 本函数只回答"哪一个是它"，**不回答"我们能不能读它"** —— 二进制那份目前
    `AddressablesCatalog.load()` 还读不动（见 `core/assets/catalog.py` 与 kb/19）。
    """
    aa = game_root / CATALOG_SUBDIR
    for name in CATALOG_NAMES:
        candidate = aa / name
        if candidate.is_file():
            return candidate
    return aa / CATALOG_JSON_NAME


def catalog_json_file(game_root: Path) -> Path | None:
    """游戏根 -> 盘上那份 **JSON** catalog（`aa/catalog.json`）；没有就是 None。

    ⚠ 与 :func:`catalog_file` 的分工（别混用，2026-10-18 定）：

    * :func:`catalog_file` 回答「**盘上那份 catalog 是哪个**」—— 1.2.1 起是 `catalog.bin`；
    * 本函数回答「**还有没有 JSON 那一份**」—— 只有 ≤1.2.0 的游戏才有。**JSON 全结构编辑**
      （`asset_kernel/catalog_edit.py::CatalogEdit`，按三张 base64 表逐字节往返 + 重编号）
      只能吃这一份；把 `catalog.bin` 喂进去会抛 `AssetError`（2026-10-18 起；以前是裸
      `UnicodeDecodeError 0x89`），并指路到 `catalog_bin_write`。

    ⇒ 想要「能逐字节读写的那份」的调用方（真件用例 / 编辑入口）应该问本函数，**找不到就如实
    skip / 报不支持**，而不是拿 `catalog_file()` 的结果去试 —— 试出来的是一条假红
    （2026-10-17 11:30 游戏更新后，`tests/test_catalog_edit.py` 那 15 条真件用例就是这么红的）。

    1.2.1 的等价写侧是 `core/assets/catalog_bin_write.py`。
    """
    candidate = game_root / CATALOG_SUBDIR / CATALOG_JSON_NAME
    return candidate if candidate.is_file() else None


#: catalog 的**哈希**文件名（与 catalog 同一个目录；内容是 32 个 ASCII 字符、**无换行**）。
CATALOG_HASH_NAME = "catalog.hash"


def catalog_hash_file(game_root: Path) -> Path:
    """游戏根 -> `aa/catalog.hash`（catalog 的 Hash128 落在哪）。

    ⚠ 三点与 :func:`catalog_file` 不同（照它写、但别照它的名字想当然）：
    1. 它**不跟着 catalog 换名字** —— 1.2.1 把 catalog 从 `catalog.json` 换成 `catalog.bin`，
       而同一目录下那份哈希仍然叫 `catalog.hash`（1.2.1 现场实测就是这个组合）；
    2. 它与 catalog **同层**（都在 `aa/`），**不在** `aa/PC/`；
    3. 本函数只回答"它在哪"，**不回答盘上有没有** —— 调用方自己 `is_file()`。
    """
    return game_root / CATALOG_SUBDIR / CATALOG_HASH_NAME


# --------------------------------------------------------------------------- #
# 数据库通道（外部 BA_Mod.zip / 编译库 data.unity3d）
# --------------------------------------------------------------------------- #

#: 数据库容器相对游戏根的位置。
DATA_SUBDIR = Path("BrokenArrow_Data") / "data.unity3d"

#: 游戏主程序名。用于第二道闸（写入前检测游戏是否在跑）。
GAME_EXE = "BrokenArrow.exe"


def is_game_root_for_db(path: Path) -> bool:
    """这个目录能不能当"数据库通道的游戏根"。

    判据比 :func:`is_game_root` **宽**：只要有 `BrokenArrow_Data/data.unity3d` 就行。
    理由：数据库通道不需要 bundle 与 catalog，用了另一套判据去要求它们是错的
    （实测本机两者都存在，但没装素材的安装形态不该被这条挡住）。
    """
    return path.is_dir() and (path / DATA_SUBDIR).is_file()


def find_game_root_for_db() -> Path | None:
    """定位数据库通道要用的游戏根。找不到返回 `None`（**不是错误**，由调用方弹选择框）。"""
    override = os.environ.get(ENV_VAR)
    if override:
        candidate = Path(override)
        return candidate if is_game_root_for_db(candidate) else None
    for candidate in CANDIDATES:
        if is_game_root_for_db(candidate):
            return candidate
    return None


def data_file(game_root: Path) -> Path:
    """游戏根 -> `BrokenArrow_Data/data.unity3d`。"""
    return game_root / DATA_SUBDIR


def is_game_running() -> bool:
    """游戏进程是否在跑 —— 第二道闸。

    ⚠️ **只拦写，不拦读**。实测（2026-09）：
    游戏占着 `data.unity3d` 时，**读仍然成功**（0.12 秒读出对象头），
    但 `os.replace` 覆盖会以 `WinError 5 拒绝访问` 失败。
    所以"开着游戏照样能打开看数据、导出 BA_Mod.zip"，只有写回那一步必须关游戏。
    """
    if os.name != "nt":
        return False
    import subprocess

    try:
        done = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {GAME_EXE}", "/NH"],
            capture_output=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        # 查不到就当没在跑，交给真正的写入去失败（错误信息更准确）。
        return False
    return GAME_EXE.lower() in decode_console_output(done.stdout or b"").lower()


def decode_console_output(raw: bytes) -> str:
    """子进程输出 -> str：**先按 UTF-8 解，解不动退 OEM 码页**（⛔ 不许用 `text=True`）。

    2026-10-13 真机踩到（用户点 ④「写回这个参数」报
    `AttributeError: 'NoneType' object has no attribute 'lower'`）：
    中文 Windows 上 `tasklist` 按 **OEM 码页（cp936）** 输出，
    而 **Blender 5.2 的 Python 按 UTF-8 解**
    ⇒ `subprocess` 的读线程抛 `UnicodeDecodeError`、`stdout` 变成 **None** ⇒
    下一行 `.lower()` 炸 ⇒ **整条写回链在"游戏在不在跑"这一步就断了**。
    这里只做 ASCII 子串查找（进程名），退化解码完全够用。
    """
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("mbcs" if os.name == "nt" else "latin-1", "replace")
