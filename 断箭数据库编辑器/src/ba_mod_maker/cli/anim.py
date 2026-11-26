"""`anim` 子命令：**动画（AnimationClip <-> Action 规格）** —— D 线的产品入口。

## 一句话

把游戏里的一条 `AnimationClip` 解成「**Action 规格**」（骨 / 通道 / 关键帧，纯数据）；
把它的**时间轴整段拉伸**（同长度改动）；每一条都做**自证**（delta 全中 + 往返逐字节）。
Blender 侧拿规格造真 Action（`blender_addon/anim_entry.py` · 面板 `panels_anim.py`）。

## 六个动作

| 动作 | 干什么 | 退出码 |
|---|---|---|
| `anim list` | 列出所有 clip 的读数（曲线 / 时长 / 采样率 / **绑定解析 x/y**） | 0 / 2 |
| `anim spec` | 一条 clip -> Action 规格（人读 + `--json`） | **0 接得上 / 1 接不上 / 2 用不了** |
| `anim stretch` | 改时长：`m_StopTime` + 键时刻 + 系数（+ 同步 delta） | 0 / 1 / 2 |
| `anim apply` | 把改后的**对象字节写回包**（默认干跑，`--write` 才落字节；反向 Δ） | 0 / 1 / 2 |
| `anim verify` | 一条 clip 的自证（delta 全中 + 往返逐字节） | 0 / 1 / 2 |
| **`anim params`** | **整族读 / 整族改** `AnimationManager` 那 7 个 float | 0 / 1 / 2 |

## `anim params`：游戏侧**唯一**能改行为的那一族旋钮（L11）

游戏里车轮 / 履带 / 悬挂 / 抖动是 C# 每帧算的，包里的 AnimationClip 一根曲线都没有；
真正 `[SerializeField]` 进 prefab、且**不用重打动画**就能改行为的，是挂在单位 prefab 上那个
`AnimationManager` 的 `_container._settings`（内联 `BoneSettings`，7 个 float）：

    WheelSpeed · SuspensionSpeed · TracksSpeed · TurnSpeedMod ·
    BodyShake · BodyShakeAggressive · BodyMass

形状与名字的**唯一出处**是 `asset_kernel/unit_script_shapes.py` 的 `...|AnimationManager` 那一项
（生成物）；读 / 改 / 拒那三条口径都在 `asset_kernel/anim_params.py`（⛔ 本文件不重写一套）。
⛔ 只写这 7 个 float —— `UseContainer` / `_root` / `_body` / `_traks` / `_wheels` /
`_suspension` / `_acceleration` / `_recoils` 一个都不碰。

    ba-mod-maker anim params --bundle <units 包> --path-id <AnimationManager 的 pathID>
    ba-mod-maker anim params --bundle <包> --path-id <N> --set TracksSpeed=5.0            # 干跑
    ba-mod-maker anim params --bundle <包> --path-id <N> --set TracksSpeed=5.0 --write \
        --project-root <工程目录>

不给 `--set` 就是**纯读**（一个字节都不碰）；给了就干跑，`--write` 才落字节，且**写后读回**
再印一次（7 格逐格对）。⚠ 履带那条特别说明：往材质里写 UV offset 会被运行期满帧盖掉
（`Tracks.Move` 每帧 `set_mainTextureOffset`）⇒ 数据侧**唯一**旋钮就是 `TracksSpeed`。

## `anim apply`：从「写出对象字节」到「装回包里」（2026-10-13 补的那一跳）

`stretch --out` 只写出一份**对象字节**（一个独立文件），包还是原样 —— 本动作用它/或就地
拉伸，把那条 clip 的**等长**新字节写回包里，走的是与 `mesh edit` **同一条**链：

    ClipStore 读那一条 clip --(改值：--k 就地拉伸 / --from 装现成字节)-->
      --writer.write_delta + 反向 Δ 补丁柜--> 落盘
      --再读一遍包--> 写后复核（曲线数 / 词数 / 时长 / 字节逐项对）
      --assets restore--> 整文件 sha256 逐位回到改前

三条硬口径：

1. **默认干跑**：不给 `--write` 一个字节都不写 —— 本命令自己在干跑前后各量一次
   sha256 与 mtime 当场比对，对不上就报红；干跑**连补丁柜目录都不建**；
2. **只做等长改写**：`--k` 那条路天然等长（词数不变）；`--from` 那份字节与源**字节数不等就拒**
   （变长 = 增删关键帧那一类，要动 SerializedFile 的对象表，不在这一档）；
3. **`patch_store` 必须给**：⛔ 不给就落进 writer 里那条**拷整包**的旧链（+3.4 GB / 73.6 秒）
   —— 实现里没有关掉它的开关（见 `asset_kernel/anim_write.py::write_clip`）。

## 输入件（都要显式给，不猜路径）

| 参数 | 是什么 | 从哪来 |
|---|---|---|
| `--bundle` | units 的 UnityFS 包 | `<游戏根>/BrokenArrow_Data/StreamingAssets/aa/PC/` 里 |
| `--fixture` | 离线夹具 `clips_index.json`（离线跑 / 用例用） | `_work/anim-line/fixtures/` |
| `--meta` | units 的 SerializedFile 元数据冻结件（夹具路才要） | `_work/w10_ref/units_meta.bin` |
| `--path-table` | 节点路径表 `_nodepaths.json` | 研究线产物 `研究-模型与动画/_nodepaths.json` |
| `--nodeindex`（可选） | 父链自证用 `_nodeindex.json` | 同上；不给就**不做**父链自证（不猜） |

## 两条硬口径

1. **接不上就报**：`n_resolved == 0` 的 clip（例 LAV-M · `Id=154` 那 11 条）**抛**，退出码 1；
   部分命中要显式 `--allow-partial`，读数里带 x/y 与跳过的 `path_crc`。
2. **改时长只改该改的**：`m_FrameCount` 不动（dense 残留位）；
   `m_ValueArrayDelta` **必须同步**（`--no-sync-delta` 只给负控用，输出会打警告）。

## 例

```
ba-mod-maker anim list --bundle <units.bundle> --path-table <_nodepaths.json>
ba-mod-maker anim spec --clip "T72B hatch" --bundle <units.bundle> \
    --path-table <_nodepaths.json> --nodeindex <_nodeindex.json> --json
ba-mod-maker anim stretch --clip "T72B hatch" --bundle <units.bundle> --k 2.5 \
    --out <改后的对象字节.bin> --path-table <_nodepaths.json>

# 把那个文件装回包里（先干跑；确认读数干净再加 --write）
ba-mod-maker anim apply --bundle <units.bundle> --clip "T72B hatch" --from <改后的对象字节.bin>
ba-mod-maker anim apply --bundle <units.bundle> --clip "T72B hatch" --k 2.5 --write \
    --project-root <工程目录> --game <游戏根>

⛔ `anim apply --fixture` 没有这一档：夹具里没有包，装不回去（要落包必须给真包）。
```

"""

