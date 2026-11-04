# -*- coding: utf-8 -*-
"""国网（协议7）流程测试 —— **用户自定义用例层**（纯逻辑，零 Qt）。

解决什么问题
------------
以前加/改用例只能改 `gdw_test_flows.FLOW_CASES`（代码）：现场想加一条自己的
遍历顺序，得找开发改代码、重新打包。这一层把用例搬到 JSON：

- 文件：`gdw_test_flow_custom.json`（`app_paths.data_path`，开发态在项目根，已 gitignore）；
- 形状：``{"version": 1, "cases": [ ...用例... ]}``，一条用例 = 元信息 + 步骤数组；
- 入口：页面「流程测试 → 用例管理…」（`gdw_flow_editor.FlowCaseEditorDialog`）
  或直接手写 JSON（两条路径共用同一套校验）。

职责边界
--------
本模块只做**数据 ↔ FlowCase 的往返与校验**，不碰 Qt、不碰串口、不碰执行：
执行仍由 `gdw_test_flows.FlowRunner` + `gdw_test_widget` 的状态机负责。
校验失败一律给出「第 N 步：原因」的可读中文，**不做静默纠正**
（把非法用例悄悄改成别的意思，比拒绝保存更危险）。
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import app_paths
import gdw_test_core as core
import gdw_test_flows as flows
from atomic_io import atomic_write_json

FILE_NAME = "gdw_test_flow_custom.json"
FILE_VERSION = 1

#: 允许的步骤类型（与 `gdw_test_flows.KIND_*` 一一对应，顺序 = 编辑器下拉顺序）
KINDS: Tuple[str, ...] = (
    flows.KIND_SEND,
    flows.KIND_WAIT,
    flows.KIND_SLEEP,
    flows.KIND_MANUAL,
    flows.KIND_NOTE,
    flows.KIND_RESET_STA,
    flows.KIND_POLL,
    flows.KIND_SERVE,
    flows.KIND_SILENCE,
    flows.KIND_EXPECT_STA,
)

#: 允许的步骤内容校验名（与 `gdw_test_flows.CHECK_*` 一一对应；空串 = 不校验）
CHECKS: Tuple[str, ...] = (
    flows.CHECK_ACK,
    flows.CHECK_ARCHIVE,
    flows.CHECK_TOPOLOGY,
    flows.CHECK_DELAY,
    flows.CHECK_BROADCAST_ACK,
    flows.CHECK_ROUTE_STATE,
    flows.CHECK_READ_MATCH,
    flows.CHECK_MASTER_ADDR,
    flows.CHECK_NODE_COUNT,
    flows.CHECK_BAND,
    flows.CHECK_BAND_ECHO,
)

#: 步骤类型 → 中文标签（编辑器显示用；ui 只读这一份）
KIND_LABELS: Dict[str, str] = {
    flows.KIND_SEND: "发送命令（等应答）",
    flows.KIND_WAIT: "等待上行（被动）",
    flows.KIND_SLEEP: "固定延时",
    flows.KIND_MANUAL: "人工配合",
    flows.KIND_NOTE: "说明（不停留）",
    flows.KIND_RESET_STA: "复位 STA（645 广播）",
    flows.KIND_POLL: "轮询直到通过",
    flows.KIND_SERVE: "服务路由请求",
    flows.KIND_SILENCE: "静默窗口（收到任何上行即失败）",
    flows.KIND_EXPECT_STA: "观测 STA 域下行帧",
}

#: 校验名 → 中文标签
CHECK_LABELS: Dict[str, str] = {
    "": "不校验",
    flows.CHECK_ACK: "已应答（确认/数据均可）",
    flows.CHECK_ARCHIVE: "档案含组网地址",
    flows.CHECK_TOPOLOGY: "拓扑角色有效（入网）",
    flows.CHECK_DELAY: "广播延迟 T1 > 0",
    flows.CHECK_BROADCAST_ACK: "确认帧等待时间 T2 > 0",
    flows.CHECK_ROUTE_STATE: "工作标志=停止 + 路由完成",
    flows.CHECK_READ_MATCH: "抄读数据与虚拟表应答一致",
    flows.CHECK_MASTER_ADDR: "主节点地址 ≠ 终端地址",
    flows.CHECK_NODE_COUNT: "从节点数量 == 组网节点数",
    flows.CHECK_BAND: "宽带载波频段 ∈ 0~3",
    flows.CHECK_BAND_ECHO: "回读频段 == 设置值",
}

#: `sta_filter` 允许的键
STA_FILTER_KEYS: Tuple[str, ...] = ("域", "控制码", "DI", "APDU")
STA_DOMAINS: Tuple[str, ...] = (flows.STA_DOMAIN_645, flows.STA_DOMAIN_698)

MAX_STEPS = 60
#: 一条用例里 `addrs` 允许的最大地址数（并发抄表测上限用 100，留一倍余量）
MAX_ADDRS = 200


def _is_addr12(text: Any) -> bool:
    """严格 12 位十进制（JSON/FlowStep 里必须是这个形态；编辑器文本可以简写）。"""
    raw = str(text or "").strip()
    return len(raw) == 12 and raw.isdigit()


def _norm_addr(text: Any) -> str:
    """12 位十进制地址（去空白；不足左补 0；非法返回空串）。"""
    raw = "".join(str(text or "").split())
    if not raw or not raw.isdigit() or len(raw) > 12:
        return ""
    return raw.zfill(12)


def _expand_range(piece: str) -> Optional[List[str]]:
    """``起始-结束`` → 地址列表；不是区间/越界/反向时返回 None。"""
    for sep in ("-", "~", ".."):
        if sep in piece:
            left, _, right = piece.partition(sep)
            start, end = _norm_addr(left), _norm_addr(right)
            if not (start and end):
                return None
            low, high = int(start), int(end)
            if high < low or high - low >= MAX_ADDRS:
                return None
            return [f"{i:012d}" for i in range(low, high + 1)]
    return None


def parse_addrs(text: str) -> List[str]:
    """把编辑器里的地址写法解析成 12 位地址列表。

    支持两种写法（可混用、用逗号/顿号/空格分隔）：

    - 区间：``000000000001-000000000100``（两端可简写为 ``1-100``）；
    - 列表：``000000000001,000000000002``。

    非法片段**原样跳过**（由调用方按 ``validate_step`` 的规则报出来），
    不静默纠正成别的地址。
    """
    out: List[str] = []
    for chunk in (str(text or "").replace("，", ",").replace("、", ",")
                  .replace("　", " ").split()):
        for part in chunk.split(","):
            piece = part.strip()
            if not piece:
                continue
            expanded = _expand_range(piece)
            if expanded is not None:
                out.extend(expanded)
                continue
            addr = _norm_addr(piece)
            if addr:
                out.append(addr)
    # 去重保序
    seen = set()
    uniq: List[str] = []
    for addr in out:
        if addr not in seen:
            seen.add(addr)
            uniq.append(addr)
    return uniq


def format_addrs(addrs: Sequence[str]) -> str:
    """地址列表 → 编辑器文本（连续段压成 ``起始-结束``）。"""
    items = [str(a).strip() for a in (addrs or ()) if str(a).strip()]
    if not items:
        return ""
    parts: List[str] = []
    start = prev = items[0]
    for addr in items[1:]:
        try:
            contiguous = int(addr) == int(prev) + 1
        except (TypeError, ValueError):
            contiguous = False
        if contiguous:
            prev = addr
            continue
        parts.append(start if start == prev else f"{start}-{prev}")
        start = prev = addr
    parts.append(start if start == prev else f"{start}-{prev}")
    return ",".join(parts)

_CATALOG: Dict[str, core.GdwCommand] = {}


def catalog() -> Dict[str, core.GdwCommand]:
    """``key → GdwCommand``（标准 + 自定义 AFN/Fn），只建一次。"""
    if not _CATALOG:
        for cmd in list(core.all_commands()) + list(core.custom_commands()):
            _CATALOG.setdefault(cmd.key, cmd)
    return _CATALOG


def refresh_catalog() -> None:
    """自定义 AFN/Fn 增删后调用（丢掉缓存，下次重建）。"""
    _CATALOG.clear()


def command_keys() -> set:
    """全部可用命令 key（``AFN-Fn``）。"""
    return set(catalog())


def field_names(key: str) -> List[str]:
    """某条命令的字段名（用于校验 ``values`` 的键）。"""
    cmd = catalog().get(str(key or ""))
    if cmd is None:
        return []
    return [str(f.get("name")) for f in (cmd.fields or ()) if f.get("name")]


# ── 路径 ────────────────────────────────────────────────────────────────
def store_path(path: Optional[str] = None) -> str:
    """自定义用例文件路径：显式 ``path`` > ``app_paths.data_path``（属性访问）。

    必须走 `app_paths.data_path` **属性访问**（不是 from-import），
    否则测试的 `_config_sandbox` patch 不生效、会把用例写到真实数据目录。
    """
    if path:
        return str(path)
    return str(app_paths.data_path(FILE_NAME))


# ── 单步校验 ────────────────────────────────────────────────────────────
def _as_int(value: Any) -> Optional[int]:
    try:
        if isinstance(value, bool):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _denial_codes(value: Any) -> Optional[List[int]]:
    """``expect_denial`` → 合法码列表；非法返回 None。"""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        out = []
        for item in value:
            code = _as_int(item)
            if code is None or not (0 <= code <= 255):
                return None
            out.append(code)
        return out
    code = _as_int(value)
    if code is None or not (0 <= code <= 255):
        return None
    return [code]


def validate_step(step: Mapping[str, Any], keys: Optional[set] = None
                  ) -> List[str]:
    """校验一步；返回**原因列表**（空 = 通过，元素不带「第 N 步」前缀）。"""
    if not isinstance(step, Mapping):
        return ["步骤必须是对象"]
    keys = command_keys() if keys is None else keys
    errors: List[str] = []
    kind = str(step.get("kind") or "").strip()
    if kind not in KINDS:
        errors.append(f"未知步骤类型 {kind!r}（可选：{'/'.join(KINDS)}）")
        return errors
    if not str(step.get("title") or "").strip():
        errors.append("缺少步骤标题")

    key = str(step.get("key") or "").strip()
    need_key = kind in (flows.KIND_SEND, flows.KIND_POLL)
    if need_key and not key:
        errors.append("该步骤类型必须选命令（key）")
    if key and key not in keys:
        errors.append(f"命令 {key} 不存在（既不在标准表也不在自定义 AFN 里）")

    alt_keys = [str(x).strip() for x in (step.get("alt_keys") or ()) if str(x).strip()]
    for alt in alt_keys:
        if alt not in keys:
            errors.append(f"备用命令 {alt} 不存在")

    check = str(step.get("check") or "").strip()
    if check and check not in CHECKS:
        errors.append(f"未知校验名 {check!r}")

    timeout = step.get("timeout", flows.FlowStep.timeout)
    try:
        seconds = float(timeout)
    except (TypeError, ValueError):
        errors.append(f"超时必须是数字（当前 {timeout!r}）")
        seconds = 0.0
    if not (0.0 < seconds <= 3600.0):
        errors.append(f"超时必须在 0~3600 秒之间（当前 {seconds:g}）")

    if kind == flows.KIND_WAIT:
        afn = _as_int(step.get("afn"))
        fn = _as_int(step.get("fn"))
        if afn is None or not (0 <= afn <= 255):
            errors.append(f"等待步骤的 AFN 必须是 0~255（当前 {step.get('afn')!r}）")
        if fn is None or not (-1 <= fn <= 255):
            errors.append(f"等待步骤的 Fn 必须是 -1~255（当前 {step.get('fn')!r}）")

    if _denial_codes(step.get("expect_denial")) is None:
        errors.append(f"预期否认码必须是 0~255 或空（当前 {step.get('expect_denial')!r}）")

    values = step.get("values") or {}
    if not isinstance(values, Mapping):
        errors.append("字段覆盖（values）必须是对象")
    elif key:
        allowed = set(field_names(key))
        bad = [name for name in values if name not in allowed]
        if bad:
            errors.append(f"字段 {'、'.join(map(str, bad))} 不在命令 {key} 的字段表里"
                          f"（可用：{'、'.join(sorted(allowed)) or '无字段'}）")

    flt = step.get("sta_filter") or {}
    if not isinstance(flt, Mapping):
        errors.append("STA 过滤条件必须是对象")
    else:
        bad = [name for name in flt if name not in STA_FILTER_KEYS]
        if bad:
            errors.append(f"STA 过滤条件只允许 {'/'.join(STA_FILTER_KEYS)}"
                          f"（多了 {'、'.join(map(str, bad))}）")
        domain = str(flt.get("域") or "").strip()
        if domain and domain not in STA_DOMAINS:
            errors.append(f"STA 过滤的「域」只能是 645 / 698（当前 {domain!r}）")

    addrs = step.get("addrs")
    if addrs:
        if not isinstance(addrs, (list, tuple)):
            errors.append("从节点地址列（addrs）必须是数组")
        else:
            if len(addrs) > MAX_ADDRS:
                errors.append(f"从节点地址列 {len(addrs)} 个，超过上限 {MAX_ADDRS}")
            bad = [a for a in addrs if not _is_addr12(a)]
            if bad:
                errors.append(f"从节点地址必须是 12 位十进制（非法：{'、'.join(map(str, bad[:5]))}）")
    batch = _as_int(step.get("batch"))
    if batch is None or batch < 0:
        errors.append(f"每帧地址个数（batch）必须是 ≥0 的整数（当前 {step.get('batch')!r}）")
    elif batch > MAX_ADDRS:
        errors.append(f"每帧地址个数（batch）不能超过 {MAX_ADDRS}")
    if step.get("concurrent") and not addrs:
        errors.append("并发连发必须给「从节点地址列」（否则只能按组网参数的 1~2 个节点连发，测不出并发上限）")
    return errors


# ── 用例校验 ────────────────────────────────────────────────────────────
def _fid_ok(fid: str) -> bool:
    if not (3 <= len(fid) <= 33):
        return False
    if not (fid[0].isascii() and fid[0].isalpha()):
        return False
    return all(ch.isascii() and (ch.isalnum() or ch == "_") for ch in fid)


def validate_case(data: Mapping[str, Any], builtin_ids: Sequence[str],
                  keys: Optional[set] = None) -> List[str]:
    """校验一条用例；返回原因列表（空 = 通过）。"""
    if not isinstance(data, Mapping):
        return ["用例必须是对象"]
    keys = command_keys() if keys is None else keys
    errors: List[str] = []

    fid = str(data.get("fid") or "").strip()
    if not _fid_ok(fid):
        errors.append("标识（fid）必须是 3~33 位的字母开头、只含字母/数字/下划线的 ASCII")
    elif fid in set(builtin_ids):
        errors.append(f"标识 {fid} 与内置用例重名")
    if not str(data.get("name") or "").strip():
        errors.append("缺少用例名称")

    steps = data.get("steps")
    if not isinstance(steps, (list, tuple)) or not steps:
        return errors + ["至少要有一个步骤"]
    if len(steps) > MAX_STEPS:
        errors.append(f"步骤数 {len(steps)} 超过上限 {MAX_STEPS}")

    for idx, step in enumerate(steps, start=1):
        for reason in validate_step(step, keys):
            errors.append(f"第 {idx} 步：{reason}")
    return errors


# ── dict ↔ FlowCase ────────────────────────────────────────────────────
def _step_to_dict(step: flows.FlowStep) -> Dict[str, Any]:
    return {
        "kind": step.kind,
        "title": step.title,
        "key": step.key,
        "afn": int(step.afn),
        "fn": int(step.fn),
        "timeout": float(step.timeout),
        "values": dict(step.values or {}),
        "accept": [[int(a), int(b)] for a, b in (step.accept or ())],
        "serve": [[int(a), int(b)] for a, b in (step.serve or ())],
        "until": [int(step.until[0]), int(step.until[1])] if step.until else [-1, -1],
        "alt_keys": [str(x) for x in (step.alt_keys or ())],
        "check": step.check,
        "optional": bool(step.optional),
        "needs_slave": bool(step.needs_slave),
        "no_reply": bool(step.no_reply),
        "split_nodes": bool(step.split_nodes),
        "addrs": [str(a) for a in (step.addrs or ())],
        "batch": int(getattr(step, "batch", 0) or 0),
        "concurrent": bool(getattr(step, "concurrent", False)),
        "expect_denial": (_denial_codes(step.expect_denial) or None),
        "sta_filter": dict(step.sta_filter or {}),
        "hint": step.hint,
    }


def _pairs(value: Any, default: Sequence[Sequence[int]]) -> Tuple[Tuple[int, int], ...]:
    out: List[Tuple[int, int]] = []
    for item in (value if value is not None else default) or ():
        try:
            a, b = int(item[0]), int(item[1])
        except (TypeError, ValueError, IndexError):
            continue
        out.append((a, b))
    return tuple(out)


def _step_from_dict(data: Mapping[str, Any]) -> flows.FlowStep:
    codes = _denial_codes(data.get("expect_denial"))
    denial: Any = None
    if codes:
        denial = codes[0] if len(codes) == 1 else tuple(codes)
    until = data.get("until")
    until_pair = (int(until[0]), int(until[1])) if until else (-1, -1)
    return flows.FlowStep(
        kind=str(data.get("kind") or flows.KIND_SEND),
        title=str(data.get("title") or ""),
        key=str(data.get("key") or ""),
        afn=int(data.get("afn", -1)),
        fn=int(data.get("fn", -1)),
        timeout=float(data.get("timeout", 6.0)),
        values=dict(data.get("values") or {}),
        needs_slave=bool(data.get("needs_slave")),
        optional=bool(data.get("optional")),
        check=str(data.get("check") or ""),
        accept=_pairs(data.get("accept"), ()),
        serve=_pairs(data.get("serve"), ((0x14, 0x01), (0x14, 0x02))),
        until=until_pair,
        no_reply=bool(data.get("no_reply")),
        hint=str(data.get("hint") or ""),
        expect_denial=denial,
        alt_keys=tuple(str(x) for x in (data.get("alt_keys") or ())),
        split_nodes=bool(data.get("split_nodes")),
        addrs=tuple(str(a) for a in (data.get("addrs") or ())),
        batch=int(data.get("batch") or 0),
        concurrent=bool(data.get("concurrent")),
        sta_filter=dict(data.get("sta_filter") or {}),
    )


def case_from_dict(data: Mapping[str, Any]) -> flows.FlowCase:
    """dict → FlowCase（非法数据抛 ``ValueError``，原因以「；」连接）。"""
    errors = validate_case(data, [c.fid for c in flows.builtin_cases()])
    if errors:
        raise ValueError("；".join(errors))
    return flows.FlowCase(
        fid=str(data.get("fid")).strip(),
        name=str(data.get("name")).strip(),
        desc=str(data.get("desc") or ""),
        steps=[_step_from_dict(step) for step in data.get("steps") or ()],
        source=str(data.get("source") or ""),
        builtin=False,
    )


def case_to_dict(case: flows.FlowCase) -> Dict[str, Any]:
    return {
        "fid": case.fid,
        "name": case.name,
        "desc": case.desc,
        "source": case.source,
        "steps": [_step_to_dict(step) for step in case.steps],
    }


def new_step_dict(kind: str = flows.KIND_SEND) -> Dict[str, Any]:
    """一条空白步骤（编辑器「新增步骤」用，形状与手写 JSON 完全一致）。"""
    return {
        "kind": kind,
        "title": "",
        "key": "",
        "afn": -1,
        "fn": -1,
        "timeout": 6.0,
        "values": {},
        "accept": [],
        "alt_keys": [],
        "check": "",
        "optional": False,
        "needs_slave": False,
        "no_reply": False,
        "split_nodes": False,
        "addrs": [],
        "batch": 0,
        "concurrent": False,
        "expect_denial": None,
        "sta_filter": {},
        "hint": "",
    }


def new_case_dict(fid: str = "my_case", name: str = "我的用例") -> Dict[str, Any]:
    """一条空白用例（编辑器「新增用例」用）。"""
    return {
        "fid": fid,
        "name": name,
        "desc": "",
        "source": "用户自定义",
        "steps": [new_step_dict()],
    }


# ── 读写 ────────────────────────────────────────────────────────────────
def load_cases(path: Optional[str] = None) -> Tuple[List[flows.FlowCase], List[str]]:
    """读自定义用例 → ``(用例列表, 错误列表)``。

    容错口径（不抛）：
    - 文件不存在 → ``([], [])``；
    - JSON 坏 / 顶层不是对象或 ``cases`` 不是数组 → ``([], ["…"])``；
    - **单条用例非法 → 跳过该条并记原因，其余照常返回**（一条写错不该让整页空掉）。
    """
    target = store_path(path)
    try:
        import json

        with open(target, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return [], []
    except OSError as exc:
        return [], [f"无法读取 {target}：{exc}"]
    except ValueError as exc:
        return [], [f"无法解析 {target}（JSON 格式错误）：{exc}"]
    if not isinstance(data, Mapping):
        return [], [f"{target} 的顶层必须是对象（形如 {{\"version\": 1, \"cases\": [...]}}）"]
    raw_cases = data.get("cases")
    if raw_cases is None:
        return [], []
    if not isinstance(raw_cases, (list, tuple)):
        return [], [f"{target} 的 cases 必须是数组"]

    cases: List[flows.FlowCase] = []
    errors: List[str] = []
    builtin_ids = [c.fid for c in flows.builtin_cases()]
    seen: Dict[str, int] = {}
    for idx, raw in enumerate(raw_cases, start=1):
        if not isinstance(raw, Mapping):
            errors.append(f"第 {idx} 条用例：必须是对象")
            continue
        fid = str(raw.get("fid") or "").strip()
        if fid in seen:
            errors.append(f"第 {idx} 条用例：标识 {fid} 与第 {seen[fid]} 条重复（已跳过）")
            continue
        reasons = validate_case(raw, builtin_ids)
        if reasons:
            label = f"第 {idx} 条用例" + (f"（{fid}）" if fid else "")
            errors.append(f"{label}：" + "；".join(reasons))
            continue
        seen[fid] = idx
        cases.append(case_from_dict(raw))
    return cases, errors


def save_cases(cases: Sequence[Mapping[str, Any]], path: Optional[str] = None) -> None:
    """原子写自定义用例（``cases`` 是 dict 序列，形状同 JSON）。

    写的是**调用方给的 dict 列表**而不是 FlowCase：编辑器里可能故意留着
    校验不通过的草稿，落盘时由调用方先 `validate_case` 拦下来。
    """
    target = store_path(path)
    atomic_write_json(target, {"version": FILE_VERSION, "cases": list(cases)})
    flows.invalidate_custom_cache()


def dump_cases() -> List[Dict[str, Any]]:
    """当前文件里的用例（dict 形状，供编辑器载入）。"""
    target = store_path()
    try:
        import json

        with open(target, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return []
    raw = data.get("cases") if isinstance(data, Mapping) else None
    if not isinstance(raw, (list, tuple)):
        return []
    return [dict(item) for item in raw if isinstance(item, Mapping)]


__all__ = [
    "FILE_NAME",
    "FILE_VERSION",
    "KINDS",
    "CHECKS",
    "KIND_LABELS",
    "CHECK_LABELS",
    "STA_FILTER_KEYS",
    "STA_DOMAINS",
    "MAX_STEPS",
    "MAX_ADDRS",
    "parse_addrs",
    "format_addrs",
    "catalog",
    "refresh_catalog",
    "command_keys",
    "field_names",
    "store_path",
    "validate_step",
    "validate_case",
    "case_from_dict",
    "case_to_dict",
    "new_step_dict",
    "new_case_dict",
    "load_cases",
    "save_cases",
    "dump_cases",
]
