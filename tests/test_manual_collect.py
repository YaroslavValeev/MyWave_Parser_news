import pytest
from services.manual_collect import parse_period_argument


def test_parse_period_argument():
    p = parse_period_argument("1d")
    assert isinstance(p, int)
    p2 = parse_period_argument("3h")
    assert isinstance(p2, int)
