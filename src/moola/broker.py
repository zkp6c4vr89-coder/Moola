"""Alpaca PAPER trading client. PROTECTED: reviews must not edit this file.

This module is hard-wired to paper trading and refuses to run against a live account.
"""
from datetime import date, datetime

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, QueryOrderStatus, TimeInForce
from alpaca.trading.requests import GetCalendarRequest, GetOrdersRequest, MarketOrderRequest

from moola.config import alpaca_keys, is_crypto
from moola.types import Order


def _normalize(symbol: str, asset_class: str) -> str:
    """Alpaca reports crypto positions as BTCUSD; the rest of Moola uses BTC/USD."""
    if "crypto" in str(asset_class).lower() and "/" not in symbol and symbol.endswith("USD"):
        return f"{symbol[:-3]}/USD"
    return symbol


class PaperBroker:
    def __init__(self):
        key, secret = alpaca_keys()
        self.client = TradingClient(key, secret, paper=True)
        account = self.client.get_account()
        if not str(account.account_number).startswith("PA"):
            raise RuntimeError(f"Account {account.account_number} is not a paper account. Refusing to trade.")
        self.account = account

    def account_summary(self) -> dict:
        a = self.client.get_account()
        return {"equity": float(a.equity), "cash": float(a.cash), "last_equity": float(a.last_equity),
                "buying_power": float(a.buying_power)}

    def positions(self) -> list[dict]:
        return [{
            "symbol": _normalize(p.symbol, p.asset_class),
            "qty": float(p.qty),
            "market_value": float(p.market_value),
            "avg_entry_price": float(p.avg_entry_price),
            "current_price": float(p.current_price),
            "unrealized_pl": float(p.unrealized_pl),
            "unrealized_plpc": float(p.unrealized_plpc),
        } for p in self.client.get_all_positions()]

    def was_trading_day(self, day: date) -> bool:
        return bool(self.client.get_calendar(GetCalendarRequest(start=day, end=day)))

    def cancel_open_orders(self) -> None:
        self.client.cancel_orders()

    def submit(self, order: Order) -> dict:
        if order.close_all:
            o = self.client.close_position(order.symbol.replace("/", ""))
        else:
            tif = TimeInForce.GTC if is_crypto(order.symbol) else TimeInForce.DAY  # DAY after close = next open
            o = self.client.submit_order(MarketOrderRequest(
                symbol=order.symbol, notional=order.notional,
                side=OrderSide.BUY if order.side == "buy" else OrderSide.SELL, time_in_force=tif))
        return {"order_id": str(o.id), "status": str(o.status.value if hasattr(o.status, "value") else o.status)}

    def filled_orders_since(self, since: datetime) -> list[dict]:
        orders = self.client.get_orders(GetOrdersRequest(status=QueryOrderStatus.CLOSED, after=since, limit=500))
        return [{
            "order_id": str(o.id),
            "symbol": _normalize(o.symbol, o.asset_class),
            "side": o.side.value,
            "filled_qty": float(o.filled_qty or 0),
            "filled_avg_price": float(o.filled_avg_price) if o.filled_avg_price else None,
            "filled_at": o.filled_at.isoformat() if o.filled_at else None,
            "status": o.status.value,
        } for o in orders if o.filled_at]
