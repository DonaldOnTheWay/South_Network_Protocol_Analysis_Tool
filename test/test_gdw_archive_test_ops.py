# -*- coding: utf-8 -*-
"""档案管理：详细版本列 LME 解析 + 抄读测试的停止/清除/成功率（协议7 / 国网）。

跑法：``python test/test_gdw_archive_test_ops.py``（需要 offscreen Qt）。

锁住的口径：

- B1 详细版本应答（AFN=13 F1，内层 DI=FF00DD01 / LME 4.6）→「详细版本」列显示
  解析后的条目摘要（厂商/芯片/版本…），不再回退原始 hex；「版本信息」列同时补摘要。
- B2 多行连续查询时列不串行：每行都写自己的第 7 列（历史上 _set_version_query_result
  每调一次就 pop 一次，一帧写两列会把队列错位）。
- B3 非 LME 的普通 645 数据不会被误当 TLV 条目（必须回退原文，且不污染「版本信息」列）。
- B4 点抄/并发抄表：新增「停止」按钮，可中断队列与定时器。
- B5 「清除记录」按钮清空版本信息/详细版本/抄读结果与本次统计。
- B6 总成功率汇总：成功次数 / 计划总次数。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import _config_sandbox

    SANDBOX = _config_sandbox.install()
except Exception:                                   # pragma: no cover
    SANDBOX = None

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, Signal  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import archive_widget as A  # noqa: E402

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


class FakeSerial(QObject):
    """假串口线程：只记录发出的 hex（archive_widget 会连三个信号）。"""

    frame_received = Signal(bytes)
    log_message = Signal(str)
    connection_changed = Signal(bool)

    def __init__(self):
        super().__init__()
        self.sent = []

    def send_hex_string(self, hex_str):
        self.sent.append(str(hex_str))


def make_widget():
    app = QApplication.instance() or QApplication([])
    w = A.ArchiveWidget()
    fake = FakeSerial()
    w.set_serial_worker(fake)
    w.set_protocol_mode("gdw")
    app.processEvents()
    return app, w, fake


# ── 内层 DLT645 帧构造（数据域整体 +0x33，与线上一致）──────────────────
def build_645(decoded_data, ctrl=0x91, addr=b"\x10\x00\x00\x25\x00\x00"):
    enc = bytes((b + 0x33) & 0xFF for b in decoded_data)
    body = bytes([0x68]) + addr + bytes([0x68, ctrl, len(enc)]) + enc
    return body + bytes([sum(body) & 0xFF, 0x16])


def tlv_header(data_id, cat, enc, length):
    """4.6 条目头：2 字节位打包的小端值"""
    return (data_id | (cat << 6) | (enc << 9) | (length << 11)).to_bytes(2, "little")


def lme46_payload():
    """LME 4.6（DI=FF00DD01）应答数据域（减 0x33 后）。M=17，含厂商 LH。"""
    hw_group = bytes([0x04]) + bytes.fromhex("42590002") + bytes([1, 1, 1, 1, 0, 0]) \
        + b"LSX830A-GPR001C-D2" + b"\x00" * 2
    hw_group = hw_group[:31].ljust(31, b"\x00")
    d = bytearray([0x01, 0xDD, 0x00, 0xFF, 0x00, 0x11])
    d += tlv_header(2, 7, 2, 3) + bytes([0x00, 0x00, 0x03])
    d += tlv_header(1, 7, 2, 31) + hw_group
    d += tlv_header(60, 7, 2, 2) + bytes([0xAB, 0xCD])
    d += tlv_header(1, 0, 1, 2) + b"\x48\x4C"          # 外部厂商代码 = LH
    for _ in range(13):
        d += tlv_header(1, 4, 2, 2) + bytes([0x00, 0x00])
    return bytes(d)


def uplink_afn13_f1(inner_frame, duration=2, proto=2, addr6=b"\x00\x00\x11\xff\x02\xc2"):
    """手搓 13H F1 上行：上行时长(2B) + 协议类型(1B) + 报文长度(1B) + 报文内容。"""
    data = int(duration).to_bytes(2, "little") + bytes([proto, len(inner_frame) & 0xFF]) + inner_frame
    payload = bytes([0xC3]) + addr6 + bytes([0x13, 0x01, 0x00]) + data
    total_len = len(payload) + 5
    return (bytes([0x68, total_len & 0xFF, (total_len >> 8) & 0xFF]) + payload
            + bytes([sum(payload) & 0xFF, 0x16]))


def add_meters(w, count, proto="645"):
    for i in range(count):
        w._add_meter_to_table({
            "meter_id": f"00000000000{i + 1}",
            "protocol": proto,
            "phase": "A相",
            "status": "测试",
            "extra": "",
        }, save=False)


def cell(w, row, col):
    item = w.table.item(row, col)
    return item.text() if item else ""


def reset_archive(w):
    w._clear_table()


# ══════════════════════════════════════════════════════════════════════
def test_detail_version_lme():
    section("B1 详细版本列显示 LME 4.6 解析结果（不再是原始 hex）")
    app, w, fake = make_widget()
    reset_archive(w)
    add_meters(w, 1)
    w._start_version_query([0], "detail")
    check("开始时详细版本列=查询中...", cell(w, 0, 7), "查询中...")
    frame = uplink_afn13_f1(build_645(lme46_payload()))
    w._on_frame_received(frame)
    app.processEvents()
    col7 = cell(w, 0, 7)
    col6 = cell(w, 0, 6)
    check_true("第 7 列显示解析摘要（厂商:LH）", "厂商:LH" in col7, col7)
    check_true("第 7 列不再是「数据:010D00FF...」原文", "数据:" not in col7, col7)
    check("第 6 列（版本信息）不由详细版本命令写入", col6, "")
    check_true("全量条目：含硬件信息组等非优先字段",
               "硬件信息组" in col7 or "复位计数" in col7, col7)
    check_true("队列已清空", not w._version_query_queue, w._version_query_queue)
    check("查询结束 _is_querying_version", w._is_querying_version, False)
    w.deleteLater()
    return app


def test_detail_version_multi_rows():
    section("B2 多行连续查询：每行写自己的第 7 列（队列不错位）")
    app, w, fake = make_widget()
    reset_archive(w)
    add_meters(w, 2)
    w._start_version_query([0, 1], "detail")
    frame = uplink_afn13_f1(build_645(lme46_payload()))
    w._on_frame_received(frame)
    app.processEvents()
    check_true("第 1 行第 7 列已写解析结果", "厂商:LH" in cell(w, 0, 7), cell(w, 0, 7))
    check("第 1 行第 6 列未被详细版本命令写", cell(w, 0, 6), "")
    # 第 2 行还在等它的应答，不能被第 1 帧写掉
    check("第 2 行第 7 列仍=查询中...", cell(w, 1, 7), "查询中...")
    check("第 2 行第 6 列仍为空", cell(w, 1, 6), "")
    w._on_frame_received(frame)
    app.processEvents()
    check_true("第 2 行第 7 列也写了", "厂商:LH" in cell(w, 1, 7), cell(w, 1, 7))
    check("两行都收完，队列空", w._version_query_queue, [])
    w.deleteLater()
    return app


def test_detail_version_non_lme():
    section("B3 普通 645 数据不被误判为 LME 条目（回原文）")
    app, w, fake = make_widget()
    reset_archive(w)
    add_meters(w, 1)
    w._start_version_query([0], "detail")
    # DI=00010000 当前正向有功总电能，4 字节数据
    plain = bytes([0x00, 0x00, 0x01, 0x00]) + bytes([0x01, 0x00, 0x00, 0x00])
    w._on_frame_received(uplink_afn13_f1(build_645(plain)))
    app.processEvents()
    col7 = cell(w, 0, 7)
    check_true("第 7 列回退原文（含 DI: 与 数据:）",
               "DI:" in col7 and "数据:" in col7, col7)
    check("普通数据不污染第 6 列", cell(w, 0, 6), "")
    w.deleteLater()
    return app


def test_copy_stop():
    section("B4 点抄测试：停止按钮可中断")
    app, w, fake = make_widget()
    reset_archive(w)
    add_meters(w, 3)
    w.copy_count_input.setText("2")
    check("初始停止按钮 disabled", w.stop_copy_btn.isEnabled(), False)
    w._start_copy_test([0, 1, 2], "sequential")
    app.processEvents()
    check("开始后停止按钮 enabled", w.stop_copy_btn.isEnabled(), True)
    check("_is_copy_testing", w._is_copy_testing, True)
    check("队列 = 3 表 × 2 轮 = 6 个任务", len(w._copy_test_queue), 6)
    check("调度顺序 = 先第 1 轮按表号，再第 2 轮",
          [(t["round"], t["addr"]) for t in w._copy_test_queue],
          [(1, "000000000001"), (1, "000000000002"), (1, "000000000003"),
           (2, "000000000001"), (2, "000000000002"), (2, "000000000003")])
    check_true("已发出第 1 帧", len(fake.sent) >= 1, fake.sent)
    check("第 1 行抄读中", cell(w, 0, 8), "抄读中...")
    w._on_stop_copy_test()
    app.processEvents()
    check("停止后 _is_copy_testing", w._is_copy_testing, False)
    check("停止后队列空", w._copy_test_queue, [])
    check("停止后停止按钮 disabled", w.stop_copy_btn.isEnabled(), False)
    left = [cell(w, r, 8) for r in range(3)]
    check_true("没有行停在「抄读中...」",
               all(t != "抄读中..." for t in left), left)
    check_true("已发过帧的行显示部分统计",
               "抄读1次" in left[0], left[0])
    check_true("未发送的行标注已停止",
               any("已停止" in t for t in left), left)
    check_true("日志有「已停止」提示", "已停止" in w.serial_log.toPlainText())
    w.deleteLater()
    return app


def test_clear_records_and_summary():
    section("B5/B6 清除记录 + 总成功率汇总")
    app, w, fake = make_widget()
    reset_archive(w)
    add_meters(w, 2)
    # 手工造统计，验证汇总口径与清除
    w._copy_test_stats = {
        "000000000001": {"sent": 3, "success": 3, "total": 3,
                         "elapsed_last": 0.12, "elapsed_sum": 0.36, "elapsed_n": 3},
        "000000000002": {"sent": 3, "success": 1, "total": 3,
                         "elapsed_last": 0.5, "elapsed_sum": 1.5, "elapsed_n": 3},
    }
    w._copy_test_started_at = 0.0        # 关掉「用时」段，便于断言固定文案
    w._refresh_copy_summary()
    label = w.copy_summary_label.text()
    check_true("总成功率 = 成功4/总抄读6 (66.7%)", "成功4/6" in label and "66.7%" in label, label)
    check_true("汇总含 抄表2表", "抄表2表" in label, label)
    # 单表结果列文案：抄读次数 / 成功数 / 成功率
    w._update_copy_test_result(1, "000000000002")
    check("单表结果列", cell(w, 1, 8), "抄读3次 成功1次 (33%)")
    # 单表耗时列：单次 + 平均
    check("单表耗时列", cell(w, 1, 10), "单次500ms 平均500ms")
    # 填三列再清除
    w.table.setItem(0, 6, A.QTableWidgetItem("版本摘要"))
    w.table.setItem(0, 7, A.QTableWidgetItem("详细摘要"))
    w._on_clear_test_records()
    app.processEvents()
    check("清除后第 6 列空", cell(w, 0, 6), "")
    check("清除后第 7 列空", cell(w, 0, 7), "")
    check("清除后第 8 列空", cell(w, 1, 8), "")
    check("清除后耗时列空", cell(w, 1, 10), "")
    check("清除后统计空", w._copy_test_stats, {})
    check("清除后标签回默认", w.copy_summary_label.text(), "成功率: -")
    # 无记录时再点一次不报错
    w._on_clear_test_records()
    check_true("重复清除不异常", True)
    w.deleteLater()
    return app


def test_version_query_retry_and_stop():
    section("B7 版本查询：次数=重试、成功统计、停止按钮可中断")
    app, w, fake = make_widget()
    reset_archive(w)
    add_meters(w, 2)
    w.copy_count_input.setText("3")
    w._start_version_query([0, 1], "detail")
    app.processEvents()
    check("开始时已发 1 帧", len(fake.sent), 1)
    check("统计 sent=1", w._version_query_stats["000000000001"]["sent"], 1)
    check("停止按钮 enabled", w.stop_copy_btn.isEnabled(), True)
    # 第 1 次超时 → 重试
    w._on_version_timeout()
    app.processEvents()
    check("超时后重发（第 2 帧）", len(fake.sent), 2)
    check("统计 sent=2", w._version_query_stats["000000000001"]["sent"], 2)
    check("第 1 行仍=查询中...", cell(w, 0, 7), "查询中...")
    # 再超时两次 → 用尽 3 次判超时，换下一个节点
    w._on_version_timeout()
    w._on_version_timeout()
    app.processEvents()
    check("第 1 行超时", cell(w, 0, 7), "超时")
    check("已推进到第 2 个节点", w._version_query_stats["000000000002"]["sent"], 1)
    # 停止：中断剩余节点
    w._on_stop_copy_test()
    app.processEvents()
    check("停止后 _is_querying_version", w._is_querying_version, False)
    check("停止后队列空", w._version_query_queue, [])
    check("停止后停止按钮 disabled", w.stop_copy_btn.isEnabled(), False)
    check("第 2 行标已停止", cell(w, 1, 7), "已停止")
    label = w.copy_summary_label.text()
    # 节点1 用满 3 次（首发+2 次重试）、节点2 发 1 次 → 共 4 次发送、0 次成功
    check_true("成功率含版本段（成功0/4）", "版本2表 成功0/4" in label, label)
    w.deleteLater()
    return app


def test_version_query_success_rate():
    section("B8 版本查询：收到应答即记成功")
    app, w, fake = make_widget()
    reset_archive(w)
    add_meters(w, 1)
    w.copy_count_input.setText("2")
    w._start_version_query([0], "detail")
    w._on_frame_received(uplink_afn13_f1(build_645(lme46_payload())))
    app.processEvents()
    check("成功数 1", w._version_query_stats["000000000001"]["success"], 1)
    check("发送数 1", w._version_query_stats["000000000001"]["sent"], 1)
    label = w.copy_summary_label.text()
    check_true("成功率段 版本1表 成功1/1 (100.0%)",
               "版本1表 成功1/1 (100.0%)" in label, label)
    check("查询已结束", w._is_querying_version, False)
    w.deleteLater()
    return app


def main():
    tests = [
        test_detail_version_lme,
        test_detail_version_multi_rows,
        test_detail_version_non_lme,
        test_copy_stop,
        test_clear_records_and_summary,
        test_version_query_retry_and_stop,
        test_version_query_success_rate,
    ]
    for t in tests:
        try:
            t()
        except Exception as e:
            import traceback
            traceback.print_exc()
            FAILED.append(f"{t.__name__} 异常: {e}")
        print()

    print("=" * 70)
    if FAILED:
        print(f"失败 {len(FAILED)} 项 / 共 {CHECKS[0]} 项：")
        for f in FAILED:
            print(f"  - {f}")
        return 1
    print(f"全部通过（{CHECKS[0]} 项）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
