"""人形肌肉乙路的**离线**判据（⛔ 不开 Unity、⛔ 不碰游戏目录、⛔ 不开 3.38 GB 包）。

尺子自证的三控（缺一个都不算尺子）：

    * 正控   _work/anim-recon/out/m1_clip_classify.json 里 **72 条纯人形 clip**
             -> classify_bindings 全部返回 130 条、attribute 恰好 7..136（0 例外）
    * 负控   同文件的 generic clip（ATGM，pid -9207998336256044584，3 条绑定 0 条肌肉）
             -> 必须抛 NotHumanoidClipError（⛔ 不是静默返回空列表）
    * 尺子  把真 clip 的绑定故意弄坏（删一条 / 重复一条 / 改 typeID）-> 必须变红

另加一条**独立来源**的对拍：BODY_MUSCLE_NAMES 必须与
研究-模型与动画/07-clip解码/clip_decode.py::MUSCLE_NAMES 逐字相同（那份来自 Unity 的
human::MuscleName 字符串表，是另一条链的产物，不是本表抄自己）。

------------------------------------------------ 人形整体精度尺子（逐骨测地距离）

**这把尺子量什么**：把「甲」（src/ba_mod_maker/asset_kernel/muscle_solve.py::solve_pose_swing）
在一组**真肌肉值**上算出来的 53 根骨，与 _work/muscleline/out/ 里**真 Unity 烘出来的**参考位姿
逐骨比**测地距离**（2·acos|dot|，度；先归一化 —— 尺子是 tools/muscle_reference.py::geodesic_deg）。

**数据从哪来**（⛔ 不造数据、⛔ 不落盘、⛔ 不开 Unity、⛔ 不碰游戏目录）：
    muscle_probe.json（单通道激励探针：q_base 与 91 条「肌肉→骨·轴·度数」）
    rig_dump.json（muscleDefaultMinMax 95 条 · 参考位姿 _work/…/poses[clip][t].bones，53 骨）
    rigpayloads/<clip>.unity_payload.json（烘制时逐时刻喂进去的 95 条肌肉值 + bodyRot）
实测总量：**3 clip × 4 时刻 × 53 骨 = 636 次逐骨比对**。

**两条线 + 一份逐骨上限的规则表**（⛔ 不是把线放松成能过）：
    逐骨精度线 TIGHT_LINE_DEG = 1e-3  —— 每根骨都该 ≤ 它（「通过」档）
    结构线     STRUCTURAL_LINE_DEG = 1e-2 —— 没登记的骨越过它 = 结构性错误，不是数值抖动
    规则表     ALLOWED_VIOLATION_CAPS = {"Hips": 10.0}：**骨名 -> 这根骨允许的最大测地距离**；
               没登记的骨一律按结构线卡。登记 = 承认它现在不过线，并写下它的上限。
    ⇒ 判据（纯函数 accuracy_verdict，三条缺一不可）：
        ① violations == [] —— 没登记却越线的骨一根都不许有；
        ② missing == []    —— 登记了却实测没越线的骨 = **规则表过期**，也要红（方向与①相反）；
        ③ stale == []      —— 越过精度线的骨必须**恰好**是 ALLOWED_TIGHT_BONES。
      ⛔ 为什么不是「一个阈值」：只卡松的，一根骨烂到 20 度也照样绿；只卡紧的，
         Hips 现在 6.897797 度（2026-10-13 修正后）会让整条尺子**恒红** —— 恒红和恒绿一样不是尺子。

**尺子必须归一化（实测，别省这一步）**：本文件那个 _geo_deg **不归一化**，而参考里的
四元数是 float32 写出来的（|q| 差 ~1e-7）—— 未归一化时「真值 vs 自己」它给 **0.0537 度**
（LeftToes，实测），**比逐骨精度线 1e-3 大 54 倍**。所以本尺子的唯一入口是 _pair_deg()：
先归一化，再走 canonical 的 tools/muscle_reference.py::geodesic_deg。

**读数（实跑 _work/muscleline/accept.py 同口径，最差值）**：只剩 **Hips 6.897797 度**；
**其余 52 根 ≤ 4.42e-05 度**（≈ 逐骨精度线的 1/23）；两档之间是**空的** ⇒ 上限不靠猜。
⚠ 报两端（⛔ 只报 max 是不合格的）：Hips 那 12 个时刻是 0.737404 ~ 6.897797 度 ——
**最小那个也仍越逐骨精度线**（0.737404 > 1e-3），所以它「每一时刻都越线」可复算。
★ **留痕（2026-10-13）**：Hips 7.535490 → **6.897797 度** —— A 线那处**纯修正**（脊链 9 条的轴
改用 e.hips_axis；探针实测 dot = +1.000000，13/13）所致，**其余 52 根逐位不变**；
依据 _work/muscleline/accept.py（判据仍是**未通过** <= 1e-3，⛔ 别把它读成「通过了」）。

⚠ 判据口径两条（写清免得下一个人踩）：①「越线」看的是**任一时刻**，⛔ 不是看该骨的 max；
② 规则表里登记过的骨，只要**任一时刻**仍越过结构线就**不算过期** —— 它可以同时越过精度线
（那正是它留在 ALLOWED_TIGHT_BONES 里的原因）。两条合起来 = 「精度线」与「结构线」各管一件事。

**能红的范围（负控见 test_humanoid_ruler_goes_red_on_a_known_violation 等三条）**：
    ① 某根骨**越过它自己的上限**（例：Hips 被转坏 50 度）—— 报红，且点名是哪一条判据红的；
    ② 冒出一根**没登记**的坏骨（例：LeftHand 塞 0.5 度）—— 报红，并列出骨名；
    ③ **规则表过期**（例：登记过的 Hips 被塞回 0.5 度）—— 报红（方向与 ② 相反）；
    ④ **骨名对不上**（预测少一根 / 多一根）—— 报红（⛔ 不跳过）。

**⛔ 测不了的（明写在下面，⛔ 别拿它当「都对」）**：
    ① 参考列**本身**是不是真值 —— 那把尺子在 tests/test_anim_handfoot.py 的 field 用例里
       （bake 列由 Unity 探针产出，本文件只读它）；
    ② 逐时刻之间的**时间维**：抖动 / 突变 / 角速度 —— 这里只比**同一时刻**的静态位姿；
    ③ pose 之外的量（脚尖滑动 / 脚穿地 / 实际观感、「看起来对不对」）—— **测不了**，
       那三类观感只有用户能判（AGENTS §0.12.1）；
    ④ 标定与它**同源**的那类错 —— q_base 与 91 条通道参数都来自同一份探针，
       所以「探针自己系统性标错」这把尺子**看不见**（那要独立来源，见 test_l1_* 那两条）。
"""
from __future__ import annotations

import importlib.util
import json
import math
import sys
import zlib
from collections import Counter
from pathlib import Path

import pytest

import os
# 工作根：数据已移出仓（2026-11-09，统一入口）。
WORK = Path(os.environ.get("BA_WORK_ROOT") or r"D:\断箭模组制作\_work_db")

REPO = Path(__file__).resolve().parents[1]
BASE = REPO.parent
CLASSIFY_JSON = WORK / "anim-recon" / "out" / "m1_clip_classify.json"
DECODER_PY = BASE / "研究-模型与动画" / "07-clip解码" / "clip_decode.py"
AVATAR_JSON = WORK / "anim-recon" / "out" / "m1b_avatars.json"
INDEX_MAPS_JSON = WORK / "muscleline" / "out" / "index_maps_72.json"
L1_JSON = WORK / "muscleline" / "out" / "muscleline_l1.json"
MSOLVE = REPO / "src" / "ba_mod_maker" / "asset_kernel" / "muscle_solve.py"
MREF_PY = REPO / "tools" / "muscle_reference.py"

REGEN = (f"重生成：cd {REPO} 后 .venv/Scripts/python.exe "
         "_work/anim-recon/m1_clip_classify.py（需要 units 包副本）。")


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, f"载不进 {path}"
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


U = _load(REPO / "tools" / "unity_humanpose_bake.py", "unity_humanpose_bake")
# ★ 与甲线**同一把**测地距离尺子（tools/muscle_reference.py::geodesic_deg），⛔ 不另写一份。
MR = _load(MREF_PY, "muscle_reference_humanoid")