from __future__ import annotations

import argparse
import json
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ba_mod_maker.asset_kernel import (
    anim_action,
    anim_build,
    anim_path,
    anim_write,
    builder,
    clone,
    reverse_delta,
    unityfs,
)
from ba_mod_maker.asset_kernel.mesh_diff import payload_diff
from ba_mod_maker.cli.emit import Emitter
from ba_mod_maker.core.errors import AssetError

EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_UNREADABLE = 2

#: 「anim apply」的三档退出码 —— **照 mesh edit 那张表逐条对齐**（数值就是上面那两个）：
#: 0 完成（含干跑）· 1 写盘失败 / 写后复核不过 / 干跑动了文件 · 2 用不了（打不开包 /
#: 没有这条 clip / 参数不对 / 新对象字节数与源不等）。
EXIT_FAILED = EXIT_REFUSED
EXIT_UNUSABLE = EXIT_UNREADABLE

_CLIP_CLASS_ID = anim_build.CLASS_ANIMATION_CLIP
#: SerializedFile 头长（48 B；元数据区 = 头 + 头部声明的 metadata_size）。
_SF_HEADER = 48


def _class_index(meta: bytes) -> int:
    """AnimationClip 在**本包类型表里的下标** —— 它就是对象表 typeID 的取值。

    ⛔ 不是 classID（74）：typeID 是类型表的**项下标**（units 包实测 50 项，
    AnimationClip 在其中某一项上）。搞错这一步的症状是「拿 clip 的树去解 GameObject」
    —— 报的是 `数组 m_RotationCurves 声明 482,193,544 个元素`（2026-10-13 真踩过）。
    """
    table = builder.read_raw_type_table(meta, common_strings=clone.unitypy_common_strings())
    for index, entry in enumerate(table.entries):
        if int(entry.class_id) == _CLIP_CLASS_ID:
            return index
    raise AssetError(
        f"这个包的类型表里没有 classID={_CLIP_CLASS_ID}（AnimationClip）",
        hint="包拿错了；units 那条路是 StreamingAssets/aa/PC/units_assets_all_*.bundle",
    )


@dataclass(frozen=True, slots=True)
class ClipRecord:
    """一条 clip 的原始件。"""

    path_id: int
    payload: bytes


class ClipStore:
    """给出「所有 clip 的对象字节」+「clips 的类型树节点」—— 真包 / 离线夹具一个调用面。"""

    def __init__(self, meta: bytes, records: Sequence[ClipRecord]) -> None:
        self._meta = bytes(meta)
        self._records = tuple(records)
        self._nodes: tuple[clone.TypeNode, ...] | None = None
        #: 解出来的字段树（**懒缓存**：`by_clip` 会走两遍 rows()，不然 475 条要解两遍）。
        self._fields: list[dict[str, Any]] | None = None

    @staticmethod
    def from_bundle(bundle: str | Path) -> ClipStore:
        """真包：读 CAB- 节点的串行化元数据 + 逐对象字节（**不开 UnityPy**）。"""
        reader = unityfs.open_bundle(bundle)
        try:
            node = next((n for n in reader.nodes if n.path.startswith("CAB-")), None)
            if node is None:
                raise AssetError(
                    f"{Path(bundle).name} 里没有 CAB- 节点（共 {len(reader.nodes)} 个节点）",
                    hint="units 的 bundle 只有 CAB- 一个串行化节点",
                )
            info = unityfs.read_serialized(reader, node)
            meta = reader.read_range(node.offset, _SF_HEADER + int(info.metadata_size))
            # ★ 一定要按 **typeID 过滤**：units 包有 162,010 个对象，第 0 个是 GameObject，
            #   拿 AnimationClip 的 529 节点树去解一个 80 字节的 GameObject 必然报错。
            type_index = _class_index(meta)
            records = [
                ClipRecord(
                    path_id=int(path_id),
                    payload=reader.read_range(
                        node.offset + info.data_offset + stored, size),
                )
                for _i, path_id, stored, size, tid in info.objects
                if int(tid) == type_index
            ]
        finally:
            reader.close()
        return ClipStore(meta, records)

    @staticmethod
    def from_fixture(payloads: str | Path, index: str | Path, meta: str | Path) -> ClipStore:
        """离线夹具（**不开包**）：`clips_payloads.bin` + `clips_index.json` + meta。"""
        blob = Path(payloads).read_bytes()
        rows = json.loads(Path(index).read_text(encoding="utf-8"))["rows"]
        records = [
            ClipRecord(path_id=int(r["path_id"]),
                       payload=blob[r["offset"]: r["offset"] + r["length"]])
            for r in rows
        ]
        return ClipStore(Path(meta).read_bytes(), records)

    def nodes(self) -> tuple[clone.TypeNode, ...]:
        if self._nodes is None:
            table = builder.read_raw_type_table(
                self._meta, common_strings=clone.unitypy_common_strings())
            self._nodes = tuple(table.entry_for(_CLIP_CLASS_ID).nodes)
        return self._nodes

    def __len__(self) -> int:
        return len(self._records)

    def _decoded(self) -> list[dict[str, Any]]:
        if self._fields is None:
            nodes = self.nodes()
            self._fields = [dict(clone.read_object(r.payload, nodes)) for r in self._records]
        return self._fields

    def rows(self) -> Iterator[tuple[ClipRecord, dict[str, Any]]]:
        yield from zip(self._records, self._decoded(), strict=True)

    def by_clip(self, needle: str) -> tuple[ClipRecord, dict[str, Any]]:
        """按**名字**或 **pathID** 找一条；同名多条必须用 pathID（不静默取第一条）。"""
        text = str(needle).strip()
        wanted_pid = int(text) if text.lstrip("-").isdigit() else None
        label = f"pathID={text}" if wanted_pid is not None else repr(text)
        hits = [
            (record, fields)
            for record, fields in self.rows()
            if (record.path_id == wanted_pid if wanted_pid is not None
                else str(fields.get("m_Name")) == text)
        ]
        if not hits:
            raise AssetError(
                f"包里没有 {label} 的 AnimationClip",
                hint="先跑 anim list（同名 clip 有多条时请用 pathID）",
            )
        if len(hits) > 1:
            raise AssetError(
                f"有 {len(hits)} 条同名 clip {text!r}：pathID={[h[0].path_id for h in hits]}",
                hint="同名必须用 pathID 指定",
            )
        return hits[0]


