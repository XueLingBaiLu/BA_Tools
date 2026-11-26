r"""G1：[SerializeReference]（ReferencedObjectData）读得进、写得回、逐字节相同。

**判据一行**：pytest tests/test_clone_reference.py -q -> 通过数**从 0 收集变 N passed**
（本文件新建前不存在）。真件那条（本机 units 包）跑在 _work/g1-ref-probe.py 上：

    修前：clone.read_object 读得了 = 4   / 抛 AssetError = 604（全是 ReferencedObjectData）
    修后：clone.read_object(ref_types) 读得了 = 608 / write_object 逐字节相同 = 608

| 用例 | 判据 |
|---|---|
| test_ref_type_pool_* | 池子按 (class, ns, asm) 三个名索引；每棵树的根类型 == 那一族的类名 |
| test_tree_layout_* | ReferencedObject 的子节点顺序 rid -> type -> data；data **没有子节点** |
| test_recorded_counts_* | 夹具里记的真件读数（608 / 3288 / 空引用 0 / 修前读不了 604） |
| test_recorded_samples_roundtrip_* | 3 个真件对象：读 -> 写，**逐字节相同** |
| test_unitypy_agrees_* | 跨实现对拍：UnityPy 读出的字段树 == 我的；UnityPy 写回的字节 == 真件 |
| test_without_ref_types_* | 负控：不给池子必须抛，且**点名 ref_types**（不许退回老症状） |
| test_pool_missing_* / test_pool_entry_without_a_tree_* | 负控：池子缺一族 / 那一族没有树 |
| test_the_match_is_by_name_* | 负控：只改类名一个字符必须红（证明**按名字**而不是按下标匹配） |
| test_empty_reference_* | 空引用（class 为空串）不占字节、不查池子 —— 合成树（真件 0 例） |
| test_the_pool_decides_the_layout_of_data | 合成树 + 改名的一族：池子里那棵树决定 data 怎么解 |
| test_data_before_type_* | 负控：data 排在 type 之前 -> 硬报错，不许静默 |
| test_writer_refuses_data_on_an_empty_reference | 负控：空引用却给了 data -> 不许静默丢字节 |

## 夹具是**录下来的真字节**

出处：本机 units 包（3,381,248,409 B / 162,010 对象）里 608 个 AnimationHub
（classID 114 / type_id 15），由

    .venv\Scripts\python.exe _work\g1-ref-probe.py     -> _work/g1-ref-cache/fixture.json（只读）
    .venv\Scripts\python.exe _work\g1-gen-fixture.py   -> 下面那一段

生成：1 棵 AnimationHub 类型树（58 节点）+ 7 族 ref 类型树 + 3 个真件对象字节。

## 为什么这样能抓住错

* **正控 1**：真件字节读一遍写一遍，逐字节相同；
* **正控 2（跨实现）**：同一份字节，UnityPy 用**它自己的** TypeTreeNode.parse_blob 解树、
  自己读、自己写 —— 字段树与字节两头都对得上，所以不是自证；
* **负控 8 条**：不给池子 / 池子缺一族 / 那一族没有树 / 类名改一个字 / 三元组重名 /
  空引用给 data / data 排在 type 前 / 空引用时池子为空 —— 每条都能红（AGENTS §0.9 动作 2）。

## 这个环境测不了的那条

**真件里的空引用（type.class 为空串）一例都没有**（实测 3288 条引用条目里 0 条），
所以空引用那条分支只由本文件的**合成类型树**覆盖，没有真件读数。
"""

from __future__ import annotations

import base64
import struct
import types

import pytest

from ba_mod_maker.asset_kernel import clone as cl
from ba_mod_maker.asset_kernel import metadata_writer as mw
from ba_mod_maker.core.errors import AssetError

# --------------------------------------------------------------------------
# 录下来的真字节（2026-10-06，units 包 / G1 = [SerializeReference]）
# 重跑：.venv\Scripts\python.exe _work\g1-ref-probe.py    -> _work/g1-ref-cache/fixture.json
#       .venv\Scripts\python.exe _work\g1-gen-fixture.py  -> 本段
# --------------------------------------------------------------------------
#: classID=114 / type_id=15 的 AnimationHub 类型树（2121 节点，**原始字节**）
ANIMATIONHUB_TREE = (
    "OgAAAAEBAAABAAAABwEAgDcAAID/////AAAAAACAAAAAAAAAAAAAAAEAAQA0AgCAdgEAgAwAAAABAAAAQQAAAAAA"
    "AAAAAAAAAQACAN4AAIAAAAAABAAAAAIAAABBAAAAAAAAAAAAAAABAAIALgMAgAkAAAAIAAAAAwAAAEEAAAAAAAAA"
    "AAAAAAEAAQCgAwCAXQEAgAEAAAAEAAAAAUEAAAAAAAAAAAAAAQABAGgCAIDqAQCADAAAAAUAAAAAAAAAAAAAAAAA"
    "AAABAAIA3gAAgAAAAAAEAAAABgAAAAEAgAAAAAAAAAAAAAEAAgAuAwCACQAAAAgAAAAHAAAAAQCAAAAAAAAAAAAA"
    "AQABAEgDAICrAQCA/////wgAAAABgAgAAAAAAAAAAAABAAIBMQAAgDEAAID/////CQAAAAFACAAAAAAAAAAAAAEA"
    "AwDeAACAGwMAgAQAAAAKAAAAAQAIAAAAAAAAAAAAAQADAFEAAIBqAACAAQAAAAsAAAABAAgAAAAAAAAAAAABAAEI"
    "EgAAACYAAAD/////DAAAAAAAAAAAAAAAAAAAAAEAAgExAACAMQAAgP////8NAAAAAAAAAAAAAAAAAAAAAQADAN4A"
    "AIAbAwCABAAAAA4AAAAAAAAAAAAAAAAAAAABAAMCNgAAAGoAAIAIAAAADwAAAAAAAAAAAAAAAAAAAAEABAAuAwCA"
    "SgAAAAgAAAAQAAAAAAAAAAAAAAAAAAAAAQABCBIAAABOAAAA/////xEAAAAAAAAAAAAAAAAAAAABAAIBMQAAgDEA"
    "AID/////EgAAAAAAAAAAAAAAAAAAAAEAAwDeAACAGwMAgAQAAAATAAAAAAAAAAAAAAAAAAAAAQADAjYAAABqAACA"
    "CAAAABQAAAAAAAAAAAAAAAAAAAABAAQALgMAgEoAAAAIAAAAFQAAAAAAAAAAAAAAAAAAAAEAAQgSAAAAWQAAAP//"
    "//8WAAAAAAAAAAAAAAAAAAAAAQACATEAAIAxAACA/////xcAAAAAAAAAAAAAAAAAAAABAAMA3gAAgBsDAIAEAAAA"
    "GAAAAAAAAAAAAAAAAAAAAAEAAwI2AAAAagAAgAgAAAAZAAAAAAAAAAAAAAAAAAAAAQAEAC4DAIBKAAAACAAAABoA"
    "AAAAAAAAAAAAAAAAAAABAAEIEgAAAGQAAAD/////GwAAAAAAAAAAAAAAAAAAAAEAAgExAACAMQAAgP////8cAAAA"
    "AAAAAAAAAAAAAAAAAQADAN4AAIAbAwCABAAAAB0AAAAAAAAAAAAAAAAAAAABAAMCNgAAAGoAAIAIAAAAHgAAAAAA"
    "AAAAAAAAAAAAAAEABAAuAwCASgAAAAgAAAAfAAAAAAAAAAAAAAAAAAAAAQABCBIAAABzAAAA/////yAAAAAAAAAA"
    "AAAAAAAAAAABAAIBMQAAgDEAAID/////IQAAAAAAAAAAAAAAAAAAAAEAAwDeAACAGwMAgAQAAAAiAAAAAAAAAAAA"
    "AAAAAAAAAQADAjYAAABqAACACAAAACMAAAAAAAAAAAAAAAAAAAABAAQALgMAgEoAAAAIAAAAJAAAAAAAAAAAAAAA"
    "AAAAAAEAAQR/AAAAmQAAAP////8lAAAAAYAAAAAAAAAAAAAAAQACAN4AAICkAAAABAAAACYAAAABAAAAAAAAAAAA"
    "AAABAAIA1QMAgKwAAAD/////JwAAAAGAAAAAAAAAAAAAAAEAAwExAACAMQAAgP////8oAAAAAcAAAAAAAAAAAAAA"
    "AQAEAN4AAIAbAwCABAAAACkAAAABAAAAAAAAAAAAAAABAAQAswAAAGoAAID/////KgAAAAGAAAAAAAAAAAAAAAEA"
    "BQAuAwCASgAAAAgAAAArAAAAAQAAAAAAAAAAAAAAAQAFAMQAAADaAAAA/////ywAAAABgCAAAAAAAAAAAAABAAYA"
    "SAMAgN8AAAD/////LQAAAAGAIAAAAAAAAAAAAAEABwExAACAMQAAgP////8uAAAAAUAgAAAAAAAAAAAAAQAIAN4A"
    "AIAbAwCABAAAAC8AAAABACAAAAAAAAAAAAABAAgAUQAAgGoAAIABAAAAMAAAAAEAIAAAAAAAAAAAAAEABgBIAwCA"
    "5QAAAP////8xAAAAAYAgAAAAAAAAAAAAAQAHATEAAIAxAACA/////zIAAAABQCAAAAAAAAAAAAABAAgA3gAAgBsD"
    "AIAEAAAAMwAAAAEAIAAAAAAAAAAAAAEACABRAACAagAAgAEAAAA0AAAAAQAgAAAAAAAAAAAAAQAGAEgDAIDoAAAA"
    "/////zUAAAABgCAAAAAAAAAAAAABAAcBMQAAgDEAAID/////NgAAAAFAIAAAAAAAAAAAAAEACADeAACAGwMAgAQA"
    "AAA3AAAAAQAgAAAAAAAAAAAAAQAIAFEAAIBqAACAAQAAADgAAAABACAAAAAAAAAAAAABAAUA7AAAAGoAAIAAAAAA"
    "OQAAAAEAAAAAAAAAAAAAAG1fRmlsZUlEAG1fUGF0aElEAElBbmltYXRpb25CZWhhdmlvdXIAX3VuaXZlcnNhbFN0"
    "YXRlAG1hbmFnZWRSZWZBcnJheUl0ZW0AcmlkAF9kZW1vU3RhdGUAX2dhbWVTdGF0ZQBfcHJlRGVhdGhTdGF0ZQBf"
    "ZGVhdGhTdGF0ZQBNYW5hZ2VkUmVmZXJlbmNlc1JlZ2lzdHJ5AHJlZmVyZW5jZXMAdmVyc2lvbgBSZWZJZHMAUmVm"
    "ZXJlbmNlZE9iamVjdABSZWZlcmVuY2VkTWFuYWdlZFR5cGUAdHlwZQBjbGFzcwBucwBhc20AUmVmZXJlbmNlZE9i"
    "amVjdERhdGEA"
)

