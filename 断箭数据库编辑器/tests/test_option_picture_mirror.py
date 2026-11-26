"""选项图片**逐资产**判翻不翻的判据（用户 2026-10-03 当场报）。

用户原话：「这个改装的图标跟游戏是反的」。

实测（`_work/w9c_probe_flip4.py`，同一把尺子带**两条方向相反**的正控）：

| 游戏里画的那张 | IoU(原图) | IoU(镜像) | 结论 |
|---|---|---|---|
| 正控A 武器列表 OSV-96 | 0.330 | 0.437 | 镜像 ✓（已知镜像） |
| 正控B 选项卡 OSV-96（MOD_ 那批） | 0.647 | 0.157 | **原图** ✓（已知原图） |
| 待测 选项卡 长程型（INF_T5000） | 0.284 | 0.866 | **镜像** |

⇒ 同一族里 `INF_*` 要翻、`MOD_*` 不能翻，一个族级布尔量表达不了 ——
翻不翻走 `families.family_mirrors(name, key)`，界面侧唯一出口是 `prepare_icon(..., key)`。

⛔ 遍历、命令：`.venv/Scripts/python.exe _work/w9c_probe_flip4.py`
"""

from __future__ import annotations

import pytest

from ba_mod_maker.app.theme.icons import prepare_icon
from ba_mod_maker.core.assets import families
from ba_mod_maker.core.assets.families import (
    AMMO_ICON_FAMILY,
    OPTION_PICTURE_FAMILY,
    WEAPON_ICON_FAMILY,
    family_mirrors,
)

# --------------------------------------------------------------------------- #
# ① 规则本身：按资产判
# --------------------------------------------------------------------------- #


def test_mod_option_pictures_are_not_flipped() -> None:
    """实测：游戏选项卡里 MOD_FSB_SNIPER_OSV96 画的是**原图**（IoU 0.647 : 0.157）。"""
    assert family_mirrors(OPTION_PICTURE_FAMILY, "MOD_FSB_SNIPER_OSV96") is False
    assert family_mirrors(OPTION_PICTURE_FAMILY, "mod_fsb_sniper_osv96") is False, "大小写不敏感"


def test_weapon_icon_option_pictures_are_still_flipped() -> None:
    """实测：同一屏里 INF_T5000 那张画的是**镜像**（IoU 0.866 : 0.284）。"""
    assert family_mirrors(OPTION_PICTURE_FAMILY, "INF_T5000") is True


def test_the_family_defaults_stay_untouched() -> None:
    """③ 未知族 / 未知键 / **没传键**时的默认行为。

    没传键（key=""）时必须落回族默认 = 翻 —— 与改前**逐字一致**：
    拿不到键的调用方不许被悄悄改行为。其余族的族级口径也一个字没动。
    """
    assert family_mirrors(OPTION_PICTURE_FAMILY) is True, "没传键 -> 族默认"
    assert family_mirrors(WEAPON_ICON_FAMILY, "INF_OSV96") is True
    assert family_mirrors(WEAPON_ICON_FAMILY) is True
    assert family_mirrors(AMMO_ICON_FAMILY, "AMMO_KONKURS_M") is False, "弹药那族本来就朝右"
    assert family_mirrors("没登记过的族", "任意键") is False


def test_the_rule_is_what_the_assertions_are_reading(monkeypatch) -> None:
    """负控（必须红）：把规则反过来喂进去，上面那两条断言就得反过来。

    能红才说明这两条断言读的是**规则**，不是常量巧合。
    """
    monkeypatch.setattr(families, "OPTION_PICTURE_UNMIRRORED_PREFIXES", ("INF_",))
    assert family_mirrors(OPTION_PICTURE_FAMILY, "MOD_FSB_SNIPER_OSV96") is True
    assert family_mirrors(OPTION_PICTURE_FAMILY, "INF_T5000") is False
    monkeypatch.setattr(families, "OPTION_PICTURE_UNMIRRORED_PREFIXES", ())
    assert family_mirrors(OPTION_PICTURE_FAMILY, "MOD_FSB_SNIPER_OSV96") is True


# --------------------------------------------------------------------------- #
# ② 唯一出口真的按它办：prepare_icon 出来的图，像素级左右互换
# --------------------------------------------------------------------------- #


def _left_right_pixmap():
    """8x2：左半红、右半蓝 —— 一看就知道有没有被水平翻。"""
    from PySide6.QtCore import QSize
    from PySide6.QtGui import QColor, QImage, QPixmap

    box = QSize(8, 2)
    image = QImage(box, QImage.Format.Format_ARGB32)
    image.fill(QColor(255, 0, 0))
    for y in range(2):
        for x in range(4, 8):
            image.setPixelColor(x, y, QColor(0, 0, 255))
    return QPixmap.fromImage(image), box


def _ends(pixmap) -> tuple[bool, bool]:
    """(左端是不是蓝的, 右端是不是蓝的) —— 只看两端，避开插值。"""
    image = pixmap.toImage()
    left = image.pixelColor(0, 0).blue() > 200
    right = image.pixelColor(image.width() - 1, 0).blue() > 200
    return left, right


def test_prepare_icon_flips_by_key_not_by_family(qapp) -> None:
    """同一族、同一尺寸，只换 key —— MOD_ 不翻、INF_ 翻。"""
    pixmap, box = _left_right_pixmap()
    kept = prepare_icon(pixmap, OPTION_PICTURE_FAMILY, box, None, "MOD_FSB_SNIPER_OSV96")
    flipped = prepare_icon(pixmap, OPTION_PICTURE_FAMILY, box, None, "INF_T5000")
    assert _ends(kept) == (False, True), "MOD_ 那张被翻了（用户报的就是这个）"
    assert _ends(flipped) == (True, False), "INF_ 那张没翻"


def test_prepare_icon_matches_the_rule_for_every_option_picture_key(qapp) -> None:
    """尺子扫一遍缓存里全部选项图片键：出口画出来的朝向 = family_mirrors 的判据。

    负控在 `test_the_rule_is_what_the_assertions_are_reading`（把规则反过来必须红）。
    """
    import os
    from pathlib import Path

    from ba_mod_maker.core.assets.families import sanitize

    cache = Path(os.environ.get("LOCALAPPDATA", "")) / "BA_Mod_Maker" / "icons"
    directory = cache / OPTION_PICTURE_FAMILY
    if not directory.is_dir():
        pytest.skip("这台机器没有图标缓存，扫不出这条")
    keys = sorted(path.stem for path in directory.glob("*.png"))
    keys = [key for key in keys if key]
    if not keys:
        pytest.skip("图标缓存里一张选项图片都没有")

    pixmap, box = _left_right_pixmap()
    checked = 0
    for stem in keys:
        # 缓存文件名是 sanitize(key)，MOD_ 前缀在 sanitize 之后原样保留
        assert sanitize(stem) == stem
        out = prepare_icon(pixmap, OPTION_PICTURE_FAMILY, box, None, stem)
        drew_flipped = _ends(out)[0]
        assert drew_flipped == family_mirrors(OPTION_PICTURE_FAMILY, stem), stem
        checked += 1
    assert checked > 100, f"只扫到 {checked} 张，这条尺子没在工作"
    assert any(stem.startswith("MOD_") for stem in keys), "缓存里没有 MOD_ 那批，这条测不到用户那格"
