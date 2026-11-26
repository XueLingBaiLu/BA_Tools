"""`AssetSource` 的对象表读侧（task-8 · P1/P3 的机械判据）。

**判据一行**：`object_at(pid)` 对**全部** pathID 的返回值与原版线性扫描逐个相同 ——
包括「取第一个命中的对象」「同一个 pathID 出现两次仍取第一个」「不存在 ⇒ None」，
而且索引**只建一次**（老实现是"每问一次就扫一遍"）。

⛔ 本模块**不开真包、不 import UnityPy**（`env.objects` 用夹具替身），秒级；
真包上的全量对拍（162,010 个 pathID + 正控）在 `_work/vtb1/bench/probe_index_equiv.py`。

每条判据都带**能红**的正控：把已知错的实现（last-wins / 给 miss 造一个命中）摆出来，
断言它会与本判据不一致。
"""

from __future__ import annotations

from typing import Any

import pytest

from ba_mod_maker.asset_kernel import unitypy_source


class _Type:
    def __init__(self, name: str) -> None:
        self.name = name


class _Obj:
    """`env.objects` 里那种对象（只要 path_id / type.name / read_typetree）。"""

    def __init__(self, path_id: int, kind: str = "Mesh") -> None:
        self.path_id = path_id
        self.type = _Type(kind)

    def read_typetree(self) -> dict[str, Any]:
        return {"m_Name": f"{self.type.name}#{self.path_id}"}


class _NoPathId:
    """没有 path_id 的对象：老实现在它身上永远匹配不上（None == 7 为 False）。"""

    def __init__(self) -> None:
        self.type = _Type("MonoBehaviour")


class _CountingObjects(list):
    """数一遍遍历：`iterations` 就是"扫了几遍 env.objects"。"""

    def __init__(self, items: list[Any]) -> None:
        super().__init__(items)
        self.iterations = 0

    def __iter__(self):  # type: ignore[no-untyped-def]
        self.iterations += 1
        return super().__iter__()


class _Env:
    def __init__(self, objects: list[Any]) -> None:
        self.objects = _CountingObjects(objects)


class _Reader:
    def close(self) -> None:
        pass


class _StubUnityPy:
    """替身：每次 `load()` 都给一个**新的** env（close 之后要不要重建索引就看它）。"""

    def __init__(self, objects: list[Any]) -> None:
        self._objects = objects

    def load(self, path: str) -> _Env:
        return _Env(self._objects)


def _source(monkeypatch: pytest.MonkeyPatch, objects: list[Any]) -> unitypy_source.AssetSource:
    monkeypatch.setattr(unitypy_source.unityfs, "open_bundle", lambda path: _Reader())
    monkeypatch.setattr(unitypy_source, "require_unitypy", lambda: _StubUnityPy(objects))
    return unitypy_source.AssetSource("fake.bundle")


def _linear(objects: list[Any], path_id: int) -> Any:
    """**原版 object_at 的算法**（逐字照抄老实现，供对拍用）。"""
    for obj in objects:
        if getattr(obj, "path_id", None) == path_id:
            return obj
    return None


def _last_wins_index(objects: list[Any], path_id: int) -> Any:
    """正控 A：索引建成 last-wins（后一个顶掉前一个）—— 重复 pid 那条判据必须红。"""
    table: dict[int, Any] = {}
    for obj in objects:
        pid = getattr(obj, "path_id", None)
        if pid is not None:
            table[int(pid)] = obj
    return table.get(path_id)


def _fabricate_on_miss(objects: list[Any], path_id: int) -> Any:
    """正控 B：miss 时编一个命中出来 —— P3 判据必须红。"""
    return _last_wins_index(objects, path_id) or (objects[0] if objects else None)


FIXTURE_OBJECTS: list[Any] = [
    _Obj(3, "SkinnedMeshRenderer"),
    _NoPathId(),
    _Obj(1, "Mesh"),
    _Obj(2, "Material"),
    _NoPathId(),
    _Obj(1000, "Texture2D"),
]


