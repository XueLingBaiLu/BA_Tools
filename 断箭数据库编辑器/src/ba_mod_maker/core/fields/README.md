# 字段词典（产品自带，不是数据源的附属品）

这两份 JSON 是 **GUI 显示词条用的词典** —— 中文名、作用说明、枚举/掩码的值域。
口径与 `core/rules/rulebook.json` 完全一样：**产品知识，随产品走**。

为什么不能留在 `_work/` 或数据源旁边：

- `_work/*.json` 被 `.gitignore:57` 忽略 —— 图与界面都靠它，但它不进 git，换台机器就复现不出来；
- 用户可以用「打开…」直接开 `BA_Mod.zip` / `DataBaseCompiled.json` / `data.unity3d`，
  **那三种来源旁边没有词典**，于是界面会整块退回英文列名。

## 两份文件

| 文件 | 内容 | 形状 |
|---|---|---|
| `field_labels.json` | 24 张表 **391 列**的中文名 + 作用说明 | `{表: {列: {cn, doc}}}` + 顶层 `_meta` |
| `field_enums.json` | **159 列**的枚举 / 位掩码值域（264 档） | `{"表.列": {cn, kind, doc, values, evidence, source}}` + `_meta` |

分工：

- **列集合互补**：`field_labels` 覆盖 391 列，剩下 16 列（`field_labels._meta.skipped_columns`）交给
  `field_enums`；391 + 16 = 407 = 全库列数。
- ⚠️ **但注解有重叠**：两边共同有键的列有 **143 个**（bool 57 + other 84 + enum 2）——
  那是 task-4 收尾时把列名词典并进枚举词典的结果。
- **取值优先查 `field_enums`**（枚举 / 掩码 / 布尔），**中文名与说明优先查 `field_labels`**。
  两边都没有的列，界面上就显示英文列名（并当作一个待补的口子）。

## 怎么用

```python
labels = json.loads((FIELD_DIR / "field_labels.json").read_text(encoding="utf-8"))
labels["Units"]["Cost"]["cn"]  # -> "基础成本"
labels["Units"]["Cost"]["doc"]  # -> 一句话说明（含陷阱与出处）

enums = json.loads((FIELD_DIR / "field_enums.json").read_text(encoding="utf-8"))
node = enums["Ammunitions.TargetType"]
node["kind"]  # "mask"
node["values"]["4"]  # "载具"
```

## 证据等级（每条都带）

`evidence` ∈ `已确认` / `强假设` / `推断`；`source` 写清是哪张表哪一列 / 哪个文件哪一行 / 哪条命令。
当前：**推断 0 条**，强假设 7 条（值域有 `dump.cs` 枚举钉死，但游戏没给官方中文）。

## 怎么再生成

生成脚本仍在 `_work/`（它们要读 `DataBaseCompiled`，是开发期工具）：

    .venv/Scripts/python.exe _work/probe_label_build.py    # 列名词典
    .venv/Scripts/python.exe _work/probe_enum_build.py     # 枚举 / bool / other 条目
    .venv/Scripts/python.exe _work/probe_enum_render.py    # 渲染 docs/字段词典-*.md

生成完把两份 JSON 拷到这里即可。人读版在 `docs/字段词典-词条中文.md` 与
`docs/字段词典-枚举与掩码.md`（由脚本渲染，别手改）。
