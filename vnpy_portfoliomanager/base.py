"""合约与组合的盈亏结果。"""
from typing import TYPE_CHECKING

from vnpy.trader.object import TickData, TradeData, ContractData
from vnpy.trader.constant import Direction

if TYPE_CHECKING:
    from .engine import PortfolioEngine


class ContractResult:
    """按合约统计某个组合内的仓位和盈亏。"""

    def __init__(
        self,
        engine: "PortfolioEngine",
        reference: str,
        vt_symbol: str,
        open_pos: float = 0
    ) -> None:
        """记录组合引用、合约和开盘仓位，并清零盈亏与成交统计。"""
        super().__init__()

        self.engine: PortfolioEngine = engine

        self.reference: str = reference
        self.vt_symbol: str = vt_symbol

        self.open_pos: float = open_pos
        self.last_pos: float = open_pos

        self.trading_pnl: float = 0
        self.holding_pnl: float = 0
        self.total_pnl: float = 0

        self.trades: dict[str, TradeData] = {}
        self.new_trades: list[TradeData] = []

        self.long_volume: float = 0
        self.short_volume: float = 0
        self.long_cost: float = 0
        self.short_cost: float = 0

    def update_trade(self, trade: TradeData) -> None:
        """忽略重复成交，否则记下成交并按方向增减当前仓位。"""
        # 过滤重复成交
        if trade.vt_tradeid in self.trades:
            return
        self.trades[trade.vt_tradeid] = trade
        self.new_trades.append(trade)

        if trade.direction == Direction.LONG:
            self.last_pos += trade.volume
        else:
            self.last_pos -= trade.volume

    def calculate_pnl(self) -> None:
        """没有合约或行情时直接返回，否则按最新价计算交易盈亏，并按昨收和开盘仓位计算持仓盈亏。"""
        vt_symbol: str = self.vt_symbol

        contract: ContractData | None = self.engine.get_contract(vt_symbol)
        tick: TickData | None = self.engine.get_tick(vt_symbol)
        if not contract or not tick:
            return

        last_price: float = tick.last_price
        size: float = contract.size

        # 计算新成交额
        for trade in self.new_trades:
            trade_volume: float = trade.volume
            trade_cost: float = trade.price * trade_volume * size

            if trade.direction == Direction.LONG:
                self.long_cost += trade_cost
                self.long_volume += trade_volume
            else:
                self.short_cost += trade_cost
                self.short_volume += trade_volume

        self.new_trades.clear()

        # 计算成交利润
        long_value: float = self.long_volume * last_price * size
        long_pnl: float = long_value - self.long_cost

        shrot_value: float = self.short_volume * last_price * size
        short_pnl: float = self.short_cost - shrot_value

        self.trading_pnl = long_pnl + short_pnl

        # 计算未实现利润和总利润
        self.holding_pnl = (last_price - tick.pre_close) * self.open_pos * size
        self.total_pnl = self.holding_pnl + self.trading_pnl

    def get_data(self) -> dict:
        """获取数据字典"""
        data: dict = {
            "reference": self.reference,
            "vt_symbol": self.vt_symbol,
            "open_pos": self.open_pos,
            "last_pos": self.last_pos,
            "trading_pnl": self.trading_pnl,
            "holding_pnl": self.holding_pnl,
            "total_pnl": self.total_pnl,
            "long_volume": self.long_volume,
            "short_volume": self.short_volume,
            "long_cost": self.long_cost,
            "short_cost": self.short_cost
        }
        return data


class PortfolioResult:
    """按组合名称汇总盈亏。"""

    def __init__(self, reference: str) -> None:
        """记录组合名称并把三项盈亏清零。"""
        super().__init__()

        self.reference: str = reference
        self.trading_pnl: float = 0
        self.holding_pnl: float = 0
        self.total_pnl: float = 0

    def clear_pnl(self) -> None:
        """把交易盈亏、持仓盈亏和总盈亏清零。"""
        self.trading_pnl = 0
        self.holding_pnl = 0
        self.total_pnl = 0

    def get_data(self) -> dict:
        """获取数据字典"""
        data: dict = {
            "reference": self.reference,
            "trading_pnl": self.trading_pnl,
            "holding_pnl": self.holding_pnl,
            "total_pnl": self.total_pnl,
        }
        return data
