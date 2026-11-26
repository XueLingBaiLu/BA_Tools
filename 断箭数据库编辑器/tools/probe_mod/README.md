# probe_mod — BaModProbe（调试用探针 mod）

> **这是调试工具，不进产品分发。** 独占写入范围 `BA_Mod_Maker_2.0/tools/probe_mod/`。
>
> 📖 **知识结论（不是操作手册）在 [`kb/27-运行期探针与活库对拍.md`](../../kb/27-运行期探针与活库对拍.md)** ——
> 首次「活库 vs 离线库」对拍读数、Il2CppInterop 的四个坑、12 个「游戏现算列」、浮点十进制字面量、
> `Turrets.ParentTurretId` 的完整调查。
> ⛔ **装上它 = 游戏客户端被改过 ⇒ 绝不能联机**（2026-10-10 实测封号 7 天，见 [`kb/26`](../../kb/26-联机封号实测.md)）。

两件事：

1. **只读探针** —— 挂 `DataBaseService` 的 `Load / LoadCompiled / LoadByJson / LoadScenario` **Postfix**，
   把 `db.RawAccess`（`BrokenArrow.DataBase.DataBaseSourceData`）的 **24 张表**序列化成契约 JSON。
2. **稀疏 delta 覆盖**（调试用）—— 读 `override.json`，按 `表 → 行 Id → 列 → 新值` 反射写库；
   写前存原值，`ProbeMod.Restore()` 一键还原，并记「实际写了几格 / 哪几格没成功」。

### 为什么挂四个钩子（不是三个）

| 钩子 | 谁调 | 后果 |
|---|---|---|
| `Load()` | `Initialize()`（启动）、`ReloadByDefault()`（回主菜单） | **只到主菜单也能出 dump** —— 这一个让"一次启动"就够 |
| `LoadCompiled()` | `ProfileDataBaseResolver.ApplyForScenario()` | **进场景才调**；BARO 只挂了这一组，光到菜单不触发 |
| `LoadByJson()` | JSON 那条路 | |
| `LoadScenario()` | 场景 `db.zip` | |

出处：`kb/11-游戏读取外部数据库（二进制补丁）.md` §1（Lead 核过的实测结论）。
代理程序集上实测的访问级别：**`Load()` 与 `LoadByJson()` 在代理里是 public**（dummy dll 里报 private，Il2CppInterop 放宽了）—— 两种都能挂。

### 加载路径上的三条自保（`Load()` 会反复触发，所以必须有）

1. **dump 按钩子种类节流**：`Load()` 启动一次 + 每次回菜单一次，`LoadCompiled()` 每次进场景一次。
   同一会话里同一种钩子只 dump 一次；**不同钩子各 dump 一次**（场景库和编译库是两回事，不能互相吃掉）；
   `redump.flag` 随时强制重来。
2. **重载时 journal 只丢不写回**（`WriteJournal.Discard()`）：`Load()` 会重建源对象，我们之前写进去的值
   本来就已经没了，而 journal 里记的行代理**可能已被释放** —— 对已释放的 Il2Cpp 对象写是**原生访问违例（崩溃）**，
   不是托管异常，catch 不住。所以重载路径主动丢弃 journal；`Restore()` 只留给**活库**路径
   （`restore.flag`、会话中途改 `override.json`）。
3. **覆盖每次都重新应用**：重载会把我们写的东西冲掉，所以 Job 2 不节流。

### 磁盘占用有上限（用户明确讨厌垃圾文件）

`dumps\` 只保留**最新 20 份**带时间戳的 dump，更旧的自动删（`DbDump.PruneStampedDumps`）。
`load_latest.json` 永远是最新一份，不受影响。
理由：AGENTS §2 用户原话「不要帮我手动备份，会生成很多的垃圾备份文件占用我的硬盘空间」，
而 `Load()` 每次回菜单都触发 —— 不加这条就是每次回菜单多一个几 MB 的文件。

---

## 0. 首编状态：**已经编译通过了**（2026-10 用户起过一次游戏之后）

代理程序集一到位就编过了，不是"待编"：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File build.ps1
```

