from decimal import Decimal, ROUND_HALF_UP

DECIMAL_PLACES = Decimal("0.00000001")
MONEY_PLACES = Decimal("0.01")


def D(value: Decimal | int | str) -> Decimal:
    """Build a Decimal from a non-float source.

    Floats are rejected so binary noise cannot enter the ledger.
    """
    if isinstance(value, float):
        raise TypeError("Pass money as Decimal or str, not float")
    if isinstance(value, Decimal):
        return value
    return Decimal(value)


def q8(value: Decimal) -> Decimal:
    return value.quantize(DECIMAL_PLACES, rounding=ROUND_HALF_UP)


def q2(value: Decimal) -> Decimal:
    return value.quantize(MONEY_PLACES, rounding=ROUND_HALF_UP)


def pct(value: Decimal) -> str:
    return f"{(value * Decimal('100')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)}%"