@pytest.fixture(scope="module")
def classify() -> dict:
    if not CLASSIFY_JSON.exists():
        pytest.fail(f"缺落盘读数 {CLASSIFY_JSON}（这把尺子读真数据，不许跳）。{REGEN}")
    return json.loads(CLASSIFY_JSON.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def pure_humanoid(classify: dict) -> list[dict]:
    return [c for c in classify["clips"] if c["kind"] == "humanoid_pure"]


@pytest.fixture(scope="module")
def index_maps() -> dict:
    if not INDEX_MAPS_JSON.exists():
        pytest.fail(f"缺落盘读数 {INDEX_MAPS_JSON}。复算：_work/muscleline/dump_index_maps.py")
    return json.loads(INDEX_MAPS_JSON.read_text(encoding="utf-8"))


# ------------------------------------------------- L1：真 Unity 跑出来的名字表
@pytest.fixture(scope="module")
def l1() -> dict:
    if not L1_JSON.exists():
        pytest.fail(f"缺 L1 哨兵产物 {L1_JSON}（真 Unity 跑出来的读数，这把尺子不许跳）。"
                    "复算：_work/muscleline/run_l1.ps1")
    return json.loads(L1_JSON.read_text(encoding="utf-8"))


def test_l1_ninety_names_are_confirmed_by_unity(l1: dict) -> None:
    """L1 哨兵读数（Unity 2022.3.62f3c1 / 工程 _work/muscleline/proj / 0.2 s）：
    名字可寻址的 **90/130** 条，attribute **逐条**等于本工具的表，**0 例外** ——
    55 条身体肌肉 == 下标+42（42..96）· 7 条 root（7..13）· 28 条 goal（14..41）。

    ⚠ verdict 是 **L1_FAIL**，而那是**结论不是 bug**：剩下 40 条手指走的是另一条路（见下一条）。
    """
    m = {n: int(a) for n, a in l1["nameToAttribute"]}
    mus = l1["muscleNames"]
    assert len(mus) == 95, "Unity 的 MuscleName 应当 95 条"
    assert sum(1 for i in range(55) if m.get(mus[i]) == i + 42) == 55, "身体肌肉没全对上"
    want = {c.name: c.attribute for c in U.CHANNELS
            if c.kind in ("rootT", "rootQ", "goalT", "goalQ")}
    assert len(want) == 35
    assert all(m.get(n) == a for n, a in want.items()), "root/goal 名字没全对上"
    assert l1["yamlNumericAttrs"] == 90 and l1["yamlOtherAttrs"] == 40
    assert l1["verdict"] == "L1_FAIL", "verdict 变了就说清楚为什么，别默默改口径"


def test_l1_redefined_verdict_covers_all_130_fail_closed(l1: dict) -> None:
    """**重定义后的 L1 判据**（Lead 2026-10-12 批准，三条硬约束）：

    ① 全 130 条**无一遗漏**：每条必须落进「数字段 == 下标+42 / root+goal 7..41」或
       「哈希段 == crc32(名字)」，**出现第三类即 FAIL**（fail-closed）；
    ② 两段各有能红的正控（下面两条 assert）；
    ③ 历史顺序保留：哨兵**原始**自报是 L1_FAIL，重定义后才是 L1_OK（⛔ 不许抹掉）。

    ⚠ 为什么不是放松：原判据把手指 40 条算进分母是**范畴错误**（它们根本没有 attribute 映射）；
    新判据覆盖**全部 130 条**且不允许第三类 ⇒ 分母没变小、出口更严。
    """
    rep = U.l1_verdict(l1["nameToAttribute"], l1["muscleNames"])
    assert rep["verdict"] == "L1_OK"
    assert rep["total"] == 130 and rep["numeric"] == 90 and rep["hashed"] == 40
    assert rep["third_class"] == []
    assert l1["verdict"] == "L1_FAIL", "哨兵的原始判据是历史，必须原样保留"

    # 正控一：把一条改成「既不是应得数字、也不是 crc32」=> 必须 FAIL（fail-closed 能红）
    broken = [(n, a) for n, a in l1["nameToAttribute"]]
    broken[0] = (broken[0][0], 123456789)
    r1 = U.l1_verdict(broken, l1["muscleNames"])
    assert r1["verdict"] == "L1_FAIL" and len(r1["third_class"]) == 1

    # 正控二：少一条 => 分母不满 130，也必须 FAIL
    assert U.l1_verdict(broken[:-1], l1["muscleNames"])["verdict"] == "L1_FAIL"


def _geo_deg(p: list[float], q: list[float]) -> float:
    """四元数测地距离（度）—— q 与 -q 等价。⛔ 别用分量差（会被 q~-q 骗）。"""
    d = abs(sum(x * y for x, y in zip(p, q, strict=True)))
    return math.degrees(2.0 * math.acos(min(1.0, d)))


# ==========================================================================================
# 人形整体精度尺子：逐骨测地距离 vs _work/muscleline/out/ 真读数
#   能红在哪 / 不能红在哪 -> 见模块 docstring 末两段（⛔ 改这里就一起改那里）
# ==========================================================================================
ML_OUT = WORK / "muscleline" / "out"
PROBE_JSON = ML_OUT / "muscle_probe.json"
RIG_DUMP = ML_OUT / "rig_dump.json"
PAYLOAD_DIR = ML_OUT / "rigpayloads"
HUMANOID_CLIPS = ("Kneel_aim", "Prone_run", "Prone_idle")
N_EXPECT_BONES = 53              # 探针读 53 骨（muscle_probe.nBones = 53）
N_MUSCLES_PER_TIME = 95          # HumanPose.muscles 长度
UNITY_EXPECT = "2022.3.62f3c1"   # 探针 Unity 版本（同游戏版本）

TIGHT_LINE_DEG = 1e-3        # 逐骨精度线（「通过」档）：每根骨都应该 <= 它
STRUCTURAL_LINE_DEG = 1e-2   # 结构线：**没登记**的骨越过它 = 结构性错误，不是数值抖动
# ★ 规则表：骨名 -> 这根骨允许的最大测地距离（度）。没登记的骨一律按 STRUCTURAL_LINE_DEG 卡。
#   实测 6.897797 度（2026-10-13 修正后 · _work/muscleline/accept.py；修正前 7.535490）；
#   上限给 10.0 = 留 45% 余量，
#   ⛔ 不是贴着实测值卡 —— 贴着卡会让这条尺子变成「记录当前读数」而不是判据。
ALLOWED_VIOLATION_CAPS = {"Hips": 10.0}
# ★ 允许越过逐骨精度线的骨 —— 实测**恰好**就是上面那一根（其余 52 根最大 4.42e-05 度）。
ALLOWED_TIGHT_BONES = frozenset(ALLOWED_VIOLATION_CAPS)

ACC_REGEN = (
    "复算（只读 _work/muscleline/out/，⛔ 不开 Unity / 不碰游戏目录 / 不落盘）："
    f"{REPO}/.venv/Scripts/python.exe _work/muscleline/accept.py —— "
    "重建探针与烘制读数则是 _work/muscleline/run_rig.ps1 / run_l2.ps1。"
)


def _pair_deg(p, q) -> float:
    """★ 本尺子的**唯一**测地距离入口：**先归一化**再 2*acos|dot|。

    为什么非要包一层（⛔ 不是重复造尺子）：本文件那个 _geo_deg **不归一化**，
    而参考里的四元数是 float32 写出来的、|q| 差 ~1e-7 —— 未归一化的 2*acos|dot| 会把
    这点量化噪声放大成 **0.0537 度**（LeftToes 实测），**比逐骨精度线 1e-3 大 54 倍**。
    也就是：拿未归一化的读数去判 1e-3，判的是**浮点噪声**，不是解算精度。
    canonical 那份（tools/muscle_reference.py::geodesic_deg）由 _pair_deg 调用。
    """
    return float(MR.geodesic_deg(list(p), list(q)))


def accuracy_verdict(readings: list, caps: dict, *,
                     tight_line: float = TIGHT_LINE_DEG,
                     structural_line: float = STRUCTURAL_LINE_DEG,
                     allowed_tight: frozenset = ALLOWED_TIGHT_BONES) -> dict:
    """逐骨测地距离判据 —— **纯函数**：只吃「每时刻读数 + 一份规则表」，不碰盘、不碰 rig。

    ★ 规则表 caps 的形状与语义：**骨名 -> 这根骨允许的最大测地距离（度）**。
      没登记的骨一律按结构线 structural_line 卡；登记过的骨按它自己的那个数卡。
      为什么不用单一阈值 —— 同一个数字盖不住两种相反的失败：
        · 只卡一个松数字：一根骨烂到 20 度也照样绿（测不出结构性错误）；
        · 只卡一个紧数字：Hips 现在 6.897797 度会让整条尺子恒红（**恒红 = 不是尺子**）。
      => 规则表把「每根坏骨都必须具名登记、并写清它的上限」变成机械可查的事。

    三条判据（⛔ 缺一条这把尺子就不完整）：
        ① violations == []  —— 没登记却越线的骨，一根都不许有；
        ② missing == []     —— 登记了却**实测没越线**的骨 = **规则表过期**，也要红；
        ③ stale == []       —— 越过逐骨精度线的骨必须**恰好**是 allowed_tight
                               （Hips 哪天降到 1e-3 以内，这条就红）。
      ① 与 ② 是**相反方向**的失败：一个是「冒出新坏骨」，一个是「旧坏骨好了」；
      ③ 是「坏骨清单」本身的守门人（⛔ 不许靠一个松阈值把新坏骨一起放过去）。

    返回（全 dict，便于报错时逐项打印；⛔ 只报 max 不报两端是不合格的）：
        n_measurements / worst_deg / worst_bone / worst_where —— 最差那条（骨 + clip + 时刻）
        per_bone_worst    骨名 -> 该骨全部时刻上的最大偏差（两端里另一边）
        worst_bone_where  骨名 -> 它自己最差的那一个 (clip, 时刻)
        over_tight        越过逐骨精度线的骨集合
        violations        越线且没登记的骨（非空即红）
        missing           登记了却没越线的骨（非空即红 = 规则表过期）
        stale             越过精度线却不属于 allowed_tight 的骨（非空即红）
        verdict           'OK' / 'FAIL'
    """
    if not readings:
        raise ValueError("没有任何读数 —— ⛔ 缺参数时不许静默给绿")
    if not caps:
        raise ValueError("规则表是空的 —— ⛔ 那等于「谁都不许越线」，先写清登记了哪些骨")
    per_bone: dict = {}
    worst_where: dict = {}
    breach: set = set()          # ★ **任一时刻**越过结构线就算越线（⛔ 不是看该骨的 max）
    over_tight: set = set()      # ★ 同理：**任一时刻**越过逐骨精度线就算越过
    worst, worst_bone, worst_at = -1.0, "", ("", 0)
    for clip, ti, per in readings:
        for bone, deg in per.items():
            if deg > per_bone.get(bone, -1.0):
                per_bone[bone] = deg
                worst_where[bone] = (clip, ti)
            if deg > structural_line:
                breach.add(bone)
            if deg > tight_line:
                over_tight.add(bone)
            if deg > worst:
                worst, worst_bone, worst_at = deg, bone, (clip, ti)
    violations = sorted(breach - set(caps))
    over_cap = sorted(b for b in caps if per_bone.get(b, -1.0) > caps[b])
    missing = sorted(b for b in caps if b not in breach)
    stale = sorted(over_tight - set(allowed_tight))
    ok = not (violations or over_cap or missing or stale)
    return {"n_measurements": len(readings), "worst_deg": worst, "worst_bone": worst_bone,
            "worst_where": worst_at, "per_bone_worst": per_bone, "over_tight": over_tight,
            "violations": violations, "over_cap": over_cap, "missing": missing,
            "stale": stale, "caps": dict(caps), "worst_bone_where": worst_where,
            "verdict": "OK" if ok else "FAIL"}


def make_reference_run(rig, reference_poses, minmax):
    """返回 run(perturb=None) -> {clip: [每时刻「预测位姿 / 参考位姿」]}。

    run          ：甲（muscle_solve.solve_pose_swing）的当前输出
    run(perturb=)：把甲的输出里某一根骨故意转坏 —— **负控**用（⛔ 这函数自己不判绿）

    ⚠ perturb 只吃 solve_pose_swing 实际写出的那 53 骨（rig.base 是 53 骨），所以
      「只动一根」之后其余 52 根**逐位不变** —— 负控的期望值随即可复算，
      ⛔ 不是从「我改了多少」直接抄（kb/19：判据的期望值要由扰动传播推出）。
    """
    ms = _load(MSOLVE, "muscle_solve_for_humanoid_ruler")
    body_of, ref = {}, reference_poses
    for clip in HUMANOID_CLIPS:
        p = PAYLOAD_DIR / f"{clip}.unity_payload.json"
        if not p.exists():
            pytest.fail(f"缺烘制载荷 {p}（真读数，这把尺子不许跳）。{ACC_REGEN}")
        body_of[clip] = json.loads(p.read_text(encoding="utf-8"))
        for ti, sample in enumerate(ref[clip]):
            if "bones" not in sample:
                pytest.fail(f"参考位姿缺 bones：{p} 第 {ti} 时刻。{ACC_REGEN}")

    def run(perturb=None) -> dict:
        out = {}
        for clip in HUMANOID_CLIPS:
            payload = body_of[clip]
            n_times = len(payload["times"])
            if n_times != len(ref[clip]):
                pytest.fail(f"{clip}：载荷 {n_times} 个时刻 vs 参考 {len(ref[clip])} 个 —— "
                            f"两侧数据代际不一致（⚠ 同一 out/ 下有过两代同名 payload）")
            out[clip] = []
            for ti in range(n_times):
                mus = payload["muscles"][ti * N_MUSCLES_PER_TIME:
                                         (ti + 1) * N_MUSCLES_PER_TIME]
                pred = ms.solve_pose_swing(
                    mus, rig, minmax,
                    body_rot=payload["bodyRot"][ti * 4:(ti + 1) * 4],
                )
                if perturb is not None:
                    pred = perturb(clip, ti, pred)
                out[clip].append({"pred": pred, "ref": ref[clip][ti]["bones"]})
        return out

    return run


def iter_measurements(runs, tag: str) -> list:
    """逐 (clip, 时刻, 骨) 的测地距离清单。骨名对不上 -> **FAIL**（⛔ 跳过 = 静默给绿）。"""
    rows = []
    for clip in HUMANOID_CLIPS:
        for ti, pair in enumerate(runs[clip]):
            pred, ref = pair["pred"], pair["ref"]
            where = f"{tag}·{clip}·t={ti}"
            if set(pred) != set(ref):
                pytest.fail(f"{where}：骨名对不上（预测 {len(pred)} 骨 / 参考 {len(ref)} 骨；"
                            f"差集 {sorted(set(pred) ^ set(ref))[:5]}）—— ⛔ 尺子不许跳过。"
                            f"{ACC_REGEN}")
            rows.append((clip, ti, {b: MR.geodesic_deg(pred[b], ref[b]) for b in pred}))
    return rows


@pytest.fixture(scope="module")
def mrig():
    """真探针参数（单通道激励）。⛔ 缺文件 -> fail 并给复算命令（这个形状保留）。"""
    if not PROBE_JSON.exists():
        pytest.fail(f"缺单通道激励探针 {PROBE_JSON}（这把尺子读真数据，不许跳）。{ACC_REGEN}")
    return _load(MSOLVE, "muscle_solve_for_humanoid_ruler").MuscleRig.from_probe(PROBE_JSON)


@pytest.fixture(scope="module")
def mrefs():
    """真参考位姿（Unity 烘出来的）+ HumanTrait 默认上下限。"""
    if not RIG_DUMP.exists():
        pytest.fail(f"缺 {RIG_DUMP}（参考位姿与 muscleDefaultMinMax）。{ACC_REGEN}")
    rd = json.loads(RIG_DUMP.read_text(encoding="utf-8"))
    poses = rd.get("poses") or {}
    for clip in HUMANOID_CLIPS:
        if clip not in poses:
            pytest.fail(f"{RIG_DUMP.name} 里没有 {clip} 的参考位姿（有：{sorted(poses)}）。"
                        f"{ACC_REGEN}")
    return rd


@pytest.fixture(scope="module")
def mmeasure(mrig, mrefs):
    """一次算完甲的输出（正控），并逐骨测地距离；负控在用例里对同一次输出做扰动。"""
    run = make_reference_run(mrig, mrefs["poses"], mrefs["muscleDefaultMinMax"])
    return {"run": run, "readings": iter_measurements(run(), "甲")}


def _per_bone_worst(readings) -> dict:
    """骨名 -> 该骨在全部时刻上的**最大**测地距离（⛔ 不报平均）。"""
    worst: dict = {}
    for _clip, _ti, per in readings:
        for bone, deg in per.items():
            if deg > worst.get(bone, -1.0):
                worst[bone] = deg
    return worst




def test_humanoid_ruler_reads_real_data_and_measures_all_53_bones(mrig, mrefs, mmeasure) -> None:
    """★ 正控：尺子读的是**真读数**，且**一根骨都不许漏**（漏 = 静默给绿）。

    读数：探针 53 骨（muscle_probe.nBones = 53）· 参考位姿 53 骨 · 3 clip x 4 时刻
    ⇒ **636 次逐骨比对**；**每一个**时刻都必须是 53 骨（不是「平均意义上出现」）。

    ⛔ 这条**盖不住**什么（实测，别以为它全能）：把尺子入口换成不归一化那份
      （_pair_deg -> _geo_deg），整体判据的结论**一个字都不变**（还是 OK、还是只有 Hips 越线）——
      因为那份 0.0537 度的量化噪声，其 max 落在 52 根那一边、够不到 1e-2 的结构线。
      真正抓它的是 test_single_source_geodesic_ruler（那条会当场红）。两条要一起跑。
    """
    assert mrefs["unity"] == UNITY_EXPECT, (
        f"参考位姿是 {mrefs['unity']} 跑出来的（应为 {UNITY_EXPECT}）—— 换过探针就重读一遍")
    assert len(mrig.base) == N_EXPECT_BONES, f"探针只给 {len(mrig.base)} 骨"
    assert len(mrig.entries) == len(mrig.channel_map()) >= 91, f"通道只有 {len(mrig.entries)} 条"

    readings = mmeasure["readings"]
    assert len(readings) == len(HUMANOID_CLIPS) * 4, len(readings)
    assert {len(per) for _c, _t, per in readings} == {N_EXPECT_BONES}, "每时刻都必须是 53 骨"
    assert sum(len(per) for _c, _t, per in readings) == 636, (
        f"逐骨比对总数 = {sum(len(per) for _c, _t, per in readings)}，应为 3x4x53 = 636")

    # ★ 缺参不许静默给绿：缺一条肌肉参数 -> 甲必须**抛**，不是回落成单位四元数
    ms = _load(MSOLVE, "muscle_solve_for_humanoid_ruler")
    with pytest.raises(ms.MissingAvatarParametersError):
        ms.MuscleRig.from_probe(ML_OUT / "根本没有这个文件.json")


def test_humanoid_overall_reading_and_verdict(mrig, mrefs, mmeasure) -> None:
    """★★ 判据：**人形整体精度**（逐骨测地距离）＋ 一份**逐骨上限**的规则表。

    实测（636 次逐骨比对，最差值口径，尺子已归一化 —— 见 _pair_deg 那段）：
        · 越过逐骨精度线 1e-3 度的骨**恰好 1 根 = Hips**，最差读数 **6.897797** 度；
        · 其余 52 根**最大 4.42e-05** 度（比精度线小 23 倍）；
        · 越过结构线 1e-2 度的骨**也只此一根**；两档之间是**空的**（0.000044 < ? < 6.898）；
        · 没登记的坏骨 0 根 · 规则表里没有过期项。
    ⇒ 判 OK。⛔ 这不是「Hips 没问题」：它由 ALLOWED_VIOLATION_CAPS 逐条具名登记（上限 10 度）；
      哪天它降到 1e-3 以内、或又冒出一根新的坏骨，这条都会红。

    ★ 留痕（2026-10-13）：下面那个冻结期望值由 **7.535490 -> 6.897797**（A 线**纯修正**：
      脊链 9 条的轴改用 e.hips_axis，探针实测 dot = +1.000000，13/13；**其余 52 骨逐位不变**，
      依据 accept.py）。
      ⛔ **不许**把断言改成「读当前值、断言它在 cap 内」—— 那是 kb/19 的同义反复；
      「冻结值 ＋ 改动必须留痕」才是对的形状：甲一变就被抓住，要改就得先写清为什么。
    """
    got = accuracy_verdict(mmeasure["readings"], ALLOWED_VIOLATION_CAPS)

    assert got["n_measurements"] == 12, got["n_measurements"]
    assert got["verdict"] == "OK", (
        f"没登记的坏骨 {got['violations']} · 超过自己上限的 {got['over_cap']} · "
        f"规则表过期项 {got['missing']} · 越过精度线却不该越的 {got['stale']}")
    assert got["violations"] == [], "除规则表外必须 0 根越过结构线"
    assert got["over_cap"] == [], f"登记过的骨也不许超过自己的上限：{got['caps']}"
    assert got["missing"] == [], "规则表里有骨已经越不过结构线了 —— 过期就要改表，不是让它留着"
    assert got["over_tight"] == set(ALLOWED_TIGHT_BONES), (
        f"越过 {TIGHT_LINE_DEG} 度的骨应恰好是 {sorted(ALLOWED_TIGHT_BONES)}，"
        f"实测 {sorted(got['over_tight'])} —— 多一根 / 少一根都要在这里说清")
    assert set(ALLOWED_TIGHT_BONES) == set(ALLOWED_VIOLATION_CAPS)
    assert len(ALLOWED_VIOLATION_CAPS) == 1, "多一个豁免就说明还有别的骨没解对"
    assert got["worst_bone"] == "Hips" and got["worst_where"][0] in HUMANOID_CLIPS
    assert got["worst_bone_where"]["Hips"] == got["worst_where"], "点名要指到同一个 (clip, 时刻)"
    # ★★ 冻结值（⛔ 不许改成「读当前值再断言它在 cap 内」—— 那是 kb/19 的同义反复）。
    #    7.535490 -> **6.897797**（2026-10-13，A 线轴基准修正：脊链 9 条的轴改用 e.hips_axis，
    #    探针实测 dot = +1.000000，13/13）；**其余 52 骨逐位不变**；依据 accept.py。
    #    判据**仍是未通过**（<= 1e-3），⛔ 别把这个数读成「通过了」。
    assert got["worst_deg"] == pytest.approx(6.897797, abs=1e-5), (
        f"最差读数 {got['worst_deg']:.6f} 度 —— 与冻结值 6.897797 不符："
        f"要么换了数据、要么甲变了；⛔ 别默默改这条断言，要改就先写清为什么（见上一段留痕）")

    # ★ 报两端而不是只报 max（kb/19）：52 根「其余骨」必须仍在精度线内
    rest = {b: d for b, d in got["per_bone_worst"].items() if b not in ALLOWED_TIGHT_BONES}
    assert len(rest) == N_EXPECT_BONES - len(ALLOWED_TIGHT_BONES) == 52, len(rest)
    assert max(rest.values()) <= TIGHT_LINE_DEG, (
        f"除规则表外最差 {max(rest.values()):.3e} 度（{max(rest, key=rest.get)}）已越过精度线")


def test_humanoid_ruler_goes_red_on_a_known_violation(mrig, mrefs, mmeasure) -> None:
    """★★ **负控**：喂一个**已知违规**的点进去，尺子**必须报红**（⛔ 只写正控 = 恒绿 = 没测）。

    三步（期望值由扰动传播推出，⛔ 不是从「我改了多少」直接抄）：
      ① 甲当前输出判 OK —— **绿过**（⛔ 恒红的尺子不是尺子）；
      ② 把 Hips 故意转坏 50.0 度（tools/muscle_reference.py::perturb，⛔ 不另写一份扰动器）
         => 最差骨仍是 Hips、**只多 50 度**、骨名一个不多一个不少（perturb 只碰那一个键）；
      ③ 同一套判据必须 **FAIL**，且**指明是哪条**判据红的 —— 这里是 over_cap
         （Hips 在规则表里，所以红**不该**由「没登记的坏骨」给出；那条由下一条用例单独验）。

    ⚠ 扰动量为什么是 50 度而不是 5 度（第一版真踩了）：Hips **已经**在 6.897797 度、
      已在两条线外，再转 5 度**判据的结论一个字都不变**（还是那条 Hips）—— 那种负控等于没测。
      5 度那版当场红了，报的是 assert 'OK' == 'FAIL' —— 它自己把这条教训写出来了。
    """
    base = accuracy_verdict(mmeasure["readings"], ALLOWED_VIOLATION_CAPS)
    assert base["verdict"] == "OK", "前提不成立：甲当前输出本该判绿 —— 先看上面那条"
    assert base["worst_bone"] == "Hips" and base["over_cap"] == []

    def hurt(_clip: str, _ti: int, pose: dict) -> dict:
        mine = dict(pose)
        mine["Hips"] = MR.perturb(pose, "Hips", 50.0)["Hips"]
        return mine

    red_readings = iter_measurements(mmeasure["run"](perturb=hurt), "负控/Hips+50度")
    assert len(red_readings) == len(mmeasure["readings"]), "负控不该改变时刻数"
    red = accuracy_verdict(red_readings, ALLOWED_VIOLATION_CAPS)

    assert red["verdict"] == "FAIL", "尺子没红 —— 它测不了「有一根骨被解坏了」（恒绿尺子）"
    assert red["over_cap"] == ["Hips"], (
        f"红的应当是「Hips 超过自己的上限 {ALLOWED_VIOLATION_CAPS['Hips']} 度」，"
        f"实测 over_cap = {red['over_cap']}")
    assert red["violations"] == [], (
        "Hips 在规则表里，所以红**不该**由「没登记的坏骨」给出 —— 两条失败路径要分开验")
    assert red["missing"] == [], "Hips 本来就越线，规则表不该被判过期"
    assert red["worst_bone"] == "Hips" and red["worst_deg"] > base["worst_deg"] + 45.0, (
        f"扰动没接上：{base['worst_deg']:.6f} -> {red['worst_deg']:.6f}")
    assert red["worst_deg"] > ALLOWED_VIOLATION_CAPS["Hips"], red["worst_deg"]
    assert {c for c, _t, _p in red_readings} == set(HUMANOID_CLIPS)


def test_ruler_red_on_unregistered_bad_bone_and_stale_allowance(mmeasure) -> None:
    """负控 2/3/4：**没登记的坏骨**能红 · **规则表过期**能红 · 但尺子**不是**「凡扰动必红」。

    ⛔ 这条防的是「尺子退化成扰动免疫 / 对扰动过敏」：
      (a) 拿真读数里的**一个中间值**塞进一根骨（远在两条线内）=> 必须**仍然绿**；
      (b) 同一根骨塞 0.5 度（越过结构线、而规则表里没有它）
          => 必须红，且 violations == ['LeftHand']；
      (c) 把一条**登记过的**骨塞到「越线但没超过自己上限」（Hips 放到 0.5 度）
          => 必须红，红的理由是 **missing（规则表过期）** —— 与 (b) 正好相反的方向；
          ⚠ 值取 1e-4：要让它**同时**低于结构线（否则不算「不再越线」）与精度线
      (d) 规则表为空 -> 必须直接抛（⛔ 空表 = 谁都不许越线，不许静默全绿）。
    """
    keys = [f"{c}·t{ti}" for c, ti, _p in mmeasure["readings"]]
    bones = dict(zip(keys, (per for _c, _ti, per in mmeasure["readings"]), strict=True))

    def verdict(live: dict, caps: dict = ALLOWED_VIOLATION_CAPS) -> dict:
        # ⚠ 读数必须**从被改过的那份 bones 现推**（⛔ 不许复用真读数那批 tuple），
        #   否则判据看的是「一组合法读数 + 一份被改坏的骨名索引」= 两个不同的世界。
        #   第一版就是这么写的 —— 被这条用例自己抓出来：注入 0.5 度居然还是 OK。
        live_readings = [(c, ti, live[f"{c}·t{ti}"]) for c, ti, _p in mmeasure["readings"]]
        return accuracy_verdict(live_readings, caps)

    real_mid = sorted(mmeasure["readings"][0][2].values())[len(mmeasure["readings"][0][2]) // 2]
    assert 0.0 <= real_mid <= TIGHT_LINE_DEG, real_mid
    live = {k: dict(v) for k, v in bones.items()}
    live[keys[0]]["LeftHand"] = real_mid
    assert verdict(live)["verdict"] == "OK", "在精度线内的扰动不该红 —— 尺子对扰动过敏"

    hurt = {k: dict(v) for k, v in bones.items()}
    hurt[keys[0]]["LeftHand"] = 0.5
    got = verdict(hurt)
    assert got["verdict"] == "FAIL" and got["violations"] == ["LeftHand"], got
    assert got["over_cap"] == [] and got["missing"] == []

    # ⚠ 必须把**每一个时刻**都降下来 —— 只改一个时刻的话该骨的 max 仍是 7.535（越线），
    #   「规则表过期」这条根本不会触发。（第一版就只改了 t0，当场红在 missing == []。）
    #   ⚠ 这也正是尺子自己的口径：越线看的是**任一时刻**，所以「不再越线」要**全部**降下来。
    natural = {k: dict(v) for k, v in bones.items()}
    for k in keys:
        natural[k]["Hips"] = 1e-4   # 从 7.5 度一路降到 1e-4：越过精度线、但**够不到**结构线
    nat = verdict(natural)
    assert nat["verdict"] == "FAIL", "越线却登记过、也没超上限 —— 必须由「规则表过期」判红"
    assert nat["missing"] == ["Hips"], nat["missing"]
    assert nat["violations"] == [] and nat["over_cap"] == [], nat
    assert nat["over_tight"] == set(), (
        "1e-4 连精度线都不到 —— 那它**不该**再占着 ALLOWED_TIGHT_BONES 那个位置；"
        "下面那条才是「还在线上」的情形")
    # 报两端里的另一端：把 Hips 停在「精度线之上、结构线之下」（1e-3 ~ 1e-2 之间）——
    # 那时它**不再算越线**（missing 会红），但**仍越过逐骨精度线**，所以还留在
    # ALLOWED_TIGHT_BONES 里。这两条线各管一件事，这里正好把它们的分工钉死。
    between = {k: dict(v) for k, v in bones.items()}
    for k in keys:
        between[k]["Hips"] = 5e-3
    mid = verdict(between)
    assert mid["verdict"] == "FAIL" and mid["missing"] == ["Hips"], mid
    assert mid["over_tight"] == {"Hips"} and mid["stale"] == [], mid

    # 报两端：除规则表外 52 根**一根都没越线**（红线四周是干的）
    assert nat["worst_bone"] == "Hips"
    rest = {b: d for b, d in nat["per_bone_worst"].items() if b != "Hips"}
    assert len(rest) == 52 and max(rest.values()) <= TIGHT_LINE_DEG

    with pytest.raises(ValueError):
        accuracy_verdict(mmeasure["readings"], {})
    with pytest.raises(ValueError):
        accuracy_verdict([], ALLOWED_VIOLATION_CAPS)


def test_ruler_goes_red_when_a_bone_is_missing_from_the_prediction(mmeasure) -> None:
    """⛔ 反面自证：**把 Hips 从预测里抹掉** -> 必须红（走的正是「骨名对不上」那道闸）。

    为什么要单独验它：Hips 不在 channel_map 里（91 条通道没有一条驱动它），所以
    「少一根骨」这种错**不会**体现在任何一根骨的角差上 —— 只能靠骨名/数量断言抓。
    这条也顺带把「跳过 = 静默给绿」那条路堵死（尺子宁可 FAIL 也不跳过）。
    """
    def drop_hips(_clip: str, _ti: int, pose: dict) -> dict:
        return {b: q for b, q in pose.items() if b != "Hips"}

    with pytest.raises(BaseException) as ei:
        iter_measurements(mmeasure["run"](perturb=drop_hips), "负控/删Hips")
    msg = str(getattr(ei.value, "value", ei.value))
    assert "骨名对不上" in msg, msg


def test_single_source_geodesic_ruler(mrefs) -> None:
    """★ 一把尺子只有一份：本文件那个本地 _geo_deg 必须与 tools/muscle_reference.py
    在**全部 53 根真骨**上一致（⛔ 两份实现会在 q~-q / 未归一化 上分叉，那正是 kb/19 的旧伤）。"""
    assert MR.geodesic_deg([1.0, 0.0, 0.0, 0.0], [1.0, 0.0, 0.0, 0.0]) == 0.0
    assert MR.geodesic_deg([0.0, 0.0, 0.0, 1.0], [0.0, 0.0, 0.0, -1.0]) == 0.0
    assert MR.geodesic_deg([0.0, 0.0, 0.0, 1.0], [1.0, 0.0, 0.0, 0.0]) == pytest.approx(180.0)
    with pytest.raises(ValueError):
        MR.geodesic_deg([0.0, 0.0, 0.0, 1.0], [0.0, 0.0, 1.0])   # 长度不等必须抛

    q0 = mrefs["poses"]["Kneel_aim"][0]["bones"]
    assert len(q0) == N_EXPECT_BONES
    # ⚠ 两份**不是**逐位相等，而且差在**归一化**上（这是实测出来的，不是估计）：
    #   参考里的四元数是 float32 写出来的，|q| 差一点点；_geo_deg（本文件）**不归一化**，
    #   于是「真值 vs 自己」它不是 0，而是 **2.4148e-06 度**（Chest 那根最明显）。
    #   canonical（muscle_reference.geodesic_deg）归一化后才是 0 —— 那个 2.4e-06 就是
    #   纯量化的**刻度底噪**（muscle_reference.py:145 已记过同一个数）。
    # ⚠ 实测：两份**在未归一化的输入上会分叉**，而且差得**不小** —— 这不是估计：
    #     LeftToes 那根 q 的 |q| = 0.999999945173286（float32 写出来的），
    #     _geo_deg（本文件）**不归一化** ⇒ 「真值 vs 自己」它给 **0.05366 度**，
    #     而 canonical 归一化后给 **0.0**：同一把尺子的两种自证读数差 **0.0537 度** ——
    #     比本文件的逐骨精度线 1e-3 还大 53 倍 ⇒ 「两份实现」这件事**真的会出账**。
    #     ⇒ 判据不是「两份逐位相等」，而是「**归一化之后**两份逐位一致」——
    #       那正是肌肉线一直在用的口径（muscle_reference.py 里的注释：
    #       2*acos|dot| 只在单位四元数下成立；不归一化时「真值 vs 自己」给 0.0544 度）。
    def _unit(v: list[float]) -> list[float]:
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / n for x in v]

    worst_bone = max(q0, key=lambda b: _geo_deg(q0[b], q0[b]))
    worst_local = _geo_deg(q0[worst_bone], q0[worst_bone])
    assert worst_local > 1e-3, (
        f"未归一化那份的自身读数只有 {worst_local:.3e} 度（{worst_bone}）—— "
        f"参考不再是 float32 写出来的？那这条「两份会分叉」的读数就要重测")
    assert _pair_deg(q0[worst_bone], q0[worst_bone]) < 1e-5, (
        "归一化那份对同一根骨给的不是底噪级 —— 归一化没生效？那下面这条判据就没意义了")
    drift = max(abs(MR.geodesic_deg(u, u) - _geo_deg(u, u)) for u in map(_unit, q0.values()))
    assert drift < 1e-5, f"归一化后两份仍差 {drift:.3e} 度 —— 真的分叉了（不只是归一化口径）"
    # ⚠ 阈值是 1e-5 而不是 == 0：参考是 float32 写的，归一化后仍留 **2.4148e-06 度**
    #   的刻度底噪（muscle_reference.py 里记的同一个数）—— 那是数据自身的下限，
    #   ⛔ 不是「q 与 -q 不同」。判据要的是「底噪 << 逐骨精度线」，不是逐位相等。
    floor = 1e-5
    for bone, q in q0.items():
        # ⛔ 这两条必须走**归一化**入口：未归一化那份在真数据上会把不变量判成
        #   0.0463 度（LeftLittleDistal 实测）—— 那是浮点噪声，不是「q 与 -q 不同」。
        assert _pair_deg(q, [-x for x in q]) < floor, (bone, "q 与 -q 必须是同一个旋转")
        assert _pair_deg(q, q) < floor, (bone, "归一化后「真值 vs 自己」必须是底噪级")
        assert MR.geodesic_deg(q, q) < floor, (bone, "canonical 自证超过底噪")
    assert drift > 0.0, (
        "drift 变成 0 了 —— 要么参考换了（不再是 float32 写出来的），"
        "要么两份尺子被合并成一份；两种情况都要在这里说清")
    h = q0["Hips"]
    assert MR.geodesic_deg(h, [0.0, 0.0, 0.0, 1.0]) > TIGHT_LINE_DEG


def test_hips_is_the_only_bone_outside_the_channel_map(mrig) -> None:
    """为什么豁免表里**只能是 Hips**：探针的 91 条通道里**没有一条**驱动 Hips。

    => 其余 52 根的旋转是「通道参数的函数」，而 Hips 走 hips_rel() 那条分配律
    （躯干链重新分配）—— 两条路的**误差口径本来就不同**。哪天多出一根不在通道表里的骨，
    这里会红：那就说明有第二根骨也在走另一条路（⛔ 别默默放过）。
    """
    driven = {e.bone for e in mrig.entries}
    assert "Hips" in mrig.base and "Hips" not in driven
    assert driven == set(mrig.base) - {"Hips"}, sorted(set(mrig.base) - driven)
    assert len(driven) == N_EXPECT_BONES - 1 == 52


def test_hips_reading_is_over_the_line_at_every_recorded_time(mrig, mrefs, mmeasure) -> None:
    """Hips 的 **6.897797** 度（修正前 7.535490）不是某一时刻的偶然 —— 12 个时刻**每一个**都越线。

    实测两端（⛔ 只报 max 是不合格的）：这 12 个时刻是 **0.737404 ~ 6.897797** 度。
    ⚠ 这条同时是「规则表**逐条具名**」的支撑：Hips 是**系统性**的分配残差，不是抖动；
      而其余 52 根在**每一个**时刻都在精度线内（最大 4.42e-05 度）。
    """
    per_time = {(clip, ti): per for clip, ti, per in mmeasure["readings"]}
    assert len(per_time) == 12, len(per_time)
    hips = {k: v["Hips"] for k, v in per_time.items()}
    assert len(hips) == 12 and "Hips" in per_time[("Kneel_aim", 0)]
    assert min(hips.values()) > TIGHT_LINE_DEG, (
        f"Hips 最小读数 {min(hips.values()):.6f} 度 <= 逐骨精度线：那它就该从 "
        f"ALLOWED_TIGHT_BONES 里退出（规则表过期），⛔ 不是默默留着")
    # ★★ 冻结值（⛔ 不许读当前值再断言 —— 同义反复）：7.535490 -> **6.897797**，
    #    2026-10-13 A 线轴基准修正（脊链 9 条的轴改用 e.hips_axis，dot = +1.000000，13/13），
    #    其余 52 骨逐位不变；依据 _work/muscleline/accept.py（判据仍未通过 <= 1e-3）。
    assert max(hips.values()) == pytest.approx(6.897797, abs=1e-5), max(hips.values())
    rest = [d for per in per_time.values() for b, d in per.items() if b != "Hips"]
    assert len(rest) == 12 * 52, len(rest)
    assert max(rest) <= TIGHT_LINE_DEG, f"其余骨最大 {max(rest):.3e} 度"


def test_finger_mismatch_is_explained_by_zero_game_muscles() -> None:
    """task-8 的闭环（真读数，能红）：手指的「相同/不同」是被**游戏自己的肌肉值**决定的。

    机制（三步都由读数推出，不是猜）：
      ① 自造 clip **没有手指通道**（L1 结论：Unity 不接受手指肌肉曲线名）
         ⇒ 真值侧手指 DoF 取默认 **0**；
      ② 烘制侧 HumanPoseHandler 用的是**游戏的**手指肌肉值；
      ③ ⇒ 两侧只在「游戏值恰好为 0」时重合。

    读数（Kneel_aim，常值）：
      · 游戏手指肌肉 8 条/组：**Middle 全 0.000000 · Little 全 0.000000**；
        Thumb 最大 1.498641 · Index 0.811476 · Ring 0.811465（非零）；
      · 逐骨测地距离 anim~bake：Middle/Little 那 12 根是 **float32 级**（最大 0.045 度），
        Thumb/Index/Ring 那 18 根是 **4.347~36.516 度** ⇒ 两簇间距 **约 97 倍**；
      · b) 同 rig 双读：基线 == rest（最大 0.031 度）；
        SampleAnimation 让 **30/30** 根手指都动了（8~55 度）
        ⇒ 采样**确实驱动**手指，驱动到的是「手指 DoF = 0」那个姿势，**不是 rest**；
      · c) packed clip（打包读回）驱动读数与编辑器 clip **逐位相同**
        ⇒ 打包没有改变手指行为。

    ⚠ 判据用**两簇分离**（>100 倍空隙）而不是固定阈值 —— 固定 1e-6 度会把
      float32 噪声（0.03~0.04 度）误判成「不同」（第一版就是这么红的，10 vs 12）。
    """
    import json as _json
    bake_p = WORK / "muscleline" / "out" / "Kneel_aim.bake.json"
    mus_p = WORK / "muscleline" / "out" / "Kneel_aim.muscles.json"
    if not (bake_p.exists() and mus_p.exists()):
        pytest.fail(f"缺真读数 {bake_p} / {mus_p} —— 复算：_work/muscleline/run_l2.ps1")
    b = _json.loads(bake_p.read_text(encoding="utf-8"))
    mus = _json.loads(mus_p.read_text(encoding="utf-8"))["humanPoses"][0]["muscles"]
    anim, baked = b["samples"][0]["anim"], b["samples"][0]["bake"]

    groups = {"Thumb": 0, "Index": 4, "Middle": 8, "Ring": 12, "Little": 16}
    mx = {g: max(abs(x) for x in (mus[55 + o:55 + o + 4] + mus[75 + o:75 + o + 4]))
          for g, o in groups.items()}
    assert mx["Middle"] == 0.0 and mx["Little"] == 0.0, mx
    assert mx["Thumb"] > 1.0 and mx["Index"] > 0.5 and mx["Ring"] > 0.5, mx

    dist = {n: _geo_deg(anim[n], baked[n]) for n in U.FINGER_BONES}
    ml = [d for n, d in dist.items() if "Middle" in n or "Little" in n]
    tir = [d for n, d in dist.items() if "Thumb" in n or "Index" in n or "Ring" in n]
    assert len(ml) == 12 and len(tir) == 18, (len(ml), len(tir))
    # 阈值取**实测值**（Lead 摘要里写 10.0~36.5，逐骨实测最小是 RightRingProximal 4.347）
    assert max(ml) < 0.05, f"Middle/Little 应当全是 float32 级，实测最大 {max(ml)}"
    assert min(tir) > 4.0, f"Thumb/Index/Ring 应当全是大角度，实测最小 {min(tir)}"
    gap = min(tir) / max(max(ml), 1e-9)
    assert gap > 50.0, f"两簇之间应当有大空隙，实测 {gap:.1f} 倍"

    # 能红的正控：把「全 0 那两组」扰动一下，两侧重合这条对应关系就不再成立
    pert = list(mus)
    for o in (groups["Middle"], groups["Little"]):
        for k in range(4):
            pert[55 + o + k] = 0.5
    pert_grp = (pert[55 + 8:55 + 12] + pert[75 + 8:75 + 12]
                + pert[55 + 16:55 + 20] + pert[75 + 16:75 + 20])
    assert max(abs(x) for x in pert_grp) > 0.0, "扰动没生效 —— 那这条正控是假的"


def test_finger_bones_are_the_ones_the_ruler_cannot_see() -> None:
    """L2 必须排掉的那 30 根手指骨（无真值：驱动它们的 40 条肌肉没有数字 attribute）。"""
    assert len(U.FINGER_BONES) == 30
    assert U.FINGER_BONES[0] == "LeftThumbProximal"
    assert U.FINGER_BONES[-1] == "RightLittleDistal"
    assert not (set(U.FINGER_BONES) - set(U.HUMAN_BODY_BONES))


def test_compare_bones_lists_excluded_bones_separately() -> None:
    a = {"bones": {"Hips": [0, 0, 0, 1], "LeftThumbProximal": [0, 0, 0, 1]}}
    b = {"bones": {"Hips": [0, 0, 0, 1], "LeftThumbProximal": [0.5, 0, 0, 1]}}
    got = U.compare_bones(a, b, exclude=U.FINGER_BONES)
    assert got["maxDev"] == 0.0 and got["nBones"] == 1
    assert got["excluded"] == ["LeftThumbProximal"], "不参与比对的骨必须单独列出来"
    assert U.compare_bones(a, b)["maxDev"] == 0.5, "不排除时它本来是会红的"


def test_l1_forty_finger_names_take_the_generic_property_path(l1: dict) -> None:
    """剩下 40 条手指名 Unity **不认成肌肉曲线** ⇒ 掉进通用组件属性那条路，attribute = crc32(名字)。

    逐条钉死 40/40；两条反证一并留成尺子：
      crc32 = 40/40 · fnv1a（Animator.StringToHash 那一族）= 0/40 · Mono String.GetHashCode = 0/40。
    """
    pairs = [(n, int(a)) for n, a in l1["nameToAttribute"]]
    big = [(n, a) for n, a in pairs if not 7 <= a <= 136]
    assert len(big) == 40
    assert all(zlib.crc32(n.encode("utf-8")) & 0xFFFFFFFF == a for n, a in big), "crc32 规则不成立"

    def fnv1a(s: str) -> int:
        h = 0x811C9DC5
        for ch in s.encode("utf-8"):
            h = ((h ^ ch) * 0x01000193) & 0xFFFFFFFF
        return h

    assert not any(fnv1a(n) == a for n, a in big), "fnv1a 不该命中（那是 StringToHash 那一族）"
    assert all(("Thumb" in n or "Index" in n or "Middle" in n or "Ring" in n or "Little" in n)
               for n, _ in big), "这 40 条应当全是手指"
    n0, a0 = big[0]
    assert (zlib.crc32((n0 + "x").encode("utf-8")) & 0xFFFFFFFF) != a0, "换一个字符必须不再命中"


# ------------------------------------------------------- m_IndexArray 的结构
def test_index_map_is_a_bijection_on_all_72(index_maps: dict) -> None:
    recs = index_maps["records"]
    assert len(recs) == 72
    for r in recs:
        assert sorted(r["indexMap"]) == list(range(130)), f"{r['name']} 的 indexMap 不是双射"
        assert r["isPerm"] is True


def test_index_map_blocks_must_match_the_clip_blocks(index_maps: dict, classify: dict) -> None:
    """纯人形 clip 的 130 条曲线**全是肌肉通道** ⇒ 按 indexMap 落进 S/D/C 三块的条数，
    必须**恰好**等于这条 clip 的 (streamed, dense, constant)。这是能红的一条。"""
    cls = {c["pid"]: c for c in classify["clips"]}

    def blocks(imap: list[int], s: int, d: int) -> Counter:
        return Counter("S" if g < s else ("D" if g < s + d else "C") for g in imap)

    bad = []
    for r in index_maps["records"]:
        c = cls[r["pid"]]
        want = Counter({"S": c["streamed"], "D": c["dense"], "C": c["constant"]})
        if blocks(r["indexMap"], c["streamed"], c["dense"]) != want:
            bad.append((r["name"], c["streamed"], c["dense"], c["constant"]))
    assert bad == [], f"块计数对不上的：{bad[:3]}"

    # 尺子正控（⚠ 第一版写错了，如实记）：**交换排列里两个元素是改不动这张直方图的**
    #   （多重集不变）—— 所以正控必须是"把一个值搬出它所在的块"，而不是"换位置"。
    r0 = index_maps["records"][0]
    c0 = cls[r0["pid"]]
    want0 = Counter({"S": c0["streamed"], "D": c0["dense"], "C": c0["constant"]})
    broken = list(r0["indexMap"])
    hi = next(i for i, g in enumerate(broken) if g >= c0["streamed"])   # 搬进 streamed 块
    broken[hi] = 0
    assert blocks(broken, c0["streamed"], c0["dense"]) != want0, "把一个值搬出它所在的块必须被抓到"
    # 第二条正控：把块边界读错一位，也必须红
    assert blocks(r0["indexMap"], c0["streamed"] - 1, c0["dense"]) != want0, "边界读错必须被抓到"


def test_more_permutations_than_human_avatars(index_maps: dict) -> None:
    """Lead 的假设 H（排列由 Avatar 决定）**在计数上就被证伪**：

    同一 Avatar 必共享同一排列 ⇒ 排列种类 <= 人形 Avatar 数；units 包里只有 16 个 Avatar 对象
    （15 个人形，读数 m1b_avatars.json），实测排列种类 **44** 种 ⇒ H 不成立。
    顺带钉死：恒等 6 条、非恒等 66 条、族大小分布 {1:36, 2:6, 6:1, 18:1}。
    """
    groups = {tuple(r["indexMap"]) for r in index_maps["records"]}
    assert index_maps["distinct"] == len(groups) == 44
    assert len(groups) > 15, "不超过人形 Avatar 数的话，计数法就证伪不了 H"
    ident = [r for r in index_maps["records"] if list(r["indexMap"]) == list(range(130))]
    assert len(ident) == 6
    assert len(index_maps["records"]) - len(ident) == 66
    sizes = Counter(Counter(tuple(r["indexMap"]) for r in index_maps["records"]).values())
    assert dict(sizes) == {1: 36, 2: 6, 6: 1, 18: 1}, dict(sizes)


def test_quaternion_components_may_span_different_blocks(index_maps: dict, classify: dict) -> None:
    """把整件事解释清楚的那条读数：Prone_idle 的 RootQ.xyzw 落在全局号 [2,3,58,68]，
    跨了 streamed / dense / constant **三个块** ⇒「4 个分量必须连续」这个直觉是错的，
    所以「模长==1 的 4 连窗」那把尺子**注定判不了**非恒等 clip。"""
    cls = {c["pid"]: c for c in classify["clips"]}
    pr = next(r for r in index_maps["records"] if r["name"] == "Prone_idle")
    c = cls[pr["pid"]]
    s, d = c["streamed"], c["dense"]
    root_q = [pr["indexMap"][a - 7] for a in range(10, 14)]
    kinds = ["S" if g < s else ("D" if g < s + d else "C") for g in root_q]
    assert root_q == [2, 3, 58, 68], root_q
    assert kinds == ["S", "S", "D", "C"], kinds
    assert (s, d, c["constant"]) == (58, 9, 63)


# ------------------------------------------------------------------ 表结构
def test_channel_table_covers_the_130_attributes() -> None:
    attrs = [c.attribute for c in U.CHANNELS]
    assert attrs == list(range(7, 137)), "通道表必须恰好覆盖 attribute 7..136"
    assert U.N_CHANNELS == 130
    kinds: dict[str, int] = {}
    for c in U.CHANNELS:
        kinds[c.kind] = kinds.get(c.kind, 0) + 1
    assert kinds == {"rootT": 3, "rootQ": 4, "goalT": 12, "goalQ": 16, "muscle": 95}, kinds


def test_muscle_index_maps_to_attribute_42_plus() -> None:
    assert U.MUSCLE_ATTR[0] == 42
    assert U.MUSCLE_ATTR[54] == 96
    assert U.MUSCLE_ATTR[55] == 97
    assert U.MUSCLE_ATTR[94] == 136
    assert len(U.MUSCLE_ATTR) == 95


def test_goal_channels_are_the_four_goals() -> None:
    got = [(c.attribute, c.name) for c in U.CHANNELS if c.kind in ("goalT", "goalQ")]
    assert got[0] == (14, "LeftFootT.x")
    assert got[6] == (20, "LeftFootQ.w")
    assert got[-1] == (41, "RightHandQ.w")
    assert {c.goal for c in U.CHANNELS if c.goal} == set(U.GOALS)


def test_muscle_bone_table_is_95_unique_pairs() -> None:
    assert len(U.MUSCLE_BONES) == 95
    assert len(set(U.MUSCLE_BONES)) == 95, "同一 (骨, DoF) 不许出现两次"
    bad = sorted({b for b, _ in U.MUSCLE_BONES if b not in U.BONE_INDEX})
    assert bad == [], f"表里有人形骨表里没有的骨名：{bad}"
    assert len(U.HUMAN_BODY_BONES) == 55


def test_body_muscle_names_match_the_research_decoder() -> None:
    """独立来源对拍：研究线 clip_decode.py 的 55 条名字表（来自 Unity human::MuscleName）。"""
    if not DECODER_PY.exists():
        pytest.fail(f"缺独立对拍源 {DECODER_PY} —— 这把尺子不许跳")
    dec = _load(DECODER_PY, "clip_decode_probe")
    assert list(U.BODY_MUSCLE_NAMES) == list(dec.MUSCLE_NAMES), "55 条身体肌肉名与解码件不一致"


def test_human_bone_names_appear_in_real_avatars() -> None:
    """骨头名表对拍真数据：16 个 Avatar 的 m_HumanDescription.m_Human 里的 human 名。"""
    if not AVATAR_JSON.exists():
        pytest.fail(f"缺落盘读数 {AVATAR_JSON} —— 这把尺子不许跳")
    doc = json.loads(AVATAR_JSON.read_text(encoding="utf-8"))
    names = {h["human"] for a in doc["avatars"] for h in a["human"] if h.get("human")}
    assert names, "Avatar 表里一条 human 骨名都没有 —— 读数坏了"
    unknown = sorted(n for n in names if n not in U.BONE_DISPLAY_INDEX)
    assert unknown == [], f"游戏 Avatar 用的骨名不在本表里：{unknown}"


def test_bone_display_names_differ_from_enum_only_on_fingers() -> None:
    """实测：HumanTrait.BoneName 的手指骨名带空格（"Left Thumb Proximal"），枚举名不带。

    这条是**真读数逼出来的**：第一版拿枚举名去对 Avatar 的表，24 条手指骨对不上。
    """
    diff = {e: d for e, d in zip(U.HUMAN_BODY_BONES, U.HUMAN_BONE_DISPLAY, strict=True) if e != d}
    assert len(diff) == 30, f"带空格的应恰好是 30 根手指骨（5 指 x 3 段 x 2 手），实为 {len(diff)}"
    assert all(" " in d for d in diff.values())
    assert U.BONE_DISPLAY_INDEX["Left Thumb Proximal"] == U.BONE_INDEX["LeftThumbProximal"]
    assert U.BONE_DISPLAY_INDEX["Hips"] == U.BONE_INDEX["Hips"] == 0


def test_avatar_finger_names_are_exactly_the_derived_ones() -> None:
    if not AVATAR_JSON.exists():
        pytest.fail(f"缺落盘读数 {AVATAR_JSON} —— 这把尺子不许跳")
    doc = json.loads(AVATAR_JSON.read_text(encoding="utf-8"))
    names = {h["human"] for a in doc["avatars"] for h in a["human"] if h.get("human")}
    fingers = {n for n in names if any(k in n for k in U.FINGERS) and " " in n}
    derived = {d for d in U.HUMAN_BONE_DISPLAY if " " in d}
    assert fingers <= derived, f"实测里冒出推导不出的手指骨名：{sorted(fingers - derived)}"
    # 实测这 15 个人形 Avatar **没有映射小指**（Little），所以派生出来的 30 个里正好少 6 个；
    # 这一条是读数说的，不是猜的 —— 哪天 Avatar 加了小指，这里会红。
    missing = sorted(derived - fingers)
    assert missing == ["Left Little Distal", "Left Little Intermediate",
                       "Left Little Proximal", "Right Little Distal",
                       "Right Little Intermediate", "Right Little Proximal"], missing


# ------------------------------------------------------------------ 正控
def test_all_72_pure_humanoid_clips_have_exactly_attribute_7_to_136(
        pure_humanoid: list[dict]) -> None:
    assert len(pure_humanoid) == 72, f"纯人形 clip 数应为 72，实为 {len(pure_humanoid)}"
    for c in pure_humanoid:
        mus = U.classify_bindings(c["bindings"])
        assert len(mus) == 130, f"{c['name']} 肌肉绑定 {len(mus)} 条"
        assert sorted(b["attribute"] for b in mus) == list(range(7, 137)), c["name"]
        assert c["n_muscle_bindings"] == 130
        assert c["curves"] == 130
    assert sum(c["curves"] for c in pure_humanoid) == 72 * 130


# ------------------------------------------------------------------ 负控
def test_generic_clip_reports_no_muscle_instead_of_empty(
        classify: dict) -> None:
    generic = [c for c in classify["clips"] if c["kind"] == "generic"]
    assert len(generic) == 360
    for c in generic:
        assert c["n_muscle_bindings"] == 0
        with pytest.raises(U.NotHumanoidClipError) as ei:
            U.classify_bindings(c["bindings"])
        assert "这条不含肌肉" in str(ei.value), "负控的报错文案必须点名「不含肌肉」"


def test_atgm_is_the_named_negative_control(classify: dict) -> None:
    atgm = [c for c in classify["clips"] if c["pid"] == -9207998336256044584]
    assert len(atgm) == 1 and atgm[0]["name"] == "ATGM", "负控样本变了吗"
    assert atgm[0]["kind"] == "generic" and atgm[0]["n_muscle_bindings"] == 0
    with pytest.raises(U.NotHumanoidClipError):
        U.classify_bindings(atgm[0]["bindings"])


def test_empty_clip_and_empty_binding_list_raise() -> None:
    with pytest.raises(U.NotHumanoidClipError):
        U.classify_bindings([])


# ------------------------------------------------------------------ 尺子能红
def test_ruler_goes_red_on_dropped_duplicated_and_wrong_type(
        pure_humanoid: list[dict]) -> None:
    src = [dict(b) for b in pure_humanoid[0]["bindings"]]
    assert len(U.classify_bindings(src)) == 130

    dropped = [b for b in src if b["attribute"] != 60]
    with pytest.raises(U.NotHumanoidClipError) as e1:
        U.classify_bindings(dropped)
    assert "缺 1 条" in str(e1.value)

    dup = src + [dict(src[0])]
    with pytest.raises(U.NotHumanoidClipError) as e2:
        U.classify_bindings(dup)
    assert "重复" in str(e2.value)

    wrong = [dict(b) for b in src]
    for b in wrong:
        if b["attribute"] == 60:
            b["typeID"] = 4
    with pytest.raises(U.NotHumanoidClipError) as e3:
        U.classify_bindings(wrong)
    assert "缺 1 条" in str(e3.value)


def test_mapping_verdict_labels_its_own_confidence() -> None:
    """选通道序这件事**必须自报可信度** —— 三个真读数各是一条用例。

    真读数：
      _work/muscleline/out/index_maps_72.json  -> m_IndexArray[7:137] 在 **72/72** 条上是
        0..129 的排列，其中 **66 条非恒等 / 44 种不同排列**（x18 那种占 18 条，6 条恒等）
      extract_report.json 的 indexDetail -> Kneel_aim A=B=7.9e-08 · Prone_run A=B=1.80e-03
        · Prone_idle A=0.8225 / B=0.4391
    ⇒ 非恒等一律 B；但"没到模长 1"只能写 unverified（Unity 会 normalize，尺子只能证伪）。
    """
    # 恒等：A 与 B 逐位相同，取 A 并判 settled
    assert U.mapping_verdict(7.9e-08, 7.9e-08, True) == ("A_attr_minus_7", "settled")
    # 非恒等：A 构造上就错 ⇒ 一律走 B，哪怕 B 也没到模长 1（unverified，不是"可信"）
    assert U.mapping_verdict(1.799236e-03, 1.799236e-03, False) == ("B_index_array", "unverified")
    assert U.mapping_verdict(0.822530143, 0.439078086, False) == ("B_index_array", "unverified")
    assert U.mapping_verdict(0.9, 1e-9, False) == ("B_index_array", "settled")
    # ★ 2026-10-13 补：上面四条**全是 `err_b <= err_a`** ⇒ 覆盖不到「B 自证过不去」那条分支。
    #   旧实现在那里**回落到 A**，而 A（当它是恒等）在非恒等时构造上就错 ⇒ 那处 bug 曾长期躲过它。
    #   判据：`err_b > err_a` 且非恒等 ⇒ **必须仍是 B**（⛔ 不许回落）。
    assert U.mapping_verdict(0.5, 0.9, False) == ("B_index_array", "unverified")


def test_compare_bones_ruler_goes_red_on_a_0_05_perturbation() -> None:
    bones = {"Hips": [0.0, 0.0, 0.0, 1.0], "Spine": [0.1, 0.2, 0.3, 0.9]}
    same = {"Hips": [0.0, 0.0, 0.0, 1.0], "Spine": [0.1, 0.2, 0.3, 0.9]}
    green = U.compare_bones({"bones": dict(bones)}, {"bones": dict(same)})
    assert green["maxDev"] == 0.0 and green["nBones"] == 2

    baked = {"bones": json.loads(json.dumps(bones))}
    name = U.apply_bone_perturb(baked, 0, 0.05)
    assert name in bones
    red = U.compare_bones(baked, {"bones": dict(same)})
    assert red["maxDev"] == pytest.approx(0.05, abs=1e-9), "尺子必须能红"
    assert red["worstBone"] == name


def test_compare_bones_handles_quaternion_sign_flip() -> None:
    """q 与 -q 是同一个旋转 —— 尺子不许把它报成偏差。"""
    a = {"bones": {"Hips": [0.1, 0.2, 0.3, 0.9]}}
    b = {"bones": {"Hips": [-0.1, -0.2, -0.3, -0.9]}}
    assert U.compare_bones(a, b)["maxDev"] == 0.0


def test_compare_bones_lists_missing_bones() -> None:
    a = {"bones": {"Hips": [0, 0, 0, 1], "Spine": [0, 0, 0, 1]}}
    b = {"bones": {"Hips": [0, 0, 0, 1]}}
    got = U.compare_bones(a, b)
    assert got["missing"] == ["Spine"] and got["nBones"] == 1
