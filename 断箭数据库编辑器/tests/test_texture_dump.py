"""`texture dump`（2026-10-17）：把一个单位用到的**每一张贴图**导成 PNG 的产品入口。"""

from __future__ import annotations

from ba_mod_maker.asset_kernel.texture_dump import DumpReport, DumpRow
from ba_mod_maker.cli import texture as texture_cli


def test_cli_registers_dump_with_unit_and_sheet() -> None:
    """`texture` 子命令必须认 `dump --unit`（用户点名的入口：不用再让我现写脚本）。"""
    import argparse

    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="group")
    texture_cli.add_parser(sub)
    args = parser.parse_args(["texture", "dump", "--unit", "DLC3_Yak130", "--out", "X", "--sheet"])
    assert args.action == "dump"
    assert args.unit == "DLC3_Yak130"
    assert args.out == "X"
    assert args.sheet is True


def test_dumped_counts_only_rows_with_pixels() -> None:
    """导不出来的槽（`missing=True`）**不许**算进 `dumped`（否则汇总数字会骗人）。"""
    rows = (
        DumpRow(material="m", slot="s1", texture_id=1, width=4, height=4, rgba=b"x" * 64),
        DumpRow(material="m", slot="s2", texture_id=2, missing=True, note="纹素不在包里"),
    )
    report = DumpReport(
        unit_key="U", asset_path="A", bundle="B", materials=("m",), rows=rows
    )
    assert report.dumped == 1


def test_safe_filename_strips_windows_reserved_characters() -> None:
    """材质名里带空格 / `/` 也要能落成文件名（Yak130 的真名就是 `DLC3_Yak-130 1`）。"""
    assert texture_cli._safe("DLC3_Yak-130 1") == "DLC3_Yak-130 1"
    assert texture_cli._safe("a/b:c") == "a_b_c"
    assert texture_cli._safe("") == "unnamed"
