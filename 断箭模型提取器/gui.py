r"""断箭模型提取器 · 图形界面（暗色默认）。

用法：双击 `提取器.pyw`（无参数时起 GUI）；命令行仍走 `ba_extract.py`。⛔ 不再提供 .cmd。

布局：左＝模型名单（可搜）· 右＝定位与提取 · 下＝日志。
⛔ 本界面只读游戏：不写游戏目录、不改包、不装机。
"""

from __future__ import annotations

import json
import sys
import os
from pathlib import Path

#: 记住窗口最大化 —— 用户 2026-11-02：「每次要点一遍最大化很烦」。只存 UI 偏好。
UI_STATE_FILE = Path(__file__).resolve().parent / "var" / "ui_state.json"


def load_ui_state() -> dict:
    try:
        data = json.loads(UI_STATE_FILE.read_text(encoding="utf-8"))
        return dict(data) if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def save_ui_state(state: dict) -> None:
    try:
        UI_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        UI_STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass

from PySide6 import QtCore, QtGui, QtWidgets

import ba_extract as core

QSS = """
QWidget { background: #17191c; color: #d7dae0; font-family: "Microsoft YaHei UI", "Segoe UI"; font-size: 13px; }
QLineEdit, QTableWidget, QPlainTextEdit, QComboBox {
    background: #1f2226; border: 1px solid #2c3036; border-radius: 6px; padding: 4px;
    selection-background-color: #2f6feb;
}
QLineEdit:focus, QComboBox:focus { border-color: #3b82f6; }
QPushButton { background: #2a2e34; border: 1px solid #343941; border-radius: 6px; padding: 6px 14px; }
QPushButton:hover { background: #333941; }
QPushButton:disabled { color: #6b7280; background: #212429; }
QPushButton#primary { background: #2f6feb; border-color: #2f6feb; color: #ffffff; font-weight: 600; }
QPushButton#primary:hover { background: #3b7cf0; }
QHeaderView::section { background: #1f2226; color: #9aa3ad; border: none; padding: 6px; }
QTableWidget { gridline-color: #262a30; }
QTableWidget::item:selected { background: #2f6feb; color: #ffffff; }
QGroupBox { border: 1px solid #2c3036; border-radius: 8px; margin-top: 10px; padding: 10px; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; color: #9aa3ad; }
QLabel#hint { color: #8b949e; }
QLabel#ok { color: #4ade80; }
QLabel#bad { color: #f87171; }
QStatusBar { color: #8b949e; }
QCheckBox::indicator { width: 15px; height: 15px; border-radius: 3px; border: 1px solid #3a4048; background: #1f2226; }
QCheckBox::indicator:checked { background: #2f6feb; border-color: #2f6feb; }
"""


