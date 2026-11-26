r"""vt_bake：**一条命令把 N 组源图烘成 VT 池子**（开工程 -> 出 Player -> 捞 5 个文件）。

## 它做什么

    N 组源图（每组 normal + mask）+ Unity 工程
      -> 往工程写 Assets\<work>\（带 [TextureStack._Textures(0/1)] 的 shader + N 组源图 +
         N 颗材质（**同一个 shader**）+ 一个含 N 个 cube 的场景；
         编辑器脚本进 Assets\<work>\Editor\）
      -> **一趟 Unity 会话**（VtBakeOne.Run）按序做完三站：
           ① setup：打开工程级 VT + 对齐 HDRP 的 virtualTexturingSettings（**原值先存盘**）
                     + 写 N 颗材质与场景
           ② build：AssetBundle 趟拿 group/item + BuildPlayer（池子只在出 Player 时烘，kb/18 §6.3）
           ③ restore：按快照复原（写在 \`finally\` 里 —— ② 出了事也照样复原）
      -> 从 <player>_Data\StreamingAssets\ 捞 5 个文件到 --out
      -> 最后删掉 Assets\<work>\、VT 开关回原值、核 Unity 无残留；
         这一趟要是没复原成（死在半路 / 复原本身失败）-> **再起一趟专跑复原**兜底
   打印下一步要用的 group / item（正是 cli/vt.py 的那两个参数）。

## 为什么三站能挤进**一趟**会话（2026-10-09 · 41-45 s -> 20-25 s）

三站原本各起一次 Unity，是因为两条"必须"：

1. **构建那一趟必须「开机就是 VT 开」** —— 引擎读的是**进程启动时**的
   \`Library\EditorOnlyVirtualTextureState.json\`。这一条**不要求另起进程**：本模块在起跑之前
   就把那份状态文件写成 true，于是这一趟的启动值本来就是 true（三站共用同一条要求）；
2. **站与站之间要传的只有两样**：工程里的资产（材质 / 场景 / 源图）与那份原值快照
   —— 同一趟进程里它们本来就在手上，**不需要靠"退出再进来"传递**。

省下的是两次进程启动 + 两次工程加载（实测约 20 秒）。写法：\`VtBakeOne.Run()\` 顺序调
\`VtBakeSetup.SetupBody()\` -> \`VtBakeBuild.BuildBody()\`，\`finally\` 里调
\`VtBakeSetup.RestoreBody()\` —— ⛔ 复原**不是**"成功路径"的一部分。

**安全的另一半（K3）**：一趟会话死在半路（超时被杀 / 崩溃 / 复原本身失败）时，
python 侧看读数就知道"复原那一站没写成" ⇒ **再起一趟专跑 \`VtBakeSetup.Restore\`** 兜底；
Library 状态文件由 python 侧无条件写回（它不依赖编辑器）。
⇒ 正常路径 **1 个进程**，出事路径 **2 个进程**，**任何路径下工程都必须被复原**。

**两条路共用同一套站内主体**（\`SetupBody\` / \`BuildBody\` / \`RestoreBody\`）：
\`--split\`（三趟，A/B 对照与排障）与 \`--only setup|build|restore\`（单站）走的是一套，
三合一走的 \`run_merged()\` 调的是同一批方法 —— 不存在"两套逻辑"（AGENTS §5）。

## 为什么一次要烘 N 颗材质（「整车一套皮肤」的地基）

一辆车用**多颗材质**（实测 T-90M：RU_T90M 1 管车体壳、RU_T72B 1 管反应装甲块与炮塔；
履带 / 高机是 2D 件）。而**一个 group 只有一套池子**（<group>.gts/.gtp 同名）——
同名文件互相覆盖，所以"一次烘一颗材质"会把上一颗的 item **挤掉**（kb/18 §6.3）。

**group 与 item 是两件事**（kb/18 §6.3，三次烘焙实测）：group = 池子容器的身份
（同一套"两层纹理栈"的 shader 烘出来就同组，**跨工程也相同**）；item = 那一轮那两张源图的身份。
⇒ 本模块把 N 组源图写成**同一个 shader 上的 N 颗材质**，**同一次构建**里引擎就会把它们写成
**同一个 group 下的 N 个 item** —— 游戏自己那个组里 355 个 item 就是这么来的。

N=1 时行为与"只烘一颗"逐字相同（工程里的文件名 / 读数 key / 判据都不变）：向后兼容是硬要求。

## 为什么是「源图驱动」

编辑器认的是**创作语义**：材质上带 [TextureStack._Textures(n)] 的属性 + 层上有**源贴图**
=> 构建时由编辑器内的 Granite 构建器烘成池子。反过来不成立：把**别人烘好的**池子
放进工程 Assets/StreamingAssets\ 再让材质指过去，编辑器只报
Texture Stack incomplete: no texture at layer N（kb/18 §6.3「闸C」实测）。

## 判据（每条都能红）

* **J1 产物**：--out 下 <group>.gts(1 MiB) + <group>0_1/2/3/4.gtp **5 个**，
  且实时打印的 group == .gts 的文件名前缀（两处独立来源对得上才印出来）；
* **J2 可复现**：同一组源图连跑两次 -> group / item 逐字相同；
* **J3 能红**：源图不存在 / 不是能解码的图 / N 与 M 不对称（给了 2 个 --normal 只给 1 个 --mask）
  -> 抛 AssetError（CLI 退出码 2），且**一个池子文件都不产出**
  （前置检查先于任何目录创建与进程启动）；
* **J4 收尾**：Assets\<work>\ 清掉、VT 开关回原值、Get-Process Unity = 0；
* **J5 多颗材质**：N 组 (normal, mask) -> **一个池子**（同一个 group 的那几个 .gts/.gtp）
  + **N 个 item**，每个 item 都取自"该材质在 AssetBundle 读回时写的 itemName"；
  N>1 时这 N 个 item 必须**两两不同**（不同才说明没有互相覆盖）。

不认 batchmode 的退出码 —— 判据看**产物** + 读数文件里的 VERDICT（kb/14 §6）；
日志里出现 error CS 一律判失败（= executeMethod 根本没跑）。

## 两个硬事实（照抄装置里的坑，别重踩）

1. **VT 开关要「另一个编辑器会话」才生效**：PlayerSettings.SetVirtualTexturingSupportEnabled(true)
   在本会话里改了，构建那一趟仍按 Library\EditorOnlyVirtualTextureState.json 的**启动值**走
   （VtPlayerBuild.cs 的原话：「先用 VtPlayerSetup.Run 打开，需要**另一个**编辑器会话」）。
   所以这里是「会话 A 开 -> 写状态文件 -> 会话 B 构建」两趟。
2. **AssetBundle 那一趟会把**构建产物**里栈层的源贴图引用清空**（kb/18 §6.3 实测）。
   本模块在出 Player 之前把源图**重新钉回**项目材质，免得第二跳烘不出东西。

不写游戏目录、不读数据库、不动别人的 Assets/VTPlayer/**（只照抄它的形状）。
"""

from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from ba_mod_maker.core.errors import AssetError

# --------------------------------------------------------------------------- #
# 口径常量
# --------------------------------------------------------------------------- #
#: 工程里的工作子目录（只碰自己这一个）
WORK_SUBDIR = "VtBake"
#: 池子文件后缀：Granite 的页表（.gts）与瓦片页（.gtp）
POOL_SUFFIXES = (".gts", ".gtp")
#: 一组的池子形状：1 个 .gts + 4 个 .gtp（0_1 / 2 / 3 / 4）——
#: kb/18 §6.2（游戏的组）与 docs/设计-材质完全自定义.md §2.6（自烘的组）两次实测同形。
POOL_SHARDS = ("", "0_1", "2", "3", "4")
#: 小组页表的固定大小（1 MiB）—— 两次实测都是它
GTS_BYTES = 1_048_576
#: 编辑器算出来的名字长度（32 位 hex 小写）
NAME_LEN = 32
#: 读数文件里的判据句（照装置的口径：判 VERDICT，不判退出码）
VERDICT_SETUP = "VERDICT setup-ok"
VERDICT_BAKE = "VERDICT bake-ok"
VERDICT_RESTORE = "VERDICT restore-ok"
#: 杀掉超时进程树之后，等它真的退出的宽限秒数
KILL_GRACE = 60.0
#: 默认的 Unity 编辑器 —— **与游戏同代**（1.2.1 = Unity 6000.3.24f1；用户 2026-10-17
#: 「默认值写新代」，kb/42 §4 C13）。旧代 2022.3.62f3c1 只留给历史对拍件，⛔ 不再是默认值：
#: 拿 2022.3 的编辑器去开 Unity 6 的尺子工程必然失败。
DEFAULT_UNITY = Path(r"D:\Unity\6000.3.24f1\Editor\Unity.exe")
#: 默认的 Player 落点（**必须 ASCII**：非 ASCII 路径下 Granite 打不开烘好的池子，
#: tools/unity_vtplayer.py 文件头那条 A/B 实测）
DEFAULT_PLAYER_DIR = Path(os.environ.get("SYSTEMDRIVE", "C:") + "\\vtbake_player")

#: 工程里的相对路径（都是**临时件**，收尾整体删掉）
SHADER_REL = f"Assets/{WORK_SUBDIR}/VtBake.shader"
MATERIAL_REL = f"Assets/{WORK_SUBDIR}/VtBake.mat"
SCENE_REL = f"Assets/{WORK_SUBDIR}/VtBakeScene.unity"
SETUP_CS_REL = f"Assets/{WORK_SUBDIR}/Editor/VtBakeSetup.cs"
BUILD_CS_REL = f"Assets/{WORK_SUBDIR}/Editor/VtBakeBuild.cs"
#: 三合一那一趟的编辑器脚本（它调的是上面两颗的**站内主体**，不是第二套逻辑）
ONE_CS_REL = f"Assets/{WORK_SUBDIR}/Editor/VtBakeOne.cs"
SOURCE_NORMAL_NAME = "src_normal.png"
SOURCE_MASK_NAME = "src_mask.png"
#: 材质与场景的固定名（第 i 颗材质 = 词干 + 下标，见下面 material_name / source_name）。
#: 第 0 颗**沿用原来的名字**，所以 N=1 时工程里的文件集合与从前逐字相同。
MATERIAL_NAME = "VtBake.mat"
SCENE_NAME = "VtBakeScene.unity"
#: 材质颗数怎么告诉编辑器侧（VtBakeSetup.cs / VtBakeBuild.cs 读它）
SOURCE_COUNT_ENV = "BAVTB_SOURCES"

#: 三合一那一趟的入口（一个 -executeMethod 里按序做完 PHASES）与它的会话标识
METHOD_ONE = "VtBakeOne.Run"
SESSION_ONE = "one"
#: 三合一的相位顺序（⛔ 与 C# 里 VtBakeOne.Run 的调用顺序同一套）
PHASES = ("setup", "build", "restore")
#: 站 -> 它该写出的 VERDICT 行（判据看它 + 读数 mtime，⛔ 不认 batchmode 退出码）
EXPECT_VERDICT = {
    "setup": "VERDICT setup-ok",
    "build": "VERDICT bake-ok",
    "restore": "VERDICT restore-ok",
}

#: 会话 -> 读数文件（编辑器侧按这个名字落盘）
READING_NAMES = {
    "setup": "vtbake-setup.txt",
    "build": "vtbake-build.txt",
    "restore": "vtbake-restore.txt",
}
#: 会话 -> 编辑器日志（"one" = 三合一那一趟，一趟一份日志）
LOG_NAMES = {
    "setup": "unity-setup.log",
    "build": "unity-build.log",
    "restore": "unity-restore.log",
    SESSION_ONE: "unity-one.log",
}
#: 会话 -> 要调用的编辑器方法（"一次进程一站"那一套；--split 与 --only 用它）
METHODS = {
    "setup": "VtBakeSetup.Run",
    "build": "VtBakeBuild.Run",
    "restore": "VtBakeSetup.Restore",
}


def _indexed(stem: str, index: int) -> str:
    """第 index 份的名字：词干.png -> 词干<index>.png；index=0 原样返回。"""
    if index == 0:
        return stem
    base, dot, ext = stem.partition(".")
    return f"{base}{index}{dot}{ext}"


def source_name(index: int, layer: str) -> str:
    """第 index 组源图在工程里的文件名（normal / mask）。⛔ 规则要与 C# 侧同一套。"""
    if layer not in ("normal", "mask"):
        raise ValueError(f"layer 只认 normal / mask：{layer!r}")
    return _indexed(SOURCE_NORMAL_NAME if layer == "normal" else SOURCE_MASK_NAME, index)


def material_name(index: int) -> str:
    """第 index 颗材质在工程里的文件名（第 0 颗 = VtBake.mat）。"""
    return _indexed(MATERIAL_NAME, index)


def material_stem(index: int) -> str:
    """材质的**资产名**（不带扩展名）—— AssetBundle 读回来时它的 m.name 就是它。"""
    return Path(material_name(index)).stem

#: 源驱动那一半全靠这颗 shader 上的 [TextureStack._Textures(n)]（形状照 BaVtProbe3.cs）。
SHADER_SOURCE = """\
// VtBake.shader —— 由 ba-mod-maker vt-bake 生成（临时件，收尾会被删）
// 它只有一件事要做：把 _Layer0 / _Layer1 声明成**纹理栈的源图**。
// 编辑器在「出 Player」时会按这个声明把源图烘成 <group>.gts + <group>0_1/2/3/4.gtp。
Shader "VtBake/LayerSrc"
{
    Properties
    {
        [NoScaleOffset] _MainAlbedo ("Albedo", 2D) = "white" {}
        [TextureStack._Textures(0)] [NoScaleOffset] _Layer0 ("Layer 0 (Normal)", 2D) = "white" {}
        [TextureStack._Textures(1)] [NoScaleOffset] _Layer1 ("Layer 1 (Mask)", 2D) = "white" {}
    }
    SubShader
    {
        Tags { "RenderType" = "Opaque" }
        Pass
        {
            CGPROGRAM
            #pragma vertex vert
            #pragma fragment frag
            #include "UnityCG.cginc"
            struct appdata { float4 vertex : POSITION; float2 uv : TEXCOORD0; };
            struct v2f { float4 pos : SV_POSITION; float2 uv : TEXCOORD0; };
            sampler2D _MainAlbedo;
            v2f vert (appdata v)
            {
                v2f o;
                o.pos = UnityObjectToClipPos(v.vertex);
                o.uv = v.uv;
                return o;
            }
            fixed4 frag (v2f i) : SV_Target { return tex2D(_MainAlbedo, i.uv); }
            ENDCG
        }
    }
}
"""