def _load_path_table(args: argparse.Namespace) -> anim_path.PathTable:
    return anim_path.read_path_table(args.path_table)


def _load_chain(args: argparse.Namespace) -> dict[str, Any] | None:
    if not args.nodeindex:
        return None
    payload = json.loads(Path(args.nodeindex).read_text(encoding="utf-8"))
    return payload["nodes"]


def _open_store(args: argparse.Namespace) -> ClipStore:
    if args.bundle:
        return ClipStore.from_bundle(args.bundle)
    if args.fixture:
        if not args.meta:
            raise AssetError(
                "--fixture 那条路必须同时给 --meta（夹具里没有类型树）",
                hint="例：--meta _work/w10_ref/units_meta.bin",
            )
        payloads = Path(args.fixture).with_name("clips_payloads.bin")
        if not payloads.exists():
            raise AssetError(
                f"夹具少了 {payloads.name}（应与 {Path(args.fixture).name} 同目录）")
        return ClipStore.from_fixture(payloads, args.fixture, args.meta)
    raise AssetError("要么给 --bundle，要么给 --fixture",
                     hint="anim --help 里两者各有用法")


def _spec_for(
    args: argparse.Namespace,
    table: anim_path.PathTable,
    record: ClipRecord,
    fields: dict[str, Any],
    chain: dict[str, Any] | None,
) -> anim_action.AnimSpec:
    return anim_action.build_action_spec(
        fields, table, path_id=record.path_id,
        allow_partial=bool(args.allow_partial), chain_source=chain,
    )


# --------------------------------------------------------------------------- #
# 四个动作
# --------------------------------------------------------------------------- #
def _report(args: argparse.Namespace, lines: Sequence[str], payload: Any) -> None:
    """把「人读 + `--json`」二选一打出去（与 `names.py` 同一个形状，⛔ 不混）。"""
    if getattr(args, "json_out", False):
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    for line in lines:
        print(line, flush=True)


def _fail(args: argparse.Namespace, message: str) -> None:
    """失败也要在 `--json` 时给一份机器可读的（stdout 上永远不出现非 JSON）。"""
    if getattr(args, "json_out", False):
        print(json.dumps({"ok": False, "error": str(message)}, ensure_ascii=False))
        return
    print(f"错误：{message}", flush=True)


def _cmd_list(args: argparse.Namespace) -> int:
    store = _open_store(args)
    table = _load_path_table(args)
    chain = _load_chain(args)
    rows: list[dict[str, Any]] = []
    for record, fields in store.rows():
        stats = anim_action.binding_stats(
            str(fields.get("m_Name") or ""), record.path_id,
            list(fields["m_ClipBindingConstant"]["genericBindings"]), table,
            chain_source=chain,
        )
        channels = anim_build.channels_of(fields)
        muscle = fields["m_MuscleClip"]
        duration = float(muscle["m_StopTime"]) - float(muscle["m_StartTime"])
        rows.append({
            "name": str(fields.get("m_Name") or ""),
            "path_id": record.path_id,
            "curves": channels.curve_total,
            "duration": duration,
            "sample_rate": float(fields["m_SampleRate"]),
            "bindings": stats.n_bindings,
            "generic_bindings": stats.n_generic_bindings,
            "muscle_bindings": stats.n_muscle_bindings,
            "resolved": stats.n_resolved,
            "kind": ("empty" if stats.empty else
                     "dead" if stats.dead else
                     "partial" if stats.partial else "full"),
        })
    n_full = sum(1 for r in rows if r["kind"] == "full")
    n_partial = sum(1 for r in rows if r["kind"] == "partial")
    n_dead = sum(1 for r in rows if r["kind"] == "dead")
    n_empty = sum(1 for r in rows if r["kind"] == "empty")
    total_bind = sum(r["generic_bindings"] for r in rows)
    total_hit = sum(r["resolved"] for r in rows)
    lines = [
        f"动画 clip：{len(rows)} 条  ·  曲线合计 {sum(r['curves'] for r in rows)} 条",
        f"绑定解析：{total_hit} / {total_bind} "
        + (f"({100.0 * total_hit / total_bind:.4f}%)" if total_bind else "(没有 generic 绑定)"),
        f"逐条口径：全命中 {n_full} · 部分 {n_partial} · **接不上 {n_dead}** · 空 clip {n_empty}",
    ]
    if n_dead:
        dead_names = sorted({r["name"] for r in rows if r["kind"] == "dead"})
        lines.append(f"   接不上的那些：{dead_names[:8]}" + ("…" if len(dead_names) > 8 else ""))
    _report(args, lines, {"clips": rows,
                          "totals": {"clips": len(rows), "bindings": total_bind,
                                     "resolved": total_hit, "full": n_full,
                                     "partial": n_partial, "dead": n_dead,
                                     "empty": n_empty}})
    return EXIT_OK


def _cmd_spec(args: argparse.Namespace) -> int:
    store = _open_store(args)
    table = _load_path_table(args)
    record, fields = store.by_clip(args.clip)
    try:
        spec = _spec_for(args, table, record, fields, _load_chain(args))
    except AssetError as exc:
        _fail(args, str(exc))
        return EXIT_REFUSED
    payload = {
        "clip": spec.name,
        "path_id": spec.path_id,
        "curves": spec.n_curves,
        "duration": spec.duration,
        "sample_rate": spec.sample_rate,
        "samples": spec.n_samples,
        "bones": [b.path for b in spec.bones],
        "channels": sorted({str(c.channel) for c in spec.curves}),
        "bindings": {"generic": spec.stats.n_generic_bindings,
                     "muscle": spec.stats.n_muscle_bindings,
                     "resolved": spec.stats.n_resolved,
                     "unresolved_crcs": list(spec.stats.unresolved_crcs)},
        "max_quat_norm_delta": spec.max_quat_norm_delta,
        "notes": list(spec.notes),
        "curves_detail": [
            {"index": c.curve_index, "bone": c.bone_path, "channel": c.channel,
             "component": c.component, "keys": len(c.keys)}
            for c in spec.curves
        ],
    }
    _report(args, list(spec.summary()), payload)
    return EXIT_OK


