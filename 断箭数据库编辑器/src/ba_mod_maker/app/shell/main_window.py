"""主窗口外壳。

外壳 = 顶部导航 + 内容区 + 状态栏。**真正的功能都在 app/screens 与 app/widgets 里**，
外壳只负责把它们摆到一起、把动作接起来。

主屏是**军械库**（见 docs/adr/0002-主屏-军械库视图.md）。
数据表原样保留：主屏只做概览 + 精选字段，
Units 31 列 / Options 46 列 / Ammunitions 67 列的精修还是表格的活。
检索页照旧产品那套查法重做（一个框：名字 / HUD 名 / UIName / 美术文件名 / Id，
输入即筛、回车直接打开那一行）—— 见 app/widgets/global_search.py。

界面尺寸全部从"屏幕可用区"推，不写死：
1080p 上写死 1600x980 会被窗口管理器裁掉右侧和下侧。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QByteArray, QSettings, Qt, QTimer
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ba_mod_maker import __version__
from ba_mod_maker.app import diag
from ba_mod_maker.app.screens import ArmoryScreen, CountryPage, SpecPage
from ba_mod_maker.app.screens.editor import UnitEditorScreen
from ba_mod_maker.app.screens.editor.bench_page import KIND_TIPS
from ba_mod_maker.app.shell.edit_session import EditSession
from ba_mod_maker.app.theme import BackdropWidget
from ba_mod_maker.app.theme.icons import prefetch_status_text
from ba_mod_maker.app.theme.tokens import TOKENS
from ba_mod_maker.app.widgets import (
    BreadcrumbBar,
    GlobalSearch,
    ReferencedByPanel,
    SavePill,
    TableBrowser,
    TableWriteChannel,
    row_id_of,
)
from ba_mod_maker.core.data import Dataset, Instance, load_dataset
from ba_mod_maker.core.edit.clone import add_row as core_add_row
from ba_mod_maker.core.edit.refs import CellRef
from ba_mod_maker.core.errors import BaModMakerError
from ba_mod_maker.core.session import SourceKind

# 页码常量必须与 _tabs 的下标一一对应，也必须与顶栏页签的顺序一一对应
# （tests/test_shell_menu.py 守着）。2026-09-30 界面重做：**五个**页签，
# 国家 / 专精 两张分类表插在「军械库」与「数据表」中间（用户口径）。
PAGE_ARMORY = 0
PAGE_COUNTRY = 1
PAGE_SPEC = 2
PAGE_TABLE = 3
PAGE_SEARCH = 4
#: 单位编辑器（双击军械库上的一张卡进入）。**不进导航栏** ——
#: 它是军械库的一个状态，只能靠双击进、靠「← 返回军械库」出。
PAGE_UNIT_EDITOR = 5

#: 预取跑完之后，"1519 张全部就绪"这句在状态栏留多久（毫秒）。
#: 留一句是为了让"刚才那阵忙活结束了"有个交代；一直留着就变成噪音了。
_ICON_DONE_HOLD_MS = 6000

#: 顶栏页签。**顺序即页码常量**（PAGE_ARMORY..PAGE_SEARCH），
#: 也是 tests/test_shell_menu.py 断言的那一份。
NAV_PAGES: tuple[str, ...] = ("军械库", "国家", "专精", "数据表", "检索")

#: 导航树里的分组。顺序即显示顺序。
#: 分组依据是数据模型的归属，不是字母序。
TABLE_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "单位与装备",
        ("Units", "Weapons", "Ammunitions", "WeaponAmmunitions", "Armors", "Modifications"),
    ),
    (
        "编成与乘员",
        (
            "SquadMembers",
            "SquadWeapons",
            "UnitArmors",
            "UnitPropulsions",
            "TransportAvailabilities",
        ),
    ),
    ("炮塔与选项", ("Turrets", "TurretUnits", "TurretWeapons", "Options")),
    (
        "技能与专长",
        ("Abilities", "UnitAbilities", "Specializations", "SpecializationAvailabilities"),
    ),
    ("传感器与机动", ("Sensors", "SensorUnits", "Mobility")),
    ("其他", ("Countries", "FlyPresets")),
)

#: 实例角色 -> 界面上显示的中文。角色名来自 _rules.json 的 instances 段。
#: 未知角色不显示后缀（宁可不说，也不要瞎猜一个中文名）。
_ROLE_LABELS: dict[str, str] = {
    "default_non_campaign": "默认（非战役）",
    "campaign_route": "战役",
}


def _role_label(role: str | None) -> str:
    """角色 -> 界面后缀。`campaign_route@1.2.0` 这类**带版本号**的显示成「战役 1.2.0」。

    版本号来自数据侧（`_rules.json` 的角色名，判据 = 容器里谁引用了那个实例 ——
    见 `DataBaseCompiled/analyze_refs.py`）。⛔ 认不出来的角色**一律不显示**：
    宁可不说，也不要瞎猜一个中文名。
    """
    if not role:
        return ""
    base, sep, version = role.partition("@")
    label = _ROLE_LABELS.get(base)
    if label is None:
        return ""
    return f"{label} {version}" if sep else label


def new_settings() -> QSettings:
    """窗口状态存哪。

    单独抽成函数是为了给测试留一个**接缝**：用例 monkeypatch 成临时 INI 文件，
    不然跑一次测试就往用户注册表里写一堆窗口坐标。
    """
    return QSettings("BA_Mod_Maker", "BA_Mod_Maker")


class MainWindow(QMainWindow):
    """主窗口外壳。"""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"BA_Mod_Maker 2.0 · 数据表主体  —  v{__version__}")
        self.setMinimumSize(1100, 680)
        self._settings = new_settings()
        # 先看上次是怎么摆的；没有记录（首次运行）才按屏幕推尺寸。
        if not self._restore_window_state():
            self._fit_to_screen()

        # 黑匣子先装上（文件日志 + 未捕获异常 + Qt 告警）。幂等：重复调只装一次。
        # 放在 MainWindow 里而不是 app/main.py：_work/gui_live.py 这类入口也直接建窗。
        self._log_path = diag.install_all()
        # "一闪而过的小弹窗"守卫：逮到就当场隐藏 + 把调用栈写进日志（用户要求彻底解决）。
        diag.install_stray_window_guard(QApplication.instance())

        self._dataset: Dataset | None = None
        self._instance: Instance | None = None
        self._show_all_instances = False
        #: 通过「打开文件…」打开的数据库文件（三种之一）。
        #: 有它就说明当前数据源来自**可写通道**，保存写回这个文件。
        self._source = None
        #: `_dataset` 是**从哪个 OpenedSource 装出来的**（None = 目录来源 / 还没打开）。
        #:
        #: ⚠️ 它必须与 `_source` 同时变，且**只有 `load_file` 一处会给两者赋值**。
        #: 从前 `_source` 只在 load_file 里赋值、目录分支不重置，于是
        #: "先开文件、再开目录"之后：导出导出的是**上一个文件**、保存拿上一个文件
        #: 跟新数据比"有没有改动"。所以这里显式记一份来源身份，
        #: 导出前拿它断言"dataset 与 source 同源"（见 `_export_db_zip`）。
        self._dataset_source = None
        #: 字段词典窗口（☰ -> 「📖 词典…」）。**懒建**：没点开就不建。
        self._dictionary = None
        #: 调试对话框（☰ -> 👁 视图 -> 「🐞 调试…」）。同样**懒建**。
        self._debug = None
        #: 正在跑一次"关不起窗"的长写盘（从前是写回 7.1 GB 容器，约 4 分钟）。
        #: ⚠️ 保存通道收口之后 GUI 里已经没有那条路了（只剩 CLI 的 `session.save`），
        #: 所以它现在恒为 False；closeEvent 里那道守卫**保留** —— 那 4 分钟一旦
        #: 再出现，工作线程还握着打开着的容器，关到一半失去目标比按不动 X 糟得多。
        self._saving = False

        # 写盘通道只有一份（见 app/shell/edit_session.py 的模块注释）：
        # 编辑器与两个新页都往它里面写，免得"A 页存 X、B 页存 Y"把对方刚改的表
        # 按源文件字节写回去。
        self._edit_session = EditSession(self)
        self._armory = ArmoryScreen()
        self._unit_editor = UnitEditorScreen()
        self._unit_editor.set_session(self._edit_session)
        # 中栏抬头 / 面包屑上那些**真图标**：整个应用只有这一份图标缓存
        # （`self._armory.icons`，bundle 直读，见 ADR 0003）。编辑器只是接过去用。
        self._unit_editor.set_icons(self._armory.icons)
        self._country = CountryPage()
        self._country.set_session(self._edit_session)
        self._spec = SpecPage()
        self._spec.set_session(self._edit_session)
        self._browser = TableBrowser()
        self._search_panel = GlobalSearch()
        self._tabs = QStackedWidget()
        #: 数据表页**自己的**行操作撤销栈（加行 / 复制行 / 删行）。
        #: 为什么页面自己记一份、不去动 Changeset：那条账本里列级改动与行级改动各记各的顺序、
        #: **没有统一序号**（见 core/edit/changeset.py），而这一页的 Ctrl+Z 只承诺撤自己在
        #: 这一页做过的行操作 —— 否则会撤到别的页（比如整单位克隆插的那三十几行）。
        self._table_row_undo: list = []
        self._tree = QTreeWidget()
        self._status_label = QLabel()
        self._instance_label = QLabel()
        self._progress = QProgressBar()
        self._nav_buttons: dict[int, QPushButton] = {}

        self._build_menu()
        # 还没打开任何东西：导出入口先是灭的（`_source` -> None 就是"没有那个文件"）。
        self._sync_source_actions()
        self._build_body()
        self._build_statusbar()
        self._connect()
        self._armory.set_instance(None)
        self._browser.set_instance(None)
        self._search_panel.set_instance(None)
        # ❗ **启动时不自动加载任何数据源**（用户口径）。
        #
        # 原先这里会 _autoload_dataset()：从 cwd 往上找 DataBaseCompiled 目录并直接加载。
        # 两个问题：
        #   ① 用户没得选 —— 想要的那个文件（容器 / BA_Mod.zip / json）不是它找到的那个；
        #   ② 它加载的是 DataBaseCompiled/ 那份**只读**数据源，看着有数据但保存不了，
        #      容易被误以为产品坏了。
        # 现在一律空着启动，由「文件 ▸ 打开文件…」或「打开数据源…」决定。

    # ------------------------------------------------------------------ #
    # 窗口尺寸
    # ------------------------------------------------------------------ #

    def _restore_window_state(self) -> bool:
        """恢复上次的窗口尺寸与最大化状态。没有可用记录返回 False。

        为什么要存"最大化"这个标志而不是只看几何：
        最大化窗口的 geometry() 是**普通状态**下的框，只恢复它会得到
        "上次最大化 → 这次是个大而不最大的窗口"，用户还得再点一下最大化。
        """
        geometry = self._settings.value("window/geometry")
        if not isinstance(geometry, QByteArray) or geometry.isEmpty():
            return False
        if not self.restoreGeometry(geometry):
            return False
        if self._settings.value("window/maximized", False, type=bool):
            self.setWindowState(self.windowState() | Qt.WindowState.WindowMaximized)
        # 屏幕拔掉/换显示器后，恢复出来的框可能落在可见区之外——那就退回默认。
        screen = self.screen()
        if screen is not None and not screen.availableGeometry().intersects(self.frameGeometry()):
            self._fit_to_screen()
        return True

    def _save_window_state(self) -> None:
        self._settings.setValue("window/geometry", self.saveGeometry())
        self._settings.setValue(
            "window/maximized", bool(self.windowState() & Qt.WindowState.WindowMaximized)
        )

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        """关窗前记住窗口状态：最大化/尺寸下次直接用。"""
        if self._saving:
            # 写回 7.1 GB 时工作线程还握着那个打开着的容器（也握着它的读取器）。
            # 让它关到一半失去目标，比让 X 暂时按不动糟糕得多。
            event.ignore()
            return
        self._save_window_state()
        self._armory.icons.shutdown()
        super().closeEvent(event)

    def _fit_to_screen(self) -> None:
        """初始尺寸取"屏幕可用区的 92%"与固定值的较小者。"""
        screen = self.screen()
        if screen is None:
            self.resize(1440, 900)
            return
        available = screen.availableGeometry()
        width = min(1600, int(available.width() * 0.92))
        height = min(980, int(available.height() * 0.92))
        self.resize(width, height)

    # ------------------------------------------------------------------ #
    # 菜单
    # ------------------------------------------------------------------ #

    def _build_menu(self) -> None:
        """菜单收进顶栏那个 ☰（界面重做定稿 块 2：3 行 90px -> 1 行 36px）。

        为什么不做成真的 QMenuBar：它自己就是一行（约 25px），而用户点名要
        "左上角什么文件、编辑、视图…纵向排列太占画面空间"。所以做成一个
        **弹出菜单**：☰ 点开，里面还是 文件 / 编辑 / 视图 三组，一项没少。
        仍然**没有**「帮助」菜单（用户明确要求去掉）。
        """
        # 屏上不放 QMenuBar：留着它会白占一行。这里显式藏掉而不是不创建 ——
        # QMainWindow.menuBar() 是惰性建的，别的地方一调它就会冒出来。
        self.menuBar().setVisible(False)
        popup = QMenu(self)
        self._menu = popup

        file_menu = popup.addMenu("📂 文件")
        # **只有一个打开入口**（用户口径：「有了打开文件，就不要打开数据源了」）。
        # 它能开四种东西：三种数据库文件（容器 / DataBaseCompiled.json / BA_Mod.zip）
        # 加上 DataBaseCompiled **目录** —— 目录走只读那条老路，文件走可写通道。
        # 顶栏那个「打开…」按钮接的是同一个槽，不再各有一套。
        self._add_action(
            file_menu, "打开…", QKeySequence.StandardKey.Open, slot=self._prompt_open_file
        )
        self._add_action(file_menu, "重新加载", QKeySequence.StandardKey.Refresh, slot=self._reload)
        file_menu.addSeparator()
        # 保存写回**打开时的那个文件**（唯一通道：EditSession.save()）。
        # ⚠️ Ctrl+S 全窗口**只有这一条**：菜单挂在 ☰ 上、不走 menuBar，
        #    所以这里显式把它挂到窗口上（`addAction`），否则那个快捷键
        #    不一定注册得到；单位编辑器那条同键的 QShortcut 由 T3 删掉。
        self._save_action = self._add_action(
            file_menu, "保存", QKeySequence.StandardKey.Save, slot=self._save_current
        )
        self._save_action.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
        self.addAction(self._save_action)
        # 导出：**打开的是一个文件**时才亮（目录来源没有"那个文件"可导出，
        # 从前"导出的是上一个文件"就是这么来的）。
        self._export_action = self._add_action(
            file_menu, "导出 BA_Mod.zip…", slot=self._export_db_zip
        )
        # 导入天然需要**两个**文件（来源 + 目标），所以它不能塞进「打开文件…」，
        # 单独一个对话框，并且**先预演再动手**。
        self._add_action(file_menu, "导入到 data.unity3d…", slot=self._open_import)
        file_menu.addSeparator()
        # 补丁决定「保存」是写 7.1 GB 还是写 300 KB，所以它属于文件菜单。
        self._add_action(file_menu, "外部数据库补丁…", slot=self._open_dll_patch)
        file_menu.addSeparator()
        self._add_action(file_menu, "退出", QKeySequence.StandardKey.Quit, slot=self.close)

        edit_menu = popup.addMenu("✏ 编辑")
        find_action = self._add_action(edit_menu, "查找…", slot=self._focus_search)
        # 两个键都能聚焦检索框：Ctrl+F 是老习惯，F3 是旧产品那套
        # （设计稿顶栏那句「按名查 Id（F3）」）。一个动作挂两个键，别做两条菜单项。
        find_action.setShortcuts([QKeySequence(QKeySequence.StandardKey.Find), QKeySequence("F3")])

        view_menu = popup.addMenu("👁 视图")
        self._add_action(view_menu, "军械库", slot=lambda: self.show_page(PAGE_ARMORY))
        self._add_action(view_menu, "国家", slot=lambda: self.show_page(PAGE_COUNTRY))
        self._add_action(view_menu, "专精", slot=lambda: self.show_page(PAGE_SPEC))
        self._add_action(view_menu, "数据表", slot=lambda: self.show_page(PAGE_TABLE))
        self._add_action(view_menu, "全局检索", slot=lambda: self.show_page(PAGE_SEARCH))
        view_menu.addSeparator()
        # 字段词典（只读）：列的中文名 / 说明 / 枚举取值。
        # 它本来就该在菜单里 —— 窗口早就写好了，只是此前**没有任何入口**。
        self._add_action(view_menu, "📖 词典…", slot=self._open_dictionary)
        # 诊断日志（用户点名要的那套）：出事时用户点开就能把日志拿给我们看。
        self._add_action(view_menu, "📜 日志…", slot=self._open_log)
        # Debug 模式（用户 2026-10-17）：一行体检 ＋ 📋复制诊断 ＋ 💾落盘。
        # 与上一条的分工：日志给**原料**（原始尾部）；调试给**能整段粘给我们的一段话**
        # （一行体检 ＋ 环境 ＋ 最近事件）—— 用户报问题时不用知道日志文件在哪。
        self._add_action(view_menu, "🐞 调试…", slot=self._open_debug)
        view_menu.addSeparator()
        # 实例菜单要等数据源加载后才能填（得先知道有哪些实例、哪个是默认）。
        self._instance_menu = view_menu.addMenu("切换实例")
        self._instance_menu.setEnabled(False)

        # 为什么留这个开关而不是彻底隐藏：
        # 数据侧结论是「只改一份 = 只有一半模式生效」。彻底藏掉会让人忘了它存在。
        self._all_instances_action = QAction("显示全部实例", view_menu)
        self._all_instances_action.setCheckable(True)
        self._all_instances_action.setEnabled(False)
        self._all_instances_action.toggled.connect(self._on_toggle_all_instances)
        view_menu.addAction(self._all_instances_action)

        # 刻意**没有**「帮助」菜单：它下面只有一项「关于」，占着菜单栏却解决不了任何问题。
        # 版本号本来就写在标题栏上，不需要再靠菜单重复一遍。

    @staticmethod
    def _add_action(menu, text: str, shortcut=None, *, slot=None, enabled: bool = True) -> QAction:
        action = QAction(text, menu)
        if shortcut is not None:
            action.setShortcut(shortcut)
        action.setEnabled(enabled)
        if slot is not None:
            action.triggered.connect(slot)
        menu.addAction(action)
        return action

    # ------------------------------------------------------------------ #
    # 主体
    # ------------------------------------------------------------------ #

    def _build_body(self) -> None:
        backdrop = BackdropWidget()
        layout = QVBoxLayout(backdrop)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._build_topbar())

        # 页签顺序 = 页码常量顺序 = NAV_PAGES（国家 / 专精 插在军械库与数据表中间）。
        self._tabs.addWidget(self._armory)  # 0 军械库
        self._tabs.addWidget(self._country)  # 1 国家
        self._tabs.addWidget(self._spec)  # 2 专精
        self._tabs.addWidget(self._build_table_page())  # 3 数据表
        self._tabs.addWidget(self._search_panel)  # 4 检索
        # 第 5 页：单位编辑器。**没有对应的导航按钮**（见 PAGE_UNIT_EDITOR 的注释）。
        self._tabs.addWidget(self._unit_editor)  # 5
        layout.addWidget(self._tabs, 1)

        self.setCentralWidget(backdrop)

    def _build_topbar(self) -> QWidget:
        """**一行 36px**：☰ + 五个页签 + 面包屑 + 保存胶囊 + 📂 打开… / 🔄 重新加载。

        原先是三行约 90px（菜单栏 / 导航条 / 编辑器自己的那条），用户口径：
        "左上角什么文件、编辑、视图、军械库、数据表、检索、返回军械库这些东西，
        纵向排列太占画面空间了"。所以这里只剩一行，菜单进 ☰，
        而编辑器那一条**取消**（它要显示的东西搬进面包屑与保存胶囊）。

        实例下拉也搬走了：它属于「文件」那一类动作，落在 ☰ 的视图菜单里
        （状态栏右下角仍然常驻「实例：52085」）。
        """
        bar = QWidget()
        bar.setProperty("role", "topbar")
        bar.setFixedHeight(TOKENS.size.topbar_height)

        layout = QHBoxLayout(bar)
        layout.setContentsMargins(TOKENS.spacing.sm, 0, TOKENS.spacing.md, 0)
        layout.setSpacing(TOKENS.spacing.xs)

        self._menu_button = QPushButton("☰")
        self._menu_button.setProperty("role", "menu-button")
        self._menu_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._menu_button.setToolTip("文件 / 编辑 / 视图（原菜单栏，收进这里省一行）")
        self._menu_button.clicked.connect(self._open_menu)
        layout.addWidget(self._menu_button)

        group = QButtonGroup(self)
        group.setExclusive(True)
        for index, text in enumerate(NAV_PAGES):
            button = QPushButton(text)
            button.setProperty("role", "nav")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _=False, page=index: self.show_page(page))
            group.addButton(button)
            layout.addWidget(button)
            self._nav_buttons[index] = button
        self._nav_buttons[PAGE_ARMORY].setChecked(True)

        # 面包屑：父级可点、当前层不可点（编辑器里才有内容）。
        self._breadcrumb = BreadcrumbBar()
        self._breadcrumb.set_icons(self._armory.icons)
        self._breadcrumb.back_clicked.connect(self._crumb_back)
        self._breadcrumb.segment_clicked.connect(self._crumb_go)
        layout.addWidget(self._breadcrumb, 1)

        # 「↩ 回上个单位」：贴在面包屑右边（用户口径 2026-10-01：「加一条回上个单位吧」）。
        # 编辑器里跳过单位之后，面包屑写的是**现在**是哪个单位，这颗按钮写回哪个。
        # ❗ **不占快捷键**（Ctrl+Z/Y/S、F3 都名花有主）—— 就是个看得见、点得到的按钮；
        #    历史空 / 不在编辑器里就整颗藏起来（`_sync_trail` 是唯一的刷新点）。
        self._back_unit_button = QPushButton("↩ 回上个单位")
        self._back_unit_button.setProperty("variant", "ghost")
        self._back_unit_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._back_unit_button.setVisible(False)
        self._back_unit_button.clicked.connect(self._return_to_previous_unit)
        layout.addWidget(self._back_unit_button)

        # 「⧉ 克隆整个单位」：整套克隆（自带的行 + 炮塔链 + 改装选项，改装里的炮塔指向也重指）。
        # 用户口径 2026-10-01：「迫切需要整单位克隆功能」。与「↩ 回上个单位」同一段、
        # 同一个刷新点（`_sync_back_unit_button`）—— 不在编辑器里就整颗藏起来。
        self._clone_unit_button = QPushButton("⧉ 克隆整个单位")
        self._clone_unit_button.setProperty("variant", "ghost")
        self._clone_unit_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._clone_unit_button.setVisible(False)
        self._clone_unit_button.setToolTip(
            "把这个单位整套克隆一份（新 Id）：\n"
            "· 自带的 11 张表整套复制（装甲/机动/传感器/能力/专精/运输/"
            "炮塔候选/携弹/改装/班组成员/班用武器）\n"
            "· 炮塔链一起复制（Turrets + TurretWeapons），改装选项里的 21 个炮塔指向重指到新炮塔\n"
            "· 武器/弹药/装甲等模板仍共享，一件都不多抄\n"
            "一次动作 = 一条撤销（Ctrl+Z 退得回来）；落盘仍走顶栏 💾"
        )
        self._clone_unit_button.clicked.connect(self._clone_current_unit)
        layout.addWidget(self._clone_unit_button)

        # 「🔧 编成台 / 🌲 关系树」：两个**顶层页**之间的切换（用户 2026-10-01 口径：
        # 双击单位默认进编成台；关系树退居兜底，但必须点得到，不许藏起来）。
        # 与上面几颗同一个刷新点（_sync_back_unit_button）—— **三种形态都有编成台了**
        # （2026-10-01 补的载具 / 直升机 / 固定翼），所以只要进了单位就露出来。
        self._bench_toggle_button = QPushButton("🌲 关系树")
        self._bench_toggle_button.setProperty("variant", "ghost")
        self._bench_toggle_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._bench_toggle_button.setVisible(False)
        self._bench_toggle_button.setToolTip("换回「关系树 ｜ 全部列 ｜ 只读卡」那一套")
        self._bench_toggle_button.clicked.connect(self._toggle_bench_page)
        layout.addWidget(self._bench_toggle_button)

        # 「👥 步兵班」：老 SquadPage 一直没入口（设计稿 §7 那条挂着的 d）。
        # **只对步兵露出来**（这个单位在 SquadMembers 里真有人），点它进那一页整页视图 ——
        # 卡上「班组成员」那一套 ＋/✕ 是同一件事的卡片版，两条路并存（2026-09-30 的口径）。
        self._squad_page_button = QPushButton("👥 步兵班")
        self._squad_page_button.setProperty("variant", "ghost")
        self._squad_page_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._squad_page_button.setVisible(False)
        self._squad_page_button.setToolTip(
            "进「步兵班」整页：一行一名士兵（+1 加人 / 行尾 ✕ 减人），右栏是选中那名士兵的 6 列"
        )
        self._squad_page_button.clicked.connect(self._open_squad_page)
        layout.addWidget(self._squad_page_button)

        # 顶栏那个「按名查 Id」入口（设计稿 overhaul3 顶栏那一格）。
        # 它只是**入口** —— 真正的输入框在检索页上：一份逻辑，不做两个搜索框。
        self._search_entry = QPushButton("🔎 按名查 Id（F3）")
        self._search_entry.setProperty("variant", "ghost")
        self._search_entry.setCursor(Qt.CursorShape.PointingHandCursor)
        self._search_entry.setToolTip("切到「检索」页并聚焦那个框（F3 / Ctrl+F）")
        self._search_entry.clicked.connect(self._focus_search)
        layout.addWidget(self._search_entry)

        # 保存状态胶囊：常驻，说清"缓存里还有几处没落盘"。
        self._save_pill = SavePill()
        layout.addWidget(self._save_pill)

        # 「💾 保存」：**唯一**的写盘入口（用户口径 2026-09-29）。
        # 改一格只进缓存（毫秒级、不碰盘），点它才把缓存写进数据库。
        # 没有改动时它自己灭掉 —— 免得每次点都弹一句"没有改动"。
        self._save_button = QPushButton("💾 保存")
        self._save_button.setProperty("variant", "primary")
        self._save_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._save_button.setEnabled(False)
        self._save_button.setToolTip("把缓存里的改动写进数据库（Ctrl+S）")
        self._save_button.clicked.connect(self._save_current)
        layout.addWidget(self._save_button)

        self._open_button = QPushButton("📂 打开…")
        self._open_button.setCursor(Qt.PointingHandCursor)
        self._open_button.setProperty("variant", "primary")
        # 与 ☰ 菜单里「打开…」同一个槽 —— 只有一条打开路径。
        self._open_button.clicked.connect(self._prompt_open_file)
        layout.addWidget(self._open_button)

        self._reload_button = QPushButton("🔄 重新加载")
        self._reload_button.setCursor(Qt.PointingHandCursor)
        self._reload_button.setProperty("variant", "ghost")
        self._reload_button.setEnabled(False)
        self._reload_button.clicked.connect(self._reload)
        layout.addWidget(self._reload_button)

        return bar

    def menu(self) -> QMenu:
        """顶栏 ☰ 那个弹出菜单（测试与其它入口读它；菜单栏本身不上屏）。"""
        return self._menu

    def _open_menu(self) -> None:
        """☰ 点开：菜单挂在按钮正下方（不是鼠标位置 —— 那样会飘）。"""
        from PySide6.QtCore import QPoint

        below = self._menu_button.mapToGlobal(QPoint(0, self._menu_button.height()))
        self._menu.popup(below)

    def _build_table_page(self) -> QWidget:
        """数据表页 = 导航树 + 表格浏览器 + 底部「被谁引用」（只读）。

        侧栏只作导航入口，所有节点默认折叠——不承担"一眼看全 24 张表"的职责，
        那会让侧栏变成一堵墙。

        底部那块是**只读**的引用反查：选中一行 -> 列出谁引用了它 -> 双击跳过去。
        "改一个共享行就是改一大片"（645 的传感器被 142 个单位共用）在这里才看得见。
        """
        splitter = QSplitter(Qt.Orientation.Horizontal)

        panel = QWidget()
        panel.setProperty("role", "sidebar")
        panel.setMinimumWidth(240)
        panel.setMaximumWidth(400)
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(
            TOKENS.spacing.md, TOKENS.spacing.md, TOKENS.spacing.md, TOKENS.spacing.md
        )
        panel_layout.setSpacing(TOKENS.spacing.sm)

        header = QLabel("24 张表")
        header.setProperty("role", "subtitle")
        panel_layout.addWidget(header)

        self._tree.setHeaderHidden(True)
        panel_layout.addWidget(self._tree, 1)

        self._sidebar_hint = QLabel("▲ 尚未加载数据源")
        self._sidebar_hint.setProperty("role", "muted")
        self._sidebar_hint.setWordWrap(True)
        panel_layout.addWidget(self._sidebar_hint)

        splitter.addWidget(panel)
        splitter.addWidget(self._browser)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([380, 1000])

        # 底部那块「被谁引用」。放在数据表页这一层、不塞进 TableBrowser：
        # 反查要的是"这一行是谁"（账本 / 关系图的口径），属于页面之间的接线，
        # 不是表格控件自己的事（控件保持自包含，见 app/widgets/__init__.py）。
        self._refs_panel = ReferencedByPanel()

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(splitter, 1)
        layout.addWidget(self._refs_panel)
        return page

    def _build_statusbar(self) -> None:
        bar = self.statusBar()
        self._status_label.setText("就绪 · 未加载数据源")
        bar.addWidget(self._status_label, 1)

        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setFixedWidth(200)
        self._progress.setVisible(False)
        bar.addPermanentWidget(self._progress)

        self._instance_label.setText("实例：—")
        bar.addPermanentWidget(self._instance_label)

        # 图标预取的**真进度条**（与 _set_busy 那根转圈的不是一回事）。
        # 用户原话"我希望在加载的时候能看到进度条"：点过「跳过」之后、
        # 或者换了数据源重新预取时，这里是唯一还看得见进度的地方。
        self._icon_bar = QProgressBar()
        self._icon_bar.setRange(0, 1)
        self._icon_bar.setValue(0)
        self._icon_bar.setFixedWidth(140)
        self._icon_bar.setTextVisible(False)
        self._icon_bar.setVisible(False)
        bar.addPermanentWidget(self._icon_bar)

        # 图标来源必须**可见**：装了游戏却读不到 bundle（路径变了 / 没装 UnityPy）
        # 会静默退成剪影，不说的话没人知道"图标怎么全没了"。
        self._icon_label = QLabel(self._icon_source_text())
        self._icon_label.setToolTip(self._icon_source_tip())
        bar.addPermanentWidget(self._icon_label)

        # "全部就绪"这句话到点自己收回去（见 _ICON_DONE_HOLD_MS）。
        self._icon_done_timer = QTimer(self)
        self._icon_done_timer.setSingleShot(True)
        self._icon_done_timer.timeout.connect(lambda: self._write_icon_label(""))

        bar.addPermanentWidget(QLabel(f"主题：{TOKENS.name}"))

    def _on_icon_progress(self, done: int, total: int) -> None:
        """预取进度：进度条 + 文字（计数 / 百分比 / 剩余时间）。

        预取结束那一刻（done == total）先把条收起来，随后 prefetch_finished
        再补一句"全部就绪"——两根信号分工不同，别在这里抢着写同一句话。
        """
        # 0/N 也要显示：预取一排队就该看得见条，不能等第一张到了才出现。
        active = total > 0 and done < total
        self._icon_bar.setVisible(active)
        if active:
            self._icon_bar.setRange(0, total)
            self._icon_bar.setValue(done)
            self._write_icon_label(prefetch_status_text(self._armory.icons, done=done, total=total))
        else:
            self._write_icon_label("")

    def _on_icon_prefetch_finished(self) -> None:
        """预取跑完：收起进度条，留一句"全部就绪"，几秒后回到简洁文案。"""
        self._icon_bar.setVisible(False)
        self._write_icon_label(prefetch_status_text(self._armory.icons))
        self._icon_done_timer.start(_ICON_DONE_HOLD_MS)

    def _write_icon_label(self, status: str) -> None:
        base = self._icon_source_text()
        self._icon_label.setText(f"{base} · {status}" if status else base)
        self._icon_label.setToolTip(self._icon_source_tip())

    @property
    def icons(self):
        """图标缓存（启动流程要拿它做预取门禁）。"""
        return self._armory.icons

    def _icon_source_text(self) -> str:
        return {
            "bundle": "图标：游戏 bundle",
            "cache": "图标：本地缓存",
            "none": "图标：无（剪影）",
        }.get(self._armory.icons.source, "图标：?")

    def _icon_source_tip(self) -> str:
        cache = self._armory.icons
        game = cache.game_root
        lines = [
            f"来源：{cache.source}",
            f"游戏目录：{game}" if game else "游戏目录：未找到（用缓存或剪影）",
            f"运行时缓存：{cache.runtime_cache}",
        ]
        if cache.prefetch_active:
            # 悬浮提示里说全一点：状态栏那一行放不下"正在取哪一族"。
            lines.append(f"预取：{prefetch_status_text(cache, current=True)}")
            lines.append("（预取在后台线程跑，界面不等它）")
        return chr(10).join(lines)

    def _connect(self) -> None:
        self._tree.currentItemChanged.connect(self._on_tree_selection)
        self._browser.table_changed.connect(self._on_table_changed)
        # 行操作那句话（「已删除 Armors#3（有 12 行引用它）· Ctrl+Z 可撤」）走状态栏 ——
        # 用户口径：完成/成功类不弹窗（AGENTS §5）。
        self._browser.status_message.connect(self._status_label.setText)
        # 数据表页的写入走**同一份账本**（EditSession）：四个口子由外壳注入，
        # 口径见 app/widgets/table_model.py::TableWriteChannel。
        # 没这一步，数据表页的改动顶栏看不见（P0：💾 不亮、Ctrl+S 说没改动）。
        self._browser.set_write_channel(
            TableWriteChannel(
                write_cell=self._write_table_cell,
                is_dirty=self._table_cell_is_dirty,
                dirty_count=self._table_dirty_count,
                revert_table=self._revert_table_changes,
                # 加行 / 删行（用户 2026-10-01：「需要整表加行、复制行、删行功能」）。
                # 两个口子都落**同一份** changeset —— 顶栏胶囊与 💾 才看得见它们。
                insert_row=self._insert_table_row,
                delete_row=self._delete_table_row,
                undo_last=self._undo_table_row,
            )
        )
        # 选中一行 -> 底部「被谁引用」跟着换；双击一条 -> 跳到那张表那一行。
        self._browser.row_selected.connect(self._on_browser_row_selected)
        self._refs_panel.hit_activated.connect(self._open_table_row)
        self._search_panel.hit_activated.connect(self._on_search_hit)
        self._armory.unit_selected.connect(self._on_armory_unit_selected)
        # 卡片装配失败：状态栏也得说一句（卡片上已经写了原因，这里只是别让它静默）。
        self._armory.card_failed.connect(self._on_armory_card_failed)
        # 图标预加载进度：状态栏上要看得见"还要多久"，不然像卡住了。
        self._armory.icons.progress.connect(self._on_icon_progress)
        self._armory.icons.prefetch_finished.connect(self._on_icon_prefetch_finished)
        # 双击一张卡 -> 进单位编辑器；编辑器里「← 返回军械库」/ Esc 回来。
        self._armory.unit_activated.connect(self._open_unit_editor)
        self._unit_editor.back_requested.connect(self._close_unit_editor)
        self._unit_editor.status_message.connect(self._status_label.setText)
        self._unit_editor.open_table_row_requested.connect(self._open_table_row)
        # 编成台右栏「数据表页」那颗胶囊：整页跳过去（不是某一行）。
        self._unit_editor.tables_requested.connect(lambda: self.show_page(PAGE_TABLE))
        # 编辑器换了一层（或换了单位）-> 顶栏面包屑跟着动（设计块 4：父级可点）。
        self._unit_editor.trail_changed.connect(self._sync_trail)
        # 国家页 / 专精页：状态栏一句话（成功不弹窗）。
        self._country.status_message.connect(self._status_label.setText)
        self._spec.status_message.connect(self._status_label.setText)
        # 写盘通道 -> 顶栏那个胶囊 + 「💾 保存」那颗按钮。
        self._edit_session.saved.connect(self._on_saved)
        self._edit_session.failed.connect(self._on_save_failed)
        # 缓存里有改动 -> 按钮亮起来、胶囊写清"几处未保存"。
        # 两条线都要接：编辑器直接写 changeset（走 dirty_changed），
        # 国家页 / 专精页走 EditSession.set_value（发 session.changed）。
        self._unit_editor.dirty_changed.connect(self._sync_save_state)
        self._edit_session.changed.connect(self._sync_save_state)
        # 账本变了就重画数据表页：别的页（编辑器 / 国家 / 专精）改的是**同一份账本**，
        # 数据表页自己的黄底与「本表 N 处未保存」得跟着走。
        self._edit_session.changed.connect(self._browser.refresh_dirty_state)

    # ------------------------------------------------------------------ #
    # 页面
    # ------------------------------------------------------------------ #

    def show_page(self, index: int) -> None:
        self._tabs.setCurrentIndex(index)
        button = self._nav_buttons.get(index)
        if button is not None:
            button.setChecked(True)
        # 诊断日志的上下文：出事时最想知道"人在哪一页、哪个单位"。
        diag.set_context(page=NAV_PAGES[index] if 0 <= index < len(NAV_PAGES) else "单位编辑器")
        # 面包屑只在编辑器里出现（顶层页没有"上一级"）。
        self._sync_trail()

    # ------------------------------------------------------------------ #
    # 面包屑 + 保存胶囊
    # ------------------------------------------------------------------ #

    def _sync_trail(self) -> None:
        """把编辑器的导航栈画成顶栏那条面包屑。**别的页没有面包屑。**

        「↩ 回上个单位」也在这里一起刷新 —— 顶栏这一段的**唯一**刷新点
        （show_page / trail_changed / 点面包屑都走它）。
        """
        on_editor = self._tabs.currentWidget() is self._unit_editor
        trail = self._unit_editor.trail() if on_editor else ()
        self._sync_back_unit_button(on_editor)
        if not trail:
            self._breadcrumb.set_trail((), back=False)
            return
        self._breadcrumb.set_trail(
            trail,
            back=self._unit_editor.can_go_back(),
            marks=self._unit_editor.trail_marks(),
        )

    def _sync_back_unit_button(self, on_editor: bool) -> None:
        """那颗「↩ 回上个单位」的可见性与 tooltip。

        历史空 / 不在编辑器里 → 藏起来（一颗点不动的按钮比没有更烦人）。
        tooltip 写清**回哪个单位 + 它的 Id**（用户口径）。
        """
        self._clone_unit_button.setVisible(on_editor)
        self._squad_page_button.setVisible(on_editor and self._unit_editor.squad_size() > 0)
        self._sync_bench_toggle(on_editor)
        previous = self._unit_editor.previous_unit_id() if on_editor else None
        self._back_unit_button.setVisible(previous is not None)
        if previous is None:
            return
        self._back_unit_button.setToolTip(
            f"回到上一个单位：{self._unit_editor.unit_label(previous)}（#{previous}）"
        )

    def _sync_bench_toggle(self, on_editor: bool) -> None:
        """「编成台 / 关系树」那颗切换钮：只在**这个单位有编成台**时露出来。

        三种形态都有了（2026-10-01 补的载具 / 直升机 / 固定翼）——
        判据是 bench_kind()：有班成员=squad / 飞得起来=air / 其余=vehicle，
        三者各有各的编成台，所以判据从"== squad"放宽成"**非空**"。
        tooltip 按形态说清进去会看到什么（文案只有一份，在 bench_page.KIND_TIPS）。
        """
        kind = self._unit_editor.current_bench_kind() if on_editor else ""
        available = bool(kind)
        self._bench_toggle_button.setVisible(available)
        if not available:
            return
        on_bench = self._unit_editor.on_bench_page()
        self._bench_toggle_button.setText("🌲 关系树" if on_bench else "🔧 编成台")
        self._bench_toggle_button.setToolTip(
            "换回「关系树 ｜ 全部列 ｜ 只读卡」那一套（全库兜底）"
            if on_bench
            else KIND_TIPS.get(kind, "进编成台")
        )

    def _toggle_bench_page(self) -> None:
        """在两个顶层页之间来回切（状态栏说清到了哪一页）。"""
        if self._unit_editor.on_bench_page():
            self._unit_editor.show_main()
            self._status_label.setText("已回到关系树（← / Esc 逐层往回）")
        elif self._unit_editor.show_bench():
            self._status_label.setText("已进编成台（只有 ＋ 和 ✕）")
        else:
            self._status_label.setText("这个单位还没有编成台（还没进单位？先双击军械库里的一张卡）")
        self._sync_trail()

    def _open_squad_page(self) -> None:
        """顶栏「👥 步兵班」：进老 SquadPage 那一页整页视图（设计稿 §7 的 d）。"""
        self._unit_editor.open_squad_page()
        self._status_label.setText("已进「步兵班」页（← / Esc 回本单位那一行）")
        self._sync_trail()

    def _clone_current_unit(self) -> None:
        """顶栏「⧉ 克隆整个单位」：整套克隆当前单位（状态栏那句由编辑器发）。"""
        self._unit_editor.clone_current_unit()

    def _return_to_previous_unit(self) -> None:
        """点「↩ 回上个单位」。状态栏那句话由编辑器自己发（见 status_message 的接线）。"""
        self._unit_editor.return_to_previous_unit()
        self._sync_trail()

    def _crumb_back(self) -> None:
        self._unit_editor.go_back()
        self._sync_trail()

    def _crumb_go(self, index: int) -> None:
        """点面包屑的父级：0 = 军械库（退出编辑器），其余 = 回到栈里那一层。"""
        if index == 0:
            self._close_unit_editor()
            return
        self._unit_editor.go_to(index)
        self._sync_trail()

    def _sync_save_state(self) -> None:
        """顶栏那两样跟着"缓存里还有几处没落盘"走。

        - 有改动：按钮亮起来、胶囊写「● N 处未保存」；
        - 没改动：按钮灭掉；胶囊**不动** —— 它上面那句「✓ 已存 xxx · 时间」是
          刚写盘成功的回执，拿"未改动"盖掉它等于把回执吃了。
        """
        # 用**共享会话**的计数：编辑器 / 国家页 / 专精页 / **数据表页**
        # 改的都算同一份缓存（数据表页经 TableWriteChannel 也写这一份）。
        # ⚠ **表条目与资产条目一起算**（ADR 0008 决定 3：一个会话两份账本）。
        #   顶栏是**唯一**的脏态指示（AGENTS §4.5），少算一份就会出现
        #   「账本里有条目、💾 却灭着」—— 那等于用户点不动保存。
        count = self._edit_session.total_change_count
        self._save_button.setEnabled(count > 0)
        if count:
            self._save_pill.mark_dirty(
                f"● {count} 处未保存", tip="点「💾 保存」写进数据库（Ctrl+S 同效）"
            )

    def _on_saved(self, path: str, total: int) -> None:
        """写盘成功：胶囊说清"存到哪、什么时候"。**不弹窗**（AGENTS §5）。"""
        name = Path(path).name
        self._save_pill.mark_saved(name, tip=f"{path}\n本次 {total:,} 字节")
        self._sync_save_state()

    def _on_save_failed(self, message: str) -> None:
        """写盘失败：胶囊变红 + **弹窗**（错误必须打断，状态栏一闪而过会被错过）。"""
        self._save_pill.mark_failed(tip=message)
        QMessageBox.warning(self, "没有写成功", message)

    # ------------------------------------------------------------------ #
    # 数据表页的写通道（四个口子，全部落在 EditSession 那一份账本上）
    # ------------------------------------------------------------------ #

    def _write_table_cell(self, table: str, row_id: int, column: str, value) -> bool:
        """数据表页改一格：**只进缓存**（账本），不落盘。

        行用 **Id**（不是行下标）—— 插行会让行下标整体位移，账本按 Id 记
        （core/edit/changeset.py 的模块说明）。
        """
        if self._edit_session.changeset is None:
            return False
        self._edit_session.set_value(CellRef(table, row_id, column), value)
        return True

    def _table_cell_is_dirty(self, table: str, row_id: int, column: str) -> bool:
        """这一格在账本里有没有未保存的改动（单元格黄底读它）。"""
        return self._edit_session.is_dirty(table, row_id, column)

    def _table_dirty_count(self, table: str) -> int:
        """这张表在账本里有几处未保存的改动（「放弃改动」按钮读它）。"""
        return self._edit_session.dirty_count(table)

    def _revert_table_changes(self, table: str) -> int:
        """丢掉**这张表**的改动（逐条写回旧值，不重读磁盘）。"""
        return self._edit_session.revert_table(table)

    def _insert_table_row(self, table: str, values) -> int:
        """数据表页**加一行 / 复制一行**：只进缓存，Id 由 core 分配。

        用户口径（2026-10-01）：「需要整表加行、复制行、删行功能」。

        走的还是 `EditSession` 那一份 changeset —— 顶栏胶囊、💾 保存、「放弃改动」立刻
        看得见它（P0 那次静默丢数据就是因为数据表页自己写内存、绕过账本）。
        `values` 带不带 Id 都行：一律置 None，交给 `core.edit.clone.add_row` 取
        `next_row_id(table, changeset)`（本会话删过的 Id 不复用，连点两次不会撞）。
        """
        changeset = self._edit_session.changeset
        if changeset is None:
            return 0
        payload = dict(values)
        payload["Id"] = None
        row_id = core_add_row(changeset, table, payload)
        self._remember_table_row_change(changeset, table, row_id)
        self._edit_session.changed.emit()
        return row_id

    def _remember_table_row_change(self, changeset, table: str, row_id: int) -> None:
        """把刚插进去那一行的账目记进**这一页**的撤销栈（Ctrl+Z 用）。"""
        for record in reversed(changeset.row_changes):
            if record.table == table and record.row_id == row_id:
                self._table_row_undo.append(record)
                return

    def _undo_table_row(self) -> str:
        """数据表页的 Ctrl+Z：撤**这一页**最后一次行操作（加行 / 复制行 / 删行）。

        ❗ 撤的是**页面自己的栈**，不是「Changeset 里最后一条」：那条账本里列级改动与行级
        改动各记各的顺序、没有统一序号（core/edit/changeset.py），照它撤会撤到别的页 ——
        例如整单位克隆刚插的那三十几行，按一下就把一个克隆拆成半截。
        栈空了如实说一句，不假装撤掉了什么。
        """
        while self._table_row_undo:
            record = self._table_row_undo.pop()
            changeset = self._edit_session.changeset
            if changeset is None:  # pragma: no cover - 有记录就一定有账本
                break
            try:
                changeset.revert_row(record)
            except Exception:  # noqa: BLE001 - 已经撤过 / 保存后账本清空：说一句，不炸
                return f"这一条撤不动了（可能已撤过或已保存）：{record.summary}"
            self._edit_session.changed.emit()
            return f"已撤销：{record.summary}"
        return "数据表页没有可撤销的行操作"

    def _delete_table_row(self, table: str, row_id: int) -> None:
        """数据表页**删一行**：只进缓存（整行旧值留在账本里，撤得回来）。"""
        changeset = self._edit_session.changeset
        if changeset is None:
            return
        record = changeset.delete_row(table, row_id)
        self._table_row_undo.append(record)
        self._edit_session.changed.emit()

    # ------------------------------------------------------------------ #
    # 「被谁引用」（只读）
    # ------------------------------------------------------------------ #

    def _on_browser_row_selected(self, source_row: int) -> None:
        """数据表页选中一行 -> 底部「被谁引用」跟着换。"""
        table = self._browser.model.table
        if table is None or source_row < 0:
            self._refs_panel.clear()
            return
        row_id = row_id_of(table, source_row)
        if row_id is None:
            # 这张表没有整数 Id 列 -> 反查没有可靠的"这一行是谁"，不猜。
            self._refs_panel.show_unavailable()
            return
        hits = self._referencing_rows(table.name, row_id)
        if hits is None:
            self._refs_panel.show_unavailable()
        else:
            self._refs_panel.show_hits(table.name, row_id, hits)

    def _referencing_rows(self, table: str, row_id: int):
        """谁引用了这一行；**反查口径只有一份**（core.edit.refs.referencing_rows）。

        用 `getattr` 软引用而不是直接 import：那份口径由 T1 交付，并行开发期可能
        还没落地 —— 这时返回 `None`，界面如实说"还没接上"，
        **不在这里自己实现一份**（"逻辑只有一份"，AGENTS §5）。
        """
        if self._instance is None:
            return None
        from ba_mod_maker.core.edit import refs

        finder = getattr(refs, "referencing_rows", None)
        if finder is None:
            return None
        return tuple(finder(self._instance, table, row_id))

    # ------------------------------------------------------------------ #
    # 字段词典
    # ------------------------------------------------------------------ #

    def _open_dictionary(self) -> None:
        """☰ -> 「📖 词典…」：只读的字段词典窗口（一个实例一份，懒建）。"""
        if self._dictionary is None:
            self._dictionary = self._dictionary_class()(self, instance=self._instance)
        else:
            # 换过数据源的话，窗口里那份"取值观测"要跟着换。
            self._dictionary.set_instance(self._instance)
        self._dictionary.show()
        self._dictionary.raise_()
        self._dictionary.activateWindow()

    @staticmethod
    def _dictionary_class():
        """词典窗口的类（T5 交付，只读）。

        `DictionaryWindow(parent=None, *, instance=None)` + `set_instance(instance)`；
        模块是 `app/dialogs/dictionary_window.py`，包口统一从 `app.dialogs` 出去
        （与 `app/dialogs/__init__.py` 的说明一致）。
        """
        from ba_mod_maker.app.dialogs import DictionaryWindow

        return DictionaryWindow

    # ------------------------------------------------------------------ #
    # 数据源
    # ------------------------------------------------------------------ #

    def _autoload_dataset(self) -> None:
        """**已停用**（用户口径：启动时不自动加载）。

        保留这个方法体是为了把「为什么不用它」写在代码里，
        而不是让人以为漏写了。它原本做的事：从 cwd 往上找
        DataBaseCompiled 目录并加载。

        为什么停用见 __init__ 末尾那段注释。要恢复只是把调用加回去 ——
        但先跟用户确认：自动加载会抢走「选哪个文件」的决定权。
        """
        return

    def _prompt_open_file(self) -> None:
        """**唯一的打开入口。**可开文件，也可开目录。

        为什么保留"目录"这条：`DataBaseCompiled/` 是数据侧的交付物，
        没装游戏的机器上也常有，直接开它比开容器快得多（毫秒级）。
        但它是**只读**的 —— 保存时会提示改用文件。

        **起始目录 = 上次打开过的那个目录**（用户口径："在打开文件的时候，
        能不能记住我上次打开的路径"）。优先级：当前已打开文件所在目录 >
        上次记住的目录 > 进程工作目录。记住的是**目录**不是文件 ——
        用户接着多半还是从同一个地方挑（游戏目录 / 工程目录）。
        """
        start = str(self._start_dir())
        chosen, _filter = QFileDialog.getOpenFileName(
            self,
            "打开文件（也可直接输入 DataBaseCompiled 目录）",
            start,
            "数据库文件 (*.unity3d *.json *.zip);;全部文件 (*)",
        )
        if chosen:
            self.open_path(Path(chosen))

    def _start_dir(self) -> Path:
        """打开 / 保存对话框从哪儿开始。见 _prompt_open_file 的说明。"""
        if self._source is not None:
            return Path(self._source.path).parent
        remembered = str(self._settings.value("last_open_dir", "") or "")
        if remembered and Path(remembered).is_dir():
            return Path(remembered)
        return Path.cwd()

    def _remember_dir(self, path: Path) -> None:
        """记住"上次打开/保存过哪个目录"（写 QSettings —— new_settings 那条接缝）。"""
        target = Path(path)
        directory = target if target.is_dir() else target.parent
        if directory.is_dir():
            self._settings.setValue("last_open_dir", str(directory))

    def open_path(self, path: Path) -> bool:
        """按路径打开：**目录走只读数据源，文件走可写通道**。

        一个入口分派两种，而不是两个菜单项 —— 用户不需要先想"我这是文件还是目录"。
        """
        path = Path(path)
        opened = self.load_dataset(path) if path.is_dir() else self.load_file(path)
        if opened:
            self._remember_dir(path)
        return opened

    def load_file(self, path: Path) -> bool:
        """打开一个数据库文件并装成界面认的数据源。成功返回 True。"""
        from ba_mod_maker.app.shell import db_channel as chan
        from ba_mod_maker.core import session

        self._set_busy(True, "正在打开数据库文件…")
        try:
            src = session.open_source(path)
            dataset = chan.dataset_from_source(src)
        except BaModMakerError as exc:
            self._set_busy(False, "打开失败")
            QMessageBox.warning(self, "无法打开这个文件", str(exc))
            return False

        if not self.load_dataset(dataset=dataset):
            src.close()
            return False

        # ⚠️ 这一对赋值**必须挨着**（见 `_dataset_source` 的说明）：换数据源时
        # "数据"与"它从哪个文件来的"要同时换，否则导出/保存会拿上一个文件干活。
        if self._source is not None:
            self._source.close()
        self._source = src
        self._dataset_source = src
        self._sync_source_actions()
        # 侧栏那句"只读来源"要按**新的**来源重画（load_dataset 里那次跑在绑定之前）。
        self._refresh_sidebar_hint()
        title, subtitle = chan.describe_source(src)
        self.setWindowTitle(f"{title} · {subtitle} · BA_Mod_Maker 2.0")
        self._set_busy(False, f"已打开 {src.kind.label}")
        return True

    def _save_current(self) -> None:
        """把缓存里的改动写回**打开时的那个文件**。

        ⚠️ 写盘**只有一条通道**：`EditSession.save()`（AGENTS §4.5）。
        顶栏「💾 保存」、☰ 菜单「保存」、Ctrl+S 全走它 —— 从前这里另有一条
        `db_channel.apply_dataset_to_source` + `core.session.save` 的旧通道，
        那条路改的是 `_source`（不是账本），于是"改了却没进账、保存说没改动"。
        """
        if self._source is None:
            # 这句是"解释为什么不能写"，属于该被看到的信息，但不值得弹窗 ——
            # 状态栏能说清楚，而且用户马上会去点「打开…」。
            self._status_label.setText(
                "当前是只读的数据源目录，无法写回；请用「打开…」打开容器 / json / BA_Mod.zip"
            )
            return
        if self._dataset is None:
            return
        # 落点与"存了什么"都由账本决定（打开的是文件就写回那个文件；
        # 目录来源在上面已经挡掉）。返回的那句话直接进状态栏 —— 成功与
        # "没有改动"都不弹窗（用户口径）。
        self._status_label.setText(self._edit_session.save())

    def _export_db_zip(self) -> None:
        """导出 BA_Mod.zip —— 这就是「提取」。"""
        if self._source is None:
            self._status_label.setText("请先用「文件 ▸ 打开…」打开一个数据库文件")
            return
        # ⚠️ 导出前**断言 dataset 与 source 同源**。P0 就出在这儿：先开一个文件、
        #    再开一个目录（目录分支从前不重置 _source），于是"导出"导出的是
        #    **上一个文件**，而界面上摆着的是刚打开那份数据 —— 还什么都不报。
        if self._dataset is None or self._dataset_source is not self._source:
            self._status_label.setText(
                "数据源与「打开的那个文件」不是同一份，已取消导出；请用「打开…」重新打开那个文件"
            )
            return
        from ba_mod_maker.core import session

        suggested = str(self._start_dir() / session.PACKAGE_NAME)
        chosen, _filter = QFileDialog.getSaveFileName(
            self,
            f"导出 {session.PACKAGE_NAME}",
            suggested,
            f"外部库 {session.PACKAGE_NAME} (*.zip)",
        )
        if not chosen:
            return
        try:
            out = session.extract_db_zip(self._source, Path(chosen))
        except BaModMakerError as exc:
            QMessageBox.warning(self, "导出失败", str(exc))
            return
        # 结果只进状态栏。导出是高频低风险动作，弹窗只会碍事。
        self._status_label.setText(f"已导出 24 张表 -> {out.name}（{out.stat().st_size:,} 字节）")

    def _open_import(self) -> None:
        """打开「导入到 data.unity3d」对话框。

        目标容器**现取**：当前打开的若是容器就优先用它，
        否则回落到游戏根目录下那个（与补丁对话框同一个口径）。
        """
        from ba_mod_maker.app.shell.db_import import open_import_dialog

        target = None
        if self._source is not None and self._source.kind is SourceKind.UNITY3D:
            target = self._source.path
        open_import_dialog(self, container=target)

    def _open_log(self) -> None:
        """☰ -> 「📜 日志…」：看一眼产品自己记了什么。"""
        from ba_mod_maker.app.diag.ui import LogDialog

        LogDialog(self).exec()

    def _open_debug(self) -> None:
        """☰ -> 👁 视图 -> 「🐞 调试…」：一行体检 ＋ 一键复制诊断 ＋ 落盘。

        **不 exec()**：调试窗口开着的几十秒里用户还可能在用主窗（他在复述刚才那下
        是怎么点的），模态会把主窗钉死。与词典窗口同一种开法。
        """
        from ba_mod_maker.app.diag.ui import DebugDialog

        if self._debug is None:
            self._debug = DebugDialog(self)
        else:
            self._debug.refresh()  # 日志目录 / 环境变量可能刚被改过，一行体检要现算
        self._debug.show()
        self._debug.raise_()
        self._debug.activateWindow()

    @property
    def log_path(self) -> Path:
        return self._log_path

    def _open_dll_patch(self) -> None:
        """打开外部数据库补丁对话框。

        游戏目录**现取**，不在建窗时缓存 —— 用户可能在设置里改过环境变量，
        也可能刚把游戏挪走。取不到时对话框自己会说清楚。
        """
        from ba_mod_maker.app.shell.dll_patch import DllPatchDialog

        DllPatchDialog(self).exec()

    def _reload(self) -> None:
        """重新加载当前数据源。

        ⚠️ **必须分两种**（本轮 P0 的连带）：打开的是文件就重新打开**那个文件** ——
        否则会掉进目录分支，而那一支会把 `_source` 清成 None（目录 = 只读），
        写盘通道当场没了，用户只是点了下"重新加载"就再也存不了。
        """
        if self._source is not None:
            self.load_file(Path(self._source.path))
            return
        if self._dataset is not None:
            self.load_dataset(self._dataset.root)

    def _forget_source(self) -> None:
        """当前数据源是**只读目录**：把"打开的那个文件"忘掉（`_source` -> None）。

        为什么非要清（P0）：`_source` 从前只在 `load_file` 里赋值，目录分支不动它 ——
        "先开文件、再开目录"之后，导出导出的是**上一个文件**、
        "保存"还拿上一个文件跟新数据比"有没有改动"（于是恒说没有改动）。
        """
        if self._source is not None:
            self._source.close()
        self._source = None
        self._dataset_source = None
        self._sync_source_actions()

    def _sync_source_actions(self) -> None:
        """导出入口只在"打开的是一个文件"时才亮。

        目录来源没有"那个文件"可导出 —— 亮着只会导出上一个文件（就是那个 P0）。
        真被程序调到时，`_export_db_zip` 里还有一道同源断言。
        """
        has_file = self._source is not None
        self._export_action.setEnabled(has_file)
        self._export_action.setToolTip(
            ""
            if has_file
            else "当前是只读的数据源目录：先用「打开…」选一个文件（容器 / json / BA_Mod.zip）"
        )

    def load_dataset(self, root: Path | None = None, *, dataset=None) -> bool:
        """加载数据源。成功返回 True。

        两种用法：

        - `load_dataset(某个目录)` —— 读磁盘上的 DataBaseCompiled 目录（第一线就在用）；
        - `load_dataset(dataset=...)` —— 直接装一份**已经在内存里**的数据源，
          给「打开文件…」（三种数据库文件）用。后者不走磁盘，所以不需要 _schema.json。

        失败时弹一个可读的错误框（而不是静默失败），
        并保持原有数据源不变——加载失败不应把已有工作弄丢。
        """
        if dataset is None and root is None:
            raise ValueError("要么给 root，要么给 dataset")
        self._set_busy(True, "正在加载数据源…")
        if dataset is None:
            try:
                dataset = load_dataset(root)
            except BaModMakerError as exc:
                self._set_busy(False, "加载失败")
                QMessageBox.warning(self, "无法加载数据源", str(exc))
                return False
            except OSError as exc:
                self._set_busy(False, "加载失败")
                QMessageBox.warning(self, "无法读取目录", f"{root}\n\n{exc}")
                return False
            # 目录来源是**只读**的：它没有"打开的那个文件"。
            # 放在加载成功之后（加载失败不许把已有工作丢掉）。
            self._forget_source()

        self._dataset = dataset
        self._show_all_instances = False
        self._all_instances_action.blockSignals(True)
        self._all_instances_action.setChecked(False)
        self._all_instances_action.blockSignals(False)
        self._all_instances_action.setEnabled(len(dataset.instance_ids) > 1)
        self._rebuild_instance_menu()
        self._populate_tree()

        current = dataset.default_instance_id
        try:
            self._activate_instance(current)
        except BaModMakerError as exc:
            # 数据源本身能打开，但某张表读不出来（提取不完整 / 被改坏）。
            # 不能让它一路抛穿界面——窗口已经建好了，
            # 应当弹框说明原因并停在可用状态。
            self._set_busy(False, "表数据读取失败")
            QMessageBox.warning(self, "无法读取表数据", str(exc))
            self._reload_button.setEnabled(True)
            return True

        self._reload_button.setEnabled(True)
        self._refresh_sidebar_hint()
        # 落点那句话不许写死：打开的是 zip / json 就写回**那个文件**，目录来源才写 BA_Mod.zip
        # （唯一一份口径在 core/edit/package.default_save_target）。
        self._save_pill.mark_dirty(
            "● 未改动", tip="改任何一格都只进缓存；点「💾 保存」才写进数据库"
        )
        self._sync_save_state()
        self._set_busy(False, f"已加载 {len(dataset.instance_ids)} 个实例 · 当前 {current}")
        return True

    def _visible_instance_ids(self) -> tuple[str, ...]:
        """导航树里要显示哪些实例。

        默认只显示 _rules.json 指明的「默认非战役」实例。
        为什么不删另一个：数据侧结论是「只改一份 = 只有一半模式生效」，
        彻底隐藏会让人忘了战役实例的存在，所以留了「显示全部实例」开关。
        """
        if self._dataset is None:
            return ()
        if self._show_all_instances:
            return self._dataset.instance_ids
        return (self._dataset.default_instance_id,)

    def _populate_tree(self) -> None:
        """把导航树按「实例 -> 分组 -> 表」重建。**所有节点默认折叠。**"""
        self._tree.blockSignals(True)
        # ❗ 不用 clear()：走模型整表重置会踩 shiboken releaseWrapper 的 NULL 查表
        #    （实测与证据见 docs/交接-2026-10-01-载具飞机编成台.md §6.7-§6.10）
        while self._tree.topLevelItemCount():
            self._tree.takeTopLevelItem(0)
        if self._dataset is None:
            self._tree.blockSignals(False)
            return

        for path_id in self._visible_instance_ids():
            instance = self._dataset.instance(path_id)
            role = self._dataset.instance_roles.get(path_id)
            label = _role_label(role)
            suffix = f" · {label}" if label else ""

            root_item = QTreeWidgetItem([f"实例 {path_id}{suffix}"])
            root_item.setData(0, Qt.ItemDataRole.UserRole, ("instance", path_id))
            root_item.setToolTip(0, f"实例 {path_id}{suffix}")
            self._tree.addTopLevelItem(root_item)

            placed: set[str] = set()
            for group_name, tables in TABLE_GROUPS:
                members = [t for t in tables if t in instance.schema]
                if not members:
                    continue
                root_item.addChild(self._make_group_item(group_name, instance, members))
                placed.update(members)

            leftovers = [t for t in instance.table_names if t not in placed]
            if leftovers:
                root_item.addChild(self._make_group_item("未分类", instance, leftovers))

            root_item.setExpanded(False)

        self._tree.blockSignals(False)
        self._refresh_sidebar_hint()

    def _refresh_sidebar_hint(self) -> None:
        """刷新侧栏底部的常驻提示。

        为什么放这儿而不是状态栏：状态栏会被当前表信息覆盖，一闪就没了。
        而下面两件事都是**用户需要一直知道的**：

        - 另有实例被隐藏 —— 数据侧结论是「只改一份 = 只有一半模式生效」；
        - 规则文件过期 —— 说明游戏很可能更新过，而失效本身是静默的。
        """
        if self._dataset is None:
            self._sidebar_hint.setText("▲ 尚未加载数据源")
            return

        lines = [f"数据源：{self._dataset.root.name}"]

        if self._source is None:
            # 只读来源这件事要**常驻可见**：💾 保存点下去只会说一句"无法写回"，
            # 用户不该靠点一下才知道。目录来源（DataBaseCompiled/）与"还没打开"
            # 都是 `_source is None`，两种写法都说得通。
            lines.append("只读来源：不写回这个目录；要保存请用「打开…」选一个文件")

        stale = self._dataset.stale_role_ids
        if stale:
            lines.append(f"⚠ 规则文件已过期（无 {'、'.join(stale)}）· 建议重跑 make_rules.py")

        hidden = len(self._dataset.instance_ids) - len(self._visible_instance_ids())
        if hidden:
            lines.append(f"另有 {hidden} 个实例已隐藏（视图 → 显示全部实例）")

        self._sidebar_hint.setText("\n".join(lines))

    def _make_group_item(
        self, group_name: str, instance: Instance, members: list[str]
    ) -> QTreeWidgetItem:
        group_item = QTreeWidgetItem([group_name])
        group_item.setData(0, Qt.ItemDataRole.UserRole, None)
        group_item.setToolTip(0, f"{group_name}（{len(members)} 张表）")
        for table_name in members:
            group_item.addChild(self._make_table_item(instance, table_name))
        group_item.setExpanded(False)
        return group_item

    @staticmethod
    def _make_table_item(instance: Instance, table_name: str) -> QTreeWidgetItem:
        rows = instance.manifest_entry(table_name).get("rows")
        # 实测：异形表在 manifest 里报 rows=-1，那不是真实行数，宁可不显示。
        count = f"  ({rows})" if isinstance(rows, int) and rows >= 0 else ""
        label = f"{table_name}{count}"
        item = QTreeWidgetItem([label])
        item.setData(0, Qt.ItemDataRole.UserRole, ("table", instance.path_id, table_name))
        item.setToolTip(0, f"{table_name}\n{count.strip() or '行数未知'}")
        return item

    # ------------------------------------------------------------------ #
    # 实例切换
    # ------------------------------------------------------------------ #

    def _rebuild_instance_menu(self) -> None:
        menu = self._instance_menu
        menu.clear()
        if self._dataset is None:
            menu.setEnabled(False)
            return
        menu.setEnabled(True)
        for path_id in self._dataset.instance_ids:
            role = self._dataset.instance_roles.get(path_id)
            label = _role_label(role)
            suffix = f" · {label}" if label else ""
            action = QAction(f"{path_id}{suffix}", menu)
            action.setCheckable(True)
            action.triggered.connect(lambda _checked=False, pid=path_id: self._select_instance(pid))
            menu.addAction(action)
        self._sync_instance_menu()

    def _sync_instance_menu(self) -> None:
        """实例切换只剩 ☰ 菜单里那一个入口。

        顶栏那个下拉**取消**了（界面重做定稿 块 2：一行 36px 里塞不下，
        而且它属于"文件"那一类动作）。状态栏右下角仍然常驻「实例：52085」。
        """
        current = self._instance.path_id if self._instance is not None else ""
        for action in self._instance_menu.actions():
            action.setChecked(bool(current) and action.text().startswith(current))

    def _select_instance(self, path_id: str) -> None:
        """选中某个实例。**刻意不展开树节点**——折叠是导航策略的一部分。"""
        if self._dataset is None or path_id not in self._dataset.instances:
            return
        self._activate_instance(path_id)
        for i in range(self._tree.topLevelItemCount()):
            item = self._tree.topLevelItem(i)
            if item is None:
                continue
            payload = item.data(0, Qt.ItemDataRole.UserRole)
            if not payload or payload[0] != "instance" or payload[1] != path_id:
                continue
            self._tree.setCurrentItem(item)
            break
        self._sync_instance_menu()

    def _on_toggle_all_instances(self, checked: bool) -> None:
        self._show_all_instances = checked
        self._populate_tree()

        keep = self._instance.path_id if self._instance is not None else ""
        visible = self._visible_instance_ids()
        self._select_instance(keep if keep in visible else (visible[0] if visible else ""))

    def _missing_columns_note(self) -> str:
        """当前实例有哪几张表「比 `_schema.json` 少列」-> 一句状态栏后缀（空串 = 不缺）。

        ★ 2026-10-18（全量验证）：`DataBaseCompiled/` 现在是**多版本快照**（7 个实例），旧快照
        天然没有新列 —— loader 现在允许并把它记进 `Table.missing_columns`。但**只记不报 = 静默**：
        用户切到 52460 会看到一张少 15 列的表，界面上却什么提示都没有。这一行就是那个出口。
        """
        inst = self._instance
        if inst is None:
            return ""
        try:
            # ⚠ 走**清单里的列数**（`_manifest.json` 的 `keys`），⛔ 不逐表 `table()`：
            #   那会把 24 张表全读一遍（切实例时要多等 100~300 ms），而这个提示只需要「缺几张表、
            #   缺几列」。manifest 没有 keys（别种来源）时按「不缺」处理 —— ⛔ 不假报。
            tables = 0
            total = 0
            for name in inst.table_names:
                schema = inst.schema.get(name) if inst.schema else None
                if schema is None:
                    continue
                want = len(schema.columns)
                got = int(inst.manifest_entry(name).get("keys") or want)
                if want and got < want:
                    tables += 1
                    total += want - got
        except Exception:  # noqa: BLE001  —— 读数拿不到不许把状态栏搞崩
            return ""
        if not tables:
            return ""
        return f"（缺 {tables} 表 / {total} 列）"

    def _activate_instance(self, path_id: str) -> None:
        if self._dataset is None:
            return
        self._instance = self._dataset.instance(path_id)
        # 写盘通道先认这份实例 —— 别的页 set_instance 时会顺着它建变更集。
        self._edit_session.set_instance(self._instance)
        self._armory.set_instance(self._instance)
        self._unit_editor.set_instance(self._instance)
        self._country.set_instance(self._instance)
        self._spec.set_instance(self._instance)
        self._browser.set_instance(self._instance)
        self._search_panel.set_instance(self._instance)
        self._instance_label.setText(f"实例：{path_id}" + self._missing_columns_note())
        diag.set_context(instance=path_id)
        self._sync_instance_menu()
        # 词典窗口里那份"取值观测"跟着换数据源走（没点开过就不建窗口）。
        if self._dictionary is not None:
            self._dictionary.set_instance(self._instance)
        # 引用面板说的是"某张表的某一行"，换了实例就不再成立。
        self._refs_panel.clear()

    # ------------------------------------------------------------------ #
    # 响应
    # ------------------------------------------------------------------ #

    def _on_tree_selection(self, current: QTreeWidgetItem | None, _previous) -> None:
        if current is None:
            return
        payload = current.data(0, Qt.ItemDataRole.UserRole)
        if not payload:
            return
        kind, *rest = payload
        if kind == "instance":
            self._activate_instance(rest[0])
        elif kind == "table":
            path_id, table_name = rest
            if self._instance is None or self._instance.path_id != path_id:
                self._activate_instance(path_id)
            self._browser.select_table(table_name)

    def _on_table_changed(self, table_name: str) -> None:
        self._status_label.setText(f"当前表：{table_name}")
        # 换表 = 之前选中的那一行不作数了（新表选中行时会再发一次 row_selected）。
        self._refs_panel.clear()

    def _on_search_hit(self, table_name: str, row_id: int, column: str) -> None:
        """检索结果点一条：**Units 进单位编辑器，其它表在数据表页打开那一行**。

        为什么 Units 特殊：单位是这一版的主对象（军械库 + 单位编辑器），
        打开一个单位就该进那一屏，而不是让用户再去数据表里看一行。

        其它表走**现成的数据表页**：选中那一行，下面那块详情面板就是这个行的
        **全部列**（`选中行的字段（完整值，不截断）`）—— 检索页不自己造一个
        "行详情"控件，那会是第二套口径（设计稿：中间那栏显示这一行的全部列）。

        顺序不能反：先切页 → 再选表 → 最后定位。—— 页面不可见时布局尚未生效。
        """
        if self._instance is None:
            return
        if table_name == "Units":
            self._open_unit_editor(row_id)
            self._status_label.setText(f"检索：打开单位 Id {row_id}")
            return
        self.show_page(PAGE_TABLE)
        self._browser.select_table(table_name)
        row = _row_index_of_id(self._instance, table_name, row_id)
        if row < 0:
            self._status_label.setText(f"{table_name} 里找不到 Id {row_id}")
            return
        self._browser.select_row(row)
        if column:
            self._browser.select_cell(row, column)
            self._status_label.setText(f"已打开 {table_name} #{row_id} · 命中列 {column}")
            return
        # 右栏点值那一格 / 行尾 ↗ 那条路**没有"命中哪一列"可言**（链接只知道目标表+行）——
        # 别在状态栏拖一个空列名（2026-10-02 真机看到的是"已打开 Countries #2 · 命中列 "）。
        self._status_label.setText(f"已打开 {table_name} #{row_id}")

    def _on_armory_unit_selected(self, unit_id: int) -> None:
        self._status_label.setText(f"军械库 · 选中单位 Id {unit_id}")

    def _on_armory_card_failed(self, unit_id: int, reason: str) -> None:
        """卡片装配失败：状态栏写原因（卡片上还写了一整块，终端有完整 traceback）。"""
        self._status_label.setText(f"军械库 · 单位 Id {unit_id} 的卡片装配失败：{reason}")

    def _open_unit_editor(self, unit_id: int) -> None:
        """双击一张卡：进单位编辑器（数据一个字节不动 —— 改动只落在变更集里）。"""
        if self._instance is None:
            return
        self._unit_editor.open_unit(unit_id)
        # 用户 2026-10-01 口径：双击单位**默认进编成台**（有编成台的形态）。
        # 三种形态都有自己的编成台（见 bench_page.bench_kind），所以这里**都**能真落上去。
        landed = self._unit_editor.land_on_bench()
        self.show_page(PAGE_UNIT_EDITOR)
        diag.set_context(page="单位编辑器", unit=unit_id)
        diag.event("进单位编辑器", unit=unit_id, unit_name=self._unit_editor.unit_name())
        if landed:
            self._status_label.setText("编成台：点一格 = 选中它，右栏是它的全部列")

    def _close_unit_editor(self) -> None:
        """回军械库（面包屑最左边那一级 / Esc）。

        改动**不还原** —— 现在是"改一格就立刻写盘"，回军械库看到的就该是
        刚改完的样子（旧口径的"离开即还原"是自动保存之前的规则）。
        """
        message = self._unit_editor.leave()
        self.show_page(PAGE_ARMORY)
        self._armory.refresh()
        if message:
            self._status_label.setText(message)

    def _open_table_row(self, table_name: str, row_id: int) -> None:
        """编辑器里点「落在哪」-> 跳到数据表页的那一行。"""
        if self._instance is None:
            return
        self.show_page(PAGE_TABLE)
        self._browser.select_table(table_name)
        row = _row_index_of_id(self._instance, table_name, row_id)
        if row >= 0:
            self._browser.select_row(row)
            self._status_label.setText(f"已跳到 {table_name} 第 {row} 行（Id {row_id}）")
        else:
            self._status_label.setText(f"{table_name} 里找不到 Id {row_id}")

    def _focus_search(self) -> None:
        """顶栏「🔎 按名查 Id（F3）」：切到检索页 **并**把光标放进那个框。

        ❗ 聚焦要**延到下一轮事件循环**：`show_page` 刚 `setCurrentWidget`，新页还没被
        映射出来，这时候 `setFocus()` 是空操作 —— 表现就是用户报的「搜索栏要点两遍」
        （第一下只切了页，还得再点一下那个框）。同一个坑见 `InlineEditor.focus_input`
        的说明：控件还没上屏时 setFocus 不算数。
        """
        self.show_page(PAGE_SEARCH)
        QTimer.singleShot(0, self._search_panel.focus_input)

    # ------------------------------------------------------------------ #
    # 杂项
    # ------------------------------------------------------------------ #

    def _set_busy(self, busy: bool, message: str) -> None:
        self._status_label.setText(message)
        self._progress.setVisible(busy)
        if busy:
            self._progress.setRange(0, 0)
        else:
            self._progress.setRange(0, 100)


def _row_index_of_id(instance, table_name: str, row_id: int) -> int:
    """表里 Id == row_id 的行下标；找不到 -1（**不猜**）。"""
    try:
        table = instance.table(table_name)
        id_index = table.column_index("Id")
    except (KeyError, AttributeError):
        return -1  # 表名可能来自旧数据 / 已经不是那个数据源
    for position, row in enumerate(table.rows):
        if row[id_index] == row_id:
            return position
    return -1