# --------------------------------------------------------------------------- #
# 编辑器脚本（**临时件**，写进工程 Assets/<work>/Editor/；照抄 VtPlayerSetup 的形状）
# --------------------------------------------------------------------------- #
EDITOR_SETUP_SOURCE = r'''// VtBakeSetup.cs —— 「源图驱动」那一半：开 VT + 对齐 HDRP + 写材质/场景
// 由 ba-mod-maker vt-bake 生成（临时件，收尾会被删）。
// 形状照 unity-armory/Assets/VTPlayer/Editor/VtPlayerSetup.cs（那份是 VT 的观测装置）。
using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

[Serializable]
public class VtbGpuEntry { public int format; public uint sizeMB; }

[Serializable]
public class VtbHdrpSnapshot
{
    public string path;
    public int cpuMB;
    public int mipPreload;
    public int preloadMip;
    public List<VtbGpuEntry> gpu = new List<VtbGpuEntry>();
}

[Serializable]
public class VtbSnapshot
{
    public bool vtEnabled;
    public List<VtbHdrpSnapshot> hdrp = new List<VtbHdrpSnapshot>();
}

public static class VtBakeSetup
{
    public const string Dir = "Assets/VtBake";
    public const string ShaderPath = Dir + "/VtBake.shader";
    public const string ScenePath = Dir + "/VtBakeScene.unity";
    const string SnapName = "project-state-before.json";

    // 一次烘几颗材质（python 侧用 BAVTB_SOURCES 告诉我们）。
    // ⛔ 名字规则必须与 python 侧的 source_name / material_name **同一套**：
    //    第 0 组 = 原来的名字，第 i 组 = 词干 + i。
    static int Sources()
    {
        int n;
        if (int.TryParse(Environment.GetEnvironmentVariable("BAVTB_SOURCES"), out n) && n > 0)
            return n;
        return 1;
    }

    static string Suf(int i) { return i == 0 ? "" : i.ToString(); }
    static string SrcNormal(int i) { return Dir + "/src_normal" + Suf(i) + ".png"; }
    static string SrcMask(int i) { return Dir + "/src_mask" + Suf(i) + ".png"; }
    static string MatPath(int i) { return Dir + "/VtBake" + Suf(i) + ".mat"; }

    static readonly StringBuilder L = new StringBuilder();
    static void Say(string s) { L.AppendLine(s); Debug.Log("[VTB-setup] " + s); }

    // 这一站花了多少秒 —— 三合一时三站共用**一趟**进程，分段耗时只能由各自记进读数
    // （python 侧读 key = seconds；用 InvariantCulture 免得某些区域设置写成逗号）
    static string Seconds(System.Diagnostics.Stopwatch sw)
    {
        return "seconds=" + sw.Elapsed.TotalSeconds.ToString(
            "0.0", System.Globalization.CultureInfo.InvariantCulture);
    }

    static string OutDir()
    {
        string p = Environment.GetEnvironmentVariable("BAVTB_OUTDIR");
        if (string.IsNullOrEmpty(p))
            p = Path.Combine(Directory.GetCurrentDirectory(), "_work", "vtbake");
        return p;
    }

    static void Flush(string name)
    {
        try
        {
            string d = OutDir();
            Directory.CreateDirectory(d);
            File.WriteAllText(Path.Combine(d, name), L.ToString(), new UTF8Encoding(false));
        }
        catch (Exception e) { Debug.LogError("[VTB-setup] 写读数失败 " + e.Message); }
    }

    static string Desc(UnityEngine.Object o)
    {
        var t = o as Texture2D;
        if (t != null) return t.name + " " + t.width + "x" + t.height + " fmt=" + t.format;
        return o == null ? "(null)" : o.name;
    }

    // GraphicsFormat 的成员值：**不猜命名空间** —— 直接从 GPUCacheSetting.format 的字段类型拿
    // （这个枚举在托管侧的命名空间各版本不一样，写死字符串会静默返回 -1）
    static Type GfxFormatType()
    {
        try
        {
            var ft = typeof(UnityEngine.Rendering.VirtualTexturing.GPUCacheSetting);
            var f = ft.GetField("format");
            if (f != null && f.FieldType.IsEnum) return f.FieldType;
        }
        catch { }
        foreach (var asm in AppDomain.CurrentDomain.GetAssemblies())
        {
            Type[] ts;
            try { ts = asm.GetTypes(); } catch { continue; }
            foreach (var t in ts)
            {
                if (t == null || !t.IsEnum || t.Name != "GraphicsFormat") continue;
                if (Array.IndexOf(Enum.GetNames(t), "BC3_SRGB") >= 0) return t;
            }
        }
        return null;
    }

    static int GfxFormatValue(Type t, string[] members, out string hit)
    {
        hit = null;
        if (t == null) return -1;
        foreach (var m in members)
        {
            try { var v = Convert.ToInt32(Enum.Parse(t, m)); hit = m; return v; }
            catch { }
        }
        return -1;
    }

    static string Describe(SerializedProperty gpuProp)
    {
        var parts = new List<string>();
        if (gpuProp == null) return "(没有这个字段)";
        for (int i = 0; i < gpuProp.arraySize; i++)
        {
            var e = gpuProp.GetArrayElementAtIndex(i);
            var f = e.FindPropertyRelative("format");
            var sz = e.FindPropertyRelative("sizeInMegaBytes");
            string nm = "(?)";
            try
            {
                if (f.enumDisplayNames != null && f.enumValueIndex >= 0
                    && f.enumValueIndex < f.enumDisplayNames.Length)
                    nm = f.enumDisplayNames[f.enumValueIndex];
            }
            catch { }
            parts.Add("{" + nm + "(int=" + f.intValue + ")," + sz.intValue + "MiB}");
        }
        return string.Join(" + ", parts.ToArray());
    }

    static readonly string[] SrgbNames = { "BC3_SRGB", "RGBA_DXT5_SRGB", "DXT5_SRGB" };
    static readonly string[] UnormNames = { "BC3_UNorm", "RGBA_DXT5_UNorm", "DXT5_UNorm" };

    // 站内主体：干活 + 写读数，**不退出**（三合一那一趟要接着跑下一站，见 VtBakeOne.cs）。
    public static void SetupBody()
    {
        L.Length = 0;      // 一趟会话里三站共用这个缓冲：每站从空开始（读数才是"这一站"的）
        var sw = System.Diagnostics.Stopwatch.StartNew();
        try
        {
            Say("=== VtBakeSetup · 开 VT + 对齐 HDRP + 写材质/场景 ===");
            Say("unity   " + Application.unityVersion);
            Say("project " + Directory.GetParent(Application.dataPath).FullName);
            Say("colorSpace = " + PlayerSettings.colorSpace
                + " target = " + EditorUserBuildSettings.activeBuildTarget);

            bool vtWas = PlayerSettings.GetVirtualTexturingSupportEnabled();
            var snap = new VtbSnapshot { vtEnabled = vtWas };
            Say("");
            Say("--- 0. 原值 ---");
            Say("PlayerSettings.VirtualTexturingSupportEnabled(原) = " + vtWas);

            Say("");
            Say("--- 1. 打开工程级 VT ---");
            PlayerSettings.SetVirtualTexturingSupportEnabled(true);
            Say("SetVirtualTexturingSupportEnabled(true) -> 读回 = "
                + PlayerSettings.GetVirtualTexturingSupportEnabled());
            AssetDatabase.SaveAssets();

            Say("");
            Say("--- 2. 对齐 HDRP Asset 的 virtualTexturingSettings ---");
            var fmt = GfxFormatType();
            Say("GPUCacheSetting.format 的字段类型 = "
                + (fmt == null ? "(拿不到)" : fmt.FullName));
            string nameS, nameU;
            int bc3s = GfxFormatValue(fmt, SrgbNames, out nameS);
            int bc3u = GfxFormatValue(fmt, UnormNames, out nameU);
            Say("选中的成员：SRGB=" + nameS + "(" + bc3s + ")  UNorm=" + nameU + "(" + bc3u + ")");
            if (bc3s < 0 || bc3u < 0)
            {
                Say("**拿不到 GraphicsFormat 的成员值，放弃对齐（不许猜数字）**");
                Say("VERDICT setup-fail");
                return;
            }
            try
            {
                var helpers = typeof(UnityEngine.Rendering.VirtualTexturing.EditorHelpers);
                var sup = UnityEngine.Rendering.VirtualTexturing.EditorHelpers
                    .QuerySupportedFormats();
                var supInts = new HashSet<int>();
                foreach (var s in sup) supInts.Add(Convert.ToInt32(s));
                Say("VT 支持格式 " + sup.Length + " 种 · 含 " + bc3s + " = "
                    + supInts.Contains(bc3s) + " · 含 " + bc3u + " = " + supInts.Contains(bc3u));
                var tp = helpers.GetProperty("tileSize",
                    System.Reflection.BindingFlags.Static
                    | System.Reflection.BindingFlags.Public
                    | System.Reflection.BindingFlags.NonPublic);
                Say("tileSize = " + tp.GetValue(null));
            }
            catch (Exception ex) { Say("EditorHelpers EX " + ex.Message); }

            foreach (string guid in AssetDatabase.FindAssets("t:HDRenderPipelineAsset"))
            {
                string path = AssetDatabase.GUIDToAssetPath(guid);
                var asset = AssetDatabase.LoadAssetAtPath<UnityEngine.Object>(path);
                if (asset == null) { Say("  " + path + " : 读不到，跳过"); continue; }
                var so = new SerializedObject(asset);
                var root = so.FindProperty("virtualTexturingSettings");
                if (root == null)
                {
                    Say("  " + path + " : 没有 virtualTexturingSettings 字段，跳过");
                    continue;
                }
                var relCpu = root.FindPropertyRelative("streamingCpuCacheSizeInMegaBytes");
                var relMip = root.FindPropertyRelative("streamingMipPreloadTexturesPerFrame");
                var relPre = root.FindPropertyRelative("streamingPreloadMipCount");
                var gpu0 = root.FindPropertyRelative("streamingGpuCacheSettings");
                var before = new VtbHdrpSnapshot { path = path };
                before.cpuMB = relCpu.intValue;
                before.mipPreload = relMip.intValue;
                before.preloadMip = relPre.intValue;
                for (int i = 0; i < gpu0.arraySize; i++)
                {
                    var e = gpu0.GetArrayElementAtIndex(i);
                    var ef = e.FindPropertyRelative("format");
                    var es = e.FindPropertyRelative("sizeInMegaBytes");
                    before.gpu.Add(new VtbGpuEntry
                    {
                        format = ef.intValue,
                        sizeMB = (uint)es.intValue,
                    });
                }
                snap.hdrp.Add(before);
                Say("  " + path);
                Say("     改前: cpuMB=" + before.cpuMB + " gpu=" + Describe(gpu0));
                relCpu.intValue = 256;
                relMip.intValue = 0;
                relPre.intValue = 1;
                gpu0.arraySize = 2;
                var e0 = gpu0.GetArrayElementAtIndex(0);
                e0.FindPropertyRelative("format").intValue = bc3s;
                e0.FindPropertyRelative("sizeInMegaBytes").intValue = 128;
                var e1 = gpu0.GetArrayElementAtIndex(1);
                e1.FindPropertyRelative("format").intValue = bc3u;
                e1.FindPropertyRelative("sizeInMegaBytes").intValue = 32;
                so.ApplyModifiedPropertiesWithoutUndo();
                EditorUtility.SetDirty(asset);
                var again = AssetDatabase.LoadAssetAtPath<UnityEngine.Object>(path);
                var so2 = new SerializedObject(again);
                var root2 = so2.FindProperty("virtualTexturingSettings");
                Say("     改后: cpuMB="
                    + root2.FindPropertyRelative("streamingCpuCacheSizeInMegaBytes").intValue
                    + " gpu="
                    + Describe(root2.FindPropertyRelative("streamingGpuCacheSettings")));
            }
            AssetDatabase.SaveAssets();

            string snapPath = Path.Combine(OutDir(), SnapName);
            if (File.Exists(snapPath)) Say("原值快照**已存在**，不覆盖：" + snapPath);
            else
            {
                Directory.CreateDirectory(OutDir());
                string snapJson = JsonUtility.ToJson(snap, true);
                File.WriteAllText(snapPath, snapJson, new UTF8Encoding(false));
                Say("原值已存 " + snapPath);
            }

            Say("");
            Say("--- 3. 源图 / shader / N 颗材质 ---");
            AssetDatabase.Refresh();
            int n = Sources();
            Say("材质颗数（BAVTB_SOURCES）= " + n);
            var sh = AssetDatabase.LoadAssetAtPath<Shader>(ShaderPath);
            if (sh == null)
            {
                Say("shader " + ShaderPath + " = **读不到**");
                Say("**缺 shader，放弃**");
                Say("VERDICT setup-fail");
                return;
            }
            Say("shader " + ShaderPath + " = " + sh.name
                + " isSupported=" + sh.isSupported);
            // N 颗材质**共用这一颗 shader** 是故意的：group 由栈配置决定（kb/18 §6.3），
            // 只有共用 shader 才会烘进同一个 group、同一个池子。
            var mats = new List<Material>();
            for (int i = 0; i < n; i++)
            {
                var texI = AssetDatabase.LoadAssetAtPath<Texture2D>(SrcNormal(i));
                var mskI = AssetDatabase.LoadAssetAtPath<Texture2D>(SrcMask(i));
                Say("组[" + i + "] normal = " + Desc(texI));
                Say("组[" + i + "] mask   = " + Desc(mskI));
                if (texI == null || mskI == null)
                {
                    Say("**缺源图（组 " + i + "），放弃**");
                    Say("VERDICT setup-fail");
                    return;
                }
                string mp = MatPath(i);
                var one = AssetDatabase.LoadAssetAtPath<Material>(mp);
                if (one == null)
                {
                    one = new Material(sh);
                    AssetDatabase.CreateAsset(one, mp);
                    Say("  材质已建 " + mp);
                }
                else Say("  材质已存在 " + mp + "（复用）");
                one.shader = sh;
                one.SetTexture("_Layer0", texI);
                one.SetTexture("_Layer1", mskI);
                EditorUtility.SetDirty(one);
                mats.Add(one);
                Say("  材质 " + mp + " _Layer0=" + Desc(one.GetTexture("_Layer0"))
                    + " _Layer1=" + Desc(one.GetTexture("_Layer1")));
            }
            AssetDatabase.SaveAssets();
            int stacks = 0;
            for (int i = 0; i < sh.GetPropertyCount(); i++)
            {
                string sn;
                int li;
                try
                {
                    if (!sh.FindTextureStack(i, out sn, out li)) continue;
                    stacks++;
                    Say("  prop[" + i + "] " + sh.GetPropertyName(i)
                        + " -> stack=" + sn + " layer=" + li);
                }
                catch (Exception ex) { Say("  FindTextureStack EX " + ex.Message); }
            }
            Say("shader 上的纹理栈属性 = " + stacks + " 条（要 2 条：_Layer0 / _Layer1）");

            Say("");
            Say("--- 4. 构建用场景（" + mats.Count + " 个 cube，一颗材质一个）---");
            // 场景**每次都重建**：N 颗材质要 N 个 cube，留着上一轮那个单 cube 的旧场景
            // 会让多出来的材质根本不进构建（VT 构建器只烘构建里真的用到的栈）。
            var sc = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
            for (int i = 0; i < mats.Count; i++)
            {
                var cube = GameObject.CreatePrimitive(PrimitiveType.Cube);
                cube.name = "vtb-cube-" + i;
                cube.transform.position = new Vector3(i * 2.5f, 0f, 0f);
                cube.GetComponent<Renderer>().sharedMaterial = mats[i];
            }
            bool saved = EditorSceneManager.SaveScene(sc, ScenePath);
            Say("建场景 " + ScenePath + " = " + saved + " · cube " + mats.Count + " 个");
            Say("");
            Say("VERDICT setup-ok");
        }
        catch (Exception e) { Say("**异常** " + e); }
        finally { Say(Seconds(sw)); Flush("vtbake-setup.txt"); }
    }

    // 单站入口（--split / --only setup 用）：跑完这一站就退出。
    public static void Run()
    {
        SetupBody();
        EditorApplication.Exit(0);
    }

    // 站内主体：按快照复原，**不退出**（三合一那一趟的最后一步就是它；也是兜底那一趟）。
    public static void RestoreBody()
    {
        L.Length = 0;      // 一趟会话里三站共用这个缓冲：每站从空开始（读数才是"这一站"的）
        var sw = System.Diagnostics.Stopwatch.StartNew();
        try
        {
            Say("=== VtBakeSetup · 复原 ===");
            string snapPath = Path.Combine(OutDir(), SnapName);
            if (!File.Exists(snapPath))
            {
                Say("**没有原值快照 " + snapPath + "，只把 VT 关回去**");
                PlayerSettings.SetVirtualTexturingSupportEnabled(false);
                Say("VT -> " + PlayerSettings.GetVirtualTexturingSupportEnabled());
                Say("VERDICT restore-ok");
                return;
            }
            var snap = JsonUtility.FromJson<VtbSnapshot>(File.ReadAllText(snapPath));
            Say("原值: vtEnabled=" + snap.vtEnabled + " · HDRP asset " + snap.hdrp.Count + " 个");
            foreach (var h in snap.hdrp)
            {
                var asset = AssetDatabase.LoadAssetAtPath<UnityEngine.Object>(h.path);
                if (asset == null) { Say("  " + h.path + " : 读不到，跳过"); continue; }
                var so = new SerializedObject(asset);
                var root = so.FindProperty("virtualTexturingSettings");
                if (root == null) continue;
                root.FindPropertyRelative("streamingCpuCacheSizeInMegaBytes").intValue = h.cpuMB;
                root.FindPropertyRelative("streamingMipPreloadTexturesPerFrame").intValue
                    = h.mipPreload;
                root.FindPropertyRelative("streamingPreloadMipCount").intValue = h.preloadMip;
                var gpu = root.FindPropertyRelative("streamingGpuCacheSettings");
                gpu.arraySize = h.gpu.Count;
                for (int i = 0; i < h.gpu.Count; i++)
                {
                    var e = gpu.GetArrayElementAtIndex(i);
                    e.FindPropertyRelative("format").intValue = h.gpu[i].format;
                    e.FindPropertyRelative("sizeInMegaBytes").intValue = (int)h.gpu[i].sizeMB;
                }
                so.ApplyModifiedPropertiesWithoutUndo();
                EditorUtility.SetDirty(asset);
                var so2 = new SerializedObject(asset);
                var vts = so2.FindProperty("virtualTexturingSettings");
                Say("  复原 " + h.path + " -> cpuMB="
                    + vts.FindPropertyRelative("streamingCpuCacheSizeInMegaBytes").intValue
                    + " gpu=" + Describe(vts.FindPropertyRelative("streamingGpuCacheSettings")));
            }
            AssetDatabase.SaveAssets();
            PlayerSettings.SetVirtualTexturingSupportEnabled(snap.vtEnabled);
            AssetDatabase.SaveAssets();
            Say("VT 开关恢复 -> " + PlayerSettings.GetVirtualTexturingSupportEnabled());
            Say("VERDICT restore-ok");
        }
        catch (Exception e) { Say("**异常** " + e); }
        finally { Say(Seconds(sw)); Flush("vtbake-restore.txt"); }
    }

    // 单站入口（--only restore，以及"三合一那一趟没复原成"时的兜底那一趟）。
    public static void Restore()
    {
        RestoreBody();
        EditorApplication.Exit(0);
    }
}
'''

