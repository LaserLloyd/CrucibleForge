import pytest

from tabsplit.money import parse_amount


@pytest.mark.parametrize("text,cents", [
    ("19.99", 1999), ("0.29", 29), ("4.35", 435), ("12.5", 1250), ("12", 1200),
    ("-0.05", -5), ("-3.10", -310), ("1,234.56", 123456), ("12,345,678.9", 1234567890),
    ("  7.07 ", 707), ("0", 0), ("1000", 100000), ("999999999.99", 99999999999),
])
def test_valid(text, cents):
    got = parse_amount(text)
    assert got == cents and type(got) is int


@pytest.mark.parametrize("text", [
    "1.234", "1,23", "12.", ".5", "", "   ", "abc", "--1", "1,2345", "12,34.00",
    "1.2.3", "+5", "1e3", "nan", "inf", "5-", "1 000",
])
def test_rejected(text):
    with pytest.raises(ValueError):
        parse_amount(text)
