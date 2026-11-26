"""CLI 的 `clone-unit»：**新增一个单位**（把一行整套克隆成新单位）。

这一层钉四件事：

1. **读数**：源包 Units 行数 / maxId 各 +1；**别的行一行不动**；新行的五个字段对；
   「每张表插了几行」逐表与 `core/edit/clone.py::UNIT_OWNED_TABLES» 那条链一致；
2. **零写入**：`--dry-run» 后源文件 sha256 不变、`--out» 那个文件也不许出现、不留 .tmp；
3. **落点口径**：省略 `--out» = 写回打开的那个文件；给了 = 另存一份 BA_Mod.zip，
   **源文件一个字节都不动**（用户口径：「保存写回你打开的那个文件」）；
4. **两条显式逃生门**：`--hidden» / `--as-modification»（默认必须 true/false ——
   本命令的语义是「新增一个**能上场**的单位」，不是"复制一行内部数据"）。

夹具是**内存造的小包**（24 张表；9 号单位带完整的炮塔链 + 一个改装槽），毫秒级、
与游戏目录无关：`BA_MOD_MAKER_GAME» 指到一个不存在的目录，就不会去读真容器
（与 `tests/test_cli_validate_find.py::_package» 同一个手法）。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from ba_mod_maker.cli.__main__ import main as cli_main
from ba_mod_maker.core.asset import dbzip
from ba_mod_maker.core.asset.tables import TABLES
from ba_mod_maker.core.assets import game as game_mod

#: 源单位 Id（带炮塔链 + 改装槽的那个；1 号是"别动它"的对照行）。
SOURCE_ID = 9
OTHER_ID = 1

#: 克隆 9 号单位该插进去的每一行（13 张表 / 16 行）。
#: 炮塔链：TurretUnits 指着 100 与 101，而 101 的父是 100 ⇒ 两座都要克隆（老工具漏的就是这条）。
EXPECTED_COUNTS = {
    "Units": 1,
    "Turrets": 2,
    "TurretWeapons": 1,
    "UnitArmors": 1,
    "UnitPropulsions": 1,
    "SensorUnits": 1,
    "UnitAbilities": 1,
    "SpecializationAvailabilities": 1,
    "TransportAvailabilities": 1,
    "TurretUnits": 2,
    "WeaponAmmunitions": 1,
    "Modifications": 1,
    "Options": 1,
}
EXPECTED_TOTAL = sum(EXPECTED_COUNTS.values())


# --------------------------------------------------------------------------- #
# 夹具：24 张表的小包
# --------------------------------------------------------------------------- #


def _tables() -> dict[str, bytes]:
    """24 张表的最小明文：表表都有，只有 9 号单位那一套有内容。"""
    rows: dict[str, list] = {naming.table: [{"Id": 1}] for naming in TABLES}
    base = {"DisplayInArmory": True, "IsUnitModification": False}
    rows["Units"] = [
        {
            "Id": OTHER_ID,
            "Name": "BTR-82",
            "HUDName": "BTR-82",
            "ModelFileName": "RU_BTR82",
            **base,
        },
        {"Id": SOURCE_ID, "Name": "T-72B", "HUDName": "T-72B", "ModelFileName": "RU_T72B", **base},
    ]
    rows["Turrets"] = [
        {"Id": 100, "Name": "炮塔", "ParentTurretId": None},
        {"Id": 101, "Name": "子炮塔", "ParentTurretId": 100},
    ]
    rows["TurretWeapons"] = [{"Id": 200, "TurretId": 100, "WeaponId": 300}]
    rows["TurretUnits"] = [
        {"Id": 406, "UnitId": SOURCE_ID, "TurretId": 100},
        {"Id": 407, "UnitId": SOURCE_ID, "TurretId": 101},
    ]
    rows["UnitArmors"] = [{"Id": 400, "UnitId": SOURCE_ID, "ArmorId": 500}]
    rows["UnitPropulsions"] = [{"Id": 401, "UnitId": SOURCE_ID, "MobilityId": 501}]
    rows["SensorUnits"] = [{"Id": 402, "UnitId": SOURCE_ID, "SensorId": 502}]
    rows["UnitAbilities"] = [{"Id": 403, "UnitId": SOURCE_ID, "AbilityId": 503}]
    rows["SpecializationAvailabilities"] = [
        {"Id": 404, "UnitId": SOURCE_ID, "SpecializationId": 504}
    ]
    rows["TransportAvailabilities"] = [{"Id": 405, "UnitId": SOURCE_ID}]
    rows["WeaponAmmunitions"] = [
        {"Id": 408, "UnitId": SOURCE_ID, "WeaponId": 300, "AmmunitionId": 301}
    ]
    rows["Modifications"] = [{"Id": 409, "UnitId": SOURCE_ID, "Name": "改装"}]
    rows["Options"] = [{"Id": 410, "ModificationId": 409, "Turret0Id": 100}]
    return {
        naming.table: json.dumps(rows[naming.table], ensure_ascii=False).encode("utf-8")
        for naming in TABLES
    }


@pytest.fixture()
def package(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """一个 BA_Mod.zip 到临时目录（**绝不碰用户那份**）。"""
    monkeypatch.setenv(game_mod.ENV_VAR, str(tmp_path / "没有游戏"))
    path = tmp_path / "BA_Mod.zip"
    dbzip.DbZip(tables=_tables()).write(path)
    return path


# --------------------------------------------------------------------------- #
# 小助手
# --------------------------------------------------------------------------- #


def _run(capsys, *argv: str) -> tuple[int, str, str]:
    code = cli_main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _units_of(path: Path) -> list[dict]:
    return json.loads(dbzip.DbZip.read(path).tables["Units"].decode("utf-8"))


def _counts_from_report(out: str) -> dict[str, int]:
    """从人读报告里把「每张表插了几行」那一段读回来（它是唯一一处逐表读数）。"""
    lines = out.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("每张表插了几行："))
    counts: dict[str, int] = {}
    for line in lines[start + 1 :]:
        if not line.startswith("  "):
            break
        # 报告那一行是 f"  {table:26s} {count:6d}" —— 按空白切，别按字符位切
        name, number = line.split()
        counts[name] = int(number)
    return counts


# --------------------------------------------------------------------------- #
# ① 正常克隆：行数 / maxId / 别的行 / 新行字段 / 逐表读数
# --------------------------------------------------------------------------- #


def test_clone_unit_writes_back_and_bumps_the_id(capsys, package: Path) -> None:
    before = _units_of(package)
    code, out, err = _run(
        capsys,
        "clone-unit",
        "--source",
        str(package),
        "--id",
        str(SOURCE_ID),
        "--name",
        "克隆甲",
        "--model-key",
        "MY_KEY",
    )
    assert code == 0, err
    assert err == ""

    after = _units_of(package)
    assert len(after) == len(before) + 1
    assert max(row["Id"] for row in after) == max(row["Id"] for row in before) + 1
    # 原来的两行**一行不动**（克隆不许动到源单位与别的单位）
    assert after[: len(before)] == before

    fresh = after[-1]
    assert fresh == {
        "Id": 10,
        "Name": "克隆甲",
        "HUDName": "克隆甲",
        "ModelFileName": "MY_KEY",
        "DisplayInArmory": True,
        "IsUnitModification": False,
    }
    assert "新 Id     : 10" in out
    assert "ModelFileName=MY_KEY" in out
    assert _counts_from_report(out) == EXPECTED_COUNTS
    assert f"每张表插了几行：{EXPECTED_TOTAL} 行 / {len(EXPECTED_COUNTS)} 张表" in out


def test_clone_unit_copies_the_cross_table_rows_and_remaps_them(capsys, package: Path) -> None:
    """11 张自有表 + 炮塔链 + 改装槽底下的 Options 都要搬，且 UnitId 指新单位。"""
    _run(capsys, "clone-unit", "--source", str(package), "--id", str(SOURCE_ID))
    tables = {name: json.loads(blob) for name, blob in dbzip.DbZip.read(package).tables.items()}

    new_unit = 10
    for name in (
        "UnitArmors",
        "UnitPropulsions",
        "SensorUnits",
        "UnitAbilities",
        "SpecializationAvailabilities",
        "TransportAvailabilities",
        "TurretUnits",
        "WeaponAmmunitions",
        "Modifications",
    ):
        mine = [row for row in tables[name] if row.get("UnitId") == new_unit]
        assert mine, f"{name} 没有指到新单位的行"
        # 源单位那一份还在（克隆 = 两边各一份）
        assert [row for row in tables[name] if row.get("UnitId") == SOURCE_ID]

    # 炮塔链：两座炮塔、子炮塔的 ParentTurretId 指向**克隆出来的父炮塔**（不是旧的 100）
    cloned_turrets = [row for row in tables["Turrets"] if row["Id"] > 101]
    assert len(cloned_turrets) == 2
    parent = next(row for row in cloned_turrets if row["Name"] == "子炮塔")
    assert parent["ParentTurretId"] == 102, "子炮塔还指着别人的父炮塔"
    # 改装槽底下的选项：ModificationId 与 Turret0Id 都要重指
    option = tables["Options"][-1]
    new_mod = next(row for row in tables["Modifications"] if row.get("UnitId") == new_unit)
    assert option["ModificationId"] == new_mod["Id"]
    assert option["Turret0Id"] == 102


def test_name_and_model_key_are_optional_and_inherit(capsys, package: Path) -> None:
    """不给 --name / --model-key = 新行继承源行（新单位可以复用同一个模型）。"""
    _run(capsys, "clone-unit", "--source", str(package), "--id", str(SOURCE_ID))
    fresh = _units_of(package)[-1]
    assert fresh["Name"] == "T-72B"
    assert fresh["HUDName"] == "T-72B"
    assert fresh["ModelFileName"] == "RU_T72B"


def test_escape_hatches_flip_the_two_display_columns(capsys, package: Path) -> None:
    """默认 true/false；--hidden / --as-modification 是**显式**逃生门。"""
    _run(
        capsys,
        "clone-unit",
        "--source",
        str(package),
        "--id",
        str(SOURCE_ID),
        "--hidden",
        "--as-modification",
    )
    fresh = _units_of(package)[-1]
    assert fresh["DisplayInArmory"] is False
    assert fresh["IsUnitModification"] is True


# --------------------------------------------------------------------------- #
# ② --dry-run：一个字节都不写
# --------------------------------------------------------------------------- #


def test_dry_run_writes_nothing_at_all(capsys, package: Path, tmp_path: Path) -> None:
    before = _digest(package)
    never = tmp_path / "不许出现.zip"
    code, out, err = _run(
        capsys,
        "clone-unit",
        "--source",
        str(package),
        "--id",
        str(SOURCE_ID),
        "--name",
        "干跑",
        "--model-key",
        "DRY_KEY",
        "--out",
        str(never),
        "--dry-run",
    )
    assert code == 0, err
    assert _digest(package) == before, "--dry-run 动了源文件"
    assert not never.exists(), "--dry-run 造出了落点文件"
    assert list(tmp_path.glob("*.tmp")) == [], "--dry-run 留了临时文件"
    # 读数照印（干跑只是不落盘，不是不算）
    assert "新 Id     : 10" in out
    assert _counts_from_report(out) == EXPECTED_COUNTS
    assert "一个字节都没写" in out


# --------------------------------------------------------------------------- #
# ③ --id 不存在 / 打不开源：退出码 2
# --------------------------------------------------------------------------- #


def test_missing_id_exits_two_and_does_not_touch_the_file(capsys, package: Path) -> None:
    before = _digest(package)
    code, out, err = _run(capsys, "clone-unit", "--source", str(package), "--id", "999")
    assert code == 2
    assert "错误" in err and "999" in err, err
    assert "新 Id" not in out, "没克隆成还印了新 Id"
    assert _digest(package) == before


def test_unreadable_source_exits_two(capsys, tmp_path: Path) -> None:
    code, _out, err = _run(
        capsys, "clone-unit", "--source", str(tmp_path / "没有.zip"), "--id", "1"
    )
    assert code == 2
    assert "错误" in err


# --------------------------------------------------------------------------- #
# ④ --out：另存一份，源文件一个字节都不动
# --------------------------------------------------------------------------- #


def test_out_saves_a_copy_and_leaves_the_source_alone(
    capsys, package: Path, tmp_path: Path
) -> None:
    before = _digest(package)
    out_path = tmp_path / "另存" / "BA_Mod.zip"
    out_path.parent.mkdir()
    code, out, err = _run(
        capsys,
        "clone-unit",
        "--source",
        str(package),
        "--id",
        str(SOURCE_ID),
        "--name",
        "另存甲",
        "--model-key",
        "OUT_KEY",
        "--out",
        str(out_path),
    )
    assert code == 0, err
    assert _digest(package) == before, "--out 动了源文件"
    assert len(_units_of(package)) == 2, "源包被写进新行了"

    saved = _units_of(out_path)
    assert len(saved) == 3
    assert saved[-1]["Name"] == "另存甲"
    assert saved[-1]["ModelFileName"] == "OUT_KEY"
    assert str(out_path) in out


def test_out_equal_to_the_source_writes_back_in_place(capsys, package: Path) -> None:
    """--out 恰好就是打开的那个文件时按"写回"处理（判据是路径相等，不是猜）。"""
    code, _out, err = _run(
        capsys,
        "clone-unit",
        "--source",
        str(package),
        "--id",
        str(SOURCE_ID),
        "--out",
        str(package),
    )
    assert code == 0, err
    assert len(_units_of(package)) == 3


# --------------------------------------------------------------------------- #
# --help：入口与退出码成文
# --------------------------------------------------------------------------- #


def test_help_lists_the_entry_and_the_exit_codes(capsys) -> None:
    with pytest.raises(SystemExit) as exit_info:
        cli_main(["clone-unit", "--help"])
    assert exit_info.value.code == 0
    out = capsys.readouterr().out
    assert "--source" in out and "--dry-run" in out
    assert "退出码" in out and "没有落点" in out


def test_top_level_help_lists_clone_unit(capsys) -> None:
    with pytest.raises(SystemExit):
        cli_main(["--help"])
    assert "clone-unit" in capsys.readouterr().out