EDITOR_BUILD_SOURCE = r'''// VtBakeBuild.cs —— 出 Player（池子**只在出 Player 时**烘，kb/18 §6.3）
// 由 ba-mod-maker vt-bake 生成（临时件，收尾会被删）。
//
// 两跳：
//   ① AssetBundle 那一趟 —— groupName / itemName 是**构建期**由编辑器自己算出来写进材质的，
//      这是不读 Player 数据就能拿到这两个名字的唯一地方（BaVtProbe3.cs 实测）。
//   ② BuildPlayer —— 池子（<group>.gts + <group>0_1/2/3/4.gtp）落进
//      <player>_Data\StreamingAssets\。
// 判据：① 的 group 必须与 ② 的 .gts 文件名**逐字相同**（两处独立来源），否则 VERDICT bake-fail。
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text;
using UnityEditor;
using UnityEditor.Build.Reporting;
using UnityEngine;

public static class VtBakeBuild
{
    const string Dir = "Assets/VtBake";
    const string ScenePath = Dir + "/VtBakeScene.unity";

    // 一次烘几颗材质（python 侧用 BAVTB_SOURCES 告诉我们）。
    // ⛔ 名字规则必须与 python 侧的 source_name / material_name **同一套**：
    //    第 0 组 = 原来的名字，第 i 组 = 词干 + i。
    static int Sources()
    {
        int n;
        if (int.TryParse(Env("BAVTB_SOURCES"), out n) && n > 0) return n;
        return 1;
    }

    static string Suf(int i) { return i == 0 ? "" : i.ToString(); }
    static string MatName(int i) { return "VtBake" + Suf(i); }
    static string MatPath(int i) { return Dir + "/" + MatName(i) + ".mat"; }
    static string SrcNormal(int i) { return Dir + "/src_normal" + Suf(i) + ".png"; }
    static string SrcMask(int i) { return Dir + "/src_mask" + Suf(i) + ".png"; }

    static readonly StringBuilder L = new StringBuilder();
    static void Say(string s) { L.AppendLine(s); Debug.Log("[VTB-build] " + s); }
    static string Env(string n) { return Environment.GetEnvironmentVariable(n); }

    // 这一站花了多少秒 —— 三合一时三站共用**一趟**进程，分段耗时只能由各自记进读数
    static string Seconds(System.Diagnostics.Stopwatch sw)
    {
        return "seconds=" + sw.Elapsed.TotalSeconds.ToString(
            "0.0", System.Globalization.CultureInfo.InvariantCulture);
    }

    static string OutDir()
    {
        string p = Env("BAVTB_OUTDIR");
        if (string.IsNullOrEmpty(p))
            p = Path.Combine(Directory.GetCurrentDirectory(), "_work", "vtbake");
        return p;
    }

    static void Flush()
    {
        try
        {
            string d = OutDir();
            Directory.CreateDirectory(d);
            string text = L.ToString();
            File.WriteAllText(Path.Combine(d, "vtbake-build.txt"), text, new UTF8Encoding(false));
        }
        catch (Exception e) { Debug.LogError("[VTB-build] 写读数失败 " + e.Message); }
    }

    static string Show(string s) { return string.IsNullOrEmpty(s) ? "(没拿到)" : s; }

    static int CountDistinct(string[] values)
    {
        var set = new HashSet<string>();
        foreach (var v in values)
            if (!string.IsNullOrEmpty(v)) set.Add(v);
        return set.Count;
    }

    static string Stacks(Material m, out string group, out string item)
    {
        group = null;
        item = null;
        var so = new SerializedObject(m);
        var bs = so.FindProperty("m_BuildTextureStacks");
        if (bs == null) return "(没有 m_BuildTextureStacks 字段)";
        var parts = new List<string>();
        for (int i = 0; i < bs.arraySize; i++)
        {
            var e = bs.GetArrayElementAtIndex(i);
            string g = e.FindPropertyRelative("groupName").stringValue;
            string it = e.FindPropertyRelative("itemName").stringValue;
            parts.Add("[" + i + "] group=" + g + " item=" + it);
            if (i == 0) { group = g; item = it; }
        }
        return parts.Count == 0 ? "(空表 —— 构建器没写)" : string.Join(" ", parts.ToArray());
    }

    static string TileSize()
    {
        try
        {
            var t = typeof(UnityEngine.Rendering.VirtualTexturing.EditorHelpers);
            var p = t.GetProperty("tileSize",
                BindingFlags.Static | BindingFlags.Public | BindingFlags.NonPublic);
            return p.GetValue(null).ToString();
        }
        catch (Exception e) { return "EX " + e.Message; }
    }

    // 站内主体：干活 + 写读数，**不退出**（三合一那一趟要接着跑下一站，见 VtBakeOne.cs）。
    public static void BuildBody()
    {
        L.Length = 0;      // 一趟会话里三站共用这个缓冲：每站从空开始（读数才是"这一站"的）
        var sw = System.Diagnostics.Stopwatch.StartNew();
        bool ok = false;
        int n = 1;
        // 第 i 颗材质在 AssetBundle 读回时写下的 group / item（**一颗一个 item**）
        string[] groupB = new string[1];
        string[] itemB = new string[1];
        string groupPool = null;
        try
        {
            string exe = Env("BAVTB_PLAYER_EXE");
            string bundleDir = Env("BAVTB_BUNDLE_DIR");
            Say("=== VtBakeBuild ===");
            Say("unity    " + Application.unityVersion);
            Say("VT flag  " + PlayerSettings.GetVirtualTexturingSupportEnabled());
            Say("tileSize " + TileSize());
            Say("player   " + exe);
            Say("场景 " + ScenePath + " 存在=" + File.Exists(ScenePath));
            n = Sources();
            groupB = new string[n];
            itemB = new string[n];
            Say("材质颗数（BAVTB_SOURCES）= " + n);
            for (int i = 0; i < n; i++)
                Say("材质[" + i + "] " + MatPath(i) + " 存在=" + File.Exists(MatPath(i)));
            AssetDatabase.Refresh();
            var mats = new List<Material>();
            for (int i = 0; i < n; i++)
            {
                var one = AssetDatabase.LoadAssetAtPath<Material>(MatPath(i));
                if (one == null)
                {
                    Say("**没有材质 " + MatPath(i) + " —— 先跑 VtBakeSetup.Run**");
                    return;
                }
                mats.Add(one);
                if (one.shader == null) Say("材质[" + i + "] shader = null");
                else Say("材质[" + i + "] " + MatPath(i) + " shader = " + one.shader.name
                         + " isSupported=" + one.shader.isSupported);
            }

            Say("");
            Say("--- 1. AssetBundle 那一趟：拿构建期算出来的 group / item ---");
            if (string.IsNullOrEmpty(bundleDir)) Say("（没给 BAVTB_BUNDLE_DIR，跳过）");
            else
            {
                try
                {
                    if (Directory.Exists(bundleDir)) Directory.Delete(bundleDir, true);
                    Directory.CreateDirectory(bundleDir);
                    var matAssets = new string[n];
                    for (int i = 0; i < n; i++) matAssets[i] = MatPath(i);
                    var build = new AssetBundleBuild
                    {
                        assetBundleName = "vtbake_assets_all_0000000000000000.bundle",
                        assetNames = matAssets,
                    };
                    var opts = BuildAssetBundleOptions.None;
                    var target = BuildTarget.StandaloneWindows64;
                    var man = BuildPipeline.BuildAssetBundles(
                        bundleDir, new[] { build }, opts, target);
                    var found = Directory.GetFiles(bundleDir, "*.bundle");
                    var files = found.OrderBy(f => f).ToArray();
                    Say("BuildAssetBundles = " + (man == null ? "null" : "ok")
                        + " · 包 " + files.Length + " 个");
                    if (files.Length > 0)
                    {
                        var b = AssetBundle.LoadFromFile(files[0]);
                        if (b == null) Say("  **LoadFromFile 返回 null**");
                        else
                        {
                            // ⛔ 按**材质下标**取，不按包内枚举顺序取（那顺序不保证）
                            var byName = new Dictionary<string, string[]>();
                            var seen = new List<string[]>();
                            foreach (var m in b.LoadAllAssets<Material>())
                            {
                                if (m == null) continue;
                                string g;
                                string it;
                                string d = Stacks(m, out g, out it);
                                var tex = m.GetTexture("_Layer0");
                                Say("  包内材质 " + m.name + " m_BuildTextureStacks = " + d
                                    + " · _Layer0=" + (tex == null ? "null" : tex.name));
                                var pair = new string[] { g, it };
                                if (!byName.ContainsKey(m.name)) byName[m.name] = pair;
                                seen.Add(pair);
                            }
                            for (int i = 0; i < n; i++)
                            {
                                string[] pair = null;
                                if (byName.ContainsKey(MatName(i))) pair = byName[MatName(i)];
                                else if (i < seen.Count) pair = seen[i];
                                if (pair != null && !string.IsNullOrEmpty(pair[0]))
                                {
                                    groupB[i] = pair[0];
                                    itemB[i] = pair[1];
                                }
                            }
                            b.Unload(true);
                        }
                    }
                }
                catch (Exception e)
                {
                    Say("**AssetBundle 那一趟失败** "
                        + e.GetType().Name + ": " + e.Message);
                }
            }
            Say("bundle 读回 " + n + " 颗材质的 group/item（拼起来看）：");
            for (int i = 0; i < n; i++)
                Say("  [" + i + "] " + MatName(i) + " group=" + Show(groupB[i])
                    + " item=" + Show(itemB[i]));

            Say("");
            Say("--- 2. 出 Player 之前：把源图重新钉回 N 颗项目材质 ---");
            // 打 AssetBundle 那一趟会把栈层上的**源贴图引用清空**（kb/18 §6.3 实测）——
            // 不钉回去，第二跳（出 Player）就烘不出东西。N 颗都要钉。
            for (int i = 0; i < n; i++)
            {
                var texN = AssetDatabase.LoadAssetAtPath<Texture2D>(SrcNormal(i));
                var texM = AssetDatabase.LoadAssetAtPath<Texture2D>(SrcMask(i));
                if (texN == null || texM == null)
                {
                    Say("  **源图读不到** 组[" + i + "] normal=" + (texN == null)
                        + " mask=" + (texM == null));
                    continue;
                }
                mats[i].SetTexture("_Layer0", texN);
                mats[i].SetTexture("_Layer1", texM);
                EditorUtility.SetDirty(mats[i]);
                Say("  [" + i + "] _Layer0=" + texN.name
                    + " (" + texN.width + "x" + texN.height + ")"
                    + "  _Layer1=" + texM.name + " (" + texM.width + "x" + texM.height + ")");
            }
            AssetDatabase.SaveAssets();
            for (int i = 0; i < n; i++)
            {
                string g0;
                string i0;
                Say("  项目材质[" + i + "]（钉回后）= " + Stacks(mats[i], out g0, out i0));
            }

            if (string.IsNullOrEmpty(exe)) { Say("**缺 BAVTB_PLAYER_EXE**"); return; }
            var exeDir = Path.GetDirectoryName(exe);
            if (!string.IsNullOrEmpty(exeDir)) Directory.CreateDirectory(exeDir);

            Say("");
            Say("--- 3. BuildPlayer ---");
            var bopts = new BuildPlayerOptions
            {
                scenes = new[] { ScenePath },
                locationPathName = exe,
                target = BuildTarget.StandaloneWindows64,
                options = BuildOptions.None,
            };
            var rep = BuildPipeline.BuildPlayer(bopts);
            var sum = rep.summary;
            Say("BuildPlayer result = " + sum.result + "  errors=" + sum.totalErrors);
            Say("   size=" + sum.totalSize + "  time=" + sum.totalTime);
            foreach (var st in rep.steps)
                Say(string.Format("   step {0,-52} {1}", st.name, st.duration));
            for (int i = 0; i < n; i++)
            {
                string g1;
                string i1;
                Say("BuildPlayer 之后项目材质[" + i + "] = " + Stacks(mats[i], out g1, out i1));
            }

            Say("");
            Say("--- 4. 池子 ---");
            string exeName = Path.GetFileNameWithoutExtension(exe);
            string root = string.IsNullOrEmpty(exeDir) ? "." : exeDir;
            string dataDir = Path.Combine(root, exeName + "_Data");
            string sa = Path.Combine(dataDir, "StreamingAssets");
            Say("产物 exe 存在=" + File.Exists(exe) + " · _Data=" + Directory.Exists(dataDir)
                + " · StreamingAssets=" + Directory.Exists(sa));
            if (Directory.Exists(sa))
            {
                var all = Directory.GetFiles(sa, "*", SearchOption.TopDirectoryOnly);
                var ordered = all.OrderBy(f => f).ToArray();
                var vt = ordered.Where(f => f.EndsWith(".gtp") || f.EndsWith(".gts")).ToArray();
                Say("StreamingAssets 顶层文件 " + ordered.Length
                    + " 个 · 含 .gtp/.gts = " + vt.Length);
                foreach (var f in vt)
                    Say("   " + Path.GetFileName(f) + "  " + new FileInfo(f).Length + " B");
                var gts = vt.Where(f => f.EndsWith(".gts")).ToArray();
                if (gts.Length >= 1) groupPool = Path.GetFileNameWithoutExtension(gts[0]);
            }

            bool built = sum.result == BuildResult.Succeeded;
            // N 颗材质（**同一个 shader**）应该落在**同一个 group** 下，各自一个 item
            // ⇒ 一个池子装 N 个 item。任何一颗没拿到名字 / group 分裂，都算红。
            bool named = true;
            bool sameGroup = true;
            for (int i = 0; i < n; i++)
            {
                bool hasGroup = !string.IsNullOrEmpty(groupB[i]);
                bool hasItem = !string.IsNullOrEmpty(itemB[i]);
                if (!hasGroup || !hasItem) named = false;
                if (i > 0 && groupB[i] != groupB[0]) sameGroup = false;
            }
            string group = (!string.IsNullOrEmpty(groupB[0])) ? groupB[0] : groupPool;
            string item = itemB[0];
            bool agrees = !string.IsNullOrEmpty(groupPool) && group == groupPool;
            bool distinct = CountDistinct(itemB) == n;
            Say("");
            Say("group=" + Show(group));
            Say("item=" + Show(item));
            for (int i = 0; i < n; i++)
            {
                Say("mat" + i + "_name=" + MatName(i));
                Say("mat" + i + "_group=" + Show(groupB[i]));
                Say("mat" + i + "_item=" + Show(itemB[i]));
            }
            for (int i = 0; i < n; i++) Say("group_bundle=" + Show(groupB[i]));
            for (int i = 0; i < n; i++) Say("item_bundle=" + Show(itemB[i]));
            Say("group_pool=" + Show(groupPool));
            Say("判据：BuildPlayer 成功=" + built + " · N 个 group/item 都有=" + named
                + " · N 颗同一个 group=" + sameGroup + " · N 个 item 互不相同=" + distinct
                + " · bundle 的 group == 池子文件名=" + agrees);
            if (!named) Say("**有材质没从 AssetBundle 里读回 group / item**");
            if (!sameGroup) Say("**N 颗材质的 group 不一样 —— 写不进同一套池子**");
            if (!distinct) Say("**N 颗材质里有重复的 item —— 它们其实是同一颗**");
            if (!agrees) Say("**AssetBundle 算出来的 group 与池子文件名对不上**");
            ok = built && named && sameGroup && agrees;
            Say("VERDICT " + (ok ? "bake-ok" : "bake-fail"));
        }
        catch (Exception e) { Say("**异常** " + e); }
        finally { Say(Seconds(sw)); Flush(); }
    }

    // 单站入口（--split / --only build 用）：跑完这一站就退出。
    public static void Run()
    {
        BuildBody();
        EditorApplication.Exit(0);
    }
}
'''

