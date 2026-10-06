from __future__ import annotations

from abc import ABC, abstractmethod

from ..contracts import Account, MarketSnapshot, OrderRequest, OrderResult


class Broker(ABC):
    name: str = "broker"

    @abstractmethod
    def account(self) -> Account: ...

    @abstractmethod
    def positions(self) -> dict[str, float]:
        """asset ("BTC/USD") -> quantity held."""

    @abstractmethod
    def submit(self, req: OrderRequest) -> OrderResult: ...

    @abstractmethod
    def cancel(self, order_id: str) -> None: ...

    @abstractmethod
    def order_status(self, order_id: str) -> OrderResult: ...

    def on_market(self, snapshot: MarketSnapshot) -> None:
        """Hook for simulated brokers to process resting orders against new bars."""
