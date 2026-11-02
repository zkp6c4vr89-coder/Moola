"""Order execution on Alpaca. Paper by default; live only when LIVE=true in .env.

Exits are managed because Alpaca bracket orders can't be fractional:
  - after an entry fills, a real stop order rests on Alpaca (protection if the bot dies);
  - the bot watches the take-profit (and the stop, as a backup) every loop and exits at market.
Every order carries a client_order_id starting with "db-<kind>-" so fills can be attributed later.
"""
import logging
import time as _time
from datetime import datetime

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, QueryOrderStatus, TimeInForce
from alpaca.trading.requests import GetOrdersRequest, MarketOrderRequest, StopOrderRequest

from daybot import config

log = logging.getLogger("daybot")


class Broker:
    def __init__(self):
        key, secret, live = config.credentials()
        self.live = live
        self.client = TradingClient(key, secret, paper=not live)
        acct = self.client.get_account()
        is_paper_account = str(acct.account_number).startswith("PA")
        if live and is_paper_account:
            raise RuntimeError("LIVE=true but these are paper keys. Refusing to start.")
        if not live and not is_paper_account:
            raise RuntimeError("LIVE is not true but this is a live account. Refusing to start.")
        if live:
            log.warning("!!! LIVE TRADING WITH REAL MONEY (LIVE=true) !!!")

    # --- reads -----------------------------------------------------------------
    def account(self) -> dict:
        a = self.client.get_account()
        return {"equity": float(a.equity), "last_equity": float(a.last_equity), "cash": float(a.cash),
                "buying_power": float(a.buying_power),
                "non_marginable_buying_power": float(a.non_marginable_buying_power)}

    def clock_open(self) -> bool:
        return bool(self.client.get_clock().is_open)

    def positions(self) -> dict[str, dict]:
        return {p.symbol: {"qty": float(p.qty), "avg_entry": float(p.avg_entry_price),
                           "price": float(p.current_price), "unrealized_pl": float(p.unrealized_pl)}
                for p in self.client.get_all_positions()}

    def open_orders(self) -> list:
        return self.client.get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN, limit=500))

    def orders_since(self, since: datetime) -> list:
        return self.client.get_orders(GetOrdersRequest(status=QueryOrderStatus.ALL, after=since, limit=500))

    def entries_today(self, session_start: datetime) -> int:
        return sum(1 for o in self.orders_since(session_start)
                   if (o.client_order_id or "").startswith("db-entry-")
                   and (float(o.filled_qty or 0) > 0 or o.status.value in ("new", "accepted", "pending_new")))

    # --- checks ------------------------------------------------------------------
    def preflight(self, symbol: str, notional: float) -> tuple[bool, str]:
        """Before every entry: market open, enough buying power, no duplicate order or position."""
        if not self.clock_open():
            return False, "market not open"
        acct = self.account()
        bp = min(acct["buying_power"], acct["non_marginable_buying_power"] or acct["buying_power"])
        if bp < notional:
            return False, f"buying power ${bp:.2f} < ${notional:.2f}"
        if any(o.symbol == symbol for o in self.open_orders()):
            return False, "open order already exists"
        if symbol in self.positions():
            return False, "position already open"
        return True, "ok"

    # --- writes --------------------------------------------------------------------
    def _wait_fill(self, order_id) -> object:
        deadline = _time.time() + config.FILL_TIMEOUT_SECONDS
        while True:
            o = self.client.get_order_by_id(order_id)
            if o.status.value in ("filled", "canceled", "expired", "rejected") or _time.time() > deadline:
                return o
            _time.sleep(1)

    def buy(self, symbol: str, notional: float, cid: str):
        o = self.client.submit_order(MarketOrderRequest(symbol=symbol, notional=notional, side=OrderSide.BUY,
                                                        time_in_force=TimeInForce.DAY, client_order_id=cid))
        o = self._wait_fill(o.id)
        if o.status.value != "filled":
            self.client.cancel_order_by_id(o.id)
        return o

    def sell(self, symbol: str, qty: float, cid: str):
        o = self.client.submit_order(MarketOrderRequest(symbol=symbol, qty=qty, side=OrderSide.SELL,
                                                        time_in_force=TimeInForce.DAY, client_order_id=cid))
        return self._wait_fill(o.id)

    def place_stop(self, symbol: str, qty: float, stop_price: float, cid: str):
        return self.client.submit_order(StopOrderRequest(symbol=symbol, qty=qty, side=OrderSide.SELL,
                                                         time_in_force=TimeInForce.DAY, stop_price=round(stop_price, 2),
                                                         client_order_id=cid))

    def cancel(self, order_id) -> None:
        try:
            self.client.cancel_order_by_id(order_id)
        except Exception as e:  # already filled or cancelled
            log.info("cancel %s: %s", order_id, e)

    def flatten(self, reason: str, stamp: str) -> list:
        """Cancel every open order and sell every position at market."""
        self.client.cancel_orders()
        _time.sleep(1)
        results = []
        for sym, p in self.positions().items():
            results.append(self.sell(sym, p["qty"], f"db-{reason}-{sym}-{stamp}"))
        return results
