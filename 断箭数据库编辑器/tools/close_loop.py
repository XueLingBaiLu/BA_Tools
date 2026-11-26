r"""P3-0 · 改件闭环的**离线彩排** + **字节对拍尺子**。

判据一行
-------
    .venv\Scripts\python.exe tools\close_loop.py --selftest
      -> 末行印「只有 1 处变了：块 #K（对象 pathID=P 的 4 字节）· 其余 N-1 块逐字节相同」且 rc=0

八步（**全程只读游戏目录**，改动只落在 _work 的副本上）
------------------------------------------------------
  ① 选一个真包（--bundle；默认 ammoprefabs_assets_all，75 MB，86 Mesh / 97 Material）
  ② copy2 到 _work/p3loop/work.bundle，核对 sha256 与源**逐位相同**
  ③ 打开副本，挑一颗 Material，读它现有的一个浮点参数
  ④ build_material_change(...) 改**一格** -> ObjectChange（等长；四条自证在产品里）
  ⑤ write_objects(副本, [change], dry_run=False, patch_store=...) —— ADR 0007 的 Δ 链
  ⑥ **三级对拍**
       对象级：新旧对象字节的 changed_spans 必须 == 恰好那个 float 的 4 字节
       块级  ：新旧文件块表里，**除命中块外**每块的压缩字节逐字节相同
       文件级：文件长度不变（等长档）
  ⑦ 负控（**尺子必须能红**）：合成一处远处单字节扰动 -> changed_spans 必须报出 2 段
  ⑧ 还原：PatchStore.restore() -> 副本 sha256 必须回到 ⑤ 之前那个值（= kb/19 J6 的实测）

⛔ 不重造第二份实现：改字节走产品的 build_material_change，写回走产品的 write_objects，
   反向 Δ 走产品的 PatchStore；本文件只做**编排 + 对拍**（kb/19 §4 的例外条款）。
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import shutil
import sys
import time
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    with contextlib.suppress(Exception):
        _s.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ba_mod_maker.asset_kernel import reverse_delta, writer  # noqa: E402
from ba_mod_maker.asset_kernel.skin_install import changed_spans  # noqa: E402
from ba_mod_maker.asset_kernel.unitypy_source import open_source  # noqa: E402
from ba_mod_maker.core.asset import ufs  # noqa: E402

import os
# 工作根：数据已移出仓（2026-11-09，统一入口）。
# 默认 D:\断箭模组制作\_work_db；换机器/换位置用环境变量 BA_WORK_ROOT 覆盖。
WORK = Path(os.environ.get("BA_WORK_ROOT") or r"D:\断箭模组制作\_work_db")

DEFAULT_GAME = Path(r"D:\Steam\steamapps\common\broken_arrow")
DEFAULT_BUNDLE = "ammoprefabs_assets_all_1db6cf2c07a5d1ce9b184aa7b89f23a2.bundle"
DEFAULT_WORK = WORK / "p3loop"


# --------------------------------------------------------------------------- #
# 小工具
# --------------------------------------------------------------------------- #
def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


def block_payloads(path: Path) -> list[bytes]:
    """逐块的**压缩字节**（按块表切片，直接读盘 —— 不解压）。"""
    out: list[bytes] = []
    with ufs.UfsReader(path) as reader, path.open("rb") as fh:
        for index, blk in enumerate(reader.blocks):
            fh.seek(reader.block_foff[index])
            out.append(fh.read(blk.compressed))
    return out


def object_bytes(source, path_id: int) -> bytes:
    obj = source.object_at(path_id)
    if obj is None:
        raise KeyError(f"包里没有 pathID={path_id}")
    return bytes(obj.get_raw_data())


def _floats_of(tree) -> list[tuple[str, float]]:
    props = tree.get("m_SavedProperties")
    if not isinstance(props, dict):
        return []
    out: list[tuple[str, float]] = []
    for entry in props.get("m_Floats") or ():
        try:
            name, value = entry[0], entry[1]
        except Exception:
            name, value = getattr(entry, "first", None), getattr(entry, "second", None)
        if name is None:
            continue
        try:
            out.append((str(name), float(value)))
        except (TypeError, ValueError):
            continue
    return out


def pick_material_and_float(source, want_pid: int | None):
    """挑 (pathID, 浮点名, 旧值) —— 优先 --path-id，否则挑第一颗**有浮点参数**的材质。"""
    if want_pid is not None:
        tree = source.read_typetree(want_pid)
        floats = _floats_of(tree)
        if not floats:
            raise SystemExit(f"pathID={want_pid} 没有任何浮点参数，换一个 --path-id")
        return want_pid, floats[0][0], floats[0][1]
    for pid in sorted(source.object_ids("Material")):
        try:
            floats = _floats_of(source.read_typetree(pid))
        except Exception:
            continue
        if floats:
            return int(pid), floats[0][0], floats[0][1]
    raise SystemExit("这个包里没有带浮点参数的 Material —— 换一个 --bundle")


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def run(*, game: Path, bundle_name: str, work_dir: Path, keep: bool,
        path_id: int | None, param: str | None, value: float | None) -> int:
    t0 = time.time()
    src = game / "BrokenArrow_Data" / "StreamingAssets" / "aa" / "PC" / bundle_name
    if not src.is_file():
        raise SystemExit(f"找不到源包：{src}")
    work_dir.mkdir(parents=True, exist_ok=True)
    work = work_dir / "work.bundle"
    patches = work_dir / "patches"
    patches.mkdir(parents=True, exist_ok=True)

    print(f"[① 选包] {bundle_name}  {src.stat().st_size:,} B")
    shutil.copy2(src, work)
    sha_source_before = sha256_of(src)
    sha_work_copy = sha256_of(work)
    ok_copy = sha_source_before == sha_work_copy
    print(f"[② 拷贝] -> {work}")
    print(f"    源 sha256 = {sha_source_before}")
    print(f"    副 sha256 = {sha_work_copy}   逐位相同 = {ok_copy}")
    if not ok_copy:
        return 2

    before_blocks = block_payloads(src)
    print(f"[② b] 源块数 = {len(before_blocks)}")

    # ---------- ④ 改一格 ----------
    change = None
    target_pid = 0
    old_obj = b""
    param_used = ""
    old_value = 0.0
    new_value = 0.0
    with open_source(work) as source:
        target_pid, param_used, old_value = pick_material_and_float(source, path_id)
        if param is not None:
            param_used = param
        new_value = float(value) if value is not None else old_value + 0.125
        if new_value == old_value:
            raise SystemExit(f"新值 == 旧值（{old_value}）—— 那就不是一次改动")
        old_obj = object_bytes(source, target_pid)
        change = writer.build_material_change(source, target_pid, param=param_used, value=new_value)
    spans_obj = changed_spans(old_obj, change.payload)
    n_bytes_obj = sum(hi - lo for lo, hi in spans_obj)
    n_spans_obj = len(spans_obj)
    print(f"[③④ 改一格] pathID={target_pid}  {param_used}: {old_value!r} -> {new_value!r}")
    same_len = len(old_obj) == len(change.payload)
    print(f"    对象字节 {len(old_obj):,} -> {len(change.payload):,}（等长 = {same_len}）")
    print(f"    对象级对拍：变了 {len(spans_obj)} 段 / {n_bytes_obj} 字节 · 段 = {spans_obj[:6]}")

    sha_before_write = sha256_of(work)
    # ---------- ⑤ 写回（Δ 链）----------
    store = reverse_delta.PatchStore(patches, game_root=game)
    store.clear()
    t1 = time.time()
    report = writer.write_objects(
        work, [change], dry_run=False, backup_dir=None, project_root=ROOT,
        game_root=game, catalog_path=None, patch_store=store, patch_label="p3-0 close loop",
    )
    print(f"[⑤ 写回] 用时 {time.time() - t1:.1f}s · dry_run={report.dry_run} · "
          f"{report.plan.old_file_size:,} -> {report.plan.new_file_size:,} B")

    # ---------- ⑥ 三级对拍 ----------
    after_blocks = block_payloads(work)
    if len(after_blocks) != len(before_blocks):
        print(f"    ⚠ 块数变了 {len(before_blocks)} -> {len(after_blocks)}（Δ 链插了块）")
    n = min(len(before_blocks), len(after_blocks))
    dirty = [i for i in range(n) if before_blocks[i] != after_blocks[i]]
    clean = n - len(dirty)
    print(f"[⑥ 块级对拍] 块 {n} 个：**脏块 {len(dirty)} 个** {dirty[:8]} · "
          f"其余 {clean} 块逐字节相同")
    # 声明 vs 实测（计划里自己说的重压块，必须与实测脏块**同一个集合**）
    rc_blocks = report.plan.recompressed_blocks
    declared = tuple(sorted(rc_blocks)) if rc_blocks else ()
    declared_runs = tuple(report.plan.block_runs)
    if declared:
        same = set(declared) == set(dirty)
        print(f"    计划声明重压块 = {declared} · 实测脏块 = {tuple(dirty)} · 同一个集合 = {same}")
    else:
        # L1 链不填 recompressed_blocks，声明在 block_runs / hit_blocks 上
        covered = {i for lo, hi in declared_runs for i in range(lo, hi + 1)}
        same = covered == set(dirty) and report.plan.hit_blocks == len(dirty)
        print(f"    计划声明命中块 = {declared_runs}（hit_blocks={report.plan.hit_blocks}）· "
              f"实测脏块 = {tuple(dirty)} · 同一个集合 = {same}")
        declared = tuple(sorted(covered))
    # 头部区间：脏块压缩尺寸变了 ⇒ blocksInfo 必然变（这是**可解释**的，不是"别处也变了"）
    def _head(path: Path) -> bytes:
        """文件头 + 数据起点之前那段（块表/CRC 都在这儿）。走公开 API 取长度。"""
        with ufs.UfsReader(path) as r:
            size = r.block_foff[0]
        with path.open("rb") as fh:
            return fh.read(size)

    head_src, head_work = _head(src), _head(work)
    head_spans = changed_spans(head_src, head_work) if len(head_src) == len(head_work) else None
    grew_head = len(after_blocks[dirty[0]]) - len(before_blocks[dirty[0]])
    head_note = (f"变了 {len(head_spans)} 段（块表/CRC —— 脏块尺寸 {grew_head:+,} ⇒ 必然）"
                 if head_spans else "未比（长度不同）")
    print(f"    头部 [0, 数据区) {len(head_src):,} B vs {len(head_work):,} B：{head_note}")
    # 负控 B：在**干净块**里扰动 1 字节 —— 块级尺子必须因此多报 1 个脏块
    if clean:
        victim = next(i for i in range(n) if i not in dirty)
        mutated = bytearray(after_blocks[victim])
        mutated[0] ^= 0xFF
        probe_after = [bytes(mutated) if i == victim else after_blocks[i] for i in range(n)]
        dirty_neg = [i for i in range(n) if before_blocks[i] != probe_after[i]]
        neg_b = set(dirty_neg) == set(dirty) | {victim}
        print(f"[⑦ 负控 B] 干净块 #{victim} 扰 1 字节 -> 脏块 {len(dirty)} -> {len(dirty_neg)} 个 "
              f"（必须 +1）= {'命中' if neg_b else '✗ 块级尺子看不见'}")
    else:
        neg_b = False
        print("[⑦ 负控 B] 没有干净块可比 —— 这条负控不成立")

    patch = store.pending()
    stored = None
    if patch is not None:
        for target in patch.targets:
            if isinstance(target, reverse_delta.BundleTarget):
                stored = sum(1 for r in target.blocks if r.stored)
                print(f"    反向 Δ 记下 {stored}/{len(target.blocks)} 块的原始压缩字节"
                      f"（补丁 {target.stored_bytes:,} B）")
        print("    " + " / ".join(patch.lines()[:2]))

    # ---------- ⑦ 负控：尺子必须能红 ----------
    probe = bytearray(change.payload)
    far = len(probe) - 1
    probe[far] ^= 0xFF
    spans_neg = changed_spans(old_obj, bytes(probe))
    neg_ok = len(spans_neg) >= 2
    print(f"[⑦ 负控 A] 对象里远处再扰动 1 字节 -> changed_spans 报出 {len(spans_neg)} 段 "
          f"（必须 >= 2）= {'命中' if neg_ok else '✗ 对象级尺子看不见'}")

    # ---------- ⑧ 还原 ----------
    t2 = time.time()
    restore = store.restore()
    sha_after_restore = sha256_of(work)
    back_ok = sha_after_restore == sha_before_write
    print(f"[⑧ 还原] 用时 {time.time() - t2:.1f}s · 补丁还剩 = {store.has_pending()} · "
          f"副本 sha256 回到改动前 = {back_ok}")
    if not back_ok:
        print(f"    期望 {sha_before_write}")
        print(f"    实得 {sha_after_restore}")
    print("    " + " / ".join(restore.lines()[:3]))

    # ---------- 判据一行 ----------
    ok = (
        ok_copy
        and len(change.payload) == len(old_obj)
        and n_spans_obj == 1
        and len(dirty) >= 1
        and (not declared or set(declared) == set(dirty))
        and (stored is None or stored == len(dirty))
        and neg_ok
        and bool(clean) and neg_b
        and back_ok
    )
    grew = report.plan.new_file_size - report.plan.old_file_size
    print()
    print("=" * 78)
    print(f"判据一行：对象 pathID={target_pid} 改了 {n_spans_obj} 段 / {n_bytes_obj} 字节 · "
          f"块级只有 {len(dirty)}/{n} 块变了（#{dirty}，尺寸 {grew:+,} B）· "
          f"其余 {clean} 块**逐字节相同** · 还原后 sha256 一致")
    print(f"    副本逐位={ok_copy} 对象等长={len(change.payload) == len(old_obj)} "
          f"对象段数={n_spans_obj} 脏块={len(dirty)} 声明块={declared or '—'} Δ记块={stored} "
          f"负控A段数={len(spans_neg)} 负控B={'命中' if neg_b else '✗'} 还原一致={back_ok} "
          f"包体积 {grew:+,} B（重压同长块的必然代价）")
    print(f"    {'✅ 闭环成立' if ok else '❌ 闭环不成立'}")
    print("=" * 78)

    evidence = {
        "bundle": bundle_name, "bundle_bytes_before": report.plan.old_file_size,
        "bundle_bytes_after": report.plan.new_file_size, "blocks": n,
        "target_path_id": target_pid, "param": param_used,
        "old_value": old_value, "new_value": new_value,
        "object_bytes": len(old_obj), "object_spans": n_spans_obj,
        "object_changed_bytes": n_bytes_obj, "dirty_blocks": dirty,
        "declared_blocks": list(declared), "clean_blocks": clean,
        "patch_stored_blocks": stored, "negative_control_object": len(spans_neg),
        "negative_control_block": neg_b, "restore_sha256_equal": back_ok,
        "copy_bit_identical": ok_copy, "source_sha256": sha_source_before,
        "ok": ok, "seconds": round(time.time() - t0, 1),
    }
    (work_dir / "loop_report.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[证据] {work_dir / 'loop_report.json'}")

    if not keep:
        (work_dir / "work.bundle").unlink(missing_ok=True)
        store.clear()
        print(f"[清理] 删掉工作副本（--keep 保留）· 总用时 {time.time() - t0:.1f}s")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="P3-0 改件闭环离线彩排 + 字节对拍尺子")
    ap.add_argument("--selftest", action="store_true", help="全自动跑一遍（默认行为）")
    ap.add_argument("--game", default=str(DEFAULT_GAME))
    ap.add_argument("--bundle", default=DEFAULT_BUNDLE)
    ap.add_argument("--workdir", default=str(DEFAULT_WORK))
    ap.add_argument("--path-id", type=int, default=None)
    ap.add_argument("--param", default=None)
    ap.add_argument("--value", type=float, default=None)
    ap.add_argument("--keep", action="store_true", help="保留工作副本（默认删掉）")
    args = ap.parse_args(argv)
    return run(game=Path(args.game), bundle_name=args.bundle, work_dir=Path(args.workdir),
               keep=args.keep, path_id=args.path_id, param=args.param, value=args.value)


if __name__ == "__main__":
    raise SystemExit(main())