| 判据 | 读数 |
|---|---|
| `dotnet build BaModProbe.csproj -c Release` | **0 警告 0 错误**，产物 36 352 B |
| `dotnet run --project tests\JsonTests` | **pass=58 fail=0** |
| `tools\check_columns.py` | `tables=24 columns=407 unresolved_tables=0` |
| `tools\check_reflection_names.ps1` | 11 项全 PASS，正控先红 |
| 游戏侧引用 | `Mods\BaModProbe.dll` **46592 B**；`UserData\BaModProbe\columns.json` 8965 B |
| IL 闸门（**能变红**） | `tools\check_no_il2cpp_ienumerable_cast.ps1` → 正控 `tests\CastProbe` 被点出 **2** 条，成品 dll **0** 条 |
| 四个钩子真的进了二进制 | `_work\verify_hooks.ps1` → `HarmonyPatch(typeof(DataBaseService), "Load" / "LoadCompiled" / "LoadByJson" / "LoadScenario")` 4 条，4 个 Postfix 都 `call ProbeMod.OnDatabaseLoaded` |

### 首跑复盘：24 张表全空 —— 根因与修法（2026-10-09）

首跑钩子全中（`Load: RawAccess acquired (source_id='Resources default')`），但 24/24 表都是：
`WARN dump: table X not read -- GetAll() did not return IEnumerable`。

**根因（量出来的，比最初的诊断更宽）**：用 Mono.Cecil 读 `Il2Cppmscorlib.dll`，**每一个 Il2Cpp 代理集合
都不实现任何托管接口** —— `implements:` 全是空的：

| 代理类型 | 实现的托管接口 |
|---|---|
| `Il2CppSystem.Collections.IEnumerable` | **无** |
| `Il2CppSystem.Collections.Generic.IEnumerable\`1` | **无** |
| `Il2CppSystem.Collections.Generic.IEnumerator\`1` | **无**（而且它**只有 `get_Current()`，没有 `MoveNext()`**） |
| `Il2CppSystem.Collections.Generic.List\`1` | **无** |
| `Il2CppSystem.Collections.Generic.Dictionary\`2` | **无** |

⇒ `x as System.Collections.IEnumerable` **永远是 null**（与游戏状态无关，是结构性的），
`foreach` 一个 Il2Cpp 代理集合也永远跑不起来。复算：`_work\probe_collections.ps1`。

**修法：照抄 BARO 的 `Props.Rows<T>`（`decompiled.cs:407727`，生产代码里被调 190 次）**
—— 拷进 `List<T>` 再**按索引**走，彻底绕开枚举器：

```csharp
Type il2ListT = typeof(Il2CppSystem.Collections.Generic.List<>).MakeGenericType(elemType);
object list   = Activator.CreateInstance(il2ListT, new object[] { enumerableObj });  // .ctor(IEnumerable<T>)
int n         = (int)il2ListT.GetProperty("Count").GetValue(list);
for (int i = 0; i < n; i++) rows.Add(il2ListT.GetProperty("Item").GetValue(list, new object[] { i }));
```

依据（都在代理程序集上量过）：`List\`1` 有 `.ctor(IEnumerable\`1)`、`get_Count()`、`get_Item(Int32)`，**全是 public**。
退路（实测同样存在）：`System_Collections_IEnumerable_GetEnumerator()` → **非泛型** `Il2CppSystem.Collections.IEnumerator`
（只有它带 `MoveNext()`）。两条都失败时，错误信息里会写出 **`GetAll()` 实际返回的运行时类型全名**和两条路各自的异常
—— 下次真跑直接点名，不再是一句"did not return IEnumerable"。