#: m_RefTypes 里的这几族（(class, ns, asm) -> 那一族的类型树原始 blob）
REF_TREES: dict[tuple[str, str, str], str] = {
    ('AnimatorConnect', 'BrokenArrow.Client.Ecs.AnimationBehaviors', 'BrokenArrow'):
        (
            "iAAAAL0CAAABAAAAAAAAADcAAID/////AAAAAACAAAAAAAAAAAAAAAEAAQAQAAAAIAAAAAwAAAABAAAAAAAAAAAA"
            "AAAAAAAAAQACAN4AAIAqAAAABAAAAAIAAAABAIAAAAAAAAAAAAABAAIALgMAgDMAAAAIAAAAAwAAAAEAgAAAAAAA"
            "AAAAAAEAAQBIAwCAPAAAAP////8EAAAAAIAAAAAAAAAAAAAAAQACATEAAIAxAACA/////wUAAAABQAAAAAAAAAAA"
            "AAABAAMA3gAAgBsDAIAEAAAABgAAAAEAAAAAAAAAAAAAAAEAAwBRAACAagAAgAEAAAAHAAAAAQAAAAAAAAAAAAAA"
            "AQABAEgDAIBOAAAA/////wgAAAAAgAAAAAAAAAAAAAABAAIBMQAAgDEAAID/////CQAAAAFAAAAAAAAAAAAAAAEA"
            "AwDeAACAGwMAgAQAAAAKAAAAAQAAAAAAAAAAAAAAAQADAFEAAIBqAACAAQAAAAsAAAABAAAAAAAAAAAAAAABAAEA"
            "SAMAgF4AAAD/////DAAAAACAAAAAAAAAAAAAAAEAAgExAACAMQAAgP////8NAAAAAUAAAAAAAAAAAAAAAQADAN4A"
            "AIAbAwCABAAAAA4AAAABAAAAAAAAAAAAAAABAAMAUQAAgGoAAIABAAAADwAAAAEAAAAAAAAAAAAAAAEAAQBIAwCA"
            "cgAAAP////8QAAAAAIAAAAAAAAAAAAAAAQACATEAAIAxAACA/////xEAAAABQAAAAAAAAAAAAAABAAMA3gAAgBsD"
            "AIAEAAAAEgAAAAEAAAAAAAAAAAAAAAEAAwBRAACAagAAgAEAAAATAAAAAQAAAAAAAAAAAAAAAQABAEgDAICEAAAA"
            "/////xQAAAAAgAAAAAAAAAAAAAABAAIBMQAAgDEAAID/////FQAAAAFAAAAAAAAAAAAAAAEAAwDeAACAGwMAgAQA"
            "AAAWAAAAAQAAAAAAAAAAAAAAAQADAFEAAIBqAACAAQAAABcAAAABAAAAAAAAAAAAAAABAAEASAMAgJoAAAD/////"
            "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"
            "ewAAAAEAAAAAAAAAAAAAAAEAAQCSAgAArAIAAP////98AAAAAIAAAAAAAAAAAAAAAQACANUDAIB6AQAA/////30A"
            "AAABgAAAAAAAAAAAAAABAAMBMQAAgDEAAID/////fgAAAAHAAAAAAAAAAAAAAAEABADeAACAGwMAgAQAAAB/AAAA"
            "AQAAAAAAAAAAAAAAAQAEAEgDAIBqAACA/////4AAAAABgAAAAAAAAAAAAAABAAUBMQAAgDEAAID/////gQAAAAFA"
            "AAAAAAAAAAAAAAEABgDeAACAGwMAgAQAAACCAAAAAQAAAAAAAAAAAAAAAQAGAFEAAIBqAACAAQAAAIMAAAABAAAA"
            "AAAAAAAAAAABAAIA1QMAgJ8BAAD/////hAAAAAGAAAAAAAAAAAAAAAEAAwExAACAMQAAgP////+FAAAAAUAAAAAA"
            "AAAAAAAAAQAEAN4AAIAbAwCABAAAAIYAAAABAAAAAAAAAAAAAAABAAQA3gAAgGoAAIAEAAAAhwAAAAEAAAAAAAAA"
            "AAAAAEFuaW1hdG9yQ29ubmVjdABQUHRyPCRBbmltYXRvcj4AX2FuaW1hdG9yAG1fRmlsZUlEAG1fUGF0aElEAF9s"
            "b2FkU3RhcnRUcmlnZ2VyAF9sb2FkRW5kVHJpZ2dlcgBfdW5sb2FkU3RhcnRUcmlnZ2VyAF91bmxvYWRFbmRUcmln"
            "Z2VyAF9yYWRhckFjdGl2YXRlVHJpZ2dlcgBfcmFkYXJEZWFjdGl2YXRlVHJpZ2dlcgBfc3RhdGljUG9zaXRpb25U"
            "cmlnZ2VyAF9zdGFydE1vdmluZ1RyaWdnZXIAX3JlbG9hZFRyaWdnZXIAX2FpbVN0YXJ0VHJpZ2dlcgBfYWltRW5k"
            "VHJpZ2dlcgBfaGFuZ2FyRXhjbHVkZWRUcmlnZ2VycwBfaGFuZ2FyT3JkZXJlZFRyaWdnZXJzAF9oYW5nYXJBbmlt"
            "YXRpb25TcGVlZABXZWFwb25UcmlnZ2Vyc0RpY3Rpb25hcnkAX3dlYXBvblRyaWdnZXJzAF9rZXlEYXRhAFBQdHI8"
            "JFRyYW5zZm9ybT4AV2VhcG9uRGF0YQBfdmFsdWVEYXRhAE9uU2hvdABPblN0YXJ0QWltaW5nAE9uRW5kQWltaW5n"
            "AE9uU3RhcnRSZWxvYWRpbmcAT25FbmRSZWxvYWRpbmcAVGVycmFpblR5cGVUcmlnZ2Vyc0RpY3Rpb25hcnkAX3Rl"
            "cnJhaW5UeXBlVHJpZ2dlcnMAVGVycmFpblRyaWdnZXJzRGF0YQBPbkVudGVyAE9uRXhpdABFbnRyYW5jZVRyaWdn"
            "ZXJzRGljdGlvbmFyeQBfZW50cmFuY2VUcmlnZ2VycwBFbnRyYW5jZVRyaWdnZXJzRGF0YQBPbk9wZW4AT25DbG9z"
            "ZQBFeHRyYVBhcmFtZXRlcnNEaWN0aW9uYXJ5AF9leHRyYVBhcmFtZXRlcnMA"
        ),
    ('AxisRandom', 'BrokenArrow.Client.Ecs.AnimationBehaviors', 'BrokenArrow'):
        (
            "GwAAAKQAAAABAAAAAAAAADcAAID/////AAAAAACAAAAAAAAAAAAAAAEAAQCgAwCACwAAAAEAAAABAAAAAEAAAAAA"
            "AAAAAAAAAQABAEgDAIAVAAAA/////wIAAAAAgAAAAAAAAAAAAAABAAIBMQAAgDEAAID/////AwAAAAFAAAAAAAAA"
            "AAAAAAEAAwDeAACAGwMAgAQAAAAEAAAAAQAAAAAAAAAAAAAAAQADAFEAAIBqAACAAQAAAAUAAAABAAAAAAAAAAAA"
            "AAABAAEAoQAAgCQAAAAEAAAABgAAAAAAAAAAAAAAAAAAAAEAAQChAACAKwAAAAQAAAAHAAAAAAAAAAAAAAAAAAAA"
            "AQABAKEAAIA4AAAABAAAAAgAAAAAAAAAAAAAAAAAAAABAAEARQAAAFcAAAAUAAAACQAAAAAAAAAAAAAAAAAAAAEA"
            "AgBaAAAAawAAAAwAAAAKAAAAAAAAAAAAAAAAAAAAAQADAN4AAIByAAAABAAAAAsAAAABAIAAAAAAAAAAAAABAAMA"
            "LgMAgHsAAAAIAAAADAAAAAEAgAAAAAAAAAAAAAEAAgChAACAhAAAAAQAAAANAAAAAAAAAAAAAAAAAAAAAQACAKEA"
            "AICRAAAABAAAAA4AAAAAAAAAAAAAAAAAAAABAAEARQAAAJ4AAAAUAAAADwAAAAAAAAAAAAAAAAAAAAEAAgBaAAAA"
            "awAAAAwAAAAQAAAAAAAAAAAAAAAAAAAAAQADAN4AAIByAAAABAAAABEAAAABAIAAAAAAAAAAAAABAAMALgMAgHsA"
            "AAAIAAAAEgAAAAEAgAAAAAAAAAAAAAEAAgChAACAhAAAAAQAAAATAAAAAAAAAAAAAAAAAAAAAQACAKEAAICRAAAA"
            "BAAAABQAAAAAAAAAAAAAAAAAAAABAAEARQAAAKEAAAAUAAAAFQAAAAAAAAAAAAAAAAAAAAEAAgBaAAAAawAAAAwA"
            "AAAWAAAAAAAAAAAAAAAAAAAAAQADAN4AAIByAAAABAAAABcAAAABAIAAAAAAAAAAAAABAAMALgMAgHsAAAAIAAAA"
            "GAAAAAEAgAAAAAAAAAAAAAEAAgChAACAhAAAAAQAAAAZAAAAAAAAAAAAAAAAAAAAAQACAKEAAICRAAAABAAAABoA"
            "AAAAAAAAAAAAAAAAAABBeGlzUmFuZG9tAF9sb2RHcm91cABfaW5zcGVjdG9yTmFtZQBfc3BlZWQAX21pbmltYWxU"
            "aW1lAF9tYXhpbWFsVGltZQBBeGlzUmFuZENvbnRhaW5lcgBfeABQUHRyPCRUcmFuc2Zvcm0+AFNvdXJjZQBtX0Zp"
            "bGVJRABtX1BhdGhJRABNaW5pbWFsQW5nbGUATWF4aW1hbEFuZ2xlAF95AF96AA=="
        ),
    ('DustVFX', 'BrokenArrow.Client.Ecs.AnimationBehaviors', 'BrokenArrow'):
        (
            "GgAAAJcAAAABAAAAAAAAADcAAID/////AAAAAACAAAAAAAAAAAAAAAEAAQAIAAAAFwAAAP////8BAAAAAIAAAAAA"
            "AAAAAAAAAQACATEAAIAxAACA/////wIAAAAAgAAAAAAAAAAAAAABAAMA3gAAgBsDAIAEAAAAAwAAAAAAAAAAAAAA"
            "AAAAAAEAAwAIAAAAagAAgP////8EAAAAAIAAAAAAAAAAAAAAAQAEANUDAIAdAAAA/////wUAAAAAAAAAAAAAAAAA"
            "AAABAAUBMQAAgDEAAID/////BgAAAAAAAAAAAAAAAAAAAAEABgDeAACAGwMAgAQAAAAHAAAAAAAAAAAAAAAAAAAA"
            "AQAGACQAAABqAACADAAAAAgAAAAAAAAAAAAAAAAAAAABAAcA3gAAgDUAAAAEAAAACQAAAAEAgAAAAAAAAAAAAAEA"
            "BwAuAwCAPgAAAAgAAAAKAAAAAQCAAAAAAAAAAAAAAQAEAEcAAABSAAAA/////wsAAAAAgAAAAAAAAAAAAAABAAUB"
            "MQAAgDEAAID/////DAAAAACAAAAAAAAAAAAAAAEABgDeAACAGwMAgAQAAAANAAAAAAAAAAAAAAAAAAAAAQAGAEcA"
            "AABqAACA/////w4AAAAAgAAAAAAAAAAAAAABAAcAoAMAgF4AAAABAAAADwAAAABAAAAAAAAAAAAAAAEABwDVAwCA"
            "agAAAP////8QAAAAAAAAAAAAAAAAAAAAAQAIATEAAIAxAACA/////xEAAAAAAAAAAAAAAAAAAAABAAkA3gAAgBsD"
            "AIAEAAAAEgAAAAAAAAAAAAAAAAAAAAEACQByAAAAagAAgAwAAAATAAAAAAAAAAAAAAAAAAAAAQAKAN4AAIA1AAAA"
            "BAAAABQAAAABAIAAAAAAAAAAAAABAAoALgMAgD4AAAAIAAAAFQAAAAEAgAAAAAAAAAAAAAEABADVAwCAhAAAAP//"
            "//8WAAAAAMAAAAAAAAAAAAAAAQAFATEAAIAxAACA/////xcAAAAAQAAAAAAAAAAAAAABAAYA3gAAgBsDAIAEAAAA"
            "GAAAAAAAAAAAAAAAAAAAAAEABgCgAwCAagAAgAEAAAAZAAAAAAAAAAAAAAAAAAAARHVzdFZGWABWZnhUZXJyYWlu"
            "RGF0YQBfZGF0YQBQb2ludHMAUFB0cjwkVHJhbnNmb3JtPgBtX0ZpbGVJRABtX1BhdGhJRABWZnhUZXJyYWluAE92"
            "ZXJyaWRlVmZ4AFRlcnJhaW5UeXBlAEVmZmVjdHMAUFB0cjwkR2FtZU9iamVjdD4AUGxheU9ubHlPblRlcnJhaW5z"
            "AA=="
        ),
    ('MathConnect', 'BrokenArrow.Client.Ecs.AnimationBehaviors', 'BrokenArrow'):
        (
            "GwAAAOUAAAABAAAAAAAAADcAAID/////AAAAAACAAAAAAAAAAAAAAAEAAQCgAwCADAAAAAEAAAABAAAAAEAAAAAA"
            "AAAAAAAAAQABABYAAAAlAAAADQAAAAIAAAAAgAAAAAAAAAAAAAABAAIAoQAAgC8AAAAEAAAAAwAAAAAAAAAAAAAA"
            "AAAAAAEAAgChAACAOgAAAAQAAAAEAAAAAAAAAAAAAAAAAAAAAQACAKEAAIBCAAAABAAAAAUAAAAAAAAAAAAAAAAA"
            "AAABAAIAoAMAgEwAAAABAAAABgAAAABBAAAAAAAAAAAAAAEAAQBfAAAAcAAAAAwAAAAHAAAAAAAAAAAAAAAAAAAA"
            "AQACAN4AAIB2AAAABAAAAAgAAAABAIAAAAAAAAAAAAABAAIALgMAgH8AAAAIAAAACQAAAAEAgAAAAAAAAAAAAAEA"
            "AQBfAAAAiAAAAAwAAAAKAAAAAAAAAAAAAAAAAAAAAQACAN4AAIB2AAAABAAAAAsAAAABAIAAAAAAAAAAAAABAAIA"
            "LgMAgH8AAAAIAAAADAAAAAEAgAAAAAAAAAAAAAEAAQCgAwCAkAAAAAEAAAANAAAAAEEAAAAAAAAAAAAAAQABAKAD"
            "AICcAAAAAQAAAA4AAAAAQQAAAAAAAAAAAAABAAEAoAMAgKgAAAABAAAADwAAAABBAAAAAAAAAAAAAAEAAQC0AAAA"
            "xQAAAP////8QAAAAAIAAAAAAAAAAAAAAAQACANUDAIDRAAAA/////xEAAAABAAAAAAAAAAAAAAABAAMBMQAAgDEA"
            "AID/////EgAAAAEAAAAAAAAAAAAAAAEABADeAACAGwMAgAQAAAATAAAAAQAAAAAAAAAAAAAAAQAEAF8AAABqAACA"
            "DAAAABQAAAABAAAAAAAAAAAAAAABAAUA3gAAgHYAAAAEAAAAFQAAAAEAgAAAAAAAAAAAAAEABQAuAwCAfwAAAAgA"
            "AAAWAAAAAQCAAAAAAAAAAAAAAQACANUDAIDaAAAA/////xcAAAABgAAAAAAAAAAAAAABAAMBMQAAgDEAAID/////"
            "GAAAAAFAAAAAAAAAAAAAAAEABADeAACAGwMAgAQAAAAZAAAAAQAAAAAAAAAAAAAAAQAEAKEAAIBqAACABAAAABoA"
            "AAABAAAAAAAAAAAAAABNYXRoQ29ubmVjdABfbG9kR3JvdXAAT2JqZWN0Rm9sbG93ZXIAX3NldHRpbmdzAF9mcmVx"
            "dWVuY3kAX2RhbXBlcgBfcmVhY3Rpb24AX2RlZmF1bHRTZXJpYWxpemVkAFBQdHI8JFRyYW5zZm9ybT4AX3Jvb3QA"
            "bV9GaWxlSUQAbV9QYXRoSUQAX3RhcmdldABfZnJlZXplWFBvcwBfZnJlZXplWVBvcwBfZnJlZXplWlBvcwBXZWFw"
            "b25TaG90Rm9yY2VzAF9zaG90Rm9yY2VzAF9rZXlEYXRhAF92YWx1ZURhdGEA"
        ),
    ('ModelReplace', 'BrokenArrow.Client.Ecs.AnimationBehaviors', 'BrokenArrow'):
        (
            "FAAAAG0AAAABAAAAAAAAADcAAID/////AAAAAAAAAAAAAAAAAAAAAAEAAQANAAAAHwAAAAwAAAABAAAAAAAAAAAA"
            "AAAAAAAAAQACAN4AAIAlAAAABAAAAAIAAAABAIAAAAAAAAAAAAABAAIALgMAgC4AAAAIAAAAAwAAAAEAgAAAAAAA"
            "AAAAAAEAAQDVAwCANwAAAP////8EAAAAAAAAAAAAAAAAAAAAAQACATEAAIAxAACA/////wUAAAAAAAAAAAAAAAAA"
            "AAABAAMA3gAAgBsDAIAEAAAABgAAAAAAAAAAAAAAAAAAAAEAAwBDAAAAagAAgAwAAAAHAAAAAAAAAAAAAAAAAAAA"
            "AQAEAN4AAIAlAAAABAAAAAgAAAABAIAAAAAAAAAAAAABAAQALgMAgC4AAAAIAAAACQAAAAEAgAAAAAAAAAAAAAEA"
            "AQDVAwCAVAAAAP////8KAAAAAAAAAAAAAAAAAAAAAQACATEAAIAxAACA/////wsAAAAAAAAAAAAAAAAAAAABAAMA"
            "3gAAgBsDAIAEAAAADAAAAAAAAAAAAAAAAAAAAAEAAwANAAAAagAAgAwAAAANAAAAAAAAAAAAAAAAAAAAAQAEAN4A"
            "AIAlAAAABAAAAA4AAAABAIAAAAAAAAAAAAABAAQALgMAgC4AAAAIAAAADwAAAAEAgAAAAAAAAAAAAAEAAQANAAAA"
            "XAAAAAwAAAAQAAAAAAAAAAAAAAAAAAAAAQACAN4AAIAlAAAABAAAABEAAAABAIAAAAAAAAAAAAABAAIALgMAgC4A"
            "AAAIAAAAEgAAAAEAgAAAAAAAAAAAAAEAAQChAACAZgAAAAQAAAATAAAAAAAAAAAAAAAAAAAATW9kZWxSZXBsYWNl"
            "AFBQdHI8JEdhbWVPYmplY3Q+AF9yb290AG1fRmlsZUlEAG1fUGF0aElEAF9tb3ZlVG9Sb290AFBQdHI8JFRyYW5z"
            "Zm9ybT4AX3JlbW92ZQBfbmV3TW9kZWwAX2RlbGF5AA=="
        ),
    ('ShowVFX', 'BrokenArrow.Client.Ecs.AnimationBehaviors', 'BrokenArrow'):
        (
            "EgAAAGUAAAABAAAAAAAAADcAAID/////AAAAAAAAAAAAAAAAAAAAAAEAAQAIAAAAEAAAAP////8BAAAAAAAAAAAA"
            "AAAAAAAAAQACATEAAIAxAACA/////wIAAAAAAAAAAAAAAAAAAAABAAMA3gAAgBsDAIAEAAAAAwAAAAAAAAAAAAAA"
            "AAAAAAEAAwAIAAAAagAAgP////8EAAAAAAAAAAAAAAAAAAAAAQAEANUDAIAWAAAA/////wUAAAAAAAAAAAAAAAAA"
            "AAABAAUBMQAAgDEAAID/////BgAAAAAAAAAAAAAAAAAAAAEABgDeAACAGwMAgAQAAAAHAAAAAAAAAAAAAAAAAAAA"
            "AQAGAB0AAABqAACADAAAAAgAAAAAAAAAAAAAAAAAAAABAAcA3gAAgC4AAAAEAAAACQAAAAEAgAAAAAAAAAAAAAEA"
            "BwAuAwCANwAAAAgAAAAKAAAAAQCAAAAAAAAAAAAAAQAEANUDAIBAAAAA/////wsAAAAAAAAAAAAAAAAAAAABAAUB"
            "MQAAgDEAAID/////DAAAAAAAAAAAAAAAAAAAAAEABgDeAACAGwMAgAQAAAANAAAAAAAAAAAAAAAAAAAAAQAGAEQA"
            "AABqAACADAAAAA4AAAAAAAAAAAAAAAAAAAABAAcA3gAAgC4AAAAEAAAADwAAAAEAgAAAAAAAAAAAAAEABwAuAwCA"
            "NwAAAAgAAAAQAAAAAQCAAAAAAAAAAAAAAQAEAN4AAIBWAAAABAAAABEAAAAAAAAAAAAAAAAAAABTaG93VkZYAFZm"
            "eERhdGEAX2RhdGEAUG9pbnRzAFBQdHI8JFRyYW5zZm9ybT4AbV9GaWxlSUQAbV9QYXRoSUQAVmZ4AFBQdHI8JEdh"
            "bWVPYmplY3Q+AERlZmF1bHRWZnhUeXBlAA=="
        ),
    ('SpawnVFX', 'BrokenArrow.Client.Ecs.AnimationBehaviors', 'BrokenArrow'):
        (
            "HwAAANkAAAABAAAAAAAAADcAAID/////AAAAAACAAAAAAAAAAAAAAAEAAQAJAAAAFgAAAP////8BAAAAAIAAAAAA"
            "AAAAAAAAAQACATEAAIAxAACA/////wIAAAAAgAAAAAAAAAAAAAABAAMA3gAAgBsDAIAEAAAAAwAAAAAAAAAAAAAA"
            "AAAAAAEAAwAJAAAAagAAgP////8EAAAAAIAAAAAAAAAAAAAAAQAEAEgDAIAcAAAA/////wUAAAAAgAAAAAAAAAAA"
            "AAABAAUBMQAAgDEAAID/////BgAAAAFAAAAAAAAAAAAAAAEABgDeAACAGwMAgAQAAAAHAAAAAQAAAAAAAAAAAAAA"
            "AQAGAFEAAIBqAACAAQAAAAgAAAABAAAAAAAAAAAAAAABAAQAoQAAgCYAAAAEAAAACQAAAAAAAAAAAAAAAAAAAAEA"
            "BACgAwCAMgAAAAEAAAAKAAAAAEAAAAAAAAAAAAAAAQAEACEEAIA+AAAACAAAAAsAAAAAACAAAAAAAAAAAAABAAUA"
            "3gAAgE8AAAAEAAAADAAAAAAAIAAAAAAAAAAAAAEABQDeAACAUQAAAAQAAAANAAAAAAAgAAAAAAAAAAAAAQAEAKEA"
            "AIBTAAAABAAAAA4AAAAAAAAAAAAAAAAAAAABAAQAXgAAAGYAAAD/////DwAAAACAAAAAAAAAAAAAAAEABQExAACA"
            "MQAAgP////8QAAAAAIAAAAAAAAAAAAAAAQAGAN4AAIAbAwCABAAAABEAAAAAAAAAAAAAAAAAAAABAAYAXgAAAGoA"
            "AID/////EgAAAACAAAAAAAAAAAAAAAEABwBrAAAAfAAAAAwAAAATAAAAAAAAAAAAAAAAAAAAAQAIAN4AAICHAAAA"
            "BAAAABQAAAABAIAAAAAAAAAAAAABAAgALgMAgJAAAAAIAAAAFQAAAAEAgAAAAAAAAAAAAAEABwCgAwCAmQAAAAEA"
            "AAAWAAAAAEEAAAAAAAAAAAAAAQAHAKsAAAC9AAAADAAAABcAAAAAAAAAAAAAAAAAAAABAAgA3gAAgIcAAAAEAAAA"
            "GAAAAAEAgAAAAAAAAAAAAAEACAAuAwCAkAAAAAgAAAAZAAAAAQCAAAAAAAAAAAAAAQAHAEgDAIDBAAAA/////xoA"
            "AAAAgAAAAAAAAAAAAAABAAgBMQAAgDEAAID/////GwAAAAFAAAAAAAAAAAAAAAEACQDeAACAGwMAgAQAAAAcAAAA"
            "AQAAAAAAAAAAAAAAAQAJAFEAAIBqAACAAQAAAB0AAAABAAAAAAAAAAAAAAABAAcAoAMAgMwAAAABAAAAHgAAAABB"
            "AAAAAAAAAAAAAFNwYXduVkZYAFNwYXduVmZ4RGF0YQBfZGF0YQBHcm91cE5hbWUAR3JvdXBXZWlnaHQAVGVycmFp"
            "blR5cGUATWluTWF4U3Bhd25Db3VudAB4AHkARGVhdGhEZWxheQBWZnhEYXRhAERhdGEAUFB0cjwkVHJhbnNmb3Jt"
            "PgBTcGF3blBvaW50AG1fRmlsZUlEAG1fUGF0aElEAER5bmFtaWNTcGF3blBvaW50AFBQdHI8JEdhbWVPYmplY3Q+"
            "AFZmeABBdWRpb0V2ZW50AFNwYXduQXNDaGlsZAA="
        ),
}