EDITOR_ONE_SOURCE = r'''// VtBakeOne.cs —— **三合一**：一趟会话按序做完 setup / build / restore
// 由 ba-mod-maker vt-bake 生成（临时件，收尾会被删）。
//
// 为什么能挤进一趟（模块头「为什么三站能挤进一趟会话」）：
//   ① 构建要的「VT 开机就开」是**进程启动时**读 Library\EditorOnlyVirtualTextureState.json
//      —— python 在起这一趟之前已经把它写成 true，所以本会话的启动值本来就是 true；
//   ② 站与站之间要传的只有工程里的资产与那份原值快照 —— 同一趟进程里它们本来就在手上。
// ⇒ 少起两个进程、少加载两次工程。
//
// 复原写在 finally：**任何一站抛异常，工程也要按快照复原**。
// python 侧还有一层兜底：这一趟没写出 VERDICT restore-ok，就再起一趟 VtBakeSetup.Restore。
using System;
using UnityEditor;
using UnityEngine;

public static class VtBakeOne
{
    public static void Run()
    {
        int rc = 0;
        bool keep = Environment.GetEnvironmentVariable("BAVTB_KEEP") == "1";
        var sw = System.Diagnostics.Stopwatch.StartNew();
        try
        {
            Debug.Log("[VTB-one] 一趟会话：setup -> build"
                + (keep ? "（--keep：不复原）" : " -> restore"));
            VtBakeSetup.SetupBody();
            VtBakeBuild.BuildBody();
        }
        catch (Exception e)
        {
            rc = 1;
            Debug.LogError("[VTB-one] **未捕获异常** " + e);
        }
        finally
        {
            if (keep)
            {
                Debug.Log("[VTB-one] --keep：跳过复原（工程临时件与 VT 开关留给调试）");
            }
            else
            {
                try { VtBakeSetup.RestoreBody(); }
                catch (Exception e)
                {
                    rc = 1;
                    Debug.LogError("[VTB-one] **复原异常** " + e);
                }
            }
            Debug.Log("[VTB-one] 这一趟墙钟 " + sw.Elapsed.TotalSeconds.ToString(
                "0.0", System.Globalization.CultureInfo.InvariantCulture) + " s");
        }
        EditorApplication.Exit(rc);
    }
}
'''

# --------------------------------------------------------------------------- #
# 读数
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class Reading:
    """一份编辑器读数（key=value 行 + VERDICT 行）。"""

    values: Mapping[str, tuple[str, ...]]
    verdict: str | None
    text: str

    def one(self, key: str) -> str | None:
        got = self.values.get(key)
        return got[0] if got else None

    def many(self, key: str) -> tuple[str, ...]:
        return self.values.get(key, ())


def parse_reading(text: str) -> Reading:
    """把读数文本拆成 {key: (值, ...)} + VERDICT。

    只认**行首无空格**、键里没有空格的 key=value —— 编辑器那些人读行
    （例：     改前: cpuMB=4 gpu=...）里的等号不会被误当读数。
    """
    values: dict[str, list[str]] = {}
    verdict: str | None = None
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line:
            continue
        if line.startswith("VERDICT "):
            verdict = line
            continue
        key, sep, value = line.partition("=")
        if not sep or not key or " " in key:
            continue
        values.setdefault(key, []).append(value.strip())
    return Reading(values={key: tuple(vals) for key, vals in values.items()},
                   verdict=verdict, text=text)


# --------------------------------------------------------------------------- #
# 计划（**纯数据**：建它只做校验，不写任何东西）
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class BakeSource:
    """一组源图（normal + mask）：磁盘上的原件 + 两张图的尺寸。

    它在工程里的落点由**下标**决定（normal_name / mask_name / material_name）——
    第 0 组的名字与从前逐字相同，所以 N=1 的临时件集合不变。
    """

    index: int
    normal: Path
    mask: Path
    size_normal: tuple[int, int] = (0, 0)
    size_mask: tuple[int, int] = (0, 0)

    @property
    def normal_name(self) -> str:
        return source_name(self.index, "normal")

    @property
    def mask_name(self) -> str:
        return source_name(self.index, "mask")

    @property
    def material_name(self) -> str:
        return material_name(self.index)

    @property
    def asset_name(self) -> str:
        """材质在 AssetBundle 里那个 m.name（不带扩展名）。"""
        return material_stem(self.index)


@dataclass(frozen=True, slots=True)
class BakePlan:
    """一次烘池子的全部输入与落点（N 组源图 -> N 颗材质 -> 一个 group 下 N 个 item）。"""

    project: Path
    sources: tuple[BakeSource, ...]
    out_dir: Path
    unity_argv: tuple[str, ...]
    player_dir: Path
    work_subdir: str = WORK_SUBDIR
    timeout_setup: float = 600.0
    timeout_build: float = 1800.0
    timeout_restore: float = 600.0
    only: str = "all"
    #: True = 三站挤进**一趟** Unity 会话（默认，实测省下两次启动 + 两次工程加载）；
    #: False = 老的三趟写法（CLI 的 --split，A/B 对照与排障用）
    merged: bool = True
    keep: bool = False
    notes: tuple[str, ...] = ()
    #: 每组源图的 Mask 档位读数（R = 金属度 / A = 光滑度）—— **8 位原值**，读图时量的
    levels: tuple[MaskLevels, ...] = ()
    #: 档位体检的红（R 的 p95 越出游戏自用档位）—— bake 拿它挡在 Unity 之前
    level_problems: tuple[str, ...] = ()

    # ---- 向后兼容的读法（N=1 的老用法：只烘一颗材质） ----
    @property
    def normal(self) -> Path:
        return self.sources[0].normal

    @property
    def mask(self) -> Path:
        return self.sources[0].mask

    @property
    def image_sizes(self) -> tuple[tuple[int, int], tuple[int, int]]:
        """第 0 组那两张图的尺寸（N=1 时就是"那两张"）。"""
        return (self.sources[0].size_normal, self.sources[0].size_mask)

    @property
    def material_count(self) -> int:
        """这一次要烘几颗材质 = 同一个 group 下应该有几个 item。"""
        return len(self.sources)

    # ---- 工程里的落点（都是临时件） ----
    @property
    def asset_dir(self) -> Path:
        return self.project / "Assets" / self.work_subdir

    @property
    def editor_dir(self) -> Path:
        return self.asset_dir / "Editor"

    @property
    def shader_path(self) -> Path:
        return self.asset_dir / "VtBake.shader"

    @property
    def scene_path(self) -> Path:
        return self.asset_dir / SCENE_NAME

    def material_path(self, index: int) -> Path:
        """第 index 颗材质的落点（N 颗**共用同一个 shader** ⇒ 同一个 group）。"""
        return self.asset_dir / material_name(index)

    def source_png(self, index: int, layer: str) -> Path:
        return self.asset_dir / source_name(index, layer)

    @property
    def normal_png(self) -> Path:
        return self.source_png(0, "normal")

    @property
    def mask_png(self) -> Path:
        return self.source_png(0, "mask")

    @property
    def vt_state_path(self) -> Path:
        return self.project / "Library" / "EditorOnlyVirtualTextureState.json"

    # ---- 证据与产物 ----
    @property
    def log_dir(self) -> Path:
        return self.out_dir / "_vtbake"

    @property
    def bundle_dir(self) -> Path:
        return self.log_dir / "_bundle"

    @property
    def snapshot_path(self) -> Path:
        return self.log_dir / "project-state-before.json"

    def reading_path(self, stage: str) -> Path:
        return self.log_dir / READING_NAMES[stage]

    def log_path(self, stage: str) -> Path:
        return self.log_dir / LOG_NAMES[stage]

    # ---- Player ----
    @property
    def player_exe(self) -> Path:
        return self.player_dir / (self.player_dir.name + ".exe")

    @property
    def player_streaming(self) -> Path:
        return self.player_dir / (self.player_dir.name + "_Data") / "StreamingAssets"


