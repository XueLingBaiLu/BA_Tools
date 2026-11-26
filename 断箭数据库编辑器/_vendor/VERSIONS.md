# _vendor/VERSIONS.md · 自带运行时的版本账单

> 依赖不能只锁在 pyproject 里 —— 插件是**自带**的，升级这件事由我们负责（设计稿 §4 D2 义务 2）。
> 机器可读的那份在 vendor_manifest.json，这份是给人读的。

## 1. 支持面（设计稿 §4 D2.1，已裁定）

| 轴 | 取值 |
|---|---|
| 平台 | **Windows x64 单一平台**（不做 macOS / Linux） |
| Blender | **4.2 LTS + 4.5 LTS + 5.x**（4.0 / 4.1 不做） |
| 分发形态 | v1 只出 **legacy addon zip**（4.x / 5.x 通用），不出 extension |
| 解释器 | 4.x -> cp311 ｜ 5.x -> 见下（按 sys.version_info 自选） |

## 2. 版本锁定表（本机实测那一列是真读数，不是估的）

| 包 | 锁定版本 | 来源 | 证据 |
|---|---|---|---|
| UnityPy | 1.25.3 | pure | Blender 5.2.1 下 blender_addon/selftest.py 实测打印 |
| attrs | 25.3.0 | pure | 同上 |
| fsspec | 2026.7.0 | pure | 同上 |
| lz4 | 4.4.5 | native/cp313 | 同上 |
| texture2ddecoder | 1.0.6 | native/cp313 | 同上（crn 解码靠它） |
| etcpak | 0.9.15 | native/cp313 | 同上 |
| brotli | 未锁 | native/cp313（可选） | 本作无 .unityweb |

**不带的**：Pillow（改走 raw 像素 -> 图像）、astc_encoder（本作无 ASTC）、fmod_toolkit、tpk_ar。

## 3. 池的目录契约

    _vendor/
      vendor_manifest.json     <- 上面那张表的机器可读版
      VERSIONS.md              <- 本文件
      LICENSES/                <- 随包许可证（D2 义务 1）
      pure/                    <- 纯 Python 共享部分（一份，≈7.4 MB）
      native/cp311/            <- 4.x 的原生扩展（≈1.8 MB）
      native/cp312/            <- 5.0/5.1 若实测是 cp312 就放这里
      native/cp313/            <- 5.2.x 的原生扩展（≈1.8 MB）

选池规则与「不许静默回落」由 blender_addon/runtime.py 实现，并在 register() 时
**无条件打印**一遍来源与版本号。

## 4. 还没做的那一步（**明确挂账**）

W2 只交付**结构 + 选择器 + 版本矩阵**，池子是空的（只有 README 占位）。真把 wheel
放进去要另外两步，都不在 W2 的写入范围内：

1. 按上表把包从**实测过的解释器**里取出来（cp313 这台机器有：Blender 5.2 的
   site-packages 就是实测环境）；
2. 每个包随包放一份许可证到 LICENSES/（D2 义务 1 —— 没做这一步就不能分发）。

在那之前，runtime.py 会**显式**报「已回落到当前解释器环境」并列出缺哪份 ——
这正是 D2 要的那条 warning，不是故障。
