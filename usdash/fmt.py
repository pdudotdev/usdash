"""How numbers are written on screen."""


def money(value: float) -> str:
    """Dollars to the cent; an amount under half a cent shows as <$0.01, not $0.00."""
    value = abs(value)
    return "<$0.01" if 0 < value < 0.005 else f"${value:,.2f}"


def tokens_text(value: float | None) -> str:
    """504,113 -> '504k', 9,500 -> '9.5k', 3,200,000 -> '3.2M'. The unit is
    picked after rounding: 999,600 is '1.0M', not '1000k'; 9,960 is '10k'."""
    if value is None:
        return "?"
    thousands = value / 1000
    if round(thousands) >= 1000:
        return f"{value / 1_000_000:.1f}M"
    return f"{thousands:.0f}k" if round(thousands, 1) >= 10 else f"{thousands:.1f}k"


def clock(seconds: int) -> str:
    return f"{seconds // 3600}h{seconds % 3600 // 60:02d}m" if seconds >= 3600 else f"{seconds // 60}:{seconds % 60:02d}"


def plural(n: int, word: str, words: str | None = None) -> str:
    """'1 message', '6 messages'; `words` for an irregular plural ('web searches')."""
    return f"{n} {word}" if n == 1 else f"{n} {words or word + 's'}"