def _abs(path: str | Path) -> Path:
    r"""钉成绝对路径。

    ⛔ **Unity 编辑器的当前目录会变成工程根**（本机实测：相对路径的读数 / 日志 /
    证据全落进了 <工程>\_work\...，而 python 侧在仓目录里等它 ⇒ 三站全报「读数没更新」）。
    ⇒ 凡是交给编辑器的路径都必须是绝对的，产品不许依赖编辑器的 CWD。
    """
    return Path(path).expanduser().resolve()


def _image_size(path: Path, label: str) -> tuple[int, int]:
    """源图先验：不存在 / 解不开 / 太小 一律 AssetError（**先于任何写入**）。"""
    if not path.is_file():
        raise AssetError(
            f"{label} 不在了：{path}",
            hint="vt-bake 要两张**源图**（normal / mask PNG）—— 源图驱动才有池子，见 kb/18 §6.3",
        )
    try:
        with Image.open(path) as probe:
            probe.verify()
        with Image.open(path) as image:
            width, height = image.size
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise AssetError(
            f"{label} 不是能解码的图：{path}（{type(exc).__name__}: {exc}）",
            hint="换一张真的 PNG/JPG —— 这一步在**任何写入之前**，坏图不会产出半个池子",
        ) from exc
    if width < 64 or height < 64:
        raise AssetError(
            f"{label} 太小：{path}（{width}x{height}）",
            hint="VT 的瓦片是 128；实测能烘出 5 个文件的是 2048x2048 —— 至少给 64x64",
        )
    return width, height



# --------------------------------------------------------------------------- #
# 源图档位体检（Mask：R = 金属度 / A = 光滑度）
# --------------------------------------------------------------------------- #
#: 游戏自己那张 Mask 的档位（kb/22 §5 实测：R p50 0.42 / p95 0.86 · A p50 0.27 / p95 0.59）。
#: ⛔ **红线只钉 R（金属度）** —— 依据：同一台装置只改 mask 档位（R 0.854 → 0.427）⇒
#: 主体像素 26,239 → 94,490，机理是「R 0.43→0.85 之间**阈值式掉底**」；
#: 而 A（光滑度）那半条在真机上只表现为「很光滑 / 不反光」（设计稿 §2.7 第①轮 A=255
#: 是用户判读过的观感实验）⇒ A 越界 / 没有 alpha 一律**只出备注，不当红**。
GAME_MASK_R_P50 = 0.42
GAME_MASK_R_P95 = 0.86
GAME_MASK_A_P50 = 0.27
GAME_MASK_A_P95 = 0.59
#: 参照分布是**按 8 位量化报出来的统计量**（0.86 × 255 = 219.3），所以判据落在字节值上，
#: 并留 2 级容差吸收那次四舍五入：R 的 p95 字节值 > 219 + 2 = **221**（≈0.867）才算越界。
MASK_P95_TOLERANCE = 2
MASK_R_LIMIT_BYTE = round(GAME_MASK_R_P95 * 255) + MASK_P95_TOLERANCE
MASK_A_LIMIT_BYTE = round(GAME_MASK_A_P95 * 255) + MASK_P95_TOLERANCE


def _percentile(histogram: Sequence[int], percent: float) -> int:
    """8 位直方图上的百分位：累计占比首次 ≥ percent 的那个值。"""
    total = sum(histogram)
    if total <= 0:
        return 0
    want = total * percent / 100.0
    seen = 0
    for value, count in enumerate(histogram):
        seen += count
        if seen >= want:
            return value
    return len(histogram) - 1


@dataclass(frozen=True, slots=True)
class MaskLevels:
    """一张 Mask 源图的档位读数（**8 位原值**：R = 金属度 / A = 光滑度）。

    为什么读的是字节而不是浮点：源图就是 8 位/通道，参照分布也是按这个量化报出来的
    ⇒ 判据直接用字节值，不给自己造一个浮点边界。
    """

    label: str
    path: Path
    size: tuple[int, int]
    has_alpha: bool
    r50: int
    r95: int
    rmax: int
    #: R 超过档位上限的像素占比（0..1）—— 报出来给人读，判据只用 p95
    over_share: float
    a50: int | None = None
    a95: int | None = None
    amax: int | None = None

    @property
    def over(self) -> bool:
        """R 的 p95 越出游戏自用档位 ⇒ **红**（本次唯一那条红线）。"""
        return self.r95 > MASK_R_LIMIT_BYTE

    @property
    def limit_byte(self) -> int:
        """R 的 p95 上限（8 位）—— 判据那一把尺子的刻度，印给人看。"""
        return MASK_R_LIMIT_BYTE

    @property
    def limit_value(self) -> float:
        return MASK_R_LIMIT_BYTE / 255

    @property
    def a_over(self) -> bool:
        """A 越界 ⇒ 只是备注（见上面那段依据）。"""
        return self.a95 is not None and self.a95 > MASK_A_LIMIT_BYTE

    @staticmethod
    def _show(value: int | None) -> str:
        return "（没有 A）" if value is None else f"{value / 255:.3f}（{value}）"

    def lines(self) -> tuple[str, ...]:
        """给人读的那几行（CLI 原样印；每个数字都能复算）。"""
        out = [
            f"  [源图档位体检] {self.label} {self.path}  {self.size[0]}x{self.size[1]}",
            f"    R（金属度） p50 {self._show(self.r50)} · p95 {self._show(self.r95)}"
            f" · max {self._show(self.rmax)}（超上限的像素 {self.over_share * 100:.2f}%）",
            f"      参照 游戏自用：p50 {GAME_MASK_R_P50} · p95 {GAME_MASK_R_P95}"
            f" ⇒ 判据 p95 ≤ {MASK_R_LIMIT_BYTE / 255:.3f}（8 位 {MASK_R_LIMIT_BYTE}）",
        ]
        if self.has_alpha:
            out.append(
                f"    A（光滑度） p50 {self._show(self.a50)} · p95 {self._show(self.a95)}"
                f" · max {self._show(self.amax)}"
                f"（参照 游戏自用 p50 {GAME_MASK_A_P50} / p95 {GAME_MASK_A_P95}）"
            )
        else:
            out.append("    A（光滑度） 这张源图**没有 alpha 通道** ⇒ VT 的 A 会被填成 1.0"
                       "（满光滑）")
            out.append("      ⇒ 这是备注不是红：要控制光滑度就存成带 A 的 PNG"
                       "（kb/22 §5 · 设计稿 §2.7 第①轮）")
        if self.over:
            out.append(f"    ★ 红：R 的 p95 {self.r95 / 255:.3f} 越出游戏自用档位"
                       f"（>{MASK_R_LIMIT_BYTE / 255:.3f}）—— 金属度写高了整片发暗/发黑")
        else:
            out.append(f"    ✅ 在游戏自用档位内（R p95 {self.r95 / 255:.3f}"
                       f" ≤ {MASK_R_LIMIT_BYTE / 255:.3f}）")
        if not self.over and self.rmax > MASK_R_LIMIT_BYTE:
            out.append(f"    备注：有极少数像素到 {self.rmax / 255:.3f}"
                       f"（>{MASK_R_LIMIT_BYTE / 255:.3f}），但 p95 在档位内 —— 面积小，不当红")
        if self.a_over:
            out.append(f"    备注：A 的 p95 {self.a95 / 255:.3f} 高于游戏自用 p95"
                       f" {GAME_MASK_A_P95}（只报不当红）")
        return tuple(out)

    def problems(self) -> tuple[str, ...]:
        """有红的那几条（`plan_bake` 记进计划，`bake` 拿它挡在 Unity 之前）。"""
        if not self.over:
            return ()
        return (
            f"{self.label} 的 Mask 档位越界：R（金属度）p95 {self.r95 / 255:.3f}"
            f" > 游戏自用 p95 {GAME_MASK_R_P95}（8 位上限 {MASK_R_LIMIT_BYTE}）"
            " —— 参照 kb/22 §5，照抄游戏自用档位（漆面 ~0.05 / 裸金属 ~0.45）",
        )


def source_levels(path: Path, label: str) -> MaskLevels:
    """只读一张 Mask 源图，算档位读数。

    ⛔ 用 PIL 自己的 8 位直方图（`Image.histogram()`），**不引 numpy** —— 源图就是
    8 位/通道，判据也在字节上；多拉一个依赖只会多一处漂移。
    """
    with Image.open(path) as image:
        has_alpha = "A" in image.getbands() or (
            image.mode == "P" and "transparency" in image.info
        )
        size = (image.width, image.height)
        rgba = image.convert("RGBA")
    histogram = rgba.histogram()
    red = histogram[0:256]
    total = max(1, sum(red))
    over = sum(count for value, count in enumerate(red) if value > MASK_R_LIMIT_BYTE)
    alpha = histogram[768:1024] if has_alpha else None
    return MaskLevels(
        label=label,
        path=path,
        size=size,
        has_alpha=has_alpha,
        r50=_percentile(red, 50.0),
        r95=_percentile(red, 95.0),
        rmax=max((value for value, count in enumerate(red) if count), default=0),
        over_share=over / total,
        a50=_percentile(alpha, 50.0) if alpha else None,
        a95=_percentile(alpha, 95.0) if alpha else None,
        amax=(max((value for value, count in enumerate(alpha) if count), default=0)
              if alpha else None),
    )



def _source_pairs(
    normal: str | Path | None,
    mask: str | Path | None,
    pairs: Sequence[Sequence[str | Path]] | None,
) -> tuple[tuple[str | Path, str | Path], ...]:
    """把两种写法收敛成一份**有序**的 (normal, mask) 清单。

    ⛔ 这里只校验参数**形状**（组数对不对、是不是成对），一张图都不读 —— 读图在下面。
    两种写法只能给一种：normal + mask（今天的老用法），或 pairs=[(n0,m0), (n1,m1), ...]。
    """
    if pairs is not None:
        if normal is not None or mask is not None:
            raise AssetError(
                "pairs 与 normal/mask 是两种写法，只能给一种",
                hint="一次烘 N 组源图用 pairs=[(normal0, mask0), (normal1, mask1), ...]",
            )
        raw: list[tuple[str | Path, str | Path]] = []
        for item in pairs:
            got = tuple(item)
            if len(got) != 2:
                raise AssetError(
                    f"每一组源图要恰好两张（normal, mask），这一组有 {len(got)} 张",
                    hint="pairs 的形状 = [(normal0, mask0), (normal1, mask1), ...]",
                )
            raw.append((got[0], got[1]))
    elif normal is not None and mask is not None:
        raw = [(normal, mask)]
    elif normal is None and mask is None:
        raise AssetError(
            "一组源图都没有：要 --normal 与 --mask（可以重复给，成对）",
            hint="例：--normal a_n.png --mask a_m.png --normal b_n.png --mask b_m.png",
        )
    else:
        missing, given = ("--mask", "--normal") if normal is not None else ("--normal", "--mask")
        raise AssetError(
            f"只给了 {given}，缺 {missing} —— 源图必须成对",
            hint="一组源图 = 一张 normal + 一张 mask；N 组就重复 N 次（顺序即材质顺序）",
        )
    if not raw:
        raise AssetError("源图清单是空的", hint="至少给一组 (normal, mask)")
    return tuple(raw)


