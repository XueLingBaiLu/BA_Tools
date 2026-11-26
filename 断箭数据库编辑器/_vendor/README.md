# _vendor · 插件自带的第三方运行时

设计稿 §4 D2 的裁定是「插件全自带」：插件自己保证 UnityPy 及其依赖，
不指望用户环境里恰好有。

- 版本账单与支持面：VERSIONS.md
- 机器可读矩阵：vendor_manifest.json
- 选池与启动自检：../blender_addon/runtime.py

⚠ 池子目前是**空的**（W2 只交付结构 + 选择器 + 矩阵），
所以 register() 会显式报「已回落到当前解释器环境」并列出缺哪份。
