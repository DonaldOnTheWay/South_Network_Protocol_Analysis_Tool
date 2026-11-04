# -*- coding: utf-8 -*-
"""档案页「抄读数据项」配置与内层报文构造（纯逻辑，零 Qt）。
背景
----
协议7 档案页的「点抄测试 / 并发抄表」以前把抄读内容写死成「正向有功总电能」，
现场需要的是**可配置的数据项**（如从节点版本信息、信号强度、冻结电量等）。
数据项分三类（`proto`）：
- ``645``    —— 645 表的数据项，标识是 **DI**（如 ``00010000`` 正向有功总电能）
- ``698``    —— 698 表的数据项，标识是 **OAD**（如 ``00100200`` 正向有功电能）
- ``module`` —— **STA 模块自身**的数据项（模块自动应答，不用电表参与），
  标识是 645 扩展 **DI**（``EB03xxxx``）；对 698 表按国网《附件1 V3.42》
  「所有 376.2 协议应能支持通过 698 OAD `EB55XXXX` 进行映射操作」映射成 OAD。
因此一个数据项会带两份标识：``di``（645 用）与 ``oad``（698 用）。
缺哪一份就跳过该表并给出原因，不猜。
组帧
----
- 645 表：``68 <地址6B 低字节在前> 68 11 04 <DI 4B 逆序 +0x33> <CS> 16``，
  一个 DI 一帧（645 读数据只支持单个数据标识）。
- 698 表：把所有 698 项（含映射后的 module 项）打进**一帧**
  ``GetRequestNormalList`` = ``05 02 <PIID=01> <count> <OAD×n> 00``（时间标签=无）。
  帧结构与 HCS/FCS 口径彻底复用 `gdw_meter_request.build_698_read_total_active`
  的现场实测口径（HCS 用「不含时间标签」的长度算，FCS 用真实长度）。
"""
from __future__ import annotations
import json
from typing import Dict, Iterable, List, Optional, Tuple
from app_paths import data_path
from gdw_meter_request import _addr_bytes
# ── 数据项归属 ───────────────────────────────────────────────────────────
PROTO_645 = "645"
PROTO_698 = "698"
PROTO_MODULE = "module"
PROTO_LABELS = {
    PROTO_645: "645 表数据项",
    PROTO_698: "698 表数据项",
    PROTO_MODULE: "STA 模块数据项（模块自动应答）",
}
#: 645 扩展 DI → 698 映射 OAD 的前两字节（国网《附件1 V3.42》：
#: 「所有 376.2 协议应能支持通过 698 OAD「EB55XXXX」进行映射操作」）
MODULE_OAD_PREFIX = "EB55"
#: 数据项种类
KIND_READ = ""                    # 普通读（645 读 DI / 698 读 OAD）
KIND_MODULE_VERSION = "module_version"   # 公司扩展协议：查从节点详细版本（不区分表协议）
KIND_MODULE_QUERY = "module_query"       # 公司扩展协议：自定义条目清单（不区分表协议）
#: 「从节点版本信息」= 查询从节点详细版本用的公司扩展报文：LME 4.6（DI=FF00DD01）
#: 查询条目列表（分类/数据ID），与现场参考报文的 27 条目逐项对齐。
MODULE_VERSION_DI = "FF00DD01"
MODULE_VERSION_ENTRIES = (
    [(0, i) for i in (1, 2, 3, 4, 5, 6, 9)]
    + [(1, i) for i in range(1, 15)]
    + [(1, i) for i in (20, 21, 22, 23, 24, 30)]
)
#: 「通信信道信号强度」的条目清单 —— 取自现场实测下发帧（2026-11-04 日志）：
#: 与版本信息清单同源，另加 分类2/数据22「载波接收信息」（应答 PLC:下行/上行），
#: 以及分类0 的 7/8/11~25、分类1 的 15/16、分类2 的 5。共 48 条。
MODULE_CHANNEL_ENTRIES = (
    [(0, i) for i in (1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 12, 13, 14, 15, 16,
                      17, 18, 19, 20, 21, 22, 23, 24, 25)]
    + [(1, i) for i in range(1, 17)]
    + [(1, i) for i in (20, 21, 22, 23, 24, 30)]
    + [(2, 5), (2, 22)]
)
def module_di_to_oad(di: str) -> str:
    """645 扩展 DI（EB03xx xx）→ 698 映射 OAD（EB55 + DI1 + DI0）。
    例：``EB030302``（通信模块所在相位）→ ``EB550302``。
    仅用于**其它模块数据项**的备查；「从节点版本信息」按用户口径走公司扩展协议，
    不区分表协议（见 `build_module_version_frame`）。
    """
    text = str(di or "").strip().upper()
    if len(text) != 8:
        return ""
    return MODULE_OAD_PREFIX + text[4:8]