def plan_bake(
    *,
    project: str | Path,
    normal: str | Path | None = None,
    mask: str | Path | None = None,
    pairs: Sequence[Sequence[str | Path]] | None = None,
    out_dir: str | Path,
    unity: str | Path | None = None,
    unity_argv: Sequence[str] | None = None,
    player_dir: str | Path | None = None,
    work_subdir: str = WORK_SUBDIR,
    timeout_setup: float = 600.0,
    timeout_build: float = 1800.0,
    timeout_restore: float = 600.0,
    only: str = "all",
    merged: bool = True,
    keep: bool = False,
    strict_levels: bool = True,
) -> BakePlan:
    """建计划：**只校验**（源图能不能解码、工程像不像 Unity 工程、编辑器在不在）。

    ⛔ 这里一个字节都不写 —— J3（坏图 / 组数不对称必须不产出池子文件）就靠这条顺序。
    unity_argv 是给离线用例的接缝：给了就原样当命令前缀用（假编辑器）。
    normal + mask 是**一组**（今天的老用法）；pairs=[(n0, m0), (n1, m1), ...] 是 N 组。
    merged=True（默认）走三合一：一趟 Unity 会话做完 setup + build + restore。

    **源图档位体检**（kb/22 §5）：每张 --mask 的 **R（金属度）p95** 与游戏自用 p95
    （0.86）比 —— 越界 ⇒ 记进 `level_problems`（bake 会**一趟 Unity 都不起**）；
    `strict_levels=False` 是"我知道我在做什么"的放行口（越界降级成备注）。
    A（光滑度）/ 没有 alpha 只出备注，**不当红**（依据见上面那一节的常量表）。
    """
    if only not in ("all", "setup", "build", "restore"):
        raise AssetError(f"不认识的 stage：{only}", hint="只认 all / setup / build / restore")
    proj = _abs(project)
    if not (proj / "Assets").is_dir() or not (proj / "ProjectSettings").is_dir():
        raise AssetError(
            f"这不像 Unity 工程（要含 Assets\\ 与 ProjectSettings\\）：{proj}",
            hint="--project 指到工程根，例 D:\\断箭模组制作\\unity-armory",
        )
    raw = _source_pairs(normal, mask, pairs)

    notes: list[str] = []
    sources: list[BakeSource] = []
    levels: list[MaskLevels] = []
    for index, (raw_normal, raw_mask) in enumerate(raw):
        # N=1 时标签里不带下标 —— 坏图的错误原文与从前逐字相同（向后兼容）。
        suffix = "" if len(raw) == 1 else f"[{index}]"
        normal_src = _abs(raw_normal)
        mask_src = _abs(raw_mask)
        size_normal = _image_size(normal_src, "--normal" + suffix)
        size_mask = _image_size(mask_src, "--mask" + suffix)
        for label, size in (("--normal" + suffix, size_normal), ("--mask" + suffix, size_mask)):
            width, height = size
            if width & (width - 1) or height & (height - 1):
                notes.append(f"{label} {width}x{height} 不是 2 的幂 —— 池子形状不对时先看这一条")
        # 档位体检就长在"读图"这一步：于是"要不要花那 3 分钟起 Unity"在起跑前就定了。
        levels.append(source_levels(mask_src, "--mask" + suffix))
        sources.append(BakeSource(index=index, normal=normal_src, mask=mask_src,
                                  size_normal=size_normal, size_mask=size_mask))

    level_problems: list[str] = []
    for got in levels:
        level_problems += list(got.problems())
    if level_problems and not strict_levels:
        notes += [f"档位体检放行（strict_levels=False）：{line}" for line in level_problems]
        level_problems = []

    if unity_argv is None:
        exe = Path(unity) if unity else DEFAULT_UNITY
        if not exe.is_file():
            raise AssetError(
                f"找不到 Unity 编辑器：{exe}",
                hint="--unity 指到 Unity.exe（例 D:\\Unity\\6000.3.24f1\\Editor\\Unity.exe）",
            )
        argv: tuple[str, ...] = (str(exe),)
    else:
        argv = tuple(str(item) for item in unity_argv)
        if not argv or not Path(argv[0]).is_file():
            raise AssetError(
                f"起不了编辑器：{argv[0] if argv else '(空命令)'}",
                hint="要么 --unity 指到 Unity.exe，要么给一个非空的命令",
            )

    pdir = _abs(player_dir) if player_dir else DEFAULT_PLAYER_DIR
    if not str(pdir).isascii():
        notes.append(
            f"Player 落点不是纯 ASCII：{pdir} —— 实测非 ASCII 路径下 Granite 打不开烘好的池子"
            "（tools/unity_vtplayer.py 文件头那条 A/B）。本命令只取构建产物；"
            "要拿这个 Player 出图就得换 ASCII 目录。"
        )
    return BakePlan(
        project=proj,
        sources=tuple(sources),
        out_dir=_abs(out_dir),
        unity_argv=argv,
        player_dir=pdir,
        work_subdir=work_subdir,
        timeout_setup=timeout_setup,
        timeout_build=timeout_build,
        timeout_restore=timeout_restore,
        only=only,
        merged=merged,
        keep=keep,
        notes=tuple(notes),
        levels=tuple(levels),
        level_problems=tuple(level_problems),
    )


# --------------------------------------------------------------------------- #
# 工程里的临时件（写 / 删）
# --------------------------------------------------------------------------- #
def stage_assets(plan: BakePlan) -> tuple[Path, ...]:
    """把临时件写进工程：**同一个** shader + N 组源图 + 三颗编辑器脚本。返回写下的文件。

    N 颗材质共用这一颗 shader 是**故意的**：group 由栈配置（shader）决定，
    只有共用 shader 才会烘进**同一个 group**（kb/18 §6.3）。
    """
    plan.editor_dir.mkdir(parents=True, exist_ok=True)
    plan.shader_path.write_text(SHADER_SOURCE, encoding="utf-8", newline="\n")
    written: list[Path] = [plan.shader_path]
    for source in plan.sources:
        normal_dst = plan.source_png(source.index, "normal")
        mask_dst = plan.source_png(source.index, "mask")
        shutil.copy2(source.normal, normal_dst)
        shutil.copy2(source.mask, mask_dst)
        written += [normal_dst, mask_dst]
    # 三颗编辑器脚本：前两颗是「一次进程一站」的老入口（--split / --only / 兜底复原那趟），
    # 第三颗（VtBakeOne）是默认那条三合一的路 —— 它调的是前两颗的**站内主体**
    # （SetupBody / BuildBody / RestoreBody），所以两条路共用同一套逻辑（AGENTS §5）。
    setup_cs = plan.editor_dir / "VtBakeSetup.cs"
    build_cs = plan.editor_dir / "VtBakeBuild.cs"
    one_cs = plan.editor_dir / "VtBakeOne.cs"
    setup_cs.write_text(EDITOR_SETUP_SOURCE, encoding="utf-8", newline="\n")
    build_cs.write_text(EDITOR_BUILD_SOURCE, encoding="utf-8", newline="\n")
    one_cs.write_text(EDITOR_ONE_SOURCE, encoding="utf-8", newline="\n")
    written += [setup_cs, build_cs, one_cs]
    return tuple(written)


def clear_assets(plan: BakePlan) -> tuple[str, ...]:
    """删掉工程里的整个工作子目录（含 .meta）—— J4 的收尾判据。"""
    removed: list[str] = []
    target = plan.asset_dir
    if target.is_dir():
        shutil.rmtree(target)
        removed.append(str(target))
    meta = target.with_name(target.name + ".meta")
    if meta.is_file():
        meta.unlink()
        removed.append(str(meta))
    return tuple(removed)


# --------------------------------------------------------------------------- #
# 工程级 VT 开关（Library\EditorOnlyVirtualTextureState.json）
# --------------------------------------------------------------------------- #
def read_vt_state(plan: BakePlan) -> str | None:
    """开工前那份状态文件的**原文**（None = 本来不存在）。"""
    path = plan.vt_state_path
    return path.read_text(encoding="utf-8") if path.is_file() else None


def write_vt_state(plan: BakePlan, enabled: bool) -> None:
    """钉住引擎启动时读的那份值（构建那一趟必须开机就是 true，见模块头第 1 条）。"""
    path = plan.vt_state_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '{"m_VirtualTexturingEnabled": %s, "m_VirtualTexturingEnabledInitialized": true}'
        % ("true" if enabled else "false"),
        encoding="utf-8",
    )


def restore_vt_state(plan: BakePlan, original: str | None) -> None:
    """把状态文件写回原样（原本不存在就删掉）。Unity 退出时可能又写过一遍，所以放最后。"""
    path = plan.vt_state_path
    if original is None:
        with contextlib.suppress(OSError):
            path.unlink()
        return
    path.write_text(original, encoding="utf-8")


# --------------------------------------------------------------------------- #
# 池子形状（**尺子**）与收割
# --------------------------------------------------------------------------- #
def pool_files(directory: str | Path) -> tuple[Path, ...]:
    """目录顶层的 .gts / .gtp（不看子目录 —— 证据目录 _vtbake 就住在 --out 下面）。"""
    target = Path(directory)
    if not target.is_dir():
        return ()
    return tuple(sorted(p for p in target.iterdir() if p.is_file() and p.suffix in POOL_SUFFIXES))


@dataclass(frozen=True, slots=True)
class PoolSet:
    """**一套池子** = 一个 group 的全部文件（1 个 `.gts` ＋ 它的若干 `.gtp` 分片）。

    为什么要有"一套"这个概念：**一趟构建里可能烘出不止一套** —— 实测（`_work/vtb1/extreme/pool/`）
    两颗材质同 shader、同两层，只因 Mask 内容不同就拿到**两个 group**，两套池子都落在
    Player 的 StreamingAssets 里。所以"收产物"这件事的单位是 **group**，不是"这个目录"。
    """

    group: str
    files: tuple[Path, ...]
    problems: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.problems

    @property
    def gts(self) -> Path | None:
        return next((path for path in self.files if path.suffix == ".gts"), None)

    @property
    def shards(self) -> tuple[Path, ...]:
        return tuple(path for path in self.files if path.suffix == ".gtp")

    @property
    def size(self) -> int:
        return sum(path.stat().st_size for path in self.files)

    def account(self) -> str:
        """**这一套自己的账**（一行：group · 几个文件 · 多少字节）。"""
        gts = self.gts
        head = f"{self.group}：{len(self.files)} 个文件（{self.size:,} B）"
        if gts is not None:
            head += f" · .gts {gts.stat().st_size:,} B + {len(self.shards)} 个 .gtp"
        return head


def shard_of(stem: str) -> tuple[str, str]:
    r"""`<group><分片>` 的词干 -> (group, 分片)；认不出分片就把整个词干当 group。

    分片名是固定的那几个（`POOL_SHARDS`）；group 是 32 位 hex、**不可能含 _ 以外的分隔**，
    所以"从右往左剥一个已知分片"是无歧义的。
    """
    for shard in POOL_SHARDS[1:]:
        if stem.endswith(shard):
            return stem[: -len(shard)], shard
    return stem, ""


def pool_sets(files: Sequence[Path], *, size_check: bool = True) -> tuple[PoolSet, ...]:
    """把一堆池子文件**按 group 分组**，每组各自过一次形状尺子。

    每组的判据（分母是"这一套"）：group 是 32 位小写 hex · 恰好 1 个 `.gts` ·
    **第一片 `0_1.gtp` 必须在**（分片个数不钉死：游戏自己的三个组就是
    3 / 4 / 5 个文件，kb/18 §6.2）；`size_check` 时再加一条 `.gts` 不小于单 item
    实测的 1 MiB —— 那条是**烘**出来的池子的判据，装那一步（`vt pool install`）只判
    "这一套齐不齐"，所以它传 `size_check=False`。
    """
    buckets: dict[str, list[Path]] = {}
    for path in files:
        if path.suffix == ".gts":
            group = path.stem
        elif path.suffix == ".gtp":
            group, _shard = shard_of(path.stem)
        else:
            group = path.stem
        buckets.setdefault(group, []).append(path)
    out: list[PoolSet] = []
    for group in sorted(buckets):
        got = sorted(buckets[group], key=lambda item: item.name)
        problems: list[str] = []
        if len(group) != NAME_LEN or any(ch not in "0123456789abcdef" for ch in group):
            problems.append(f"group 不是 32 位小写 hex：{group!r}")
        gts = [path for path in got if path.suffix == ".gts"]
        if len(gts) != 1:
            shown = ", ".join(path.name for path in gts) or "(一个都没有)"
            problems.append(f".gts 应该恰好 1 个，实际 {len(gts)} 个：{shown}")
        names = {path.name for path in got}
        if group + POOL_SHARDS[1] + ".gtp" not in names:
            problems.append(f"缺 {group}{POOL_SHARDS[1]}.gtp（一套池子的第一片总该在）")
        if gts and size_check:
            size = gts[0].stat().st_size
            if size < GTS_BYTES:
                problems.append(f"{gts[0].name} 只有 {size:,} B，"
                                f"比单 item 实测的 {GTS_BYTES:,} B 还小")
        out.append(PoolSet(group=group, files=tuple(got), problems=tuple(problems)))
    return tuple(out)


def check_pool_shape(
    files: Sequence[Path], *, items: int = 1
) -> tuple[str | None, tuple[str, ...]]:
    """池子形状的尺子 —— **单套口径**（一趟只该有一套池子），保留给老调用方/用例。

    · items == 1（今天的老用法，判据一字不变）：
      恰好 1 个 <group>.gts(**1 MiB**) + 4 个 <group>0_1/2/3/4.gtp，名字同一个 group；
    · items > 1：**还是只该有一个 group**（多颗材质写进同一套池子才是目的），
      但**不钉死分片个数** —— 游戏自己的三个组就是 3 / 4 / 5 个文件（kb/18 §6.2），
      分片按内容出现；页表大小也随瓦片数增长（游戏主组 37 MiB）。

    ⚠ **2026-10-11 起"多套池子"是合法的**（实测一趟构建会烘出两个 group）——
    要按 group 收产物请用 `pool_sets()`；本函数留给"一次只该有一套"的老口径
    （它现在的调用方只有老用例与 A/B 对照）。
    """
    if not files:
        return None, ("StreamingAssets 里一个 .gts/.gtp 都没有（池子没烘出来）",)
    groups = pool_sets(files)
    problems: list[str] = []
    gts = [path for path in files if path.suffix == ".gts"]
    if len(groups) != 1:
        shown = ", ".join(path.name for path in gts) or "(一个都没有)"
        problems.append(f".gts 应该恰好 1 个，实际 {len(gts)} 个：{shown}")
        for got in groups:
            problems += [f"池子组 {got.group}：{line}" for line in got.problems]
        return None, tuple(dict.fromkeys(problems))
    group = groups[0].group
    problems += list(groups[0].problems)
    want = {group + ".gts"} | {group + shard + ".gtp" for shard in POOL_SHARDS[1:]}
    names = {path.name for path in files}
    if items <= 1:
        for name in sorted(want - names):
            problems.append(f"缺 {name}")
        if groups[0].gts is not None and groups[0].gts.stat().st_size != GTS_BYTES:
            size = groups[0].gts.stat().st_size
            problems.append(f"{groups[0].gts.name} 大小 {size:,} B"
                            f" 不等于实测的 {GTS_BYTES:,} B")
    for name in sorted(names - want):
        problems.append(f"多出来 {name}（不属于这一组）")
    return group, tuple(dict.fromkeys(problems))


