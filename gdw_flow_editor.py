# -*- coding: utf-8 -*-
"""国网（协议7）流程测试 —— **用例管理对话框**（Qt 层）。

职责
----
把 `gdw_test_flow_custom` 那层 JSON 用例变成可编辑的表单：

- 左侧用例列表（内置 9 条 + 文档补充 + 自定义，**只有自定义可改**，其余靠「复制」改造）；
- 右侧元信息（名称/标识/出处/说明）+ 步骤表 + 步骤属性表单；
- 顶部：新增用例 / 复制 / 删除 / 导入… / 导出…；底部：保存 / 取消。

约定
----
- **保存前一律过 `gdw_test_flow_custom.validate_case`**，有任何问题都只弹框列原因、
  **不写盘**（把非法用例悄悄改成别的意思，比拒绝保存危险）；
- 编辑是「即时写回 dict」+ 切换行/用例前 `_commit_*()`，不依赖控件焦点丢失顺序；
- 程序化填充表单时用 `self._loading` 屏蔽回写，避免把空值写回去；
- 按钮一律不写死宽度（`gui_utils.fit_btn_width`，本项目字号可配置，写死必裁字）。
"""

from __future__ import annotations

import copy
import json
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFileDialog,
    QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMessageBox, QPlainTextEdit, QPushButton, QSpinBox,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

import gdw_test_core as core
import gdw_test_flow_custom as custom
import gdw_test_flows as flows
from gui_utils import fit_btn_width

#: 步骤表列（与 `_fill_step_table` 一一对应）
STEP_HEADERS = ("#", "类型", "命令 / 等待", "超时(s)", "判定")


def _fid_auto(base: str, taken: List[str]) -> str:
    """生成不重复的 fid（``base`` / ``base_2`` / ``base_3`` …）。"""
    base = "".join(ch for ch in (base or "case") if ch.isalnum() or ch == "_") or "case"
    if base[0].isdigit():
        base = "c_" + base
    used = set(taken)
    if base not in used:
        return base
    idx = 2
    while f"{base}_{idx}" in used:
        idx += 1
    return f"{base}_{idx}"


