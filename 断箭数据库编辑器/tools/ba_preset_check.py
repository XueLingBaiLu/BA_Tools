r"""tools/ba_preset_check.py —— 「断箭 Mod 开发」预设的机械体检尺子（v4，2026-11-22）。

背景：2026-11-22 用户当场定「把断箭Mod开发预设的所有闸全部删掉，我只需要它做到一条，
就是严禁有『猜』的行为」。v4 把预设里所有闸删净、只留 persona 一条「严禁猜」。
旧版（v3）的体检是查「闸在不在」；这一版**反过来查** —— 查闸有没有被悄悄加回来：

    ① persona：必须能提取，且「严禁猜」「未验证」等锚点在（丢掉 = 红）；
    ② 闸不许回来（全部只读，不跑产品、不起游戏）：
       · 插件行里没有 `ba-mode`（闸的 Host 插件）；bundle 目录里没有 index.js / selftest.mjs；
       · 预设配置里没有 `gateRoots` / `tools.guard`；
       · 去注释后的预设文本里没有 `ba_boot` / `ba_gate`（这两个工具已不注册）；
    ③ persona 里引用的路径类 token 逐条查存在（kb/NN 编号 · tools/xxx.py · docs/xxx ·
       _work/xxx · 工作区相对路径 · `D:\` 绝对路径）—— 散文过期 = 红；
       明确写了「不存在 / 没有」的 token 走白名单。
    ④ bundle 清单：只该有 `cordis.patch.yml` ＋ `package.json`；
       package.json 必须有 `dsh.bundle.patch` 指向 `./cordis.patch.yml`。

判据：
    rc=0 = 全绿；rc=1 = 有红（逐条印「哪条红、怎么复算」）。
    `--strict` 把「黄」也升级成红（默认黄只提示）。
    `--json` 机读；`--selftest` 负控（假夹具里塞不存在的 kb / 工具 / 把闸加回来 ⇒ 必须红）。

用法::

    .venv\Scripts\python.exe tools\ba_preset_check.py                # 人类可读
    .venv\Scripts\python.exe tools\ba_preset_check.py --strict --json
    .venv\Scripts\python.exe tools\ba_preset_check.py --selftest     # 负控必须真命中
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path

# 工作根：数据已移出仓（2026-11-09，统一入口）。
# 默认 D:\断箭模组制作\_work_db；换机器/换位置用环境变量 BA_WORK_ROOT 覆盖。
WORK = Path(os.environ.get("BA_WORK_ROOT") or r"D:\断箭模组制作\_work_db")

TOOLS = Path(__file__).resolve().parent
PROD = TOOLS.parent
ROOT = PROD.parent
PLUGIN_DIR = TOOLS / "dsh-plugin-ba-mode"
PATCH = PLUGIN_DIR / "cordis.patch.yml"
PACKAGE_JSON = PLUGIN_DIR / "package.json"

#: bundle 里**该有**的文件（v4 只有预设声明；旧版 index.js / selftest.mjs 已删）。
ALLOWED_BUNDLE_FILES = {"cordis.patch.yml", "package.json"}
#: 闸实现的文件 —— 只要回来一条就红（「全部删掉」的机械判据）。
FORBIDDEN_BUNDLE_FILES = {"index.js", "selftest.mjs"}

#: persona 必备锚点（改丢任何一条 = 红；关键词取唯一锚点）。
REQUIRED_PERSONA_MARKERS = {
    "唯一硬规矩·严禁猜": "严禁猜",
    "拿不到证据写未验证": "未验证",
}

#: 「闸」在预设文本里的机械痕迹（去注释后查）—— 命中 = 红。
GATE_PATTERNS = (
    ("插件行 ba-mode", re.compile(r"^\s*-\s*id:\s*ba-mode\s*$", re.M)),
    ("插件名 dsh-ba-mode", re.compile(r"^\s*name:\s*['\"]dsh-ba-mode['\"]", re.M)),
    ("硬门 gateRoots", re.compile(r"^\s*gateRoots:", re.M)),
    ("硬门 tools.guard", re.compile(r"tools\.guard\b")),
    ("开工闸 ba_boot", re.compile(r"\bba_boot\b")),
    ("证据闸 ba_gate", re.compile(r"\bba_gate\b")),
)

#: 明确写了「不存在」的 token —— 不查存在性（每一条都要写理由，别把真漏当成豁免）。
NEGATED_REFS = {
    "blender_addon/": "persona 明写「仓内没有 blender_addon/」（2026-10-29 插件已整体删除）",
    "tools/build_addon_zip.py": "persona 明写「没有 tools/build_addon_zip.py」（随插件一起删了）",
}
#: 不是文件路径的写法（通配示例 / 目录清单），跳过但写明理由。
SKIP_REFS: dict[str, str] = {}
NEGATION_HINTS = ("没有", "不存在", "已删", "已作废", "别再引用")

#: persona 里出现的「路径类 token」正则（顺序敏感：先长后短）。
_REF_PATTERNS = (
    ("kb", re.compile(r"kb/(\d{2})-([^\s`，。、）)\"']*)")),
    ("wtool", re.compile(r"(?<![\w./\\-])tools/([A-Za-z0-9_]+\.py)")),
    ("wsub", re.compile(r"(?<![\w./\\-])(_work|docs|src|tests)/([^\s`，。、）)\"']+)")),
    ("wrel", re.compile(r"(?<![\w./\\-])(断箭模型提取器|断箭资产管理器)/([^\s`，。、）)\"']+)")),
    ("abs", re.compile(r"(D:\\[^\s`，。、）)\"']+)")),
)


def _force_utf8_streams() -> None:
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(AttributeError, ValueError):
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]


def _read(path: Path) -> str:
    with contextlib.suppress(OSError, UnicodeError):
        return path.read_text(encoding="utf-8", errors="replace")
    return ""


def extract_persona(patch_text: str) -> str:
    """从预设 YAML 里取 persona 的 `prefix: |` 正文（找不到返回空串）。

    v4 起结束标记不再是 `- id: ba-mode`（那一行已删），改成**下一个插件行**（任意 id）。
    """
    start_mark = "              prefix: |\n"
    start = patch_text.find(start_mark)
    if start < 0:
        return ""
    body = patch_text[start + len(start_mark) :]
    end = body.find("\n          - id:")
    return body if end < 0 else body[:end]


def strip_comments(text: str) -> str:
    """去掉**整行 YAML 注释**，但 persona 的 `prefix: |` 正文整块保留。

    预设顶部的 v1→v3.2 沿革注释里会提到被删的闸（那是留档说明，不是「闸回来了」）；
    而 persona 正文里的 Markdown 小标题以 `##` 开头，**不能**被当成注释剥掉
    （否则写在标题里的闸词会漏检）—— 这是 selftest 负控 f 当场抓出来的假绿。
    """
    start_mark = "              prefix: |\n"
    start = text.find(start_mark)
    end = -1
    if start >= 0:
        body = text[start + len(start_mark) :]
        rel = body.find("\n          - id:")
        end = start + len(start_mark) + (rel if rel >= 0 else len(body))
    out: list[str] = []
    offset = 0
    for line in text.splitlines(keepends=True):
        line_start = offset
        offset += len(line)
        inside_persona = start >= 0 and line_start >= start + len(start_mark) and line_start < end
        if not inside_persona and line.lstrip().startswith("#"):
            continue
        out.append(line)
    return "".join(out)


def scan_refs(text: str) -> list[dict]:
    """扫一段文本 → [{kind, token, raw}]（去重、保序）。识别不了的写法不猜。"""
    found: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for kind, pattern in _REF_PATTERNS:
        for match in pattern.finditer(text):
            if kind == "kb":
                token = f"{match.group(1)}-{match.group(2)}"
            else:
                token = "/".join(part for part in match.groups() if part)
            key = (kind, token)
            if key in seen:
                continue
            seen.add(key)
            found.append({"kind": kind, "token": token, "raw": match.group(0)})
    return found


def _is_negated(text: str, raw: str) -> bool:
    """token 是否被**同一行前面的否定词**声明为「不存在」（白名单兜底见 NEGATED_REFS）。

    ⚠️ 只看 token **之前**的否定词：文件名里自带「不存在」三个字
    （如 `kb/99-不存在的文档.md`）不该被当成豁免 —— 这是 selftest 负控当场抓出来的假绿。
    """
    if raw in NEGATED_REFS or raw.rstrip("/") + "/" in NEGATED_REFS:
        return True
    for line in text.splitlines():
        idx = line.find(raw)
        if idx < 0:
            continue
        before = line[:idx]
        if any(hint in before for hint in NEGATION_HINTS):
            return True
    return False


def resolve_ref(ref: dict, root: Path, prod: Path, work: Path | None = None) -> tuple[bool, str]:
    """一条 token → (存在吗, 说明)。只认产品自己的锚点，不做模糊搜索。

    ⚠️ 2026-11-09 数据移出仓：`_work/…` 的根是 **WORK**（默认 `D:\\断箭模组制作\\_work_db`），
    不再是仓内 `prod/_work`（那个目录还在，但 ISIL / loc / hdrp17 这些已搬走）。
    """
    kind, token = ref["kind"], ref["token"]
    if kind == "kb":
        exact = prod / "kb" / token
        if exact.is_file():
            return True, token
        number = token.split("-", 1)[0]
        hits = sorted((prod / "kb").glob(f"{number}-*.md"))
        if hits:
            return False, (
                f"kb/{token}（编号 {number} 在，但文件名对不上：{hits[0].name} —— 疑似改名）"
            )
        return False, f"kb/{token}（**整个编号都不存在**）"
    if kind == "wtool":
        for base, label in ((prod / "tools", "仓 tools"), (root / "tools", "根 tools")):
            if (base / token).is_file():
                return True, f"{label}/{token}"
        return False, f"tools/{token}（仓内 / 根都没有）"
    if kind == "wsub":
        sub, rest = token.split("/", 1)
        base = (work or WORK) if sub == "_work" else prod / sub
        path = base / rest
        return (path.exists(), f"{sub}/{rest}")
    if kind == "wrel":
        head, rest = token.split("/", 1)
        path = root / head / rest
        return (path.exists(), f"{head}/{rest}")
    if kind == "abs":
        path = Path(ref["raw"])
        return (path.exists(), str(path))
    return False, f"未知 token 种类 {kind}"


def check_package(package_text: str) -> list[dict]:
    """package.json：名字 / 版本 / dsh.bundle.patch 指向 cordis.patch.yml。"""
    rows: list[dict] = []
    try:
        payload = json.loads(package_text)
    except (ValueError, TypeError):
        return [{"level": "red", "name": "package.json 可解析", "detail": "**不是合法 JSON**"}]
    version = str(payload.get("version") or "")
    rows.append(
        {
            "level": "ok" if re.fullmatch(r"\d+\.\d+\.\d+", version) else "red",
            "name": "package.json 版本号",
            "detail": version or "**缺 version**",
        }
    )
    patch = ""
    with contextlib.suppress(AttributeError, TypeError):
        patch = str(payload.get("dsh", {}).get("bundle", {}).get("patch") or "")
    rows.append(
        {
            "level": "ok" if patch == "./cordis.patch.yml" else "red",
            "name": "dsh.bundle.patch 指向预设声明",
            "detail": patch or "**缺 dsh.bundle.patch**",
        }
    )
    name = str(payload.get("name") or "")
    rows.append(
        {
            "level": "ok" if name == "dsh-ba-mode" else "red",
            "name": "bundle 包名",
            "detail": name or "**缺 name**",
        }
    )
    return rows


def check_no_gates(patch_text: str, bundle_files: list[str] | None = None) -> list[dict]:
    """闸不许回来：插件行 / gateRoots / tools.guard / ba_boot / ba_gate / 闸实现文件。"""
    rows: list[dict] = []
    body = strip_comments(patch_text)
    for label, pattern in GATE_PATTERNS:
        rows.append(
            {
                "level": "red" if pattern.search(body) else "ok",
                "name": f"闸不许回来：{label}",
                "detail": "**命中（闸被加回来了）**" if pattern.search(body) else "无痕迹",
            }
        )
    if bundle_files is not None:
        names = set(bundle_files)
        forbidden = sorted(names & FORBIDDEN_BUNDLE_FILES)
        rows.append(
            {
                "level": "red" if forbidden else "ok",
                "name": "闸实现文件已删（index.js / selftest.mjs）",
                "detail": f"**回来了：{forbidden}**" if forbidden else "都不在",
            }
        )
        extras = sorted(names - ALLOWED_BUNDLE_FILES - FORBIDDEN_BUNDLE_FILES)
        rows.append(
            {
                "level": "yellow" if extras else "ok",
                "name": "bundle 只留预设声明（cordis.patch.yml + package.json）",
                "detail": f"多出：{extras}" if extras else "干净",
            }
        )
    return rows


def check_persona_markers(persona: str) -> list[dict]:
    rows: list[dict] = []
    for name, marker in REQUIRED_PERSONA_MARKERS.items():
        rows.append(
            {
                "level": "ok" if marker in persona else "red",
                "name": f"persona 段落「{name}」",
                "detail": f"关键词 `{marker}`" + ("命中" if marker in persona else "**丢了**"),
            }
        )
    return rows


def run_all(
    *,
    patch_text: str,
    package_text: str,
    root: Path,
    prod: Path,
    work: Path | None = None,
    bundle_files: list[str] | None = None,
) -> list[dict]:
    """全量检查 → 检查行列表（`level` ∈ ok / yellow / red）。纯函数式，selftest 可注入假夹具。"""
    rows: list[dict] = []
    persona = extract_persona(patch_text)
    rows.append(
        {
            "level": "ok" if persona.strip() else "red",
            "name": "persona 正文可提取",
            "detail": f"{len(persona)} 字符" if persona.strip() else "**prefix 块没找到**",
        }
    )
    rows.extend(check_persona_markers(persona))
    rows.extend(check_no_gates(patch_text, bundle_files))
    rows.extend(check_package(package_text))

    errors: dict[tuple[str, str], str] = {}
    skipped: list[str] = []
    refs = scan_refs(persona)
    for ref in refs:
        key = f"{ref['kind']}:{ref['token']}"
        if key in SKIP_REFS or ref["raw"] in SKIP_REFS:
            skipped.append(key)
            continue
        if _is_negated(persona, ref["raw"]):
            skipped.append(key)
            continue
        ok, detail = resolve_ref(ref, root, prod, work)
        if not ok:
            errors[(ref["kind"], ref["token"])] = detail
    rows.append(
        {
            "level": "ok" if not errors else "red",
            "name": "persona 路径引用",
            "detail": (
                f"{len(refs)} 条 token 全在（跳过明写不存在/非路径 {len(skipped)} 条）"
                if not errors
                else f"红 {len(errors)} 条（另跳过 {len(skipped)} 条）"
            ),
        }
    )
    for (kind, token), detail in sorted(errors.items()):
        rows.append(
            {
                "level": "red",
                "name": f"引用 `{kind}:{token}`",
                "detail": f"**找不到**：{detail}",
            }
        )
    return rows


def _summarize(rows: list[dict]) -> tuple[int, int, int]:
    red = sum(1 for r in rows if r["level"] == "red")
    yellow = sum(1 for r in rows if r["level"] == "yellow")
    return len(rows), red, yellow


def _emit(rows: list[dict], as_json: bool) -> None:
    total, red, yellow = _summarize(rows)
    if as_json:
        payload = {
            "ok": red == 0, "total": total, "red": red, "yellow": yellow, "rows": rows,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=1))
        return
    print(f"# 预设体检 · ba_preset_check v4（{total} 条检查 · 红 {red} · 黄 {yellow}）")
    icon = {"ok": "✓", "yellow": "⚠", "red": "❌"}
    for row in rows:
        print(f"{icon.get(row['level'], '?')} {row['name']} —— {row['detail']}")
    print("")
    if red == 0:
        print("结论：✅ 全绿（预设只剩「严禁猜」那条，一扇闸都没挂回来）")
    else:
        print(f"结论：❌ 有 {red} 条红 —— 逐条按上面复算")


def selftest() -> int:
    """负控：假夹具里塞不存在的 kb / 工具 / 把闸加回来 / 闸文件回来 ⇒ 必须红；正控 ⇒ 不红。"""
    passed: list[str] = []
    failures: list[str] = []

    def ok(name: str, cond: bool, detail: str = "") -> None:
        if cond:
            passed.append(name)
        else:
            failures.append(f"{name} —— {detail or '不通过'}")

    def reds_of(rows: list[dict]) -> list[str]:
        return [r["name"] for r in rows if r["level"] == "red"]

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        prod = base / "prod"
        (prod / "kb").mkdir(parents=True)
        (prod / "tools").mkdir(parents=True)
        (prod / "docs").mkdir(parents=True)
        work = base / "work"
        work.mkdir(parents=True)
        (prod / "kb" / "13-怎么解一条卡片行.md").write_text("x", encoding="utf-8")
        (prod / "tools" / "real_tool.py").write_text("x", encoding="utf-8")
        (prod / "docs" / "x.md").write_text("x", encoding="utf-8")
        (work / "y").write_text("x", encoding="utf-8")

        good_persona = (
            "## 唯一硬规矩 · 严禁猜\n"
            "- 不知道就去读、去量：kb/13-怎么解一条卡片行.md · tools/real_tool.py · "
            "docs/x.md · _work/y\n"
            "- 拿不到证据就明写「未验证」＋ 缺什么。\n"
        )
        bad_persona = good_persona + "- 还有 kb/99-举例文档.md tools/no_such_tool_xyz.py\n"
        patch_good = (
            "              prefix: |\n" + good_persona + "\n          - id: agent-instructions\n"
        )
        patch_bad = (
            "              prefix: |\n" + bad_persona + "\n          - id: agent-instructions\n"
        )
        package_good = json.dumps(
            {
                "name": "dsh-ba-mode",
                "version": "4.0.0",
                "dsh": {"bundle": {"patch": "./cordis.patch.yml"}},
            },
            ensure_ascii=False,
        )
        package_bad = json.dumps(
            {"name": "dsh-ba-mode", "version": "4.0.0", "dsh": {"bundle": {"patch": "./x.yml"}}},
            ensure_ascii=False,
        )
        bundle_good = ["cordis.patch.yml", "package.json"]

        def run(patch_text: str, package_text: str = package_good, bundle_files=None):
            return run_all(
                patch_text=patch_text,
                package_text=package_text,
                root=base,
                prod=prod,
                work=work,
                bundle_files=bundle_good if bundle_files is None else bundle_files,
            )

        rows = run(patch_good)
        ok("正控：好夹具全绿", not reds_of(rows), f"红：{reds_of(rows)[:3]}")

        names = reds_of(run(patch_bad))
        ok("负控 a：不存在的 kb 必须红", any("99" in n for n in names), f"红：{names[:6]}")
        ok(
            "负控 b：不存在的工具必须红",
            any("no_such_tool_xyz" in n for n in names),
            f"红：{names[:6]}",
        )

        patch_no_marker = patch_good.replace("严禁猜", "随便做")
        rows_no_marker = run(patch_no_marker)
        ok(
            "负控 c：persona 丢了「严禁猜」必须红",
            any("严禁猜" in n for n in reds_of(rows_no_marker)),
            f"红：{reds_of(rows_no_marker)[:6]}",
        )

        rows_gate_row = run(patch_good + "          - id: ba-mode\n")
        ok(
            "负控 d：ba-mode 插件行回来必须红",
            any("插件行 ba-mode" in n for n in reds_of(rows_gate_row)),
            f"红：{reds_of(rows_gate_row)[:6]}",
        )

        rows_gate_roots = run(
            patch_good + "              gateRoots:\n                - path: 'prod'\n"
        )
        ok(
            "负控 e：gateRoots 回来必须红",
            any("gateRoots" in n for n in reds_of(rows_gate_roots)),
            f"红：{reds_of(rows_gate_roots)[:6]}",
        )

        rows_gate_tool = run(patch_good.replace("严禁猜\n", "严禁猜（先跑 ba_gate --kind plan）\n"))
        ok(
            "负控 f：ba_gate / ba_boot 回来必须红",
            any("ba_gate" in n for n in reds_of(rows_gate_tool)),
            f"红：{reds_of(rows_gate_tool)[:6]}",
        )

        rows_file = run(patch_good, bundle_files=bundle_good + ["index.js"])
        ok(
            "负控 g：闸实现文件 index.js 回来必须红",
            any("index.js" in n for n in reds_of(rows_file)),
            f"红：{reds_of(rows_file)[:6]}",
        )

        rows_pkg = run(patch_good, package_text=package_bad)
        ok(
            "负控 h：dsh.bundle.patch 指错必须红",
            any("dsh.bundle.patch" in n for n in reds_of(rows_pkg)),
            f"红：{reds_of(rows_pkg)[:6]}",
        )

        negated = "              prefix: |\n" + good_persona + (
            "- ⛔ 仓内没有 tools/gone_tool.py（已删）\n"
        ) + "\n          - id: agent-instructions\n"
        rows_neg = run(negated)
        ok(
            "正控：明写「没有」的 token 不算红",
            not reds_of(rows_neg),
            f"红：{reds_of(rows_neg)[:3]}",
        )

    if failures:
        print("❌ 自检失败：")
        for row in failures:
            print(f"   · {row}")
        return 1
    print(f"✅ 自检全绿（{len(passed)} 条：正控 2 + 负控 8）")
    return 0


def main(argv: list[str] | None = None) -> int:
    _force_utf8_streams()
    parser = argparse.ArgumentParser(description="断箭 Mod 开发模式 · 预设机械体检（只读）")
    parser.add_argument("--json", action="store_true", help="机读输出")
    parser.add_argument("--strict", action="store_true", help="黄也当红（rc=1）")
    parser.add_argument("--selftest", action="store_true", help="负控自检（必须能红）")
    args = parser.parse_args(argv)

    if args.selftest:
        return selftest()

    rows = run_all(
        patch_text=_read(PATCH),
        package_text=_read(PACKAGE_JSON),
        root=ROOT,
        prod=PROD,
        work=WORK,
        bundle_files=sorted(p.name for p in PLUGIN_DIR.iterdir() if p.is_file())
        if PLUGIN_DIR.is_dir()
        else [],
    )
    total, red, yellow = _summarize(rows)
    _emit(rows, args.json)
    if red or (args.strict and yellow):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