#: 3 个真件 AnimationHub 对象（字节 + 录下来的 rid 序列）
SAMPLES: dict[int, dict[str, object]] = {
    -9199692119187050589: {
        "payload": (
            "AAAAAKMvG5ZJgzTeAQAAAAAAAAAbqVEKT7/AQAAAAAAAAAAAAQAAAAYAMDaLg3hvAAAAAAAAAAAAAAAAAgAAAAEA"
            "AAAGADA2i4N4bwoAAABBeGlzUmFuZG9tAAApAAAAQnJva2VuQXJyb3cuQ2xpZW50LkVjcy5BbmltYXRpb25CZWhh"
            "dmlvcnMAAAALAAAAQnJva2VuQXJyb3cAAQAAAAAAAAAAAKBBAAAAQAAAIEEAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
            "AACjLyeuQ2g/6QAANMIAADRCAAAAAAAAAAAAAAAAAAAAAAAAAAA="
        ),
        "rids": [
            8032314569348087814,
        ],
        "ref_count": 1,
        "triples": [
            ('AxisRandom', 'BrokenArrow.Client.Ecs.AnimationBehaviors', 'BrokenArrow'),
        ],
    },
    -9156792366958150884: {
        "payload": (
            "AAAAABx76+r0HCvaAQAAAAAAAAAbqVEKT7/AQAAAAAAAAAAAAAAAAAEAAAABAIhMHrOqAgAAAAAAAAAAAgAAAAEA"
            "AAABAIhMHrOqAg8AAABBbmltYXRvckNvbm5lY3QAKQAAAEJyb2tlbkFycm93LkNsaWVudC5FY3MuQW5pbWF0aW9u"
            "QmVoYXZpb3JzAAAACwAAAEJyb2tlbkFycm93AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
            "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAIA/AQAAAAAAAAAce+b68GzPDgEAAAAAAAAAAwAAAEFpbQAHAAAA"
            "QWltU3RvcAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=="
        ),
        "rids": [
            192162876831039489,
        ],
        "ref_count": 1,
        "triples": [
            ('AnimatorConnect', 'BrokenArrow.Client.Ecs.AnimationBehaviors', 'BrokenArrow'),
        ],
    },
    -9137087956441601870: {
        "payload": (
            "AAAAALJo/LB7yth7AQAAAAAAAAAbqVEKT7/AQAAAAAADAAAAAgBEJKDzwngDAEQkoPPCeAAAkKClQcF0AAAAAAIA"
            "AAAhAByJXb4EUSYAHIldvgRRAAAAAAIAAAAjAByJXb4EUTIAvN+Qdax4AgAAAAcAAAAhAByJXb4EUQcAAABTaG93"
            "VkZYACkAAABCcm9rZW5BcnJvdy5DbGllbnQuRWNzLkFuaW1hdGlvbkJlaGF2aW9ycwAAAAsAAABCcm9rZW5BcnJv"
            "dwABAAAAAQAAAAAAAACyaCPh/0UZZwAAAAABAAAAIwAciV2+BFEIAAAAU3Bhd25WRlgpAAAAQnJva2VuQXJyb3cu"
            "Q2xpZW50LkVjcy5BbmltYXRpb25CZWhhdmlvcnMAAAALAAAAQnJva2VuQXJyb3cAAQAAAAAAAAAAAIA/AAAAAAAA"
            "AAAAAAAAAAAAAAIAAAAAAAAAsmiteyLcNf0AAAAAAQAAAC0Gow3aCqMkMwAAAGV2ZW50Oi9Ccm9rZW5BcnJvd0dh"
            "bWUvQkFfVmVoaWNsZV9EZWF0aC9CdXJuVmVoaWNsZQABAAAAAAAAALJorXsi3DX9AAAAAAEAAABhzC/Wwtb5UwAA"
            "AAABAAAAJgAciV2+BFEHAAAARHVzdFZGWAApAAAAQnJva2VuQXJyb3cuQ2xpZW50LkVjcy5BbmltYXRpb25CZWhh"
            "dmlvcnMAAAALAAAAQnJva2VuQXJyb3cAAQAAAAIAAAAAAAAAsmjiZe/U5q0AAAAAsmjj8J/PKygAAAAAAAAAAAAA"
            "kKClQcF0CgAAAEF4aXNSYW5kb20AACkAAABCcm9rZW5BcnJvdy5DbGllbnQuRWNzLkFuaW1hdGlvbkJlaGF2aW9y"
            "cwAAAAsAAABCcm9rZW5BcnJvdwABAAAAAAAAAAAAoEEAAABAAAAgQQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAALJo"
            "PJ+FU/rlAAA0wgAANEIAAAAAAAAAAAAAAAAAAAAAAAAAADIAvN+Qdax4DAAAAE1vZGVsUmVwbGFjZSkAAABCcm9r"
            "ZW5BcnJvdy5DbGllbnQuRWNzLkFuaW1hdGlvbkJlaGF2aW9ycwAAAAsAAABCcm9rZW5BcnJvdwAAAAAAsmj8sHvK"
            "2HsAAAAAAgAAAAAAAACyaFc87CcBYgAAAACyaFop1tHKlQAAAACeP7ra4VkixAAAAAACAEQkoPPCeAsAAABNYXRo"
            "Q29ubmVjdAApAAAAQnJva2VuQXJyb3cuQ2xpZW50LkVjcy5BbmltYXRpb25CZWhhdmlvcnMAAAALAAAAQnJva2Vu"
            "QXJyb3cAAgAAAAAAgD/NzMw9AAAAQAEAAAAAAAAAsmhlhKLk9Z4AAAAAsmjhYPnekeUAAAAAAQAAAAAAAAABAAAA"
            "AAAAALJoINsLUqriAQAAAAAAFkQDAEQkoPPCeAsAAABNYXRoQ29ubmVjdAApAAAAQnJva2VuQXJyb3cuQ2xpZW50"
            "LkVjcy5BbmltYXRpb25CZWhhdmlvcnMAAAALAAAAQnJva2VuQXJyb3cAAgAAAAAAgD/NzMw9AAAAQAEAAAAAAAAA"
            "smhlhKLk9Z4AAAAAsmgDT6iQvVEAAAAAAQAAAAAAAAABAAAAAAAAALJoINsLUqriAQAAAAAAFkQ="
        ),
        "rids": [
            5838000325920555041,
            5838000325920555043,
            5838000325920555046,
            8413077758523998208,
            8695454245634965554,
            8701785299161972738,
            8701785299161972739,
        ],
        "ref_count": 7,
        "triples": [
            ('AxisRandom', 'BrokenArrow.Client.Ecs.AnimationBehaviors', 'BrokenArrow'),
            ('DustVFX', 'BrokenArrow.Client.Ecs.AnimationBehaviors', 'BrokenArrow'),
            ('MathConnect', 'BrokenArrow.Client.Ecs.AnimationBehaviors', 'BrokenArrow'),
            ('ModelReplace', 'BrokenArrow.Client.Ecs.AnimationBehaviors', 'BrokenArrow'),
            ('ShowVFX', 'BrokenArrow.Client.Ecs.AnimationBehaviors', 'BrokenArrow'),
            ('SpawnVFX', 'BrokenArrow.Client.Ecs.AnimationBehaviors', 'BrokenArrow'),
        ],
    },
}

