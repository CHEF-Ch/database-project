from decimal import Decimal, InvalidOperation

from app.errors import AppError, ErrCode


def decimal_input(value, *, precision: int = 10, scale: int = 2, label: str) -> Decimal:
    quantum = Decimal(1).scaleb(-scale)
    limit = Decimal(10) ** (precision - scale)
    try:
        number = Decimal(str(value))
        if number.is_finite() and number.copy_abs() < limit:
            rounded = number.quantize(quantum)
            if rounded.copy_abs() < limit:
                return rounded
    except (InvalidOperation, ValueError):
        pass
    raise AppError(ErrCode.PARAM_INVALID, detail=f"{label}不合法")
