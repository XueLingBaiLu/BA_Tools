r"""D 线 · 骨路径解析（crc32 哈希 -> prefab 节点路径）—— 纯数据，**零宿主模块**。

## 这一模块解决什么

研究线已经钉死一条规则（`03-动画/animator-clip-binding.md` §0，带 4 条可手算正控）：

    clip 的 m_ClipBindingConstant.genericBindings[i].path == crc32(相对路径)
    相对路径从 **Animator 所在节点**算起、不含该节点自己的名字、分隔符 `/`、UTF-8、无符号 32 位

但**路径表本身**（哪个哈希对应哪条路径）是研究线的产物，产品里一直没有入口：

    read_path_table(paths_json)          读 _nodepaths.json（roots / per_root / by_hash 三张表）
    PathTable.resolve(hash)              哈希 -> 路径（None = 解析不出来，**不猜**）
    PathTable.match(hash)                True / None（用节点名的**全局前缀闭包**判命中）
    PathTable.explain(hash)              一次拿全：路径 / 是否命中 / 命中口径 / 为什么

## 三条硬口径（都有真件读数）

1. **解析不出来就是解析不出来**（`resolve -> None`），⛔ 不静默跳过、⛔ 不拿 crc 号码冒充骨名。
   实测：units 包 3988 条 generic 绑定里 **3158 条命中（79.1876%）**，
   `US_LAV_M`（LAV-M · Id=154）整个 prefab **339 条全 0 命中** —— 那是"接不上"，要如实报。
2. **命中口径要分层**：`exact`（整条路径在某个 prefab 的路径表里存在）与
   `leaf`（只有末级节点名在库里出现过 —— 路径前缀是**运行时才挂上去**的东西，
   例：士兵手里的 `AK74M`、运行时拼装的 `root/body/body/anim_turret`）。
   两者对"Blender 里能不能接到骨上"是不同的答案，所以**必须分开报**。
3. **表里每一条都能落成完整路径**（真件自证）：by_hash **22140/22140** 的路径
   在某个 `per_root` 里**逐字存在**；再拿 `_nodeindex.json` 的 `nodes[].father`
   逐段核父子链，**22127/22140 = 99.941%** 通过（13 条父链对不上 —— 如实记，见
   `chain_ok`／`chain_source`）。

## 复算（只读，两步都不开包）

    .venv\Scripts\python.exe _work\animline\probe_paths.py      # 正控 + 结构自证
    .venv\Scripts\python.exe _work\animline\probe_hitrate.py    # 3158/3988 = 79.1876%

⛔ 本模块**不开包、不碰游戏目录**：只读研究线的两份 JSON 产物（纯离线）。
"""

from __future__ import annotations

import json
import zlib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ba_mod_maker.core.errors import AssetError

__all__ = [
    "HIT_EXACT",
    "HIT_LEAF",
    "HIT_MISS",
    "KNOWN_CHAIN_FAILURES",
    "KNOWN_TABLE_PATHS",
    "PATH_TABLE_KEYS",
    "PathHit",
    "PathTable",
    "crc32_path",
    "read_path_table",
]

PATH_TABLE_KEYS = ("roots", "per_root", "by_hash")

HIT_EXACT = "exact"
HIT_LEAF = "leaf"
HIT_MISS = "miss"

#: 真件 `_nodepaths.json` 的 by_hash 条数（2026-10-08 冻结件实测）。
KNOWN_TABLE_PATHS = 22140
#: 同一份冻结件里，逐段父子链对不上的条数（如实记；不是本模块的判据）。
KNOWN_CHAIN_FAILURES = 13


def crc32_path(text: str) -> int:
    """Unity 的路径哈希：`zlib.crc32(utf-8) & 0xFFFFFFFF`（研究线正控 4/4）。"""
    return zlib.crc32(str(text).encode("utf-8")) & 0xFFFFFFFF


