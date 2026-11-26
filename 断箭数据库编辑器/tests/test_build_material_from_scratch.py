"""W10-② 判据：Material 从零序列化 + M1/M2 映射逐条对拍（N2）+ 依赖清单（N4）。

**判据一行**：pytest tests/test_build_material_from_scratch.py -q -> 通过数 **从 0 变 N**
（本文件新建前该模块不存在 = 0 收集）。

## 判据落点（每条都带正控 / 负控 —— 设计稿 §11 四条纪律之①）

- **R1 键集合**：真件类型树的 12 个顶层键 == material_build.MATERIAL_TOP_KEYS；
  负控 = 少一个键 / 多一个键都必须让 check_keyset 红。
- **R2 从零构造 + 往返**：material_fields() 造出来的字段树；真件材质「读回来 → 写出去」
  **逐字节相同**（正控）；负控 = 改一个标量 / 改一个槽 ⇒ 字节必须变。
- **R3 N2 逐条**：源材质 → 我们包里的材质（走映射表）→ n2_rows 逐条一致；
  逐条读数用 n2_text() 打出来（判据要求「一行行读数」）。
- **R4 负控**：改标量 / 改槽 / 丢槽 / 装错映射表 ⇒ n2_ok 必须 False。
- **R5 N4 依赖**：材质引用的 shader / 贴图 PPtr 逐条列出（pathID / 是否跨文件 / 搬不搬得动）。

## 现场读数从哪来

真件读数由 _work/w10_mat_deps.py 产出（重活锁 _work/w3_heavy.lock，一次开 3.38 GB 的 units 包），
落盘 **_work/w10_mat_fixture.json**；pytest 侧优先只读 JSON（秒级），夹具不在才现跑一次。
⛔ 不跑全量 pytest；本模块只碰自己那一个文件。
"""

from __future__ import annotations

import base64
import functools
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from ba_mod_maker.asset_kernel import clone, material_build, materials
from ba_mod_maker.core.errors import AssetError

# 工作根：数据已移出仓（2026-11-09，统一入口）。
WORK = Path(os.environ.get("BA_WORK_ROOT") or r"D:\断箭模组制作\_work_db")

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = WORK / "w10_mat_fixture.json"
PROBE_V1 = WORK / "w10_mat_probe.json"
PROBE = WORK / "w10_mat_deps.py"
HEAVY_LOCK = WORK / "w3_heavy.lock"

#: 验收点名的三个真件材质（pathID 来自 units 包实测）。
WANT_MATERIALS: dict[str, int] = {
    "RU_T72B 1": -6340630412728879988,
    "RU_T72B_track": 7952762356820983200,
    "LODs": 8349683979956743758,
}

#: 三个 Shader 的真名（m_ParsedForm.m_Name；m_Name 是空串）。
WANT_SHADERS: dict[int, str] = {
    2703859142610330766: "Shader Graphs/UnitV3",
    7322088784485553867: "HDRP/Lit",
    5951657029987726660: "HDRP/Unlit",
}

#: 真件 Material 类型树的节点数（units 包实测；游戏版本变了就会对不上）。
EXPECTED_MATERIAL_NODE_COUNT = 107
#: 夹具里读写往返失败的真件材质数量（锚点）。
EXPECTED_ROUNDTRIP_BAD = 0


class _HeavyLock:
    """跨进程互斥：与 W3/W4 共用 _work/w3_heavy.lock（一次只允许一个重活开包）。"""

    def __init__(self, path: Path, timeout: float = 2400.0) -> None:
        self.path = path
        self.timeout = timeout
        self.fd: int | None = None

    def __enter__(self) -> _HeavyLock:
        deadline = time.time() + self.timeout
        while time.time() < deadline:
            try:
                self.fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(self.fd, f"{os.getpid()}\n".encode())
                return self
            except FileExistsError:
                try:
                    age = time.time() - self.path.stat().st_mtime
                except OSError:
                    continue
                if age > 1800:
                    self.path.unlink(missing_ok=True)
                    continue
                time.sleep(1.0)
        raise TimeoutError(f"等重活锁超时：{self.path}")

    def __exit__(self, *exc: object) -> None:
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
        self.path.unlink(missing_ok=True)


def _fingerprint() -> str:
    parts = [
        f"{Path(__file__).name}:{Path(__file__).stat().st_mtime_ns}",
        f"{PROBE.name}:{PROBE.stat().st_mtime_ns}" if PROBE.is_file() else "probe:missing",
    ]
    for path in sorted((REPO_ROOT / "src" / "ba_mod_maker" / "asset_kernel").glob("*.py")):
        parts.append(f"{path.name}:{path.stat().st_mtime_ns}")
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]