def build_module_version_frame(addr: str) -> bytes:
    """「从节点版本信息」内层报文 —— 与「查询从节点详细版本」完全同一份。
    这是**公司扩展协议**（LME 4.6 查询，DI=``FF00DD01``），由 STA 模块自己应答，
    所以**不区分电能表是 645 还是 698**，一律按 645 读数据帧下发（1376.2 通信协议类型=2）。
    """
    return build_module_query_frame(addr, MODULE_VERSION_ENTRIES)
def build_module_query_frame(addr: str, entries, pad_to: int = 0) -> bytes:
    """公司扩展协议查询帧（DI=``FF00DD01`` + 自定义条目清单），不区分表协议。
    帧结构：``68 <地址6B 低字节在前> 68 11 <L> <DI + 条目信息 + M + M×2B 头> <CS> 16``
    数据域整体 +0x33 加扰。``entries`` 为 ``[(分类ID, 数据ID), ...]``。
    ``pad_to`` > 0 时把数据域用 ``0x00`` 补齐到该长度（现场工具的固定缓冲区写法，
    见 2026-11-04 「通信信道信号强度」日志：数据域 240 字节、尾部补 138 个 0）。
    默认 0 = 不填充，按协议最短帧下发。
    """
    data_field = _module_query_data_field(entries)
    if pad_to and len(data_field) < pad_to:
        data_field = data_field + b"\x00" * (pad_to - len(data_field))
    head = b"\x68" + _addr_bytes(addr) + b"\x68\x11" + bytes([len(data_field)]) + data_field
    return head + bytes([sum(head) & 0xFF, 0x16])
def _module_query_data_field(entries) -> bytes:
    """FF00DD01 查询数据域（含 DI，整体 +0x33）"""
    from lme_info_entry_parser import pack_lme_query_request
    decoded = bytes.fromhex(MODULE_VERSION_DI)[::-1] + pack_lme_query_request(list(entries))
    return bytes((b + 0x33) & 0xFF for b in decoded)
def _module_version_data_field() -> bytes:
    """兼容旧调用：版本信息项的查询数据域"""
    return _module_query_data_field(MODULE_VERSION_ENTRIES)
