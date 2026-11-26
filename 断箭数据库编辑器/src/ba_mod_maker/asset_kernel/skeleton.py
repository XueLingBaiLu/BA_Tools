"""骨骼名哈希（读侧）—— 设计稿 §8 规则 4 的**端到端**核对。

规则（设计稿 §8 规则 4 的原话是「zlib.crc32(从 root 起的层级路径, '/', UTF-8)，
跳过 root_scale，多索引槽取首个 _N」）：本单把它对到了**真实资产**上
（工单 §13 R12 点名的那一项「还差一步端到端核对」）。

实测（RU_T72B_Base 的 Chassis，38 个骨骼槽，2026-10-03）：

- m_BoneNameHashes 38 条、m_BindPose 38 条；
- 29 个槽有真实 Transform（其余 9 个 m_PathID == 0 = **保留空槽**，正好对应
  炮塔的 turret_0..turret_8 —— 设计稿 §6 第 7 步「m_Bones 保留空槽」的物证）；
- **29/29 命中** crc32("/".join(路径从名为 root 的 Transform 起, 含 root))；
- m_RootBoneNameHash = 0x65a7524a = crc32("root/body")（**不是** crc32("root")；
  即蒙皮根是 body，root 只是它的父级）；
- 这个 prefab 的路径里没有 root_scale，所以「跳过 root_scale」这条规则在本件上
  没有反例可验 —— 本单只**实现**它，不当成已验证的结论（写进交接的未决项）。
"""

from __future__ import annotations

import zlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

__all__ = [
    "SKIP_SEGMENTS",
    "BoneHashReport",
    "bone_path_hash",
    "crc32_u32",
    "read_bind_pose",
    "read_bone_hashes",
    "root_bone_hash",
    "verify_bone_hashes",
]

#: 拼层级路径时要跳过的节点名（设计稿 §8 规则 4）。
SKIP_SEGMENTS: tuple[str, ...] = ("root_scale",)


def crc32_u32(data: bytes) -> int:
    """crc32 的无符号 32 位值（m_BoneNameHashes 存的是 uint32）。"""
    return zlib.crc32(data) & 0xFFFFFFFF


def bone_path_hash(*parts: str, skip: Sequence[str] = SKIP_SEGMENTS) -> int:
    """层级路径 -> 骨骼名哈希。parts 是**从 root 起**的各段（含 root）。"""
    kept = [p for p in parts if p.lower() not in {s.lower() for s in skip}]
    return crc32_u32("/".join(kept).encode("utf-8"))


def read_bone_hashes(mesh: Mapping[str, object]) -> tuple[int, ...]:
    """Mesh 的 m_BoneNameHashes（uint32 序列）。"""
    raw = mesh.get("m_BoneNameHashes") or ()
    return tuple(int(v) & 0xFFFFFFFF for v in raw)


def read_bind_pose(mesh: Mapping[str, object]) -> tuple[tuple[float, ...], ...]:
    """Mesh 的 m_BindPose（每骨 16 个 float）。⛔ 设计稿 §6 第 7 步：**照抄不重算**。"""
    raw = mesh.get("m_BindPose") or ()
    return tuple(tuple(float(x) for x in m) for m in raw)


def root_bone_hash(mesh: Mapping[str, object]) -> int:
    return int(mesh.get("m_RootBoneNameHash", 0) or 0) & 0xFFFFFFFF


@dataclass(slots=True)
class BoneHashReport:
    """一次「路径 vs 哈希」比对的结果。"""

    matched: dict[int, str] = field(default_factory=dict)
    unmatched: list[tuple[int, str]] = field(default_factory=list)
    empty_slots: int = 0

    @property
    def checked(self) -> int:
        return len(self.matched) + len(self.unmatched)


def verify_bone_hashes(
    paths: Sequence[str | None], hashes: Sequence[int]
) -> BoneHashReport:
    """把「从 root 起的层级路径」逐条对到 m_BoneNameHashes 上。

    :param paths: 与 hashes 等长；None = 那个槽的 PPtr 是空的（保留空槽）。
    """
    if len(paths) != len(hashes):
        raise ValueError(f"路径 {len(paths)} 条，哈希 {len(hashes)} 条 —— 长度必须一致")
    report = BoneHashReport()
    for path, value in zip(paths, hashes, strict=True):
        if path is None:
            report.empty_slots += 1
            continue
        want = bone_path_hash(*path.split("/"))
        if want == int(value) & 0xFFFFFFFF:
            report.matched[int(value) & 0xFFFFFFFF] = path
        else:
            report.unmatched.append((int(value) & 0xFFFFFFFF, path))
    return report


