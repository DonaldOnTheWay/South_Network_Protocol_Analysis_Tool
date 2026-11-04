# -*- coding: utf-8 -*-
"""国网（协议7）流程测试 —— **文档补充用例 + 负面用例**（纯逻辑，零 Qt）。

为什么单独一个文件
------------------
`gdw_test_flows.FLOW_CASES` 是 xlsx 第 70~78 行那 9 条**互换性一致性用例**，
本模块放的是两类**性质不同**的用例：

1. **文档补充用例**：`关于能源控制器.docx` 里写了、但 xlsx 那 9 条没覆盖的场景
   （宽带载波通信参数查询/设置、事件上报（645 表）、停电上报（698 表）、宽带并发抄表）。
2. **负面/异常用例**：故意下发非法帧，验证设备回**正确的否认码**而不是静默确认。

分开理由：内置那 9 条有「顺序与 xlsx 一致」的测试断言钉着，往 `FLOW_CASES` 里追加
会把它们挤乱；本模块的用例没有顺序契约，随时可增删。

依赖方向
--------
本模块顶层 ``import gdw_test_flows``（构造 `FlowStep` / `FlowCase` 要用它的类）。
因此 `gdw_test_flows` **只能函数级** import 本模块（见其 `_extra_cases()`），
模块级互导会成环。
"""

from __future__ import annotations

from typing import List

import gdw_test_flows as flows
from gdw_test_flows import (
    CHECK_ACK,
    CHECK_BAND,
    CHECK_BAND_ECHO,
    CHECK_MASTER_ADDR,     # noqa: F401 - 供下方用例按需引用，保持同一导入口径
    CHECK_NODE_COUNT,      # noqa: F401
    CHECK_READ_MATCH,      # noqa: F401
    FlowCase,
    FlowStep,
    KIND_EXPECT_STA,
    KIND_MANUAL,
    KIND_NOTE,
    KIND_SEND,
    STA_DOMAIN_645,
    STA_DOMAIN_698,
)


# ── 1. 宽带载波通信参数（查询 / 设置 / 回读）──────────────────────────────
def _broadband_params() -> FlowCase:
    return FlowCase(
        "broadband_params", "宽带载波通信参数（查询/设置/回读）",
        "word 2.2.7：先查当前宽带载波频段（判频段有效）→ 设置为组网参数的频段"
        "（等确认）→ 再回读，回读值必须与设置值一致。\n"
        "频段取值 0/1/2/3 分别对应 1.953~11.96 / 2.441~5.615 / 0.781~2.930 / "
        "1.758~2.930MHz（表 05H F16 枚举）。\n"
        "设置命令的「宽带载波频段」由「组网参数」的载波频段自动填（手改优先）。",
        builtin=False,
        source="word 2.2.7 设置/查询宽带载波通信参数（表 05H F16 / 03H F16）",
        steps=[
            FlowStep(KIND_SEND, "查询宽带载波通信参数 (03H F16)", key="03-16",
                     timeout=180.0, check=CHECK_BAND,
                     hint="应答里「宽带载波频段」必须落在 0~3（文档超时 3min）"),
            FlowStep(KIND_SEND, "设置宽带载波通信参数 (05H F16)：频段取组网参数",
                     key="05-16", timeout=180.0, check=CHECK_ACK,
                     hint="期望确认帧；否认时按错误状态字判定，不会当成设置成功"),
            FlowStep(KIND_SEND, "回读宽带载波通信参数 (03H F16)：核对是否生效",
                     key="03-16", timeout=180.0, check=CHECK_BAND_ECHO,
                     hint="回读频段必须等于设置值；不等说明模块没接受该设置"),
        ],
    )


