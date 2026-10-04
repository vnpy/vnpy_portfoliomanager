"""投资组合界面组件。"""
from typing import cast

from vnpy.trader.object import TradeData
from vnpy.trader.constant import Direction
from vnpy.event.engine import Event
from vnpy.trader.ui import QtWidgets, QtCore, QtGui

from vnpy.trader.engine import MainEngine, EventEngine
from vnpy.trader.ui.widget import (
    BaseCell,
    EnumCell,
    DirectionCell,
    TimeCell
)

from ..engine import (
    APP_NAME,
    EVENT_PM_CONTRACT,
    EVENT_PM_PORTFOLIO,
    EVENT_PM_TRADE,
    PortfolioEngine
)


RED_COLOR: QtGui.QColor = QtGui.QColor("red")
GREEN_COLOR: QtGui.QColor = QtGui.QColor("green")
WHITE_COLOR: QtGui.QColor = QtGui.QColor("white")


class PortfolioManager(QtWidgets.QWidget):
    """投资组合主界面。"""

    signal_contract: QtCore.Signal = QtCore.Signal(Event)
    signal_portfolio: QtCore.Signal = QtCore.Signal(Event)
    signal_trade: QtCore.Signal = QtCore.Signal(Event)

    def __init__(self, main_engine: MainEngine, event_engine: EventEngine) -> None:
        """取得组合引擎，初始化界面并刷新成交。"""
        super().__init__()

        self.main_engine: MainEngine = main_engine
        self.event_engine: EventEngine = event_engine

        self.portfolio_engine: PortfolioEngine = cast(PortfolioEngine, main_engine.get_engine(APP_NAME))

        self.contract_items: dict[tuple[str, str], QtWidgets.QTreeWidgetItem] = {}
        self.portfolio_items: dict[str, QtWidgets.QTreeWidgetItem] = {}

        self.init_ui()
        self.register_event()
        self.update_trades()

    def init_ui(self) -> None:
        """搭建组合树、成交表，以及展开、折叠、列宽、刷新频率和组合过滤。"""
        self.setWindowTitle("投资组合")

        labels: list[str] = [
            "组合名称",
            "本地代码",
            "开盘仓位",
            "当前仓位",
            "交易盈亏",
            "持仓盈亏",
            "总盈亏",
            "多头成交",
            "空头成交"
        ]
        self.column_count: int = len(labels)

        self.tree: QtWidgets.QTreeWidget = QtWidgets.QTreeWidget()
        self.tree.setColumnCount(self.column_count)
        self.tree.setHeaderLabels(labels)
        self.tree.header().setDefaultAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.tree.header().setStretchLastSection(False)

        delegate: TreeDelegate = TreeDelegate()
        self.tree.setItemDelegate(delegate)

        self.monitor: PortfolioTradeMonitor = PortfolioTradeMonitor()

        expand_button: QtWidgets.QPushButton = QtWidgets.QPushButton("全部展开")
        expand_button.clicked.connect(self.tree.expandAll)

        collapse_button: QtWidgets.QPushButton = QtWidgets.QPushButton("全部折叠")
        collapse_button.clicked.connect(self.tree.collapseAll)

        resize_button: QtWidgets.QPushButton = QtWidgets.QPushButton("调整列宽")
        resize_button.clicked.connect(self.resize_columns)

        interval_spin: QtWidgets.QSpinBox = QtWidgets.QSpinBox()
        interval_spin.setMinimum(1)
        interval_spin.setMaximum(60)
        interval_spin.setSuffix("秒")
        interval_spin.setValue(self.portfolio_engine.get_timer_interval())
        interval_spin.valueChanged.connect(self.portfolio_engine.set_timer_interval)

        self.reference_combo: QtWidgets.QComboBox = QtWidgets.QComboBox()
        self.reference_combo.setMinimumWidth(200)
        self.reference_combo.addItem("")
        self.reference_combo.currentIndexChanged.connect(self.set_reference_filter)

        hbox1: QtWidgets.QHBoxLayout = QtWidgets.QHBoxLayout()
        hbox1.addWidget(expand_button)
        hbox1.addWidget(collapse_button)
        hbox1.addWidget(resize_button)
        hbox1.addStretch()
        hbox1.addWidget(QtWidgets.QLabel("刷新频率"))
        hbox1.addWidget(interval_spin)
        hbox1.addStretch()
        hbox1.addWidget(QtWidgets.QLabel("组合成交"))
        hbox1.addWidget(self.reference_combo)

        hbox2: QtWidgets.QHBoxLayout = QtWidgets.QHBoxLayout()
        hbox2.addWidget(self.tree)
        hbox2.addWidget(self.monitor)

        vbox: QtWidgets.QVBoxLayout = QtWidgets.QVBoxLayout()
        vbox.addLayout(hbox1)
        vbox.addLayout(hbox2)
        self.setLayout(vbox)

    def register_event(self) -> None:
        """把合约、组合和成交事件接到界面。"""
        self.signal_contract.connect(self.process_contract_event)
        self.signal_portfolio.connect(self.process_portfolio_event)
        self.signal_trade.connect(self.process_trade_event)

        self.event_engine.register(EVENT_PM_CONTRACT, self.signal_contract.emit)
        self.event_engine.register(EVENT_PM_PORTFOLIO, self.signal_portfolio.emit)
        self.event_engine.register(EVENT_PM_TRADE, self.signal_trade.emit)

    def update_trades(self) -> None:
        """把带有 reference 属性的历史成交填入成交表。"""
        trades: list[TradeData] = self.main_engine.get_all_trades()
        trade: TradeData
        for trade in trades:
            # 过滤掉没有用reference的成交
            if hasattr(trade, "reference"):
                self.monitor.update_trade(trade)

    def get_portfolio_item(self, reference: str) -> QtWidgets.QTreeWidgetItem:
        """返回组合节点；没有则创建顶级节点，并加入组合下拉框。"""
        portfolio_item: QtWidgets.QTreeWidgetItem | None = self.portfolio_items.get(reference, None)

        if not portfolio_item:
            portfolio_item = QtWidgets.QTreeWidgetItem()
            portfolio_item.setText(0, reference)
            i: int
            for i in range(2, self.column_count):
                portfolio_item.setTextAlignment(i, QtCore.Qt.AlignmentFlag.AlignCenter)

            self.portfolio_items[reference] = portfolio_item
            self.tree.addTopLevelItem(portfolio_item)

            self.reference_combo.addItem(reference)

        return portfolio_item

    def get_contract_item(self, reference: str, vt_symbol: str) -> QtWidgets.QTreeWidgetItem:
        """返回合约节点；没有则挂到对应组合下。"""
        key: tuple[str, str] = (reference, vt_symbol)
        contract_item: QtWidgets.QTreeWidgetItem | None = self.contract_items.get(key, None)

        if not contract_item:
            contract_item = QtWidgets.QTreeWidgetItem()
            contract_item.setText(1, vt_symbol)
            i: int
            for i in range(2, self.column_count):
                contract_item.setTextAlignment(i, QtCore.Qt.AlignmentFlag.AlignCenter)

            self.contract_items[key] = contract_item

            portfolio_item: QtWidgets.QTreeWidgetItem = self.get_portfolio_item(reference)
            portfolio_item.addChild(contract_item)

        return contract_item

    def process_contract_event(self, event: Event) -> None:
        """用合约结果刷新树节点上的仓位、盈亏和成交量，并按盈亏着色。"""
        contract_result: dict = event.data

        contract_item: QtWidgets.QTreeWidgetItem = self.get_contract_item(
            contract_result["reference"],
            contract_result["vt_symbol"]
        )
        contract_item.setText(2, str(contract_result["open_pos"]))
        contract_item.setText(3, str(contract_result["last_pos"]))
        contract_item.setText(4, str(contract_result["trading_pnl"]))
        contract_item.setText(5, str(contract_result["holding_pnl"]))
        contract_item.setText(6, str(contract_result["total_pnl"]))
        contract_item.setText(7, str(contract_result["long_volume"]))
        contract_item.setText(8, str(contract_result["short_volume"]))

        self.update_item_color(contract_item, contract_result)

    def process_portfolio_event(self, event: Event) -> None:
        """用组合结果刷新交易盈亏、持仓盈亏和总盈亏，并着色。"""
        portfolio_result: dict = event.data

        portfolio_item: QtWidgets.QTreeWidgetItem = self.get_portfolio_item(portfolio_result["reference"])
        portfolio_item.setText(4, str(portfolio_result["trading_pnl"]))
        portfolio_item.setText(5, str(portfolio_result["holding_pnl"]))
        portfolio_item.setText(6, str(portfolio_result["total_pnl"]))

        self.update_item_color(portfolio_item, portfolio_result)

    def process_trade_event(self, event: Event) -> None:
        """把新成交插入成交表。"""
        trade: TradeData = event.data
        self.monitor.update_trade(trade)

    def update_item_color(
        self,
        item: QtWidgets.QTreeWidgetItem,
        result: dict
    ) -> None:
        """交易盈亏、持仓盈亏和总盈亏大于 0 显示红色，小于 0 显示绿色，等于 0 显示白色。"""
        start_column: int = 4
        n: int
        pnl: float
        for n, pnl in enumerate([
            result["trading_pnl"],
            result["holding_pnl"],
            result["total_pnl"]
        ]):
            i: int = n + start_column

            if pnl > 0:
                item.setForeground(i, RED_COLOR)
            elif pnl < 0:
                item.setForeground(i, GREEN_COLOR)
            else:
                item.setForeground(i, WHITE_COLOR)

    def resize_columns(self) -> None:
        """按内容调整树的每一列宽度。"""
        i: int
        for i in range(self.column_count):
            self.tree.resizeColumnToContents(i)

    def set_reference_filter(self, filter: str) -> None:
        """忽略传入值，改用组合下拉框的当前文本过滤成交表。"""
        filter = self.reference_combo.currentText()
        self.monitor.set_filter(filter)

    def show(self) -> None:
        """最大化显示窗口。"""
        self.showMaximized()


