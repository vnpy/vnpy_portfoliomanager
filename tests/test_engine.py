from datetime import datetime
from pathlib import Path

from vnpy.event import Event
from vnpy.trader.constant import Direction, Exchange, Product
from vnpy.trader.event import EVENT_ORDER, EVENT_TIMER, EVENT_TRADE
from vnpy.trader.object import ContractData, OrderData, SubscribeRequest, TickData, TradeData
from vnpy.trader.setting import SETTINGS
from vnpy.trader.utility import TEMP_DIR
from vnpy_portfoliomanager.base import ContractResult
from vnpy_portfoliomanager.engine import (
    EVENT_PM_CONTRACT,
    EVENT_PM_PORTFOLIO,
    PortfolioEngine,
)


BOOK: str = "book"
RB_SYMBOL: str = "rb2501"
AG_SYMBOL: str = "ag2506"
CU_SYMBOL: str = "cu2502"
RB: str = f"{RB_SYMBOL}.{Exchange.SHFE.value}"
AG: str = f"{AG_SYMBOL}.{Exchange.SHFE.value}"
CU: str = f"{CU_SYMBOL}.{Exchange.SHFE.value}"


class FakeMainEngine:
    def __init__(self) -> None:
        self.contracts: dict[str, ContractData] = {}
        self.ticks: dict[str, TickData] = {}
        self.subscriptions: list[SubscribeRequest] = []

    def get_contract(self, vt_symbol: str) -> ContractData | None:
        return self.contracts.get(vt_symbol)

    def get_tick(self, vt_symbol: str) -> TickData | None:
        return self.ticks.get(vt_symbol)

    def subscribe(self, req: SubscribeRequest, gateway_name: str) -> None:
        self.subscriptions.append(req)


class FakeEventEngine:
    def __init__(self) -> None:
        self.events: list[Event] = []

    def register(self, event_type: str, handler: object) -> None:
        return None

    def put(self, event: Event) -> None:
        self.events.append(event)


def make_contract(symbol: str, size: float) -> ContractData:
    return ContractData(
        symbol=symbol,
        exchange=Exchange.SHFE,
        name=symbol,
        product=Product.FUTURES,
        size=size,
        pricetick=1,
        gateway_name="FAKE",
    )


def make_tick(contract: ContractData, last_price: float, pre_close: float) -> TickData:
    return TickData(
        symbol=contract.symbol,
        exchange=contract.exchange,
        datetime=datetime(2024, 1, 2, 9, 0),
        last_price=last_price,
        pre_close=pre_close,
        gateway_name="FAKE",
    )


def make_order(orderid: str, symbol: str, reference: str) -> OrderData:
    return OrderData(
        symbol=symbol,
        exchange=Exchange.SHFE,
        orderid=orderid,
        direction=Direction.LONG,
        reference=reference,
        gateway_name="FAKE",
    )


def make_trade(
    order: OrderData,
    tradeid: str,
    direction: Direction,
    volume: float,
    price: float,
) -> TradeData:
    return TradeData(
        symbol=order.symbol,
        exchange=order.exchange,
        orderid=order.orderid,
        tradeid=tradeid,
        direction=direction,
        price=price,
        volume=volume,
        datetime=datetime(2024, 1, 2, 9, 0),
        gateway_name=order.gateway_name,
    )


def test_settings_dir_is_temporary() -> None:
    assert SETTINGS["log.file"] is False
    assert SETTINGS["log.console"] is False
    assert TEMP_DIR.resolve() == Path.cwd().resolve().joinpath(".vntrader")
    assert TEMP_DIR.resolve() != Path.home().resolve().joinpath(".vntrader")


