r"""炮塔装机那一跳：按行下标改 `Turrets.ModelFileName`（`rewrite_zip_row` 的 table/row_index）。

**判据一行**：`.venv\Scripts\python.exe -m pytest tests/test_turret_row_rewrite.py -q -n 0`

| 段 | 判据 |
|---|---|
| A | 按行下标改 Turrets[1] ⇒ 只动那一格；Units 逐字节不动 |
| B | 行下标越界 ⇒ 点名拒绝（不许静默挑一行） |
| C | 非规范写法（带缩进）⇒ 拒绝（反向 Δ 会退化成整份表） |
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ba_mod_maker.core.asset import dbzip, new_asset
from ba_mod_maker.core.errors import AssetError


def _canon(obj: object) -> bytes:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _write(tmp_path: Path, turrets: bytes) -> Path:
    path = tmp_path / "BA_Mod.zip"
    path.parent.mkdir(parents=True, exist_ok=True)
    dbzip.DbZip(tables={"Units": b"[{\"Id\": 1}]", "Turrets": turrets}).write(path)
    return path


def test_a_rewrites_only_the_selected_turret_row(tmp_path: Path) -> None:
    turrets = _canon([{"Id": 1, "ModelFileName": "REAL/TURRET"},
                      {"Id": 2, "ModelFileName": "REAL/CUPOLA"}])
    zip_path = _write(tmp_path, turrets)
    edit = new_asset.rewrite_zip_row(
        zip_path,
        unit="",
        key="NEWTURRET_A",
        column="ModelFileName",
        table="Turrets",
        row_index=1,
    )
    assert (edit.row_index, edit.row_id, edit.old_key) == (1, 2, "REAL/CUPOLA")
    out = tmp_path / "after.zip"
    out.write_bytes(edit.zip_after)
    before = dbzip.DbZip.read(zip_path)
    after = dbzip.DbZip.read(out)
    rows = json.loads(after.tables["Turrets"].decode("utf-8"))
    assert rows[1]["ModelFileName"] == "NEWTURRET_A"
    assert rows[0]["ModelFileName"] == "REAL/TURRET"
    assert after.tables["Units"] == before.tables["Units"]


def test_b_rejects_an_out_of_range_row(tmp_path: Path) -> None:
    zip_path = _write(tmp_path, _canon([{"Id": 1, "ModelFileName": "REAL/TURRET"}]))
    with pytest.raises(AssetError) as excinfo:
        new_asset.rewrite_zip_row(
            zip_path, unit="", key="K", column="ModelFileName", table="Turrets", row_index=9
        )
    assert "行下标" in str(excinfo.value)


def test_c_rejects_a_non_canonical_turrets_table(tmp_path: Path) -> None:
    pretty = json.dumps([{"Id": 1, "ModelFileName": "REAL/TURRET"}], ensure_ascii=False,
                       indent=2).encode("utf-8")
    zip_path = _write(tmp_path, pretty)
    with pytest.raises(AssetError) as excinfo:
        new_asset.rewrite_zip_row(
            zip_path, unit="", key="K", column="ModelFileName", table="Turrets", row_index=0
        )
    assert "序列化口径" in str(excinfo.value)