class PortfolioTradeMonitor(QtWidgets.QTableWidget):
    """组合成交表。"""

    def __init__(self) -> None:
        """初始化成交表，过滤条件为空。"""
        super().__init__()

        self.init_ui()
        self.filter: str = ""

    def init_ui(self) -> None:
        """设置成交表列，并隐藏行号、禁止编辑。"""
        labels: list[str] = [
            "组合",
            "成交号",
            "委托号",
            "代码",
            "交易所",
            "方向",
            "开平",
            "价格",
            "数量",
            "时间",
            "接口",
        ]
        self.setColumnCount(len(labels))
        self.setHorizontalHeaderLabels(labels)
        self.verticalHeader().setVisible(False)
        self.setEditTriggers(self.EditTrigger.NoEditTriggers)

    def update_trade(self, trade: TradeData) -> None:
        """在表格首行插入成交；过滤条件与组合名不一致时隐藏该行。"""
        self.insertRow(0)

        reference_cell: BaseCell = BaseCell(getattr(trade, "reference"), trade)  # noqa: B009
        tradeid_cell: BaseCell = BaseCell(trade.tradeid, trade)
        orderid_cell: BaseCell = BaseCell(trade.orderid, trade)
        symbol_cell: BaseCell = BaseCell(trade.symbol, trade)
        exchange_cell: EnumCell = EnumCell(trade.exchange, trade)
        direction_cell: DirectionCell = DirectionCell(cast(Direction, trade.direction), trade)
        offset_cell: EnumCell = EnumCell(trade.offset, trade)
        price_cell: BaseCell = BaseCell(trade.price, trade)
        volume_cell: BaseCell = BaseCell(trade.volume, trade)
        datetime_cell: TimeCell = TimeCell(trade.datetime, trade)
        gateway_cell: BaseCell = BaseCell(trade.gateway_name, trade)

        self.setItem(0, 0, reference_cell)
        self.setItem(0, 1, tradeid_cell)
        self.setItem(0, 2, orderid_cell)
        self.setItem(0, 3, symbol_cell)
        self.setItem(0, 4, exchange_cell)
        self.setItem(0, 5, direction_cell)
        self.setItem(0, 6, offset_cell)
        self.setItem(0, 7, price_cell)
        self.setItem(0, 8, volume_cell)
        self.setItem(0, 9, datetime_cell)
        self.setItem(0, 10, gateway_cell)

        if self.filter and getattr(trade, "reference") != self.filter:  # noqa: B009
            self.hideRow(0)

    def set_filter(self, filter: str) -> None:
        """记录过滤组合名；空字符串显示全部行，否则只显示组合名相同的行。"""
        self.filter = filter

        row: int
        for row in range(self.rowCount()):
            if not filter:
                self.showRow(row)
            else:
                item: QtWidgets.QTableWidgetItem | None = self.item(row, 0)
                if item and item.text() == filter:
                    self.showRow(row)
                else:
                    self.hideRow(row)


class TreeDelegate(QtWidgets.QStyledItemDelegate):
    """组合树的单元格委托。"""

    def sizeHint(
        self,
        option: QtWidgets.QStyleOptionViewItem,
        index: QtCore.QModelIndex | QtCore.QPersistentModelIndex
    ) -> QtCore.QSize:
        """在原有尺寸上把高度设为 40。"""
        size: QtCore.QSize = super().sizeHint(option, index)
        size.setHeight(40)
        return size
