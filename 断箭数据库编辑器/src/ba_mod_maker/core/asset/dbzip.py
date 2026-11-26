"""`BA_Mod.zip` 的读写（零 Qt）。

游戏的原生外部库格式。实测结论（kb/04 §5.2）：

- 游戏根目录那一份靠**固定文件名** `BA_Mod.zip` + `File.Exists` 认（补丁做的）；
  场景目录里那份是游戏原生机制、仍叫 `db.zip`；
- 然后 `ZipFile.OpenRead`，按**条目名**分派：`<表名>.json` 且基名落在
  `DataBaseSourceData` 的 24 个属性名里 → 收；**未知条目静默跳过**；
- 条目内容是**明文 UTF-8 JSON 数组**，没有加密（加密只存在于容器里）；
- 缺哪张表，游戏就**不加载**哪张表（不是保留旧值）—— 所以残缺的 BA_Mod.zip 是残废的。

实测对照：`_work/probe_bak/db_cost7777.zip` 的 24 个条目内容与
`DataBaseCompiled/52085/*.json` **逐字节相同**；另外多一个单数 `PlaneFlyPreset.json`。

❗ **那个单数条目不是冗余**（2026-10-02 实机翻案）：游戏取行时按**模型类型名**
`typeof(T).Name` 查字典（ISIL `GetTableRows<T>` 5939-5962），而这张表的类型叫
`Table<PlaneFlyPreset>`（单数）—— 只写复数那一份，游戏在
`ReadTablesFromZip` 里就把它 `continue` 掉了 ⇒ 飞行预设表是空的 ⇒
`LoadUnits.LoadMobility` NRE ⇒ **军械库空白**（`GameLogs/Gamelog__2026_10_02__15_18.log:114`）。
工坊那份能用的包两个名字都写，我们照做（见 `tables.ZIP_ENTRY_ALIASES`）。

## 本模块的两条策略（用户拍板）

1. **未知条目原样保留**。打开一个别人的包再保存，不会把他塞的额外文件清掉 ——
   反正游戏会跳过它们，删掉反而可能弄坏别人的东西。
2. **缺表自动补齐**。补齐的来源由调用方注入，本模块不认识游戏目录。
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from ba_mod_maker.core.asset import container, tables
from ba_mod_maker.core.asset.tables import TABLES
from ba_mod_maker.core.errors import DataSourceError


@dataclass(slots=True)
class DbZip:
    """一份外部库包的内容（成品叫 BA_Mod.zip，改名前的老包叫 db.zip）。

    `tables` 是认得的表（表名 -> 明文 JSON 字节）；
    `extra` 是游戏不认识的条目，原样留着以便保存时写回。
    """

    tables: dict[str, bytes] = field(default_factory=dict)
    extra: dict[str, bytes] = field(default_factory=dict)

    # ---------------- 读 ----------------

    @classmethod
    def read(cls, path: str | Path) -> DbZip:
        """读一个外部库包（按内容认，两种名字都行）。"""
        path = Path(path)
        try:
            with zipfile.ZipFile(path) as zf:
                return cls._from_zipfile(zf)
        except zipfile.BadZipFile as exc:
            raise DataSourceError(
                f"{path.name} 不是合法的 zip",
                hint="外部库必须是普通 zip；游戏用 ZipFile.OpenRead 直接打开，不做嗅探",
                error=str(exc),
            ) from None
        except FileNotFoundError:
            raise DataSourceError(
                f"找不到文件：{path}",
                hint="确认路径；游戏根目录那份固定叫 BA_Mod.zip",
            ) from None

    @classmethod
    def _from_zipfile(cls, zf: zipfile.ZipFile) -> DbZip:
        # ❗ 别名条目（单数 `PlaneFlyPreset.json`）也算"我们认得的" —— 否则会被塞进
        #    `extra` 原样带回去，于是包里同一份内容出现两次（见 tables.BY_ENTRY）。
        by_entry = {name: naming.table for name, naming in tables.BY_ENTRY.items()}
        out = cls()
        for info in zf.infolist():
            if info.is_dir():
                continue
            base = info.filename.rsplit("/", 1)[-1]
            table = by_entry.get(base)
            if table is None:
                # 游戏会静默跳过它；我们留着，保存时原样写回。
                out.extra[info.filename] = zf.read(info)
            else:
                out.tables[table] = zf.read(info)
        if not out.tables:
            raise DataSourceError(
                "这个 zip 里没有任何一张认得的表",
                hint=(
                    "条目名必须是 <表名>.json；FlyPresets 那张要写成 PlaneFlyPresets.json"
                    " **并且**再写一份单数 PlaneFlyPreset.json（游戏按模型类型名查）"
                ),
            )
        return out

    # ---------------- 写 ----------------

    def write(self, path: str | Path) -> Path:
        """写成一个 BA_Mod.zip。

        ⚠️ 用 DEFLATE（`ZIP_DEFLATED`），与游戏和工坊那份一致。
        `ZIP_STORED` 也能被读，但体积会大一个数量级。

        写出顺序 = 24 张表的声明顺序，然后是保留的未知条目。顺序不影响加载
        （游戏按名字查表），固定顺序只是为了产物可比对。
        """
        path = Path(path)
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
            for naming in TABLES:
                blob = self.tables.get(naming.table)
                if blob is None:
                    continue
                zf.writestr(naming.zip_entry, blob)
                # 别名条目（内容相同）：游戏读的是**模型类型名**那一份，见
                # tables.ZIP_ENTRY_ALIASES。多写它没有副作用（不认识的条目被静默跳过）。
                for alias in naming.alias_entries:
                    zf.writestr(alias, blob)
            for name, blob in self.extra.items():
                zf.writestr(name, blob)
        return path

    # ---------------- 与容器对象互转 ----------------

    @classmethod
    def from_object(cls, meta: container.ObjectMeta) -> DbZip:
        """容器对象 -> BA_Mod.zip（**解密**那一步）。"""
        return cls(tables={s.table: s.data for s in meta.tables})

    def to_object(
        self, template: container.ObjectMeta, *, strict: bool = True
    ) -> container.ObjectMeta:
        """BA_Mod.zip -> 容器对象（**加密**那一步）。

        参数
        ----
        template
            形状模板：提供对象头/尾、字段名与顺序、pathID，以及**未改动表要复用的 IV**。
            BA_Mod.zip 自己不携带这些 —— 这正是“加密需要一个模板”的原因。
        strict
            缺表时是否报错。传 `False` 允许残缺（游戏会少加载那几张表）。

        返回
        ----
        `container.ObjectMeta`
        """
        slots: list[container.TableSlot] = []
        missing: list[str] = []
        for tpl in template.tables:
            plain = self.tables.get(tpl.table)
            if plain is None:
                missing.append(tpl.table)
                continue
            # 保留 IV 的规则只有一份：见 container.slot_with_data。
            slots.append(container.slot_with_data(tpl, plain))
        if missing and strict:
            raise DataSourceError(
                f"这个包里缺 {len(missing)} 张表：{'、'.join(missing[:6])}",
                hint="游戏对缺失的表是【不加载】而不是保留旧值，所以残缺的库会少东西",
                missing=missing,
            )
        return container.ObjectMeta(
            object_head=template.object_head,
            object_tail=template.object_tail,
            tables=slots,
            path_id=template.path_id,
            source_sha256="",
        )


def entry_names() -> tuple[str, ...]:
    """24 个真实条目名，按数据源属性的声明顺序。"""
    return tuple(t.zip_entry for t in TABLES)