def _cmd_stretch(args: argparse.Namespace) -> int:
    store = _open_store(args)
    record, fields = store.by_clip(args.clip)
    try:
        after, report = anim_write.stretch_clip(
            fields, float(args.k), payload=record.payload, nodes=store.nodes(),
            sync_delta=not args.no_sync_delta,
        )
    except AssetError as exc:
        _fail(args, str(exc))
        return EXIT_REFUSED
    out = Path(args.out) if args.out else None
    written = 0
    digest = ""
    if out is not None:
        raw_new = clone.write_object(after, store.nodes())
        out.write_bytes(raw_new)
        written = len(raw_new)
        digest = anim_write.clip_sha256(raw_new)
    lines = list(report.lines())
    if out is not None:
        lines.append(f"    写出 {out}（{written:,} B · sha256 {digest[:16]}…）")
        record_delta = anim_write.inverse_record(record.payload)
        digest16 = record_delta["sha256"][:16]
        lines.append(f"    反向 Δ：{record_delta['size']:,} B · sha256 {digest16}…")
    _report(args, lines, {
        "clip": report.name, "k": report.k, "old_stop": report.old_stop,
        "new_stop": report.new_stop, "words": report.words,
        "old_size": report.old_size, "new_size": report.new_size,
        "size_unchanged": report.size_unchanged,
        "delta": {"hits": report.delta_hits, "total": report.delta_total,
                  "max_change": report.delta_max_change},
        "dense": {"curves": report.dense.curve_count, "frames": report.dense.frame_count,
                  "consumed": report.dense.consumed},
        "out": str(out) if out else None, "sha256": digest,
    })
    return EXIT_OK


def _cmd_verify(args: argparse.Namespace) -> int:
    store = _open_store(args)
    record, fields = store.by_clip(args.clip)
    nodes = store.nodes()
    roundtrip = clone.write_object(fields, nodes) == record.payload
    channels = anim_build.channels_of(fields)
    muscle = fields["m_MuscleClip"]
    check = anim_build.delta_check(
        channels, muscle["m_ValueArrayDelta"],
        float(muscle["m_StartTime"]), float(muscle["m_StopTime"]))
    can, why = anim_write.stretchable(fields)
    lines = [
        f"{fields['m_Name']}（pathID={record.path_id}）：{len(record.payload):,} B",
        f"    往返逐字节：{'相等' if roundtrip else '**不相等**'}",
        f"    delta 自证：{check.line()}",
        f"    改时长：{'可以就地拉伸' if can else '**不行**'} —— {why}",
    ]
    _report(args, lines, {
        "clip": str(fields["m_Name"]), "path_id": record.path_id,
        "roundtrip": roundtrip, "delta": {"matched": check.matched,
                                          "curves": check.curves, "worst": check.worst},
        "stretchable": can, "why": why,
    })
    return EXIT_OK if (roundtrip and check.ok) else EXIT_REFUSED


def _game_root(text: str | None) -> Path | None:
    """游戏根：--game 优先，否则自动探测。找不到返回 None（**不是异常**）。

    ⚠ 它只喂「补丁柜跟随游戏所在盘」那一条守卫；探测本身只是 stat 一个标记文件。
    """
    from ba_mod_maker.core.assets import game as game_mod

    if text:
        root = Path(text)
        return root if game_mod.is_game_root(root) else None
    return game_mod.find_game_root()