#: 真件读数（复算命令见文件头）：608 个 AnimationHub，ref 条目 3288，空引用 0
REAL_COUNTS = {
    "instances": 608,
    "ref_entries": 3288,
    "empty_type_entries": 0,
    # 修之前的读数（第一次跑，旧 codec）：_work/g1-ref-cache/stdout.txt
    "unreadable_before_fix": 604,
    "unreadable_after_fix": 0,
    "animationhub_type_id": 15,
    "unity_version": '2022.3.62f3',
}

COMMON = cl.unitypy_common_strings()
NODE_STRUCT = struct.Struct("<hBBIIiiiQ")

#: 最小那个样本（1 条 AxisRandom）；它那一族的类型池键
SMALLEST = -9199692119187050589
AXIS_KEY = ("AxisRandom", "BrokenArrow.Client.Ecs.AnimationBehaviors", "BrokenArrow")


# --------------------------------------------------------------------------- #
# 夹具 -> 产品对象
# --------------------------------------------------------------------------- #
def _tree_of(blob: bytes) -> mw.TypeTree:
    """一段内嵌类型树 blob -> mw.TypeTree（夹具里存的是**原始字节**）。"""
    node_count, size = struct.unpack_from("<ii", blob, 0)
    return mw.TypeTree(
        node_count=node_count,
        string_buffer_size=size,
        nodes=blob[8 : 8 + 32 * node_count],
        strings=blob[8 + 32 * node_count :],
    )