def check_items(plan: BakePlan, reading: Reading, items: Sequence[str]) -> tuple[str, ...]:
    """J5 的尺子：读数里那 N 个 item 要**齐、互不相同、且与逐材质那几条对得上**。

    · 条数 == 材质颗数（少一条 = 有一颗没进池子；多一条 = 混进了别的材质）；
    · 每条都是 32 位小写 hex（与 group 同一套形状）；
    · **N>1 时两两不同** —— 相同就意味着后一颗把前一颗**挤掉**了
      （同名池子会互相覆盖，kb/18 §6.3）。实测（_work/vtbake/run-same）：连
      **源图完全相同**的两颗材质都会拿到**不同**的 item ⇒ 这一条不会误伤
      "两颗材质长得一样"的输入，红了就是真出事了；
    · 逐材质那几条（mat<i>_item，键里带下标、与打印顺序无关）必须与按序那一串逐字相同
      —— 两种写法互为独立来源，对不上就是读数自相矛盾。
    """
    out: list[str] = []
    want = plan.material_count
    if len(items) != want:
        out.append(f"读数里 item 有 {len(items)} 条，材质有 {want} 颗（一颗材质一个 item）")
    bad = [value for value in items
           if len(value) != NAME_LEN or any(ch not in "0123456789abcdef" for ch in value)]
    if bad:
        shown = ", ".join(repr(value) for value in bad[:4])
        out.append(f"item 不是 32 位小写 hex：{shown}")
    if len(set(items)) != len(items):
        out.append("这 N 个 item 有重复 —— 后一颗材质把前一颗挤掉了（同名池子互相覆盖）")
    head = (reading.one("item") or "").strip()
    if items and head != items[0]:
        out.append(f"读数自相矛盾：item={head or '(没有)'} 与第一颗材质的 item={items[0]} 不同")
    for index, value in enumerate(items):
        indexed = (reading.one(f"mat{index}_item") or "").strip()
        if indexed != value:
            out.append(
                f"读数自相矛盾：mat{index}_item={indexed or '(没有)'}"
                f" 与按序的 item={value or '(没有)'} 不同"
            )
    return tuple(out)


def harvest(
    plan: BakePlan, expected: Sequence[str] = ()
) -> tuple[tuple[Path, ...], tuple[str, ...], tuple[str, ...]]:
    """把 Player 构建输出里的池子拷进 --out。返回 (落盘的文件, 问题, 备注)。

    **按 group 分组收**（2026-10-11 起）：`expected` = 这一轮**读数里**该有的 group
    （每颗材质一个，编辑器构建期算的）。盘上多出来的 group 只当**备注**
    （多半是上一轮留在同一个 Player 目录里的池子，收了它就会装错）；读数里有、盘上没有 ⇒ 红。
    不传 `expected` 就"盘上有几套收几套"（只读用法 / 老调用方）。
    """
    produced = pool_files(plan.player_streaming)
    if not produced:
        return (), ("StreamingAssets 里一个 .gts/.gtp 都没有（池子没烘出来）",), ()
    groups = pool_sets(produced)
    want = tuple(dict.fromkeys(str(group) for group in expected if str(group)))
    if not want:
        want = tuple(got.group for got in groups)
    on_disk = {got.group for got in groups}
    problems: list[str] = []
    notes: list[str] = []
    keep: list[Path] = []
    for got in groups:
        if got.group not in want:
            notes.append(f"不收 {got.group}（不在这一轮的读数里：{got.account()}）"
                         " —— 多半是上一轮留在 Player 里的池子")
            continue
        problems += [f"池子组 {got.group}：{line}" for line in got.problems]
        keep += list(got.files)
    for group in want:
        if group not in on_disk:
            problems.append(
                f"读数与盘上对不上：读数里的 group={group} 在 Player 的 StreamingAssets 里"
                f"没有池子（盘上的是 {', '.join(sorted(on_disk)) or '(一个都没有)'}）"
            )
    if plan.material_count == 1 and len(want) == 1:
        # N=1 的老判据一字不变：这一套必须恰好是 .gts + 0_1/2/3/4 这 5 个文件
        group = want[0]
        canonical = {group + ".gts"} | {group + shard + ".gtp" for shard in POOL_SHARDS[1:]}
        names = {path.name for path in keep}
        problems += [f"缺 {name}" for name in sorted(canonical - names)]
        problems += [f"多出来 {name}（不属于这一组）" for name in sorted(names - canonical)]
    if problems:
        return (), tuple(dict.fromkeys(problems)), tuple(notes)
    keep_names = {path.name for path in keep}
    plan.out_dir.mkdir(parents=True, exist_ok=True)
    stale = tuple(path.name for path in pool_files(plan.out_dir) if path.name not in keep_names)
    written: list[Path] = []
    for source in produced:
        if source.name not in keep_names:
            continue
        dest = plan.out_dir / source.name
        shutil.copy2(source, dest)
        written.append(dest)
    if stale:
        notes.append("--out 里还留着上一轮的文件（不属于这一轮）：" + ", ".join(stale))
    if len(want) > 1:
        notes.append(
            f"这一趟烘出了 {len(want)} 套池子（按 group 分别收）："
            + "；".join(got.account() for got in groups if got.group in want)
        )
    return tuple(written), (), tuple(notes)


def per_material_groups(plan: BakePlan, reading: Reading) -> tuple[str, ...]:
    """**每颗材质自己的 group**（长度 == 材质颗数，允许重复 —— 同族几颗可以落在同一个组）。

    实测（`_work/vtb1/extreme/pool/_vtbake/vtbake-build.txt`）：两颗材质同 shader、同两层，
    只因 Mask 内容不同就拿到**两个 group** ⇒ group 是**逐材质**的，不是"整个构建一个"。
    读数里两个独立来源：`mat<i>_group`（逐材质、键里带下标）与 `group_bundle`（按序重复 N 次）；
    两个来源对不上时优先用逐材质那串（`check_groups` 会判红）。
    """
    values = tuple(
        (reading.one(f"mat{index}_group") or "").strip() for index in range(plan.material_count)
    )
    if all(values):
        return values
    bundles = tuple(value.strip() for value in reading.many("group_bundle"))
    if len(bundles) == plan.material_count and all(bundles):
        return bundles
    head = (reading.one("group") or "").strip()
    return values or tuple([head] * plan.material_count)


def expected_groups(plan: BakePlan, reading: Reading) -> tuple[str, ...]:
    """这一轮**该有哪几个 group**（去重、按出现顺序）—— harvest 按它分组收产物。"""
    return tuple(
        dict.fromkeys(
            value for value in per_material_groups(plan, reading) if value
        )
    )


def check_groups(
    plan: BakePlan, reading: Reading, groups: Sequence[str]
) -> tuple[str, ...]:
    """逐材质 group 的尺子：条数 == 材质数 · 都是 32 位小写 hex · 与 group_bundle 串对得上。"""
    out: list[str] = []
    if len(groups) != plan.material_count:
        out.append(f"读数里 group 有 {len(groups)} 个（去重后），材质有 {plan.material_count} 颗")
    bad = [
        value
        for value in groups
        if len(value) != NAME_LEN or any(ch not in "0123456789abcdef" for ch in value)
    ]
    if bad:
        out.append("group 不是 32 位小写 hex：" + ", ".join(repr(value) for value in bad[:4]))
    bundles = tuple(value.strip() for value in reading.many("group_bundle"))
    if bundles:
        per_material = tuple(
            (reading.one(f"mat{index}_group") or "").strip() for index in range(len(bundles))
        )
        if per_material != bundles:
            out.append(
                "读数自相矛盾：mat<i>_group 那串 "
                + ", ".join(per_material)
                + " 与 group_bundle 那串 "
                + ", ".join(bundles)
                + " 不同"
            )
    return tuple(out)

# --------------------------------------------------------------------------- #
# 前台跑 + 硬超时（kb/14 §6：⛔ 不用后台 job 等 Unity）
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class RunTiming:
    """一次前台进程的读数（rc 只记录 —— batchmode 的退出码**不当判据**）。"""

    rc: int | None
    seconds: float
    timed_out: bool


def _print(text: str = "") -> None:
    print(text, flush=True)


def kill_tree(pid: int) -> None:
    """杀掉整棵进程树（taskkill /F /T）—— 超时就得连子进程一起收。"""
    with contextlib.suppress(OSError, subprocess.SubprocessError):
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)


def run_foreground(cmd: Sequence[str], timeout: float, what: str, *,
                   env: Mapping[str, str] | None = None,
                   say: Callable[[str], None] = _print) -> RunTiming:
    """前台跑 + 硬超时。超时就杀进程树，再等 KILL_GRACE 秒。"""
    try:
        proc = subprocess.Popen(list(cmd), env=dict(env) if env else None,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as exc:
        raise AssetError(
            f"起不了进程 {cmd[0]}（{type(exc).__name__}: {exc}）",
            hint="--unity 指到真的 Unity.exe",
        ) from exc
    started = time.monotonic()
    timed_out = False
    try:
        rc: int | None = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        say(f"   {what} 超过硬超时 {timeout:.0f} 秒 —— 杀进程树")
        kill_tree(proc.pid)
        try:
            rc = proc.wait(timeout=KILL_GRACE)
        except subprocess.TimeoutExpired:
            rc = None
    return RunTiming(rc=rc, seconds=time.monotonic() - started, timed_out=timed_out)


def count_unity_processes() -> int:
    """残留 Unity 进程数（-1 = 数不出来）。J4 的最后一条。"""
    try:
        done = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-Process Unity -ErrorAction SilentlyContinue | Measure-Object).Count"],
            capture_output=True, text=True, check=False,
        )
    except OSError:
        return -1
    try:
        return int(done.stdout.strip())
    except ValueError:
        return -1


# --------------------------------------------------------------------------- #
# 一个编辑器会话
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class EditorStage:
    """一个编辑器会话的读数与判据。"""

    stage: str
    method: str
    expect: str
    rc: int | None
    seconds: float
    timed_out: bool
    reading_path: Path
    log_path: Path
    reading_updated: bool
    cs_errors: tuple[str, ...]
    reading: Reading | None
    #: 这一站跟谁共用**一趟** Unity 进程（三合一的三个阶段共用 "one"；空 = 一次进程一站）
    session: str = ""

    @property
    def verdict_ok(self) -> bool:
        return self.reading is not None and self.reading.verdict == self.expect

    def problems(self) -> tuple[str, ...]:
        """这个会话的**红**（rc 不进这里 —— batchmode 的退出码不可靠）。"""
        out: list[str] = []
        if self.timed_out:
            out.append(f"{self.stage}：超过硬超时（{self.seconds:.0f}s），进程树已杀")
        if self.cs_errors:
            count = len(self.cs_errors)
            out.append(f"{self.stage}：日志里有 {count} 条 error CS（executeMethod 没跑）")
        if not self.reading_updated:
            out.append(f"{self.stage}：读数文件没更新（{self.reading_path.name}）")
        if not self.verdict_ok:
            if self.reading is None:
                got = "没有读数文件"
            else:
                got = self.reading.verdict or "没有 VERDICT 行"
            out.append(f"{self.stage}：期望 {self.expect}，实际 {got}")
        return tuple(out)

    @property
    def ok(self) -> bool:
        return not self.problems()

    def verdict_show(self) -> str:
        if self.reading is None or not self.reading.verdict:
            return "(没有)"
        return self.reading.verdict

    def line(self) -> str:
        fresh = "更新" if self.reading_updated else "没更新"
        color = "绿" if self.ok else "红"
        where = f"  [会话 {self.session}]" if self.session else ""
        return (f"{self.stage:8s} {self.seconds:6.1f}s  rc={self.rc}  读数={fresh}  "
                f"VERDICT={self.verdict_show()}  errorCS={len(self.cs_errors)}  {color}{where}")


#: 站 -> 这一站的硬超时（同一趟进程跑三站时，预算是三者之和）
def _stage_timeout(plan: BakePlan, stage: str) -> float:
    return {"setup": plan.timeout_setup, "build": plan.timeout_build,
            "restore": plan.timeout_restore}[stage]


def _editor_env(plan: BakePlan) -> dict[str, str]:
    """交给编辑器那一趟的环境 —— ⛔ 键名要与 C# 侧同一套（三站共用同一批）。"""
    env = dict(os.environ)
    env["BAVTB_OUTDIR"] = str(plan.log_dir)
    env["BAVTB_PLAYER_EXE"] = str(plan.player_exe)
    env["BAVTB_BUNDLE_DIR"] = str(plan.bundle_dir)
    # 编辑器侧按这个数去写 N 颗材质（名字规则与 material_name / source_name 同一套）
    env[SOURCE_COUNT_ENV] = str(plan.material_count)
    return env


def _exec_cmd(plan: BakePlan, method: str, logfile: Path) -> list[str]:
    """一条编辑器命令行（-batchmode -quit -executeMethod ... -logFile ...）。"""
    return [*plan.unity_argv, "-batchmode", "-quit", "-projectPath", str(plan.project),
            "-executeMethod", method, "-logFile", str(logfile)]


def _cs_errors(logfile: Path) -> tuple[str, ...]:
    """日志里的 error CS（有它 = executeMethod 根本没跑，是**判红**的一条）。"""
    text = logfile.read_text(encoding="utf-8", errors="replace") if logfile.is_file() else ""
    return tuple(line.strip() for line in text.splitlines() if "error CS" in line)


def _read_reading(path: Path) -> tuple[str, Reading | None]:
    raw = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
    return raw, (parse_reading(raw) if raw else None)