class FlowCaseEditorDialog(QDialog):
    """流程用例编辑器（模态）。保存成功后发 ``saved``。"""

    saved = Signal()

    def __init__(self, parent=None, path: str = ""):
        super().__init__(parent)
        self._path = path or custom.store_path()
        self._loading = False
        self._case_index = -1
        self._step_index = -1
        self._errors: List[str] = []
        self.cases: List[Dict[str, Any]] = self._load()
        self.setWindowTitle("流程用例管理")
        self.resize(1040, 720)
        self._build_ui()
        self._reload_case_list()

    # ── 载入 / 落盘 ─────────────────────────────────────────────────────
    def _load(self) -> List[Dict[str, Any]]:
        """读自定义用例（保留原样 dict，含校验不通过的草稿，便于就地修）。"""
        path = self._path
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except FileNotFoundError:
            return []
        except (OSError, ValueError) as exc:
            self._errors.append(f"读取 {path} 失败：{exc}")
            return []
        raw = data.get("cases") if isinstance(data, dict) else None
        if not isinstance(raw, list):
            return []
        return [copy.deepcopy(item) for item in raw if isinstance(item, dict)]

    # ── UI ─────────────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        tip = QLabel(
            "内置 9 条与「文档补充/负面」用例是只读的：选中后点「复制」即可基于它改出自己的用例。\n"
            f"自定义用例存 {self._path}"
            + ("\n⚠ " + "；".join(self._errors) if self._errors else ""))
        tip.setWordWrap(True)
        tip.setStyleSheet("color:#6E7781;")
        root.addWidget(tip)

        root.addWidget(self._build_list_row(), 1)
        root.addWidget(self._build_meta_box())
        root.addLayout(self._build_bottom_row())

    def _build_list_row(self) -> QWidget:
        host = QWidget()
        lay = QHBoxLayout(host)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        left = QGroupBox("用例")
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(6, 4, 6, 4)
        self.list_cases = QListWidget()
        self.list_cases.setMinimumWidth(200)
        self.list_cases.currentRowChanged.connect(self._on_case_row_changed)
        left_lay.addWidget(self.list_cases, 1)
        left_lay.addLayout(self._case_buttons())
        lay.addWidget(left)

        lay.addWidget(self._build_step_box(), 1)
        return host

    def _case_buttons(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(4)
        self.btn_case_new = QPushButton("新增用例")
        self.btn_case_copy = QPushButton("复制")
        self.btn_case_del = QPushButton("删除")
        self.btn_case_import = QPushButton("导入…")
        self.btn_case_export = QPushButton("导出…")
        self.btn_case_new.clicked.connect(self._on_case_new)
        self.btn_case_copy.clicked.connect(self._on_case_copy)
        self.btn_case_del.clicked.connect(self._on_case_delete)
        self.btn_case_import.clicked.connect(self._on_import)
        self.btn_case_export.clicked.connect(self._on_export)
        buttons = (self.btn_case_new, self.btn_case_copy, self.btn_case_del,
                   self.btn_case_import, self.btn_case_export)
        fit_btn_width(*buttons)
        for btn in buttons:
            row.addWidget(btn)
        return row

    def _build_meta_box(self) -> QWidget:
        box = QGroupBox("用例信息")
        grid = QGridLayout(box)
        grid.setContentsMargins(6, 4, 6, 4)
        self.edit_name = QLineEdit()
        self.edit_fid = QLineEdit()
        self.edit_fid.setPlaceholderText("字母开头，只含字母/数字/下划线")
        self.edit_source = QLineEdit()
        self.edit_desc = QPlainTextEdit()
        self.edit_desc.setMaximumHeight(56)
        for w in (self.edit_name, self.edit_fid, self.edit_source):
            w.textEdited.connect(self._commit_case)
        self.edit_desc.textChanged.connect(self._commit_case)
        grid.addWidget(QLabel("名称"), 0, 0)
        grid.addWidget(self.edit_name, 0, 1)
        grid.addWidget(QLabel("标识(fid)"), 0, 2)
        grid.addWidget(self.edit_fid, 0, 3)
        grid.addWidget(QLabel("出处"), 1, 0)
        grid.addWidget(self.edit_source, 1, 1, 1, 3)
        grid.addWidget(QLabel("说明"), 2, 0, Qt.AlignmentFlag.AlignTop)
        grid.addWidget(self.edit_desc, 2, 1, 1, 3)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        return box

    def _build_step_box(self) -> QWidget:
        box = QGroupBox("步骤")
        lay = QVBoxLayout(box)
        lay.setContentsMargins(6, 4, 6, 4)
        lay.setSpacing(4)

        self.step_table = QTableWidget(0, len(STEP_HEADERS))
        self.step_table.setHorizontalHeaderLabels(list(STEP_HEADERS))
        self.step_table.verticalHeader().setVisible(False)
        self.step_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.step_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.step_table.setColumnWidth(0, 34)
        self.step_table.setColumnWidth(1, 130)
        self.step_table.setColumnWidth(2, 260)
        self.step_table.setColumnWidth(3, 70)
        self.step_table.horizontalHeader().setStretchLastSection(True)
        self.step_table.currentCellChanged.connect(self._on_step_row_changed)
        lay.addWidget(self.step_table, 1)

        row = QHBoxLayout()
        row.setSpacing(4)
        self.btn_step_new = QPushButton("新增步骤")
        self.btn_step_del = QPushButton("删除步骤")
        self.btn_step_up = QPushButton("上移")
        self.btn_step_down = QPushButton("下移")
        self.btn_step_new.clicked.connect(self._on_step_new)
        self.btn_step_del.clicked.connect(self._on_step_delete)
        self.btn_step_up.clicked.connect(lambda: self._move_step(-1))
        self.btn_step_down.clicked.connect(lambda: self._move_step(1))
        fit_btn_width(self.btn_step_new, self.btn_step_del,
                      self.btn_step_up, self.btn_step_down)
        for btn in (self.btn_step_new, self.btn_step_del, self.btn_step_up, self.btn_step_down):
            row.addWidget(btn)
        row.addStretch(1)
        lay.addLayout(row)

        lay.addWidget(self._build_step_form())
        return box

    def _build_step_form(self) -> QWidget:
        host = QWidget()
        grid = QGridLayout(host)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(4)

        self.combo_kind = QComboBox()
        for kind in custom.KINDS:
            self.combo_kind.addItem(custom.KIND_LABELS.get(kind, kind), kind)
        self.combo_cmd = QComboBox()
        self.combo_cmd.setMinimumWidth(240)
        self._fill_command_combo("")
        self.spin_afn = QSpinBox()
        self.spin_afn.setRange(-1, 255)
        self.spin_fn = QSpinBox()
        self.spin_fn.setRange(-1, 255)
        self.spin_timeout = QDoubleSpinBox()
        self.spin_timeout.setRange(0.1, 3600.0)
        self.spin_timeout.setDecimals(1)
        self.spin_timeout.setSingleStep(1.0)
        self.combo_check = QComboBox()
        for name in ("",) + custom.CHECKS:      # 第一项 = 不校验（空串）
            self.combo_check.addItem(custom.CHECK_LABELS.get(name, name or "不校验"), name)
        self.spin_denial = QSpinBox()
        self.spin_denial.setRange(-1, 255)
        self.spin_denial.setToolTip("-1 = 不预期否认；0~255 = 预期否认的错误状态字")
        self.edit_title = QLineEdit()
        self.edit_title.setPlaceholderText("这一步做什么（会显示在步骤表里）")
        self.edit_accept = QLineEdit()
        self.edit_accept.setPlaceholderText("额外接受的应答，如 14-3,14-1（空=只认命令本身）")
        self.edit_alt = QLineEdit()
        self.edit_alt.setPlaceholderText("备用命令，如 11-2（失败后按序改试）")
        self.edit_hint = QLineEdit()
        self.edit_hint.setPlaceholderText("提示（失败/超时时显示在「说明」列）")

        self.chk_slave = QCheckBox("需要从节点")
        self.chk_optional = QCheckBox("可选(失败不算)")
        self.chk_no_reply = QCheckBox("不等应答")
        self.chk_split = QCheckBox("按节点分帧")
        self.chk_concurrent = QCheckBox("并发连发（收到否认即停）")
        self.chk_concurrent.setToolTip(
            "按「从节点地址列」逐帧连发、不逐帧等应答；收到否认即停，\n"
            "并把「发到第几帧被否认」记为被测的最大并发数（必须填地址列）")
        self.edit_addrs = QLineEdit()
        self.edit_addrs.setPlaceholderText(
            "从节点地址列，如 000000000001-000000000100（留空 = 用组网参数的 1~2 个节点）")
        self.spin_batch = QSpinBox()
        self.spin_batch.setRange(0, custom.MAX_ADDRS)
        self.spin_batch.setToolTip("每帧里放几个地址：0/1 = 一个地址一帧；建档可填 10 分批")

        self.text_values = QPlainTextEdit()
        self.text_values.setMaximumHeight(52)
        self.text_values.setPlaceholderText('字段覆盖 JSON，如 {"节点数量": 0}（键必须在该命令字段表里）')
        self.text_sta_filter = QPlainTextEdit()
        self.text_sta_filter.setMaximumHeight(52)
        self.text_sta_filter.setPlaceholderText(
            'STA 过滤 JSON，如 {"域": "645", "控制码": 17, "DI": "0001"} 或 {"域": "698", "APDU": 136}')

        grid.addWidget(QLabel("类型"), 0, 0)
        grid.addWidget(self.combo_kind, 0, 1)
        grid.addWidget(QLabel("命令"), 0, 2)
        grid.addWidget(self.combo_cmd, 0, 3, 1, 3)
        grid.addWidget(QLabel("AFN"), 1, 0)
        grid.addWidget(self.spin_afn, 1, 1)
        grid.addWidget(QLabel("Fn"), 1, 2)
        grid.addWidget(self.spin_fn, 1, 3)
        grid.addWidget(QLabel("超时(s)"), 1, 4)
        grid.addWidget(self.spin_timeout, 1, 5)
        grid.addWidget(QLabel("判定"), 2, 0)
        grid.addWidget(self.combo_check, 2, 1)
        grid.addWidget(QLabel("预期否认码"), 2, 2)
        grid.addWidget(self.spin_denial, 2, 3)
        grid.addWidget(QLabel("标题"), 3, 0)
        grid.addWidget(self.edit_title, 3, 1, 1, 5)
        grid.addWidget(QLabel("接受应答"), 4, 0)
        grid.addWidget(self.edit_accept, 4, 1, 1, 2)
        grid.addWidget(QLabel("备用命令"), 4, 3)
        grid.addWidget(self.edit_alt, 4, 4, 1, 2)

        flags = QHBoxLayout()
        flags.setSpacing(8)
        for chk in (self.chk_slave, self.chk_optional, self.chk_no_reply,
                    self.chk_split, self.chk_concurrent):
            flags.addWidget(chk)
        flags.addStretch(1)
        grid.addLayout(flags, 5, 0, 1, 6)

        grid.addWidget(QLabel("字段覆盖"), 6, 0, Qt.AlignmentFlag.AlignTop)
        grid.addWidget(self.text_values, 6, 1, 1, 2)
        grid.addWidget(QLabel("STA 过滤"), 6, 3, Qt.AlignmentFlag.AlignTop)
        grid.addWidget(self.text_sta_filter, 6, 4, 1, 2)
        grid.addWidget(QLabel("提示"), 7, 0)
        grid.addWidget(self.edit_hint, 7, 1, 1, 5)
        grid.addWidget(QLabel("从节点地址列"), 8, 0)
        grid.addWidget(self.edit_addrs, 8, 1, 1, 3)
        grid.addWidget(QLabel("每帧个数"), 8, 4)
        grid.addWidget(self.spin_batch, 8, 5)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        grid.setColumnStretch(5, 1)

        self.combo_kind.currentIndexChanged.connect(self._commit_step)
        self.combo_cmd.currentIndexChanged.connect(self._commit_step)
        for spin in (self.spin_afn, self.spin_fn, self.spin_denial):
            spin.valueChanged.connect(self._commit_step)
        self.spin_timeout.valueChanged.connect(self._commit_step)
        self.combo_check.currentIndexChanged.connect(self._commit_step)
        for chk in (self.chk_slave, self.chk_optional, self.chk_no_reply,
                    self.chk_split, self.chk_concurrent):
            chk.toggled.connect(self._commit_step)
        self.edit_addrs.textEdited.connect(self._commit_step)
        self.spin_batch.valueChanged.connect(self._commit_step)
        for edit in (self.edit_title, self.edit_accept, self.edit_alt, self.edit_hint):
            edit.textEdited.connect(self._commit_step)
        self.text_values.textChanged.connect(self._commit_step)
        self.text_sta_filter.textChanged.connect(self._commit_step)
        return host

    def _build_bottom_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        self.lbl_status = QLabel("")
        self.lbl_status.setStyleSheet("color:#6E7781;")
        row.addWidget(self.lbl_status, 1)
        self.btn_save = QPushButton("保存")
        self.btn_cancel = QPushButton("取消")
        self.btn_save.clicked.connect(self._on_save)
        self.btn_cancel.clicked.connect(self.reject)
        fit_btn_width(self.btn_save, self.btn_cancel)
        row.addWidget(self.btn_save)
        row.addWidget(self.btn_cancel)
        return row

    # ── 命令下拉 ────────────────────────────────────────────────────────
    def _fill_command_combo(self, current: str) -> None:
        """命令下拉：AFN/Fn 全表（标准 + 自定义）；``current`` 不在表里也显示出来。"""
        self.combo_cmd.blockSignals(True)
        self.combo_cmd.clear()
        commands = list(core.all_commands()) + list(core.custom_commands())
        for cmd in commands:
            self.combo_cmd.addItem(f"{cmd.afn:02X}H F{cmd.fn} {cmd.name}", cmd.key)
        if current and self.combo_cmd.findData(current) < 0:
            self.combo_cmd.addItem(f"{current}（表里没有这条命令）", current)
        idx = self.combo_cmd.findData(current) if current else -1
        self.combo_cmd.setCurrentIndex(max(0, idx))
        self.combo_cmd.blockSignals(False)

    # ── 用例列表 ────────────────────────────────────────────────────────
    def _case_fids(self) -> List[str]:
        return [str(c.get("fid") or "") for c in self.cases]

    def _reload_case_list(self, select: Optional[int] = None) -> None:
        target = self._case_index if select is None else select
        self._loading = True
        try:
            self.list_cases.clear()
            for item in self.cases:
                qitem = QListWidgetItem(f"{item.get('name') or '(未命名)'}  ·  "
                                        f"{item.get('fid') or '?'}")
                qitem.setToolTip(str(item.get("desc") or ""))
                self.list_cases.addItem(qitem)
            if self.cases:
                target = max(0, min(len(self.cases) - 1, target))
                self.list_cases.setCurrentRow(target)     # 触发被 _loading 抑制
        finally:
            self._loading = False
        if not self.cases:
            self._case_index = -1
            self._fill_case_form(None)
            self._reload_step_table(0)
            self._update_status()
            return
        self._case_index = target
        self._fill_case_form(self._current_case())
        self._reload_step_table(0)
        self._update_status()

    def _on_case_row_changed(self, row: int) -> None:
        if self._loading:
            return
        self._commit_step()
        self._commit_case()
        self._case_index = row
        self._fill_case_form(self._current_case())
        self._reload_step_table(0)

    def _fill_case_form(self, case: Optional[Dict[str, Any]]) -> None:
        """把用例元信息填进控件（``case`` 为 None 时清空）。"""
        self._loading = True
        try:
            self.edit_name.setText(str((case or {}).get("name") or ""))
            self.edit_fid.setText(str((case or {}).get("fid") or ""))
            self.edit_source.setText(str((case or {}).get("source") or ""))
            self.edit_desc.setPlainText(str((case or {}).get("desc") or ""))
        finally:
            self._loading = False

    # ── 步骤表 ──────────────────────────────────────────────────────────
    def _current_case(self) -> Optional[Dict[str, Any]]:
        if 0 <= self._case_index < len(self.cases):
            return self.cases[self._case_index]
        return None

    def _current_steps(self) -> List[Dict[str, Any]]:
        case = self._current_case()
        return list(case.get("steps") or []) if case else []

    def _reload_step_table(self, select: int = 0) -> None:
        steps = self._current_steps()
        target = -1
        self._loading = True
        try:
            self.step_table.setRowCount(0)
            for idx, step in enumerate(steps, start=1):
                row = self.step_table.rowCount()
                self.step_table.insertRow(row)
                self.step_table.setItem(row, 0, QTableWidgetItem(str(idx)))
                self.step_table.setItem(row, 1, QTableWidgetItem(
                    custom.KIND_LABELS.get(str(step.get("kind")), str(step.get("kind")))))
                self.step_table.setItem(row, 2, QTableWidgetItem(self._step_target(step)))
                self.step_table.setItem(row, 3, QTableWidgetItem(
                    f"{float(step.get('timeout') or 0):g}"))
                self.step_table.setItem(row, 4, QTableWidgetItem(
                    custom.CHECK_LABELS.get(str(step.get("check") or ""),
                                            str(step.get("check") or ""))))
            if steps:
                target = max(0, min(len(steps) - 1, int(select)))
                self.step_table.setCurrentCell(target, 0)   # 触发被 _loading 抑制
        finally:
            self._loading = False
        self._step_index = target
        self._fill_step_form(steps[target] if 0 <= target < len(steps) else None)

    @staticmethod
    def _step_target(step: Dict[str, Any]) -> str:
        """步骤表「命令 / 等待」列的文案。"""
        kind = str(step.get("kind") or "")
        key = str(step.get("key") or "")
        if kind in (flows.KIND_SEND, flows.KIND_POLL, flows.KIND_SERVE) and key:
            addrs = step.get("addrs") or ()
            if step.get("concurrent") and addrs:
                return f"{key} ×{len(addrs)}（连发）"
            if addrs:
                each = max(1, int(step.get("batch") or 1))
                frames = (len(addrs) + each - 1) // each
                return f"{key} ×{frames} 帧/{len(addrs)} 地址"
            return key
        if kind == flows.KIND_WAIT:
            return f"{int(step.get('afn', -1)):02X}H F{int(step.get('fn', -1))}"
        if kind == flows.KIND_EXPECT_STA:
            flt = step.get("sta_filter") or {}
            return "、".join(f"{k}={v}" for k, v in flt.items()) or "任意 645/698 帧"
        if kind == flows.KIND_SILENCE:
            return "静默窗口"
        return "—"

    def _on_step_row_changed(self, row: int, *_args) -> None:
        if self._loading:
            return
        self._commit_step()
        self._step_index = row
        steps = self._current_steps()
        self._fill_step_form(steps[row] if 0 <= row < len(steps) else None)

    def _fill_step_form(self, step: Optional[Dict[str, Any]]) -> None:
        self._loading = True
        try:
            kind = str((step or {}).get("kind") or flows.KIND_SEND)
            idx = self.combo_kind.findData(kind)
            self.combo_kind.setCurrentIndex(max(0, idx))
            self._fill_command_combo(str((step or {}).get("key") or ""))
            self.spin_afn.setValue(int((step or {}).get("afn", -1)))
            self.spin_fn.setValue(int((step or {}).get("fn", -1)))
            self.spin_timeout.setValue(float((step or {}).get("timeout", 6.0) or 6.0))
            check = str((step or {}).get("check") or "")
            self.combo_check.setCurrentIndex(max(0, self.combo_check.findData(check)))
            self.spin_denial.setValue(self._denial_value(step))
            self.edit_title.setText(str((step or {}).get("title") or ""))
            self.edit_accept.setText(self._pairs_text((step or {}).get("accept")))
            self.edit_alt.setText(",".join(str(x) for x in ((step or {}).get("alt_keys") or ())))
            self.edit_hint.setText(str((step or {}).get("hint") or ""))
            self.chk_slave.setChecked(bool((step or {}).get("needs_slave")))
            self.chk_optional.setChecked(bool((step or {}).get("optional")))
            self.chk_no_reply.setChecked(bool((step or {}).get("no_reply")))
            self.chk_split.setChecked(bool((step or {}).get("split_nodes")))
            self.chk_concurrent.setChecked(bool((step or {}).get("concurrent")))
            self.edit_addrs.setText(custom.format_addrs((step or {}).get("addrs") or ()))
            self.spin_batch.setValue(int((step or {}).get("batch") or 0))
            self.text_values.setPlainText(self._json_text((step or {}).get("values")))
            self.text_sta_filter.setPlainText(
                self._json_text((step or {}).get("sta_filter")))
            enabled = step is not None
            for widget in (self.combo_kind, self.combo_cmd, self.spin_afn, self.spin_fn,
                           self.spin_timeout, self.combo_check, self.spin_denial,
                           self.edit_title, self.edit_accept, self.edit_alt, self.edit_hint,
                           self.chk_slave, self.chk_optional, self.chk_no_reply,
                           self.chk_split, self.chk_concurrent, self.edit_addrs,
                           self.spin_batch, self.text_values, self.text_sta_filter):
                widget.setEnabled(enabled)
        finally:
            self._loading = False

    @staticmethod
    def _denial_value(step: Optional[Dict[str, Any]]) -> int:
        """``expect_denial`` → 单个码（序列取第一个；不预期 → -1）。"""
        value = (step or {}).get("expect_denial")
        if value is None:
            return -1
        if isinstance(value, (list, tuple)):
            for item in value:
                try:
                    return int(item)
                except (TypeError, ValueError):
                    continue
            return -1
        try:
            return int(value)
        except (TypeError, ValueError):
            return -1

    @staticmethod
    def _pairs_text(value: Any) -> str:
        out = []
        for item in value or ():
            try:
                out.append(f"{int(item[0])}-{int(item[1])}")
            except (TypeError, ValueError, IndexError):
                continue
        return ",".join(out)

    @staticmethod
    def _json_text(value: Any) -> str:
        if not value:
            return ""
        try:
            return json.dumps(value, ensure_ascii=False)
        except (TypeError, ValueError):
            return ""

    # ── 回写 ────────────────────────────────────────────────────────────
    def _commit_case(self, *_args) -> None:
        """把元信息控件写回当前用例 dict。"""
        if self._loading:
            return
        case = self._current_case()
        if case is None:
            return
        case["name"] = self.edit_name.text().strip()
        case["fid"] = self.edit_fid.text().strip()
        case["source"] = self.edit_source.text().strip()
        case["desc"] = self.edit_desc.toPlainText()
        row = self._case_index
        if 0 <= row < self.list_cases.count():
            self._loading = True
            try:
                self.list_cases.item(row).setText(
                    f"{case.get('name') or '(未命名)'}  ·  {case.get('fid') or '?'}")
            finally:
                self._loading = False

    def _commit_step(self, *_args) -> None:
        """把步骤表单写回当前步骤 dict（非法 JSON 原样保留为字符串，保存时校验拦下）。"""
        if self._loading:
            return
        steps = self._current_steps()
        if not (0 <= self._step_index < len(steps)):
            return
        step = steps[self._step_index]
        step["kind"] = self.combo_kind.currentData() or flows.KIND_SEND
        step["key"] = self.combo_cmd.currentData() or ""
        step["afn"] = int(self.spin_afn.value())
        step["fn"] = int(self.spin_fn.value())
        step["timeout"] = float(self.spin_timeout.value())
        step["check"] = self.combo_check.currentData() or ""
        denial = int(self.spin_denial.value())
        step["expect_denial"] = None if denial < 0 else denial
        step["title"] = self.edit_title.text().strip()
        step["hint"] = self.edit_hint.text().strip()
        step["accept"] = self._parse_pairs(self.edit_accept.text())
        step["alt_keys"] = [x.strip() for x in self.edit_alt.text().split(",") if x.strip()]
        step["needs_slave"] = self.chk_slave.isChecked()
        step["optional"] = self.chk_optional.isChecked()
        step["no_reply"] = self.chk_no_reply.isChecked()
        step["split_nodes"] = self.chk_split.isChecked()
        step["concurrent"] = self.chk_concurrent.isChecked()
        step["addrs"] = custom.parse_addrs(self.edit_addrs.text())
        step["batch"] = int(self.spin_batch.value())
        step["values"] = self._parse_json(self.text_values.toPlainText(), {})
        step["sta_filter"] = self._parse_json(self.text_sta_filter.toPlainText(), {})
        case = self._current_case()
        if case is not None:
            case["steps"] = steps
        self._refresh_step_row(self._step_index, step)

    @staticmethod
    def _parse_pairs(text: str) -> List[List[int]]:
        out: List[List[int]] = []
        for chunk in str(text or "").replace("，", ",").split(","):
            part = chunk.strip().replace(" ", "")
            if not part:
                continue
            for sep in ("-", ":", "/"):
                if sep in part:
                    left, _, right = part.partition(sep)
                    try:
                        out.append([int(left, 0), int(right, 0)])
                    except ValueError:
                        pass
                    break
        return out

    @staticmethod
    def _parse_json(text: str, default: Any) -> Any:
        raw = str(text or "").strip()
        if not raw:
            return default
        try:
            value = json.loads(raw)
        except ValueError:
            return raw          # 留着原文，保存时由 validate_step 报「必须是对象」
        return value

    def _refresh_step_row(self, row: int, step: Dict[str, Any]) -> None:
        if not (0 <= row < self.step_table.rowCount()):
            return
        self._loading = True
        try:
            self.step_table.setItem(row, 1, QTableWidgetItem(
                custom.KIND_LABELS.get(str(step.get("kind")), str(step.get("kind")))))
            self.step_table.setItem(row, 2, QTableWidgetItem(self._step_target(step)))
            self.step_table.setItem(row, 3, QTableWidgetItem(
                f"{float(step.get('timeout') or 0):g}"))
            self.step_table.setItem(row, 4, QTableWidgetItem(
                custom.CHECK_LABELS.get(str(step.get("check") or ""),
                                        str(step.get("check") or ""))))
        finally:
            self._loading = False

    # ── 用例操作 ────────────────────────────────────────────────────────
    def _on_case_new(self) -> None:
        self._commit_step()
        self._commit_case()
        fid = _fid_auto("my_case", self._case_fids())
        self.cases.append(custom.new_case_dict(fid, f"我的用例 {len(self.cases) + 1}"))
        self._case_index = len(self.cases) - 1
        self._reload_case_list(select=self._case_index)

    def _on_case_copy(self) -> None:
        case = self._current_case()
        if case is None:
            QMessageBox.information(self, "复制用例", "先在左侧选中一条用例")
            return
        self._commit_step()
        self._commit_case()
        clone = copy.deepcopy(case)
        clone["fid"] = _fid_auto(str(case.get("fid") or "case") + "_copy", self._case_fids())
        clone["name"] = f"{case.get('name') or '用例'}（副本）"
        self.cases.append(clone)
        self._case_index = len(self.cases) - 1
        self._reload_case_list(select=self._case_index)

    def _on_case_delete(self) -> None:
        case = self._current_case()
        if case is None:
            return
        if QMessageBox.question(
                self, "删除用例",
                f"删除「{case.get('name') or case.get('fid')}」？保存后生效。") != QMessageBox.StandardButton.Yes:
            return
        self.cases.pop(self._case_index)
        self._case_index = min(self._case_index, len(self.cases) - 1)
        self._reload_case_list(select=max(0, self._case_index))

    def _on_import(self) -> None:
        path, _flt = QFileDialog.getOpenFileName(self, "导入用例", "", "JSON (*.json)")
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "导入用例", f"读取失败：{exc}")
            return
        raw = data.get("cases") if isinstance(data, dict) else data
        if not isinstance(raw, list):
            QMessageBox.warning(self, "导入用例", "文件里没有 cases 数组")
            return
        builtin_ids = [c.fid for c in flows.builtin_cases()]
        errors: List[str] = []
        accepted: List[Dict[str, Any]] = []
        taken = self._case_fids()
        for idx, item in enumerate(raw, start=1):
            if not isinstance(item, dict):
                errors.append(f"第 {idx} 条：不是对象")
                continue
            reasons = custom.validate_case(item, builtin_ids)
            if reasons:
                errors.append(f"第 {idx} 条（{item.get('fid')}）：" + "；".join(reasons))
                continue
            if str(item.get("fid")) in taken:
                errors.append(f"第 {idx} 条：标识 {item.get('fid')} 与已有用例重复")
                continue
            taken.append(str(item.get("fid")))
            accepted.append(copy.deepcopy(item))
        if errors:
            # 有一条不合法就整批拒绝 —— 半批导入会让用户分不清哪条进来了
            QMessageBox.warning(self, "导入用例",
                                "以下条目未通过校验，本次**未导入任何用例**：\n\n"
                                + "\n".join(errors[:12]))
            return
        self.cases.extend(accepted)
        self._case_index = len(self.cases) - 1
        self._reload_case_list(select=self._case_index)

    def _on_export(self) -> None:
        self._commit_step()
        self._commit_case()
        if not self.cases:
            QMessageBox.information(self, "导出用例", "没有可导出的自定义用例")
            return
        path, _flt = QFileDialog.getSaveFileName(self, "导出用例",
                                                 "gdw_test_flow_custom.json", "JSON (*.json)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"version": custom.FILE_VERSION, "cases": self.cases},
                          fh, ensure_ascii=False, indent=2)
        except OSError as exc:
            QMessageBox.warning(self, "导出用例", f"写入失败：{exc}")
            return
        self._update_status(f"已导出到 {path}")

    # ── 步骤操作 ────────────────────────────────────────────────────────
    def _require_case(self) -> bool:
        if self._current_case() is None:
            QMessageBox.information(self, "步骤", "先新增或选中一条用例")
            return False
        return True

    def _on_step_new(self) -> None:
        if not self._require_case():
            return
        self._commit_step()
        steps = self._current_steps()
        steps.append(custom.new_step_dict())
        case = self._current_case()
        if case is not None:
            case["steps"] = steps
        self._reload_step_table(len(steps) - 1)
        self._update_status()

    def _on_step_delete(self) -> None:
        steps = self._current_steps()
        if not (0 <= self._step_index < len(steps)):
            return
        self._loading = True
        steps.pop(self._step_index)
        case = self._current_case()
        if case is not None:
            case["steps"] = steps
        self._loading = False
        self._step_index = max(0, self._step_index - 1)
        self._reload_step_table(self._step_index)
        self._update_status()

    def _move_step(self, delta: int) -> None:
        steps = self._current_steps()
        idx = self._step_index
        target = idx + int(delta)
        if not (0 <= idx < len(steps)) or not (0 <= target < len(steps)):
            return
        self._commit_step()
        steps[idx], steps[target] = steps[target], steps[idx]
        case = self._current_case()
        if case is not None:
            case["steps"] = steps
        self._reload_step_table(target)
        self._update_status()

    # ── 保存 ────────────────────────────────────────────────────────────
    def _update_status(self, extra: str = "") -> None:
        head = f"共 {len(self.cases)} 条自定义用例"
        if self._current_case() is not None:
            head += f" · 当前 {len(self._current_steps())} 步"
        self.lbl_status.setText(head + (f" · {extra}" if extra else ""))

    def _on_save(self) -> None:
        self._commit_step()
        self._commit_case()
        builtin_ids = [c.fid for c in flows.builtin_cases()]
        errors: List[str] = []
        seen: Dict[str, int] = {}
        for idx, case in enumerate(self.cases, start=1):
            fid = str(case.get("fid") or "")
            if fid and fid in seen:
                errors.append(f"第 {idx} 条（{fid}）：标识与第 {seen[fid]} 条重复")
            if fid:
                seen[fid] = idx
            for reason in custom.validate_case(case, builtin_ids):
                errors.append(f"第 {idx} 条（{fid or '无标识'}）：{reason}")
        if errors:
            QMessageBox.warning(self, "用例校验未通过",
                                "以下问题需要先修正，本次**未写入文件**：\n\n"
                                + "\n".join(errors[:15])
                                + ("\n…" if len(errors) > 15 else ""))
            return
        try:
            custom.save_cases(self.cases, self._path)
        except OSError as exc:
            QMessageBox.warning(self, "保存失败", f"写 {self._path} 失败：{exc}")
            return
        self.saved.emit()
        self.accept()


__all__ = ["FlowCaseEditorDialog", "STEP_HEADERS"]