def _ref_entries() -> list[mw.SerializedType]:
    """把录下来的 ref 类型树包成 mw.SerializedType（与真件元数据里那一节的形状相同）。"""
    return [
        mw.SerializedType(
            class_id=-1,
            is_stripped=False,
            script_type_index=index,
            script_id=None,
            old_type_hash=b"\x00" * 16,
            type_tree=_tree_of(base64.b64decode(blob)),
            class_name=key[0],
            namespace=key[1],
            assembly_name=key[2],
            is_ref_type=True,
        )
        for index, (key, blob) in enumerate(REF_TREES.items())
    ]


@pytest.fixture(scope="module")
def nodes() -> tuple[cl.TypeNode, ...]:
    return cl.read_type_nodes(base64.b64decode(ANIMATIONHUB_TREE), common_strings=COMMON)


@pytest.fixture(scope="module")
def pool() -> dict[tuple[str, str, str], tuple[cl.TypeNode, ...]]:
    return cl.ref_type_pool(_ref_entries(), common_strings=COMMON)


def _payload(path_id: int) -> bytes:
    return base64.b64decode(SAMPLES[path_id]["payload"])


def _subtree(nodes: tuple[cl.TypeNode, ...], index: int) -> list[cl.TypeNode]:
    """扁平节点表里第 index 个节点的**所有后代**（到同级或更浅为止）。"""
    level = nodes[index].level
    out: list[cl.TypeNode] = []
    for node in nodes[index + 1 :]:
        if node.level <= level:
            break
        out.append(node)
    return out