# --------------------------------------------------------------------------- #
# P1 · 全 pathID 与原版逐个相同
# --------------------------------------------------------------------------- #
def test_object_at_agrees_with_linear_scan_for_every_path_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    src = _source(monkeypatch, FIXTURE_OBJECTS)
    for pid in (1, 2, 3, 1000):
        assert src.object_at(pid) is _linear(FIXTURE_OBJECTS, pid)
    # 没有 path_id 的对象（夹具里第 2、5 个）永远不该被匹配上
    assert src.object_at(3) is FIXTURE_OBJECTS[0]
    assert _linear(FIXTURE_OBJECTS, 3) is not FIXTURE_OBJECTS[1]


def test_first_occurrence_wins_when_path_id_repeats(monkeypatch: pytest.MonkeyPatch) -> None:
    """同一 pathID 出现两次 ⇒ 原版取**第一个**（setdefault 口径）。"""
    first, second = _Obj(7, "Mesh"), _Obj(7, "Mesh")
    objects = [first, _Obj(8), second]
    src = _source(monkeypatch, objects)
    assert src.object_at(7) is first
    assert src.object_at(7) is _linear(objects, 7)
    # 正控：last-wins 的索引在这里给出**另一个对象** —— 判据确实能红。
    assert _last_wins_index(objects, 7) is second
    assert _last_wins_index(objects, 7) is not first


def test_index_is_built_once_for_many_lookups(monkeypatch: pytest.MonkeyPatch) -> None:
    """索引只建一次：一千次查找只允许扫一遍 env.objects（老实现是每次一遍）。"""
    src = _source(monkeypatch, FIXTURE_OBJECTS)
    for pid in (1, 2, 3, 1000):
        assert src.object_at(pid) is not None
    for _ in range(1000):
        src.object_at(3)
    assert src.env.objects.iterations == 1


def test_index_is_rebuilt_after_close(monkeypatch: pytest.MonkeyPatch) -> None:
    """close() 之后 env 是新的 ⇒ 索引跟着重建（没进过缓存的 pid 必须拿**新 env** 的对象）。"""
    made: list[_Obj] = []

    class _FreshStub:
        def load(self, path: str) -> _Env:
            obj = _Obj(2 + len(made), "Material")  # 第一次给 path_id=2，第二次给 3
            made.append(obj)
            return _Env([obj])

    monkeypatch.setattr(unitypy_source.unityfs, "open_bundle", lambda path: _Reader())
    monkeypatch.setattr(unitypy_source, "require_unitypy", lambda: _FreshStub())
    src = unitypy_source.AssetSource("fake.bundle")
    before = src.object_at(2)
    assert before is made[0]
    src.close()
    # ① 已经在 _object_at 缓存里的 pid：仍旧返回旧对象（原版 close() 也不清缓存 —— 逐位保留）
    assert src.object_at(2) is before
    # ② 没进过缓存的 pid：必须来自**重建后**的索引（新 env 的那个对象）
    assert src.object_at(3) is made[1]
    assert src.env.objects.iterations == 1  # 新 env 只扫了一遍


# --------------------------------------------------------------------------- #
# P3 · 不存在的 pathID：老语义 = None / KeyError（索引不许把它变成别的）
# --------------------------------------------------------------------------- #
def test_missing_path_id_is_none_and_key_error(monkeypatch: pytest.MonkeyPatch) -> None:
    src = _source(monkeypatch, FIXTURE_OBJECTS)
    for pid in (0, 4, 999, -1, 2**63 - 1):
        assert _linear(FIXTURE_OBJECTS, pid) is None
        assert src.object_at(pid) is None
        with pytest.raises(KeyError) as excinfo:
            src.read_typetree(pid)
        assert excinfo.value.args[0] == f"这个包里没有 pathID={pid}"
    # 正控：给 miss 编一个命中的实现，上面两条判据必须红。
    assert _fabricate_on_miss(FIXTURE_OBJECTS, 999) is not None


def test_negative_result_is_not_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    """miss 不进缓存（老实现 `if found is not None` 才写）—— 语义逐位保留。"""
    src = _source(monkeypatch, FIXTURE_OBJECTS)
    assert src.object_at(999) is None
    assert 999 not in src._object_at
    assert src.object_at(2) is FIXTURE_OBJECTS[3]
    assert src._object_at[2] is FIXTURE_OBJECTS[3]
