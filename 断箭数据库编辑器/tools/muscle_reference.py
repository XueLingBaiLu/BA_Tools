r"""甲 的验收尺子：把「一组骨骼局部旋转」与 Unity 烘出的真值逐骨比 —— **一律测地距离**。

判据一行：

    .venvScriptspython.exe tools/muscle_reference.py <bake.json>
      -> 打印 54 骨 × N 时刻的逐骨测地距离（度），并给出 max / 最差骨 / 最差时刻

⛔ **不许用分量差**：四元数 q 与 -q 是同一个旋转，分量差会把它们判成"差 2.0"。
   本仓实测踩过（Lead 也差点踩），所以这把尺子只认 2*acos|dot|。
⛔ 自证：--selftest 会把一根骨扰动一下，**必须变红**才算尺子（不能红就写"这个环境测不了"）。
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def geodesic_deg(p: list[float], q: list[float]) -> float:
    """两个四元数之间的测地距离（度）。q 与 -q 等价，所以先取 |dot|。

    ⚠ **必须先归一化**：2*acos|dot| 只在两边都是单位四元数时才成立。
      本仓实测踩到 —— Unity 写出的四元数**不是严格单位的**（float32），
      不归一化时「真值 vs 自己」会算出 **0.0544 度**（尺子自证当场红了才发现的）。
      同时把 dot 夹到 <=1，免得 acos(1+eps) 出 NaN。
    """
    if not p or not q or len(p) != len(q):
        raise ValueError(f"四元数长度不对：{len(p) if p else 0} vs {len(q) if q else 0}")
    def _n(v: list[float]) -> float:
        return math.sqrt(sum(x * x for x in v)) or 1.0
    d = sum(x * y for x, y in zip(p, q, strict=True)) / (_n(p) * _n(q))
    return math.degrees(2.0 * math.acos(min(1.0, abs(d))))


def load_bake(path: str | Path) -> dict:
    p = Path(path)
    if not p.is_absolute():
        p = REPO / p
    return json.loads(p.read_text(encoding="utf-8"))


def reference_poses(doc: dict, column: str = "bake") -> list[dict]:
    """真值 = Unity 烘出的那一列（bake），每个时刻一份 {骨名: 四元数}。"""
    out = []
    for s in doc.get("samples") or []:
        if column not in s:
            raise ValueError(f"样本里没有 {column} 列（有：{sorted(s)}）")
        out.append({"time": s.get("time"), "bones": s[column]})
    if not out:
        raise ValueError("bake.json 里没有 samples")
    return out


def compare_poses(candidate: dict, reference: dict, exclude: tuple[str, ...] = ()) -> dict:
    """逐骨测地距离。返回 maxDeg / worstBone / perBone / missing / excluded。"""
    if not candidate:
        raise ValueError("候选位姿是空的 —— ⛔ 缺参数时不许静默返回零")
    skip = set(exclude)
    per: dict[str, float] = {}
    missing: list[str] = []
    excluded: list[str] = []
    worst, worst_bone = -1.0, ""
    for n in sorted(set(candidate) | set(reference)):
        if n in skip:
            excluded.append(n)
            continue
        if n not in candidate or n not in reference:
            missing.append(n)
            continue
        d = geodesic_deg(candidate[n], reference[n])
        per[n] = d
        if d > worst:
            worst, worst_bone = d, n
    return {"maxDeg": max(worst, 0.0), "worstBone": worst_bone, "perBone": per,
            "missing": missing, "excluded": excluded, "nBones": len(per)}


def compare_all(candidate_poses: list[dict], reference_poses_: list[dict],
                exclude: tuple[str, ...] = ()) -> dict:
    """多时刻比：报**最差的那一条**（⛔ 不许只报最好那条）。"""
    rows = []
    for c, r in zip(candidate_poses, reference_poses_, strict=False):
        got = compare_poses(c.get("bones", c), r["bones"], exclude)
        got["time"] = r.get("time")
        rows.append(got)
    if not rows:
        raise ValueError("没有可比对的时刻")
    worst = max(rows, key=lambda x: x["maxDeg"])
    return {"maxDeg": worst["maxDeg"], "worstBone": worst["worstBone"],
            "worstTime": worst.get("time"), "perTime": rows,
            "nTimes": len(rows), "nBones": worst["nBones"]}


def perturb(bones: dict, name: str, delta_deg: float) -> dict:
    """正控用：把那根骨绕一个正交轴转 delta_deg。"""
    q = bones[name]
    half = math.radians(delta_deg) / 2.0
    # 取一个与 q 大致正交的轴，保证转得动
    ax = [1.0, 0.0, 0.0]
    n = math.sqrt(sum(x * x for x in q))
    qn = [x / n for x in q]
    dot = sum(a * b for a, b in zip(ax + [0.0], qn, strict=True))
    ax = [a - dot * b for a, b in zip(ax + [0.0], qn, strict=True)]
    nn = math.sqrt(sum(x * x for x in ax[:3])) or 1.0
    ax = [x / nn for x in ax[:3]]
    s, c = math.sin(half), math.cos(half)
    dq = [ax[0] * s, ax[1] * s, ax[2] * s, c]
    out = dict(bones)
    out[name] = mul(q, dq)
    return out


def mul(a: list[float], b: list[float]) -> list[float]:
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return [aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw,
            aw * bw - ax * bx - ay * by - az * bz]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="甲 的验收尺子（测地距离，度）")
    ap.add_argument("bake_json", help="乙 烘出来的 <stem>.bake.json")
    ap.add_argument("--column", default="bake", help="真值列（默认 bake；对照可用 anim/rest）")
    ap.add_argument("--selftest", action="store_true", help="正控：扰动一根骨，必须变红")
    args = ap.parse_args(argv)

    doc = load_bake(args.bake_json)
    print(f"文件 = {args.bake_json}  unity = {doc.get('unity')}  stem = {doc.get('stem')}")
    refs = reference_poses(doc, args.column)
    print(f"真值列 = {args.column} · {len(refs)} 个时刻 · 每时刻 {len(refs[0]['bones'])} 骨")
    self_cmp = compare_all([r for r in refs], refs)
    print(f"[下限] 真值 vs 自己：maxDeg = {self_cmp['maxDeg']:.3e} 度"
          f"（最差 {self_cmp['worstBone']} @ t={self_cmp['worstTime']}）")
    if args.selftest:
        name = refs[0]["bones"] and sorted(refs[0]["bones"])[0]
        bad = [{"time": r["time"], "bones": perturb(r["bones"], name, 5.0)} for r in refs]
        red = compare_all(bad, refs)
        print(f"[正控] 把 {name} 转 5 度后：maxDeg = {red['maxDeg']:.3f} 度"
              f"（最差 {red['worstBone']}）")
        # ⚠ 自证阈值不能写 1e-9：真值里的四元数是 float32 写出来的，归一化后仍留
        #   **2.4e-06 度**的刻度底噪 —— 那是**真值自身的可复现下限**，不是误差。
        #   判据应当是「底噪远小于目标」：底噪必须 < 1e-5（否则目标根本不可达）。
        floor_ok = self_cmp["maxDeg"] < 1e-5
        ok = red["maxDeg"] > 1.0 and floor_ok
        print(f"  刻度底噪 = {self_cmp['maxDeg']:.3e} 度（< 1e-5 目标？ {floor_ok}）")
        print("VERDICT " + ("尺子能红 OK" if ok
                            else "⛔ 尺子是假的（要么转 5 度没红，要么底噪已超过目标）"))
        return 0 if ok else 1
    print("（接甲：compare_all(甲的位姿, refs) -> maxDeg 必须 <= 1e-5 度）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