# ── 内置数据项（全部取自协议原文，见各条 comment）────────────────────────
BUILTIN_ITEMS: List[Dict[str, str]] = [
    # ===== STA 模块数据项（国网《附件1 V3.42》表：通信模块功能扩展定义 EB03xxxx）=====
    {
        "key": "sta_version", "name": "从节点版本信息", "proto": PROTO_MODULE,
        "kind": KIND_MODULE_VERSION, "di": MODULE_VERSION_DI, "enabled": "1",
        "note": "与「查询从节点详细版本」同一份公司扩展协议报文（LME 4.6 查询 DI=FF00DD01），"
                "STA 模块自动应答，**不区分表协议**（645/698 表都按 645 帧下发）",
    },
    {
        "key": "sta_channel_strength", "name": "通信信道信号强度", "proto": PROTO_MODULE,
        "kind": KIND_MODULE_QUERY, "di": MODULE_VERSION_DI, "enabled": "0",
        "entries": [list(e) for e in MODULE_CHANNEL_ENTRIES],
        "note": "现场实测口径（2026-11-04 日志）：同为 FF00DD01 查询，条目清单 48 条，"
                "含 分类2/数据22「载波接收信息」（应答 PLC:下行/上行）；模块自动应答，不区分表协议",
    },
    {
        "key": "sta_phase", "name": "从节点所在相位", "proto": PROTO_MODULE,
        "di": "EB030302", "enabled": "0",
        "note": "通信模块所在相位（EB030302，0未知/1A相/2B相/3C相），模块自动应答",
    },
    {
        "key": "sta_temp", "name": "模块环境温度", "proto": PROTO_MODULE,
        "di": "EB030311", "enabled": "0",
        "note": "环境温度（EB030311，单位 ℃），模块自动应答；不是「温度校准参数」本身",
    },
    {
        "key": "sta_volt", "name": "模块载波接口电压", "proto": PROTO_MODULE,
        "di": "EB030310", "enabled": "0",
        "note": "从节点模块载波接口电压（EB030310，A/B/C 三相），模块自动应答",
    },
    {
        "key": "sta_signal_self", "name": "周边节点信号质量（本台区）", "proto": PROTO_MODULE,
        "di": "EB030313", "enabled": "0",
        "note": "周边节点信号通信质量_本台区（EB030313：通信成功率/SMR/RSSI），模块自动应答",
    },
    {
        "key": "sta_signal_other", "name": "周边节点信号质量（非本台区）", "proto": PROTO_MODULE,
        "di": "EB030314", "enabled": "0",
        "note": "周边节点信号通信质量_非本台区（EB030314：SNID/TEI/SNR/RSSI），模块自动应答",
    },
    # ===== 645 表数据项（DL/T 645-2007 + 国网补遗）=====
    {
        "key": "645_total_active", "name": "正向有功总电能", "proto": PROTO_645,
        "di": "00010000", "enabled": "1", "note": "DI 00010000 当前正向有功总电能",
    },
    {
        "key": "645_total_reverse", "name": "反向有功总电能", "proto": PROTO_645,
        "di": "00020000", "enabled": "0", "note": "DI 00020000 当前反向有功总电能",
    },
    {
        "key": "645_freeze_reverse_1d", "name": "上1次定时冻结反向有功", "proto": PROTO_645,
        "di": "05000201", "enabled": "0",
        "note": "DI 05000201（上1次）定时冻结反向有功电能数据（645 附录：DI3=05 DI2=00 DI1=02 DI0=01）",
    },
    # ===== 698 表数据项（DL/T 698.45 标准 OAD）=====
    {
        "key": "698_total_active", "name": "正向有功电能", "proto": PROTO_698,
        "di": "", "oad": "00100200", "enabled": "1",
        "note": "OAD 00100200 电能量类·总及费率电能量数组（正向有功电能）",
    },
    {
        "key": "698_total_reverse", "name": "反向有功电能", "proto": PROTO_698,
        "di": "", "oad": "00200200", "enabled": "0",
        "note": "OAD 00200200 电能量类·反向有功电能",
    },
    {
        "key": "698_geo", "name": "设备地理位置", "proto": PROTO_698,
        "di": "", "oad": "40040200", "enabled": "0",
        "note": "OAD 40040200 参数变量类·设备地理位置（OI=4004）",
    },
]
def _default_path() -> str:
    return data_path("archive_copy_items.json")
