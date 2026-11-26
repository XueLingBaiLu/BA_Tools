"""tools/vfx_versions.py 的回归锁 —— 正控必须绿、负控必须红，两条缺一不可。

由来（2026-10-12 · F2 线 task-3/task-4）：
「打进包的 VisualEffectAsset 是 runtime 0 / compile 0 / systems 0」的根因**不是版本号**，
也**不是**没调 `VFXGraphCompiledData.Compile(VFXCompilationMode.Runtime, false, false)` ——
而是**跑这个工程的 Unity 处在 Built-in 渲染管线**：

    VFXLibrary.currentSRPBinder == null   （只有 HDRP / URP 的包会注册 binder）
      ⇒ Compile() 第一行就  CleanRuntimeData(); return;   （源码 VFXGraphCompiledData.cs:1026-1031）
      ⇒ 资产永远 0/0/0，而且**不报错**（静默；实测显式反射调 Compile 只花 1 ms，因为已经编过）

三条夹具（2026-10-16 起）：
  · **T5_hdrp_u6** 35,517 B —— **现行契约**：Unity 6000.3.24f1 + HDRP 17.3.0
    → **39 / 7 / systems 2**（出自 `_work/l4/out/a2_hdrp/t2_hdrp`，L4 / task-11 的实测产物）
  · T4_hdrp   41,087 B —— **上一代契约**：Unity 2022.3 + HDRP 14，挂 HDRP → 35 / 7 / systems 2
    ⇒ 契约改成 39 之后它**必须判红**（挡「拿 2022.3 编的 35 包混进 1.2.1」）
  · T3_builtin 8,536 B —— 同一工程把 GraphicsSettings.defaultRenderPipeline 与
    QualitySettings.renderPipeline 置 null（binder = null）→ 0 / 0 / 0

⇒ T5 必须判 OK；T3 与 T4 必须判 FAIL。少了任何一条，这把尺子就退化成恒绿或恒红 ——
而「恒绿的尺子」正是让它下个会话又跑去调 Compile 的原因。

⚠ 夹具合计约 51 KB（两个小包 + 两个 .manifest）。⛔ 不要把 `_work/vfxline/proj/`
（unity-hdrp 的整份拷贝，3.2 GB / 4.6 万文件）拷进来。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import vfx_versions as vv  # noqa: E402  （仓内 tools/ 是脚本不是包，只能改 sys.path）

FIX = ROOT / "tests" / "fixtures" / "vfx"
#: 正控 = **现行契约**（1.2.1 / Unity 6000.3.24f1）：39 / 7 / systems 2
HDRP = FIX / "T5_hdrp_u6"
#: 负控① = 上一代契约（2022.3 + HDRP 14）：35 / 7 —— 契约改 39 之后**必须红**
OLD35 = FIX / "T4_hdrp"
#: 负控② = 同工程关掉 SRP 打的：0 / 0 / 0
BUILTIN = FIX / "T3_builtin"
MAX_FIXTURE_BYTES = 200_000   # 防有人把 3.2 GB 的工程拷进夹具目录


def test_positive_control_hdrp_bundle_is_ok() -> None:
    """正控：挂 HDRP（Unity 6 / HDRP 17.3）打出来的包必须判 OK，且真的是 39 / 7 / systems>0。"""
    assert HDRP.is_file(), f"夹具丢了: {HDRP}"
    assert HDRP.stat().st_size < MAX_FIXTURE_BYTES, "夹具被换成了大包"

    rows = vv.read_assets(str(HDRP))
    ok, problems, _notes = vv.judge(rows)

    assert len(rows) == 1, f"这个包里应该只有一个 VisualEffectAsset，实读 {len(rows)}"
    assert ok, f"正控红了（尺子读不出真值）: {problems}"
    _pid, name, runtime, compile_, _exposed, systems, exprs = rows[0]
    assert (runtime, compile_) == (39, 7), f"{name}: 版本契约不对 {runtime}/{compile_}"
    assert systems > 0, f"{name}: systems={systems} —— 图没编出来"
    assert exprs > 0, f"{name}: exprs={exprs} —— 只有空壳没有表达式"


def test_negative_control_builtin_bundle_is_red() -> None:
    """负控：同一工程**关掉 SRP**打出来的包必须判 FAIL —— 尺子要能红。"""
    assert BUILTIN.is_file(), f"夹具丢了: {BUILTIN}"
    assert BUILTIN.stat().st_size < MAX_FIXTURE_BYTES, "夹具被换成了大包"

    rows = vv.read_assets(str(BUILTIN))
    ok, problems, _notes = vv.judge(rows)

    assert not ok, "负控没红 —— 这把尺子测不了「图没编出来」"
    joined = " ".join(problems)
    assert "runtime=0" in joined, f"负控没点名 runtime: {problems}"
    assert "systems" in joined, f"负控没点名 systems: {problems}"


def test_old_contract_bundle_is_red() -> None:
    """**契约负控**：上一代（35/7）的包在 39/7 这把尺子下必须红。

    这条是「35 只对上一代副本成立」的机械自证 —— ⛔ 别删，删了就没东西挡
    「拿 2022.3 编出来的 35 包混进 1.2.1」。
    """
    assert OLD35.is_file(), f"夹具丢了: {OLD35}"
    rows = vv.read_assets(str(OLD35))
    ok, problems, _notes = vv.judge(rows)
    assert not ok, "旧契约的包没红 —— 这把尺子认不出 35 与 39 的区别"
    assert any("runtime=35" in p for p in problems), f"没点名 35: {problems}"


def test_cli_exit_code_follows_the_verdict(capsys) -> None:
    """命令行那一层的判据：OK ⇒ exit 0；FAIL ⇒ exit 1（Lead 就是看这个 rc）。"""
    assert vv.main([str(HDRP)]) == 0
    assert vv.main([str(BUILTIN)]) == 1
    assert vv.main([str(OLD35)]) == 1
    out = capsys.readouterr().out
    assert "VERDICT: OK" in out and "VERDICT: FAIL" in out