def _cmd_apply(args: argparse.Namespace) -> int:
    """把一条改后的 AnimationClip **写回包**（默认干跑；--write 才落字节）。

    与 mesh edit 同一条链：writer.write_delta ＋ **反向 Δ 补丁柜**（⛔ 不拷整包）＋
    写后复核（读回那条 clip，比曲线数 / 词数 / 时长 / 对象字节）。
    退出码见下面的 EXIT_FAILED / EXIT_UNUSABLE（照 mesh edit 那张表）。
    那一行「哪些字节变了」转调 asset_kernel.mesh_diff.payload_diff（唯一实现，⛔ 不抄第二份）。
    """
    emitter = Emitter(json_mode=bool(getattr(args, "json_out", False)))
    bundle = Path(str(getattr(args, "bundle", "") or ""))
    if not bundle.is_file():
        emitter.failure(
            f"打不开这个包：{bundle}",
            hint="给一个真的 .bundle 路径（例 .../StreamingAssets/aa/PC/xxx.bundle）",
        )
        return EXIT_UNUSABLE
    needle = str(getattr(args, "clip", "") or "").strip()
    if not needle:
        emitter.failure("--clip 必填（clip 名或 pathID；同名多条要用 pathID）")
        return EXIT_UNUSABLE
    k = getattr(args, "k", None)
    from_file = getattr(args, "from_file", None)
    if (k is None) == (from_file is None):
        emitter.failure(
            "要么给 --k（就地拉伸时间轴）、要么给 --from（装一份现成的对象字节）—— 二选一",
            hint="--from 那份就是 anim stretch --out 写出来的对象字节",
        )
        return EXIT_UNUSABLE
    if k is not None and not float(k) > 0:
        emitter.failure(f"--k 必须为正（收到 {k!r}）")
        return EXIT_UNUSABLE

    try:
        store = ClipStore.from_bundle(bundle)
    except (AssetError, OSError, ValueError, TypeError) as exc:
        emitter.failure(f"{type(exc).__name__}: {exc}")
        return EXIT_UNUSABLE
    try:
        record, fields = store.by_clip(needle)
    except AssetError as exc:
        emitter.failure(exc.message, hint=exc.hint)
        return EXIT_UNUSABLE
    nodes = store.nodes()
    name = str(fields.get("m_Name") or "")
    source_shape = anim_write.shape_of(fields, record.payload, name=name)

    # ---- 新对象字节：--k 就地拉伸 / --from 装现成的那一份 ----
    changed_lines: tuple[str, ...] = ()
    if k is not None:
        try:
            after, stretch = anim_write.stretch_clip(
                fields, float(k), payload=record.payload, nodes=nodes)
        except AssetError as exc:
            emitter.failure(exc.message, hint=exc.hint)
            return EXIT_UNUSABLE
        expected_fields: Mapping[str, Any] = after
        new_raw = clone.write_object(after, nodes)
        changed_lines = stretch.lines()
    else:
        try:
            new_raw = Path(str(from_file)).read_bytes()
        except OSError as exc:
            emitter.failure(f"读不了 --from 那个文件：{exc}")
            return EXIT_UNUSABLE
        try:
            expected_fields = dict(clone.read_object(new_raw, nodes))
        except (AssetError, ValueError, TypeError, KeyError, IndexError) as exc:
            emitter.failure(f"--from 那份字节解不成 AnimationClip：{type(exc).__name__}: {exc}")
            return EXIT_UNUSABLE
        if str(expected_fields.get("m_Name") or "") != name:
            emitter.failure(
                f"--from 那份的名字是 {expected_fields.get('m_Name')!r}，源 clip 是 {name!r}",
                hint="拿错文件了：本动作只改**同一条** clip 的值",
            )
            return EXIT_UNUSABLE
    try:
        expected_shape = anim_write.shape_of(expected_fields, new_raw, name=name)
    except (AssetError, ValueError, TypeError, KeyError, IndexError) as exc:
        # 解得开但**读不出形状**的字节（例：流式词流被截断）—— 那也是"这份字节不能用"
        emitter.failure(f"--from 那份的形状读不出来：{type(exc).__name__}: {exc}")
        return EXIT_UNUSABLE

    # ---- 两条闸：等长 ＋ 真的改了（不然就是白写一次盘）----
    if len(new_raw) != len(record.payload):
        emitter.failure(
            f"新对象 {len(new_raw):,} B 与源 {len(record.payload):,} B 不等：这不是「等长改写」",
            hint="变长 / 变短 = 增删关键帧那一类（要动 SerializedFile 的对象表），"
                 "本动作拒；--k 那条路天然等长",
        )
        return EXIT_UNUSABLE
    if new_raw == record.payload:
        emitter.failure(
            "这条 clip 改完逐字节不变（没有可改的时间轴）—— 一个字节都没写",
            hint="换一条 clip；anim verify 的「改时长」那一行会先说这条能不能改",
        )
        return EXIT_UNUSABLE

    catalog = Path(str(getattr(args, "catalog", ""))) if getattr(args, "catalog", None) else None
    project_root = Path(str(getattr(args, "project_root", "") or Path.cwd()))
    game_root = _game_root(getattr(args, "game", None))
    patch_dir = reverse_delta.patch_store_root(project_root)
    targets = [bundle] + ([catalog] if catalog is not None and catalog.is_file() else [])
    before = {str(p): (reverse_delta.sha256_of(p), p.stat().st_mtime_ns) for p in targets}
    patch_dir_before = patch_dir.exists()
    write = bool(getattr(args, "write", False))
    try:
        report = anim_write.write_clip(
            bundle,
            record.path_id,
            new_raw,
            name=name,
            dry_run=not write,
            project_root=project_root,
            game_root=game_root,
            catalog_path=catalog,
            allow_running_game=bool(getattr(args, "allow_running", False)),
            patch_label=f"anim apply {bundle.name}#{record.path_id}",
        )
    except (AssetError, OSError) as exc:
        emitter.failure(f"写回失败：{type(exc).__name__}: {exc}")
        return EXIT_FAILED
    after = {str(p): (reverse_delta.sha256_of(p), p.stat().st_mtime_ns) for p in targets}
    untouched = before == after
    patch_dir_created = patch_dir.exists() and not patch_dir_before

    # ---- 人读那一份 ----
    emitter.field("包", str(bundle))
    emitter.field("clip", f"{name!r}（pathID={record.path_id}）")
    emitter.field("模式", "真写" if write else "干跑")
    for line in changed_lines:
        emitter.line("    " + line)
    emitter.field("源", source_shape.line())
    emitter.field("预期", expected_shape.line())
    emitter.field("改动", f"{len(payload_diff(record.payload, new_raw)):,} / "
                          f"{len(record.payload):,} B 变了（等长）")
    for line in report.report_lines():
        emitter.line("    " + line)
    if not write:
        for path in targets:
            emitter.line(f"    零写入核对 {path.name}: sha256 {after[str(path)][0][:16]}…")
        emitter.line(
            "    ✅ 干跑零写入（sha256 与 mtime 逐位未变）" if untouched
            else "    ❌ 干跑动了文件（sha256 或 mtime 变了）—— 这是缺陷，报它"
        )
        emitter.line(
            f"    ❌ 干跑建了补丁柜目录：{patch_dir} —— 这是缺陷，报它" if patch_dir_created
            else f"    ✅ 干跑没建补丁柜目录（{patch_dir}）"
        )

    # ---- 写后复核：再读一遍包 ----
    check: anim_write.WritebackCheck | None = None
    others: tuple[bool, int, tuple[int, ...]] | None = None
    if write:
        try:
            again = ClipStore.from_bundle(bundle)
            record2, fields2 = again.by_clip(str(record.path_id))
        except (AssetError, OSError, ValueError, TypeError) as exc:
            emitter.error(f"写后读不回来：{type(exc).__name__}: {exc}")
            return EXIT_FAILED
        check = anim_write.check_writeback(
            path_id=record.path_id,
            source_shape=source_shape, source_raw=record.payload,
            expected_shape=expected_shape, expected_raw=new_raw,
            readback_shape=anim_write.shape_of(fields2, record2.payload, name=name),
            readback_raw=record2.payload,
        )
        others = anim_write.other_clips_unchanged(
            {r.path_id: r.payload for r, _f in store.rows()},
            {r.path_id: r.payload for r, _f in again.rows()},
            record.path_id,
        )
        for line in check.lines():
            emitter.line("    " + line)
        ok_others, n_others, bad_ids = others
        emitter.line(
            f"    {'✅' if ok_others else '❌'} 其它 clip {n_others} 条逐字节不变"
            + ("" if ok_others else f"：变了 {list(bad_ids)}")
        )

    verify = report.verify or {}
    bad_verify = [key for key, value in verify.items()
                  if isinstance(value, Mapping) and not value.get("ok")]
    emitter.document({
        "bundle": str(bundle),
        "path_id": record.path_id,
        "clip": name,
        "mode": "write" if write else "dry_run",
        "source": _shape_doc(source_shape),
        "expected": _shape_doc(expected_shape),
        "changed_bytes": len(payload_diff(record.payload, new_raw)),
        "object_bytes": len(new_raw),
        "patch": str(report.patch) if report.patch else None,
        "patch_bytes": int(report.patch_bytes),
        "write_seconds": report.seconds,
        "zero_write": bool(untouched) if not write else None,
        "patch_dir_created": bool(patch_dir_created),
        "verify_failed": bad_verify,
        "readback": None if check is None else {
            "ok": check.ok,
            "bytes_match": check.bytes_match,
            "target_changed": check.target_changed,
            "same_length": check.same_length,
            "shape_bad": list(check.shape_bad),
            **_shape_doc(check.readback),
        },
        "others_unchanged": None if others is None else {
            "ok": others[0], "compared": others[1], "changed": list(others[2]),
        },
    })
    if not write and not untouched:
        return EXIT_FAILED
    if not write and patch_dir_created:
        return EXIT_FAILED
    if write:
        if bad_verify:
            emitter.error("写后复核不过：" + ", ".join(bad_verify))
            return EXIT_FAILED
        if check is not None and not check.ok:
            emitter.error("写后读回与预期对不上（见上面那几行）")
            return EXIT_FAILED
        if others is not None and not others[0]:
            emitter.error(f"目标之外还有 {len(others[2])} 条 clip 的字节变了")
            return EXIT_FAILED
    return EXIT_OK


