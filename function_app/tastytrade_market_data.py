"""
Tastytrade options market-data adapter for Crassus 2.5.

The instrument chain supplies tradeable Tastytrade option symbols. The
market-data endpoint supplies current quote fields for those symbols.
"""

from dataclasses import dataclass, replace
from datetime import date
from typing import Any, Iterable, List, Optional


class TastytradeMarketDataError(Exception):
    """Raised when Tastytrade option market data is unavailable or malformed."""


@dataclass(frozen=True)
class TastytradeOptionContract:
    """A single equity option contract enriched with Tastytrade quote data."""

    contract_symbol: str
    streamer_symbol: str
    option_type: str
    strike: float
    expiration: date
    last_price: float = 0.0
    bid: float = 0.0
    ask: float = 0.0
    volume: int = 0
    open_interest: int = 0
    implied_volatility: float = 0.0


@dataclass(frozen=True)
class TastytradeOptionChain:
    """Complete Tastytrade option-chain snapshot for one underlying."""

    underlying: str
    contracts: List[TastytradeOptionContract]


def parse_nested_option_chain(payload: dict, underlying: str) -> TastytradeOptionChain:
    """Parse Tastytrade's nested option-chain payload into contract models."""
    data = payload.get("data", payload) if isinstance(payload, dict) else {}
    items = _items(data)
    contracts: List[TastytradeOptionContract] = []

    for item in items:
        expirations = item.get("expirations") or []
        for expiration in expirations:
            exp_date = _parse_date(expiration.get("expiration-date"))
            strikes = expiration.get("strikes") or []
            for strike in strikes:
                strike_price = _safe_float(strike.get("strike-price"))
                for option_type, symbol_key, streamer_key in (
                    ("call", "call", "call-streamer-symbol"),
                    ("put", "put", "put-streamer-symbol"),
                ):
                    symbol = str(strike.get(symbol_key) or "").strip()
                    if not symbol:
                        continue
                    contracts.append(
                        TastytradeOptionContract(
                            contract_symbol=symbol,
                            streamer_symbol=str(strike.get(streamer_key) or "").strip(),
                            option_type=option_type,
                            strike=strike_price,
                            expiration=exp_date,
                        )
                    )

    if not contracts:
        raise TastytradeMarketDataError(f"No Tastytrade option contracts returned for {underlying}")

    return TastytradeOptionChain(underlying=underlying.upper(), contracts=contracts)


def get_option_chain(
    client,
    underlying: str,
    correlation_id: str = "",
    batch_size: int = 100,
) -> TastytradeOptionChain:
    """Fetch Tastytrade option chain metadata and merge current market data."""
    del correlation_id
    chain = parse_nested_option_chain(
        client.get_nested_option_chain(underlying.upper()),
        underlying,
    )
    if not chain.contracts:
        return chain

    market_data_by_symbol: dict[str, dict] = {}
    symbols = [contract.contract_symbol for contract in chain.contracts]
    for chunk in _chunks(symbols, max(1, int(batch_size))):
        raw_market_data = client.get_market_data_by_type(equity_options=chunk)
        for item in _items(raw_market_data):
            symbol = str(item.get("symbol") or "").strip()
            if symbol:
                market_data_by_symbol[_symbol_key(symbol)] = item

    return TastytradeOptionChain(
        underlying=chain.underlying,
        contracts=[
            _merge_market_data(contract, market_data_by_symbol.get(_symbol_key(contract.contract_symbol), {}))
            for contract in chain.contracts
        ],
    )


def _merge_market_data(contract: TastytradeOptionContract, data: dict) -> TastytradeOptionContract:
    bid = _safe_float(_first_value(data, ("bid", "bid-price", "bidPrice")))
    ask = _safe_float(_first_value(data, ("ask", "ask-price", "askPrice")))
    last = _safe_float(_first_value(data, ("last", "last-price", "lastPrice", "mark", "mid")))
    return replace(
        contract,
        bid=bid,
        ask=ask,
        last_price=last,
        volume=_safe_int(_first_value(data, ("volume", "day-volume", "dayVolume"))),
        open_interest=_safe_int(_first_value(data, ("open-interest", "openInterest", "open_interest"))),
        implied_volatility=_safe_float(
            _first_value(data, ("implied-volatility", "impliedVolatility", "volatility"))
        ),
    )


def _items(data: Any) -> list[dict]:
    if isinstance(data, dict):
        if isinstance(data.get("items"), list):
            return data["items"]
        if isinstance(data.get("data"), (dict, list)):
            return _items(data["data"])
        if data.get("symbol"):
            return [data]
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    return []


def _chunks(values: list[str], size: int) -> Iterable[list[str]]:
    for start in range(0, len(values), size):
        yield values[start:start + size]


def _first_value(data: dict, keys: tuple[str, ...]) -> Optional[Any]:
    for key in keys:
        value = data.get(key)
        if value not in (None, ""):
            return value
    return None


def _parse_date(value: Any) -> date:
    if isinstance(value, date):
        return value
    if not value:
        raise TastytradeMarketDataError("Tastytrade option expiration date is missing")
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError as exc:
        raise TastytradeMarketDataError(f"Invalid Tastytrade expiration date: {value}") from exc


def _safe_float(value: Any) -> float:
    try:
        return float(value) if value is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _safe_int(value: Any) -> int:
    try:
        return int(float(value)) if value is not None else 0
    except (TypeError, ValueError):
        return 0


def _symbol_key(symbol: str) -> str:
    return "".join(str(symbol).upper().split())