def normalize_item(raw: Dict) -> Optional[Dict[str, str]]:
    """校验/规整一条数据项；不合法返回 None（导入时不因一条坏项整份失败）"""
    if not isinstance(raw, dict):
        return None
    # 旧版配置迁移：v1.26.22 之前把「从节点版本信息」存成 645 扩展 DI EB030303，
    # 按用户口径它其实是公司扩展协议（与「查询从节点详细版本」同一份报文 FF00DD01），
    # 不区分表协议 —— 读到旧值就地升级，避免老配置继续按错误标识下发。
    if (str(raw.get("di") or "").strip().upper() == "EB030303"
            and "版本" in str(raw.get("name") or "")):
        raw = dict(raw)
        raw["kind"] = KIND_MODULE_VERSION
        raw["di"] = MODULE_VERSION_DI
        raw["note"] = ("与「查询从节点详细版本」同一份公司扩展协议报文"
                       "（LME 4.6 查询 DI=FF00DD01），STA 模块自动应答，不区分表协议")
    name = str(raw.get("name") or "").strip()
    key = str(raw.get("key") or name).strip()
    proto = str(raw.get("proto") or "").strip()
    kind = str(raw.get("kind") or "").strip()
    # 公司扩展协议项（版本信息 / 模块查询）没有 DI 要求，直接走专用分支
    if kind in (KIND_MODULE_VERSION, KIND_MODULE_QUERY):
        proto = PROTO_MODULE
    if proto not in (PROTO_645, PROTO_698, PROTO_MODULE):
        # 兼容：没写 proto 就按标识长度猜
        di = str(raw.get("di") or "").strip().upper()
        oad = str(raw.get("oad") or "").strip().upper()
        if oad and not di:
            proto = PROTO_698
        elif di.upper().startswith("EB"):
            proto = PROTO_MODULE
        else:
            proto = PROTO_645
    di = "".join(ch for ch in str(raw.get("di") or "").upper() if ch in "0123456789ABCDEF")
    oad = "".join(ch for ch in str(raw.get("oad") or "").upper() if ch in "0123456789ABCDEF")
    if proto == PROTO_698:
        if len(oad) != 8:
            return None
        di = ""
    elif proto == PROTO_MODULE:
        if kind not in (KIND_MODULE_VERSION, KIND_MODULE_QUERY):
            if len(di) != 8:
                return None
            oad = module_di_to_oad(di)
    else:
        if len(di) != 8:
            return None
        oad = ""
    if not name:
        name = di or oad
    enabled = raw.get("enabled", "1")
    if kind in (KIND_MODULE_VERSION, KIND_MODULE_QUERY):
        # 公司扩展协议：固定 DI=FF00DD01，不区分表协议，也没有 698 映射
        if kind == KIND_MODULE_VERSION:
            entries = [list(e) for e in MODULE_VERSION_ENTRIES]
        else:
            raw_entries = raw.get("entries")
            entries = []
            for e in (raw_entries or []):
                try:
                    cat, did = int(e[0]), int(e[1])
                except (TypeError, ValueError, IndexError):
                    continue
                if 0 <= cat <= 7 and 0 <= did <= 0x3F:
                    entries.append([cat, did])
            if not entries:
                entries = [list(e) for e in MODULE_CHANNEL_ENTRIES]
        return {
            "key": key or name,
            "name": name or ("从节点版本信息" if kind == KIND_MODULE_VERSION
                             else "模块查询项"),
            "proto": PROTO_MODULE,
            "kind": kind,
            "di": MODULE_VERSION_DI,
            "oad": "",
            "entries": entries,
            "pad_to": int(raw.get("pad_to") or 0),
            "enabled": "1" if str(enabled) in ("1", "True", "true") else "0",
            "note": str(raw.get("note") or ""),
        }
    return {
        "key": key or name,
        "name": name,
        "proto": proto,
        "kind": KIND_READ,
        "di": di,
        "oad": oad,
        "enabled": "1" if str(enabled) in ("1", "True", "true") else "0",
        "note": str(raw.get("note") or ""),
    }
