"""容器地址（`AssetBundle.m_Container` 的 first）解析 —— 单一实现。

为什么要有这一层（2026-10-23 实测，读数在 `_work/b1/build-result-b2.txt`）：

* Unity 建包时把容器地址写成**小写**：同一个包里 `GetAllAssetNames()` 回的是
  `assets/resources_moved/modelprefabs/ru/ru_t72b_base.prefab`，而同一次构建里
  `AssetDatabase.GetAssetPath` 仍是 `Assets/Resources_moved/ModelPrefabs/RU/RU_T72B_Base.prefab`；
* `AssetBundle.LoadAsset()` **不区分大小写**：同一份包上 `probe.loadLower` 与
  `probe.loadUpper` 都取到 `RU_T72B_Base`（两条读数都在上面那份文件里）；
* 游戏真件包的容器地址是**大写**（`ammoprefabs` 实测 166 条全大写，见 catalog 那条键）。

⇒ 只按逐字符比，会让「Unity 建的包」在 A2 / 节点契约这一档恒红，而引擎其实取得到。
  catalog 里登的那条地址（引擎真正拿去 `LoadAsset` 的串）大小写无所谓。

口径：**先逐字符命中；不行再按大小写不敏感找唯一一条**。命中 ≥2 条 = 撞名 ⇒ 返回
None（fail-closed，⛔ 不猜）。返回的是**容器里那条原文**，调用方拿它去查表。
"""

from __future__ import annotations

from collections.abc import Iterable

__all__ = ["resolve_container_path"]


def resolve_container_path(names: Iterable[str], asset_path: str) -> str | None:
    """在容器地址里找 `asset_path`：先逐字符，再大小写不敏感（唯一才认）。

    返回**容器里那条原文**（不是传进来的串），查表要用它；找不到或撞名给 None。
    """
    wanted = str(asset_path)
    pool = [str(n) for n in names if n is not None]
    if wanted in pool:
        return wanted
    key = wanted.casefold()
    hits = [n for n in pool if n.casefold() == key]
    if len(hits) == 1:
        return hits[0]
    return None
