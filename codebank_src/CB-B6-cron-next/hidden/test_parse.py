import pytest

from cronplan.cron import parse


@pytest.mark.parametrize("expr", ["* * * * *", "*/15 9-17 * * MON-FRI", "0 0 L * ?", "30 2 15W * *",
                                  "0 8 ? * 5#2", "0 20 * * 5L", "5/10 */2 1,15,L JAN,jul 7", "0 0 1-31/10 * *"])
def test_accepted(expr):
    parse(expr)


@pytest.mark.parametrize("expr", ["* * * *", "* * * * * *", "60 * * * *", "* 24 * * *", "* * 0 * *",
                                  "* * * 13 *", "* * * * 8", "5-1 * * * *", "*/0 * * * *", "L * * * *",
                                  "* * * * 5#6", "* * 3#2 * *", "* * * * 15W", "* * 32W * *", "* * * FOO *"])
def test_rejected(expr):
    with pytest.raises(ValueError):
        parse(expr)
