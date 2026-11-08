"""Interfaces for sources that are not connected yet.

Callers receive an empty result and a reason. Nothing is filled in.
"""

from typing import Protocol


class NewsProvider(Protocol):
    def get_company_news(self, symbol: str) -> dict: ...

    def get_filings(self, symbol: str) -> dict: ...

    def get_events(self, symbol: str) -> dict: ...


class FundamentalsProvider(Protocol):
    def get_income_statement(self, symbol: str) -> dict: ...

    def get_balance_sheet(self, symbol: str) -> dict: ...

    def get_cash_flow(self, symbol: str) -> dict: ...

    def get_estimates(self, symbol: str) -> dict: ...


class OwnershipProvider(Protocol):
    def get_institutional_ownership(self, symbol: str) -> dict: ...

    def get_insider_ownership(self, symbol: str) -> dict: ...

    def get_major_holders(self, symbol: str) -> dict: ...


def _empty(kind: str) -> dict:
    return {"ready": False, "rows": [], "note": f"No {kind} provider is connected. Nothing was estimated."}


class UnavailableNews:
    def get_company_news(self, symbol: str) -> dict:
        return _empty("news")

    def get_filings(self, symbol: str) -> dict:
        return _empty("filings")

    def get_events(self, symbol: str) -> dict:
        return _empty("company event")


class UnavailableFundamentals:
    def get_income_statement(self, symbol: str) -> dict:
        return _empty("income statement")

    def get_balance_sheet(self, symbol: str) -> dict:
        return _empty("balance sheet")

    def get_cash_flow(self, symbol: str) -> dict:
        return _empty("cash flow")

    def get_estimates(self, symbol: str) -> dict:
        return _empty("estimate")


class UnavailableOwnership:
    def get_institutional_ownership(self, symbol: str) -> dict:
        return _empty("institutional ownership")

    def get_insider_ownership(self, symbol: str) -> dict:
        return _empty("insider ownership")

    def get_major_holders(self, symbol: str) -> dict:
        return _empty("holder")
