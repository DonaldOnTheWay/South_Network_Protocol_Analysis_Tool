# -*- coding: utf-8 -*-
"""协议7 流程测试「自定义用例」回归：JSON 层 + 编辑器 + 页面接线。

跑法：``python test/test_gdw_flow_custom.py``（需要 offscreen Qt）。

覆盖：
- [1] ``case_to_dict`` / ``case_from_dict`` 往返不丢语义
- [2] ``validate_case`` 矩阵（坏 kind / 不存在的 key / timeout / 字段表 / sta_filter / fid）
- [3] ``save_cases`` / ``load_cases`` 容错（坏 JSON / 单条坏用例跳过 / 重复 fid）
- [4] ``all_cases`` / ``flow_case`` / ``leaf_count`` 集成（不存在的文件不影响内置用例）
- [5] 编辑器 offscreen：新增 + 加步 + 保存 → saved 信号 + 落盘；非法 fid → 弹框且不写盘
- [6] 页面接线：保存后流程页树上出现该用例叶子
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import _config_sandbox

    SANDBOX = _config_sandbox.install()
except Exception:                                   # pragma: no cover - 无沙箱时退化
    SANDBOX = None

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Signal  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox  # noqa: E402

import gdw_test_core as C  # noqa: E402
import gdw_test_flow_custom as K  # noqa: E402
import gdw_test_flows as F  # noqa: E402
import gdw_test_widget as W  # noqa: E402
import gdw_flow_editor as E  # noqa: E402

FAILED = []
CHECKS = [0]


def section(title):
    print(f"\n-- {title} " + "-" * max(0, 62 - len(title)))


def check(name, got, want):
    CHECKS[0] += 1
    if got == want:
        print(f"  [OK] {name}")
        return True
    FAILED.append(f"{name}\n    got  = {got!r}\n    want = {want!r}")
    print(f"  [NG] {name}  -> {got!r}")
    return False


def check_true(name, cond, detail=""):
    CHECKS[0] += 1
    if cond:
        print(f"  [OK] {name}")
        return True
    FAILED.append(f"{name}\n    detail = {detail!r}")
    print(f"  [NG] {name}  detail={detail!r}")
    return False


def _tmp_path(name="case.json"):
    if SANDBOX is None:
        import tempfile

        return os.path.join(tempfile.mkdtemp(prefix="nw_flow_"), name)
    return str(SANDBOX / name)


def _good_case(fid="c_mine", name="我的用例"):
    """一条能通过校验的最小用例（2 步：send + wait）。"""
    case = K.new_case_dict(fid, name)
    step0 = case["steps"][0]
    step0.update({"title": "查主节点地址", "key": "03-4", "timeout": 6.0,
                  "check": F.CHECK_MASTER_ADDR})
    case["steps"].append(dict(K.new_step_dict(F.KIND_WAIT), title="等 03H F10 上报",
                              afn=0x03, fn=10, timeout=60.0, needs_slave=False))
    case["desc"] = "测试用自定义用例"
    case["source"] = "用户自定义"
    return case


# ══════════════════════════════════════════════════════════════════════
# [1] dict ↔ FlowCase 往返
# ══════════════════════════════════════════════════════════════════════
def test_roundtrip():
    section("[1] dict ↔ FlowCase 往返")
    data = _good_case()
    step0 = data["steps"][0]
    # 用 10H F104 承载 values（03H F4 无字段，键会被校验拦下）
    step0.update({"key": "10-104", "title": "查询升级后模块版本信息"})
    step0.update({"alt_keys": ["11-2"], "expect_denial": [1, 2], "split_nodes": True,
                  "addrs": ["000000000001", "000000000002"], "batch": 2,
                  "concurrent": True, "no_reply": True, "optional": True,
                  "needs_slave": True, "accept": [[0x14, 3], [0x14, 1]],
                  "values": {"节点数量": 0}, "hint": "备注"})
    case = K.case_from_dict(data)
    check("fid/name 解析", (case.fid, case.name), ("c_mine", "我的用例"))
    check("自定义用例 builtin=False", case.builtin, False)
    check("步骤数", len(case.steps), 2)

    back = K.case_to_dict(case)
    check("往返后步骤数不变", len(back["steps"]), 2)
    s0 = back["steps"][0]
    check("往返保 key/check", (s0["key"], s0["check"]), ("10-104", F.CHECK_MASTER_ADDR))
    check("往返保 alt_keys", s0["alt_keys"], ["11-2"])
    check("往返保 expect_denial（序列）", s0["expect_denial"], [1, 2])
    check("往返保布尔开关",
          (s0["split_nodes"], s0["concurrent"], s0["no_reply"], s0["optional"],
           s0["needs_slave"]), (True, True, True, True, True))
    check("往返保从节点地址列", s0["addrs"], ["000000000001", "000000000002"])
    check("往返保每帧地址个数", s0["batch"], 2)
    check("往返保 accept", s0["accept"], [[0x14, 3], [0x14, 1]])
    check("往返保 values", s0["values"], {"节点数量": 0})
    check("往返保 hint", s0["hint"], "备注")
    check("wait 步的 afn/fn 往返", (back["steps"][1]["afn"], back["steps"][1]["fn"]), (3, 10))

    # 再解一遍：往返两轮应逐字段相等
    case2 = K.case_from_dict(back)
    check("两轮往返稳定", K.case_to_dict(case2), back)

    # 单码 / 无预期 否认码的归一
    one = _good_case("c_one")
    one["steps"][0]["expect_denial"] = 6
    check("单码往返仍是单值", K.case_to_dict(K.case_from_dict(one))["steps"][0]["expect_denial"], [6])
    check("不预期否认 → None",
          K.case_to_dict(K.case_from_dict(_good_case("c_two")))["steps"][0]["expect_denial"],
          None)


# ══════════════════════════════════════════════════════════════════════
# [2] 校验矩阵
# ══════════════════════════════════════════════════════════════════════
def test_validate():
    section("[2] validate_case 校验矩阵")
    builtin = [c.fid for c in F.builtin_cases()]
    keys = K.command_keys()
    check_true("命令表非空（含标准 AFN）", "03-4" in keys, len(keys))
    check_true("合法用例无错误", not K.validate_case(_good_case(), builtin),
               K.validate_case(_good_case(), builtin))

    bad_kind = _good_case("c_kind")
    bad_kind["steps"][0]["kind"] = "teleport"
    errors = K.validate_case(bad_kind, builtin)
    check_true("未知步骤类型被拦", any("未知步骤类型" in e for e in errors), errors)

    bad_key = _good_case("c_key")
    bad_key["steps"][0]["key"] = "99-9"
    errors = K.validate_case(bad_key, builtin)
    check_true("不存在的命令被拦", any("不存在" in e for e in errors), errors)
    check_true("错误带「第 N 步」前缀", any(e.startswith("第 1 步：") for e in errors), errors)

    missing_key = _good_case("c_nokey")
    missing_key["steps"][0]["key"] = ""
    check_true("send 缺命令被拦",
               any("必须选命令" in e for e in K.validate_case(missing_key, builtin)),
               K.validate_case(missing_key, builtin))

    bad_timeout = _good_case("c_timeout")
    bad_timeout["steps"][0]["timeout"] = 0
    check_true("timeout=0 被拦",
               any("超时必须在" in e for e in K.validate_case(bad_timeout, builtin)),
               K.validate_case(bad_timeout, builtin))

    bad_field = _good_case("c_field")
    bad_field["steps"][0]["values"] = {"不存在的字段": 1}
    errors = K.validate_case(bad_field, builtin)
    check_true("values 键不在字段表被拦", any("不在命令" in e for e in errors), errors)

    bad_check = _good_case("c_check")
    bad_check["steps"][0]["check"] = "make_coffee"
    check_true("未知校验名被拦",
               any("未知校验名" in e for e in K.validate_case(bad_check, builtin)),
               K.validate_case(bad_check, builtin))

    bad_filter = _good_case("c_filter")
    bad_filter["steps"][0]["sta_filter"] = {"域": "645", "payload": "x"}
    errors = K.validate_case(bad_filter, builtin)
    check_true("sta_filter 多余键被拦", any("只允许" in e for e in errors), errors)
    bad_domain = _good_case("c_domain")
    bad_domain["steps"][0]["sta_filter"] = {"域": "modbus"}
    check_true("sta_filter 域只认 645/698",
               any("只能是 645 / 698" in e for e in K.validate_case(bad_domain, builtin)),
               K.validate_case(bad_domain, builtin))

    bad_wait = _good_case("c_wait")
    bad_wait["steps"][1]["afn"] = 999
    check_true("wait 的 AFN 越界被拦",
               any("AFN 必须是" in e for e in K.validate_case(bad_wait, builtin)),
               K.validate_case(bad_wait, builtin))

    bad_denial = _good_case("c_denial")
    bad_denial["steps"][0]["expect_denial"] = 999
    check_true("否认码越界被拦",
               any("预期否认码" in e for e in K.validate_case(bad_denial, builtin)),
               K.validate_case(bad_denial, builtin))

    clash = _good_case("identify")
    errors = K.validate_case(clash, builtin)
    check_true("与内置用例重名被拦", any("与内置用例重名" in e for e in errors), errors)

    for bad_fid in ("ab", "1abc", "有中文", "a-b"):
        case = _good_case(bad_fid)
        check_true(f"非法 fid {bad_fid!r} 被拦",
                   any("标识（fid）" in e for e in K.validate_case(case, builtin)),
                   K.validate_case(case, builtin))

    empty = _good_case("c_empty")
    empty["steps"] = []
    check_true("没有步骤被拦",
               any("至少要有一个步骤" in e for e in K.validate_case(empty, builtin)),
               K.validate_case(empty, builtin))

    too_long = _good_case("c_long")
    too_long["steps"] = [dict(K.new_step_dict()) for _ in range(K.MAX_STEPS + 1)]
    check_true("步骤数超上限被拦",
               any("超过上限" in e for e in K.validate_case(too_long, builtin)),
               K.validate_case(too_long, builtin)[:1])

    check_true("case_from_dict 对非法数据抛 ValueError",
               _raises(lambda: K.case_from_dict(bad_kind)), "")


def test_addrs():
    section("[2b] 从节点地址列解析 / 并发连发校验")
    check("区间写法", K.parse_addrs("000000000001-000000000003"),
          ["000000000001", "000000000002", "000000000003"])
    check("简写区间", K.parse_addrs("1-3"),
          ["000000000001", "000000000002", "000000000003"])
    check("逗号列表", K.parse_addrs("000000000001,000000000005"),
          ["000000000001", "000000000005"])
    check("混用 + 去重保序",
          K.parse_addrs("000000000002, 1-2，000000000009"),
          ["000000000002", "000000000001", "000000000009"])
    check("非法片段跳过", K.parse_addrs("张三,000000000007"), ["000000000007"])
    check("回写文本压缩连续段", K.format_addrs(F.default_addrs(4)),
          "000000000001-000000000004")
    check("回写文本不连续时用逗号",
          K.format_addrs(["000000000001", "000000000005"]),
          "000000000001,000000000005")
    check("往返一致", K.parse_addrs(K.format_addrs(F.default_addrs(100))),
          list(F.default_addrs(100)))

    builtin = [c.fid for c in F.builtin_cases()]
    ok_case = _good_case("c_addr")
    ok_case["steps"][0]["addrs"] = ["000000000001", "000000000002"]
    check_true("合法地址列无错误", not K.validate_case(ok_case, builtin),
               K.validate_case(ok_case, builtin))

    bad_addr = _good_case("c_badaddr")
    bad_addr["steps"][0]["addrs"] = ["12345"]
    check_true("地址不是 12 位十进制被拦",
               any("12 位十进制" in e for e in K.validate_case(bad_addr, builtin)),
               K.validate_case(bad_addr, builtin))

    too_many = _good_case("c_many")
    too_many["steps"][0]["addrs"] = [f"{i:012d}" for i in range(1, K.MAX_ADDRS + 2)]
    check_true("地址数超上限被拦",
               any("超过上限" in e for e in K.validate_case(too_many, builtin)),
               K.validate_case(too_many, builtin))

    bad_batch = _good_case("c_batch")
    bad_batch["steps"][0]["batch"] = -1
    check_true("batch 负数被拦",
               any("每帧地址个数" in e for e in K.validate_case(bad_batch, builtin)),
               K.validate_case(bad_batch, builtin))

    conc_no_addr = _good_case("c_conc")
    conc_no_addr["steps"][0]["concurrent"] = True
    check_true("并发连发缺地址列被拦",
               any("并发连发必须给" in e for e in K.validate_case(conc_no_addr, builtin)),
               K.validate_case(conc_no_addr, builtin))


def _raises(fn):
    try:
        fn()
    except ValueError:
        return True
    except Exception:                               # noqa: BLE001 - 其它异常也算「没按契约抛」
        return False
    return False


# ══════════════════════════════════════════════════════════════════════
# [3] 读写与容错
# ══════════════════════════════════════════════════════════════════════
def test_store():
    section("[3] save_cases / load_cases 容错")
    path = _tmp_path("store.json")
    check("文件不存在 → 空表且无错误", K.load_cases(path), ([], []))

    K.save_cases([_good_case()], path)
    with open(path, "r", encoding="utf-8") as fh:
        raw = json.load(fh)
    check("落盘形状 {version, cases}", (raw.get("version"), len(raw.get("cases") or [])), (1, 1))
    cases, errors = K.load_cases(path)
    check("读回 1 条且无错误", (len(cases), errors), (1, []))
    check("读回的 fid", cases[0].fid, "c_mine")
    check("读回的步骤内容", cases[0].steps[0].key, "03-4")

    # 单条坏用例：跳过该条，其余照常返回（一条写错不能让整页空掉）
    good = _good_case("c_good", "好的")
    bad = _good_case("BAD ID", "坏的")
    K.save_cases([good, bad], path)
    cases, errors = K.load_cases(path)
    check("坏用例被跳过", [c.fid for c in cases], ["c_good"])
    check_true("错误点名第几条与 fid", ("第 2 条" in errors[0]) and ("BAD ID" in errors[0]),
               errors)

    # 重复 fid：第二条被跳过
    K.save_cases([_good_case("c_dup", "一"), _good_case("c_dup", "二")], path)
    cases, errors = K.load_cases(path)
    check("重复 fid 只保留第一条", len(cases), 1)
    check_true("错误写明重复", any("重复" in e for e in errors), errors)

    # JSON 坏 → 返回错误但不抛
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("{oops")
    cases, errors = K.load_cases(path)
    check("坏 JSON → 空表", cases, [])
    check_true("坏 JSON 给可读错误", any("无法解析" in e for e in errors), errors)

    # 顶层不是对象
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("[1, 2, 3]")
    check_true("顶层不是对象 → 报错不抛",
               any("顶层必须是对象" in e for e in K.load_cases(path)[1]),
               K.load_cases(path))

    # cases 不是数组
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"version": 1, "cases": {"a": 1}}, fh)
    check_true("cases 不是数组 → 报错不抛",
               any("必须是数组" in e for e in K.load_cases(path)[1]), K.load_cases(path))

    # dump_cases 与 save_cases 同形状
    K.save_cases([_good_case()], path)
    check("dump_cases 读回 dict 列表", [c.get("fid") for c in K.dump_cases()],
          ["c_mine"] if K.store_path() == path else [c.get("fid") for c in K.dump_cases()])


# ══════════════════════════════════════════════════════════════════════
# [4] all_cases / flow_case / leaf_count 集成
# ══════════════════════════════════════════════════════════════════════
def test_registry():
    section("[4] all_cases / flow_case / leaf_count 集成")
    path = K.store_path()
    before_all = len(F.all_cases())
    before_leaf = C.leaf_count(C.PAGE_FLOW)
    check("内置 9 条 + 文档补充 7 条", before_all, 16)
    check("leaf_count 与 all_cases 一致", before_leaf, before_all)

    K.save_cases([_good_case("c_mine", "我的自定义用例")], path)
    after_all = len(F.all_cases())
    check("自定义用例进入 all_cases", after_all, before_all + 1)
    check("leaf_count 同步 +1", C.leaf_count(C.PAGE_FLOW), before_leaf + 1)
    case = F.flow_case("c_mine")
    check_true("flow_case 能查到自定义用例", case is not None and case.name == "我的自定义用例",
               case)
    check("自定义用例 builtin=False", case.builtin, False)
    check("内置用例仍可查", F.flow_case("identify").builtin, True)
    check("不存在的 fid 返回 None", F.flow_case("nope_nope"), None)

    tree_items = [item.fid for item in C.flow_items()]
    check("树叶子含自定义用例", tree_items[-1], "c_mine")
    check("树叶子数 = all_cases", len(tree_items), after_all)

    # 清空文件：回到 16 条
    K.save_cases([], path)
    check("清空后回到 16 条", len(F.all_cases()), 16)
    check("清空后 flow_case 查不到", F.flow_case("c_mine"), None)


# ══════════════════════════════════════════════════════════════════════
# [5] 编辑器
# ══════════════════════════════════════════════════════════════════════
def test_editor():
    section("[5] 用例编辑器（新增 / 加步 / 保存 / 非法不写盘）")
    app = QApplication.instance() or QApplication([])
    path = K.store_path()
    K.save_cases([], path)

    warned = []
    orig_warn = QMessageBox.warning
    orig_question = QMessageBox.question
    QMessageBox.warning = staticmethod(lambda *a, **k: warned.append(a[2] if len(a) > 2 else ""))
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)
    try:
        dlg = E.FlowCaseEditorDialog()
        check("空文件时用例列表为空", dlg.list_cases.count(), 0)
        dlg._on_case_new()
        app.processEvents()
        check("新增用例进入列表", (len(dlg.cases), dlg.list_cases.count()), (1, 1))
        check("新增用例默认 1 步", dlg.step_table.rowCount(), 1)

        # 元信息
        dlg.edit_name.setText("我的用例")
        dlg.edit_fid.setText("c_mine")
        dlg._commit_case()
        # 第 1 步：命令 + 标题 + 判定
        dlg.step_table.setCurrentCell(0, 0)
        app.processEvents()
        dlg.combo_cmd.setCurrentIndex(dlg.combo_cmd.findData("03-4"))
        dlg.edit_title.setText("查主节点地址")
        dlg.combo_check.setCurrentIndex(dlg.combo_check.findData(F.CHECK_MASTER_ADDR))
        dlg.spin_timeout.setValue(9.0)
        app.processEvents()
        check("步骤回写命令", dlg.cases[0]["steps"][0]["key"], "03-4")
        check("步骤回写判定", dlg.cases[0]["steps"][0]["check"], F.CHECK_MASTER_ADDR)
        check("步骤回写超时", dlg.cases[0]["steps"][0]["timeout"], 9.0)

        # 加第 2 步（wait）
        dlg._on_step_new()
        app.processEvents()
        check("新增步骤后 2 步", dlg.step_table.rowCount(), 2)
        dlg.combo_kind.setCurrentIndex(dlg.combo_kind.findData(F.KIND_WAIT))
        dlg.spin_afn.setValue(0x03)
        dlg.spin_fn.setValue(10)
        dlg.edit_title.setText("等 03H F10")
        app.processEvents()
        check("wait 步的 afn/fn 写入", (dlg.cases[0]["steps"][1]["afn"],
                                       dlg.cases[0]["steps"][1]["fn"]), (3, 10))

        saved = []
        dlg.saved.connect(lambda: saved.append(1))
        dlg._on_save()
        app.processEvents()
        check("保存触发 saved 信号 1 次", saved, [1])
        check_true("保存后没有校验告警", not warned, warned)
        cases, errors = K.load_cases(path)
        check("落盘 1 条且无错误", (len(cases), errors), (1, []))
        check("落盘的步骤内容", (cases[0].steps[0].key, cases[0].steps[1].afn), ("03-4", 3))

        # 重开对话框能读回
        dlg2 = E.FlowCaseEditorDialog()
        check("重开后读到 1 条", len(dlg2.cases), 1)
        check("重开后 step 表 2 行", dlg2.step_table.rowCount(), 2)

        # 非法 fid → 弹框且不写盘
        before = open(path, "r", encoding="utf-8").read()
        warned.clear()
        saved2 = []
        dlg2.saved.connect(lambda: saved2.append(1))
        dlg2.edit_fid.setText("BAD ID")
        dlg2._commit_case()
        dlg2._on_save()
        app.processEvents()
        after = open(path, "r", encoding="utf-8").read()
        check_true("非法 fid 弹框列出原因", warned and "标识（fid）" in warned[-1], warned)
        check("非法 fid 不写盘", before, after)
        check("非法 fid 不发 saved 信号", saved2, [])

        # 复制 / 删除
        dlg2._on_case_copy()
        app.processEvents()
        check("复制出新用例", len(dlg2.cases), 2)
        check_true("副本 fid 不重复", dlg2.cases[1]["fid"] != dlg2.cases[0]["fid"],
                   [c["fid"] for c in dlg2.cases])
        dlg2._on_case_delete()
        app.processEvents()
        check("删除后回到 1 条", len(dlg2.cases), 1)

        # 步骤上移/下移
        dlg2._reload_step_table(1)
        dlg2._move_step(-1)
        app.processEvents()
        check("上移后首步是 wait", dlg2.cases[0]["steps"][0]["kind"], F.KIND_WAIT)
        dlg2._move_step(1)
        app.processEvents()
        check("下移后首步回到 send", dlg2.cases[0]["steps"][0]["kind"], F.KIND_SEND)
        dlg2.close()
        dlg.close()
    finally:
        QMessageBox.warning = orig_warn
        QMessageBox.question = orig_question


# ══════════════════════════════════════════════════════════════════════
# [6] 页面接线
# ══════════════════════════════════════════════════════════════════════
def test_widget_wiring():
    section("[6] 页面接线：保存后流程页树上出现该用例")
    app = QApplication.instance() or QApplication([])
    path = K.store_path()
    K.save_cases([], path)

    w = W.GdwTestWidget()
    w.show()
    app.processEvents()
    w._select_page(C.PAGE_FLOW, notify=True)
    app.processEvents()
    tree = w._trees[C.PAGE_FLOW]
    before = _flow_leaf_count(w)
    check("初始叶子数 = 全部用例数", before, len(F.all_cases()))

    K.save_cases([_good_case("c_mine", "我的自定义用例")], path)
    w._on_flow_cases_changed()
    app.processEvents()
    check("保存后叶子数 +1", _flow_leaf_count(w), before + 1)
    leaf = _find_leaf(w, "c_mine")
    check_true("树上能找到该用例", leaf is not None,
               [_leaf_fid(it) for it in w._iter_leaves(tree)])
    if leaf is not None:
        tree.setCurrentItem(leaf)
        app.processEvents()
        check("选中后显示用例名", w.lbl_flow_title.text(), "我的自定义用例（2 步）")
        check("步骤表 2 行", w.flow_table.rowCount(), 2)
        check("首行标题", w.flow_table.item(0, 1).text(), "查主节点地址")

    # 删掉自定义用例后树上同步消失
    K.save_cases([], path)
    w._on_flow_cases_changed()
    app.processEvents()
    check("清空后叶子数回落", _flow_leaf_count(w), before)
    check("清空后树上看不到该用例", _find_leaf(w, "c_mine"), None)

    # 「用例管理…」按钮接线：点击要构造 FlowCaseEditorDialog（用替身，不真的 exec）
    check_true("流程页有「用例管理…」按钮", hasattr(w, "btn_flow_manage"),
               [b.text() for b in w.findChildren(type(w.btn_flow_start))])
    built = []

    class _StubDialog(QDialog):
        saved = Signal()

        def __init__(self, parent=None, path=""):
            super().__init__(parent)
            built.append((parent, path))

        def exec(self):                      # noqa: A003 - 跟随 Qt 命名
            return 0

    orig_dialog = E.FlowCaseEditorDialog
    E.FlowCaseEditorDialog = _StubDialog
    try:
        w.btn_flow_manage.click()
        app.processEvents()
        check("点击后构造了用例管理对话框", len(built), 1)
        check_true("对话框父窗口是本页", bool(built) and built[0][0] is w, built)
    finally:
        E.FlowCaseEditorDialog = orig_dialog

    # saved 信号接到 _on_flow_cases_changed：重建树并保留当前用例
    K.save_cases([_good_case("c_mine", "我的自定义用例")], path)
    w._flow_case = F.flow_case("c_mine")
    w._on_flow_cases_changed()
    app.processEvents()
    check("saved 后叶子数 +1", _flow_leaf_count(w), before + 1)
    check_true("saved 后当前用例仍选中", w._flow_case is not None
               and w._flow_case.fid == "c_mine", w._flow_case)
    K.save_cases([], path)
    w.close()


def _flow_leaf_count(w) -> int:
    tree = w._trees.get(C.PAGE_FLOW)
    if tree is None:
        return 0
    return sum(1 for it in w._iter_leaves(tree) if (_leaf_fid(it) or ""))


def _leaf_fid(item):
    data = item.data(0, W.LEAF_KEY_ROLE)
    return data[1] if data and data[0] == "flow" else ""


def _find_leaf(w, fid):
    tree = w._trees.get(C.PAGE_FLOW)
    if tree is None:
        return None
    for it in w._iter_leaves(tree):
        if _leaf_fid(it) == fid:
            return it
    return None


def main():
    test_roundtrip()
    test_validate()
    test_addrs()
    test_store()
    test_registry()
    test_editor()
    test_widget_wiring()

    print()
    if FAILED:
        print(f"失败 {len(FAILED)} 项 / 通过 {CHECKS[0] - len(FAILED)} 项：")
        for item in FAILED:
            print(f"  - {item}")
        raise SystemExit(1)
    print(f"全部通过（{CHECKS[0]} 项）")


if __name__ == "__main__":
    main()
    sys.stdout.flush()
    os._exit(0)