def _at(nodes: tuple[cl.TypeNode, ...], after: int, type_name: str) -> int:
    return next(i for i, n in enumerate(nodes) if i > after and n.type == type_name)


# --------------------------------------------------------------------------- #
# ① 类型池：按三个字符串名索引
# --------------------------------------------------------------------------- #
def test_ref_type_pool_is_indexed_by_the_three_names(pool) -> None:
    """池子的键就是 (class, ns, asm)；每棵树的根类型 == 那一族的类名（真件实测如此）。"""
    assert set(pool) == set(REF_TREES)
    assert len(pool) == 7
    for key, tree_nodes in pool.items():
        assert tree_nodes, f"{key} 没有类型树"
        assert tree_nodes[0].type == key[0], f"{key} 的树根类型是 {tree_nodes[0].type}"
        assert tree_nodes[0].name == "Base"


def test_ref_type_pool_accepts_a_metadata_object(pool) -> None:
    """传 SerializedMetadata（有 .ref_types）与传它的 ref_types 序列，结果必须相同。"""
    meta_like = types.SimpleNamespace(ref_types=_ref_entries())
    assert cl.ref_type_pool(meta_like, common_strings=COMMON) == pool


def test_ref_type_pool_rejects_duplicate_triples() -> None:
    """负控：同一三元组两条 -> 抛（按名字匹配的池子不允许重名）。"""
    entries = _ref_entries()
    with pytest.raises(AssetError) as excinfo:
        cl.ref_type_pool([*entries, entries[0]], common_strings=COMMON)
    assert "重复" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# ② 类型树形状：ReferencedObject 的三段顺序