**下次真跑的自证**：`probe.row_source` 会写清走的是哪条路
（`Il2Cpp List<T>(IEnumerable<T>) + index walk  [BARO Props.Rows<T> shape]` 或非泛型退路）。

**为什么加的是 IL 闸门而不是单元测试**：这个 cast 是**合法 C#**，编译器抓不到；而我们跑不了游戏。
所以只能对**成品 dll 的 IL** 下断言：`isinst`/`castclass` 到任何托管集合抽象（`IEnumerable`/`IEnumerator`/`ICollection`/`IList`/`IDictionary`，泛型与非泛型）
都算违规。`tests\CastProbe` 是**故意写坏**的正控程序集 —— 它不被引用、不部署，只用来证明这把尺子**能红**；
闸门已接进 `build.ps1`：**IL 闸门不过就不部署**（`exit 5`）。

### 第二轮复盘：`Turrets.ParentTurretId` 读不动 + 派生列（2026-10-09 第二次真跑后）

真跑拿到 24 表 / 16687 行 / 263498 格，`row_source` 是**主路**（BARO 的 index walk），0 失败；
但 `probe.warnings` 有 **965** 条，全是 `read Turrets.ParentTurretId threw`。

**根因（Mono.Cecil 读代理程序集，`_work/probe_nullable.ps1`）—— 两条独立原因叠在一起：**

