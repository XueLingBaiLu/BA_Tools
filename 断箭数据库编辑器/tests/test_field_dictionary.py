"""字段词典的守卫（零 Qt）：中文名、枚举、**位掩码逐位中文**、控件判定。"""

from __future__ import annotations

import pytest

from ba_mod_maker.core.edit.dictionary import (
    CONTROL_BOOL,
    CONTROL_ENUM,
    CONTROL_FOREIGN,
    CONTROL_MASK,
    CONTROL_NUMBER,
    CONTROL_RESOURCE,
    CONTROL_TEXT,
    control_for,
    decode,
    field_info,
)


def test_chinese_labels_come_from_the_field_dictionary() -> None:
    assert field_info("Units", "Cost").cn == "基础成本"
    assert field_info("Ammunitions", "TargetType").cn == "可瞄准目标"
    assert field_info("Units", "MaxStress").cn == "压制值上限"


def test_unknown_column_falls_back_to_the_english_name_and_flags_itself() -> None:
    """词典里没有的列：显示英文列名，并**如实标记** missing（界面当待补的口子）。"""
    info = field_info("Units", "没有这一列")
    assert info.cn == "没有这一列"
    assert info.missing is True
    assert field_info("Units", "Cost").missing is False


def test_docs_are_present_and_not_tautological() -> None:
    """注释是"它管什么"，不是"这是成本"这种同义反复。"""
    doc = field_info("Units", "Cost").doc
    assert "相对价" in doc or "基" in doc
    assert len(doc) > 20


@pytest.mark.parametrize(
    ("value", "want"),
    [(1, "地面"), (2, "步兵"), (4, "载具"), (8, "直升机"), (16, "飞机"), (32, "舰船")],
)
def test_target_type_decodes_single_bits(value: int, want: str) -> None:
    """实测口径：**1 = 地面、2 = 步兵**（旧列清单写"1 是步兵"是错的）。"""
    info = field_info("Ammunitions", "TargetType")
    assert decode(info, value).text == want


def test_mask_is_spelled_out_bit_by_bit_and_keeps_the_raw_value() -> None:
    """36 = 载具 + 舰船；原始值必须留在旁边备查。"""
    info = field_info("Ammunitions", "TargetType")
    decoded = decode(info, 36)
    assert decoded.text == "载具 + 舰船"
    assert decoded.raw_text == "36"
    assert decoded.kind == "mask"
    assert decoded.unknown is False


def test_mask_with_an_unregistered_bit_says_so() -> None:
    """ "没登记"要如实说，不许编一个词把它盖住。"""
    info = field_info("Ammunitions", "TargetType")
    decoded = decode(info, 9999)
    assert decoded.unknown is True
    assert decoded.raw_text == "9999"


def test_enum_decodes_and_marks_unregistered_values() -> None:
    info = field_info("Ammunitions", "TrajectoryType")
    assert decode(info, 10).text == "直射弹药"
    assert decode(info, 110).text == "导弹"
    unknown = decode(info, 777)
    assert unknown.unknown is True
    assert unknown.text == "777"


def test_bool_shows_yes_or_no() -> None:
    info = field_info("Units", "DisplayInArmory")
    assert decode(info, True).text == "是"
    assert decode(info, False).text == "否"


def test_controls_by_type() -> None:
    assert field_info("Units", "Cost").control == CONTROL_TEXT  # 词典没给 kind -> 交给取值类型
    assert control_for(field_info("Units", "Cost"), "int") == CONTROL_NUMBER
    assert control_for(field_info("Units", "Name"), "text") == CONTROL_TEXT
    assert control_for(field_info("Units", "DisplayInArmory"), "bool") == CONTROL_BOOL
    assert control_for(field_info("Ammunitions", "TrajectoryType"), "int") == CONTROL_ENUM
    assert control_for(field_info("Ammunitions", "TargetType"), "int") == CONTROL_MASK


def test_foreign_keys_get_the_search_control_and_know_their_table() -> None:
    info = field_info("UnitArmors", "ArmorId")
    assert info.control == CONTROL_FOREIGN
    assert info.foreign == "Armors"
    assert info.is_foreign is True


def test_resource_key_columns_are_marked_as_such() -> None:
    """ModelFileName / HUDIcon / OptionPicture 这些存的是**文件名键**，不是数。"""
    assert field_info("Units", "ModelFileName").control == CONTROL_RESOURCE
    assert field_info("Units", "HUDIcon").control == CONTROL_RESOURCE
    assert field_info("Options", "OptionPicture").control == CONTROL_RESOURCE


def test_strong_assumptions_are_visible() -> None:
    """词典里有 7 条「强假设」——界面可以据此加一句小字，所以它必须能被读到。"""
    evidence = {field_info("Units", column).evidence for column in ("Role",)}
    assert evidence <= {"强假设", "已确认", "推断"}


def test_dictionary_covers_the_whole_library() -> None:
    """391 + 16 = 407：两份词典在列集合上互补（README 的口径）。"""
    from ba_mod_maker.core.asset.tables import resolve_table_name
    from ba_mod_maker.core.data import find_dataset_root, load_dataset
    from ba_mod_maker.core.edit.dictionary import field_enums, field_labels

    root = find_dataset_root()
    if root is None:
        pytest.skip("未找到 DataBaseCompiled 数据源")
    instance = load_dataset(root).instance("52085")
    labels = field_labels()
    enums = field_enums()
    total = 0
    missing: list[str] = []
    for name in instance.table_names:
        # 词典的键是**数据侧那一套**叫法（改过名的那张在那里叫 PlaneFlyPresets），
        # 而实例交出来的是规范名 —— 查前先归一（口径见 core/edit/dictionary.py）。
        key = resolve_table_name(name, labels.keys()) or name
        for column in instance.table(name).columns:
            total += 1
            if column in labels.get(key, {}):
                continue
            if f"{key}.{column}" in enums:
                continue
            missing.append(f"{name}.{column}")
    assert total == 407, f"全库列数变了：{total}"
    assert not missing, f"词典没覆盖到的列：{missing}"
