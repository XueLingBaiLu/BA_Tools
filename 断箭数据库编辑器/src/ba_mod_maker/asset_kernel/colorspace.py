"""颜色空间契约：Unity m_ColorSpace -> Blender 图像颜色空间（设计稿 §4 D4 / §3.5）。

口径：

| Unity m_ColorSpace | 含义 | Blender 名字 |
|---|---|---|
| 1 | sRGB（BaseMap / 自发光 / 涂装层） | "sRGB" |
| 0 | Linear（法线、Mask、厚度图…） | "Non-Color" |

**逐张显式设置，绝不继承默认** —— Blender 新建图像默认就是 sRGB，法线图不显式改就会
带着 sRGB 进渲染（法线被当颜色做 gamma 解码，凹凸方向整体偏，而这一步**不报错**）。

实测读数（本机 units 包，2026-10-03，11 张真实贴图）：

- 8 张 cs=1：BaseMap / MaskMap / AlbedoTransparency / 涂装层；
- 3 张 cs=0，槽位**全是 _NormalMap**；
- 另有 Soap_T（槽位 _IridescenceThicknessMap）也是 cs=0。

⇒ 所以判据是**按字段判**，不是按名字判：所谓「法线图不得是 sRGB」只是
「槽位是法线时 cs 必须为 0」这条规则的一个特例。

⚠ 未知的 m_ColorSpace **抛错**，不回落 —— 静默回落正是这条契约最容易烂掉的地方。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

#: Blender 的 sRGB 名字（与 image.colorspace_settings.name 逐字符一致）。
SRGB = "sRGB"
#: Blender 的线性名字。
NON_COLOR = "Non-Color"

#: Unity ColorSpace 枚举 -> Blender 名字。
COLORSPACE_BY_UNITY_VALUE: dict[int, str] = {0: NON_COLOR, 1: SRGB}

#: 认作法线槽的槽位名。实测三套 shader 各用各的：
#: HDRP/Lit 用 _NormalMap、Infantry Shader 用 _Normal（见 tests 里的 11 张表）。
NORMAL_MAP_SLOTS: tuple[str, ...] = ("_NormalMap", "_BumpMap", "_Normal")


def colorspace_of(m_color_space: int) -> str:
    """Unity m_ColorSpace -> Blender 颜色空间名。

    :raises ValueError: 类型不是 int（**不做 int() 强转**：字符串 "1"、None、bool 一律拒收），
        或者取值不在 {0, 1} 里 —— **不猜**，让上层看见。
    """
    if isinstance(m_color_space, bool) or not isinstance(m_color_space, int):
        raise ValueError(
            f"m_ColorSpace 必须是整数，收到 "
            f"{type(m_color_space).__name__}={m_color_space!r}；不做强转"
        )
    try:
        return COLORSPACE_BY_UNITY_VALUE[m_color_space]
    except KeyError as exc:
        raise ValueError(
            f"不认识的 m_ColorSpace={m_color_space}；已知 0=Linear->Non-Color、1=sRGB"
        ) from exc


def apply_colorspace(
    owner: object, m_color_space: int, *, attribute: str = "colorspace_settings"
) -> str:
    """把一个 Blender 图像（或任何有 colorspace_settings 的对象）的颜色空间**写上去**。

    返回写进去的名字。这条函数存在的唯一理由：让「显式设置」成为**唯一**的写法，
    没有「不调用就继承默认」这条路。
    """
    settings = getattr(owner, attribute)
    name = colorspace_of(m_color_space)
    settings.name = name
    return name


def srgb_normal_map_violations(
    entries: Iterable[Sequence[object]],
    *,
    slots: Sequence[str] = NORMAL_MAP_SLOTS,
) -> list[str]:
    """逐条检查「法线槽上的贴图不许是 sRGB」，**点名**违规的那张。

    :param entries: 每项 (贴图名, m_ColorSpace, 槽位名)。
    :returns: 违规描述列表；空列表 = 全部合规。空列表不是「没检查」——
        调用方要同时断言输入的条数（见 tests/test_contracts.py 的负控）。
    """
    wanted = {s.lower() for s in slots}
    bad: list[str] = []
    for name, m_color_space, slot in entries:
        if str(slot).lower() not in wanted:
            continue
        mapped = colorspace_of(m_color_space)  # type: ignore[arg-type]
        if mapped == SRGB:
            bad.append(f"{name}（槽位 {slot}）被标成 {SRGB}，法线必须 {NON_COLOR}")
    return bad
