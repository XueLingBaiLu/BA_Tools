"""真件 AnimationHub → 属性清单：引用解析（2026-11-02 现场）。

现场：US_ACV 的 `ProceduralDirt._renderers` 指向**组件**（SkinnedMeshRenderer 137632746491512912），
而 `anim_attrs._names()` 只把 Transform（class 4）映射成节点名 ⇒ 引用退化成 `""` ⇒
`AnimConfig` 解析节点名失败 ⇒ 整条行为被静默跳过（`anim_config` 里写着 ProceduralDirt，
Unity 侧一条都没落地）。同一份清单里还有真件的 `rid: -2`（Unity 序列化的 **null 槽**），
旧实现把它编成 `Unknown` 类 —— 工程里没有这个类，打包时照样跳过，却让配置看起来多一条行为。

两条都用合成 prefab 钉住：⛔ 不读真实游戏文件（用例要能在没有游戏的机器上跑）。
"""

from __future__ import annotations

from pathlib import Path

from ba_mod_maker.asset_kernel import anim_attrs

_PREFAB = """%YAML 1.1
%TAG !u! tag:unity3d.com,2011:
--- !u!1 &100
GameObject:
  m_Name: Chassis
  m_Component:
  - component: {fileID: 200}
  - component: {fileID: 300}
--- !u!4 &200
Transform:
  m_GameObject: {fileID: 100}
  m_Father: {fileID: 0}
--- !u!137 &300
SkinnedMeshRenderer:
  m_GameObject: {fileID: 100}
--- !u!114 &900
MonoBehaviour:
  m_GameObject: {fileID: 100}
  _universalState: []
  _demoState: []
  _gameState:
  - rid: 11
  - rid: -2
  - rid: 12
  - rid: 13
  _preDeathState: []
  _deathState: []
  references:
    version: 2
    RefIds:
    - rid: -2
      type: {class: , ns: , asm: }
    - rid: 11
      type: {class: ProceduralDirt, ns: , asm: BrokenArrow}
      data:
        _renderers:
        - {fileID: 300}
        _materials: []
    - rid: 12
      type: {class: HideMesh, ns: BrokenArrow.Client.Ecs.AnimationBehaviors, asm: BrokenArrow}
      data:
        _remove:
        - {fileID: 100}
    - rid: 13
      type: {class: AnimatorConnect, ns: A, asm: B}
      data:
        _animator: {fileID: 0}
        _terrainTypeTriggers:
          _keyData: 
          _valueData:
          - OnEnter: OnWater
            OnExit: WaterExit
"""


def _props(tmp_path: Path, skipped: list[str] | None = None) -> list[dict]:
    p = tmp_path / "unit.prefab"
    p.write_text(_PREFAB, encoding="utf-8")
    return anim_attrs.props_from_prefab(p, skipped)


def _value(props: list[dict], key: str):
    hit = [r["value"] for r in props if r["key"] == key]
    assert hit, "清单里没有 " + key + "：" + repr([r["key"] for r in props])
    return hit[0]


def test_component_ref_maps_to_its_node_name(tmp_path: Path) -> None:
    """`_renderers` 指向 Renderer 组件 ⇒ 必须落成节点名，⛔ 不是空串。"""
    props = _props(tmp_path)
    assert _value(props, "ba.gameState.0._renderers__json") == '["Chassis"]'


def test_gameobject_ref_maps_to_its_node_name(tmp_path: Path) -> None:
    props = _props(tmp_path)
    assert _value(props, "ba.gameState.2._remove__json") == '["Chassis"]'


def test_null_reference_slot_is_skipped_not_invented(tmp_path: Path) -> None:
    """真件的 `rid: -2` 是 null 槽 ⇒ 不产行为；⛔ 不许编成 `Unknown` 类。"""
    skipped: list[str] = []
    props = _props(tmp_path, skipped)
    classes = [r["value"] for r in props if r["key"].endswith(".__class")]
    assert "Unknown" not in classes
    assert classes == ["ProceduralDirt", "HideMesh", "AnimatorConnect"]
    assert "gameState[1]:<null>" in skipped

def test_terrain_trigger_keys_recovered_from_names(tmp_path: Path) -> None:
    """真件里 `_terrainTypeTriggers` 只写了 `_keyData:`（空）而 `_valueData` 有一条 ——
    Unity 的 SerializableDictionary 按 key 重建 ⇒ 空 key 会让地形触发**静默消失**
    （实机：ACV 下水挡浪板不展开）。名字能判出地形时要补回 TerrainType.Water = 5。"""
    skipped: list[str] = []
    props = _props(tmp_path, skipped)
    assert _value(props, "ba.gameState.3._terrainTypeTriggers._keyData__json") == "[5]"
    assert any("恢复 [5]" in s for s in skipped)
