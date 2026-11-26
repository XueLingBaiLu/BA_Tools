r"""地址清单（`catalog key` / `bundle` / `path`）的判据 —— 用户需求⑩ 的 H2 / H3 / H4。

**判据一行**：`pytest tests/test_cli_address_inventory.py -q -n 0` -> 通过数从 0 变 **N**，
其中 ≥4 条是**正控**（喂不存在的键 / 包 / 路径必须非 0 退出并点名 —— AGENTS §0.9 动作 2）。

## 两条硬口径（写进断言，别只写在文档里）

1. **图标 = resource_type 3 ＋ 一条类型 4 别名**（两条 entry）；**prefab = 1（单条）**；
2. **依赖声明不许瞎编**：本条印的是 catalog 里**真的** `entry[2]` / `entry[3]`（＋那个桶里的包），
   由 `--dependency-hash-from` 那条产品口径抄真件 —— 本文件用的是**真件 catalog** 对拍。

## 怎么保证「印出来的四条读数」不是自证

真件那几条跑在游戏里的 catalog.json 上，并用一份**独立解码器**（`_independent()`，
本文件内 20 行、**只**用 `base64` + `struct` + `json`，不 import 任何产品解析器）把同一份字节
再解一遍，逐项比：实际路径 / resource_type / 依赖键 / 依赖 hash / m_Crc / m_BundleSize / m_Hash。
⇒ 产品解析器与独立解码器同时错成一样才算漏网（两者本来就是两套解析）。
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import struct
from pathlib import Path

import pytest

from ba_mod_maker.cli import __main__ as cli_main
from ba_mod_maker.cli import address_inventory as ai
from test_catalog_edit import (
    ADDR_ONE,
    ALPHA,
    _bytes,
    _synth,
)
from test_no_swap_wording import banned_hits, iter_help_texts

#: `catalog` 短帮助里那一句（「新组进了 J3 尺子量程」的记号）。
CATALOG_HELP_MARK = "地址清单（只读）"


# --------------------------------------------------------------------------- #
# 夹具：合成件（不需要游戏）+ 真件（没有就 skip）
# --------------------------------------------------------------------------- #
def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ba-mod-maker")
    sub = parser.add_subparsers(dest="command")
    ai.add_parser(sub)
    return parser


def _parse(*argv: str) -> argparse.Namespace:
    return _parser().parse_args(list(argv))


@pytest.fixture(scope="module")
def synth() -> dict:
    edit, table = ai.view(_bytes(_synth()))
    return {"edit": edit, "table": table}


@pytest.fixture(scope="module")
def real_bytes() -> bytes:
    """真件 **JSON** catalog（没装游戏 / 已经是 1.2.1 的 catalog.bin 就 skip）。

    出处：2026-10-13 实测 —— 游戏升到 1.2.1 后 `aa/` 下只剩 `catalog.bin`，
    下面那几条 JSON 口径的用例（三张 base64 表 / 独立解码器）**在 1.2.1 上不适用**，
    ⇒ 这里必须 skip，否则它们会以 `UnicodeDecodeError` **假红**（那是在测一份不存在的格式）。
    二进制那一版的口径在 §⑤（`BinaryCatalogView`）。
    """
    from ba_mod_maker.core.assets.game import catalog_json_file, find_game_root

    root = find_game_root()
    path = catalog_json_file(root) if root is not None else None
    if path is None or not path.is_file():
        pytest.skip(
            "1.2.1（2026-10-17 11:30）起游戏只发 catalog.bin；这台机器上没有 catalog.json"
            " ⇒ 三张 base64 表 ＋ 独立解码器那几条在 1.2.1 上不适用；"
            "二进制侧的等价覆盖在 §⑤（本文件）与 tests/test_catalog_bin_write.py"
        )
    return path.read_bytes()


# --------------------------------------------------------------------------- #
# ★ 独立解码器（本文件内，20 行；⛔ 不 import 产品解析器）
# --------------------------------------------------------------------------- #
def _independent(catalog_bytes: bytes) -> dict:
    """只用 base64 + struct + json 再解一遍 catalog：entry 七格 ＋ extra 那条 UTF-16LE JSON。"""
    raw = json.loads(catalog_bytes.decode("utf-8"))
    entries = base64.b64decode(raw["m_EntryDataString"])
    extra = base64.b64decode(raw["m_ExtraDataString"])
    count = struct.unpack_from("<i", entries, 0)[0]
    rows = [struct.unpack_from("<7i", entries, 4 + 28 * i) for i in range(count)]

    def options(offset: int) -> dict:
        pos = offset + 1                      # 0x07 类型标记
        pos += 1 + extra[pos]                 # 程序集名（1 字节长 + 名）
        pos += 1 + extra[pos]                 # 类名
        (size,) = struct.unpack_from("<i", extra, pos)
        pos += 4
        return json.loads(extra[pos : pos + size].decode("utf-16-le"))

    return {"ids": raw["m_InternalIds"], "rows": rows, "options": options, "count": count}


# --------------------------------------------------------------------------- #
# ① 解析器形状 + J3 量程（与 address 那一组同一把尺子）
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("action", ["key", "bundle", "path"])
def test_catalog_offers_the_three_read_verbs(action: str) -> None:
    parsed = _parse("catalog", action, "X")
    assert parsed.catalog_action == action
    assert parsed.value == "X"


def test_catalog_has_no_replacement_entry() -> None:
    names: list[str] = []
    for action in _parser()._actions:
        if isinstance(action, argparse._SubParsersAction):
            for child in action.choices.values():
                for sub in child._actions:
                    names.extend(sub.option_strings)
                    if isinstance(sub, argparse._SubParsersAction):
                        names.extend(str(k) for k in sub.choices)
    banned = [n for n in names if re.search(r"swap|replace|(^|-)to$", n, re.IGNORECASE)]
    assert banned == [], f"地址清单上出现了顶替式入口：{banned}"
    assert {"key", "bundle", "path"} <= set(names)


def test_catalog_help_is_visible_to_the_j3_ruler_and_clean() -> None:
    text = "\n".join(iter_help_texts(_parser()))
    assert CATALOG_HELP_MARK in text, "catalog 的短帮助没被 iter_help_texts 取到"
    assert banned_hits(text) == [], f"地址清单帮助里有禁词：{banned_hits(text)}"


def test_catalog_is_wired_into_the_product_cli_parser() -> None:
    text = "\n".join(iter_help_texts(cli_main._build_parser()))
    assert CATALOG_HELP_MARK in text, "catalog 没进产品 CLI（cli/__main__.py 没接线）"


# --------------------------------------------------------------------------- #
# ② 合成件：正查 / 反查 / 登记面清单
# --------------------------------------------------------------------------- #
def test_key_lookup_returns_the_registration_face(synth: dict) -> None:
    records = ai.key_records(synth["edit"], synth["table"], ADDR_ONE)
    assert records, "合成件里这条键应当命中"
    record = records[0]
    assert record["key"] == ADDR_ONE
    assert record["asset_path"] in synth["edit"].internal_ids
    assert record["resource_type_index"] >= 0
    rendered = "\n".join(ai.render_records(records))
    for label in ("键", "实际路径", "provider", "resource_type", "依赖声明"):
        assert label in rendered


def test_missing_key_returns_nothing_from_the_pure_function(synth: dict) -> None:
    assert ai.key_records(synth["edit"], synth["table"], "no/such/key") == ()


def test_path_lookup_is_the_inverse_of_the_key_lookup(synth: dict) -> None:
    records = ai.key_records(synth["edit"], synth["table"], ADDR_ONE)
    path = records[0]["asset_path"]
    back = ai.path_records(synth["edit"], synth["table"], path)
    assert back, f"按实际路径 {path!r} 反查应当命中"
    assert ADDR_ONE in {r["key"] for r in back}, "正查 -> 反查 应当回到原键"


def test_bundle_lookup_reports_the_bundle_registration(synth: dict) -> None:
    report = ai.bundle_report(synth["edit"], synth["table"], ALPHA)
    assert report, "合成件里这个包应当命中"
    assert report["is_bundle_entry"] is True
    assert report["own"]["asset_path"].endswith(".bundle")
    assert "direct_keys" in report and "indirect_keys" in report


def test_missing_bundle_is_reported_as_missing(synth: dict) -> None:
    assert ai.bundle_report(synth["edit"], synth["table"], "no_such.bundle") == {}


def test_registration_lines_reuse_the_same_renderer(synth: dict) -> None:
    """H4：登记面清单与正查**同源**（同一渲染器），且给得出可抄的那一块。"""
    edit = synth["edit"]
    indices = [int(e) for e in edit.buckets[edit.key_index_of(ADDR_ONE)]]
    lines = ai.registration_lines(_bytes(_synth()), indices)
    text = "\n".join(lines)
    assert "本次登记面清单" in text
    assert "实际路径" in text and "resource_type" in text
    assert "可抄（制表符分隔）" in text
    assert ADDR_ONE in text


def test_registration_lines_say_so_when_there_is_nothing(synth: dict) -> None:
    lines = ai.registration_lines(_bytes(_synth()), [])
    assert lines and "没有登记新的 catalog 条目" in lines[0]


# --------------------------------------------------------------------------- #
# ③ 正控：不存在的键 / 包 / 路径 ⇒ 非 0 退出 ＋ 点名（⛔ 不许静默返回空）
# --------------------------------------------------------------------------- #
def _run(tmp_path: Path, *argv: str) -> int:
    catalog = tmp_path / "catalog.json"
    catalog.write_bytes(_bytes(_synth()))
    return ai.run(_parse("catalog", *argv, "--catalog", str(catalog)))


@pytest.mark.parametrize(
    "argv,needle",
    [
        (("key", "no/such/key"), "没有这个键"),
        (("bundle", "no_such.bundle"), "没有这个包"),
        (("path", "Assets/No/Such.prefab"), "没有这条路径"),
    ],
)
def test_a_missing_thing_exits_nonzero_and_names_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], argv: tuple, needle: str
) -> None:
    code = _run(tmp_path, *argv)
    captured = capsys.readouterr()
    assert code == ai.EXIT_MISSING, code
    assert needle in captured.err, captured.err
    assert argv[1] in captured.err, "要把那个键 / 包 / 路径**点名**印出来"


def test_a_missing_catalog_exits_unusable(tmp_path: Path) -> None:
    code = ai.run(_parse("catalog", "key", "X", "--catalog", str(tmp_path / "nope.json")))
    assert code == ai.EXIT_UNUSABLE, code


def test_an_unknown_action_is_refused_by_argparse() -> None:
    """动名词层面就挡住：argparse 直接报错退出（2），连 run() 都到不了。"""
    with pytest.raises(SystemExit) as excinfo:
        _parse("catalog", "dump", "X")
    assert excinfo.value.code == 2


def test_the_h4_printer_is_wired_into_the_cli(tmp_path, capsys: pytest.CaptureFixture[str]) -> None:
    """H4 的接线：`_cmd_newasset` 末尾调的**就是** product CLI 里那个打印器（不是另写一份）。"""
    edit, _table = ai.view(_bytes(_synth()))
    indices = [int(e) for e in edit.buckets[edit.key_index_of(ADDR_ONE)]]
    registration = argparse.Namespace(
        bundle_entry_index=indices[0], asset_entry_index=indices[0], alias_entry_index=-1
    )
    plan = argparse.Namespace(registration=registration, catalog_after=_bytes(_synth()))
    cli_main._print_registration_surface(plan)
    out = capsys.readouterr().out
    assert "本次登记面清单" in out and ADDR_ONE in out


def test_the_h4_printer_never_breaks_the_main_path(capsys: pytest.CaptureFixture[str]) -> None:
    """附赠读数不许把主链路弄崩：喂一个坏计划只许印一句、不许抛。"""
    cli_main._print_registration_surface(argparse.Namespace(registration=None, catalog_after=b""))
    cli_main._print_registration_surface(
        argparse.Namespace(
            registration=argparse.Namespace(
                bundle_entry_index=0, asset_entry_index=0, alias_entry_index=-1
            ),
            catalog_after=b"not a catalog",
        )
    )
    captured = capsys.readouterr()
    assert "印不出来" in captured.err


def test_run_refuses_an_unknown_action_even_if_it_gets_there(tmp_path: Path) -> None:
    """跑法二：绕过 argparse 直接调 run()（别的调用方 / 将来的接线改动）也不许静默通过。"""
    catalog = tmp_path / "catalog.json"
    catalog.write_bytes(_bytes(_synth()))
    args = argparse.Namespace(
        catalog_action="dump", catalog=str(catalog), json_out=False, value="X"
    )
    assert ai.run(args) == ai.EXIT_UNUSABLE


# --------------------------------------------------------------------------- #
# ④ ★ 真件：四条读数与**独立解码器**逐个相等（这是本文件的判据主体）
# --------------------------------------------------------------------------- #
def test_real_catalog_has_the_measured_shape(real_bytes: bytes) -> None:
    edit, table = ai.view(real_bytes)
    independent = _independent(real_bytes)
    assert len(edit.internal_ids) == len(independent["ids"])
    assert len(edit.entries) == independent["count"]
    assert len(edit.internal_ids) >= 14000, f"m_InternalIds 只有 {len(edit.internal_ids)} 条"
    assert edit.self_check() == ()
    assert table.key_count == edit.counts[0]


def test_real_prefab_key_is_a_single_entry_of_type_one(real_bytes: bytes) -> None:
    """硬口径：**prefab = resource_type 1（单条）**。"""
    edit, table = ai.view(real_bytes)
    records = ai.key_records(edit, table, "RU_T72B")
    assert records, "真件里没有 RU_T72B 这条键"
    assert len(records) == 1, [r["resource_type_index"] for r in records]
    assert records[0]["resource_type_index"] == 1
    assert records[0]["asset_path"].endswith(".prefab")


def test_real_icon_key_is_type_three_plus_a_type_four_alias(real_bytes: bytes) -> None:
    """硬口径：**图标 = resource_type 3 ＋ 一条类型 4 别名（两条 entry）**。"""
    edit, table = ai.view(real_bytes)
    records = ai.key_records(edit, table, "RU_T72B-Label")
    assert records, "真件里没有 RU_T72B-Label 这条键"
    assert sorted(r["resource_type_index"] for r in records) == [3, 4]
    assert len({r["asset_path"] for r in records}) == 1, "两条 entry 指同一条实际路径"


def test_real_key_readings_match_an_independent_decode(real_bytes: bytes) -> None:
    """★ 主体：实际路径 / resource_type / 依赖键 / 依赖 hash / m_Crc / m_BundleSize / m_Hash
    逐项等于独立解码器读出来的那个数。"""
    edit, table = ai.view(real_bytes)
    independent = _independent(real_bytes)
    records = ai.key_records(edit, table, "RU_T72B")
    assert records
    checked = 0
    for record in records:
        row = independent["rows"][record["entry_index"]]
        assert record["asset_path"] == independent["ids"][row[0]], "实际路径对不上"
        assert record["resource_type_index"] == row[6], "resource_type 对不上"
        dependency = record["dependency"]
        assert dependency is not None, "真件模型地址必须有依赖声明"
        assert dependency["key_index"] == row[2], "依赖键对不上"
        assert dependency["hash"] == row[3], "依赖 hash 对不上"
        assert dependency["member_count"] >= 1
        for info in dependency["bundles"]:
            options = independent["options"](independent["rows"][info["entry_index"]][4])
            assert info["m_Crc"] == options["m_Crc"], "m_Crc 对不上"
            assert info["m_BundleSize"] == options["m_BundleSize"], "m_BundleSize 对不上"
            assert info["m_Hash"] == options["m_Hash"], "m_Hash 对不上"
            assert info["name"] in independent["ids"][independent["rows"][info["entry_index"]][0]]
            checked += 1
    assert checked >= 1, "至少要核到一个包的四条读数"


def test_real_reverse_lookup_by_bundle_and_by_path(real_bytes: bytes) -> None:
    """反查的两条路都要 > 0：包 -> 键（直接）· 实际路径 -> 键。"""
    edit, table = ai.view(real_bytes)
    record = ai.key_records(edit, table, "RU_T72B-Label")[0]
    bundles = (record["dependency"] or {}).get("bundles") or []
    assert bundles, "图标那条应当有一个包"
    report = ai.bundle_report(edit, table, bundles[0]["name"])
    assert report["is_bundle_entry"] is True
    assert report["direct_keys"], "这个包应当直接支撑若干键"
    by_path = ai.path_records(edit, table, record["asset_path"])
    assert by_path, "按实际路径反查应当命中"
    assert record["key"] in {r["key"] for r in by_path}


def test_real_missing_key_exits_missing(real_bytes: bytes, tmp_path: Path,
                                        capsys: pytest.CaptureFixture[str]) -> None:
    """真件上的正控：不存在的键必须非 0 退出并点名。"""
    catalog = tmp_path / "catalog.json"
    catalog.write_bytes(real_bytes)
    code = ai.run(_parse("catalog", "key", "no/such/key", "--catalog", str(catalog)))
    captured = capsys.readouterr()
    assert code == ai.EXIT_MISSING
    assert "no/such/key" in captured.err


# --------------------------------------------------------------------------- #
# ⑤ ★ 二进制 catalog（1.2.1 起游戏换成 `aa/catalog.bin`）—— 同一套渲染器、另一条取数
# --------------------------------------------------------------------------- #
def _binary_catalog_path() -> Path:
    """盘上那份 `aa/catalog.bin`（没有就 skip —— 1.2.0 的机器上本来就没有）。"""
    from ba_mod_maker.core.assets.game import catalog_file, find_game_root

    root = find_game_root()
    path = catalog_file(root) if root is not None else None
    if path is None or Path(path).name != "catalog.bin" or not Path(path).is_file():
        pytest.skip("这台机器的 catalog 不是二进制的 catalog.bin（1.2.0 或没装游戏）")
    return Path(path)


@pytest.fixture(scope="module")
def binary_view() -> ai.BinaryCatalogView:
    return ai.BinaryCatalogView.load(_binary_catalog_path())


#: 出厂件 `aa/catalog.bin` 的 sha256（**2026-10-18 重记**）—— 同 test_catalog_bin_write.py。
#:
#: ⚠ 上一代（2026-10-13）是 `6a983c92e520fbe3…`（2,345,239 B）。2026-10-17 11:30 Steam 更新后
#: 游戏发了新的一份（2,755,558 B / `aa/catalog.hash` = 1b56bf58…），计数头也跟着换了。
#:
#: 重记命令（在仓根跑；**只读**游戏目录）：
#:
#:     .venv\Scripts\python.exe tools\catalog_baseline.py
#:
#: 本次读数 = 2,755,558 B · 6adccfb6… · counts (27764, 27764, 20614) ·
#: units 3,352,484,707 B / crc 265,969,015 · `aa/catalog.hash` = 1b56bf58…
#: （`.hash` 由 test_catalog_bin_write.py 那条外部 oracle 守）
SHIPPED_SHA256 = "6adccfb6a2b580c7e28b48e0a26b302898ba47c49168ecab67ff9625b43a45cf"

#: 盘上 `units_assets_all_*` 那条包的四件套读数（**2026-10-18 重记**；同一个重记命令）。
#:
#: ⚠ 包名里的 hash **每次重新出包都会变**（旧代 = `units_assets_all_ebe39db6…`）⇒ 名字一律
#: 由 `_units_bundle_name()` 从现场 catalog 里取，⛔ 不再写死；这四个数跟着那条包走。
EXPECTED_UNITS_CRC = 265_969_015
EXPECTED_UNITS_SIZE = 3_352_484_707


def _units_bundle_name(view: ai.BinaryCatalogView) -> str:
    """现场 catalog 里 `units_assets_all_*` 那条**包名**（⛔ 不写死）。

    2026-10-18 的教训：这个名字里嵌着 32 位 hash，游戏每次重新出包它就变
    （`ebe39db6…` -> `43d88558…`），写死它会让「形状」用例跟着换代一起假红
    （实测 `bundle_report` 对不存在的键返回 `{}` ⇒ 报 `KeyError: 'is_bundle_entry'`，
    看上去像产品 bug，其实是基线过期）。
    """
    names = [
        str(key)
        for key in view.parsed.keys
        if str(key).startswith("units_assets_all") and str(key).endswith(".bundle")
    ]
    assert len(names) == 1, f"units 包名应当唯一，现场拿到 {names}"
    return names[0]


def _shipped_or_skip(path: Path) -> None:
    """**基线条目的前提**：现场那份 `aa/catalog.bin` 就是出厂件。

    ⚠ 这不是「跳过去就能绿」的阀门，而是**出处闸**：只要跑过 `newasset add-model` /
    `newasset add-bundle`（往游戏里真装一份资产），现场那份就多出条目
    —— 实测 2,345,239 -> 2,345,779 B / 计数 (22492,22492,17963) -> (22494,22494,17965)。
    那时**跳过并说明**，而不是拿装过的文件去比出厂读数（那样红得没有任何信息）——
    与 `tests/test_catalog_bin_write.py::_need_shipped` **同一条口径与同一个先例**。

    ⛔ **2026-10-18 改造（原来这里是个假绿口子）**：旧版只有一句「不是出厂件」，
    所以 **sha 基线一过期，下面那两条基线用例就永远 skip、永远全绿**
    —— 2026-10-17 11:30 游戏更新后正是这个状态。现在：① 基线已重记成新一代出厂件
    （sha 与现场相符 ⇒ **真跑**）；② 闸不通过时把**三组读数逐字报出来**，让
    「游戏换代」与「我们自己装过」在 skip 理由里一眼能分。
    """
    got = path.read_bytes()
    got_sha = hashlib.sha256(got).hexdigest()
    if got_sha == SHIPPED_SHA256:
        return
    counts = ai.BinaryCatalogView.load(path).counts()
    pytest.skip(
        f"现场 aa/catalog.bin 不是出厂件：sha {got_sha}（期望 {SHIPPED_SHA256}）· "
        f"字节 {len(got):,} · 计数 {counts}"
        " ⇒ 基线条目的前提不成立（游戏换代 ⇒ tools/catalog_baseline.py 重记；"
        "是我们装过 ⇒ newasset restore 还原）"
    )

def test_binary_view_reads_the_measured_counts(binary_view: ai.BinaryCatalogView) -> None:
    """与 `_work/lead121/check_catalog_bin.py` 同一组实测读数（换版本就跟着改，别放宽）。"""
    _shipped_or_skip(_binary_catalog_path())
    counts = binary_view.counts()
    assert counts[0] == 27764, "键条数与实测不符"
    assert counts[2] == 20614, "条目条数与实测不符"
    assert binary_view.parsed.version == 2
    assert binary_view.parsed.locator_id == "AddressablesMainContentCatalog"


def test_binary_key_lookup_gives_the_same_shape_as_json(binary_view: ai.BinaryCatalogView) -> None:
    """口径：`key_records` 给的东西与 JSON 通道**同形**（渲染器与 --json 都不用分叉）。"""
    records = binary_view.key_records("RU_T72B")
    assert len(records) == 1
    record = records[0]
    assert record["key"] == "RU_T72B"
    assert record["asset_path"].endswith("RU_T72B_Base.prefab")
    assert record["role"] == "asset"
    bundles = {b["name"] for b in record["dependency"]["bundles"]}
    units_name = _units_bundle_name(binary_view)
    assert units_name in bundles, f"依赖里没有 units 包：{sorted(bundles)[:4]}…"
    units = next(b for b in record["dependency"]["bundles"] if b["name"] == units_name)
    assert units["m_Crc"] == EXPECTED_UNITS_CRC
    assert units["m_BundleSize"] == EXPECTED_UNITS_SIZE


def test_binary_path_lookup_is_the_inverse_of_the_key_lookup(
    binary_view: ai.BinaryCatalogView,
) -> None:
    key = "RU_T72B"
    asset_path = binary_view.key_records(key)[0]["asset_path"]
    back = binary_view.path_records(asset_path)
    assert back, "路径反查必须能找到那条 entry"
    assert any(r["asset_path"] == asset_path for r in back)


def test_binary_bundle_lookup_reports_direct_keys(binary_view: ai.BinaryCatalogView) -> None:
    name = _units_bundle_name(binary_view)
    report = binary_view.bundle_report(name)
    assert report["is_bundle_entry"] is True
    assert report["own"]["key"] == name
    assert report["own"]["dependency"]["bundles"][0]["m_BundleSize"] == EXPECTED_UNITS_SIZE
    assert len(report["direct_keys"]) > 1000, "units 包被成千上万个资产键依赖"


def test_binary_negative_control_missing_key_and_bundle(binary_view: ai.BinaryCatalogView) -> None:
    """**负控（尺子能红）**：不存在的键 / 包必须是"没有"，⛔ 不是抛异常、也不是空表冒充。"""
    assert binary_view.key_records("THIS_KEY_DOES_NOT_EXIST_0123456789") == ()
    assert binary_view.bundle_report("no_such_bundle.bundle") == {}
    assert binary_view.path_records("Assets/no/such/path.prefab") == ()


def test_binary_view_refuses_a_file_that_is_not_a_catalog_bin(tmp_path: Path) -> None:
    """**负控**：magic 改一个字节 ⇒ `AssetError`（⛔ 不许"照样读出来"）。"""
    from ba_mod_maker.core.errors import AssetError

    source = _binary_catalog_path()
    raw = bytearray(source.read_bytes())
    raw[0] ^= 0xFF
    broken = tmp_path / "catalog.bin"
    broken.write_bytes(bytes(raw))
    with pytest.raises(AssetError):
        ai.BinaryCatalogView.load(broken)


def test_binary_run_reports_the_format_and_exits_found(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """走 `run()` 那一层：`--catalog` 指二进制那份也要**印出格式**并 rc=0。"""
    args = argparse.Namespace(
        catalog_action="key",
        catalog=str(_binary_catalog_path()),
        json_out=True,
        value="RU_T72B",
    )
    assert ai.run(args) == ai.EXIT_FOUND
    out = capsys.readouterr().out
    assert "catalog.bin" in out