class TestPortfolioPnl:
    def test_trades_and_timer_update_position_and_pnl(self) -> None:
        # 成交立刻改 last_pos。trading_pnl 要等满 timer_interval 次定时事件，按最新价重算。
        # open_pos 保持 0，所以 holding_pnl 保持 0。两个合约的仓位分开记。
        main: FakeMainEngine = FakeMainEngine()
        rb_contract: ContractData = make_contract(RB_SYMBOL, 10)
        ag_contract: ContractData = make_contract(AG_SYMBOL, 15)
        main.contracts[rb_contract.vt_symbol] = rb_contract
        main.contracts[ag_contract.vt_symbol] = ag_contract
        main.ticks[rb_contract.vt_symbol] = make_tick(rb_contract, 110, 100)
        main.ticks[ag_contract.vt_symbol] = make_tick(ag_contract, 70, 90)

        events: FakeEventEngine = FakeEventEngine()
        engine: PortfolioEngine = PortfolioEngine(main, events)  # type: ignore[arg-type]

        rb_order: OrderData = make_order("1", RB_SYMBOL, BOOK)
        ag_order: OrderData = make_order("2", AG_SYMBOL, BOOK)
        engine.process_order_event(Event(EVENT_ORDER, rb_order))
        rb_order.reference = "other"
        engine.process_order_event(Event(EVENT_ORDER, rb_order))
        assert rb_order.reference == BOOK
        assert engine.order_reference_map[rb_order.vt_orderid] == BOOK
        engine.process_order_event(Event(EVENT_ORDER, ag_order))

        cu_order: OrderData = make_order("9", CU_SYMBOL, "")
        engine.process_order_event(Event(EVENT_ORDER, cu_order))
        engine.process_trade_event(
            Event(EVENT_TRADE, make_trade(cu_order, "cu", Direction.LONG, 1, 10))
        )
        assert CU not in {vt_symbol for _reference, vt_symbol in engine.contract_results}

        rb_trade: TradeData = make_trade(rb_order, "t1", Direction.LONG, 2, 100)
        engine.process_trade_event(Event(EVENT_TRADE, rb_trade))
        engine.process_trade_event(Event(EVENT_TRADE, rb_trade))
        engine.process_trade_event(
            Event(EVENT_TRADE, make_trade(ag_order, "t2", Direction.SHORT, 3, 80))
        )

        rb_result: ContractResult = engine.contract_results[(BOOK, RB)]
        ag_result: ContractResult = engine.contract_results[(BOOK, AG)]
        assert rb_result.open_pos == 0
        assert rb_result.last_pos == 2
        assert ag_result.open_pos == 0
        assert ag_result.last_pos == -3
        assert rb_result.trading_pnl == 0
        assert ag_result.trading_pnl == 0
        assert rb_result.long_volume == 0
        assert ag_result.short_volume == 0
        assert engine.portfolio_results == {}
        assert rb_trade.reference == BOOK

        for _index in range(engine.timer_interval - 1):
            engine.process_timer_event(Event(EVENT_TIMER))
        assert engine.timer_count == engine.timer_interval - 1
        assert rb_result.trading_pnl == 0
        assert engine.portfolio_results == {}

        engine.process_timer_event(Event(EVENT_TIMER))
        assert engine.timer_count == 0
        # rb: (110 - 100) * 2 * 10 = 200。ag: (80 - 70) * 3 * 15 = 450。
        assert rb_result.long_volume == 2
        assert rb_result.long_cost == 2000
        assert rb_result.short_volume == 0
        assert rb_result.trading_pnl == 200
        assert rb_result.holding_pnl == 0
        assert rb_result.total_pnl == 200
        assert ag_result.short_volume == 3
        assert ag_result.short_cost == 3600
        assert ag_result.long_volume == 0
        assert ag_result.trading_pnl == 450
        assert ag_result.holding_pnl == 0
        assert ag_result.total_pnl == 450
        assert rb_result.last_pos == 2
        assert ag_result.last_pos == -3
        book = engine.portfolio_results[BOOK]
        assert book.trading_pnl == 650
        assert book.holding_pnl == 0
        assert book.total_pnl == 650

        contract_rows: list[dict] = [
            event.data for event in events.events if event.type == EVENT_PM_CONTRACT
        ]
        assert {row["vt_symbol"] for row in contract_rows} == {RB, AG}
        portfolio_rows: list[dict] = [
            event.data for event in events.events if event.type == EVENT_PM_PORTFOLIO
        ]
        assert portfolio_rows[-1]["reference"] == BOOK
        assert portfolio_rows[-1]["trading_pnl"] == 650
        assert portfolio_rows[-1]["holding_pnl"] == 0
        assert portfolio_rows[-1]["total_pnl"] == 650

        rb_order_2: OrderData = make_order("3", RB_SYMBOL, BOOK)
        engine.process_order_event(Event(EVENT_ORDER, rb_order_2))
        engine.process_trade_event(
            Event(EVENT_TRADE, make_trade(rb_order_2, "t3", Direction.LONG, 1, 120))
        )
        assert rb_result.last_pos == 3
        assert rb_result.trading_pnl == 200
        assert ag_result.last_pos == -3

        for _index in range(engine.timer_interval):
            engine.process_timer_event(Event(EVENT_TIMER))
        # 累计多头 3 手，成本 3200，最新价值 3300，交易盈亏变为 100。ag 仍是 450。
        assert rb_result.long_volume == 3
        assert rb_result.long_cost == 3200
        assert rb_result.trading_pnl == 100
        assert rb_result.holding_pnl == 0
        assert rb_result.total_pnl == 100
        assert rb_result.last_pos == 3
        assert ag_result.trading_pnl == 450
        assert ag_result.total_pnl == 450
        assert ag_result.last_pos == -3
        assert book.trading_pnl == 550
        assert book.holding_pnl == 0
        assert book.total_pnl == 550
        assert portfolio_rows[-1]["total_pnl"] == 650
        portfolio_rows = [event.data for event in events.events if event.type == EVENT_PM_PORTFOLIO]
        assert portfolio_rows[-1]["total_pnl"] == 550
        assert portfolio_rows[-1]["trading_pnl"] == 550
        assert portfolio_rows[-1]["holding_pnl"] == 0
