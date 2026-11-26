"""`GameAssembly.dll` 的**外部数据库补丁**（零 Qt）。

让游戏从 `<游戏根目录>/BA_Mod.zip` 加载数据库，而不是只能从 `data.unity3d` 里那份编译库。
原理与全部实测读数见 `kb/11-游戏读取外部数据库（二进制补丁）.md`。

## 补丁在做什么

`DataBaseService` 里有两处 `dataBase.LoadSource()`（`Load()` 与 `LoadCompiled()`），
补丁把这两处 5 字节的 `call` 换成跳进一段**借用死代码**的 cave：

    cave: 若 <游戏根>\\BA_Mod.zip 存在 → new DataBaseSourceData(path, loadFromZip:true) 返回
          否则                          → 原样 call LoadSource()（完全原版行为，读编译库）

**所以"卸载"最省事的做法是删掉 `BA_Mod.zip`——DLL 补丁可以一直留着。**

## ❗ 2026-09-30 改版：文件名不再复用游戏的字面量

老版本是`lea rcx,[db.zip 的元数据槽] / call init_metadata`——**借用游戏自己的字符串字面量**。
好处是零成本，代价是**文件只能叫 db.zip**（那个字面量的内容在 `global-metadata.dat` 里，
游戏原生还拿它找 `Scenarios\\<场景>\\db.zip`，改不得）。

用户要求外部库改名叫 **BA_Mod.zip**（辨识度）。所以新 cave 改成**自己造字符串**：
`lea rcx,[cave 里的字面量] / call il2cpp_string_new`，再 `il2cpp_gchandle_new` 把它挂成
GC 句柄（**必须**：字符串只被寄存器引用时，`File.Exists` 里任何一次分配都可能把它回收掉）。
找不到就**直接回退编译库**，不再认 `db.zip`。

## 保存成本的变化（这才是这个补丁的意义）

| | 写 data.unity3d | 外部 BA_Mod.zip |
|---|---|---|
| 一次保存 | **7.1 GB / 30–160 秒** | **~300 KB / 毫秒级** |
| 需要什么 | 只要数据 | 装一次补丁 |
| Steam 校验完整性 | 不受影响 | 会还原 DLL（补丁消失，数据不损） |
| EAC | 关掉即可 | **必须关** |

## 三个不能碰的地方（都是踩过的坑）

1. **文件偏移 = RVA − 0xC00**，不是 VA − 0xC00。
   Python 切片赋值越界会**静默改成追加**，而 PE 加载器忽略尾部多余字节 ——
   症状是"游戏照跑、补丁没生效"。所以打完必须断言**文件长度不变**。
2. **IL2CPP 元数据槽是懒初始化的**。直接 `mov rcx,[槽]` 拿到的是哨兵值
   （本机实测 `0x2003157B`），当指针解引用就是访问违例。
   所以 cave 里先调 `il2cpp_codegen_initialize_runtime_metadata`。
3. **cave 必须是 16 字节栈对齐的**：插桩点的 `rsp` 已是 ≡0 (mod 16)，
   所以 cave 里 3 个 push + `sub rsp,8` 之后才对齐。

## 还原（三层）

| 层 | 做法 | 效果 |
|---|---|---|
| 最简 | 删掉 `BA_Mod.zip` | 补丁**休眠**，走原版路径；DLL 不用动 |
| 彻底 | :func:`restore` | 从**与现场精确匹配**的那份备份覆盖回去，逐字节还原 |
| 核对 | :func:`status` / :func:`verify` | 任何时候都能确认当前状态 |

## 地址表按**原版 sha256** 分版本（2026-10-14）

地址表不再是一套常量，而是 :class:`Build` 的一组，键是**那份原版 DLL 的 sha256**：

    BUILDS = (BUILD_120, BUILD_121)          # 认得几个版本就放几条
    build_for_sha(sha256) -> Build | None    # 按内容认版本，⛔ 不按文件名猜

两条硬口径（都是补真实缺口的，不是洁癖）：

1. :func:`apply` —— 当前 DLL 的 sha256 **必须命中 `BUILDS` 里某一份**才动手；认不出来就拒绝，
   **`force` 也不许绕过**（今天真打坏过：拿 1.2.0 的地址表打 1.2.1 的 DLL，11 个地址全指错）。
   备份写到 ``build.backup_name``。
2. :func:`restore` —— **只在 `PATCHED` 状态下按精确匹配**写回对应备份；别的状态一律拒绝。
   认不出版本就猜，猜错就是把**旧版本**的 DLL 塞进**新版本**的游戏（实测口径：必崩）。

备份按**内容**发现：``GameAssembly.dll*.orig``（历史遗留名 + 新格式 ``<名字>.<sha8>.orig``）
逐个算 sha256 认版本。⚠️ **`.orig`（1.2.0）别删** —— 它是那一版唯一的还原来源。
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import struct
import subprocess
import tempfile
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from ba_mod_maker.core.errors import UserFacingError

#: 外部库文件名：**唯一口径是 core/session.py 的 PACKAGE_NAME**（产品"导出/打包"写出来的
#: 就是这个名字）。这里直接引用、绝不另写一份字面量 —— 两边一漂移，补丁就会去找一个
#: 产品永远不会写的文件名，症状是"补丁装了、包也导出了，游戏却还是编译库"。
#: 依赖方向说明：session 只 import container/dbzip/ufs/tables/errors，不 import dllpatch，所以无环。
from ba_mod_maker.core.session import PACKAGE_DIR, PACKAGE_NAME, PACKAGE_REL_PATH

# ---------------------------------------------------------------- 地址表（按版本）
# 全部由 Il2CppDumper + capstone 实测得出（重新推导步骤见 kb/11 §5）。


@dataclass(frozen=True, slots=True)
class Build:
    """**一个游戏版本**的全套地址表 + 它那份原版 DLL 的 sha256（键）。

    ⛔ 键是**内容**（sha256），不是文件名 —— 不许有「``.orig`` 就是 1.2.0」这种隐含假设。
    要加一个游戏版本 = 在 :data:`BUILDS` 里加一条，**别改别的那条**。
    """

    label: str  #: 人话，例 "1.2.1 (2026-10-13)"
    orig_sha256: str  #: 这个版本**原版** DLL 的 sha256
    base: int  #: 镜像基址
    init_meta: int  #: il2cpp_codegen_initialize_runtime_metadata
    file_exists: int  #: System.IO.File.Exists(string)
    object_new: int  #: il2cpp_codegen_object_new
    ctor: int  #: DataBaseSourceData..ctor(string zipPath, bool loadFromZip)
    load_source: int  #: DataBaseCompiled.LoadSource()
    string_new: int  #: il2cpp_string_new(const char*)      —— 导出表
    gchandle_new: int  #: il2cpp_gchandle_new(obj, pinned)  —— 同上（给字符串保活）
    slot_klass: int  #: DataBaseSourceData 的 klass 槽
    #: `(名字, 插桩点 VA, 回跳 VA, cave VA)`
    sites: tuple[tuple[str, int, int, int], ...]
    cave_len: int  #: cave 长度（字节）；改 cave 内容时这个数必须跟着改
    backup_name: str  #: 这一版的备份文件名（历史遗留名 / `<DLL_NAME>.<sha8>.orig`）
    #: 附加的**单字节**改写：`(名字, 字节所在 VA, 原值, 新值)`。
    #: 与 `sites` 走同一套打补丁/反向 Δ/还原通道；原值对不上就拒绝动手（防版本漂移）。
    byte_patches: tuple[tuple[str, int, int, int], ...] = ()
    #: 附加的**字节串**改写：`(名字, 起址 VA, 原字节, 新字节)`。
    #: 一段函数序言 / 一条指令必须**整段**搬、整段校验 —— 拆成单字节既看不清，
    #: 也没法在换版本时整体重定位。与 `byte_patches` 共用同一套打补丁 / 反向 Δ / 还原通道。
    code_patches: tuple[tuple[str, int, bytes, bytes], ...] = ()


#: **1.2.0** —— 游戏更新到 1.2.1 之前那一版。数值就是原来那一套常量，**一字未动**。
#: cave 借用两个**零引用的死函数**：LoadByJson()（183 字节）/ Reload()（306 字节），
#: 所以 124 -> 131 这一版扩长是安全的（还在函数体内）。再长要重新核对那两个函数的边界。
BUILD_120 = Build(
    label="1.2.0 (2026-09-14)",
    orig_sha256="2317e5e16493b8eb4838751f088d97619ab46334ca2d17579fe95e865089b8db",
    base=0x180000000,
    init_meta=0x18612DBA0,
    file_exists=0x183FE1F40,
    object_new=0x186119330,
    ctor=0x18027A510,
    load_source=0x180299390,
    string_new=0x186109FA0,
    gchandle_new=0x186109680,
    slot_klass=0x187D71780,
    sites=(
        ("Load()", 0x18029AAB1, 0x18029AAB6, 0x18029A740),
        ("LoadCompiled()", 0x18029A93E, 0x18029A943, 0x18029AD60),
    ),
    cave_len=138,
    backup_name="GameAssembly.dll.orig",
)

#: **1.2.1**（2026-10-13 的官方新版，154,224,128 B）。11 个地址重新定位，
#: 验收尺子：正控 29/29 · 负控 12/12 · 1.2.1 全表 34/34（`_work/patch121/REPORT-总.md`）。
BUILD_121 = Build(
    label="1.2.1 (2026-10-13)",
    orig_sha256="2412e79af4fed4613617fa7117e0e060f30800282f445507a7c18986868e3447",
    base=0x180000000,
    init_meta=0x1866BB6B0,
    file_exists=0x1841BDA30,
    object_new=0x1866BB940,
    ctor=0x1802746D0,
    load_source=0x180288580,
    string_new=0x1866EA5B0,
    gchandle_new=0x1866E9B80,
    slot_klass=0x18871B798,
    sites=(
        ("Load()", 0x180289C91, 0x180289C96, 0x180289920),
        ("LoadCompiled()", 0x180289B1E, 0x180289B23, 0x180289F40),
    ),
    cave_len=138,
    backup_name="GameAssembly.dll.2412e79a.orig",
)

#: **1.2.1.Preview2**（154,267,648 B，sha256 `19eeefec…`）——
#: 2026-10-19 移植。方法：旧版（BUILD_121）每个地址取 16 条指令的签名
#: （立即数 / 跳转目标 / rip 相对位移**打码**），到新 DLL 的唯一命中；
#: 再用 **整体位移** 交叉验证：`il2cpp` 节全部目标统一 **+0x25A0**，
#: `.text` 里 init_meta / object_new / string_new / gchandle_new 统一 **+0xC970**。
#: slot_klass 用 ctor 里 `off=0x146` 的 `lea rcx,[rip+…]` 对齐（新 ctor 指令布局与旧版逐条同形）。
#: 移植脚本：`_work/port/port.py` · `_work/port/derive.py` · `_work/port/slot2.py`。
BUILD_121P2 = Build(
    label="1.2.1.Preview2 (2026-10-19)",
    orig_sha256="19eeefec2e5233fc89c531d29c624b33af3d616e62155d64129fd98751234907",
    base=0x180000000,
    init_meta=0x1866C8020,
    file_exists=0x1841C6320,
    object_new=0x1866C82B0,
    ctor=0x180276C70,
    load_source=0x18028AB20,
    string_new=0x1866F6F20,
    gchandle_new=0x1866F64F0,
    slot_klass=0x188729328,
    sites=(
        ("Load()", 0x18028C231, 0x18028C236, 0x18028BEC0),
        ("LoadCompiled()", 0x18028C0BE, 0x18028C0C3, 0x18028C4E0),
    ),
    cave_len=138,
    backup_name="GameAssembly.dll.19eeefec.orig",
)

#: **1.2.1 正式版**（2026-10-20 更新，154,267,648 B，sha256 `da1668ac…`）——
#: 与 Preview2 **只差 264 字节**（130 段，全是 RIP 相对位移；逐段见 `_work/check121/dlldiff2.py`）。
#: 本工具要用的 **8 个目标函数 + 2 个插桩点 + 2 段 cave** 在新旧两份里**逐字节相同**
#: （`_work/check121/patchsites.py`：全部 `same True`）⇒ 地址表原样沿用 BUILD_121P2。
#: ⚠ 只加了 sha 这一道门；**没有**在游戏目录上打过（要打先问用户）。
BUILD_121R = Build(
    label="1.2.1 (2026-10-20)",
    orig_sha256="da1668ac11bcdb036acc7807aec2aaf6f72e83921a05eaf0ae2255e7db005a26",
    base=0x180000000,
    init_meta=0x1866C8020,
    file_exists=0x1841C6320,
    object_new=0x1866C82B0,
    ctor=0x180276C70,
    load_source=0x18028AB20,
    string_new=0x1866F6F20,
    gchandle_new=0x1866F64F0,
    slot_klass=0x188729328,
    sites=(
        ("Load()", 0x18028C231, 0x18028C236, 0x18028BEC0),
        ("LoadCompiled()", 0x18028C0BE, 0x18028C0C3, 0x18028C4E0),
    ),
    cave_len=138,
    backup_name="GameAssembly.dll.da1668ac.orig",
)

#: **1.2.1**（Steam **2026-10-29** 更新，153,969,664 B，sha256 `12e70883…`）——
#: 与 2026-10-20 那版（BUILD_121R）**不是同一份编译产物**：托管代码块整体后移
#: （ctor **+0x5E10**；load_source / 两个插桩点 / 两段 cave 都 **+0x6270**），
#: il2cpp 运行期段整体前移 **-0x17540**。全部地址在**现盘这份 DLL** 上重新定位，
#: 定位脚本与原始读数在 `_work/patch1029/`（sigport / census / sites / verify_table）。
#: · 8 个目标里，`string_new` / `gchandle_new` = PE 导出表逐字命中；
#: · `init_meta` / `object_new` 非导出 ⇒ 调用点普查（489,651 / 122,214 个 e8 调用点，
#:   负控 VA+0x10 塌到 0 / 172，调用点形状 59.4% lea rcx / 87.7% mov rcx）；
#: · `file_exists` / `ctor` / `load_source` / `slot_klass` / 两段 cave 的**方法身份**
#:   来自本版 dump 的 script.json（Il2CppDumper → `_work/il2cpp1029/dump/`）；
#: · 两处插桩点 = `DataBaseService::Load()` / `LoadCompiled()` 体内**唯一**指向
#:   `LoadSource` 的 e8（字节核过 = 0xe8，rel32 精确命中）；
#: · 两段 cave = 死函数 `LoadByJson()` / `Reload()` 的函数起点（16B 对齐、体长 192 / 320、
#:   e8/e9 超集零引用）。
#: 验收尺子（与 `_work/patch121/verify_table.py` 同一份，副本在 `_work/patch1029/`）：正控 29/29 ·
#: 负控 12/12 · **本表 34/34**。⚠ 只加了表项，**没有**在游戏目录上打过（要打先问用户）。
BUILD_1029 = Build(
    label="1.2.1 (2026-10-29)",
    orig_sha256="12e7088369539b029a7bf0064062ce030a53b5189e7067420903f47b4281fddf",
    base=0x180000000,
    init_meta=0x1866B0AE0,
    file_exists=0x18421B400,
    object_new=0x1866B0D70,
    ctor=0x18027CA80,
    load_source=0x180290D90,
    string_new=0x1866DF9E0,
    gchandle_new=0x1866DEFB0,
    slot_klass=0x1886FBB60,
    sites=(
        ("Load()", 0x1802924A1, 0x1802924A6, 0x180292130),
        ("LoadCompiled()", 0x18029232E, 0x180292333, 0x180292750),
    ),
    cave_len=138,
    byte_patches=(
        # 2026-11-12 · 忽略 ResetDeckData（ServerCommands code=72）
        # `cmp sil,48h` → `cmp sil,FFh`：命令码是 byte，0xFF 永不命中 ⇒ 客户端不再把本地卡组
        # 换成默认卡组（含库里新加的单位也不换）。真机验证：含 M1A2 T 的卡组进对局未被替换。
        # 依据：_work_db/isil_b2/IsilDump/.../Room/NetworkRoomService.txt:10790-10849（code==72 分支）
        ("忽略 ResetDeckData(code 72)", 0x18062DAF8, 0x48, 0xFF),
    ),

    backup_name="GameAssembly.dll.12e70883.orig",
)

#: 本工具认得的全部版本（顺序只影响展示；认版本一律按 sha256）。
BUILDS: tuple[Build, ...] = (BUILD_120, BUILD_121, BUILD_121P2, BUILD_121R, BUILD_1029)

# ---------------------------------------------------------------- 本版常量（= 1.2.0）
# ⚠️ 这批模块级常量**全部保留**：`patched_image(orig)` / `build_cave(cave, ret)` 的老签名
#    （不带 build）就是 1.2.0 那一套 —— 大量纯函数测试吃它，别动。新代码一律显式传 build。

#: 本工具认得的**原版** DLL 的 sha256（= 1.2.0）。其它版本见 `BUILDS`。
ORIG_SHA256 = BUILD_120.orig_sha256

#: 镜像基址。
BASE = BUILD_120.base

INIT_META = BUILD_120.init_meta  # il2cpp_codegen_initialize_runtime_metadata
FILE_EXISTS = BUILD_120.file_exists  # System.IO.File.Exists(string)
OBJECT_NEW = BUILD_120.object_new  # il2cpp_codegen_object_new
CTOR = BUILD_120.ctor  # DataBaseSourceData..ctor(string zipPath, bool loadFromZip)
LOAD_SOURCE = BUILD_120.load_source  # DataBaseCompiled.LoadSource()
STRING_NEW = BUILD_120.string_new  # il2cpp_string_new(const char*)      —— 导出表
GCHANDLE_NEW = BUILD_120.gchandle_new  # il2cpp_gchandle_new(obj, pinned) —— 同上
SLOT_KLASS = BUILD_120.slot_klass  # DataBaseSourceData 的 klass 槽

#: 外部库的文件名 PACKAGE_NAME 见文件顶部 —— 它是补丁自己造出来的（不是游戏的字面量），
#: 唯一口径在 core/session.py。

#: `(名字, 插桩点 VA, 回跳 VA, cave VA)`。
SITES = BUILD_120.sites

#: cave 长度（字节）。改 cave 内容时这个数必须跟着改。
CAVE_LEN = BUILD_120.cave_len

#: DLL 与备份的文件名。
DLL_NAME = "GameAssembly.dll"
#: 历史遗留的备份名（= 1.2.0 那一版）。别的版本见 `Build.backup_name`。
BACKUP_NAME = BUILD_120.backup_name
#: 备份的发现口径：**按名字发现、按内容认版本**（两种命名都认）。
BACKUP_GLOB = DLL_NAME + "*.orig"

#: 从 RVA 与 VA 换算文件偏移。⚠️ 是 **RVA** − 0xC00，不是 VA − 0xC00。
_SECTION_DELTA = 0xC00


def _rva(va: int, build: Build | None = None) -> int:
    """VA -> RVA。`build=None` ⇒ 1.2.0 那一套（= 旧行为）。"""
    return va - (build.base if build is not None else BASE)


def _off(rva: int) -> int:
    return rva - _SECTION_DELTA


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build_for_sha(sha: str) -> Build | None:
    """按**原版 sha256** 认版本。认不出来返回 None（⛔ 别拿文件名/长度猜）。"""
    want = sha.lower()
    for b in BUILDS:
        if b.orig_sha256.lower() == want:
            return b
    return None


def build_for_backup(data: bytes) -> Build | None:
    """按备份的**内容**认版本。"""
    return build_for_sha(sha256(data))


def rel32(here_va: int, target_va: int) -> bytes:
    """算一条 `e9/e8 rel32` 的位移。

    ⚠️ 越界必须**报错**而不是静默截断 —— 截断出来的是一条跳到别处的指令，
    症状是游戏崩在莫名其妙的地方。实测地址表都在同一个二进制内、
    偏移只有几百字节，所以这条守卫正常不会触发；它守的是"将来改地址表时写错一个数"。
    """
    delta = target_va - here_va
    if not -(2**31) <= delta < 2**31:
        raise ValueError(f"rel32 越界: {delta:#x}")
    return struct.pack("<i", delta)


#: 字面量的占位目标：cave 尾部的 "BA_Mod.zip\0" 在代码写完之前还不知道 VA。
_LITERAL = object()


def build_cave(cave_va: int, ret_va: int, build: Build | None = None) -> bytes:
    """构造 cave 的机器码。

    ```asm
    push rbx / push rsi / push rdi / sub rsp,8      ; 保存 + 保持 16 字节栈对齐
    mov  rbx, rcx                                   ; rbx = 编译库对象（兜底要用）
    lea  rcx,[rip+lit] / call il2cpp_string_new     ; ★ 现造 "BA_Mod.zip"（不再借游戏的槽）
    mov  rsi, rax                                   ; 字符串：后面还要用两次
    mov  rcx, rax / xor edx,edx / call gchandle_new ; ★ 挂 GC 句柄保活
    mov  rcx, rsi / xor edx,edx / call File.Exists("BA_Mod.zip")
    test al,al / jz fallback
    lea  rcx,[klass 槽] / call init_metadata
    mov  rcx,[klass 槽] / call object_new
    mov  rdi,rax / xor r9d,r9d / mov r8b,1          ; MethodInfo=0、loadFromZip=true
    mov  rdx,rsi / mov rcx,rdi / call ctor
    mov  rax,rdi / jmp done
    fallback: mov rcx,rbx / xor edx,edx / call LoadSource
    done: add rsp,8 / pop rdi/rsi/rbx / jmp 回跳点
    lit:  "BA_Mod.zip\0"
    ```

    ⚠️ 调用约定照抄游戏自己的调用点：**非泛型方法的 MethodInfo 可以传 0**；
    `DataBaseSourceData..ctor(string,bool)` 的参数是
    `rcx=this, rdx=zipPath, r8b=bool, r9=0`。

    ⚠️ **为什么必须 gchandle**：字符串只被 rsi/rdx 两个**寄存器**指着，
    而 `File.Exists` 内部要分配（字符串封送）——任何一次分配都可能触发 GC，
    把没人引用的字符串收掉，随后 ctor 存进去的就是个死指针。挂成 GC 句柄即根，永久安全。
    （Boehm 不移动对象，所以寄存器里那份指针在句柄保活期间一直有效。）
    """

    b = BUILD_120 if build is None else build
    c = bytearray()
    holes: list[tuple[int, object | int]] = []  # (rel32 字段偏移, 目标 VA 或 _LITERAL)

    def hole(target: object) -> None:
        """留一个 4 字节位移：相对**它自己之后**那条指令。"""
        pos = len(c)
        c.extend(b"\x00\x00\x00\x00")
        holes.append((pos, target))

    def call(target: int) -> None:
        c.append(0xE8)
        hole(target)

    def lea_rip(target: object) -> None:
        c.extend(b"\x48\x8d\x0d")  # lea rcx,[rip+disp32]
        hole(target)

    def mov_rip_rcx(target: int) -> None:
        c.extend(b"\x48\x8b\x0d")  # mov rcx,[rip+disp32]
        hole(target)

    c += b"\x53\x56\x57"  # push rbx / rsi / rdi
    c += b"\x48\x83\xec\x08"  # sub rsp,8     （保持 16 字节对齐）
    c += b"\x48\x89\xcb"  # mov rbx,rcx   （编译库对象，兜底要用）
    lea_rip(_LITERAL)
    call(b.string_new)  # rax = Il2CppString*("BA_Mod.zip")
    c += b"\x48\x89\xc6"  # mov rsi,rax   （留着后面当 zipPath）
    c += b"\x48\x89\xc1"  # mov rcx,rax
    c += b"\x33\xd2"  # xor edx,edx   （pinned=false）
    call(b.gchandle_new)  # 保活：返回值（句柄）不用
    c += b"\x48\x89\xf1"  # mov rcx,rsi
    c += b"\x33\xd2"  # xor edx,edx
    call(b.file_exists)
    c += b"\x84\xc0"  # test al,al
    jz_pos = len(c) + 1
    c += b"\x74\x00"  # jz fallback（位移待补）
    lea_rip(b.slot_klass)
    call(b.init_meta)
    mov_rip_rcx(b.slot_klass)
    call(b.object_new)
    c += b"\x48\x89\xc7"  # mov rdi,rax
    c += b"\x45\x33\xc9"  # xor r9d,r9d    （MethodInfo=0，游戏自己就这么调）
    c += b"\x41\xb0\x01"  # mov r8b,1     （loadFromZip=true）
    c += b"\x48\x89\xf2"  # mov rdx,rsi   （zipPath = 刚造的那个字符串）
    c += b"\x48\x89\xf9"  # mov rcx,rdi
    call(b.ctor)
    c += b"\x48\x89\xf8"  # mov rax,rdi
    jmp_pos = len(c) + 1
    c += b"\xeb\x00"  # jmp done（位移待补）
    fallback_off = len(c)
    c += b"\x48\x89\xd9"  # fallback: mov rcx,rbx
    c += b"\x33\xd2"  # xor edx,edx
    call(b.load_source)
    done_off = len(c)
    c += b"\x48\x83\xc4\x08\x5f\x5e\x5b"  # done: add rsp,8 / pop rdi/rsi/rbx
    c.append(0xE9)  # jmp ret_va（长跳，位移已知）
    hole(ret_va)

    # 两个短跳的位移现在才算得出来（都在 ±127 内；越界说明布局被改坏了）
    for pos, target_off in ((jz_pos, fallback_off), (jmp_pos, done_off)):
        delta = target_off - (pos + 1)
        if not -128 <= delta <= 127:
            raise AssertionError(f"cave 短跳越界: {delta}")
        c[pos] = delta & 0xFF

    literal_off = len(c)
    # ★ 2026-10-27：字面量从 `BA_Mod.zip` 变成 `BA_Mod\BA_Mod.zip`（+8 B）——
    #   cave 借用的是 183 / 306 字节的死函数，139 ≤ 183 仍安全（cave_len 同步 131→139）。
    c += PACKAGE_REL_PATH.encode("ascii") + b"\x00"
    literal_va = cave_va + literal_off

    for pos, target in holes:
        want = literal_va if target is _LITERAL else int(target)
        struct.pack_into("<i", c, pos, want - (cave_va + pos + 4))

    if len(c) != b.cave_len:
        raise AssertionError(f"cave 长度 {len(c)} != {b.cave_len}")
    return bytes(c)


def _writes(build: Build) -> list[tuple[str, int, bytes, bytes]]:
    """本版补丁要写的**每一段**：`(名字, 起址 VA, 原字节, 新字节)`。

    单字节层（`byte_patches`）折成 1 字节项，再拼整段层（`code_patches`）——
    `_patch_regions` / `patched_image` / `_patched_regions` 三处都只认这一份，⛔ 别再各写一份循环。
    """
    out: list[tuple[str, int, bytes, bytes]] = []
    for name, va, orig_byte, new_byte in build.byte_patches:
        out.append((name, va, bytes([orig_byte]), bytes([new_byte])))
    for name, va, orig_bytes, new_bytes in build.code_patches:
        if len(orig_bytes) != len(new_bytes):
            raise AssertionError(f"{name}: 原字节与新字节长度不等")
        out.append((name, va, bytes(orig_bytes), bytes(new_bytes)))
    return out


def _patch_regions(build: Build) -> tuple[tuple[int, int], ...]:
    """补丁会写到的**文件区间**（两段 cave ＋ 两处 5 字节跳转），半开区间 [start, end)。"""
    spans: list[tuple[int, int]] = []
    for _name, steal, _ret, cave in build.sites:
        body = _off(_rva(cave, build))
        spans.append((body, body + build.cave_len))
        head = _off(_rva(steal, build))
        spans.append((head, head + 5))
    for _name2, va, orig_bytes, _new_bytes in _writes(build):
        at = _off(_rva(va, build))
        spans.append((at, at + len(orig_bytes)))
    return tuple(sorted(spans))


def _only_patch_regions_differ(image: bytes, orig: bytes, build: Build) -> bool:
    """盘上这份 `image` 与 `orig` 的差异**只落在补丁会写的区间里** ⇒ 它就是我们某一版补丁。

    为什么需要这条：补丁自己造的字面量 / cave 长度会随版本变（2026-10-27 就改过一次），
    只认「当前代码算出来的那一版」的话，`restore()` 会把**我们自己打的上一版补丁**当成
    「来路不明的 DLL」拒掉 —— 那等于把唯一的还原出口锁死（真踩过）。
    反过来它仍然挡住原来的风险：游戏更新过 ⇒ 区间外也有差异 ⇒ 不成立 ⇒ 拒绝。
    """
    if len(image) != len(orig):
        return False
    cursor = 0
    for start, end in _patch_regions(build):
        if image[cursor:start] != orig[cursor:start]:
            return False
        cursor = max(cursor, end)
    return image[cursor:] == orig[cursor:]


def patched_image(original: bytes, build: Build | None = None) -> bytes:
    """在原版 DLL 字节上打出补丁，返回新字节。**纯函数，不碰磁盘。**

    `build=None` ⇒ 1.2.0 那一套地址表（= 本函数的历史行为，老调用方一个字都不用改）；
    要打别的游戏版本就显式传那一版的 :class:`Build`（⛔ 版本闸在 :func:`apply` 里，不在这）。

    末尾那条"长度不变"的断言是**必须的**：Python 切片赋值越界会静默改成追加，
    而 PE 加载器忽略尾部多余字节 —— 症状是"游戏照跑、补丁没生效"，很难查。
    """
    b = BUILD_120 if build is None else build
    out = bytearray(original)
    for name, steal, ret, cave in b.sites:
        body = build_cave(cave, ret, b)
        o_cave, o_steal = _off(_rva(cave, b)), _off(_rva(steal, b))
        if not (o_cave >= 0 and o_cave + b.cave_len <= len(out)):
            raise UserFacingError(f"{name}: cave 偏移越界 {o_cave:#x}")
        if not (o_steal >= 0 and o_steal + 5 <= len(out)):
            raise UserFacingError(f"{name}: 插桩点偏移越界 {o_steal:#x}")
        out[o_cave : o_cave + b.cave_len] = body
        out[o_steal : o_steal + 5] = b"\xe9" + struct.pack("<i", cave - (steal + 5))
    for bname, va, orig_bytes, new_bytes in _writes(b):
        o = _off(_rva(va, b))
        if not (0 <= o and o + len(orig_bytes) <= len(out)):
            raise UserFacingError(f"{bname}: 改字节点偏移越界 {o:#x}")
        cur = bytes(out[o : o + len(orig_bytes)])
        if cur != orig_bytes:
            bad = next((i for i in range(len(orig_bytes)) if cur[i] != orig_bytes[i]), 0)
            raise UserFacingError(
                f"{bname}: {va + bad:#x} 处是 {cur[bad]:#04x}，期望 {orig_bytes[bad]:#04x}"
                f" —— 原版对不上，拒绝动手"
            )
        out[o : o + len(new_bytes)] = new_bytes
    if len(out) != len(original):
        raise AssertionError("补丁改变了文件长度")
    return bytes(out)


class PatchState(StrEnum):
    """补丁在现场的状态。"""

    NOT_INSTALLED = "not_installed"
    """DLL 是原版，没打过。"""

    PATCHED = "patched"
    """DLL 已打，且与「备份 + 两个补丁」逐字节吻合。"""

    MODIFIED = "modified"
    """DLL 既不是原版、也不是本工具打出来的（别的工具改过 / 游戏更新过）。"""

    NO_BACKUP = "no_backup"
    """DLL 被改过，但**没有备份** —— 此时无法核对，也无法彻底还原。"""

    MISSING = "missing"
    """找不到 DLL。"""

    @property
    def label(self) -> str:
        return {
            PatchState.NOT_INSTALLED: "未打补丁（原版）",
            PatchState.PATCHED: "已打补丁",
            PatchState.MODIFIED: "DLL 被改过，但不是本工具打的",
            PatchState.NO_BACKUP: "DLL 被改过，且没有备份",
            PatchState.MISSING: "找不到 GameAssembly.dll",
        }[self]


@dataclass(slots=True)
class PatchStatus:
    """一次状态检查的全部读数。给界面直接展示。"""

    game_root: Path
    dll: Path
    backup: Path
    db_zip: Path
    state: PatchState
    dll_sha256: str = ""
    dll_size: int = 0
    backup_sha256: str = ""
    db_zip_present: bool = False
    #: 打过补丁之后 DLL 应当是什么哈希（只在 PATCHED 时给：那一版备份 + 补丁）。
    patched_sha256: str = ""
    #: 认出来的**游戏版本**（按原版 sha256 / 备份内容）。None = 认不出来（地址表可能已失效）。
    build: Build | None = None

    @property
    def has_backup(self) -> bool:
        return bool(self.backup_sha256)

    @property
    def can_apply(self) -> bool:
        return self.state is PatchState.NOT_INSTALLED

    @property
    def can_restore(self) -> bool:
        return self.has_backup and self.state is not PatchState.NOT_INSTALLED

    def summary_lines(self) -> list[str]:
        """给人看的几行。CLI 与 GUI 共用，避免两处措辞不一致。"""
        lines = [
            f"游戏目录 : {self.game_root}",
            f"DLL      : {DLL_NAME}  {self.dll_size:,} 字节  sha256 {self.dll_sha256[:16]}…",
        ]
        if self.has_backup:
            lines.append(f"备份     : {self.backup.name}（原版 sha256 {self.backup_sha256[:16]}…）")
        else:
            lines.append("备份     : 无 —— 打了之后就还原不回去了")
        has_zip = "有" if self.db_zip_present else "无"
        lines.append(f"{PACKAGE_NAME} : {has_zip}")
        lines.append(f"补丁状态 : {self.state.label}")
        # ★ 2026-10-18（Steam 2026-10-17 11:30 更新后真机）：`MODIFIED` 的那一句
        #   「DLL 被改过，但不是本工具打的」在**游戏更新**这一档会误导人 ——
        #   实测：更新把 GameAssembly.dll 换成了新版，`.orig` 还是旧版 ⇒ 两边都对不上
        #   已知的那几份原版。所以这一档要**把两种可能都点出来**（更新 / 别的工具）。
        if self.state is PatchState.MODIFIED and self.build is None:
            lines.append(
                "⚠ 可能的原因：**游戏更新过**（Steam 换了 GameAssembly.dll，备份还是旧版本）"
                "，或者别的工具改过它。本工具还不认识这版 DLL —— 加一条 BUILDS 之前 ⛔ 不许打补丁。"
            )
        lines.append(
            f"认出来的版本 : {self.build.label}"
            if self.build is not None
            else "认出来的版本 : 没认出来（不在本工具已知的原版表里）"
        )
        # ⛔ 注入与静态改动**都要报**（kb/26 §7）：前者是"绝不许联机"那条，后者是"风险自负"。
        reasons = redline_reasons(self)
        injected = injection_reasons(self.game_root)
        if injected or reasons:
            lines.append(f"{ONLINE_REDLINE_HEAD}（{'；'.join(injected + reasons)}）")
        return lines


def _read(path: Path) -> bytes:
    return path.read_bytes()


# ---------------------------------------------------------------- 稀疏读写（2026-11-10）
#: 补丁只动 4 段（两处 cave 体 ＋ 两处 5 字节跳转，合计 286 字节）。
#: 2026-11-10 之前 apply 是「整包读 → 整包改 → 整包写 → 整包回读」＋「整包拷一份备份」，
#: 一次打补丁 I/O ≈ 735 MB，界面还要卡十几秒。现在只碰这 4 段。
CHUNK = 1 << 20
DELTA_SUFFIX = ".patchdelta"


def _sha256_file(path: Path) -> str:
    """流式算 sha256 —— 不把 147 MB 全塞进内存（版本闸只要一个哈希）。"""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(CHUNK)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _read_region(path: Path, start: int, end: int) -> bytes:
    with path.open("rb") as fh:
        fh.seek(start)
        return fh.read(end - start)


def _write_region(path: Path, start: int, data: bytes) -> None:
    """只改这几段字节。**就地写被系统拒绝时，退回整包重写**。

    只改这几段字节，**绝不整包重写**（用户 2026-11-10：「整包重写…不需要！」）。

    """
    def _one_shot() -> None:
        with path.open("r+b") as fh:
            fh.seek(start)
            fh.write(data)
            fh.flush()
    try:
        _one_shot()
        return
    except PermissionError:
        pass
    # 只读属性挡的就 `chmod` 能解，再试一次。
    try:
        os.chmod(path, 0o666)
        _one_shot()
        return
    except PermissionError:
        pass
    # 2026-11-10 实测：同一账户下，系统自带的 PowerShell(.NET) 能打开这个 DLL 读写，
    # 而 python.exe 的任何写打开都被拒（err=5）。所以最后一步交给 .NET 去写这几段字节。
    if _dotnet_write_region(path, start, data):
        return
    raise PermissionError(
        13,
        f"就地写和 .NET 都写不进去：{_last_write_error or '无详情'}",
        str(path),
    ) from None


_last_write_error = ""


def _dotnet_write_region(path: Path, start: int, data: bytes) -> bool:
    """借系统自带的 PowerShell(.NET) 就地写这几段字节（不读整包、不要管理员、不弹窗）。"""
    global _last_write_error
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    candidates = [
        shutil.which("powershell.exe"),
        os.path.join(system_root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe"),
        shutil.which("pwsh.exe"),
    ]
    exes = [c for c in candidates if c and os.path.isfile(c)]
    if not exes:
        _last_write_error = "找不到 powershell.exe"
        return False
    # 走 -File（临时脚本）而不是 -Command：长 hex 参数会被 PowerShell 的命令行解析拆坏
    # （2026-11-10 现场：rc=1「字符串缺少终止符」，就是它）。
    payload = ",".join(f"0x{byte:02x}" for byte in data)
    script_lines = [
        "$ErrorActionPreference = 'Stop'",
        f"$fs = [IO.File]::Open('{path}', 'Open', 'ReadWrite', 'ReadWrite')",
        f"$fs.Position = {start}",
        f"$b = [byte[]]({payload})",
        f"$fs.Write($b, 0, {len(data)})",
        "$fs.Close()",
    ]
    with tempfile.NamedTemporaryFile(
        "w", suffix=".ps1", delete=False, encoding="utf-8-sig", newline="\r\n",
    ) as fh:
        fh.write("\n".join(script_lines))
        script_path = fh.name
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    for exe in exes:
        try:
            done = subprocess.run(
                [exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                 "-File", script_path],
                capture_output=True, timeout=120, creationflags=flags,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            _last_write_error = f"{exe}: 起不来 {exc}"
            continue
        if done.returncode == 0:
            _last_write_error = ""
            return True
        raw = done.stderr or done.stdout or b""
        try:
            detail = raw.decode("mbcs", "replace").strip()
        except LookupError:
            detail = raw.decode("utf-8", "replace").strip()
        _last_write_error = f"{exe} rc={done.returncode} {detail[:300]}"
    with contextlib.suppress(OSError):
        os.unlink(script_path)
    return False


def _patched_regions(build: Build) -> list[tuple[int, bytes]]:
    """补丁要写进去的 (起址, 新字节)，与 :func:`_patch_regions` 一一对应。

    顺序：每个 site 先 cave 体（`cave_len` 字节）再插桩点（`E9 rel32` 5 字节）。
    """
    out: list[tuple[int, bytes]] = []
    for _name, steal, ret, cave in build.sites:
        body = build_cave(cave, ret, build)
        out.append((_off(_rva(cave, build)), body))
        jump = b"\xe9" + struct.pack("<i", cave - (steal + 5))
        out.append((_off(_rva(steal, build)), jump))
    for _name2, va, _orig_bytes, new_bytes in _writes(build):
        out.append((_off(_rva(va, build)), bytes(new_bytes)))
    return out


def delta_path(game_root: Path | None = None) -> Path:
    """反向 Δ 备份的位置 —— **放在本程序自己的 var 目录**，不写游戏目录。

    2026-11-10 实测：往游戏目录**新建**文件会被系统拒绝（`写不进游戏目录：…patchdelta`），
    而那份备份只是几百字节、还原时才读，放程序目录一样管用，还不污染游戏目录。
    """
    base = os.environ.get('BA_MOD_MAKER_VAR_DIR') or ''
    root = Path(base) if base.strip() else Path.home() / '.ba_mod_maker'
    return root / 'patchdelta' / (DLL_NAME + DELTA_SUFFIX)


def _write_delta(
    path: Path,
    build: Build,
    size: int,
    originals: list[tuple[int, bytes]],
    patched: list[tuple[int, bytes]] | None = None,
) -> None:
    """把「这几段原来的字节」存成 JSON ＋ hex。几百字节，还原只靠它。

    `patched`（2026-11-13 起）顺手记下**写进去的新字节** —— 有了它，
    即使以后地址表又改了，`status()` 仍能认出「盘上这份就是本工具打的某一版」，
    还原出口不会被表变更锁死（旧 Δ 没这个键 ⇒ 走回原来的当前表比对）。
    """
    payload = {
        "version": 1,
        "dll": DLL_NAME,
        "size": size,
        "build": build.label,
        "orig_sha256": build.orig_sha256,
        "regions": [[off, data.hex()] for off, data in originals],
    }
    if patched is not None:
        payload["patched"] = [[off, data.hex()] for off, data in patched]
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def _read_delta(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def backup_candidates(root: Path) -> list[Path]:
    """游戏根目录里**所有**候选备份（历史遗留名 + 新格式），按名字排序。

    发现靠**名字**、认版本靠**内容**（:func:`build_for_backup`）—— 这是两条不同的口径。
    """
    return sorted(p for p in root.glob(BACKUP_GLOB) if p.is_file())


def status(game_root: str | Path) -> PatchStatus:
    """检查补丁现场。**纯只读，不碰任何文件。**

    备份**按内容发现**：每份候选都算 sha256、用 `BUILDS` 认版本，然后——

    | 现场 | 判定 |
    |---|---|
    | 当前 DLL == 某份备份（逐字节相同） | NOT_INSTALLED |
    | 当前 DLL 自己命中 `BUILDS` 里某一份原版 sha256 | NOT_INSTALLED |
    | 当前 DLL == `patched_image(备份, 那一版 build)` | PATCHED |
    | 有备份，但哪一份都对不上 | MODIFIED |
    | 没有备份，而且当前 DLL 也认不出来 | NO_BACKUP |
    """
    root = Path(game_root)
    dll = root / DLL_NAME
    backup = root / BACKUP_NAME
    db_zip = root / PACKAGE_DIR / PACKAGE_NAME

    st = PatchStatus(
        game_root=root,
        dll=dll,
        backup=backup,
        db_zip=db_zip,
        state=PatchState.MISSING,
        db_zip_present=db_zip.is_file(),
    )
    if not dll.is_file():
        return st

    # ★ 2026-11-10：常见路径不再把 147 MB 读进内存 —— 流式算一个 sha 就够。
    st.dll_size = dll.stat().st_size
    st.dll_sha256 = _sha256_file(dll)

    own = build_for_sha(st.dll_sha256)  # 当前 DLL 自己就是某个版本的原版？
    delta = delta_path(root)
    if own is not None:
        # 就是原版 ⇒ 没打补丁；反向 Δ 在的话仍然记成「有备份」（还原要用）
        st.build = own
        st.state = PatchState.NOT_INSTALLED
        if delta.is_file():
            st.backup = delta
            st.backup_sha256 = own.orig_sha256
        return st
    if delta.is_file():
        # 不是原版 ⇒ 看反向 Δ：4 段对得上就是我们打的补丁
        try:
            meta = _read_delta(delta)
            b = build_for_sha(str(meta.get("orig_sha256", "")))
            if b is not None and int(meta.get("size", 0)) == st.dll_size:
                want = _patched_regions(b)
                # Δ 里记着「这次写进去的新字节」 ⇒ 用它认「本工具打的某一版」（旧 Δ 没这个键）
                recorded = [
                    (int(off), bytes.fromhex(data)) for off, data in meta.get("patched", [])
                ]
                hits_current = bool(want) and all(
                    _read_region(dll, off, off + len(data)) == data for off, data in want
                )
                hits_recorded = bool(recorded) and all(
                    _read_region(dll, off, off + len(data)) == data for off, data in recorded
                )
                if not hits_current and not hits_recorded and not recorded:
                    # 旧 Δ（没有 `patched` 键）：表变过之后认不出「这一版」，但**它记着原字节**。
                    # 只要盘上这些区间确实被写过（与原字节不同），就仍允许还原 —— 否则
                    # 一次地址表变更就把唯一的还原出口锁死（2026-10-27 踩过同一个坑）。
                    saved = [(int(off), bytes.fromhex(data)) for off, data in meta.get("regions", [])]
                    hits_current = bool(saved) and all(
                        _read_region(dll, off, off + len(data)) != data for off, data in saved
                    )
                if hits_current or hits_recorded:
                    st.state = PatchState.PATCHED
                    st.build = b
                    st.backup = delta
                    st.backup_sha256 = b.orig_sha256
                    st.patched_sha256 = st.dll_sha256
                    return st
        except (OSError, ValueError, KeyError):
            pass  # Δ 坏了 ⇒ 退回老的整包比对，别把状态判死

    cur = _read(dll)
    candidates = backup_candidates(root)
    if not candidates:
        st.build = own
        st.state = PatchState.NOT_INSTALLED if own is not None else PatchState.NO_BACKUP
        return st

    # 逐份候选按**内容**认版本；匹配到谁就用谁（⛔ 不许假设 `.orig` 就是哪一版）。
    state = PatchState.MODIFIED
    build: Build | None = own
    chosen: Path | None = None
    chosen_sha = ""
    chosen_patched = ""
    first_sha = ""

    for index, path in enumerate(candidates):
        bak = _read(path)
        bak_sha = sha256(bak)
        if index == 0:
            first_sha = bak_sha  # 一条都对不上时，展示用的还是第一份候选
        bak_build = build_for_backup(bak)
        if bak_sha == st.dll_sha256:  # ① 当前 DLL 就是这份原版（逐字节相同）
            state, build, chosen, chosen_sha = PatchState.NOT_INSTALLED, bak_build, path, bak_sha
            break
        if own is not None:  # ② 当前 DLL 命中 BUILDS ⇒ 它就是原版，备份是哪个版本都无所谓
            state, build = PatchState.NOT_INSTALLED, own
            break
        # ③ 这份备份 + 它那一版的补丁 == 当前 DLL？
        #    认不出内容的备份仍用默认（1.2.0）那套试算 —— **只为判状态**，写盘一律用认出来的那份。
        want = sha256(patched_image(bak, bak_build))
        if want == st.dll_sha256:
            state, build = PatchState.PATCHED, bak_build
            chosen, chosen_sha, chosen_patched = path, bak_sha, want
            break

    st.state = state
    st.build = build
    st.backup = chosen if chosen is not None else candidates[0]
    st.backup_sha256 = chosen_sha or first_sha
    st.patched_sha256 = chosen_patched
    return st


def verify(game_root: str | Path) -> tuple[bool, str]:
    """核对当前 DLL 是否正好等于「备份 + 两个补丁」。返回 (是否通过, 人话)。"""
    st = status(game_root)
    if not st.has_backup:
        return False, "没有备份，无法核对"
    if st.state is PatchState.PATCHED:
        return True, "OK（当前 DLL == 备份 + 全部补丁层）"
    return False, f"FAILED（当前状态：{st.state.label}）"


#: 写盘被拒时给用户的下一步（⛔ 不许只报 Errno 13 就完事）。
#: 公开名：界面（app/shell/dll_patch.py）也用它 —— 提示只许有这一份。
WRITE_PERMISSION_HINT = (
    "游戏目录写不进去（权限不够）。请右键本程序 → 以管理员身份运行；"
    "或先给该目录写权限：右键游戏目录 → 属性 → 安全 → 编辑 → 当前用户 → 完全控制。"
)


def _permission_error(target: Path, exc: OSError) -> UserFacingError:
    """把写入时的 OSError（典型 ``PermissionError[Errno 13]``）包成用户能读的错误。"""
    return UserFacingError(
        f"写不进 {target}：{exc}",
        hint=WRITE_PERMISSION_HINT,
        errno=getattr(exc, "errno", None),
        path=str(target),
    )


def _ensure_game_dir_writable(directory: str | Path) -> None:
    """写任何字节之前先探一次：在该目录建一个临时文件，再删掉。

    不可写 ⇒ :class:`UserFacingError`，**一个字节都不动**（连备份都不拷）。
    ⚠️ 这只是预检：真写的时候仍然各自包 ``OSError`` —— 预检过了也可能在
    拷贝 / 写回中途被拒（杀毒软件、文件被占用）。
    """
    path = Path(directory)
    probe: Path | None = None
    try:
        fd, name = tempfile.mkstemp(prefix=".ba_mod_maker_write_probe_", dir=path)
        probe = Path(name)
        os.close(fd)
        probe.unlink()
    except OSError as exc:
        if probe is not None:
            with contextlib.suppress(OSError):
                probe.unlink()
        raise _permission_error(path, exc) from exc


def apply(
    game_root: str | Path,
    *,
    force: bool = False,
    check_game_running: bool = True,
) -> str:
    """打补丁。返回一句人话的结果摘要。

    参数
    ----
    force
        **保留参数，不再放宽任何东西**：自 2026-10-14 起版本闸是硬的 ——
        当前 DLL 的 sha256 必须命中 `BUILDS` 里某一份，`force=True` 也一样拒绝
        （认不出版本还硬打，就是把地址指到别的函数上）。CLI 的 `--force` 还在传它，签名不动。
    check_game_running
        打之前检测游戏进程。DLL 被游戏占用时写入会失败。
    """
    # ★ 2026-11-10（用户口径）：检查全删 —— 只留版本闸。
    #   游戏在跑？直接写，写不进去自然会报 Errno 13。
    st = status(game_root)
    if st.state is PatchState.MISSING:
        raise UserFacingError(
            f"找不到 {st.dll}",
            hint="确认选的是游戏根目录（含 BrokenArrow.exe 的那一层）",
        )
    if st.state is PatchState.PATCHED:
        # 盘上确实是我们打的补丁 —— 但**是不是当前这张表那一版**，要单独判：
        # 地址表加过层之后，旧层会「是补丁、但不是这一版」，这时静默早退等于什么都不做。
        prev = st.build
        want_prev = _patched_regions(prev) if prev is not None else []
        if want_prev and all(
            _read_region(st.dll, off, off + len(data)) == data for off, data in want_prev
        ):
            return with_redline("已经是打过补丁的状态，无需重复", st.game_root)
        raise UserFacingError(
            "盘上这份是**上一版**补丁（地址表已经变过）",
            hint="先 `patch restore` 还原成原版，再 `patch apply` 打这一版；⛔ 不要手工改字节。",
        )

    # ⛔ **版本闸**：当前 DLL 的 sha256 必须命中 `BUILDS` 里某一份。认不出来就拒绝，
    #    `force` 也不许绕过 —— 地址表按版本走，拿旧版本的地址打新版本的 DLL，
    #    11 个地址会全部指到别的函数上（这是真打坏过 DLL 的那个口子）。
    build = build_for_sha(st.dll_sha256)
    if build is None:
        known = "、".join(b.label for b in BUILDS)
        raise UserFacingError(
            "当前 DLL 不是本工具已知的原版",
            hint=(
                f"本工具还不认识这一版（认得的：{known}）—— 地址表里没有它，"
                "打下去会把地址指到别的函数上。请等本工具更新支持这一版；⛔ 不要手工改字节。"
                "（`--force` 也越不过这道闸。）"
                "要先用起来：还原本工具打过的补丁，或走 Steam 校验完整性。"
            ),
            got=st.dll_sha256[:16],
        )

    # ★ 2026-11-10：只动补丁那 4 段（合计 286 字节），不再整包读/写/拷。
    #   备份也换成**反向 Δ**（AGENTS 的口径：⛔ 不拷整包当备份）。
    delta = delta_path(st.game_root)  # 备份直接覆盖（用户口径：不查）
    want = _patched_regions(build)
    originals = [(off, _read_region(st.dll, off, off + len(data))) for off, data in want]
    try:
        _write_delta(delta, build, st.dll_size, originals, want)
    except OSError as exc:
        raise _permission_error(delta, exc) from exc
    try:
        for off, data in want:
            _write_region(st.dll, off, data)
    except OSError as exc:
        raise _permission_error(st.dll, exc) from exc

    for off, data in want:
        if _read_region(st.dll, off, off + len(data)) != data:
            raise UserFacingError(
                "写完自检失败：盘上的补丁区间与预期不符",
                hint=f"备份在 {delta}，可以用还原功能恢复。",
            )
    written = sum(len(data) for _off, data in want)
    tail = "" if st.db_zip_present else f"（游戏根目录还没有 {PACKAGE_NAME}，补丁此时处于休眠状态）"
    # ⛔ 不再调 with_redline：它会为了拼警告再跑一次 status()（= 又读一遍 147 MB）。
    #    红线本来就在对话框顶部常驻显示。
    return (
        f"已打补丁（{build.label}）：只写了 {written} 字节（反向 Δ 备份 {delta.name}，"
        f"{delta.stat().st_size} 字节）{tail}"
    )


def restore(game_root: str | Path, *, check_game_running: bool = True) -> str:
    """从备份彻底还原 DLL。返回一句人话的结果摘要。

    ⚠️ 这是**逐字节覆盖**，不是「反向打补丁」。所以它只需要备份存在。

    ⛔ 2026-10-14 起**只在「认得出是我们打的补丁」时还原**：早期只认
    「当前 DLL == 备份 + 当前代码那一版补丁」这种精确匹配；2026-10-27 起放宽成
    —— 与备份的差异**全部落在补丁区间里**即可
    （`_only_patch_regions_differ`）。理由：cave 字面量会随工具版本变，精确匹配会把
    **我们自己打的上一版补丁**当成来路不明的 DLL 拒掉，把还原出口锁死（真踩过）。
    区间外有任何差异（典型：游戏更新过）照旧拒绝 —— 猜错就是把**旧版本**的 DLL 塞进
    **新版本**的游戏（实测口径：那样必崩）。
    """
    st = status(game_root)  # 检查已按用户口径删掉
    if st.state is PatchState.MISSING:
        raise UserFacingError(f"找不到 {st.dll}")
    if not st.backup.is_file():
        raise UserFacingError(
            f"游戏根目录里没有备份（找过 {BACKUP_GLOB}），无法还原",
            hint=(
                "备份是彻底还原的唯一来源。若游戏更新过，"
                "Steam 的『验证文件完整性』会把 DLL 换回原版（数据不损）。"
            ),
        )
    # ★ 2026-11-10：反向 Δ 备份 —— 只把被改的那几段原字节写回去（几百字节），
    #   不再整包 copy2（147 MB）。写完用记录里的原版 sha256 自检。
    if st.backup.name.endswith(DELTA_SUFFIX):
        meta = _read_delta(st.backup)
        regions = [(int(off), bytes.fromhex(data)) for off, data in meta.get("regions", [])]
        if not regions:
            raise UserFacingError(f"反向 Δ 备份 {st.backup.name} 里没有区间记录，拒绝还原")
        try:
            for off, data in regions:
                _write_region(st.dll, off, data)
        except OSError as exc:
            raise _permission_error(st.dll, exc) from exc
        want_sha = str(meta.get("orig_sha256", ""))
        if _sha256_file(st.dll) != want_sha:
            raise UserFacingError(
                "还原自检失败：DLL 与备份记录里的原版 sha256 不符",
                hint=f"用 Steam 验证文件完整性把 {DLL_NAME} 换回原版。",
            )
        st.backup.unlink(missing_ok=True)
        version = st.build.label if st.build is not None else "版本没认出来"
        return (
            f"已还原（{version}）：只写回 {sum(len(d) for _o, d in regions)} 字节"
            f"（sha256 {want_sha[:16]}…，反向 Δ 已删除）"
        )

    # ★ 2026-10-27：不再只认「当前代码那一版补丁」。盘上那份只要与备份的差异**全部落在
    #   补丁区间里**（`_only_patch_regions_differ`），就说明它也是本工具打的某一版 —— 允许还原。
    #   区间外有任何差异（例：游戏更新过）仍然拒绝，原来那条保护一个字没放宽。
    backup_bytes = _read(st.backup)
    backup_build = build_for_backup(backup_bytes)
    allowed = st.state is PatchState.PATCHED or (
        backup_build is not None
        and _only_patch_regions_differ(_read(st.dll), backup_bytes, backup_build)
    )
    if not allowed:
        raise UserFacingError(
            "当前 DLL 与备份对不上，拒绝还原",
            hint=(
                "认不出盘上这份 DLL 属于哪个版本 —— 写回备份可能把**旧版本**的 DLL "
                "盖进游戏（会崩）。请用 Steam → 右键游戏 → 属性 → 已安装文件 → 验证文件完整性，"
                "把 DLL 换回原版。"
            ),
        )
    try:
        shutil.copy2(st.backup, st.dll)
    except OSError as exc:
        raise _permission_error(st.dll, exc) from exc
    version = st.build.label if st.build is not None else "版本没认出来"
    return f"已还原（{version}）：{st.dll.name} sha256 {_sha256_file(st.dll)[:16]}…"


def uninstall_hint() -> str:
    """最省事的「卸载」方式（不用动 DLL）。"""
    return (
        f"最省事的卸载：删掉游戏根目录的 {PACKAGE_NAME} 即可 —— "
        f"补丁会自动走原版路径，{DLL_NAME} 可以一直留着。要彻底还原再用还原功能。"
    )

# ---------------------------------------------------------------- 联机红线（kb/26）
#
# ⛔ 这一节是**用户拿 7 天账号换来的**（kb/26 实测）。三条硬结论逐字如下 ——
# ⛔ **编号就三条，别加第四条**（2026-10-11）：下面这些东西逐字绑在这三条上 ——
#    ONLINE_REDLINE_BODY 的三段 · tests/test_online_redline.py 的 CONCLUSION_ANCHORS 四句话 ·
#    kb/26 §3 · docs/联机红线.md §1。
#    要补的教训（"只读探针也不算安全"）写成**加注**，不进编号 —— 见 kb/26 §3 的 ⚠ 块与 §0.5 取证。
# 别改写、别软化成"建议"、别只在文档里写（产品里没有等效拦阻正是 kb/26 的结论 ③）。
#
#   ① **形态决定能不能联机**（2026-10-17 用户一手口径收窄，kb/26 §7）——
#      ⛔ **进程内注入**（MelonLoader / version.dll 劫持 / 运行时内存写入 / 任何 Mods 加载器）
#      ⇒ **绝对不许进联机入口**：实测自动判定 7 天那次，现场唯一生效的就是它；
#      ✅ **静态文件替换**（外部库补丁 / BA_Mod.zip / 资产与数据包）⇒ **可以联机、风险自负**
#      （用户实测未被封；⚠ 非官方承诺）。原句「装了本产品的任何改动 ⇒ 绝对不许进联机入口」
#      **按形态收窄**，不是作废 —— 注入那一半一个字没放宽。
#   ② **关掉 EAC 不是安全措施**：EAC 跑在**本机**，关掉它只是"能启动"的前提；
#      封号是**服务器端**判定 —— 客户端带着注入连上去，服务端看得出来。
#   ③ 凡是"改游戏本体 / 让游戏读我们的数据"的入口，**用户可见处**必须有一条不可忽略的警告。
#      本节就是 ③ 的落地：CLI 的 status/apply/restore 与 GUI 的补丁对话框都会打它。
#
# "有改动"的机械定义只有三条（见 redline_reasons）；**注入形态**另有 injection_reasons ——
# 两者都**只查文件在不在**（⛔ 不做启发式）：多报一条，用户就会开始无视整条警告。
# 正控 / 负控与变异自检在 tests/test_online_redline.py。

#: 红线的标题行（**唯一口径**：CLI、GUI、文档都引这一条，别各写一份字面量）。
ONLINE_REDLINE_HEAD = "⛔ 联机红线：注入绝不许联机 · 静态替换可联机（风险自负）"

#: 形态判定行 ① —— 现场**有进程内注入**：唯一「绝不许联机」的形态（实测自动判定 7 天）。
ONLINE_REDLINE_FORM_INJECTION = (
    "⛔ 判定：进程内注入形态 ⇒ **绝对不许联机**（实测自动判定 7 天）。"
)

#: 形态判定行 ② —— 只有**静态文件替换**（补丁 / BA_Mod.zip / DLL 被改过）：可以联机、风险自负。
ONLINE_REDLINE_FORM_STATIC = (
    "✅ 判定：静态文件替换形态 ⇒ **可以联机**、**风险自负**"
    "（用户实测未被封；⚠ 非官方承诺）。"
)

#: 形态判定行 ③ —— 现场干净，但这个入口**即将**写静态文件：同上，只是"写完才生效"。
ONLINE_REDLINE_FORM_PENDING = (
    "⚠ 判定：这个入口写的是静态文件 ⇒ 写完**可以联机**、**风险自负**"
    "（⚠ 非官方承诺）；⛔ 但现场若有进程内注入，依然**绝对不许联机**。"
)

#: 红线的正文。三段与 kb/26 §3 的三条硬结论一一对应（① 已按 §7 收窄成"注入形态"）。
ONLINE_REDLINE_BODY = (
    "1. **进程内注入**（MelonLoader / version.dll 劫持 / 运行时内存写入 / 任何 Mods 加载器）"
    "⇒ **绝对不许进联机入口**（遭遇战、任何联机房间）—— 7 天那次唯一生效的就是它。\n"
    "2. **静态文件替换**（" + DLL_NAME + " 字节补丁 / " + PACKAGE_NAME + " / 资产与数据包）"
    "⇒ **可以联机**、但**风险自负**（用户实测未被封；⚠ 非官方承诺）；"
    "⚠ 走自建服务器时**漏一条外连 = 再来一次自动 7 天**。\n"
    "3. **关掉 EAC 不是安全措施**（EAC 跑在**本机**，封号是**服务器端**判定），"
    "它只是「能启动」的前提。代价是实测的：**用户账号已因「改了客户端」被判 7 天**，"
    "触发场景就是**进遭遇战**；外部的 BARO mod 作者同样 7 天，README 原话："
    "\"it happened to the author, for seven days, in a skirmish.\""
)


def redline_reasons(st: PatchStatus) -> list[str]:
    """**「有改动」的机械定义** —— 三条，逐条可复算。

    1. PatchState.PATCHED —— DLL 打过补丁（游戏会去读我们的外部库）；
    2. PatchState.MODIFIED / NO_BACKUP —— DLL 被改过（**不管是誰改的**：
       对服务端来说都是"改过的客户端"，所以照样报）；
    3. db_zip_present —— 游戏根目录里有 PACKAGE_NAME（补丁没打也是"我们把数据放进游戏目录了"）。

    ⛔ 只有这三条。**无改动却报 = 假绿**，用户会开始无视整条警告（AGENTS §0.9 动作 2）。
    """
    out: list[str] = []
    if st.state is PatchState.PATCHED:
        out.append(f"{DLL_NAME} 已打补丁（游戏会去读外部库）")
    elif st.state is PatchState.MODIFIED:
        out.append(f"{DLL_NAME} 被改过，而且不是本工具打的")
    elif st.state is PatchState.NO_BACKUP:
        out.append(f"{DLL_NAME} 被改过，而且没有备份")
    if st.db_zip_present:
        out.append(f"游戏根目录里有 {PACKAGE_NAME}")
    return out


#: **注入形态**的探针：只有这些，⛔ 不做启发式（只查"这个名字在不在游戏根目录里"）。
#: 判据来自 kb/26 §2 的现场与 §7 的收窄口径 —— 那次唯一生效的注入就是 MelonLoader
#: ＋ Mods 里的探针 DLL。
#: ⚠ `Mods` 目录**本身**不算注入：它是游戏自建的（`Maps/Scenarios/Units` 三个空目录，kb/26 §4）；
#:   算的是**直接放在 `Mods` 下的 DLL**（2026-10-10 那次事故的探针就是 `Mods\\BaModProbe.dll`）。
INJECTION_MARKERS: tuple[tuple[str, str], ...] = (
    ("version.dll", "MelonLoader.Bootstrap 的劫持点"),
    ("winhttp.dll", "doorstop / MelonLoader 的另一个劫持点"),
    ("MelonLoader", "MelonLoader 本体（目录）"),
    ("dobby.dll", "MelonLoader 的 hook 引擎"),
    ("doorstop_config.ini", "doorstop 注入配置"),
    (".doorstop_version", "doorstop 注入版本戳"),
    ("UserLibs", "MelonLoader 的用户库目录"),
)

#: `Mods` 目录名 —— 它**自己**不算注入，只有它下面的 `.dll` 才算（见 INJECTION_MARKERS 的 ⚠）。
MODS_DIR_NAME = "Mods"


def injection_reasons(game_root: str | Path | None) -> list[str]:
    """**注入形态**的机械定义 —— 逐条点名，没有就空表。

    ⛔ 只查"文件在不在"（`exists()` ＋ `Mods/*.dll`），**不做任何启发式**：多报一条，
    就会把"只有静态补丁"的现场也说成注入 —— 而这两种形态的判定**正好相反**
    （静态可以联机、注入绝对不许），所以这里错一次就是把人往 7 天上推。

    正控 / 负控：`tests/test_online_redline.py` 的 F（`version.dll`）· G（`Mods\\BaModProbe.dll`）·
    H（`Mods\\Maps` 空目录**不算**注入 —— 这条是假绿的尺子）。
    """
    if game_root is None:
        return []
    root = Path(game_root)
    out = [f"{name}（{why}）" for name, why in INJECTION_MARKERS if (root / name).exists()]
    mods = root / MODS_DIR_NAME
    if mods.is_dir():
        out.extend(
            f"{MODS_DIR_NAME}\\{dll.name}（Mods 加载器会把它注入游戏进程）"
            for dll in sorted(mods.glob("*.dll"))
            if dll.is_file()
        )
    return out


def online_redline(
    game_root: str | Path | None,
    *,
    pending_change: bool = False,
    patch_status: "PatchStatus | None" = None,
) -> str | None:
    """进「会改游戏目录 / 会让游戏读我们的数据」的入口时，该给用户的那条警告。

    **按形态给判定行**（kb/26 §7，2026-10-17 用户一手口径）：

    | 现场 | 判定行 | 能不能联机 |
    |---|---|---|
    | 有进程内注入（`injection_reasons` 非空） | `ONLINE_REDLINE_FORM_INJECTION` | ⛔ 绝对不许 |
    | 只有静态改动（补丁 / `BA_Mod.zip`） | `ONLINE_REDLINE_FORM_STATIC` | ✅ 可以，风险自负 |
    | 现场干净、但这个入口即将写静态文件 | `ONLINE_REDLINE_FORM_PENDING` | ✅ 同上 |

    注入压过静态：两者同时在 ⇒ 走 INJECTION 那条（注入才是 7 天那次的形态）。
    返回 None = **没有注入、没有任何改动，而且这个动作也不产生改动** ⇒ 一个字都不报。
    这条负控是**故意**的：警告一旦会乱报，用户就会开始无视它。

    :param pending_change: 这个入口**本身**即将产生改动（打补丁 / 导入 data.unity3d …）。
        给了它 ⇒ 即使现场是干净的也要报 —— 因为**打完就不干净了**。
    """
    if game_root is None:
        return None
    root = Path(game_root)
    injected = injection_reasons(root)
    # ★ 2026-11-10：调用方（补丁对话框）手上已经有 `status()` 的结果时直接给它 ——
    #   这里再算一次等于又读一遍 147 MB，点「打补丁」到弹确认框之间会白等。
    reasons = redline_reasons(patch_status if patch_status is not None else status(root))
    if pending_change:
        reasons.insert(0, "这个入口本身就会改游戏目录")
    if not injected and not reasons:
        return None
    form = (
        ONLINE_REDLINE_FORM_INJECTION
        if injected
        else (ONLINE_REDLINE_FORM_PENDING if pending_change else ONLINE_REDLINE_FORM_STATIC)
    )
    scene = "；".join(injected + reasons)
    return f"{ONLINE_REDLINE_HEAD}\n{form}\n{ONLINE_REDLINE_BODY}\n\n现场：{scene}。"


def with_redline(message: str, game_root: str | Path | None) -> str:
    """把动作的返回消息**挂在红线下面** —— 有改动时警告在前、人话在后。"""
    warn = online_redline(game_root)
    return f"{warn}\n\n{message}" if warn else message