def _cmd_drive(args: argparse.Namespace) -> int:
    """把驱动公式应用到一份 `.blend`（起 Blender，写 fcurve + 履带 UV 关键帧）。"""
    from ba_mod_maker.core.assets.anim_drive_apply import apply_drive

    run = apply_drive(
        args.blend,
        speed=float(args.speed),
        turn=float(args.turn),
        frames=int(args.frames),
        hide=tuple(getattr(args, "hide", ()) or ()),
        exaggerate=float(getattr(args, "exaggerate", 1.0)),
        out=getattr(args, "out", None),
    )
    for line in run.lines():
        print(line)
    return 0


def _cmd_container(args: argparse.Namespace) -> int:
    """导出 `AnimationManager._container` 整表（只读；PPtr 解成骨骼/对象名）。

    开包 / 解析那一层在 `asset_kernel/anim_container.py`（⛔ 这里不重写读面）。
    """
    from ba_mod_maker.asset_kernel.anim_container import read_container

    data = read_container(args.bundle, int(args.path_id))
    payload = json.dumps(data, ensure_ascii=False, indent=1)
    if getattr(args, "out", None):
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(payload, encoding="utf-8")
    if getattr(args, "json_out", False):
        print(payload)
        return 0
    container = data["container"]
    settings = data["settings"]
    print("包          : " + str(data["bundle"]))
    print("对象        : pathID " + str(data["path_id"]) + " / " + str(data["family"]))
    print("参数        : " + str(len(settings)) + " 个 float")
    for key, value in settings.items():
        print("    " + str(key) + " = " + str(value))
    print("整表键      : " + ", ".join(sorted(container)))
    print("悬挂元素    : " + str(data["suspension_elements"]) + " 个（每根轴一个 Bone ＋ 行程）")
    for side, items in (container.get("_wheels") or {}).items():
        if not isinstance(items, (list, tuple)):
            continue
        names = [str(item.get("name") or item.get("path_id")) for item in items]
        print("    车轮 " + str(side) + "：" + str(len(names)) + " 个 " + str(names))
    for slot, item in (container.get("_traks") or {}).items():
        if isinstance(item, dict):
            print("    履带 " + str(slot) + "：" + str(item.get("name") or item.get("path_id")))
    recoils = container.get("_recoils") or {}
    print("后坐        : _keyData " + str(len(recoils.get("_keyData") or ())) + " 条")
    if getattr(args, "out", None):
        print("已落盘      : " + str(args.out))
    return 0

def _cmd_params(args: argparse.Namespace) -> int:
    """整族读 / 整族改 `AnimationManager._container._settings`（默认干跑，⛔ 零写入）。

    这一层只做**参数解析 ＋ 印读数**；开包 / 重编码 / 写回 / 写后读回全在
    `asset_kernel/anim_params_io.run_params` —— Blender 插件那两个 Operator 调的是**同一个**
    函数（⛔ 谁都不许再写一套写面）。
    """
    from ba_mod_maker.asset_kernel import anim_params, anim_params_io

    emitter = Emitter(json_mode=bool(getattr(args, "json_out", False)))
    bundle = Path(str(getattr(args, "bundle", "") or ""))
    if not bundle.is_file():
        emitter.failure(
            f"打不开这个包：{bundle}",
            hint="给一个真的 .bundle 路径（单位 prefab 在那个 units 包里）",
        )
        return EXIT_UNUSABLE
    try:
        changes = anim_params.parse_changes(list(getattr(args, "set", []) or []))
        source_json = getattr(args, "from_json", None)
        if source_json:
            # ⚠ `utf-8-sig`：Windows 上用 PowerShell 落的那份 JSON 常带 BOM，
            #   而 `utf-8` 读到 BOM 会当场 JSONDecodeError（实测踩过）。
            document = json.loads(Path(str(source_json)).read_text(encoding="utf-8-sig"))
            for name, value in anim_params.parse_document(document).items():
                if name in changes:
                    raise anim_params.AnimParamsError(
                        f"{name} 在 --set 与 --from-json 里给了两遍"
                    )
                changes[name] = value
    except anim_params.AnimParamsError as exc:
        emitter.failure(exc.message, hint=exc.hint)
        return EXIT_UNUSABLE
    except (OSError, json.JSONDecodeError) as exc:
        emitter.failure(f"--from-json 那份读不了：{type(exc).__name__}: {exc}")
        return EXIT_UNUSABLE

    write = bool(getattr(args, "write", False))
    catalog = Path(str(getattr(args, "catalog", ""))) if getattr(args, "catalog", None) else None
    try:
        run = anim_params_io.run_params(
            bundle,
            int(getattr(args, "path_id", 0) or 0),
            changes,
            dry_run=not write,
            catalog=catalog,
            game_root=_game_root(getattr(args, "game", None)),
            project_root=Path(str(getattr(args, "project_root", "") or Path.cwd())),
            allow_running_game=bool(getattr(args, "allow_running", False)),
        )
    except anim_params_io.ParamsWriteError as exc:
        emitter.failure(str(exc))
        return EXIT_FAILED
    except anim_params.AnimParamsError as exc:
        emitter.failure(exc.message, hint=exc.hint)
        return EXIT_UNUSABLE
    except (AssetError, OSError, ValueError, TypeError, KeyError, IndexError) as exc:
        emitter.failure(f"{type(exc).__name__}: {exc}")
        return EXIT_UNUSABLE

    emitter.field("包", str(run.bundle))
    emitter.field("对象", f"pathID {run.path_id} / {anim_params.FAMILY}")
    if run.mode == "read":
        emitter.field("模式", "纯读（没给 --set；一个字节都不碰）")
        for line in run.before.lines():
            emitter.line("    " + line)
        emitter.document(run.document())
        return EXIT_OK
    emitter.field("模式", "真写" if write else "干跑")
    emitter.field(
        "改动",
        "；".join(f"{name}: {old:g} -> {new:g}" for name, old, new in run.changed)
        + f"（共 {len(run.changed)} 格 / 这一族 {len(anim_params.PARAM_ORDER)} 格）",
    )
    emitter.field(
        "对象字节",
        f"{run.object_bytes[0]:,} -> {run.object_bytes[1]:,} B"
        + ("（等长）" if run.object_bytes[0] == run.object_bytes[1] else "（变长）"),
    )
    for line in run.report_lines:
        emitter.line("    " + line)
    if not write:
        for name, digest in run.files_after:
            emitter.line(f"    零写入核对 {name}: sha256 {digest[:16]}…")
        emitter.line(
            "    ✅ 干跑零写入（sha256 与 mtime 逐位未变）" if run.untouched
            else "    ❌ 干跑动了文件（sha256 或 mtime 变了）—— 这是缺陷，报它"
        )
        emitter.line(
            f"    ❌ 干跑建了补丁柜目录：{run.patch_dir} —— 这是缺陷，报它"
            if run.patch_dir_created
            else f"    ✅ 干跑没建补丁柜目录（{run.patch_dir}）"
        )
    if run.readback is None:
        back_line = "（干跑：没写盘，不读回）"
    elif not run.readback_diff:
        back_line = "逐格相同 ✅"
    else:
        back_line = "❌ " + "；".join(
            f"{name}: 期望 {expected:g} 读回 {got:g}"
            for name, expected, got in run.readback_diff
        )
    emitter.field("写后读回", back_line)
    if run.after is not None:
        for line in run.after.lines(title="改后 "):
            emitter.line("    " + line)
    emitter.document(run.document())
    return EXIT_OK if run.ok else EXIT_FAILED