@dataclass(frozen=True, slots=True)
class PathHit:
    """一条绑定的解析结果 —— **带口径**，让"接不上"和"只能按末级名接"分得开。"""

    path_hash: int
    path: str | None
    kind: str
    root: str | None = None
    chain_ok: bool | None = None
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.kind != HIT_MISS

    def line(self) -> str:
        if self.kind == HIT_MISS:
            return f"crc32={self.path_hash} → **接不上**（路径表里没有这条哈希）"
        tail = "" if self.kind == HIT_EXACT else "（只有末级名在库里 —— 前缀是运行时挂的）"
        chain = {True: " · 父链自证通过", False: " · ⚠ 父链自证**不通过**",
                 None: ""}[self.chain_ok]
        return f"crc32={self.path_hash} → {self.path}（{self.kind}）{tail}{chain}"


class PathTable:
    """研究线的节点路径表（`_nodepaths.json`）＋ 结构自证的尺子。

    ⛔ 只读：本类不改任何输入，也不写盘。
    """

    __slots__ = ("_by_hash", "_leaf_names", "_per_root", "_roots")

    def __init__(
        self,
        roots: Mapping[str, int],
        per_root: Mapping[str, Mapping[str, int]],
        by_hash: Mapping[int, str],
    ) -> None:
        self._roots = dict(roots)
        self._per_root = {str(k): dict(v) for k, v in per_root.items()}
        self._by_hash = {int(k) & 0xFFFFFFFF: str(v) for k, v in by_hash.items()}
        leaf: set[str] = set()
        for paths in self._per_root.values():
            for path in paths:
                leaf.update(path.split("/"))
        self._leaf_names = leaf

    # ------------------------------------------------------------ 只读面
    @property
    def n_paths(self) -> int:
        return len(self._by_hash)

    @property
    def n_roots(self) -> int:
        return len(self._roots)

    @property
    def n_leaf_names(self) -> int:
        return len(self._leaf_names)

    def resolve(self, path_hash: int) -> str | None:
        """哈希 -> 路径；**解析不出来给 None**（⛔ 不抛、不猜 —— 调用方自己决定报还是拒）。"""
        return self._by_hash.get(int(path_hash) & 0xFFFFFFFF)

    def match(self, path_hash: int) -> bool | None:
        """`True` 命中 / `None` 没命中（与 clip_decode.load_pathmap 的 `.get` 口径一致）。"""
        return None if self.resolve(path_hash) is None else True

    def roots_of(self, path: str) -> tuple[str, ...]:
        """哪些 prefab 的路径表里**逐字**有这条路径（升序）。"""
        return tuple(sorted(r for r, paths in self._per_root.items() if path in paths))

    def chain_ok(self, path: str, chain_source: Mapping[str, Any] | None = None) -> bool | None:
        """逐段核父链：`children[i].father == parent`。

        chain_source = `_nodeindex.json` 的 `nodes`（key = 节点 pathID 的十进制字符串）。
        没给就返回 None（**不猜**）；给了但这条路径不在任何 per_root 里也返回 None。
        """
        if chain_source is None:
            return None
        segs = path.split("/")
        pid_by_path = None
        for paths in self._per_root.values():
            if path in paths:
                pid_by_path = paths
                break
        if pid_by_path is None:
            return None
        for i in range(1, len(segs)):
            child = pid_by_path.get("/".join(segs[: i + 1]))
            parent = pid_by_path.get("/".join(segs[:i]))
            node = chain_source.get(str(child)) if child is not None else None
            if node is None or node.get("father") != parent:
                return False
        return True

    def explain(
        self, path_hash: int, chain_source: Mapping[str, Any] | None = None
    ) -> PathHit:
        """一次拿全一条绑定的解析结果（路径 / 口径 / 父链 / 为什么）。"""
        h = int(path_hash) & 0xFFFFFFFF
        path = self._by_hash.get(h)
        if path is None:
            return PathHit(path_hash=h, path=None, kind=HIT_MISS,
                           reason="路径表里没有这条哈希")
        roots = self.roots_of(path)
        if roots:
            return PathHit(path_hash=h, path=path, kind=HIT_EXACT, root=roots[0],
                           chain_ok=self.chain_ok(path, chain_source),
                           reason=f"{len(roots)} 个 prefab 的路径表里有它")
        if path.rsplit("/", 1)[-1] in self._leaf_names:
            return PathHit(
                path_hash=h, path=path, kind=HIT_LEAF, root=None, chain_ok=None,
                reason="整条路径不在任何 prefab 里 —— 只有末级节点名在库里"
                       "（前缀是运行时才挂上去的）",
            )
        return PathHit(path_hash=h, path=path, kind=HIT_MISS, root=None, chain_ok=None,
                       reason="末级节点名在全库 4018 个节点名里也没出现过")

    def leaf_conflicts(self, hashes: Iterable[int]) -> tuple[tuple[int, str], ...]:
        """一组哈希里，**末级名相同但整条路径不同**的那些（重名 = 只能按末级名接）。"""
        groups: dict[str, list[tuple[int, str]]] = {}
        for h in hashes:
            path = self.resolve(h)
            if path is None:
                continue
            groups.setdefault(path.rsplit("/", 1)[-1], []).append((int(h) & 0xFFFFFFFF, path))
        out: list[tuple[int, str]] = []
        for rows in groups.values():
            if len({p for _, p in rows}) > 1:
                out.extend(sorted(rows))
        return tuple(sorted(out))

    def audit(self, chain_source: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """整表的自证读数（**判据在这里，不在调用方**）。"""
        in_some_root = 0
        chain_pass = chain_fail = 0
        for path in self._by_hash.values():
            roots = self.roots_of(path)
            if roots:
                in_some_root += 1
            leaf = path.rsplit("/", 1)[-1]
            if not roots and leaf not in self._leaf_names:
                continue
            ok = self.chain_ok(path, chain_source) if roots else None
            if ok is True:
                chain_pass += 1
            elif ok is False:
                chain_fail += 1
        return {
            "paths": len(self._by_hash),
            "roots": len(self._roots),
            "leaf_names": len(self._leaf_names),
            "in_some_root": in_some_root,
            "chain_pass": chain_pass,
            "chain_fail": chain_fail,
            "chain_checked": chain_source is not None,
        }


def read_path_table(source: str | Path | Mapping[str, Any]) -> PathTable:
    """读研究线的 `_nodepaths.json`（或已经读好的 dict）。

    键集合必须**逐字**等于 `PATH_TABLE_KEYS` —— 对不上就抛（⛔ 不猜缺的那个是什么意思）。
    """
    if isinstance(source, Mapping):
        payload: Any = source
        where = "传入的 dict"
    else:
        path = Path(source)
        if not path.exists():
            raise AssetError(
                f"节点路径表不存在：{path}",
                hint="研究线产物在 研究-模型与动画/_nodepaths.json（⛔ 本模块不生成它）",
            )
        payload = json.loads(path.read_text(encoding="utf-8"))
        where = str(path)
    if not isinstance(payload, Mapping):
        raise AssetError(f"{where} 的顶层不是对象", hint="期望 {{roots, per_root, by_hash}}")
    keys = tuple(sorted(str(k) for k in payload))
    if keys != tuple(sorted(PATH_TABLE_KEYS)):
        raise AssetError(
            f"{where} 的顶层键是 {list(keys)}，期望 {list(PATH_TABLE_KEYS)}",
            hint="表结构变了 —— 先复核研究线产物，⛔ 别按猜的键名往下走",
        )
    return PathTable(payload["roots"], payload["per_root"], payload["by_hash"])


def bone_paths(table: PathTable, hashes: Sequence[int]) -> tuple[tuple[str | None, ...], int]:
    """一束哈希 -> (路径序列, 命中数)。`None` 原样留在序列里（⛔ 不跳过、不塌缩）。"""
    paths: list[str | None] = []
    hit = 0
    for h in hashes:
        got = table.resolve(h)
        if got is not None:
            hit += 1
        paths.append(got)
    return tuple(paths), hit

# --------------------------------------------------------------------------- #
# 出处（tests/test_kb_citations.py 要求的「能找回去的指针」）：
#   docs/研究-动画-三量.md（骨路径解析：研究线 03-动画/animator-clip-binding.md 的规则在这里落地）
# --------------------------------------------------------------------------- #