# --------------------------------------------------------------------------- #
def test_tree_layout_is_rid_then_type_then_data(nodes) -> None:
    """真件那棵树：rid -> type{class,ns,asm} -> data，且 data **一个子节点都没有**。"""
    assert len(nodes) == 58
    root = nodes[0]
    assert (root.type, root.name) == ("MonoBehaviour", "Base")
    hits = [i for i, n in enumerate(nodes) if n.type == "ReferencedObject"]
    assert len(hits) == 1
    index = hits[0]
    kids = [n for n in _subtree(nodes, index) if n.level == nodes[index].level + 1]
    assert [(n.name, n.type) for n in kids] == [
        ("rid", "SInt64"),
        ("type", "ReferencedManagedType"),
        ("data", "ReferencedObjectData"),
    ]
    type_at = _at(nodes, index, "ReferencedManagedType")
    assert [n.name for n in _subtree(nodes, type_at) if n.level == nodes[type_at].level + 1] == [
        "class",
        "ns",
        "asm",
    ]
    data_at = _at(nodes, index, "ReferencedObjectData")
    assert _subtree(nodes, data_at) == [], "data 有子节点的话就不是 [SerializeReference] 了"


def test_recorded_counts_match_the_report() -> None:
    """夹具里记的真件读数 —— 防止夹具被改窄了还说「全覆盖」。"""
    assert REAL_COUNTS["instances"] == 608
    assert REAL_COUNTS["ref_entries"] == 3288
    assert REAL_COUNTS["empty_type_entries"] == 0
    assert REAL_COUNTS["unreadable_before_fix"] == 604
    assert REAL_COUNTS["unreadable_after_fix"] == 0
    assert REAL_COUNTS["animationhub_type_id"] == 15
    assert REAL_COUNTS["unity_version"] == "2022.3.62f3"
    assert set(SAMPLES) == {SMALLEST, -9156792366958150884, -9137087956441601870}


# --------------------------------------------------------------------------- #
# ③ 正控：真件对象 读 -> 写，逐字节相同
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("path_id", sorted(SAMPLES))
def test_recorded_samples_roundtrip_byte_identical(path_id: int, nodes, pool) -> None:
    """真件字节读一遍再写一遍，必须**逐字节相同**（G1 判据 ② 的单元版）。"""
    data = _payload(path_id)
    fields = cl.read_object(data, nodes, ref_types=pool)
    assert cl.write_object(fields, nodes, ref_types=pool) == data
    assert cl.roundtrip(data, nodes, ref_types=pool) == data
    assert cl.read_object(cl.write_object(fields, nodes, ref_types=pool), nodes,
                          ref_types=pool) == fields
    # data 那几格必须**真的解开了**（不是跳过 data 蒙混过关）
    refs = fields["references"]["RefIds"]
    assert fields["references"]["version"] == 2
    assert [int(r["rid"]) for r in refs] == SAMPLES[path_id]["rids"]
    assert len(refs) == SAMPLES[path_id]["ref_count"]
    for entry in refs:
        key = (entry["type"]["class"], entry["type"]["ns"], entry["type"]["asm"])
        assert key in pool, f"{path_id}: {key} 不在类型池里"
        assert isinstance(entry["data"], dict) and entry["data"], "data 没解开"
    if path_id == SMALLEST:
        assert sorted(refs[0]["data"]) == [
            "_inspectorName", "_lodGroup", "_maximalTime", "_minimalTime",
            "_speed", "_x", "_y", "_z",
        ]


# --------------------------------------------------------------------------- #
# ④ 跨实现对拍（UnityPy 是另一份实现）
# --------------------------------------------------------------------------- #
def test_unitypy_reads_and_writes_the_same_bytes(nodes, pool) -> None:
    """UnityPy 自己解树、自己读、自己写：字段树与真件字节两头都必须对上。"""
    pytest.importorskip("UnityPy")
    from UnityPy.helpers.TypeTreeHelper import read_typetree, write_typetree
    from UnityPy.helpers.TypeTreeNode import TypeTreeNode
    from UnityPy.streams import EndianBinaryReader, EndianBinaryWriter

    root = TypeTreeNode.parse_blob(
        EndianBinaryReader(base64.b64decode(ANIMATIONHUB_TREE), "<"), 22
    )
    assetfile = types.SimpleNamespace(
        ref_types=[
            types.SimpleNamespace(
                m_ClassName=key[0],
                m_NameSpace=key[1],
                m_AssemblyName=key[2],
                node=TypeTreeNode.parse_blob(
                    EndianBinaryReader(base64.b64decode(blob), "<"), 22
                ),
            )
            for key, blob in REF_TREES.items()
        ]
    )
    for path_id in sorted(SAMPLES):
        data = _payload(path_id)
        mine = cl.read_object(data, nodes, ref_types=pool)
        theirs = read_typetree(
            root, EndianBinaryReader(data, "<"), as_dict=True,
            byte_size=len(data), assetsfile=assetfile,
        )
        assert theirs == mine, f"{path_id}: UnityPy 读出来的字段树与我的不同"
        writer = EndianBinaryWriter(endian="<")
        write_typetree(mine, root, writer, assetfile)
        assert writer.bytes == data, f"{path_id}: UnityPy 写回的字节与真件不同"