def _shape_doc(shape: anim_write.ClipShape) -> dict[str, Any]:
    """形状读数 -> JSON 文档那一小块（人读与机器读同一组数字）。"""
    return {
        "name": shape.name,
        "bytes": shape.payload_bytes,
        "curves": shape.curves,
        "words": shape.words,
        "keys": shape.keys,
        "duration": shape.duration,
    }


# --------------------------------------------------------------------------- #
# 注册（__main__.py 只调这一句）
# --------------------------------------------------------------------------- #
def add_parser(sub: Any) -> argparse.ArgumentParser:
    """注册 `anim` 子命令（`cli/__main__.py` 只调这一句）。"""
    parser = sub.add_parser(
        "anim",
        help="动画：clip -> Action 规格 / 改时长（同长度）/ 写回包（反向 Δ）/ 自证",
        description="断箭 AnimationClip：解成 Action 规格、整段拉伸时间轴、逐条自证",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    actions = parser.add_subparsers(dest="anim_action")

    def _common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--bundle", help="units 的 UnityFS 包（真包那条路）")
        p.add_argument("--fixture", help="离线夹具 clips_index.json（离线跑/用例用）")
        p.add_argument("--meta", help="units 的 SerializedFile 元数据冻结件（夹具路必须给）")
        p.add_argument("--path-table", required=True, help="_nodepaths.json（节点路径表）")
        p.add_argument("--nodeindex", help="_nodeindex.json（给出来才做父链自证）")
        p.add_argument("--json", dest="json_out", action="store_true",
                       help="stdout 只写一份 JSON 文档")

    p_ai = actions.add_parser("attrs-import", help="真件 prefab -> props.json（动画属性清单）")
    p_ai.add_argument("--prefab", required=True, help="真件 prefab（ExportedProject 里的）")
    p_ai.add_argument("--out", required=True, help="输出 props.json")

    p_aa = actions.add_parser("attrs-apply", help="props.json -> .blend 的物体自定义属性")
    p_aa.add_argument("--blend", required=True)
    p_aa.add_argument("--props", required=True)

    p_ae = actions.add_parser(
        "attrs-export", help=".blend -> model.fbx + model.glb + anim_config.json")
    p_ae.add_argument("--blend", required=True)
    p_ae.add_argument("--out", default="", help="输出目录（默认与 .blend 同目录）")

    p_list = actions.add_parser("list", help="列出所有 clip 与绑定解析率",
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    _common(p_list)

    p_spec = actions.add_parser("spec", help="一条 clip -> Action 规格")
    _common(p_spec)
    p_spec.add_argument("--clip", required=True, help="clip 名或 pathID（同名多条必须用 pathID）")
    p_spec.add_argument("--allow-partial", action="store_true",
                        help="部分命中时继续（读数里带 x/y 与跳过的 path_crc）")

    p_str = actions.add_parser("stretch", help="改时长（同长度改动，同步 m_ValueArrayDelta）")
    _common(p_str)
    p_str.add_argument("--clip", required=True, help="clip 名或 pathID")
    p_str.add_argument("--k", required=True, type=float, help="拉伸系数（> 0）")
    p_str.add_argument("--out", help="把改后的**对象字节**写到这个文件")
    p_str.add_argument("--no-sync-delta", action="store_true",
                       help="不同步 m_ValueArrayDelta（**只给负控用**，会让自证掉下来）")

    p_app = actions.add_parser(
        "apply",
        help="把改后的对象字节写回包（默认干跑，--write 才落字节；反向 Δ）",
        epilog=(
            "退出码：\n"
            "  0  完成（含干跑）\n"
            "  1  写盘失败 / 写后复核不过 / 干跑动了文件\n"
            "  2  用不了（打不开包 / 没有这条 clip / 参数不对 / 新对象字节数与源不等）\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_app.add_argument("--bundle", required=True, help="目标 UnityFS 包（units 那种）")
    p_app.add_argument("--clip", required=True, help="clip 名或 pathID（同名多条要用 pathID）")
    p_app.add_argument("--k", type=float, help="就地拉伸系数（> 0；与 --from 二选一）")
    p_app.add_argument(
        "--from", dest="from_file", metavar="BIN",
        help="装一份现成的对象字节（就是 anim stretch --out 写出来的那份）",
    )
    p_app.add_argument("--write", action="store_true",
                       help="真写（⛔ 不给就只出计划，一个字节都不写）")
    p_app.add_argument(
        "--catalog",
        help="catalog.json / catalog.bin（给了就成对更新它的"
        " m_Crc / m_BundleSize）· ⚠ 它**不动** aa/catalog.hash"
        "（要两个文件一起管走 catalog crc）",
    )
    p_app.add_argument("--project-root",
                       help="工程目录（补丁柜落 <工程目录>/patches；默认当前目录）")
    p_app.add_argument("--game", help="游戏根目录（默认自动探测；只在判定补丁柜盘位时用）")
    p_app.add_argument("--allow-running", action="store_true",
                       help="允许游戏在跑时写入（默认：游戏在跑就拒，一个字节都不写）")
    p_app.add_argument("--json", dest="json_out", action="store_true",
                       help="stdout 只写一份 JSON 文档")

    p_par = actions.add_parser(
        "params",
        help="整族读 / 整族改 AnimationManager 的参数（默认干跑，--write 才落字节）",
        epilog=(
            "退出码：\n"
            "  0  完成（含干跑与纯读）\n"
            "  1  写盘失败 / 写后复核不过 / 干跑动了文件\n"
            "  2  用不了（打不开包 / 没有这个 pathID / 它不是 AnimationManager / 参数不对）\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_par.add_argument("--bundle", required=True, help="目标 UnityFS 包（units 那种）")
    p_par.add_argument("--path-id", type=int, required=True,
                       help="那个 AnimationManager 的 pathID（挂在单位 prefab 上）")
    p_par.add_argument("--set", action="append", default=[], metavar="名字=值",
                       help="改一格（例 --set TracksSpeed=5.0），可重复给一次改一整族；"
                            "⛔ 不给就是纯读")
    p_par.add_argument("--from-json",
                       help='整族一次给：一份 {"名字": 值} 的 JSON 文件')
    p_par.add_argument("--write", action="store_true",
                       help="真写（⛔ 不给就只出计划，一个字节都不写）")
    p_par.add_argument(
        "--catalog",
        help="catalog.json / catalog.bin（给了就成对更新它的"
        " m_Crc / m_BundleSize）· ⚠ 它**不动** aa/catalog.hash"
        "（要两个文件一起管走 catalog crc）",
    )
    p_par.add_argument("--project-root",
                       help="工程目录（补丁柜落 <工程目录>/patches；默认当前目录）")
    p_par.add_argument("--game", help="游戏根目录（默认自动探测）")
    p_par.add_argument("--allow-running", action="store_true",
                       help="允许游戏在跑时写入（默认：游戏在跑就拒，一个字节都不写）")
    p_par.add_argument("--json", dest="json_out", action="store_true",
                       help="stdout 只写一份 JSON 文档")

    p_con = actions.add_parser(
        "container",
        help="导出 AnimationManager._container **整表**（只读，PPtr 解成骨骼/对象名）",
        epilog=(
            "为什么有它：`anim params` 只读 `_settings` 那 7 个 float；复刻游戏里的驱动效果",
            "（轮速/履带/悬挂/后坐）要的是**整张表**——`_suspension` 每根轴的 Bone+行程、",
            "`_wheels`/`_traks` 的 PPtr、`_acceleration` 的动画曲线。",
            "退出码：\n"
            "  0  读到了\n"
            "  2  用不了（打不开包 / 没有这个 pathID / 它不是 AnimationManager）\n",
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_con.add_argument("--bundle", required=True, help="目标 UnityFS 包（units 那种）")
    p_con.add_argument("--path-id", type=int, required=True,
                       help="那个 AnimationManager 的 pathID（挂在单位 prefab 上）")
    p_con.add_argument("--out", help="把整表写成 JSON 落盘（例：<工程目录>/anim_facts.json）")
    p_con.add_argument("--json", dest="json_out", action="store_true",
                       help="stdout 只写一份 JSON 文档")

    p_drv = actions.add_parser(
        "drive",
        help="把游戏驱动公式（轮/履带 UV/悬挂/摇摆）应用到一份 .blend，做成能播的演示",
        epilog=(
            "公式来源：`_work/isil/IsilDump` 的 BoneContainer.Update ->"
            " Wheels/Suspension/Tracks/ShakeBody"
            "（四条公式与常量已挖定，见 STATUS 2026-10-20 08:1x）。\n"
            "输出默认是 `<输入>_drive.blend`，⛔ 不覆盖原件。\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_drv.add_argument("--blend", required=True, help="输入 .blend（提取器出的 model.blend）")
    p_drv.add_argument("--speed", type=float, default=0.25, help="归一化车速（默认 0.25）")
    p_drv.add_argument("--turn", type=float, default=0.35, help="归一化转向（默认 0.35）")
    p_drv.add_argument("--frames", type=int, default=60, help="帧数（默认 60）")
    p_drv.add_argument("--exaggerate", type=float, default=1.0, metavar="N",
                       help="演示放大系数（1.0 = 游戏口径；履带 UV 与车体摇摆太小，建议 20）")
    p_drv.add_argument("--hide", action="append", default=[], metavar="对象名",
                       help="演示里藏起来的网格（例 --hide T72BA，侧裙挡轮子时用），可重复")
    p_drv.add_argument("--out", help="输出 .blend（默认 <输入>_drive.blend）")

    return parser


def _cmd_attrs_import(args: argparse.Namespace) -> int:
    """真件 prefab -> props.json（纯 Python，不起 Blender）。"""
    from ba_mod_maker.core.assets import anim_pack

    info = anim_pack.import_props(args.prefab, args.out)
    print(f"ATTRS_IMPORT props={info['props']} out={info['out']}")
    for state, n in sorted(info['states'].items()):
        print(f'  {state:<16} {n}')
    return 0


def _cmd_attrs_apply(args: argparse.Namespace) -> int:
    """props.json -> .blend 里的物体自定义属性（起 Blender）。"""
    from ba_mod_maker.core.assets import anim_pack

    info = anim_pack.apply_props(args.blend, args.props)
    print('ATTRS_APPLY ' + (info.get('readout') or 'done'))
    return 0


def _cmd_attrs_export(args: argparse.Namespace) -> int:
    """`.blend` -> model.fbx + model.glb + anim_config.json（起 Blender）。"""
    from ba_mod_maker.core.assets import anim_pack

    info = anim_pack.export_from_blend(args.blend, args.out)
    print(f"ATTRS_EXPORT fbx={info['fbx']} glb={info['glb']} "
          f"config={info['anim_config'] or '(无)'} behaviours={info['behaviours']}")
    return 0


def run(args: argparse.Namespace) -> int:
    """`anim` 的总入口（`cli/__main__.py` 的 handlers 表里那一行）。

    ⛔ 不设 `set_defaults(func=…)`：`__main__.py` 是按 `args.command` 查表的，
    与 validate / find / names 那批**同一个形状**（两套派发会漂移）。
    """
    action = getattr(args, "anim_action", None)
    handlers = {"list": _cmd_list, "spec": _cmd_spec, "stretch": _cmd_stretch,
                "apply": _cmd_apply, "verify": _cmd_verify, "params": _cmd_params,
                "container": _cmd_container, "drive": _cmd_drive,
                "attrs-import": _cmd_attrs_import, "attrs-apply": _cmd_attrs_apply,
                "attrs-export": _cmd_attrs_export}
    handler = handlers.get(str(action or ""))
    if handler is None:
        print("错误：anim 要给一个动作：" + " / ".join(handlers), flush=True)
        return EXIT_UNREADABLE
    try:
        return handler(args)
    except AssetError as exc:
        print(f"错误：{exc}", flush=True)
        return EXIT_UNREADABLE