# ── 2. 事件上报（645 表，STA 侧观测）─────────────────────────────────────
def _event_645() -> FlowCase:
    return FlowCase(
        "event_645", "事件上报测试（645 表，STA 侧观测）",
        "word「事件上报测试（645）」：让电能表（645 虚拟表）产生主动上报事件，"
        "验证 CCO 会①抄读事件记录、②复位主动上报状态字、③回读状态字。\n"
        "本工具的串口只有「能源控制器侧」与「从节点侧」两条总线：CCO 发给 STA 的"
        "645 帧只能在**从节点串口**上看到，所以本用例的判定方式是"
        "「启动从节点模拟 → 在 STA 域观测 CCO 下发的 645 帧」。\n"
        "⚙ 需要从节点模拟配合：第 1 步按提示用「向从节点发帧」发原始上报帧。",
        builtin=False,
        source="word 事件上报测试（645 表：DI 04001501 主动上报状态字）",
        steps=[
            FlowStep(KIND_MANUAL,
                     "让 645 虚拟表主动上报「电能表主动上报状态字」(DI 04001501)："
                     "用上方「向从节点发帧」发原始帧",
                     timeout=60.0, needs_slave=True,
                     hint="没有现成的组帧器造这条主动上报帧，故按人工步骤处理；"
                          "发完模块会自己往上跑，无需人工确认（本步超时按跳过）"),
            FlowStep(KIND_EXPECT_STA,
                     "CCO 抄读电能表事件记录（645 控制码 11H，DI 前缀 0301）",
                     timeout=90.0, needs_slave=True,
                     sta_filter={"域": STA_DOMAIN_645, "控制码": 0x11, "DI": "0301"},
                     hint="在从节点串口上应看到 CCO 下发 645 读事件记录帧"),
            FlowStep(KIND_EXPECT_STA,
                     "CCO 复位主动上报状态字（645 控制码 14H，DI 04001503）",
                     timeout=60.0, needs_slave=True,
                     sta_filter={"域": STA_DOMAIN_645, "控制码": 0x14, "DI": "04001503"},
                     hint="事件被抄走后 CCO 会写 04001503 复位状态字"),
            FlowStep(KIND_EXPECT_STA,
                     "CCO 回读主动上报状态字（645 控制码 11H，DI 04001501）",
                     timeout=60.0, needs_slave=True,
                     sta_filter={"域": STA_DOMAIN_645, "控制码": 0x11, "DI": "04001501"},
                     hint="复位后 CCO 应回读状态字确认已清零"),
            FlowStep(KIND_MANUAL,
                     "主站侧确认 CCO 是否把事件上报主站（本工具无主站通道，人工判定）",
                     timeout=30.0,
                     hint="本工具不含 698.45 主站通道，这一步只能人工看主站侧记录"),
        ],
    )


# ── 3. 停电上报（698 表，STA 侧观测）─────────────────────────────────────
def _outage_698() -> FlowCase:
    return FlowCase(
        "outage_698", "停电上报测试（698 表，STA 侧观测）",
        "word「停电上报测试」：698.45 虚拟 STA 发起停电事件上报（REPORT-Request），"
        "CCO 必须回 REPORT-Response（APDU 0x88），并把停电事件上报主站。\n"
        "本用例在**从节点串口**（STA 域）观测 CCO 的 REPORT-Response。\n"
        "⚙ 需要从节点模拟配合：第 1 步用「向从节点发帧」发停电上报原始帧。",
        builtin=False,
        source="word 停电上报测试（698.45 REPORT-Request / REPORT-Response）",
        steps=[
            FlowStep(KIND_MANUAL,
                     "让 698 虚拟 STA 发起停电上报帧：用上方「向从节点发帧」发原始 698 帧",
                     timeout=60.0, needs_slave=True,
                     hint="没有现成的停电上报组帧器，故按人工步骤处理"),
            FlowStep(KIND_EXPECT_STA,
                     "CCO 回 REPORT-Response（698 APDU 0x88）",
                     timeout=60.0, needs_slave=True,
                     sta_filter={"域": STA_DOMAIN_698, "APDU": 0x88},
                     hint="收到上报后 CCO 必须回 REPORT-Response；不回即判失败"),
            FlowStep(KIND_MANUAL,
                     "主站侧确认停电事件已上报（本工具无主站通道，人工判定）",
                     timeout=30.0),
        ],
    )


# ── 4. 宽带并发抄表（F1H F1 连发，测最大并发数）──────────────────────────
def _broadband_concurrent() -> FlowCase:
    addrs = flows.default_addrs(flows.CONCURRENT_NODES)
    first, last = addrs[0], addrs[-1]
    return FlowCase(
        "broadband_concurrent", "宽带并发抄表（F1H F1 连发测最大并发数）",
        f"用户口径（2026-11-02 纠正）：并发抄表用 **`F1H F1 集中器主动并发抄表`**（不是 `13H F1`）。\n"
        f"步骤：① 参数区初始化清旧档案 → ② 用 `11H F1` 把 {len(addrs)} 个从节点地址"
        f"（{first}~{last}，10 个一批）全部建档 → ③ 用 `F1H F1` 按地址**逐帧连发、"
        f"不逐帧等应答**，直到被测 CCO 回**否认**。\n"
        f"判定：收到否认即停止发送，**「发到第几帧被否认」就是被测的最大并发数**；"
        f"否认码应为 **109（超过最大并发数）**，若是其它码（如 7 表号不存在 / 12 不在网内）"
        f"说明档案没建成或节点没入网，本用例判失败并点出该码。\n"
        f"若 {len(addrs)} 帧全部发完都没收到否认 → 判失败并写明「被测最大并发数 ≥ {len(addrs)}，"
        f"未测出上限」（把地址数调大再测，不要当成通过）。",
        builtin=False,
        source="word 宽带并发抄表 + 集中器本地通信模块接口-2024 §6.5.x（F1H F1）+ 表 10 错误状态字 109",
        steps=[
            FlowStep(KIND_SEND, "参数区初始化 (01H F2)：清空模块内已有档案", key="01-2",
                     timeout=10.0, alt_keys=("11-2",),
                     hint="不先初始化，随后 11H F1 会回「错误状态字=6 表号重复」"),
            FlowStep(KIND_SEND,
                     f"批量建档 (11H F1 ×{len(addrs) // 10} 帧，每帧 10 个地址)",
                     key="11-1", timeout=20.0,
                     addrs=addrs, batch=10,
                     hint="地址列由本步骤自带（与「组网参数」的 1~2 个节点无关）；"
                          "「从节点数量」由生成器按列表长度自动回填"),
            FlowStep(KIND_SEND,
                     f"并发连发 F1H F1 ×{len(addrs)}（连发不等应答，收到否认即停）",
                     key="F1-1", timeout=120.0,
                     addrs=addrs, batch=1, concurrent=True,
                     expect_denial=109,
                     hint="每帧一个地址、报文内容按该地址的 645/698 读正向有功总电能；"
                          "收到否认后立即停止发送并记录并发帧数"),
        ],
    )


