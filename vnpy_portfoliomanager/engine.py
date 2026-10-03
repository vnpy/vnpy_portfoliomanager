"""按委托引用统计组合盈亏的引擎。"""
from typing import Any
from collections.abc import Callable
from datetime import datetime

from vnpy.event import Event
from vnpy.trader.engine import (
    MainEngine,
    EventEngine,
    BaseEngine
)
from vnpy.trader.event import (
    EVENT_ORDER,
    EVENT_CONTRACT,
    EVENT_TIMER,
    EVENT_TRADE
)
from vnpy.trader.object import (
    ContractData,
    OrderData,
    TradeData,
    SubscribeRequest,
    TickData
)
from vnpy.trader.utility import load_json, save_json

from .base import ContractResult, PortfolioResult


APP_NAME = "PortfolioManager"

EVENT_PM_CONTRACT = "ePmContract"
EVENT_PM_PORTFOLIO = "ePmPortfolio"
EVENT_PM_TRADE = "ePmTrade"


class PortfolioEngine(BaseEngine):
    """按委托引用统计组合盈亏的引擎。"""
    setting_filename: str = "portfolio_manager_setting.json"
    data_filename: str = "portfolio_manager_data.json"
    order_filename: str = "portfolio_manager_order.json"

    def __init__(self, main_engine: MainEngine, event_engine: EventEngine) -> None:
        """初始化盈亏缓存，并加载配置、持仓数据、当日委托引用和事件监听。"""
        super().__init__(main_engine, event_engine, APP_NAME)

        self.get_tick: Callable[[str], TickData | None] = self.main_engine.get_tick
        self.get_contract: Callable[[str], ContractData | None] = self.main_engine.get_contract

        self.subscribed: set[str] = set()
        self.result_symbols: set[str] = set()
        self.order_reference_map: dict[str, str] = {}
        self.contract_results: dict[tuple[str, str], ContractResult] = {}
        self.portfolio_results: dict[str, PortfolioResult] = {}

        self.timer_count: int = 0
        self.timer_interval: int = 5

        self.load_setting()
        self.load_data()
        self.load_order()
        self.register_event()

    def register_event(self) -> None:
        """监听委托、成交、定时器和合约事件。"""
        self.event_engine.register(EVENT_ORDER, self.process_order_event)
        self.event_engine.register(EVENT_TRADE, self.process_trade_event)
        self.event_engine.register(EVENT_TIMER, self.process_timer_event)
        self.event_engine.register(EVENT_CONTRACT, self.process_contract_event)

    def process_order_event(self, event: Event) -> None:
        """首次记下委托引用，之后用已记录的引用回填委托。"""
        order: OrderData = event.data

        if order.vt_orderid not in self.order_reference_map:
            self.order_reference_map[order.vt_orderid] = order.reference
        else:
            order.reference = self.order_reference_map[order.vt_orderid]

    def process_trade_event(self, event: Event) -> None:
        """没有委托引用则忽略；否则更新合约结果并推送成交，尚未订阅且能找到合约时再订阅行情。"""
        trade: TradeData = event.data

        reference: str = self.order_reference_map.get(trade.vt_orderid, "")
        if not reference:
            return

        vt_symbol: str = trade.vt_symbol
        key: tuple[str, str] = (reference, vt_symbol)

        contract_result: ContractResult | None = self.contract_results.get(key, None)
        if not contract_result:
            contract_result = ContractResult(self, reference, vt_symbol)
            self.contract_results[key] = contract_result

        contract_result.update_trade(trade)

        # 添加成交数据
        trade.reference = reference
        self.event_engine.put(Event(EVENT_PM_TRADE, trade))

        # 自动订阅tick数据
        if trade.vt_symbol in self.subscribed:
            return

        contract: ContractData | None = self.main_engine.get_contract(trade.vt_symbol)
        if not contract:
            return

        req: SubscribeRequest = SubscribeRequest(contract.symbol, contract.exchange)
        self.main_engine.subscribe(req, contract.gateway_name)

    def process_timer_event(self, event: Event) -> None:
        """每隔 timer_interval 次定时事件重算合约和组合盈亏并推送。"""
        self.timer_count += 1
        if self.timer_count < self.timer_interval:
            return
        self.timer_count = 0

        for portfolio_result in self.portfolio_results.values():
            portfolio_result.clear_pnl()

        for contract_result in self.contract_results.values():
            contract_result.calculate_pnl()

            portfolio_result = self.get_portfolio_result(contract_result.reference)
            portfolio_result.trading_pnl += contract_result.trading_pnl
            portfolio_result.holding_pnl += contract_result.holding_pnl
            portfolio_result.total_pnl += contract_result.total_pnl

            event = Event(EVENT_PM_CONTRACT, contract_result.get_data())
            self.event_engine.put(event)

        for portfolio_result in self.portfolio_results.values():
            event = Event(EVENT_PM_PORTFOLIO, portfolio_result.get_data())
            self.event_engine.put(event)

    def process_contract_event(self, event: Event) -> None:
        """合约已在结果集合中时订阅行情，并记入已订阅集合。"""
        contract: ContractData = event.data
        if contract.vt_symbol not in self.result_symbols:
            return

        req: SubscribeRequest = SubscribeRequest(contract.symbol, contract.exchange)
        self.main_engine.subscribe(req, contract.gateway_name)

        self.subscribed.add(contract.vt_symbol)

    def load_data(self) -> None:
        """读取保存的仓位；日期不是今天时改用上次仓位并重新保存。"""
        data: dict | None = load_json(self.data_filename)
        if not data:
            return

        today: str = datetime.now().strftime("%Y-%m-%d")
        date_changed: bool = False

        date: str = data.pop("date")
        for key, d in data.items():
            reference, vt_symbol = key.split(",")

            if date == today:
                pos: float = d["open_pos"]
            else:
                pos = d["last_pos"]
                date_changed = True

            self.result_symbols.add(vt_symbol)
            self.contract_results[(reference, vt_symbol)] = ContractResult(
                self,
                reference,
                vt_symbol,
                pos
            )

        # 当数据改变时重新保存
        if date_changed:
            self.save_data()

    def save_data(self) -> None:
        """按当天日期保存每个合约结果的开盘仓位和当前仓位。"""
        data: dict[str, Any] = {"date": datetime.now().strftime("%Y-%m-%d")}

        for contract_result in self.contract_results.values():
            key: str = f"{contract_result.reference},{contract_result.vt_symbol}"
            data[key] = {
                "open_pos": contract_result.open_pos,
                "last_pos": contract_result.last_pos
            }

        save_json(self.data_filename, data)

    def load_setting(self) -> None:
        """配置里有定时器间隔时读取它。"""
        setting: dict = load_json(self.setting_filename)
        if "timer_interval" in setting:
            self.timer_interval = setting["timer_interval"]

    def save_setting(self) -> None:
        """把定时器间隔写入配置文件。"""
        setting: dict[str, int] = {"timer_interval": self.timer_interval}
        save_json(self.setting_filename, setting)

    def load_order(self) -> None:
        """仅当文件日期是今天时恢复委托引用映射。"""
        order_data: dict = load_json(self.order_filename)

        date: str = order_data.get("date", "")
        today: str = datetime.now().strftime("%Y-%m-%d")
        if date == today:
            self.order_reference_map = order_data["data"]

    def save_order(self) -> None:
        """按当天日期保存委托引用映射。"""
        order_data: dict[str, Any] = {
            "date": datetime.now().strftime("%Y-%m-%d"),
            "data": self.order_reference_map
        }
        save_json(self.order_filename, order_data)

    def close(self) -> None:
        """关闭前保存配置、仓位数据和委托引用。"""
        self.save_setting()
        self.save_data()
        self.save_order()

    def get_portfolio_result(self, reference: str) -> PortfolioResult:
        """返回组合盈亏结果，没有则创建并缓存。"""
        portfolio_result: PortfolioResult | None = self.portfolio_results.get(reference, None)
        if not portfolio_result:
            portfolio_result = PortfolioResult(reference)
            self.portfolio_results[reference] = portfolio_result
        return portfolio_result

    def set_timer_interval(self, interval: int) -> None:
        """设置定时计算间隔。"""
        self.timer_interval = interval

    def get_timer_interval(self) -> int:
        """返回定时计算间隔。"""
        return self.timer_interval
