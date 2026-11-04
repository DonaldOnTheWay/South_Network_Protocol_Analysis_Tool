# -*- coding: utf-8 -*-
"""档案管理「查询从节点信息」翻页回归（协议7 / 国网 10H F2）。

跑法：``python test/test_gdw_archive_query.py``（需要 offscreen Qt）。

现场问题：点一次只发一帧（起始序号=1、数量=50），被测模块单帧最多回 20 条，
实际 30 个节点只显示 20 个。本测试锁住改后的口径：

- A1 按响应里的「从节点总数量 / 本次应答数量」推进起始序号翻页（1/20 → 21/10）；
- A2 取全后表格/`_node_data`/状态栏/日志都对；
- A3 单页超时保留已收数据、不无限等待；
- A4 本次应答 0 条立即收尾；
- A5 2 字节节点信息解析进「电表协议 / 相位 / 其他信息」；
- A6 重复点按钮不叠加（重开第 1 页，不从残留页续）。
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
import gdw10376_parser as P  # noqa: E402

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


def uplink_node_info(nodes, total, count=None, afn=0x10, dt1=0x02, dt2=0x00):
    """手搓 10H F2 上行：总数量(2B) + 本次数量(1B) + n×[地址(6B 低字节在前) + 信息(2B)]。"""
    if count is None:
        count = len(nodes)
    data = int(total).to_bytes(2, "little") + bytes([int(count) & 0xFF])
    for addr, info in nodes:
        data += bytes.fromhex(str(addr).zfill(12))[::-1] + bytes(info)
    payload = bytes([0xC3]) + bytes.fromhex("000011ff02c2") + bytes([afn, dt1, dt2]) + data
    total_len = len(payload) + 5
    return (bytes([0x68, total_len & 0xFF, (total_len >> 8) & 0xFF]) + payload
            + bytes([sum(payload) & 0xFF, 0x16]))


def node_list(start, count, info=b"\x12\x0A"):
    return [(f"{start + i:012d}", info) for i in range(count)]


def sent_requests(fake):
    """已发帧 → [(起始序号, 数量), ...]（用解析器读回，不手数偏移）。"""
    out = []
    for hex_str in fake.sent:
        rows = {r[0].strip(): r for r in P.GDW10376Parser().parse_to_table(
            bytes.fromhex(hex_str.replace(" ", "")))}
        count_row = rows.get("从节点数量") or rows.get("节点数量")
        if "节点起始序号" in rows and count_row is not None:
            out.append((int(rows["节点起始序号"][2]), int(count_row[2])))
    return out


# ══════════════════════════════════════════════════════════════════════
def test_paging():
    section("A1/A2 按响应总数翻页取全（20 + 10 = 30）")
    app, w, fake = make_widget()
    try:
        w._on_query_node_info()
        app.processEvents()
        check("第 1 页：起始 1 / 数量 20", sent_requests(fake), [(1, 20)])
        check("清表后 0 行", w.table.rowCount(), 0)

        w._on_frame_received(uplink_node_info(node_list(1, 20), total=30))
        app.processEvents()
        check("收到 20/30 后自动发第 2 页（起始 21 / 数量 10）",
              sent_requests(fake), [(1, 20), (21, 10)])
        check("第 1 页追加后 20 行", w.table.rowCount(), 20)
        check_true("状态栏显示进度", "20/30" in w.status_label.text(), w.status_label.text())

        w._on_frame_received(uplink_node_info(node_list(21, 10), total=30))
        app.processEvents()
        check("取全后不再发第 3 页", sent_requests(fake), [(1, 20), (21, 10)])
        check("表格 30 行（不再是 20）", w.table.rowCount(), 30)
        check("_node_data 30 条", len(w._node_data), 30)
        check("首行地址", w.table.item(0, 2).text(), "000000000001")
        check("末行地址", w.table.item(29, 2).text(), "000000000030")
        check("末行序号 = 30", w.table.item(29, 1).text(), "30")
        check_true("状态栏 30/30", "30/30" in w.status_label.text(), w.status_label.text())
        log = w.serial_log.toPlainText()
        check_true("日志写明已取全", "已取全 30" in log, log[-300:])
        check_true("日志给出每页条数", "本次 20 条" in log and "本次 10 条" in log, log[-400:])
        check("翻页状态已清", w._node_query, {})
        check_true("落盘 30 条", len(w._node_data) == 30, len(w._node_data))
    finally:
        w.close()


def test_timeout():
    section("A3 单页超时：保留已收、不无限等待")
    app, w, fake = make_widget()
    try:
        w._on_query_node_info()
        w._on_frame_received(uplink_node_info(node_list(1, 20), total=30))
        app.processEvents()
        check("已发 2 帧", len(sent_requests(fake)), 2)
        w._on_node_query_timeout()
        app.processEvents()
        check("超时后保留已收 20 行", w.table.rowCount(), 20)
        log = w.serial_log.toPlainText()
        check_true("日志写明超时与进度", ("超时" in log) and ("20/30" in log), log[-300:])
        check_true("状态栏标「未取全」", "未取全" in w.status_label.text(),
                   w.status_label.text())
        check("超时后不再发新页", len(sent_requests(fake)), 2)
        check("翻页状态已清", w._node_query, {})
    finally:
        w.close()


def test_empty_page():
    section("A4 本次应答 0 条 → 立即收尾")
    app, w, fake = make_widget()
    try:
        w._on_query_node_info()
        w._on_frame_received(uplink_node_info([], total=30, count=0))
        app.processEvents()
        check("只发了第 1 页", len(sent_requests(fake)), 1)
        check("表格 0 行", w.table.rowCount(), 0)
        log = w.serial_log.toPlainText()
        check_true("日志写明设备返回 0 条", "0 条" in log, log[-300:])
        check("翻页状态已清", w._node_query, {})
    finally:
        w.close()


def test_detail_parse():
    section("A5 节点 2 字节信息解析（协议 / 相位 / 中继级别 / 信号品质）")
    app, w, _fake = make_widget()
    try:
        w._on_query_node_info()
        # b0=0x12 → 信号品质=1、中继级别=2；b1=0x0A → 相位=B相、协议=645-1997
        # b0=0x51 → 信号品质=5、中继级别=1；b1=0x1B → 相位=A,B；协议=698.45
        w._on_frame_received(uplink_node_info(
            [("000000000001", b"\x12\x0A"), ("000000000002", b"\x51\x1B")], total=2))
        app.processEvents()
        check("2 行", w.table.rowCount(), 2)
        check("第 3 列电表协议（645-1997）", w.table.item(0, 3).text(), "DL/T 645-1997")
        check("第 4 列相位（B相）", w.table.item(0, 4).text(), "B相")
        check("第 10 列其他信息（中继级别 + 信号品质）", w.table.item(0, 9).text(),
              "中继级别=2，侦听信号品质=1")
        check("第 2 行协议（698.45）", w.table.item(1, 3).text(), "DL/T 698.45")
        check("第 2 行相位（A,B）", w.table.item(1, 4).text(), "A相,B相")
        check("第 2 行其他信息", w.table.item(1, 9).text(),
              "中继级别=1，侦听信号品质=5")
        check("_node_data 带 relay/signal",
              (w._node_data[0].get("relay"), w._node_data[0].get("signal")), ("2", "1"))
        check("状态列「在线」", w.table.item(0, 5).text(), "在线")
    finally:
        w.close()


def test_repeat_click():
    section("A6 重复点按钮不叠加（重开第 1 页，不从残留页续）")
    app, w, fake = make_widget()
    try:
        w._on_query_node_info()
        w._on_frame_received(uplink_node_info(node_list(1, 20), total=30))
        app.processEvents()
        check("第一轮已到第 2 页", sent_requests(fake), [(1, 20), (21, 10)])

        fake.sent.clear()
        w._on_query_node_info()
        app.processEvents()
        check("第二轮从第 1 页重开（不是 31/0 之类残留）",
              sent_requests(fake), [(1, 20)])
        check("第二轮清表后 0 行", w.table.rowCount(), 0)

        w._on_frame_received(uplink_node_info(node_list(1, 20), total=30))
        app.processEvents()
        check("第二轮仍按 30 总数推进", sent_requests(fake), [(1, 20), (21, 10)])
        check("表格 20 行（未与上一轮混叠）", w.table.rowCount(), 20)
    finally:
        w.close()


def test_现场形态_与重复页():
    section("A7 现场形态：首页返回 11~30、第二页返回 1~10；重复页不死循环")
    app, w, fake = make_widget()
    try:
        # 现场实测：请求「起始 1 / 数量 50」，模块回的第 1 页是 11~30（20 条，总数 30）
        w._on_query_node_info()
        w._on_frame_received(uplink_node_info(node_list(11, 20), total=30))
        app.processEvents()
        check("仍是 20 条、继续取下一页", sent_requests(fake), [(1, 20), (21, 10)])
        check("已收 20", len(w._node_data), 20)
        # 第 2 页补回 1~10 → 去重后正好 30 个不同节点
        w._on_frame_received(uplink_node_info(node_list(1, 10), total=30))
        app.processEvents()
        check("两个窗口合并 = 30 个不同节点", len(w._node_data), 30)
        addrs = [w.table.item(r, 2).text() for r in range(w.table.rowCount())]
        check("地址互不重复", len(set(addrs)), 30)
        check_true("1~30 全在表里",
                   set(addrs) == {f"{i:012d}" for i in range(1, 31)}, sorted(addrs)[:5])
        check("翻页状态已清", w._node_query, {})

        # 设备不按起始序号翻页（第 2 页把第 1 页原样再发一遍）→ 必须停，不能无限问
        fake.sent.clear()
        w._on_query_node_info()
        w._on_frame_received(uplink_node_info(node_list(11, 20), total=30))
        app.processEvents()
        w._on_frame_received(uplink_node_info(node_list(11, 20), total=30))
        app.processEvents()
        check("重复页只发到第 2 页就停", len(sent_requests(fake)), 2)
        check("重复页不叠加行数", len(w._node_data), 20)
        check_true("日志告警「重复」", "重复" in w.serial_log.toPlainText(),
                   w.serial_log.toPlainText()[-300:])
        check("翻页状态已清", w._node_query, {})
    finally:
        w.close()


def test_protocol_switch_stops():
    section("A6b 切协议 / 初始化档案 / 掉线时停掉翻页")
    app, w, fake = make_widget()
    try:
        w._on_query_node_info()
        w._on_frame_received(uplink_node_info(node_list(1, 20), total=30))
        app.processEvents()
        w.set_protocol_mode("south")
        check("切协议后翻页状态清空", w._node_query, {})

        w.set_protocol_mode("gdw")
        w._on_query_node_info()
        app.processEvents()
        w._on_init_archive()
        check("初始化档案后翻页状态清空", w._node_query, {})

        w._on_query_node_info()
        app.processEvents()
        w._update_button_state(False)          # 串口断开
        check("掉线后翻页状态清空", w._node_query, {})
    finally:
        w.close()


def main():
    test_paging()
    test_timeout()
    test_empty_page()
    test_detail_parse()
    test_repeat_click()
    test_现场形态_与重复页()
    test_protocol_switch_stops()

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