def _report(stage: EditorStage, raw: str, say: Callable[[str], None]) -> None:
    """把一站印出来：判据行 + 读数原文 + error CS（三站分开跑与三合一共用这一段）。"""
    say("   " + stage.line())
    if raw:
        say("   ── 读数 " + stage.reading_path.name + " ──")
        for line in raw.splitlines():
            say("   | " + line)
    else:
        tail = "（**没更新**）" if stage.reading_path.is_file() else ""
        say("   ── 读数 " + stage.reading_path.name + " 不存在" + tail + " ──")
    # 三合一只有**一份**日志：CS 明细在第一站印一次就够（判据那侧三站都算它红）
    if stage.cs_errors and (not stage.session or stage.stage == PHASES[0]):
        say(f"   ── 日志里的 error CS（{len(stage.cs_errors)} 条，前 10 条）──")
        for line in stage.cs_errors[:10]:
            say("   ! " + line)


def run_editor(plan: BakePlan, stage: str, say: Callable[[str], None] = _print) -> EditorStage:
    """跑一个编辑器会话（**这一趟进程只干这一站**），把它变成一把能红的尺子。

    ⛔ 它只跑一站：--split / --only / 三合一失败后的兜底复原走它。
    三站共用**一趟**进程的那条路是 run_merged()。
    """
    method = METHODS[stage]
    timeout = _stage_timeout(plan, stage)
    reading = plan.reading_path(stage)
    logfile = plan.log_path(stage)
    plan.log_dir.mkdir(parents=True, exist_ok=True)
    before = reading.stat().st_mtime if reading.is_file() else 0.0
    say(f">> Unity {method}（硬超时 {timeout:.0f} 秒）")
    timing = run_foreground(_exec_cmd(plan, method, logfile), timeout,
                            f"Unity {method}", env=_editor_env(plan), say=say)
    raw, got = _read_reading(reading)
    stage_result = EditorStage(
        stage=stage, method=method, expect=EXPECT_VERDICT[stage], rc=timing.rc,
        seconds=timing.seconds, timed_out=timing.timed_out, reading_path=reading,
        log_path=logfile, reading_updated=reading.is_file() and reading.stat().st_mtime > before,
        cs_errors=_cs_errors(logfile), reading=got,
    )
    _report(stage_result, raw, say)
    return stage_result


def run_merged(plan: BakePlan, say: Callable[[str], None] = _print, *,
               phases: Sequence[str] = PHASES) -> tuple[EditorStage, ...]:
    """**一趟 Unity 会话**按序跑完 setup + build（+ 不 --keep 时的 restore）。

    与 run_editor 的唯一区别是"起几次进程"，判据口径要跟着说清：

    * rc 只是**记录**（batchmode 的退出码本来就不当判据，kb/14 §6）：一趟进程一个值，
      三站照写同一个；
    * timed_out 只标在**读数没更新**的那些站上 —— 会话是被杀了，但已经写完读数的那一站
      确实跑完了，不该被连坐；
    * error CS（编译错 ⇒ executeMethod 一步都没跑）是**整趟会话**的事实：
      **每一站都算它红**（不然"读数看着绿"的那一站会把池子收割走），
      但日志明细只在**第一站**印一次，免得同一段话印三遍；
    * 每站的秒数取它自己写进读数的 seconds=（同一趟进程内的分段耗时）；
      没写成（那一站没跑完）就退化成整趟的墙钟。
    """
    logfile = plan.log_path(SESSION_ONE)
    plan.log_dir.mkdir(parents=True, exist_ok=True)
    before = {stage: (plan.reading_path(stage).stat().st_mtime
                      if plan.reading_path(stage).is_file() else 0.0) for stage in phases}
    budget = sum(_stage_timeout(plan, stage) for stage in phases)
    env = _editor_env(plan)
    if plan.keep:
        # ⛔ 与 C# 侧同一个键名（VtBakeOne.Run 读到它就跳过复原那一步）
        env["BAVTB_KEEP"] = "1"
    say(f">> Unity {METHOD_ONE}（**一趟**跑完 {' + '.join(phases)}，"
        f"硬超时 {budget:.0f} 秒 = 各站超时之和）")
    timing = run_foreground(_exec_cmd(plan, METHOD_ONE, logfile), budget,
                            f"Unity {METHOD_ONE}", env=env, say=say)
    session_errors = _cs_errors(logfile)
    out: list[EditorStage] = []
    for stage in phases:
        reading = plan.reading_path(stage)
        raw, got = _read_reading(reading)
        updated = reading.is_file() and reading.stat().st_mtime > before[stage]
        seconds = timing.seconds
        if got is not None:
            # 读数里有这一站自己记的分段秒数就用它（没有 = 那一站没跑完，退化成整趟墙钟）
            with contextlib.suppress(TypeError, ValueError):
                seconds = float(got.one("seconds"))
        one = EditorStage(
            stage=stage, method=METHOD_ONE, expect=EXPECT_VERDICT[stage], rc=timing.rc,
            seconds=seconds, timed_out=timing.timed_out and not updated,
            reading_path=reading, log_path=logfile, reading_updated=updated,
            cs_errors=session_errors, reading=got, session=SESSION_ONE,
        )
        out.append(one)
        _report(one, raw, say)
    return tuple(out)


# --------------------------------------------------------------------------- #
# 一条命令：烘
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class BakeOutcome:
    """一次烘池子的结果（⛔ 不靠退出码：ok 来自产物 + VERDICT）。"""

    ok: bool
    group: str
    item: str
    #: 这一次烘出来的**全部** item（材质顺序）。N=1 时就是 (item,)。
    items: tuple[str, ...]
    group_pool: str
    files: tuple[Path, ...]
    stages: tuple[EditorStage, ...]
    problems: tuple[str, ...]
    notes: tuple[str, ...]
    unity_left: int
    assets_removed: tuple[str, ...]
    vt_state_restored: str | None
    #: 收下来的**每一套池子**（按 group）—— "各自有账"就是它（一趟可能不止一套）
    pools: tuple[PoolSet, ...] = ()
    #: 每颗材质自己的 group（读数里的 `mat<i>_group`）—— N 颗可能落在不同 group 上
    material_groups: tuple[str, ...] = ()

    def lines(self) -> tuple[str, ...]:
        out: list[str] = []
        for stage in self.stages:
            out.append("  " + stage.line())
        out.append("")
        out.append("  group = " + (self.group or "(没拿到)"))
        out.append("  item  = " + (self.item or "(没拿到)"))
        if len(self.items) > 1:
            for index, value in enumerate(self.items):
                out.append(f"    item[{index}] = " + (value or "(没拿到)"))
            materials = " · ".join(
                f"{material_name(i)} -> {value}" for i, value in enumerate(self.items)
            )
            out.append("  材质 -> item：" + materials)
        where = self.files[0].parent if self.files else "(无)"
        out.append(f"  池子文件 {len(self.files)} 个（{len(self.pools)} 套）-> {where}")
        for got in self.pools:
            out.append("     " + got.account())
        if not self.pools:
            for path in self.files:
                out.append(f"     {path.name}  {path.stat().st_size} B")
        tail = "已写回原样" if self.vt_state_restored is not None else "删除（原本不存在）"
        out.append(f"  收尾：工程临时件删 {len(self.assets_removed)} 项 · "
                   f"残留 Unity 进程 {self.unity_left} · VT 状态文件 {tail}")
        for note in self.notes:
            out.append("  备注：" + note)
        for problem in self.problems:
            out.append("  ★ 红：" + problem)
        return tuple(out)


def bake(plan: BakePlan, *, log: Callable[[str], None] | None = None,
         unity_probe: Callable[[], int] | None = None) -> BakeOutcome:
    """跑完整条链：写临时件 -> **一趟** Unity（setup + build + restore）-> 收割 -> 清场。

    默认（plan.merged=True）三站挤进**一趟**会话；plan.merged=False（CLI 的 --split）走老的三趟。
    两条路的判据同一套：读数 mtime + VERDICT + 池子形状 + 收尾三件事。
    ⛔ 不抛「有红」的异常：时间超了 / error CS / 读数没更新都进 problems（CLI 给退出码 1）。
    只有「用不了」（源图坏、工程不对、编辑器起不来）才抛 AssetError（CLI 给退出码 2）。
    """
    say = log if log is not None else _print
    probe = unity_probe if unity_probe is not None else count_unity_processes
    original_state = read_vt_state(plan)
    shown = original_state if original_state is not None else "(文件不存在)"
    say("工程 VT 状态文件（开工时）= " + shown)
    if plan.level_problems:
        # 体检没过：**一个字节都不写、一趟 Unity 都不起**（那 3 分钟白跑才是真的浪费）。
        for line in plan.level_problems:
            say("  ★ 红：" + line)
        return BakeOutcome(
            ok=False, group="", item="", items=(), group_pool="", files=(), stages=(),
            problems=tuple(plan.level_problems), notes=tuple(plan.notes), unity_left=probe(),
            assets_removed=(), vt_state_restored=original_state,
        )
    stages: list[EditorStage] = []
    problems: list[str] = []
    notes: list[str] = list(plan.notes)
    files: tuple[Path, ...] = ()
    group = ""
    item = ""
    items: tuple[str, ...] = ()
    group_pool = ""
    #: 每颗材质自己的 group（读数里拼出来的）；pools = 真收下来的那几套
    material_groups: tuple[str, ...] = ()
    pools: tuple[PoolSet, ...] = ()
    removed: tuple[str, ...] = ()
    unity_left = -1
    merged_phases: tuple[str, ...] = ()
    try:
        if plan.only in ("all", "setup"):
            written = stage_assets(plan)
            say(f"工程临时件 {len(written)} 个 -> {plan.asset_dir}")
        if plan.only == "all" and plan.merged:
            # 三合一：**一趟** Unity 会话按序做完三站（见 run_merged 的口径）。
            # 状态文件必须先写 —— 构建那一趟读的是**进程启动时**的那份值。
            merged_phases = ("setup", "build") if plan.keep else PHASES
            write_vt_state(plan, True)
            say("Library\\EditorOnlyVirtualTextureState.json -> m_VirtualTexturingEnabled=true"
                "（构建那一趟读的是**启动时**的值，所以先写它再起这一趟）")
            say("三合一：一趟 Unity 会话做 " + " + ".join(merged_phases))
            got = run_merged(plan, say, phases=merged_phases)
            stages += got
            for stage in got:
                problems += stage.problems()
        elif plan.only in ("all", "setup"):
            write_vt_state(plan, True)
            say("Library\\EditorOnlyVirtualTextureState.json -> m_VirtualTexturingEnabled=true")
            stage = run_editor(plan, "setup", say)
            stages.append(stage)
            problems += stage.problems()
        if plan.only in ("all", "build"):
            build_stage = next((s for s in stages if s.stage == "build"), None)
            if build_stage is None:                      # --split：build 是单独一趟
                write_vt_state(plan, True)
                build_stage = run_editor(plan, "build", say)
                stages.append(build_stage)
                problems += build_stage.problems()
            if build_stage.ok and build_stage.reading is not None:
                reading = build_stage.reading
                group = (reading.one("group") or "").strip()
                item = (reading.one("item") or "").strip()
                # item_bundle= 一条材质一行（按材质顺序）—— N 颗材质就是 N 条
                items = tuple(value.strip() for value in reading.many("item_bundle"))
                group_pool = (reading.one("group_pool") or "").strip()
                # group 是**逐材质**的（实测一趟烘出两套池子）—— 逐材质那串 + 去重那串都要
                material_groups = per_material_groups(plan, reading)
                if not group or not item or not items:
                    problems.append("读数里没有 group / item")
                problems += check_items(plan, reading, items)
                problems += check_groups(plan, reading, material_groups)
                files, hurts, stale = harvest(
                    plan, expected=tuple(dict.fromkeys(g for g in material_groups if g))
                )
                problems += hurts
                notes += list(stale)
                pools = pool_sets(files)
                if group and group_pool and group != group_pool:
                    problems.append(f"读数自相矛盾：group={group} 与 group_pool={group_pool} 不同")
                if group and group not in {got.group for got in pools}:
                    problems.append(
                        f"落盘的池子与读数对不上：读数里的 group={group}，收下来的是 "
                        + ("、".join(got.group for got in pools) or "(一个都没有)")
                    )
    finally:
        if plan.only in ("all", "restore") and not plan.keep:
            done = [s for s in stages if s.stage == "restore"]
            if not (done and done[-1].ok):
                # 三合一那一趟没把「复原」写成（死在半路 / 复原本身失败）⇒ 兜底再起一趟。
                # 正常路径下这一趟根本不会起：done 里那条是绿的。
                if done:
                    say("三合一那一趟的复原没成功 —— 兜底再起一趟 Unity 专跑复原")
                stage = run_editor(plan, "restore", say)
                stages.append(stage)
                problems += stage.problems()
        restore_vt_state(plan, original_state)
        back = original_state if original_state is not None else "(原本不存在 -> 已删)"
        say("Library 状态文件写回原样：" + back)
        if not plan.keep:
            removed = clear_assets(plan)
            say("工程临时件已清：" + (", ".join(removed) if removed else "(本来就没有)"))
        unity_left = probe()
        say("残留 Unity 进程 = " + str(unity_left))
    if not files:
        problems.append("--out 下没有池子文件")
    if unity_left > 0:
        problems.append(f"还有 {unity_left} 个 Unity 进程没退出")
    # N=1 时"恰好 5 个文件"这条判据一字不变；N>1 时形状由 check_pool_shape 管
    # （分片个数随内容，函数里已经判过），这里只要求"至少有一条 .gts"。
    files_ok = len(files) == len(POOL_SHARDS) if plan.material_count == 1 else bool(files)
    ok = (not problems and bool(group) and bool(item) and bool(items)
          and len(items) == plan.material_count and files_ok)
    return BakeOutcome(
        ok=ok, group=group, item=item, items=items, group_pool=group_pool, files=files,
        stages=tuple(stages), problems=tuple(dict.fromkeys(problems)),
        notes=tuple(dict.fromkeys(notes)), unity_left=unity_left,
        assets_removed=removed, vt_state_restored=original_state,
        pools=pools, material_groups=material_groups,
    )