def load_items(path: Optional[str] = None) -> List[Dict[str, str]]:
    """读取数据项配置；文件不存在/损坏时回退内置库

    **内置新增项自动补齐**：老配置里没有的内置项（按 ``key`` 判定）会被追加到末尾、
    保持 ``enabled=0`` 不擅自勾选 —— 否则升级后用户看不到新加的数据项。
    """
    p = path or _default_path()
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        raw_items = data.get("items") if isinstance(data, dict) else data
        if not isinstance(raw_items, list) or not raw_items:
            raise ValueError("empty")
        out = [normalize_item(it) for it in raw_items]
        out = [it for it in out if it]
        if not out:
            raise ValueError("empty")
        have = {it["key"] for it in out}
        for raw in BUILTIN_ITEMS:
            builtin = normalize_item(dict(raw))
            if builtin and builtin["key"] not in have:
                builtin["enabled"] = "0"      # 新内置项不擅自勾选
                builtin["note"] = (builtin.get("note", "")
                                   + "（本版新增，未自动勾选）")
                out.append(builtin)
                have.add(builtin["key"])
        return out
    except Exception:
        return [normalize_item(dict(it)) for it in BUILTIN_ITEMS]
def save_items(items: Iterable[Dict], path: Optional[str] = None) -> str:
    """写回数据项配置（原子写）"""
    p = path or _default_path()
    clean = [normalize_item(it) for it in items]
    clean = [it for it in clean if it]
    payload = {
        "description": "协议7 档案页「抄读数据项」配置（点抄/并发抄表使用）",
        "version": "1.0.0",
        "items": clean,
    }
    from atomic_io import atomic_write_json
    atomic_write_json(p, payload)
    return p
def enabled_items(items: Optional[List[Dict[str, str]]] = None) -> List[Dict[str, str]]:
    """已勾选的数据项"""
    src = items if items is not None else load_items()
    return [it for it in src if str(it.get("enabled", "1")) == "1"]
# ── 内层报文构造 ─────────────────────────────────────────────────────────
def build_645_read(addr: str, di: str) -> bytes:
    """645-2007 读数据帧（DI 低字节在前 +0x33 加扰）"""
    di_bytes = bytes.fromhex(di)[::-1]
    scrambled = bytes((b + 0x33) & 0xFF for b in di_bytes)
    head = b"\x68" + _addr_bytes(addr) + b"\x68\x11\x04" + scrambled
    return head + bytes([sum(head) & 0xFF, 0x16])
def build_698_get_list(addr: str, oads: List[str]) -> bytes:
    """698.45 GET 请求（可带多个 OAD），口径同现场实测报文。
    - **1 个 OAD** 用 ``GetRequestNormal``：``05 01 01 <OAD> 00``
      —— 与现场实测报文逐字节一致（`build_698_read_total_active` 同款）；
    - **多个 OAD** 用 ``GetRequestNormalList``：``05 02 01 <count> <OAD×n> 00``
      （见 `dl_t698_45_apdu_parser` 的 GET-Request 分支，choice=2 后为 PIID + SEQUENCE OF OAD）。
    公共口径（现场反推）：帧头 ``68 L C SA CA HCS``、帧尾 ``FCS 16``；
    **HCS 按「不含时间标签」的长度算**，**FCS 用真实长度**；
    必须有「时间标签 = 无」的 ``00``，否则被测 CCO 认为报文不完整而不抄表。
    """
    import crcmod.predefined
    import struct
    oad_list = [o.strip().upper() for o in oads if len(str(o).strip()) == 8]
    if not oad_list:
        raise ValueError("至少需要一个 OAD")
    crc = crcmod.predefined.Crc('x-25')
    sa = bytes([0x05]) + _addr_bytes(addr)
    ca = 0
    control = 0x43
    if len(oad_list) == 1:
        apdu = bytes([0x05, 0x01, 0x01]) + bytes.fromhex(oad_list[0]) + b"\x00"
    else:
        apdu = (bytes([0x05, 0x02, 0x01, len(oad_list)])
                + b"".join(bytes.fromhex(o) for o in oad_list) + b"\x00")
    length = 2 + 1 + len(sa) + 1 + 2 + len(apdu) + 2
    hcs_len = length - 1                     # 现场口径：HCS 用不含时间标签的长度
    hcs = struct.pack('<H', crc.new(
        struct.pack('<H', hcs_len) + bytes([control]) + sa + bytes([ca])).crcValue)
    fcs = struct.pack('<H', crc.new(
        struct.pack('<H', length) + bytes([control]) + sa + bytes([ca])
        + hcs + apdu).crcValue)
    return (b"\x68" + struct.pack('<H', length) + bytes([control]) + sa
            + bytes([ca]) + hcs + apdu + fcs + b"\x16")
