"""How numbers are written on screen."""
import pytest

from usdash.fmt import clock, money, plural, tokens_text


@pytest.mark.parametrize(("tokens", "text"), [
    (300, "0.3k"), (9_949, "9.9k"), (42_002, "42k"), (999_400, "999k"), (3_200_000, "3.2M"),
    (1_211_300_000, "1.2B"),  # a month of cache reads: not '1211.3M'
    # The unit is picked after rounding: never '10.0k', '1000k' or '1000.0M'.
    (9_960, "10k"), (9_999, "10k"), (999_600, "1.0M"), (999_999, "1.0M"),
    (999_940_000, "999.9M"), (999_960_000, "1.0B"),
    (None, "?"),
])
def test_tokens(tokens, text):
    assert tokens_text(tokens) == text


def test_money_clock_and_plural():
    assert (money(0), money(0.004), money(0.005), money(1234.5)) == ("$0.00", "<$0.01", "$0.01", "$1,234.50")
    assert (clock(59), clock(300), clock(3600), clock(5400)) == ("0:59", "5:00", "1h00m", "1h30m")
    assert (plural(1, "run"), plural(3, "run"), plural(2, "search", "searches")) == ("1 run", "3 runs", "2 searches")
    assert plural(3_864, "request") == "3,864 requests"  # as the panels write their counts: 1,212
