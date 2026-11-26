r"""L7 节点命名契约闸（`asset_kernel/node_contract.py`）的机械判据。

**判据一行**：`.venv\\Scripts\\python.exe -m pytest tests/test_node_contract.py -q -n 0`
-> 通过数**从 0 收集变 N passed**（本文件新建前不存在）。

| 段 | 判据 | 通过线 |
|---|---|---|
| A | **基线**：一棵合成单位树（含 root/body/Chassis/Base*/LR001/L001/NNN_skinned_*）全绿 | ok |
| B | **变异自检**：15 条硬规则**每一条**都有一种变异能让它红，且红的正是那一条 | 15/15 |
| C | **假红负控**：引擎本来就大小写不敏感的地方（`body`->`Body`）**不许红** | 全绿 |
| D | **正控**：真件 `Chassis` -> `chassis` ⇒ 恰好 1 条红，点名 `chassis.case` + 级别 | 1 条 |
| E | **真件负控**：`_work/l7/units_roots.json` 里**原版 285 件全绿** | 285/285 |
| F | **尺子只有一份**：大小写那条用的是 `unit_shape.wheel_side/suspension_side` 本体 | 同一对象 |
| G | 报告形状：逐条命中读数都在；`problems()` 每条带规则 key | 有 |

⚠ E 段吃的是**冻结抽件**（`_work/l7/units_roots.json`，由 `_work/l7/extract_units_roots.py`
一次开包产出）—— 不开包、不读游戏盘、写盘零字节，所以**不是 slow**。文件不在就 skip（与
`tests/test_prefab_skins.py` 对 `_work/w10_ref/units_meta.bin` 的口径一致）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from ba_mod_maker.asset_kernel import node_contract as nc
from ba_mod_maker.asset_kernel import prefab as pf
from ba_mod_maker.asset_kernel import unit_shape

import os
# 工作根：数据已移出仓（2026-11-09，统一入口）。
WORK = Path(os.environ.get("BA_WORK_ROOT") or r"D:\断箭模组制作\_work_db")

REPO_ROOT = Path(__file__).resolve().parents[1]
FROZEN = WORK / 'l7' / 'units_roots.json'

UNIT = 'RU_T72B_Base'


# --------------------------------------------------------------------------- #
# 合成树（纯 dict，⛔ 不读包）
# --------------------------------------------------------------------------- #
def build(spec: Any, *, kinds: dict[str, tuple[str, ...]] | None = None) -> list[dict[str, Any]]:
    """`(名字, [孩子…])` 的嵌套形状 -> 归一化节点序列（`unitypy_source` 的契约）。"""
    extra = kinds or {}
    out: list[dict[str, Any]] = []

    def walk(item: Any, parent: int | None) -> None:
        name, kids = item[0], item[1]
        index = len(out)
        tid = 100 + index
        out.append(
            {
                'name': name,
                'path_id': tid + 1000,
                'transform_id': tid,
                'parent_transform': parent,
                'components': tuple(
                    {'kind': k, 'path_id': 9000 + j} for j, k in enumerate(extra.get(name, ()))
                ),
            }
        )
        for kid in kids:
            walk(kid, tid)

    walk(spec, None)
    return out


#: 一棵**最小但完整**的单位树：root/body（骨骼容器）、Chassis 的三个 Base*、轮/悬挂、
#: 三位序号的 LOD 替身、根上两颗组件。
CANON: Any = (
    UNIT,
    [
        ('root', [('body', [('turret_0', [('weapon_0', [('shell_spawn_0_0', [])])])])]),
        (
            'Chassis',
            [
                ('closed', [('000_skinned_closed', []), ('001_skinned_open', [])]),
                ('BaseArmor', []),
                ('BaseAbility', []),
                ('BaseMobility', []),
                ('LR001', []),
                ('RR001', []),
                ('L001', []),
                ('R001', []),
            ],
        ),
    ],
)
CANON_KINDS = {
    UNIT: ('MB:UnitPrefabRoot', 'MB:AnimationManager'),
}


def canon(**kinds: tuple[str, ...]) -> list[dict[str, Any]]:
    """基线树；`kinds` 覆盖某几个节点上的组件（默认根上两颗）。"""
    return build(CANON, kinds=(kinds or CANON_KINDS))


def _nodes_of(tree: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {n['name']: n for n in tree}


def _keys_of(lines: tuple[str, ...]) -> dict[str, str]:
    """`hit_lines()` 的行 -> {规则 key: 整行}（行里有 `·档位` 后缀，这里剥掉）。"""
    out: dict[str, str] = {}
    for line in lines:
        head = line.split('：')[0].strip().split(' ', 1)[1]
        out[head.split('·', 1)[0]] = line
    return out


def rename(tree: list[dict[str, Any]], frm: str, to: str) -> list[dict[str, Any]]:
    """改名（名字唯一，合成树够用）。"""
    return [
        {**n, 'name': to} if n['name'] == frm else n for n in tree
    ]


def reparent(tree: list[dict[str, Any]], child: str, new_parent: str) -> list[dict[str, Any]]:
    """把 `child` 挂到 `new_parent` 下（改的是 parent_transform）。"""
    by_name = _nodes_of(tree)
    ids: dict[int, int] = {}
    for n in tree:
        ids[int(n['path_id'])] = int(n['transform_id'])
    target = int(by_name[new_parent]['transform_id'])
    return [
        {**n, 'parent_transform': target} if n['name'] == child else n for n in tree
    ]


def drop(tree: list[dict[str, Any]], name: str) -> list[dict[str, Any]]:
    """删掉 `name` 那一棵子树。"""
    by_name = _nodes_of(tree)
    drop_ids = {int(by_name[name]['transform_id'])}
    changed = True
    while changed:
        changed = False
        for n in tree:
            parent = n.get('parent_transform')
            if parent is not None and int(parent) in drop_ids \
                    and int(n['transform_id']) not in drop_ids:
                drop_ids.add(int(n['transform_id']))
                changed = True
    return [n for n in tree if int(n['transform_id']) not in drop_ids]


#: 15 条硬规则 -> 造一个**只该违反它**的变异（`expected_root_name` 那条单独给）。
MUTATIONS: dict[str, Any] = {
    'root.present': lambda t: drop(t, 'root'),
    'body.present': lambda t: drop(t, 'body'),
    'chassis.case': lambda t: rename(t, 'Chassis', 'chassis'),
    'base_armor.case': lambda t: rename(t, 'BaseArmor', 'basearmor'),
    'base_armor.under_chassis': lambda t: reparent(t, 'BaseArmor', UNIT),
    'base_ability.case': lambda t: rename(t, 'BaseAbility', 'baseability'),
    'base_ability.under_chassis': lambda t: reparent(t, 'BaseAbility', UNIT),
    'base_mobility.case': lambda t: rename(t, 'BaseMobility', 'basemobility'),
    'base_mobility.under_chassis': lambda t: reparent(t, 'BaseMobility', UNIT),
    'wheel.case': lambda t: rename(t, 'LR001', 'lr001'),
    'suspension.case': lambda t: rename(t, 'L001', 'l001'),
    'skinned.index_digits': lambda t: rename(t, '000_skinned_closed', '00_skinned_closed'),
    'component.UnitPrefabRoot_on_root': lambda t: build(
        CANON, kinds={'root': ('MB:AnimationManager',), 'body': ('MB:UnitPrefabRoot',)}
    ),
    'component.AnimationManager_on_root': lambda t: build(
        CANON, kinds={'root': ('MB:UnitPrefabRoot',), 'body': ('MB:AnimationManager',)}
    ),
}


# --------------------------------------------------------------------------- #
# A · 基线全绿
# --------------------------------------------------------------------------- #
def test_a_baseline_is_green() -> None:
    report = nc.check_nodes(canon(), name=UNIT, expected_root_name=UNIT)
    assert report.ok, report.lines()
    assert report.nodes == 17
    assert not report.problems()


def test_a_baseline_without_expected_name_skips_that_rule() -> None:
    """算不出单位名时不判 `unit_root.name` —— 本模块**不猜**单位名。"""
    report = nc.check_nodes(canon(), name=UNIT)
    assert report.ok, report.lines()
    assert report.hits.get('unit_root.name', 0) == 0


# --------------------------------------------------------------------------- #
# B · 变异自检：15 条硬规则每一条都能红
# --------------------------------------------------------------------------- #
def test_b_hard_keys_match_the_mutation_table() -> None:
    assert set(nc.HARD_KEYS) == set(MUTATIONS) | {'unit_root.name'}, (
        sorted(set(nc.HARD_KEYS) ^ (set(MUTATIONS) | {'unit_root.name'}))
    )


@pytest.mark.parametrize('key', sorted(MUTATIONS))
def test_b_every_hard_rule_can_go_red(key: str) -> None:
    baseline = nc.check_nodes(canon(), name=UNIT, expected_root_name=UNIT)
    assert baseline.ok, f'变异自检的前提是基线绿：{baseline.lines()}'
    mutated = MUTATIONS[key](canon())
    report = nc.check_nodes(mutated, name=UNIT, expected_root_name=UNIT)
    assert not report.ok, f'{key} 的变异**没能变红** —— 这条硬规则是死尺子'
    fired = {v.rule for v in report.hard_violations}
    assert key in fired, f'{key} 的变异红在 {sorted(fired)} 上（点名点错了）'


def test_b_unit_root_name_rule_can_go_red() -> None:
    report = nc.check_nodes(canon(), name=UNIT, expected_root_name='RU_NOT_THIS_ONE')
    assert not report.ok
    fired = {v.rule for v in report.hard_violations}
    assert fired == {'unit_root.name'}, report.lines()


def test_b_every_hard_violation_names_its_level() -> None:
    """逐条点名**哪一级**哪一条：每行必须既有规则 key、又有一级（父名）的落点。"""
    mutated = rename(canon(), 'Chassis', 'chassis')
    report = nc.check_nodes(mutated, name=UNIT)
    bad = next(v for v in report.hard_violations if v.rule == 'chassis.case')
    assert bad.level == f'{UNIT} 的直接子节点'
    assert bad.path == f'{UNIT}/chassis'
    assert 'chassis.case' in bad.line() and bad.level in bad.line()


# --------------------------------------------------------------------------- #
# C · 假红负控：引擎本来就大小写不敏感的地方不许红
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize('frm,to', [('root', 'Root'), ('root', 'ROOT'),
                                    ('body', 'Body'), ('body', 'BODY')])
def test_c_case_insensitive_names_stay_green(frm: str, to: str) -> None:
    """`BoneContainer.Bake` 是 `ToLower()` 之后全等 ⇒ 改大小写**不是**故障。

    真件 `US_LAV_AT-M_body` 那棵的 body 就叫 `Body`（实测，见 _work/l7/measure_contract.py）。
    """
    report = nc.check_nodes(rename(canon(), frm, to), name=UNIT, expected_root_name=UNIT)
    assert report.ok, report.lines()


def test_c_digit_count_of_a_wheel_is_not_a_violation() -> None:
    """`R1`（长 2）/ `LR0010`（4 位）真件里就有 ⇒ 引擎自己也不认它们，**不算违规**。"""
    for name in ('R1', 'LR0010', 'L12345'):
        tree = rename(canon(), 'LR001', name)
        report = nc.check_nodes(tree, name=UNIT, expected_root_name=UNIT)
        assert report.ok, (name, report.lines())


def test_c_lowercase_wheel_is_a_violation_and_says_why() -> None:
    report = nc.check_nodes(rename(canon(), 'LR001', 'lr001'), name=UNIT)
    (bad,) = report.hard_violations
    assert bad.rule == 'wheel.case'
    assert 'LR0' in bad.detail and '大小写' in bad.detail


# --------------------------------------------------------------------------- #
# D · 正控（交接文档 §3 L7 逐字要的那一条）
# --------------------------------------------------------------------------- #
def test_d_chassis_to_lowercase_is_the_positive_control() -> None:
    """正控：把 `Chassis` 改成 `chassis` ⇒ 红，而且**级联也点到名**。

    红的是 1 条 `chassis.case` ＋ 3 条 `base_*.under_chassis` —— 后者不是噪声：引擎是
    `FindChild("Chassis")` 先失败，三个 Base* 的 `FindChildren` 就再也够不着了（同一条静默链）。
    """
    report = nc.check_nodes(rename(canon(), 'Chassis', 'chassis'), name=UNIT)
    fired = [v.rule for v in report.hard_violations]
    assert fired.count('chassis.case') == 1
    assert set(fired) == {'chassis.case', 'base_armor.under_chassis',
                          'base_ability.under_chassis', 'base_mobility.under_chassis'}
    for bad in report.hard_violations:
        assert bad.level, bad.line()


# --------------------------------------------------------------------------- #
# E · 真件负控：原版 285 件全绿
# --------------------------------------------------------------------------- #
@pytest.fixture(scope='module')
def frozen() -> dict[str, Any]:
    if not FROZEN.is_file():
        pytest.skip(f'缺冻结抽件 {FROZEN}（跑 _work/l7/extract_units_roots.py 生成）')
    with FROZEN.open(encoding='utf-8') as fh:
        return json.load(fh)


def test_e_frozen_extraction_is_the_285_unit_roots(frozen: dict[str, Any]) -> None:
    """抽件本身要先自证：285 件、27492 个节点（与 _work/dig/struct 那次普查同源）。"""
    assert frozen['unit_root_count'] == 285
    assert len(frozen['prefabs']) == 285
    assert sum(len(p['nodes']) for p in frozen['prefabs']) == 27492


def test_e_all_285_originals_are_green(frozen: dict[str, Any]) -> None:
    reds = []
    for entry in frozen['prefabs']:
        report = nc.check_nodes(
            entry['nodes'], name=entry['root_name'], expected_root_name=entry['root_name']
        )
        if not report.ok:
            reds.append((entry['root_name'], [v.line() for v in report.hard_violations]))
    assert not reds, f'原版件假红 {len(reds)} 件：{reds[:3]}'


def test_e_positive_control_on_a_real_unit(frozen: dict[str, Any]) -> None:
    """真件上把 `Chassis` 改成 `chassis` ⇒ 恰好 1 条红（交接 §3 L7 的判据）。"""
    target = None
    for entry in frozen['prefabs']:
        if any(n['name'] == 'Chassis' and n['parent'] == entry['root_name']
               for n in entry['nodes']):
            target = entry
            break
    assert target is not None, '285 件里居然找不到一件根直系子叫 Chassis'
    before = nc.check_nodes(target['nodes'], name=target['root_name'])
    assert before.ok, before.lines()
    mutated = [
        {**n, 'name': 'chassis'} if n['name'] == 'Chassis' else n for n in target['nodes']
    ]
    after = nc.check_nodes(mutated, name=target['root_name'])
    assert len(after.hard_violations) == 1, after.lines()
    assert after.hard_violations[0].rule == 'chassis.case'


def test_e_a_real_body_spelled_Body_is_still_green(frozen: dict[str, Any]) -> None:
    """真件 `US_LAV_AT-M_body` 的 body 是 `Body`（引擎 ToLower）⇒ 它必须绿。"""
    hits = [
        p for p in frozen['prefabs']
        if any(n['name'] == 'Body' for n in p['nodes'])
    ]
    assert hits, '抽件里没有哪一件的 body 写成 Body —— 这条负控失去意义（换一条）'
    for entry in hits:
        report = nc.check_nodes(
            entry['nodes'], name=entry['root_name'], expected_root_name=entry['root_name']
        )
        assert report.ok, report.lines()


# --------------------------------------------------------------------------- #
# F · 尺子只有一份
# --------------------------------------------------------------------------- #
def test_f_case_ruler_is_unit_shape_itself() -> None:
    """大小写那一条**不许**另写一套前缀正则：用的必须是 `unit_shape` 那两个函数本体。"""
    matchers = {key: fn for key, _why, fn in nc.CASE_MATCHERS}
    assert matchers['wheel.case'] is unit_shape.wheel_side
    assert matchers['suspension.case'] is unit_shape.suspension_side


def test_f_module_does_not_re_implement_the_prefixes() -> None:
    """没有第二份 `LR0` / `RR0` / `L0` / `R0` 字面量表。"""
    source = (REPO_ROOT / 'src' / 'ba_mod_maker' / 'asset_kernel'
              / 'node_contract.py').read_text(encoding='utf-8')
    assert 'LR0' not in source.replace('unit_shape', '')  or source.count("'LR0'") == 0
    assert 'WHEEL_PREFIXES' not in source and 'SUSPENSION_PREFIXES' not in source


# --------------------------------------------------------------------------- #
# G · 形状与两种输入
# --------------------------------------------------------------------------- #
def test_g_hit_lines_cover_every_rule() -> None:
    report = nc.check_nodes(canon(), name=UNIT, expected_root_name=UNIT)
    lines = report.hit_lines()
    assert len(lines) == len(nc.RULES)
    joined = '\n'.join(lines)
    for rule in nc.RULES:
        assert rule.key in joined


def test_g_absent_family_is_a_dot_not_a_cross() -> None:
    """条件契约（本件没有这一类节点）不许画成红的 —— 真件里也只有 106/285 件有 BaseArmor。"""
    report = nc.check_nodes(drop(canon(), 'BaseArmor'), name=UNIT, expected_root_name=UNIT)
    assert report.ok, report.lines()
    lines = _keys_of(report.hit_lines())
    assert lines['base_armor.case'].lstrip().startswith('·'), lines['base_armor.case']
    assert lines['root.present'].lstrip().startswith('✔'), lines['root.present']
    # 而真报违规的那一条必须是 ✗
    bad = nc.check_nodes(rename(canon(), 'Chassis', 'chassis'), name=UNIT)
    marks = _keys_of(bad.hit_lines())
    assert marks['chassis.case'].lstrip().startswith('✗'), marks['chassis.case']


def _all_violations() -> tuple[nc.Violation, ...]:
    """把**每条**硬规则都逼出一条违规，收齐判据行（变异自检那段用的同一批变异）。"""
    out: list[nc.Violation] = []
    for key, mutate in MUTATIONS.items():
        report = nc.check_nodes(mutate(canon()), name=UNIT, expected_root_name=UNIT)
        out.extend(v for v in report.hard_violations if v.rule == key)
    report = nc.check_nodes(canon(), name=UNIT, expected_root_name='RU_OTHER')
    out.extend(v for v in report.hard_violations if v.rule == 'unit_root.name')
    return tuple(out)


def test_h_every_line_carries_its_impact_tier() -> None:
    """Lead 裁定（2026-10-17）：**每一行都要把档位名印出来**。

    15 条硬规则里 `convention` 那一档不是引擎硬要求 —— ⛔ 不该让用户以为它们是一回事。
    """
    bad = _all_violations()
    assert {v.rule for v in bad} == set(nc.HARD_KEYS), sorted({v.rule for v in bad})
    for violation in bad:
        rule = next(r for r in nc.RULES if r.key == violation.rule)
        assert violation.impact == rule.impact
        assert '·' + rule.impact + ']' in violation.line(), violation.line()
    assert {v.impact for v in bad} == {'silent_failure', 'lookup', 'convention'}
    for name in sorted({v.impact for v in bad}):
        assert nc.impact_words(name)
        assert not nc.impact_words(name).startswith('（未登记')


def test_h_report_prints_a_legend_for_the_tiers_it_hits() -> None:
    report = nc.check_nodes(rename(canon(), 'Chassis', 'chassis'), name=UNIT)
    joined = '\n'.join(report.lines())
    assert '档位：' in joined and 'silent_failure' in joined
    green = nc.check_nodes(canon(), name=UNIT, expected_root_name=UNIT)
    assert '档位：' in '\n'.join(green.lines())


def test_h_every_rule_line_carries_its_tier_too() -> None:
    report = nc.check_nodes(drop(canon(), 'BaseArmor'), name=UNIT, expected_root_name=UNIT)
    for rule in nc.RULES:
        assert rule.key in ' '.join(report.hit_lines())
    line = _keys_of(report.hit_lines())['skinned.index_digits']
    assert 'skinned.index_digits·convention' in line, line


def test_g_problems_are_the_hard_ones_only() -> None:
    report = nc.check_nodes(rename(canon(), 'Chassis', 'chassis'), name=UNIT)
    problems = report.problems()
    assert len(problems) == len(report.hard_violations) == 4
    assert problems[0].startswith('★ [chassis.case·')
    assert all(line.startswith('★ [') for line in problems)


def test_g_check_tree_accepts_a_prefab_tree() -> None:
    """`prefab.build_tree` 出来的 `PrefabTree` 与同一份输入判出同样的结果。"""
    nodes = canon()
    tree = pf.build_tree(nodes)
    from_tree = nc.check_tree(tree, name=UNIT, expected_root_name=UNIT)
    from_nodes = nc.check_nodes(nodes, name=UNIT, expected_root_name=UNIT)
    assert from_tree.ok and from_nodes.ok
    assert from_tree.hits == from_nodes.hits
    assert from_tree.nodes == from_nodes.nodes == len(nodes)


def test_g_check_tree_reports_the_same_violation() -> None:
    tree = pf.build_tree(rename(canon(), 'Chassis', 'chassis'))
    report = nc.check_tree(tree, name=UNIT)
    bad = next(v for v in report.hard_violations if v.rule == 'chassis.case')
    assert bad.path == f'{UNIT}/chassis'


def test_g_frozen_entry_adapter_matches_check_nodes(frozen: dict[str, Any]) -> None:
    entry = frozen['prefabs'][0]
    nodes = nc.frozen_entry_to_nodes(entry)
    assert len(nodes) == len(entry['nodes'])
    assert nodes[0]['path'] == entry['nodes'][0]['path']
    assert nodes[0]['parent_index'] is None
    assert nc.check_nodes(nodes, name=entry['root_name']).hits \
        == nc.check_nodes(entry['nodes'], name=entry['root_name']).hits

def test_turret_scope_skips_unit_only_rules() -> None:
    """炮塔件：单位那套 Chassis/Base*/轮/悬挂规则不适用 —— 同一棵树 unit 红、turret 绿。

    依据（2026-10-23 读 ISIL）：`UnitBuilder.IntegrateBaseTurret` 按 `UnitPrefabTurretInfo`
    找炮塔脚本；炮塔 prefab 本来就没有 `Chassis`，把单位规则套上去只会**假红**
    （实证：M10 炮塔包在单位口径下红一条 `base_ability.under_chassis`）。
    """
    tree = build(
        ('US_M10_105', [('base', [('root', [('body', [])])]), ('BaseAbility', [])]),
        kinds={'US_M10_105': ('MB:UnitPrefabTurretInfo',)},
    )
    unit_scope = nc.check_nodes(tree, name='US_M10_105', expected_root_name='US_M10_105')
    turret_scope = nc.check_nodes(
        tree, name='US_M10_105', expected_root_name='US_M10_105', scope='turret'
    )
    assert any(v.rule == 'base_ability.under_chassis' for v in unit_scope.hard_violations)
    assert turret_scope.hard_violations == ()
    assert turret_scope.ok


def test_turret_scope_still_checks_root_and_body() -> None:
    """炮塔口径只跳过单位专属那几族；root/body 仍是硬规则（BoneContainer.Bake 要它们）。"""
    tree = build(('US_M10_105', [('nope', [])]))
    report = nc.check_nodes(
        tree, name='US_M10_105', expected_root_name='US_M10_105', scope='turret'
    )
    rules = {v.rule for v in report.hard_violations}
    assert 'root.present' in rules and 'body.present' in rules