def frames_for_meter(addr: str, table_is_698: bool,
                     items: Optional[List[Dict[str, str]]] = None
                     ) -> Tuple[List[Tuple[str, bytes, int]], List[str]]:
    """按表协议与勾选项生成内层报文。
    返回 ``(frames, skipped)``：
    - ``frames``：``[(数据项名称, 内层报文, 1376.2 通信协议类型), ...]``
      645 表每个 645/module 项一帧；698 表把 698/module 项合成一帧。
    - ``skipped``：无法用于该表的数据项名称（例如给 645 表勾了纯 698 项）。
    **「从节点版本信息」（`KIND_MODULE_VERSION`）不区分表协议**：它是公司扩展协议，
    由 STA 模块自己应答，645 表与 698 表都按 645 帧（通信协议类型=2）单独下发。
    """
    chosen = enabled_items(items)
    frames: List[Tuple[str, bytes, int]] = []
    skipped: List[str] = []
    # 1) 公司扩展协议项：不分表协议，一律 645 帧、单独成帧
    for it in chosen:
        kind = it.get("kind")
        if kind == KIND_MODULE_VERSION:
            frames.append((f"{it['name']}({MODULE_VERSION_DI})",
                           build_module_version_frame(addr), 2))
        elif kind == KIND_MODULE_QUERY:
            entries = [tuple(e) for e in (it.get("entries") or MODULE_CHANNEL_ENTRIES)]
            pad = int(it.get("pad_to") or 0)
            frames.append((f"{it['name']}({MODULE_VERSION_DI}/{len(entries)}条)",
                           build_module_query_frame(addr, entries, pad), 2))
    def _is_special(it) -> bool:
        return it.get("kind") in (KIND_MODULE_VERSION, KIND_MODULE_QUERY)
    # 2) 其余项按表协议归集
    if table_is_698:
        oads: List[str] = []
        names: List[str] = []
        for it in chosen:
            if _is_special(it):
                continue
            if it["proto"] == PROTO_698:
                oad = it.get("oad") or ""
                if len(oad) != 8:
                    skipped.append(it["name"])
                    continue
                oads.append(oad)
                names.append(f"{it['name']}({oad})")
            elif it["proto"] == PROTO_MODULE:
                # 其它模块项是 645 扩展 DI，只能走 645 帧，塞不进 698 帧
                di = it.get("di") or ""
                if len(di) != 8:
                    skipped.append(it["name"])
                    continue
                frames.append((f"{it['name']}({di})", build_645_read(addr, di), 2))
            else:
                skipped.append(it["name"])
        if oads:
            frames.insert(0, (" + ".join(names), build_698_get_list(addr, oads), 3))
    else:
        for it in chosen:
            if _is_special(it):
                continue
            if it["proto"] in (PROTO_645, PROTO_MODULE):
                di = it.get("di") or ""
                if len(di) != 8:
                    skipped.append(it["name"])
                    continue
                frames.append((f"{it['name']}({di})", build_645_read(addr, di), 2))
            else:
                skipped.append(it["name"])
    return frames, skipped
def first_item_for(item_list: Optional[List[Dict[str, str]]] = None) -> Optional[Dict[str, str]]:
    """取第一个已勾选项（并发抄表每表只能带一个报文内容，用它对位）"""
    chosen = enabled_items(item_list)
    return chosen[0] if chosen else None
