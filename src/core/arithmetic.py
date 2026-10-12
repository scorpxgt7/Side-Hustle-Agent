"""Exact finite-decimal arithmetic and versioned, context-independent ratios."""
from decimal import Context, Decimal, DivisionByZero, InvalidOperation, Overflow, ROUND_HALF_EVEN, localcontext
from fractions import Fraction

ARITHMETIC_POLICY_VERSION = "exact-decimal-v1"
MAX_INPUT_DIGITS = 64
MAX_INPUT_EXPONENT = 128
RATIO_PRECISION = 50


def validate_decimal(value, name):
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError(f"{name} must be a finite Decimal")
    parts = value.as_tuple()
    if (len(parts.digits) > MAX_INPUT_DIGITS
            or abs(parts.exponent) > MAX_INPUT_EXPONENT
            or abs(value.adjusted()) > MAX_INPUT_EXPONENT):
        raise ValueError(f"{name} exceeds the supported decimal size/scale")


def exact_decimal(value: Fraction) -> Decimal:
    """Convert a terminating rational without consulting the Decimal context."""
    denominator = value.denominator
    twos = fives = 0
    while denominator % 2 == 0:
        denominator //= 2
        twos += 1
    while denominator % 5 == 0:
        denominator //= 5
        fives += 1
    if denominator != 1:
        raise ValueError("exact decimal requires a terminating fraction")
    scale = max(twos, fives)
    coefficient = abs(value.numerator) * 2 ** (scale - twos) * 5 ** (scale - fives)
    if coefficient == 0:
        return Decimal("0")
    exponent = -scale
    while coefficient % 10 == 0:
        coefficient //= 10
        exponent += 1
    digits = tuple(int(digit) for digit in str(coefficient))
    return Decimal((int(value < 0), digits, exponent))


def exact_add(left: Decimal, right: Decimal) -> Decimal:
    return exact_decimal(Fraction(left) + Fraction(right))


def exact_subtract(left: Decimal, right: Decimal) -> Decimal:
    return exact_decimal(Fraction(left) - Fraction(right))


def rounded_ratio(value: Fraction) -> Decimal:
    """Render ratios under a fully specified policy, never the caller's context."""
    context = Context(prec=RATIO_PRECISION, rounding=ROUND_HALF_EVEN,
                      Emin=-4096, Emax=4096,
                      traps=[InvalidOperation, DivisionByZero, Overflow])
    with localcontext(context):
        return Decimal(value.numerator) / Decimal(value.denominator)