1. **声明类型不是 `System.Nullable<int>`**，而是 `Il2CppSystem.Nullable`1<System.Int32>`
   —— 一个 **class**（`isValueType=False`，base `Il2CppSystem.ValueType`）。
   于是 `System.Nullable.GetUnderlyingType` 认不出它，值掉进"引用列"分支，
   **连那 86 行真有值的也被写成了 `null`**。
2. **具名 getter 会抛、孪生 backing 属性不会。**
   `ParentTurretId` 的 getter IL 是 `IL2CPP::il2cpp_runtime_invoke` + `Il2CppException::RaiseExceptionIfNecessary`
   —— 真的去调游戏方法，值缺席时那边抛；而 `_ParentTurretId_k__BackingField` 的 IL 是
   `il2cpp_field_get_offset` + `il2cpp_value_box` —— **不调用任何游戏方法，不会抛**。

**修法：**
- 新增零依赖 `src/Il2CppNullable.cs`：按字段支撑的 `hasValue`/`value` 解包 `Il2CppSystem.Nullable<T>`
  （不跑游戏代码）；缺席 → `null`（**不是 0**）。零依赖是为了能离线测。
- `RowAccess.ColumnHandle`：具名 getter **优先**（那是游戏自己的 API），抛异常时**退到孪生 backing 属性**再读一次；
  两条都失败才省略键 + 记 warning —— 原有语义保留。
- 离线测试 `tests/JsonTests/FakeNullable.cs` 造了形状与实测一致的替身，13 条断言（含"缺席值解包成 null 而不是 0"、
  "只有 `HasValue`/`Value` 的形状也能认"、"同名但命名空间不对要拒绝"、"真 `System.Nullable<T>` 不许在这里被吞掉"），带正控。

**`null` 落盘口径（Lead 定）**：**键永远写出来；值是 null 就写 `null`**。
只有"读真的失败（所有路径都抛）"才省略键 + 记 warning —— 这样下游能分开"读到 null"与"没读到"。

**派生列（新字段 `probe.computed_columns`）** —— 全 407 列逐个查过（`_work/classify_columns.ps1`）：

| 存储方式 | 列数 |
|---|---|
| 有 `_<name>_k__BackingField` 孪生 | 395 |
| 存在**平名**字段里 | **0** |
| **没有存储字段 ⇒ 游戏现算** | **12** |

12 列：`Ammunitions.IsTopArmorArmorAttack`、`Mobility.FlyPreset`（按引用列另报）、
`PlaneFlyPresets.{AfterburnCornerSpeed,CornerSpeedYaw,CornerAfterburnSpeedYaw}`、`Specializations.MaxSlots`、
`Units.{OriginalName,OwnerInfantryID,OriginalCost,CurrentStealth}`、`Weapons.{WeaponChannel,WeaponPriority}`。

⇒ 派生值天然可能与序列化数字差 1 ulp（`PlaneFlyPresets.CornerSpeedYaw` 在 Id 3/10 就是这样），
对比器**不该**把它当数据差异 —— 这正是 `probe.computed_columns` 的用途。

**"数值转换有没有二次转换"：没有。** `Convert.ToSingle(盒装 float)` 是恒等，之后走最短往返打印，全程不做算术。
佐证：全库 51085 个 `System.Single` 里 **2492 个不是 float32 精确值**（`0.7`/`0.9`/`0.95` 这种），
但最短往返打印会把 float32 的 `0.7f` 也印成 `"0.7"`，所以两边照样对上 —— 这就是 263498 格里只有 1060 格不同的原因。
复算 `_work/float_precision_audit.py`。**离线 JSON 存的是十进制字面量，不是内存里的 float32**。

### 第三轮：诊断优先（2026-10-09 第三次真跑前）

上一轮的"Nullable 解包 + backing 回退"**实测零效果**（预测被证伪：warnings 仍 965、键仍 90）。
读源码 + 读成品 IL 之后的结论分两半：

**已证（读 IL）**：回退**确实在成品 dll 里** —— `ColumnHandle::Get` 有 `HasExceptionHandlers=True` 的 Filter 处理器
（读 `BackingProp`、比 null、比 `Prop`、`endfilter`）以及处理器之后的 `BackingProp` 分支；
`ResolveUncached` 里有 `stfld ColumnHandle::BackingProp`。⇒ **不是部署/编译/旧 dll 问题。**

**证不了（不许猜）**：警告由 DbDump 外层 catch 打印，**两种情形文案完全一样** ——
(a) `BackingProp` 运行时为 null ⇒ 过滤器为假 ⇒ 原异常直接冒泡；
(b) `BackingProp` 找得到、但它自己的 getter 也抛。**日志无法区分这两条，只能靠运行期诊断。**

**已找到并修掉的那一半（读自己的源码，确定）**：
`IsReferenceColumn(Il2CppSystem.Nullable<int>)` 当时返回 **true** —— 因为 `IsPrimitiveish` 里
`Nullable.GetUnderlyingType(t) ?? t` 对 Il2Cpp 的 Nullable 返回它自己那个 **class**，判不出标量。
而 DbDump 的行循环里，引用分支在**读成功之后**执行 `w.Null(); continue;`，
⇒ **`WriteValue`（也就是解包）根本走不到** —— 这正是 90 格全 null 的原因。
修法：`IsPrimitiveish` 先经 `Il2CppNullable.UnderlyingOf` 递归到真实载荷（`Nullable<int>` → `int` → 标量）。

**这一轮新增（无论成败都带回答案）**：
- `probe.column_diagnostics`：对 `diagnose_columns.txt` 里的列（默认且恒含 `Turrets.ParentTurretId`），
  每列取 2 行样本，逐条记 **path1-named / path2-backing / path3-field** 的结局
  （值 / 异常全名 + **完整 inner 链**）、声明类型、`backingProp` 是否存在，以及 **path4-raw** 的结果。
- **path4-raw**：直接按偏移从对象内存里读那个字段，绕开两个 getter。
  偏移**全部来自运行期**（`IL2CPP.il2cpp_field_get_offset` 作用在代理自己的 `NativeFieldInfoPtr_*` 静态上），
  **没有任何布局是靠猜的**。成功则采用该值并计入 `probe.raw_reads_used`；失败则 trace 写明原因。
- 看门表文件：`<GameDir>\UserData\BaModProbe\diagnose_columns.txt`（`build.ps1` 首次创建，可自由编辑）。

### 代理程序集缺失/重新生成时要改什么

`build.ps1` 会在编译前做 preflight，缺哪个就点名哪一个，不会给一屏 CS0246。默认路径：

| 引用 | 路径 | 缺了会怎样 |
|---|---|---|
| `MelonLoader.dll` | `<GameDir>\MelonLoader\net6\` | preflight 报 `MelonLoader.dll not found` |
| `0Harmony.dll` | `<GameDir>\MelonLoader\net6\` | 同上 |
| `Il2CppInterop.Runtime.dll` | `<GameDir>\MelonLoader\net6\` | 同上（**注意它不在 Il2CppAssemblies 里**） |
| `Il2CppBrokenArrow.dll` + 173 个代理 | `<GameDir>\MelonLoader\Il2CppAssemblies\` | preflight 提示"起一次游戏让 Il2CppAssemblyGenerator 生成" |

**游戏目录不写死**，优先级：`-GameDir` 参数 → `$env:BA_MOD_MAKER_GAME` → `D:\Steam\steamapps\common\broken_arrow`。
本工程**不指向 BARO 目录**（BARO 那份只是参照实现）。

代理程序集更新后**不需要改任何源文件**：`csproj` 用 `<Reference Include="@(_ProbeProxyDll)">` 整目录引用，
编译器只解析源码真正点名的类型。若整目录引用出问题，退路是 `-p:ProbeReferenceMode=Minimal`
（只引 `Il2CppBrokenArrow` / `Il2Cppmscorlib` / `Il2CppSystem` / `Il2CppSystem.Core`）。

---

## 1. 运行期产物（都在 `<GameDir>\UserData\BaModProbe\`）

| 文件 | 谁写 | 说明 |
|---|---|---|
| `dump_latest.json` | 探针 | 最新一份 dump（契约 JSON） |
| `dumps\dump_<UTC>.json` | 探针 | 同一份带时间戳的副本 |
| `columns.json` | `build.ps1` | **列清单**，与 `DataBaseCompiled/52085` 逐字一致；探针和对比器读同一份 |
| `override.json` | **你** | delta 覆盖，格式见下 |
| `probe.log` | 探针 | 全过程日志（写了几格 / 哪几格没成功） |
| `restore.flag` | **你** | 存在即触发一次 `Restore()`，随后自删 |
| `redump.flag` | **你** | 存在即立刻重 dump 一次，随后自删 |

### 控制通道（不需要键盘，也不需要 Unity 输入 API）

游戏跑着的时候，往那个目录丢/改文件就能驱动探针：

```powershell
$d = 'D:\Steam\steamapps\common\broken_arrow\UserData\BaModProbe'
New-Item -ItemType File -Force "$d\redump.flag"    # 立刻重 dump
New-Item -ItemType File -Force "$d\restore.flag"   # 立刻还原所有被写过的格
# 改 override.json 保存 -> 探针 2 秒内 Restore() 后重新应用
```

---

## 2. 数据格式契约

探针落盘 / 对比器读取的是**同一个 JSON**：

```json
{
  "meta": {
    "generated_utc": "2026-10-10T00:00:00Z",
    "mod_version": "0.1.0",
    "game_source": "compiled",
    "table_count": 24,
    "row_counts": { "Units": 540, "Ammunitions": 630 },
    "failed_tables": []
  },
  "tables": { "Units": [ { "Id": 1, "Name": "..." } ] },
  "probe": { "...": "见下，附加诊断，对比器可忽略" }
}
```

硬规则落实情况：

1. **表名/列名逐字等于 `DataBaseCompiled/*.json` 的键名** —— 列名来自 `columns.json`；
   表名**不是手抄的**，是从活类型上按"属性类型是 `Il2CppBrokenArrow.DataBase.Driver.Table<T>`"枚举出来的，
   自动滤掉 `_Xxx_k__BackingField` 孪生属性，恰好 24 张。
   机械验证：`_work\table_set_audit.py` → 与 `DataBaseCompiled/52085` 文件干名**集合相等**（对称差为空）。
   ⚠️ **表名是复数 `PlaneFlyPresets`，行模型类名是单数 `...Models.PlaneFlyPreset`** —— 两者不是一回事，
   `tools\check_reflection_names.ps1` 专门盯这一条。
2. **值原样** —— 不做单位换算、不四舍五入、不丢字段。
   - `System.Single` 一律带小数点（`10.0` 而不是 `10`）。**这是量出来的**：
     `_work\number_token_audit.py` 扫全 24 张表，`System.Single` **51 085 个值全部是 float 记号**，
     `System.Int32` **142 799 个全是 int 记号**，零例外。写错方向会造成 5 万条假差异。
   - 枚举 → 底层数值（`Units.Role` 12、`Countries.ContentMembership` -1）。
   - `Nullable<int>` → `null` 或整数。
   - 唯一一个"引用列" `Mobility.FlyPreset`（模型类型 `...Models.PlaneFlyPreset`）：`DataBaseCompiled`
     266 行**全是 null**（真正带数据的是单独的 Int32 列 `Mobility.FlyPresetId`）。
     为了对比口径一致，dump 里这一列**写 null**；活库里该引用解析到了什么记在
     `probe.reference_reads` —— **信息没丢，只是挪出了被对比的命名空间**。
3. **每张表按 Id 升序**。
4. **读不到的表进 `meta.failed_tables`**，绝不静默漏掉。

### `probe` 这个顶层键（附加，非契约）

对比器**只比对 `meta` + `tables` 即可**；`probe` 是给人看的诊断：
`hook` / `source_id` / `columns_source`（`columns.json` 或 `reflection-fallback`）/
`tables_discovered` / `cells_written` / `cells_unresolved` / `failed_tables` /
`reference_columns_nonnull` / `reference_reads` / `warnings`。

---

## 3. `override.json` 格式

```json
{ "Ammunitions": { "123": { "MuzzleVelocity": 1800, "Damage": 45 } },
  "Armors":      { "70":  { "KinArmorFront": 500 } } }
```

报告把"没写进去"分成四类，**不混为一谈**（都进 `probe.log`）：

| 类别 | 含义 |
|---|---|
| `row-not-found` | 表读到了，但没有这个 Id 的行 |
| `col-not-found` | 行上没有这个列 |
| `col-read-only` | 列存在但**代理程序集里没有 public setter**。实测全库 407 列里只有 6 列如此：<br>`Ammunitions.IsTopArmorArmorAttack`、`Units.CurrentStealth`、`PlaneFlyPresets.AfterburnCornerSpeed`、`PlaneFlyPresets.CornerSpeedYaw`、`PlaneFlyPresets.CornerAfterburnSpeedYaw`、`Specializations.MaxSlots`<br>（出处：`tools\dump_proxy_members.ps1`） |
| `conversion-failed` | 值的类型转不过去（例如把字符串塞进 Int32） |

还原：`ProbeMod.Restore()`（静态）按**写前存下的原值**倒序写回。

---

## 4. 用到的每个游戏类型：全名 + 出处

**全部**用 Mono.Cecil 从**实际的代理程序集**读出，没有一个是猜的：

```powershell
# 类型/成员来源（可复算）
powershell -NoProfile -ExecutionPolicy Bypass -File tools\dump_types.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File tools\dump_proxy_members.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File tools\check_reflection_names.ps1
```

| 源码里用的名字 | 出处（程序集 :: 类型 :: 成员） |
|---|---|
| `DataBaseService` | `Il2CppBrokenArrow.dll` :: `Il2CppBrokenArrow.Shared.Ecs.DataBaseService`（dummy 名 `BrokenArrow.Shared.Ecs.DataBaseService`） |
| `DataBaseService.RawAccess` | 同上，**是属性不是字段**，`DataBaseSourceData RawAccess { get; }` |
| `DataBaseService.CurrentSourceId` | 同上，`String CurrentSourceId { get; }`（反射读，不猜 CLR 形态） |
| 三个钩子 | 同上：`Void LoadCompiled(DataBaseCompiled, String)` / `Void LoadByJson()` / `Void LoadScenario(String)` |
| `DataBaseSourceData` | `Il2CppBrokenArrow.dll` :: `Il2CppBrokenArrow.DataBase.DataBaseSourceData` |
| 24 张表的属性 | 同上，`Table<T>` 属性；**每张表同时有 `_Xxx_k__BackingField` 孪生属性**，按后缀过滤 |
| `Table<T>` | `Il2CppBrokenArrow.dll` :: `Il2CppBrokenArrow.DataBase.Driver.Table`1`；`IEnumerable<T> GetAll()`、`Dictionary<int,T> _rows` |
| 行模型（如 `Ammunitions`） | `Il2CppBrokenArrow.dll` :: `Il2CppBrokenArrow.DataBase.Models.<Table>`；每列同时有 `Xxx` 与 `_Xxx_k__BackingField` 两个 public 属性 |
| `MelonEnvironment.UserDataDirectory` | `MelonLoader.dll` :: `MelonLoader.Utils.MelonEnvironment.UserDataDirectory`（**不是** `MelonLoader.MelonEnvironment`） |
| `MelonMod.OnInitializeMelon/OnUpdate` | `MelonLoader.dll` :: `MelonLoader.MelonBase`（`MelonMod : MelonTypeBase<MelonMod> : MelonBase`） |
| `MelonInfo` | `MelonLoader.dll` :: `MelonLoader.MelonInfoAttribute` —— **只有 5 参构造**（`Type,name,version,author,downloadLink`），没有 4 参重载，所以 downloadLink 显式传 `null` |
| `HarmonyPatch(typeof(T), "M")` | `0Harmony.dll` :: `HarmonyLib.HarmonyPatch(Type, String)` |
| `Il2CppObjectBase.Pointer` | `Il2CppInterop.Runtime.dll` :: `Il2CppInterop.Runtime.InteropTypes.Il2CppObjectBase.Pointer`（`IntPtr`）—— 用来判"代理对象是不是空引用"（`Pointer == IntPtr.Zero`） |

### 照抄 BARO 的三处（不自己发明）

出处：`_work_mod_analysis\_scratch_code_layer\decomp\BrokenArrowRealismOverhaul.decompiled.cs`

| BARO 原文 | 本工程 |
|---|---|
| `408508: private static void Set(object row, PropertyInfo p, object value)`<br>里面 `IntPtr pointer = ((Il2CppObjectBase)row).Pointer;` + `Orig = p.GetValue(row)` + `p.SetValue(row, value)` | `src/RowAccess.cs` :: `WriteJournal.WriteAndRecord`（写前存原值） |
| `408785: internal static void Restore(string reason)` —— 倒序遍历 journal，`jEntry.P.SetValue(jEntry.Row, jEntry.Orig)` | `src/RowAccess.cs` :: `WriteJournal.Restore` |
| `407682: internal static PropertyInfo Get(Type t, string name)` → `t.GetProperty(name, BindingFlags.Instance\|Static\|Public)` | `src/RowAccess.cs` :: `RowAccess.Resolve`（多了一层 backing-field / 字段 / 接口退路） |

钩子那一组照抄 `414972 / 414988 / 415004` 的 `Patch_DbLoadCompiled / Patch_DbLoadByJson / Patch_DbLoadScenario`
（BARO 在同一个目标方法上还挂了别的一组，我们**没碰**游戏其它行为）。

---

## 5. 还没验证的地方（首跑要看这几条）

| # | 没验证的 | 首跑怎么看 |
|---|---|---|
| 1 | **`game_source` 的映射是假设** —— 只有 `LoadCompiled→compiled` / `LoadByJson→BA_Mod.zip` 两条是推测；`Load` 与 `LoadScenario` 一律写 `unknown`（`Load()` 走 `Initialize()`/`ReloadByDefault()`，对来源**零证据**，宁可写 unknown 也不给一个自信的错答案） | 看 `probe.hook` 与 `probe.source_id` 两个原始读数，据此定正 |
| 2 | 四个 Postfix **真的会被触发**（Harmony 打得进 Il2Cpp 方法）——BARO 同形状已证，但我们的没实跑。属性与调用点已用 Cecil 在**成品 dll** 上核过（4/4） | `probe.log` 里应出现 `patched (Postfix)` 与 `<hook>: RawAccess acquired` |
| 3 | 24 张表**全部读得到** | `meta.failed_tables` 必须为空；`meta.row_counts` 24 个键 |
| 4 | `Mobility.FlyPreset` 在活库里解析成了什么 | `probe.reference_columns_nonnull` 与 `probe.reference_reads`（dump 里这一列恒为 null，是刻意的） |
| 5 | dump 体量与耗时（同步跑在加载链上） | `probe.log` 里 `dump: tables= rows= cells= ... ms=` |
| 6 | `Restore()` 的回写路径 | 写一次 override → 落 `restore.flag` → 看 `probe.log` 的 `Restore: restored=N failed=0` |

---

## 6. 目录结构

```
tools/probe_mod/
  README.md                       <- 本文件
  BaModProbe.csproj               <- net6.0；引用路径全可配置（-p:GameDir / BA_MOD_MAKER_GAME）
  build.ps1                       <- 一条命令：列清单校验 + 编译 + 拷进 <GameDir>\Mods\
  src/
    ProbeMod.cs                   <- MelonMod 入口、OnUpdate 控制通道、静态 Restore()
    Patches.cs                    <- 三个 HarmonyPatch(Postfix)
    Contract.cs                   <- 24 张表自发现 + 读 columns.json
    DbDump.cs                     <- 事 1
    DbOverride.cs                 <- 事 2
    RowAccess.cs                  <- 列解析 / 读写 / 写前 journal
    Json.cs                       <- 手写 JSON 写读（零外部依赖）
    ProbePaths.cs                 <- UserData 路径 + 日志
  tests/JsonTests/                <- 离线跑：Json.cs 的 58 条断言（含正控）
  tools/
    dump_types.ps1                <- Mono.Cecil 读 dummy dll -> _work/il2cpp_types.json
    dump_proxy_members.ps1        <- Mono.Cecil 读代理程序集 -> _work/proxy_members.json（含可写性）
    check_columns.py              <- 尺子：407 列逐列可否解析；产出 columns.json（--selftest 正控）
    check_reflection_names.ps1    <- 尺子：反射用的字符串字面量是否真存在（先跑正控）
  _work/                          <- 度量脚本与产物（证据，不是交付物）
```

### 为什么不引 Newtonsoft.Json

MelonLoader 的 `net6` 目录里有 `Newtonsoft.Json.dll`，但游戏自己也有 `Il2CppNewtonsoft.Json` 代理。
在 MelonLoader 的加载上下文里引 Newtonsoft 有解析到错的那一份的经典风险；
而且 dump 契约要求"值原样"，数字格式本来就必须自己掌控。所以读写都手写（`src/Json.cs`，零外部依赖），
并用 `tests/JsonTests` 的 58 条断言把它钉住。

### PowerShell 5.1 的两个坑（都真踩过）

1. **BOM-less `.ps1` 会被当 ANSI 解析** —— 所以 `tools/*.ps1` 一律 **ASCII-only**，
   仓库路径（含中文）全部由 `$PSScriptRoot` 推导，不写字面量。
2. **执行策略默认禁止跑脚本** —— 一律用 `powershell -NoProfile -ExecutionPolicy Bypass -File <脚本>`。