# ── 5. 负面用例（设备必须回正确的否认码）────────────────────────────────
def _neg_len_error() -> FlowCase:
    return FlowCase(
        "neg_len_error", "负面用例：节点数量=0 → 数据单元非法",
        "故意把 10H F104「节点数量」填 0（协议 §6.5.8.3 要求 n ≥ 1，n=0 即非法数据单元），"
        "期望设备回**否认**（错误状态字 1 无效数据单元 / 2 长度错，按固件实现不同而异）。\n"
        "**若设备回确认而非否认，本用例判失败** —— 这是判定口径，不是缺陷："
        "非法数据单元被受理，说明该固件的入参校验不合规。",
        builtin=False,
        source="集中器本地通信模块接口-2024 §6.5.8.3（n ≥ 1）+ HPLC 远程升级方案 2.4.2(2)",
        steps=[
            FlowStep(KIND_NOTE,
                     "10H F104 的下行数据单元 = 节点起始序号(2B) + 节点数量(1B)，"
                     "文档要求 n ≥ 1；这里故意填 0"),
            FlowStep(KIND_SEND, "查询升级后模块版本信息 (10H F104)：节点数量故意填 0",
                     key="10-104", timeout=10.0,
                     values={"节点数量": 0},
                     expect_denial=(1, 2),
                     hint="预期否认：1（无效数据单元）或 2（长度错）"),
        ],
    )


def _neg_duplicate() -> FlowCase:
    return FlowCase(
        "neg_duplicate", "负面用例：重复建档 → 表号重复",
        "前置序列已经把组网参数里的从节点添加进档案；此时**再添加一次同样的表**，"
        "设备必须回否认「错误状态字=6 表号重复」。\n"
        "**若设备回确认（静默接受重复档案），本用例判失败** —— 判定口径，不是缺陷。",
        builtin=False,
        source="集中器本地通信模块接口-2024 §6.5.9.1（11H F1）+ 表 10 错误状态字 6",
        steps=flows.prerequisite_steps() + [
            FlowStep(KIND_NOTE,
                     "前置里的 11H F1 已按组网参数建档成功，所以下面这一帧必然是重复建档"),
            FlowStep(KIND_SEND, "再次添加从节点 (11H F1)：与已建档案完全相同",
                     key="11-1", timeout=10.0, split_nodes=True,
                     expect_denial=6,
                     hint="预期否认：6（表号重复）"),
        ],
    )


def _neg_no_table() -> FlowCase:
    return FlowCase(
        "neg_no_table", "负面用例：目标地址不存在 → 表号不存在",
        "把 04H F3「目标地址」填成组网参数里没有的 000000000000，"
        "设备必须回否认「错误状态字=7 表号不存在」。\n"
        "**若设备回确认或回数据，本用例判失败** —— 判定口径，不是缺陷。",
        builtin=False,
        source="集中器本地通信模块接口-2024 §6.5.5（04H F3）+ 表 10 错误状态字 7",
        steps=[
            FlowStep(KIND_SEND, "本地通信模块报文通信测试 (04H F3)：目标地址不存在",
                     key="04-3", timeout=15.0,
                     values={"目标地址": "000000000000"},
                     expect_denial=7,
                     hint="报文内容按从节点协议自动填（645/698 读帧），只把目标地址写错"),
        ],
    )


#: 文档补充用例 + 负面用例（顺序 = 界面树里的显示顺序）
EXTRA_CASES: List[FlowCase] = [
    _broadband_params(),
    _event_645(),
    _outage_698(),
    _broadband_concurrent(),
    _neg_len_error(),
    _neg_duplicate(),
    _neg_no_table(),
]

__all__ = [
    "EXTRA_CASES",
    "CHECK_ACK",
    "CHECK_BAND",
    "CHECK_BAND_ECHO",
    "CHECK_READ_MATCH",
    "KIND_EXPECT_STA",
    "KIND_MANUAL",
    "KIND_NOTE",
    "KIND_SEND",
    "STA_DOMAIN_645",
    "STA_DOMAIN_698",
]