def _read_fixture() -> dict | None:
    """读夹具；指纹对不上就返回 None（调用方会在重活锁下重跑）。

    ⚠ 探针自己产出的夹具**不带 _fingerprint**（它不 import 本用例）：那种情况按「首次
    采用」处理（不重跑）。一旦本文件的 _run_probe() 写过一次指纹，此后 asset_kernel
    或探针一变，指纹就对不上 —— 那时会在同一把重活锁下重跑一次。
    """
    if not FIXTURE.is_file():
        return None
    try:
        data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    except ValueError:
        return None
    stamp = data.get("_fingerprint")
    return data if stamp in (None, _fingerprint()) else None


def _run_probe() -> dict:
    proc = subprocess.run(
        [sys.executable, str(PROBE)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=3600,
        cwd=str(REPO_ROOT),
        check=False,
    )
    assert proc.returncode == 0, (
        f"重活探针退出码 {proc.returncode}\n"
        f"{(proc.stdout or '')[-3000:]}\n{(proc.stderr or '')[-2000:]}"
    )
    assert FIXTURE.is_file(), "探针没产出夹具"
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@functools.lru_cache(maxsize=1)
def _fixture() -> dict:
    """真件夹具：优先读缓存（秒级），缓存过期 / 不在才在重活锁下现跑一次。"""
    cached = _read_fixture()
    if cached is not None:
        return cached
    with _HeavyLock(HEAVY_LOCK):
        cached = _read_fixture()
        if cached is not None:
            return cached
        data = _run_probe()
        data["_fingerprint"] = _fingerprint()
        FIXTURE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return data


@functools.lru_cache(maxsize=1)
def _nodes() -> tuple[clone.TypeNode, ...]:
    """真件 Material 的类型树（从夹具里的**真包读数**重建，不是自造）。"""
    return tuple(clone.TypeNode(*row) for row in _fixture()["material_nodes"])


def _material(pid: int) -> dict:
    row = _fixture()["materials"][str(pid)]
    assert row.get("roundtrip_equal"), f"夹具里的材质 {pid} 自身就不是逐字节往返的"
    return row


def _payload(pid: int) -> bytes:
    return base64.b64decode(_material(pid)["b64"])


def _shader_name(pid: int) -> str:
    row = _material(pid).get("shader_row") or {}
    return str(row.get("parsed_name") or row.get("m_Name") or "")


# --------------------------------------------------------------------------- #
# R1 · 键集合尺子
# --------------------------------------------------------------------------- #
def test_r1_real_type_tree_has_exactly_the_twelve_top_keys() -> None:
    """真件 Material 类型树：classID 21 / 107 节点 / 12 个顶层键，与常量逐个相等。"""
    fixture = _fixture()
    assert fixture["classes"]["material"] == material_build.MATERIAL_CLASS_ID
    tree_keys = tuple(fixture["material_top_keys"])
    assert tree_keys == material_build.MATERIAL_TOP_KEYS, (
        f"类型树的顶层键与常量不一致：树={tree_keys}"
    )
    nodes = _nodes()
    assert len(nodes) == EXPECTED_MATERIAL_NODE_COUNT, len(nodes)
    assert nodes[0].type == "Material" and nodes[0].name == "Base"


def test_r1_shader_keywords_does_not_exist_in_this_version() -> None:
    """工单点名了 m_ShaderKeywords —— 本版**真件类型树里没有它**（写进去只会被静默忽略）。"""
    assert "m_ShaderKeywords" in material_build.ABSENT_FIELDS
    assert "m_ShaderKeywords" not in material_build.MATERIAL_TOP_KEYS
    assert "m_ShaderKeywords" not in _fixture()["material_top_keys"]
    if PROBE_V1.is_file():
        probe = json.loads(PROBE_V1.read_text(encoding="utf-8"))
        top = {
            key.split(".")[0]
            for key in probe["material_keys"]
            if "[" not in key  # 数组形态的键（xxx[]）不算顶层键
        }
        assert top == set(material_build.MATERIAL_TOP_KEYS), sorted(top)
        assert "m_ShaderKeywords" not in top


def test_r1_keyset_ruler_rejects_a_missing_key_and_an_extra_key() -> None:
    """尺子正控 + 负控：空材质过；少一个键红；多一个键红。"""
    good = material_build.material_fields(name="RULER")
    assert material_build.check_keyset(good) == ()

    missing = material_build.material_fields(name="RULER")
    missing["m_Shader"] = {"m_FileID": 0}
    bad = material_build.check_keyset(missing)
    assert any("m_Shader.m_PathID" in line for line in bad), bad
    with pytest.raises(AssetError):
        material_build.assert_keyset(missing)

    extra_top = material_build.material_fields(name="RULER")
    extra_top["m_ShaderKeywords"] = ""
    assert any("多顶层键" in line for line in material_build.check_keyset(extra_top))

    extra_nested = material_build.material_fields(name="RULER")
    extra_nested["m_SavedProperties"].pop("m_Ints")
    nested_bad = material_build.check_keyset(extra_nested)
    assert any("m_SavedProperties.m_Ints" in line for line in nested_bad), nested_bad


def test_r1_key_paths_constant_covers_every_path_in_a_full_material() -> None:
    """键路径常量必须覆盖一个**满槽**材质的全部路径（否则填满之后就误报）。"""
    full = material_build.material_fields(
        name="FULL",
        shader_path_id=7,
        tex_envs=[material_build.tex_env("_BaseColorMap", 11)],
        floats=[material_build.float_row("_Metallic", 0.5)],
        colors=[material_build.color_row("_BaseColor", 1.0, 1.0, 1.0, 1.0)],
        ints=[material_build.int_row("_ZTest", 4)],
        valid_keywords=["_NORMALMAP"],
        invalid_keywords=["_EMASKMAP"],
        string_tag_map=[material_build.string_tag_row("A", "B")],
        disabled_shader_passes=["SHADOWCASTER"],
        build_texture_stacks=[{"groupName": "g", "itemName": "i"}],
    )
    assert material_build.check_keyset(full) == ()


# --------------------------------------------------------------------------- #
# R2 · 从零构造 + 真件逐字节往返
# --------------------------------------------------------------------------- #
def test_r2_from_scratch_material_serializes_and_reads_back() -> None:
    """从零造的字段树能过真件类型树序列化，再读回来**字段逐个相等**。"""
    nodes = _nodes()
    fields = material_build.material_fields(
        name="FROM_SCRATCH",
        shader_path_id=12345,
        tex_envs=[material_build.tex_env("_BaseColorMap", 999)],
        floats=[material_build.float_row("_Metallic", 0.25)],
        valid_keywords=["_NORMALMAP"],
    )
    material_build.assert_keyset(fields)
    blob = clone.write_object(fields, nodes)
    assert blob, "字节是空的"
    again = clone.read_object(blob, nodes)
    assert again == fields, "读回来的字段树与写出去的不相等"
    assert clone.write_object(again, nodes) == blob

    # 负控：少一个键 -> 序列化**必须抛**（「缺键会抛」那一条）
    broken = dict(fields)
    del broken["m_BuildTextureStacks"]
    with pytest.raises(AssetError):
        clone.write_object(broken, nodes)


def test_r2_real_materials_roundtrip_byte_identical() -> None:
    """正控：真件材质读回来 → 写出去**逐字节相同**（至少 3 个，含 M1 与 M2 两张表）。"""
    nodes = _nodes()
    checked: list[str] = []
    for name, pid in WANT_MATERIALS.items():
        row = _material(pid)
        assert row["name"] == name, (row["name"], name)
        blob = _payload(pid)
        fields = clone.read_object(blob, nodes)
        written = clone.write_object(fields, nodes)
        assert written == blob, f"{name}: 往返不同（{len(blob)} vs {len(written)} B）"
        assert row["first_diff"] is None
        checked.append(name)
    assert set(checked) == set(WANT_MATERIALS)
    bad = [key for key, row in _fixture()["materials"].items() if not row["roundtrip_equal"]]
    assert len(bad) == EXPECTED_ROUNDTRIP_BAD, bad[:5]


def test_r2_summary_agrees_with_the_w4_probe_readings() -> None:
    """字段树 → 素 的转换必须与 W4 探针记下的读数一致（还原度 / 表号 / alpha_mode）。

    ⚠ 这条抓过一个真 bug：空引用（m_PathID == 0）如果照原样传给 materials._slots_of，
    真件里那几十个空槽会被算成「已赋但没映射」⇒ 还原度从 **完整** 掉成 **部分**。
    W4 交接 §2 第 9 条记的是：RU_T72B → M2 完整 / RU_T72B_track → M1 完整。

    ⚠ 2026-10-17（补 M5 之后）：`LODs`（HDRP/Unlit）**不再**是「— 未识别」——
    它进了 M5、而且 `_UnlitColorMap` 带图 ⇒ 还原度变 **完整**。
    这一格改成读 M5，旧读数（TABLE_NONE / UNKNOWN）作废。
    """
    want = {
        "RU_T72B 1": ("M2", materials.COVERAGE_FULL, "OPAQUE"),
        "RU_T72B_track": ("M1", materials.COVERAGE_FULL, "CLIP"),
        "LODs": ("M5", materials.COVERAGE_FULL, "OPAQUE"),
    }
    for name, pid in WANT_MATERIALS.items():
        shader = _shader_name(pid)
        plan = materials.plan_material(
            material_build.summary_of(_material(pid)["fields"], shader=shader)
        )
        assert (plan.table_key, plan.coverage, plan.alpha_mode) == want[name], (
            name,
            plan.table_key,
            plan.coverage,
            plan.alpha_mode,
        )
    # 负控：把空引用还原成 0（旧写法）⇒ 还原度必须掉档（尺子能红）
    source = _material(WANT_MATERIALS["RU_T72B_track"])["fields"]
    legacy = material_build.summary_of(source, shader="HDRP/Lit")
    legacy["texture_slots"] = [
        (slot, 0 if tid is None else tid) for slot, tid in legacy["texture_slots"]
    ]
    assert materials.plan_material(legacy).coverage == materials.COVERAGE_PARTIAL, (
        "把空引用报成 0 居然不掉档 —— 这条负控没咬合"
    )


def test_r2_fixture_covers_both_mapping_tables_of_the_work_order() -> None:
    """三个真件材质必须覆盖 M1（HDRP/Lit）、M2（Shader Graphs/UnitV3）与 M5（HDRP/Unlit）。

    ⚠ 2026-10-17：`HDRP/Unlit`（夹具里 `LODs` 那颗的 shader）**有表了**（M5），
    所以它不再走"认不出 ⇒ None"那一支；M6（HDRP/Decal）夹具里没有真件，单独点一次名。
    """
    keys = {
        _shader_name(pid): materials.table_for(_shader_name(pid)) for pid in WANT_MATERIALS.values()
    }
    got = {name: (table.key if table else None) for name, table in keys.items()}
    assert got["HDRP/Lit"] == "M1", got
    assert got["Shader Graphs/UnitV3"] == "M2", got
    assert got["HDRP/Unlit"] == "M5", got
    decal = materials.table_for("HDRP/Decal")
    assert decal is not None and decal.key == "M6"


# --------------------------------------------------------------------------- #
# R3 · N2 逐条对拍
# --------------------------------------------------------------------------- #
def _built_for(pid: int, *, table: materials.MaterialTable | None = None) -> tuple[dict, str]:
    source = _material(pid)["fields"]
    shader = _shader_name(pid)
    built = material_build.material_fields_from_source(source, table=table, shader=shader)
    return built, shader


@pytest.mark.parametrize("name", ["RU_T72B 1", "RU_T72B_track"])
def test_r3_every_row_matches_for_a_real_material(name: str) -> None:
    """N2：源材质 → 我们包里的材质，调用映射表之后**逐条一致**。

    ⚠ LODs（HDRP/Unlit）**不在**这里：四张表里没有它，取不到表就该抛 ——
    那一条由 test_r4_unknown_shader_raises_instead_of_guessing 单独守。
    """
    pid = WANT_MATERIALS[name]
    built, shader = _built_for(pid)
    material_build.assert_keyset(built)
    rows = material_build.n2_rows(_material(pid)["fields"], built, shader=shader)
    assert rows, "N2 一行都没产生 —— 尺子空跑"
    assert material_build.n2_ok(rows), "\n" + material_build.n2_text(rows)
    kinds = {row.kind for row in rows}
    assert {"slot", "scalar", "keyword", "alpha"} <= kinds, kinds
    assert len(rows) >= 6, material_build.n2_text(rows)
    # 判据要求「一行行读数」：失败时把整张表打出来（上面那条断言就带 n2_text）
    assert all(row.key for row in rows)


def test_r3_m1_track_covers_the_slots_named_by_the_work_order() -> None:
    """工单 (a) 点名的每一条都要在 M1 真件的读数里出现（槽 + 标量 + 通道）。"""
    pid = WANT_MATERIALS["RU_T72B_track"]
    source = _material(pid)["fields"]
    built, shader = _built_for(pid)
    table = materials.table_for(shader)
    assert table is not None and table.key == "M1", shader
    rows = {row.key: row for row in material_build.n2_rows(source, built, table=table)}
    for key in ("_BaseColorMap", "_MainTex", "_NormalMap", "_MaskMap", "_EmissiveColorMap"):
        assert key in rows, sorted(rows)
        assert rows[key].match, rows[key].line()
    for key in ("_Metallic", "_Smoothness", "_NormalScale", "_AlphaCutoff"):
        assert key in rows, sorted(rows)
        assert rows[key].match, rows[key].line()
    mask_rows = [
        row for row in material_build.n2_rows(source, built, table=table) if row.role == "mask"
    ]
    mask = {row.channel: row for row in mask_rows}
    assert mask["R"].mapped_to == "Metallic" and mask["R"].invert is False, mask["R"].line()
    assert mask["A"].mapped_to == "Roughness" and mask["A"].invert is True, mask["A"].line()
    assert mask["G"].note.startswith("标注忽略"), mask["G"].line()
    assert "Blender 侧=" in rows["_Smoothness"].note


def test_r3_m2_unitv3_layer_hash_is_the_base_color() -> None:
    """M2（UnitV3）：Layer_<hash> 直接当 Base Color；标量只有 _Metallic/_Smoothness。"""
    pid = WANT_MATERIALS["RU_T72B 1"]
    source = _material(pid)["fields"]
    built, shader = _built_for(pid)
    table = materials.table_for(shader)
    assert table is not None and table.key == "M2", shader
    rows = material_build.n2_rows(source, built, table=table)
    assert material_build.n2_ok(rows), "\n" + material_build.n2_text(rows)
    layer_rows = [row for row in rows if row.key.startswith("Layer_")]
    assert layer_rows, [row.key for row in rows]
    assert layer_rows[0].role == materials.ROLE_BASE_COLOR
    assert layer_rows[0].source == layer_rows[0].built
    assert material_build.unmapped_slots(source, table) == ()


def test_r3_shader_name_comes_from_the_parsed_form_field() -> None:
    """判据字段：Shader 的真名在 m_ParsedForm.m_Name，m_Name 是空串。"""
    fixture = _fixture()
    expected_table = {"Shader Graphs/UnitV3": "M2", "HDRP/Lit": "M1", "HDRP/Unlit": "M5"}
    checked = 0
    for pid, want in WANT_SHADERS.items():
        row = fixture["shaders"].get(str(pid))
        if row is None or "read_error" in row:
            continue  # 大件（compressedBlob 元素数 > 1e7）读不出来，读数在报告里
        assert str(row.get("m_Name") or "") == "", (pid, row.get("m_Name"))
        assert str(row.get("parsed_name") or "") == want, (pid, row.get("parsed_name"))
        table = materials.table_for(want)
        assert (table.key if table else None) == expected_table[want]
        checked += 1
    assert checked >= 2, f"只核对上 {checked} 个 shader 的判据字段"
    # 2026-10-17：`HDRP/Unlit` 已有 M5；"认不出的 shader 不许猜表" 改由下面那条守着
    unlit = materials.table_for("HDRP/Unlit")
    assert unlit is not None and unlit.key == "M5"


# --------------------------------------------------------------------------- #
# R4 · 负控：改一个参数 / 换一个槽 / 丢一个槽 / 装错表 ⇒ 必红
# --------------------------------------------------------------------------- #
def _mapped_scalar_params(source: dict, table: materials.MaterialTable | None) -> list[str]:
    if table is None:
        return []
    present = {row.get("first") for row in source["m_SavedProperties"]["m_Floats"]}
    return [binding.param for binding in table.scalars if binding.param in present]


@pytest.mark.parametrize("name", ["RU_T72B 1", "RU_T72B_track"])
def test_r4_changing_one_scalar_turns_n2_red(name: str) -> None:
    """改一个标量 ⇒ N2 必须红，字节也必须跟着变。"""
    pid = WANT_MATERIALS[name]
    source = _material(pid)["fields"]
    built, shader = _built_for(pid)
    params = _mapped_scalar_params(source, materials.table_for(shader))
    assert params, f"{name} 的映射表没有可改的标量"
    param = params[0]
    tampered = material_build.with_scalar(built, param, 12345.0)
    rows = material_build.n2_rows(source, tampered, shader=shader)
    assert not material_build.n2_ok(rows), f"改了 {param} 居然还是绿的"
    assert any(not row.match and row.key == param for row in rows)
    assert clone.write_object(tampered, _nodes()) != _payload(pid)


def test_r4_changing_one_slot_turns_n2_red() -> None:
    """改一个槽的贴图 pathID ⇒ N2 红（负控）。"""
    pid = WANT_MATERIALS["RU_T72B_track"]
    source = _material(pid)["fields"]
    built, shader = _built_for(pid)
    tampered = material_build.with_texture(built, "_BaseColorMap", 424242)
    rows = material_build.n2_rows(source, tampered, shader=shader)
    assert not material_build.n2_ok(rows)
    assert any(not row.match and row.key == "_BaseColorMap" for row in rows)
    assert clone.write_object(tampered, _nodes()) != _payload(pid)


def test_r4_dropping_a_slot_turns_n2_red() -> None:
    """我们包里**丢一个槽** ⇒ N2 红，且字节必须变短。"""
    pid = WANT_MATERIALS["RU_T72B_track"]
    source = _material(pid)["fields"]
    built, shader = _built_for(pid)
    kept = [
        row
        for row in built["m_SavedProperties"]["m_TexEnvs"]
        if str(row.get("first")) != "_MaskMap"
    ]
    slim = dict(built)
    slim["m_SavedProperties"] = dict(built["m_SavedProperties"], m_TexEnvs=kept)
    rows = material_build.n2_rows(source, slim, shader=shader)
    assert not material_build.n2_ok(rows)
    dropped = [row for row in rows if not row.match and row.key == "_MaskMap"]
    assert dropped, "丢了一个槽 N2 居然还是绿的（哨兵没生效？）"
    # ⚠ _MaskMap 在真件里是**空引用**：所以必须是 MISSING_SLOT 才红，
    # 用 None 当哨兵这条负控会假绿（实测踩过）。
    assert dropped[0].source is None and dropped[0].built == material_build.MISSING_SLOT
    assert len(clone.write_object(slim, _nodes())) < len(_payload(pid))


def test_r4_wrong_table_turns_n2_red() -> None:
    """**装错映射表**（拿 M3 去解 M1 的履带）⇒ N2 必须红。"""
    pid = WANT_MATERIALS["RU_T72B_track"]
    source = _material(pid)["fields"]
    wrong = materials.TABLES["Shader Graphs/Infantry Shader"]
    built = material_build.material_fields_from_source(source, table=wrong)
    rows = material_build.n2_rows(source, built, table=wrong)
    assert not material_build.n2_ok(rows), (
        "装错表之后 N2 还是绿的 —— 这条尺子不成立：\n" + material_build.n2_text(rows)
    )


def test_r4_unknown_shader_raises_instead_of_guessing() -> None:
    """认不出的 shader ⇒ 抛（不猜、不回落）。

    ⚠ 2026-10-17：`HDRP/Unlit`（`LODs` 那颗的 shader）有了 M5 之后**不再**是例子 ——
    这里换成真件里确实存在、但**没有映射表**的 `TextMeshPro/Distance Field`
    （`_work/audit-full/materials.jsonl` 实测 51 颗，`materials.TABLES` 里没有它）。
    """
    pid = WANT_MATERIALS["LODs"]
    source = _material(pid)["fields"]
    assert _shader_name(pid) == "HDRP/Unlit"
    unlit = materials.table_for("HDRP/Unlit")
    assert unlit is not None and unlit.key == "M5", "HDRP/Unlit 有 M5（2026-10-17 起）"
    unknown = "TextMeshPro/Distance Field"
    assert materials.table_for(unknown) is None, f"{unknown} 居然有表了 —— 换一个无表的 shader"
    with pytest.raises(AssetError):
        material_build.n2_rows(source, source, shader=unknown)


# --------------------------------------------------------------------------- #
# R5 · N4 依赖清单
# --------------------------------------------------------------------------- #
def test_r5_dependency_rows_cover_the_shader_and_every_texture_slot() -> None:
    """每个材质的 PPtr = 1 个 shader + 每个槽一个贴图；逐条给出 pathID 与搬动理由。"""
    fixture = _fixture()
    texture_names = {int(k): v.get("m_Name") for k, v in fixture["textures"].items()}
    total_assigned = 0
    for name, pid in WANT_MATERIALS.items():
        source = _material(pid)["fields"]
        shader = _shader_name(pid)
        table = materials.table_for(shader)
        rows = material_build.dependency_rows(
            source, shader=shader, table=table, texture_names=texture_names
        )
        assert rows[0]["kind"] == "shader"
        assert rows[0]["path_id"] == int(source["m_Shader"]["m_PathID"])
        assert rows[0]["shader_name"] == shader
        assert len(rows) == 1 + len(source["m_SavedProperties"]["m_TexEnvs"])
        for item in rows:
            assert item["movable"] in {"yes", "no", "n/a"}
            assert item["reason"]
        tex_rows = [item for item in rows if item["kind"] == "texture"]
        assert tex_rows, name
        # 只有**非空**的贴图引用才必须有名字（真件里大量槽是空引用）
        assigned = [item for item in tex_rows if not item["null"]]
        assert all(item["texture_name"] for item in assigned), [item["slot"] for item in assigned]
        # 真件里材质的每一个非空 PPtr 都是**包内**引用（fileID=0）⇒ 必须报 movable=yes
        assert all(item["movable"] == "yes" for item in assigned), [
            (item["slot"], item["movable"]) for item in assigned
        ]
        total_assigned += len(assigned)
    # 尺子不许空跑：三个材质加起来至少要有几条**非空**的贴图引用被点名
    assert total_assigned >= 2, total_assigned


def test_r5_external_pptr_is_reported_as_not_movable() -> None:
    """负控：跨文件（m_FileID != 0）的 PPtr 必须报 movable=no 并给理由。"""
    source = dict(_material(WANT_MATERIALS["RU_T72B_track"])["fields"])
    source["m_Shader"] = {"m_FileID": 3, "m_PathID": 555}
    rows = material_build.dependency_rows(source, shader="HDRP/Lit")
    assert rows[0]["external"] is True
    assert rows[0]["movable"] == "no"
    assert "跨文件" in rows[0]["reason"]
    source["m_Shader"] = {"m_FileID": 0, "m_PathID": 0}
    rows = material_build.dependency_rows(source, shader="HDRP/Lit")
    assert rows[0]["null"] is True and rows[0]["movable"] == "n/a"


def test_r5_pptr_rows_only_walk_real_pptr_fields() -> None:
    """材质里只有 2 种 PPtr 字段（实测）—— 浮点不会被误当成 pathID。"""
    source = _material(WANT_MATERIALS["RU_T72B_track"])["fields"]
    rows = material_build.pptr_rows(source)
    assert [row.field_path for row in rows][0] == "m_Shader"
    assert all(row.kind in {"shader", "texture"} for row in rows)
    assert len(rows) == 1 + len(source["m_SavedProperties"]["m_TexEnvs"])


# --------------------------------------------------------------------------- #
# R3b · 逐键覆盖证明（源字段树 vs 我们造的字段树）
# --------------------------------------------------------------------------- #
NON_MAPPING_KEYS = (
    "m_ValidKeywords",
    "m_InvalidKeywords",
    "m_LightmapFlags",
    "m_EnableInstancingVariants",
    "m_DoubleSidedGI",
    "m_CustomRenderQueue",
    "stringTagMap",
    "disabledShaderPasses",
    "m_BuildTextureStacks",
)


@pytest.mark.parametrize("name", ["RU_T72B 1", "RU_T72B_track"])
def test_r3b_built_tree_covers_every_mapped_key_and_drops_only_unmapped_ones(name: str) -> None:
    """逐键覆盖证明：表认得的槽/标量一个不少且值相同；丢掉的**恰好**是表不认的那些。"""
    pid = WANT_MATERIALS[name]
    source = _material(pid)["fields"]
    built, shader = _built_for(pid)
    table = materials.table_for(shader)
    assert table is not None

    # ① 顶层键：两边都恰好是那 12 个（不是「差不多」）
    assert set(built) == set(source) == set(material_build.MATERIAL_TOP_KEYS)
    # ② 名字 / shader 指针对齐
    assert built["m_Name"] == source["m_Name"]
    assert built["m_Shader"] == source["m_Shader"]
    # ③ 非映射字段逐值原样照搬（丢了就是「看着对、画出来不一样」）
    for key in NON_MAPPING_KEYS:
        assert built[key] == source[key], f"{key} 没有原样照搬"

    # ④ 槽位：表认得的一个不少、pathID 相同；丢掉的是表不认的
    src_ids = material_build.slot_texture_ids(source)
    our_ids = material_build.slot_texture_ids(built)
    want_slots = {slot: tid for slot, tid in src_ids.items() if table.matches(slot)}
    assert our_ids == want_slots, (our_ids, want_slots)
    assert set(src_ids) - set(our_ids) == {slot for slot in src_ids if not table.matches(slot)}
    assigned_unmapped = material_build.unmapped_slots(source, table)
    assert all(our_ids.get(slot) is None for slot in assigned_unmapped)

    # ⑤ 标量：表点名的保留、值相同；其余丢掉
    src_f = {row.get("first"): row.get("second") for row in source["m_SavedProperties"]["m_Floats"]}
    our_f = {row.get("first"): row.get("second") for row in built["m_SavedProperties"]["m_Floats"]}
    wanted_params = {binding.param for binding in table.scalars}
    assert our_f == {p: v for p, v in src_f.items() if p in wanted_params}
    assert len(src_f) > len(our_f), "这一件没有任何可丢的浮点 —— 覆盖证明会空跑"


def _fixture_material_row(shader_name: str) -> dict:
    fixture = _fixture()
    for row in fixture["fixture_materials"].values():
        if row["shader_name"] == shader_name:
            return row
    raise AssertionError(f"夹具里没有 {shader_name} 的真件")


def test_r3b_fixture_covers_m1_m2_m3_and_flags_m4_unsupported() -> None:
    """至少有 M1/M2/M3 三张表的真件；M4 必须**显式**标未支持（不假装还原了）。"""
    fixture = _fixture()
    by_table: dict[str, list[str]] = {}
    for row in fixture["fixture_materials"].values():
        table = materials.table_for(str(row["shader_name"]))
        by_table.setdefault(table.key if table else "-", []).append(str(row["name"]))
    assert {"M1", "M2", "M3"} <= set(by_table), sorted(by_table)
    assert by_table.get("M4"), "夹具里没有 UnitV4_Camo 的真件（M4 那条就没法自证）"

    # M3：拿真件走一遍 N2（表里 _Albedo/_Normal/_Mask 三条都要对上）
    row = _fixture_material_row("Shader Graphs/Infantry Shader")
    source = clone.read_object(base64.b64decode(row["b64"]), _nodes())
    table = materials.table_for(row["shader_name"])
    built = material_build.material_fields_from_source(source, table=table)
    rows = material_build.n2_rows(source, built, table=table)
    assert material_build.n2_ok(rows), "\n" + material_build.n2_text(rows)
    slots = {r.key: r for r in rows if r.kind == "slot"}
    for slot in ("_Albedo", "_Normal", "_Mask"):
        if slot in slots:
            assert slots[slot].role, f"{slot} 没被映射表认成任何角色"

    # M4：明确未支持 —— 槽位不展开，并留下理由（⛔ 不许假装还原）
    m4 = _fixture_material_row("Shader Graphs/UnitV4_Camo")
    m4_source = clone.read_object(base64.b64decode(m4["b64"]), _nodes())
    m4_table = materials.table_for(m4["shader_name"])
    assert m4_table is not None and m4_table.unsupported is True
    plan = materials.plan_material(material_build.summary_of(m4_source, shader=m4["shader_name"]))
    assert plan.coverage == materials.COVERAGE_UNSUPPORTED
    assert plan.notes, "标了未支持却没写理由"


def test_r5_shader_dependency_closure_is_bounded_and_in_package() -> None:
    """Shader 自己的 PPtr 闭包：只有 m_Dependencies 那一类非空、且全是**包内**引用。

    与 verifier 的独立读数（_work/w10v_shader_pptr.json）对拍：HDRP/Lit 与 HDRP/Unlit
    各 2 个依赖、UnitV3 1 个，全部 m_FileID=0；m_NonModifiableTextures 在真 shader 上全空。
    """
    want = {
        "7322088784485553867": [1354542265007958578, -8015457887526078469],
        "2703859142610330766": [3819691914921817934],
        "5951657029987726660": [1354542265007958578, -8015457887526078469],
    }
    fixture = _fixture()
    checked = 0
    for pid, deps in want.items():
        row = fixture["shaders"].get(pid)
        if not row or not row.get("m_Dependencies"):
            continue  # 夹具没带补量结果（旧缓存）
        got = [(int(d["m_FileID"]), int(d["m_PathID"])) for d in row["m_Dependencies"]]
        assert got == [(0, path_id) for path_id in deps], (pid, got)
        assert row.get("m_NonModifiableTextures") == [], (pid, row.get("m_NonModifiableTextures"))
        checked += 1
    assert checked >= 2, f"只对上 {checked} 个 shader 的依赖闭包"
