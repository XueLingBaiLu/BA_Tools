r"""L11 · `AnimationManager` 参数族的**盘上那一半**（开包 / 重编码 / 写回 / 写后读回）。

`asset_kernel/anim_params.py` 是**纯**的那一半（读 / 改 / 拒，一个字节不碰盘）；本模块是它的
另一只手，而且**只有这一份** —— CLI（`cli/anim.py::_cmd_params`）与 Blender 插件
（`blender_addon/anim_drive.py` 那两个 Operator）都调 `run_params`，⛔ 谁也不许再写一套。

写面链**不另造**（与 `mesh edit` / `anim apply` **同一条**）：

    unitypy_source.open_source --读字段树--> anim_params.apply_family --纯函数改那 7 格-->
    obj.save_typetree --新对象字节--> writer.write_delta + 反向 Δ 补丁柜 --> 落盘
    --再读一遍包--> 写后读回（逐格对）

三条硬口径（照 `cli/anim.py::_cmd_apply` 那一段）：

1. **默认干跑**：`dry_run=True` 时一个字节都不写 —— 调用方自己在前后各量一次 sha256/mtime
   当场比对（`ParamsRun.untouched`），对不上就报红；干跑**连补丁柜目录都不建**
   （`ParamsRun.patch_dir_created`）；
2. **往返自证**：读出来的树原样写回去必须与磁盘上的字节**逐字节相同** —— 不然这份序列化
   不可复现，改它会连带改坏别的字段（与 `object_edit.reencode_object` 同一条口径）；
3. **只许动那 7 格**：写完先量「变了的叶子路径」，有一条越出 `_container._settings` 就抛
   （`ParamsRun` 根本不会返回那种结果）—— 这把尺子是机械的，⛔ 不是注释。
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ba_mod_maker.asset_kernel import anim_params, reverse_delta, unitypy_source, writer
from ba_mod_maker.core.errors import AssetError


class ParamsWriteError(AssetError):
    """`writer.write_delta` 那一步失败了（rc=1）—— 与「参数/对象不对」（rc=2）分开报。"""


@dataclass(frozen=True, slots=True)
class ParamsRun:
    """一次 `anim params` 干的活的**全部读数**（人读 / JSON / 面板读同一份）。"""

    bundle: Path
    path_id: int
    #: ``read``（没给改动）/ ``dry_run`` / ``write``。
    mode: str
    before: anim_params.FamilyReading
    #: 纯读那档没有 after（连重编码都没做）。
    after: anim_params.FamilyReading | None
    changed: tuple[tuple[str, float | None, float | None], ...] = ()
    object_bytes: tuple[int, int] = (0, 0)
    #: 干跑前后文件（bundle + catalog）的 sha256/mtime 是否**逐位未变**。
    untouched: bool = True
    patch_dir_created: bool = False
    report_lines: tuple[str, ...] = ()
    #: 干跑核对用：`((文件名, sha256), ...)`（bundle + catalog）。
    files_after: tuple[tuple[str, str], ...] = ()
    #: 补丁柜目录（干跑那档要看它有没有被建出来）。
    patch_dir: str = ""
    #: 写后**再读一遍包**得到的读数（只有真写那档有）。
    readback: anim_params.FamilyReading | None = None
    readback_diff: tuple[tuple[str, float | None, float | None], ...] = ()
    lines_extra: tuple[str, ...] = field(default=())

    @property
    def ok(self) -> bool:
        """这一趟干不干净（干跑动了文件 / 写后读回对不上 ⇒ 不干净）。"""
        if self.mode != "write" and not self.untouched:
            return False
        return not self.readback_diff

    def lines(self) -> tuple[str, ...]:
        """人读那一块（CLI 与面板印的是**同一批字符串**）。"""
        head = [
            f"包: {self.bundle}",
            f"对象: pathID {self.path_id} / {anim_params.FAMILY}",
            f"模式: {self.mode}",
        ]
        if self.changed:
            head.append("改动: " + "；".join(
                f"{name}: {old:g} -> {new:g}" for name, old, new in self.changed
            ) + f"（共 {len(self.changed)} 格 / 这一族 {len(anim_params.PARAM_ORDER)} 格）")
            head.append(f"对象字节: {self.object_bytes[0]:,} -> {self.object_bytes[1]:,} B")
        out = [*head, *self.lines_extra]
        if self.after is not None:
            out.extend(self.after.lines(title="改后 "))
        if self.mode == "write":
            out.append("写后读回: " + ("逐格相同" if not self.readback_diff else "对不上"))
        return tuple(out)

    def document(self) -> dict[str, Any]:
        """`--json` 那一份。"""
        return {
            "bundle": str(self.bundle),
            "path_id": int(self.path_id),
            "family": anim_params.FAMILY,
            "mode": self.mode,
            "changed": [
                {"name": n, "old": o, "new": v} for n, o, v in self.changed
            ],
            "before": self.before.document()["values"],
            "after": None if self.after is None else self.after.document()["values"],
            "readback": (None if self.readback is None
                         else self.readback.document()["values"]),
            "object_bytes": {"before": self.object_bytes[0],
                             "after": self.object_bytes[1]},
            "dry_run": self.mode != "write",
            "zero_write": self.untouched,
            "ok": self.ok,
        }


def run_params(
    bundle: str | Path,
    path_id: int,
    changes: Mapping[str, float] | None = None,
    *,
    dry_run: bool = True,
    catalog: str | Path | None = None,
    game_root: str | Path | None = None,
    project_root: str | Path | None = None,
    allow_running_game: bool = False,
    patch_label: str = "",
) -> ParamsRun:
    """整族读 / 整族改**一次做完**（默认干跑；调用方负责把读数印出来）。

    :param changes: `{名字: 新值}`（`None` / 空 = 纯读）；名字与值由 `anim_params` 那半把关。
    :raises anim_params.AnimParamsError: 名字不在族里 / 值不是数 / 树上没有 `_settings` /
        改完逐字节不变；
    :raises AssetError: 打不开包 / 没有这个 pathID / 序列化不可复现 / 写盘失败。
    """
    target = Path(bundle)
    if not target.is_file():
        raise AssetError(f"打不开这个包：{target}")
    pid = int(path_id or 0)
    if pid == 0:
        raise AnimParamsErrorPathId()
    wanted = dict(changes or {})
    project = Path(project_root) if project_root else Path.cwd()
    game = Path(game_root) if game_root else None
    cat = Path(catalog) if catalog else None
    with unitypy_source.open_source(target) as source:
        obj = source.object_at(pid)
        if obj is None:
            raise AssetError(f"{target.name} 里没有 pathID={pid} 这个对象")
        tree = obj.read_typetree()
        original = bytes(obj.get_raw_data())
        if bytes(obj.save_typetree(tree)) != original:
            raise AssetError(
                f"pathID={pid} 的 typetree 往返不是逐字节相同",
                hint="这个对象的序列化不可复现 ⇒ 这次不写",
            )
        before = anim_params.read_family(tree, path_id=pid, source=target.name)
        if not wanted:
            return ParamsRun(
                bundle=target, path_id=pid, mode="read", before=before, after=None,
            )
        new_tree = anim_params.apply_family(tree, wanted)
        payload = bytes(obj.save_typetree(new_tree))
        after = anim_params.read_family(new_tree, path_id=pid, source=target.name)
    delta = anim_params.changed_params(before, after)
    permitted = {
        ".".join((*anim_params.SETTINGS_STEPS, name)) for name, _o, _n in delta
    }
    stray = tuple(
        path for path in anim_params.unchanged_paths(tree, new_tree, limit=64)
        if path not in permitted
    )
    if stray:
        raise AssetError(
            "这次改动越出了 _container._settings 那 7 格：" + "、".join(stray[:8]),
            hint="这是缺陷（本动作只许改那 7 个 float）—— 报它，别写",
        )
    if payload == original:
        raise anim_params.AnimParamsError(
            "改完对象字节**逐字节不变**（新值与旧值相同）—— 一个字节都不写",
            hint="你要改的那几格本来就是这些值；换一个值再来",
        )
    targets = [target] + ([cat] if cat is not None and cat.is_file() else [])
    files_before = {
        str(p): (reverse_delta.sha256_of(p), p.stat().st_mtime_ns) for p in targets
    }
    patch_dir = reverse_delta.patch_store_root(project)
    patch_dir_before = patch_dir.exists()
    if len(payload) == len(original):
        change = writer.DeltaChange(
            inplace=(writer.ObjectChange(
                path_id=pid, payload=payload, name=anim_params.FAMILY,
            ),)
        )
    else:
        change = writer.DeltaChange(
            growths=(writer.ObjectGrowth(
                path_id=pid, payload=payload, name=anim_params.FAMILY,
            ),)
        )
    try:
        report = writer.write_delta(
            target,
            change,
            dry_run=bool(dry_run),
            project_root=project,
            game_root=game,
            catalog_path=cat,
            allow_running_game=bool(allow_running_game),
            patch_store=reverse_delta.PatchStore(
                reverse_delta.patch_store_root(project), game_root=game,
            ),
            patch_label=patch_label or f"anim params {target.name}#{pid}",
        )
    except (AssetError, OSError) as exc:
        raise ParamsWriteError(
            f"写回失败：{type(exc).__name__}: {exc}"
        ) from exc
    files_after = {
        str(p): (reverse_delta.sha256_of(p), p.stat().st_mtime_ns) for p in targets
    }
    readback: anim_params.FamilyReading | None = None
    readback_diff: tuple[tuple[str, float | None, float | None], ...] = ()
    if not dry_run:
        with unitypy_source.open_source(target) as again:
            readback = anim_params.read_family(
                again.read_typetree(pid), path_id=pid, source=target.name,
            )
        readback_diff = anim_params.changed_params(after, readback)
    return ParamsRun(
        bundle=target,
        path_id=pid,
        mode="write" if not dry_run else "dry_run",
        before=before,
        after=after,
        changed=delta,
        object_bytes=(len(original), len(payload)),
        untouched=files_before == files_after,
        patch_dir_created=patch_dir.exists() and not patch_dir_before,
        report_lines=tuple(report.report_lines()),
        files_after=tuple(
            (Path(path).name, digest[0]) for path, digest in files_after.items()
        ),
        patch_dir=str(patch_dir),
        readback=readback,
        readback_diff=readback_diff,
    )


def AnimParamsErrorPathId() -> anim_params.AnimParamsError:  # noqa: N802
    """「--path-id 没给」那一条错误（CLI 与面板印同一句）。"""
    return anim_params.AnimParamsError(
        "--path-id 必填（那个 AnimationManager 的 pathID）",
        hint="它挂在单位 prefab 上；不知道 pathID 就先用 object set 那条通用手看一眼",
    )


__all__ = [
    "ParamsRun",
    "ParamsWriteError",
    "run_params",
]

# --------------------------------------------------------------------------- #
# 出处（tests/test_kb_citations.py 要求的「能找回去的指针」）：
#   docs/研究-动画-三量.md（AnimationManager 参数族的读 / 改 / 写回口径）
# --------------------------------------------------------------------------- #