class Worker(QtCore.QThread):
    """把一个函数丢到后台跑（提取/定位都可能要几秒）。"""

    done = QtCore.Signal(object)

    def __init__(self, fn):
        super().__init__()
        self._fn = fn

    def run(self):
        try:
            out = self._fn()
        except Exception as e:  # noqa: BLE001 - 界面不该被异常带崩
            out = {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
        self.done.emit(out)


class Main(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("断箭模型提取器  v" + core.VERSION)
        self.resize(1120, 720)
        self.rows: list[dict] = []
        self._worker = None
        self.repo = core.find_repo()
        self.py = core.find_python(self.repo)
        self.game = core.game_root(None)
        self._build()
        self._startup()

    def closeEvent(self, event):
        state = load_ui_state()
        state["maximized"] = bool(self.windowState() & QtCore.Qt.WindowState.WindowMaximized)
        save_ui_state(state)
        super().closeEvent(event)

    # ---------------- 界面 ----------------
    def _build(self):
        root = QtWidgets.QWidget()
        self.setCentralWidget(root)
        v = QtWidgets.QVBoxLayout(root)
        v.setContentsMargins(12, 12, 12, 12)
        v.setSpacing(8)

        bar = QtWidgets.QHBoxLayout()
        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText("搜模型：键 或 显示名（例 M113 / T-72 / Stryker）")
        self.search.textChanged.connect(self._refill)
        self.kind = QtWidgets.QComboBox()
        self.kind.addItems(["单位", "炮塔", "全部"])
        self.kind.currentIndexChanged.connect(self._refill)
        self.btn_reload = QtWidgets.QPushButton("刷新名单")
        self.btn_reload.clicked.connect(self._load_rows)
        bar.addWidget(self.search, 1)
        bar.addWidget(self.kind)
        bar.addWidget(self.btn_reload)
        v.addLayout(bar)

        split = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        v.addWidget(split, 1)

        self.table = QtWidgets.QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["键（提取用）", "显示名", "类型"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
        self.table.itemSelectionChanged.connect(self._on_pick)
        self.table.doubleClicked.connect(lambda _i: self._extract())
        split.addWidget(self.table)

        side = QtWidgets.QWidget()
        sv = QtWidgets.QVBoxLayout(side)
        sv.setContentsMargins(0, 0, 0, 0)
        sv.setSpacing(8)

        box = QtWidgets.QGroupBox("这个模型落在哪")
        bv = QtWidgets.QVBoxLayout(box)
        self.lb_key = QtWidgets.QLabel("（左边选一个）")
        self.lb_key.setStyleSheet("font-weight:600; font-size:14px;")
        self.lb_path = QtWidgets.QLabel("")
        self.lb_path.setObjectName("hint")
        self.lb_path.setWordWrap(True)
        self.lb_bundle = QtWidgets.QLabel("")
        self.lb_bundle.setObjectName("hint")
        self.lb_bundle.setWordWrap(True)
        bv.addWidget(self.lb_key)
        bv.addWidget(self.lb_path)
        bv.addWidget(self.lb_bundle)
        sv.addWidget(box)

        out_box = QtWidgets.QGroupBox("提取到哪")
        ov = QtWidgets.QVBoxLayout(out_box)
        self.out = QtWidgets.QLineEdit()
        self.out.setPlaceholderText("留空 = 输出\\<键>")
        ov.addWidget(self.out)
        row = QtWidgets.QHBoxLayout()
        self.cb_lod0 = QtWidgets.QCheckBox("只要 LOD0")
        self.cb_lod0.setToolTip("只建 LOD0 的渲染器：快，但 LOD1/2 与部分改装件会缺")
        self.cb_blend = QtWidgets.QCheckBox("出 .blend")
        self.cb_blend.setChecked(True)
        row.addWidget(self.cb_lod0)
        row.addWidget(self.cb_blend)
        row.addStretch(1)
        ov.addLayout(row)

        # ★ 装配：炮塔 / 装甲件显隐 / 预览图（全部按数据库算，不靠手填）
        row2 = QtWidgets.QHBoxLayout()
        self.cb_turrets = QtWidgets.QCheckBox("装配炮塔")
        self.cb_turrets.setChecked(True)
        self.cb_turrets.setToolTip("按 TurretUnits/Turrets 把默认炮塔导进来并挂到 turret_N")
        self.cb_armor = QtWidgets.QCheckBox("按游戏处理装甲件")
        self.cb_armor.setChecked(True)
        self.cb_armor.setToolTip("按 UnitArmors/Armors 点亮该显示的那套，其余保持隐藏")
        row2.addWidget(self.cb_turrets)
        row2.addWidget(self.cb_armor)
        row2.addStretch(1)
        ov.addLayout(row2)

        row3 = QtWidgets.QHBoxLayout()
        self.cb_showall = QtWidgets.QCheckBox("全显示（含备用装甲件）")
        self.cb_showall.setToolTip("默认按游戏；勾上则所有隐藏件都点亮")
        row3.addWidget(self.cb_showall)
        row3.addStretch(1)
        ov.addLayout(row3)
        self.btn_go = QtWidgets.QPushButton("提  取")
        self.btn_go.setObjectName("primary")
        self.btn_go.setMinimumHeight(38)
        self.btn_go.clicked.connect(self._extract)
        ov.addWidget(self.btn_go)
        self.btn_open = QtWidgets.QPushButton("打开输出文件夹")
        self.btn_open.setEnabled(False)
        self.btn_open.clicked.connect(self._open_out)
        ov.addWidget(self.btn_open)
        sv.addWidget(out_box)
        sv.addStretch(1)
        split.addWidget(side)
        split.setSizes([700, 400])

        self.log = QtWidgets.QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(400)
        self.log.setFixedHeight(150)
        v.addWidget(self.log)

        self.status = QtWidgets.QStatusBar()
        self.setStatusBar(self.status)
        self._last_out: Path | None = None

    # ---------------- 数据 ----------------
    def _startup(self):
        ok = bool(self.repo and self.game)
        self._log("仓库：" + (str(self.repo) if self.repo else "✗ 找不到"))
        self._log("游戏：" + (str(self.game) if self.game else "✗ 找不到"))
        self._log("Blender：" + str(core.find_blender() or "✗ 找不到"))
        if not ok:
            self.status.showMessage("环境不全，见上面日志（可以设 BA_GAME / BA_REPO）")
            return
        self._load_rows()

    def _load_rows(self):
        if not self.repo:
            return
        dd = core.data_dir(self.repo)
        if dd is None:
            self._log("✗ 找不到 DataBaseCompiled")
            return
        rows = []
        for u in core.load_rows(dd, "Units"):
            if u.get("ModelFileName"):
                rows.append({"kind": "unit", "key": u["ModelFileName"], "id": u.get("Id"),
                             "label": u.get("HUDName") or u.get("Name") or ""})
        for t in core.load_rows(dd, "Turrets"):
            if t.get("ModelFileName"):
                rows.append({"kind": "turret", "key": t["ModelFileName"], "id": t.get("Id"),
                             "label": t.get("Name") or ""})
        seen, uniq = set(), []
        for r in rows:
            if r["key"] in seen:
                continue
            seen.add(r["key"])
            uniq.append(r)
        self.rows = uniq
        self._log("名单 %d 个模型（%s）" % (len(uniq), dd.name))
        self.status.showMessage("%d 个模型可提" % len(uniq))
        self._refill()

    def _filtered(self) -> list[dict]:
        text = self.search.text().strip().lower()
        idx = self.kind.currentIndex()
        want = None if idx == 2 else ("unit" if idx == 0 else "turret")
        out = []
        for r in self.rows:
            if want and r["kind"] != want:
                continue
            if text and text not in r["key"].lower() and text not in str(r["label"]).lower():
                continue
            out.append(r)
        return out

    def _refill(self):
        rows = self._filtered()
        self.table.setRowCount(0)
        for r in rows[:1500]:
            i = self.table.rowCount()
            self.table.insertRow(i)
            self.table.setItem(i, 0, QtWidgets.QTableWidgetItem(r["key"]))
            self.table.setItem(i, 1, QtWidgets.QTableWidgetItem(str(r["label"])))
            self.table.setItem(i, 2, QtWidgets.QTableWidgetItem("单位" if r["kind"] == "unit" else "炮塔"))
        self.status.showMessage("显示 %d 个（共 %d）" % (min(len(rows), 1500), len(rows)))

    def _current(self) -> dict | None:
        i = self.table.currentRow()
        if i < 0:
            return None
        key = self.table.item(i, 0).text()
        for r in self.rows:
            if r["key"] == key:
                return r
        return None

    # ---------------- 动作 ----------------
    def _on_pick(self):
        row = self._current()
        if not row:
            return
        self.lb_key.setText(row["key"])
        self.lb_path.setText("… 查 catalog")
        self.lb_bundle.setText("")
        self.btn_open.setEnabled(False)
        if not (self.repo and self.game):
            return

        def work():
            raw = core.catalog_info(self.py, self.repo, self.game, row["key"])
            return core.summarize_info(raw)

        w = Worker(work)
        w.done.connect(self._show_info)
        self._hold(w)

    def _show_info(self, info):
        if not info.get("ok"):
            self.lb_path.setText("✗ " + str(info.get("error") or info.get("stderr") or "catalog 里没有这个键"))
            return
        self.lb_path.setText(info["asset_path"])
        self.lb_bundle.setText("主包：%s\n依赖组：%d 个包" % (info["main_bundle"], info["bundle_count"]))

    def _extract(self):
        row = self._current()
        if not row:
            self.status.showMessage("先在左边选一个模型")
            return
        if not (self.repo and self.game):
            self.status.showMessage("环境不全：缺仓库或游戏")
            return
        key = row["key"]
        out = Path(self.out.text().strip()) if self.out.text().strip() else (core.OUT_DIR / key.replace("/", "_"))
        self.btn_go.setEnabled(False)
        self.btn_go.setText("提取中…")
        self._log("…… 开始提取 %s" % key)

        plan = core.unit_plan(
            core.data_dir(self.repo) if self.repo else None,
            key,
            turrets=self.cb_turrets.isChecked(),
            armor=self.cb_armor.isChecked(),
            show_all=self.cb_showall.isChecked(),
        )

        def work():
            return core.run_extract(self.py, self.repo, self.game, key, out,
                                    self.cb_lod0.isChecked(), self.cb_blend.isChecked(), True,
                                    plan=plan)

        w = Worker(work)
        w.done.connect(lambda r: self._extract_done(r, out))
        self._hold(w)

    def _extract_done(self, r, out: Path):
        self.btn_go.setEnabled(True)
        self.btn_go.setText("提  取")
        if not r.get("ok"):
            self._log("✗ " + str(r.get("error")))
            if r.get("tail"):
                self._log(r["tail"][-600:])
            self.status.showMessage("提取失败")
            return
        self._log("✅ %s" % out)
        self._log("   网格 %d · 骨架 %d · 材质 %d · 贴图 %d · 用时 %.1fs" % (
            r["meshes"], r["armatures"], r["materials"], r["images"], r["seconds"]))
        for name, size in r["files"]:
            self._log("   %-12s %.1f MB" % (name, size / 1e6))
        self._last_out = out
        self.btn_open.setEnabled(True)
        self.status.showMessage("提取完成：" + str(out))

    def _open_out(self):
        if self._last_out and self._last_out.exists():
            os.startfile(str(self._last_out))  # noqa: S606 - 只是打开资源管理器

    def _hold(self, w: Worker):
        self._worker = w
        w.start()

    def _log(self, text: str):
        self.log.appendPlainText(text)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    smoke = "--smoke" in argv
    app = QtWidgets.QApplication([a for a in argv if a != "--smoke"] or ["ba-extract"])
    app.setStyleSheet(QSS)
    font = QtGui.QFont("Microsoft YaHei UI", 10)
    app.setFont(font)
    win = Main()
    if load_ui_state().get("maximized"):
        win.showMaximized()
    else:
        win.show()
    if smoke:
        QtCore.QTimer.singleShot(1200, app.quit)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
