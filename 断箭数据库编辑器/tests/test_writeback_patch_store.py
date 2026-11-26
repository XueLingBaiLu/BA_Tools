r"""④ 写回 / ③ 涂装 / ⑤ 网格 / ⑦ 装机 的**补丁柜接线**（E4）机械判据 —— 新家。

## 这条规矩是什么（照抄旧用例，⛔ 不是我新编的）

用户原话（口径）：「**不要帮我手动备份，会生成很多的垃圾备份文件占用我的硬盘空间**」
⇒ 真写回只许走 **ADR 0007 的反向 Δ 补丁柜**（<工程目录>/patches，只存被改块的原始字节），
⛔ **不许拷整包当备份**。

## 现场（可复算，2026-10-12 实测）

Blender 插件 ④ 改 · 外观 -> 材质 RU_T72B 1 -> 参数 -> 新值 0.50 -> 「写回这个参数」：

| 读数 | 值 |
|---|---|
| 目标 | <游戏根>/.../aa/PC/units_assets_all_1e6c04ce….bundle |
| 改前 sha256 | 60097a3c…f57c8（3,381,248,409 B） |
| 控制台三行 | `[干跑] 耗时 4.2s` / `[干跑] 4.4s` / **`[已写回] 耗时 73.6s`** |
| 磁盘代价 | **+3,381,248,409 B**（<工程目录>\\backup\\*.bak） |

根因：旧 `operators.py` 里那三条写回**没把 patch_store 交给 writer** ⇒
`writer._write_bundle` 走 `if patch_store is None` 那条旧链（`shutil.copy2` 整包）。

## 新家（2026-10-15 移植）

* 写回器：`asset_kernel/writer.py` —— `_write_bundle` 的两个分支**今天还在原地**
  （`patch_store is not None` -> 反向 Δ；`is None` -> `shutil.copy2` 整包）；
* 接线点：**全产品**（`src/**` ＋ `blender_addon/**`）每一条对 kernel 写入口的调用
  —— 旧用例只扫 `operators.py` 一个文件，那份文件已经删了；
* 装机那条：`core/asset/new_asset.py::run_add_bundle` 自己造 `PatchStore`；
  ⑦ 装机面板 `blender_addon/panels_07_install.py::_capture` 只是**转发**到它
  （⛔ 面板里没有第二份写回实现 —— 逻辑只有一份，AGENTS §5）。

## 判据一行

`pytest tests/test_writeback_patch_store.py -q -n 0` -> **11 passed**；
**负控**：把任一处 `patch_store=` 拿掉（或 `**common` 里那个键删掉）⇒ 第 3 条必须变红；
把 `shutil.copy2` 挪到 `if patch_store is None` **外面** ⇒ 第 7 条必须变红。
两者都在**源码串**上做（⛔ 不碰盘上的产品）—— 与旧用例 `test_ruler_can_go_red` 同一手法。

⛔ 本文件不读游戏盘、不写盘（补丁柜那几条只用 tmp_path 断言「构造时不建目录」）。
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from ba_mod_maker.asset_kernel import reverse_delta, writer

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
ADDON = REPO_ROOT / "blender_addon"

#: kernel 里**会让 writer 走「拷整包」那条旧链**的写入口（一个都不能漏）。
KERNEL_WRITES = frozenset({
    "write_delta",
    "write_objects",
    "write_stream",
    "write_removals",
    "_write_bundle",
})


# --------------------------------------------------------------------------- #
# 尺子（纯函数：喂源码 -> 报违规点）—— 负控直接喂它
# --------------------------------------------------------------------------- #
def _carriers(func: ast.AST) -> set[str]:
    """这个函数作用域里，哪些**字典变量**装着 `patch_store` 键（`**common` 那种）。"""
    names: set[str] = set()
    for node in ast.walk(func):
        if isinstance(node, ast.Assign):
            value, targets = node.value, list(node.targets)
        elif isinstance(node, ast.AnnAssign):
            # `common: dict[str, Any] = {...}` —— 带注解那种（产品的三条主路都是它）
            value, targets = node.value, [node.target]
        else:
            continue
        if not isinstance(value, ast.Dict):
            continue
        keys = {
            key.value
            for key in value.keys
            if isinstance(key, ast.Constant) and isinstance(key.value, str)
        }
        if "patch_store" not in keys:
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                names.add(target.id)
    return names


def _missing_patch_store(source: str) -> list[str]:
    """扫出「调了 kernel 写入口却没带补丁柜」的位置（函数::行 + 名字）。

    认两种携带方式（产品里就这两种）：
      `patch_store=<...>` 显式关键字；
      `**common`，而 `common` 在本函数里是个带 `"patch_store"` 键的字典字面量。
    """
    tree = ast.parse(source)
    out: list[str] = []
    funcs = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    for func in funcs:
        carriers = _carriers(func)
        for node in ast.walk(func):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name not in KERNEL_WRITES:
                continue
            if any(kw.arg == "patch_store" for kw in node.keywords):
                continue
            if any(
                kw.arg == "dry_run"
                and isinstance(kw.value, ast.Constant)
                and kw.value.value is True
                for kw in node.keywords
            ):
                continue  # 写死的干跑那一档：不写盘 ⇒ ⛔ 不需要补丁柜
            unpacked = [
                kw.value.id
                for kw in node.keywords
                if kw.arg is None and isinstance(kw.value, ast.Name)
            ]
            if any(item in carriers for item in unpacked):
                continue
            out.append(f"{func.name}@{node.lineno} 调 {name} 但没带 patch_store=")
    return out


def _is_patch_store_none(test: ast.expr) -> bool:
    """`patch_store is None` 这个判断式。"""
    return (
        isinstance(test, ast.Compare)
        and isinstance(test.left, ast.Name)
        and test.left.id == "patch_store"
        and any(isinstance(op, ast.Is) for op in test.ops)
        and any(isinstance(c, ast.Constant) and c.value is None for c in test.comparators)
    )


def _bak_path_sites(source: str) -> tuple[list[str], list[str]]:
    """全部「拼一个 `*.bak` 备份路径」的位置 ->（在 `patch_store is None` 里的, 在外的）。

    用户口径就是「不要帮我手动备份，会生成很多的垃圾备份文件占用我的硬盘空间」——
    所以量的正是那个**动作**：`target_dir / f"{bundle.name}.{stamp}.bak"`。

    ⚠ 只认 f-string（`JoinedStr`）：普通字符串里的 `.bak` 是说明文字，不是动作。
    """
    inside: list[str] = []
    outside: list[str] = []

    def builds_a_backup_path(node: ast.AST) -> bool:
        return isinstance(node, ast.JoinedStr) and any(
            isinstance(part, ast.Constant)
            and isinstance(part.value, str)
            and ".bak" in part.value
            for part in ast.walk(node)
        )

    def walk(node: ast.AST, guarded: bool) -> None:
        for child in ast.iter_child_nodes(node):
            nested = guarded
            if isinstance(child, ast.If) and _is_patch_store_none(child.test):
                nested = True
            if builds_a_backup_path(child):
                (inside if guarded else outside).append(f"bak@{child.lineno}")
                continue  # 同一个 f-string 的内部常量不再数第二遍
            walk(child, nested)

    walk(ast.parse(source), False)
    return inside, outside


def _write_call_sites(source: str) -> list[str]:
    """全部对 kernel 写入口的调用（**不看**补丁柜）—— 给「尺子不能恒真」那条用。"""
    out: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        if name in KERNEL_WRITES:
            out.append(f"{name}@{node.lineno}")
    return out


def _production_files() -> list[Path]:
    """产品源码（`src/**` ＋ `blender_addon/**`）—— 排除 `__pycache__` 与随包 vendored 内核。"""
    files: list[Path] = []
    for root in (SRC, ADDON):
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" in path.parts or "_kernel" in path.parts:
                continue
            files.append(path)
    return files


# --------------------------------------------------------------------------- #
# 1 · 负控（尺子能红）：在源码串上植入违规
# --------------------------------------------------------------------------- #
def test_ruler_can_go_red() -> None:
    """★ 负控：把 ④ 那一处的 `patch_store=store,` 拿掉，尺子必须认出它。"""
    source = (SRC / "ba_mod_maker" / "asset_kernel" / "skin_install.py").read_text(encoding="utf-8")
    assert _missing_patch_store(source) == [], "起点就不干净：先看下面那条全扫"

    planted = source.replace("        patch_store=store,\n", "", 1)
    assert planted != source, "夹具没生效：源码里找不到那一行 —— 尺子等于没测"
    found = _missing_patch_store(planted)
    assert len(found) == 1, found
    assert found[0].startswith("install_swap@"), found


def test_ruler_also_catches_a_dropped_dict_key() -> None:
    """★ 负控之二：`**common` 那条路也要能红（把字典里那个键删掉）。"""
    source = (SRC / "ba_mod_maker" / "asset_kernel" / "prefab_skins.py").read_text(encoding="utf-8")
    assert _missing_patch_store(source) == []
    planted = source.replace('        "patch_store": store,\n', "", 1)
    assert planted != source, "夹具没生效：找不到 `\"patch_store\": store,` 那一行"
    found = _missing_patch_store(planted)
    assert len(found) == 2, found  # write_delta ＋ write_objects 两处都靠那个字典
    callees = {item.split(" 调 ")[1].split(" ")[0] for item in found}
    assert callees == {"write_delta", "write_objects"}, callees


def test_backup_path_scanner_can_go_red() -> None:
    """★ 负控之三：备份路径挪到 `if patch_store is None` **外面**必须被认出来。"""
    planted = (
        "def f(d, bundle):\n"
        "    if patch_store is None:\n"
        "        backup = d / f'{bundle.name}.bak'\n"
        "    other = d / f'{bundle.name}.bak'\n"
    )
    inside, outside = _bak_path_sites(planted)
    assert inside == ["bak@3"], inside
    assert outside == ["bak@4"], outside


# --------------------------------------------------------------------------- #
# 2 · 规矩：全产品扫一遍
# --------------------------------------------------------------------------- #
def test_no_production_write_forgets_the_patch_store() -> None:
    """产品里每一条 kernel 写入口调用都必须带补丁柜（⛔ 漏一条就是 +3.4 GB 的旧链）。"""
    files = _production_files()
    assert len(files) > 50, f"只扫到 {len(files)} 个文件 —— 范围不对，这条等于没测"
    bad: list[str] = []
    total = 0
    for path in files:
        text = path.read_text(encoding="utf-8")
        total += len(_write_call_sites(text))
        for item in _missing_patch_store(text):
            bad.append(f"{path.relative_to(REPO_ROOT)}::{item}")
    # ⛔ 尺子不能恒真：真扫过一批调用点，上面那句 `bad == []` 才有意义。
    assert total >= 12, f"只扫到 {total} 处写入口调用 —— 范围/名字漂了，这条等于没测"
    assert bad == [], bad


def test_every_kernel_write_entry_takes_a_patch_store() -> None:
    """写入口自己也得收得下 —— 谁把形参删了，上面那条全扫会**静默**失效。"""
    for name in ("write_delta", "write_objects", "write_stream", "write_removals"):
        params = inspect.signature(getattr(writer, name)).parameters
        assert "patch_store" in params, f"writer.{name} 收不到补丁柜"
        assert params["patch_store"].kind is inspect.Parameter.KEYWORD_ONLY, name
    assert "patch_store" in inspect.signature(writer._write_bundle).parameters


def test_the_backup_path_is_built_only_in_the_fallback() -> None:
    """建备份路径那两处只许待在 `if patch_store is None:` 里（尺子两面都量）。"""
    inside, outside = _bak_path_sites(
        (SRC / "ba_mod_maker" / "asset_kernel" / "writer.py").read_text(encoding="utf-8")
    )
    assert outside == [], f"备份路径溜出补丁柜分支了：{outside}"
    assert len(inside) >= 2, "一处都没有？那上面那条 assert 就是恒真 —— 尺子要重做"


def test_the_install_chain_builds_its_own_patch_store() -> None:
    """⑦ 装机那条链（面板 -> new_asset）自己造补丁柜；⛔ 它不许拷整包。"""
    source = (SRC / "ba_mod_maker" / "core" / "asset" / "new_asset.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    wanted = {}
    for node in ast.walk(tree):
        wanted_names = {"run_add_bundle", "run_add_bundle_dry"}
        if isinstance(node, ast.FunctionDef) and node.name in wanted_names:
            wanted[node.name] = {
                getattr(call.func, "attr", None) or getattr(call.func, "id", None)
                for call in ast.walk(node)
                if isinstance(call, ast.Call)
            }
    assert set(wanted) == {"run_add_bundle", "run_add_bundle_dry"}, sorted(wanted)
    # 真写那一档：必须造补丁柜（尺子的正面）
    assert "PatchStore" in wanted["run_add_bundle"], "装机真写没造补丁柜 ⇒ 落回拷整包那条旧链"
    # 干跑那一档：⛔ 不许造补丁柜（尺子的反面 —— 这条红了说明干跑开始写盘了）
    assert "PatchStore" not in wanted["run_add_bundle_dry"], "干跑居然造补丁柜"
    _inside, outside = _bak_path_sites(source)
    assert outside == [], outside
    assert _inside == [], "new_asset 自己造备份路径 ⇒ 补丁柜不再是唯一退路"


# --------------------------------------------------------------------------- #
# 3 · 补丁柜的落点与红线（真跑 reverse_delta，不写盘）
# --------------------------------------------------------------------------- #
_REPO_DRIVE = REPO_ROOT.drive.upper()


def test_patch_store_follows_the_project_root(tmp_path: Path) -> None:
    """补丁柜落 <工程目录>/patches（跟随工程目录，不是 cwd、不是游戏目录）。"""
    root = reverse_delta.patch_store_root(tmp_path)
    assert root == tmp_path / "patches"
    store = reverse_delta.PatchStore(root, game_root=tmp_path)
    assert store.root == tmp_path / "patches"
    assert store.game_root == tmp_path


def test_patch_store_refuses_a_project_root_on_another_drive(tmp_path: Path) -> None:
    """负控（红线）：补丁柜跨盘必须抛 —— 历史教训是备份落 C 盘白占 6.95 GB。"""
    other = "Z:" if _REPO_DRIVE != "Z:" else "Y:"
    with pytest.raises(Exception, match="盘"):
        reverse_delta.PatchStore(
            Path(other) / "somewhere" / "patches", game_root=REPO_ROOT
        )


def test_constructing_the_store_writes_nothing(tmp_path: Path) -> None:
    """干跑安全：`PatchStore.__init__` 只校验所在盘，⛔ 不建目录、不写盘。"""
    store = reverse_delta.PatchStore(
        reverse_delta.patch_store_root(tmp_path), game_root=tmp_path
    )
    assert not (tmp_path / "patches").exists()
    assert store.has_pending() is False
