"""Wilson score interval checks against independently computed reference values."""

from __future__ import annotations

import pytest

from pii_guardbench.evaluator import wilson_interval

# Wilson 95% intervals with z = 1.96, using the standard textbook formula.
# 0/n lower bound is 0; n/n upper bound is 1; 0/n upper is z^2 / (n + z^2).
REFERENCE = {
    (0, 1): (0.0, 0.0, 0.793456708526),
    (1, 1): (1.0, 0.206543291474, 1.0),
    (0, 3): (0.0, 0.0, 0.561506080449),
    (3, 3): (1.0, 0.438493919551, 1.0),
    (0, 10): (0.0, 0.0, 0.277540168767),
    (10, 10): (1.0, 0.722459831233, 1.0),
    (1, 10): (0.1, 0.017875749516, 0.404156385498),
    (5, 10): (0.5, 0.236589593615, 0.763410406385),
    (1, 2): (0.5, 0.094528654801, 0.905471345199),
    (50, 100): (0.5, 0.403829828590, 0.596170171410),
}


@pytest.mark.parametrize("k,n,rate,low,high", [
    (k, n, rate, low, high)
    for (k, n), (rate, low, high) in REFERENCE.items()
])
def test_wilson_matches_reference_values(
    k: int, n: int, rate: float, low: float, high: float
) -> None:
    interval = wilson_interval(k, n)
    assert interval.k == k
    assert interval.n == n
    assert interval.rate == pytest.approx(rate, abs=1e-12)
    assert interval.low == pytest.approx(low, abs=1e-12)
    assert interval.high == pytest.approx(high, abs=1e-12)


def test_wilson_zero_counts_are_safe() -> None:
    interval = wilson_interval(0, 0)
    assert interval.rate is None
    assert interval.low is None
    assert interval.high is None
    assert interval.format_rate() == "n/a (0/0)"
    assert interval.format_ci() == "n/a"


def test_wilson_rejects_invalid_counts() -> None:
    with pytest.raises(ValueError):
        wilson_interval(2, 1)
    with pytest.raises(ValueError):
        wilson_interval(-1, 5)
    with pytest.raises(ValueError):
        wilson_interval(0, -1)