# --------------------------------------------------------------------------- #
# ⑤ 负控：池子
# --------------------------------------------------------------------------- #
def test_without_ref_types_the_error_names_the_pool(nodes, pool) -> None:
    """负控：不给池子必须抛，且**点名 ref_types** —— 不许退回老症状。"""
    data = _payload(SMALLEST)
    for empty in (None, {}):
        with pytest.raises(AssetError) as excinfo:
            cl.read_object(data, nodes, ref_types=empty)
        message = str(excinfo.value)
        assert "ref_types" in message and "SerializeReference" in message
        assert "认不得的字段" not in message, "退回老症状了（修前就是这条）"
    fields = cl.read_object(data, nodes, ref_types=pool)
    with pytest.raises(AssetError) as excinfo:
        cl.write_object(fields, nodes)  # 写侧同一个洞
    assert "ref_types" in str(excinfo.value)


def test_pool_missing_that_triple_goes_red(nodes, pool) -> None:
    """负控：池子里少那一族 -> 抛，且把那个三元组原样报出来。"""
    data = _payload(SMALLEST)
    thin = {k: v for k, v in pool.items() if k != AXIS_KEY}
    with pytest.raises(AssetError) as excinfo:
        cl.read_object(data, nodes, ref_types=thin)
    assert "没有这个三元组" in str(excinfo.value)
    assert "AxisRandom" in str(excinfo.value)


def test_pool_entry_without_a_tree_goes_red(nodes, pool) -> None:
    """负控：那一族在池子里但没有类型树（stripped 项的形状）-> 抛。"""
    hollow = dict(pool)
    hollow[AXIS_KEY] = ()
    with pytest.raises(AssetError) as excinfo:
        cl.read_object(_payload(SMALLEST), nodes, ref_types=hollow)
    assert "没有类型树" in str(excinfo.value)


def test_the_match_is_by_name_not_by_index(nodes, pool) -> None:
    """负控：把对象字节里那一族的类名改一个字符 -> 必须红（证明**按名字**匹配）。"""
    data = bytearray(_payload(SMALLEST))
    at = bytes(data).find(b"AxisRandom")
    assert at > 0, "夹具里找不到类名，负控无效"
    data[at] = ord("B")
    with pytest.raises(AssetError) as excinfo:
        cl.read_object(bytes(data), nodes, ref_types=pool)
    assert "BxisRandom" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# ⑥ 合成类型树（真件 0 例的那两条分支）
# --------------------------------------------------------------------------- #
def _synthetic_blob(rows: list[tuple[int, str, str, int]]) -> bytes:
    """按 (level, type, name, meta_flag) 拼一段内嵌类型树 blob（字段名走字符串池）。"""
    strings = bytearray()
    offsets: dict[str, int] = {}
    body = bytearray()
    for level, typ, name, meta in rows:
        for text in (typ, name):
            if text not in offsets:
                offsets[text] = len(strings)
                strings += text.encode("utf-8") + b"\x00"
        body += NODE_STRUCT.pack(19, level, 0, offsets[typ], offsets[name], 0, 0, meta, 0)
    return struct.pack("<ii", len(rows), len(strings)) + bytes(body) + bytes(strings)


def _cstr(text: str) -> bytes:
    """Unity 的 string 布局：i32 字节数 + 字节 + 补到 4（类型树里 string 底下就是它）。"""
    raw = text.encode("utf-8")
    return struct.pack("<i", len(raw)) + raw + b"\x00" * ((-len(raw)) % 4)


#: 合成的一棵 ReferencedObject 树（字段名/顺序/meta_flag 抄真件那棵，只把根换成引用本体）
_REF_ROWS: list[tuple[int, str, str, int]] = [
    (0, "ReferencedObject", "ref", 0x8001),
    (1, "SInt64", "rid", 0x1),
    (1, "ReferencedManagedType", "type", 0x208001),
    (2, "string", "class", 0x208001),
    (3, "Array", "Array", 0x204001),
    (4, "int", "size", 0x200001),
    (5, "char", "data", 0x200001),
    (2, "string", "ns", 0x208001),
    (3, "Array", "Array", 0x204001),
    (4, "int", "size", 0x200001),
    (5, "char", "data", 0x200001),
    (2, "string", "asm", 0x208001),
    (3, "Array", "Array", 0x204001),
    (4, "int", "size", 0x200001),
    (5, "char", "data", 0x200001),
    (1, "ReferencedObjectData", "data", 0x1),
]


def _synthetic_tree(rows: list[tuple[int, str, str, int]] = _REF_ROWS) -> tuple[cl.TypeNode, ...]:
    return cl.read_type_nodes(_synthetic_blob(rows), common_strings=COMMON)


def test_empty_reference_costs_no_byte() -> None:
    """空引用（class 为空串）：**一个字节都不占**、也不查池子（真件 0 例，合成树覆盖）。"""
    tree = _synthetic_tree()
    payload = struct.pack("<q", 0) + struct.pack("<iii", 0, 0, 0)
    fields = cl.read_object(payload, tree)
    assert fields["rid"] == 0
    assert fields["type"] == {"class": "", "ns": "", "asm": ""}
    assert "data" not in fields, "空引用不该有 data 这一格"
    assert cl.write_object(fields, tree) == payload
    assert cl.read_object(payload, tree, ref_types={}) == fields
    # 正控：同一段字节，把 class 换成非空（池子空）-> 必须红
    loud = struct.pack("<q", 0) + _cstr("Foo1") + struct.pack("<ii", 0, 0)
    with pytest.raises(AssetError) as excinfo:
        cl.read_object(loud, tree, ref_types={})
    assert "ref_types" in str(excinfo.value)


def test_the_pool_decides_the_layout_of_data(nodes, pool) -> None:
    """合成树 + 改名的一族：池子里给哪棵树，data 就按哪棵树解（**布局由池子决定**）。"""
    tree = _synthetic_tree()
    axis_nodes = pool[AXIS_KEY]
    real = cl.read_object(_payload(SMALLEST), nodes, ref_types=pool)
    inner = real["references"]["RefIds"][0]
    data_bytes = cl.write_object(inner["data"], axis_nodes)
    payload = (
        struct.pack("<q", int(inner["rid"]))
        + _cstr("Foo1")
        + _cstr("")
        + _cstr("")
        + data_bytes
    )
    renamed = {("Foo1", "", ""): axis_nodes}
    fields = cl.read_object(payload, tree, ref_types=renamed)
    assert fields["type"] == {"class": "Foo1", "ns": "", "asm": ""}
    assert fields["data"] == inner["data"]
    assert cl.write_object(fields, tree, ref_types=renamed) == payload


def test_data_before_type_is_rejected() -> None:
    """负控：把 data 挪到 type 之前 —— 布局就解不出来了，必须硬报错（不许静默）。"""
    rows = [_REF_ROWS[0], _REF_ROWS[-1], *_REF_ROWS[1:-1]]
    tree = _synthetic_tree(rows)
    payload = struct.pack("<q", 0) + struct.pack("<iii", 0, 0, 0)
    with pytest.raises(AssetError) as excinfo:
        cl.read_object(payload, tree, ref_types={})
    assert "type" in str(excinfo.value)


def test_writer_refuses_data_on_an_empty_reference() -> None:
    """负控：空引用却给了 data -> 不许静默丢字节。"""
    tree = _synthetic_tree()
    value = {"rid": 0, "type": {"class": "", "ns": "", "asm": ""}, "data": {"_x": 1.0}}
    with pytest.raises(AssetError) as excinfo:
        cl.write_object(value, tree)
    assert "空引用" in str(excinfo.value)
