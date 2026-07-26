"""Statistical helpers for performance attribution.

Provides a thin wrapper around Welch's t-test (equal-variance not assumed)
and a simple summary object.  The rest of the codebase only imports
``Comparison``, ``summarize``, ``two_tailed_p``, and ``welch_t_test`` from here.

The statistical gate in ``insights.py`` (MIN_PER_GROUP, ALPHA,
MIN_MEANINGFUL_LIFT) is what keeps the feedback loop from training on noise.
Nothing in this module weakens that gate.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class Summary:
    """Descriptive statistics for one group."""

    mean: float
    std: float
    n: int

    @property
    def variance(self) -> float:
        """Sample variance (std²)."""
        return self.std ** 2


@dataclass(frozen=True)
class Comparison:
    """Result of a two-sample comparison.

    ``a`` is the winner (higher mean), ``b`` is the loser.
    ``lift`` is the percentage improvement of ``a`` over ``b``.
    ``p_value`` is from Welch's t-test (two-tailed).
    ``df`` is the Welch–Satterthwaite degrees of freedom.
    """

    a: Summary
    b: Summary
    t: float
    p_value: float
    lift: float   # percentage points: (a.mean - b.mean) / |b.mean| * 100
    df: float     # Welch–Satterthwaite degrees of freedom

    @property
    def ci95(self) -> tuple[float, float]:
        """95% confidence interval for (a.mean - b.mean)."""
        diff = self.a.mean - self.b.mean
        if self.df == 0.0 or self.a.n == 0 or self.b.n == 0:
            return (diff, diff)
        se = math.sqrt(self.a.std ** 2 / self.a.n + self.b.std ** 2 / self.b.n)
        if se == 0.0:
            return (diff, diff)
        try:
            from scipy.stats import t as t_dist  # type: ignore[import]

            t_crit = float(t_dist.ppf(0.975, self.df))
        except ImportError:
            # Bisect on two_tailed_p to find the 97.5th-percentile critical value.
            lo, hi = 0.0, 1e6
            for _ in range(60):
                mid = (lo + hi) / 2.0
                if two_tailed_p(mid, self.df) > 0.05:
                    lo = mid
                else:
                    hi = mid
            t_crit = (lo + hi) / 2.0
        return (diff - t_crit * se, diff + t_crit * se)


def summarize(values: Sequence[float]) -> Summary:
    """Descriptive statistics for a sequence of numeric values."""
    n = len(values)
    if n == 0:
        return Summary(mean=0.0, std=0.0, n=0)
    mean = sum(values) / n
    if n == 1:
        return Summary(mean=mean, std=0.0, n=1)
    variance = sum((x - mean) ** 2 for x in values) / (n - 1)
    return Summary(mean=mean, std=math.sqrt(variance), n=n)


def two_tailed_p(t_stat: float, df: float) -> float:
    """Two-tailed p-value from a t-statistic and degrees of freedom.

    Requires scipy, which is a hard dependency for exactly this reason. There
    used to be a ``math.betainc`` fallback here "for an exact result without any
    third-party dependency" — but no released CPython has ``math.betainc``, so
    that branch raised ``AttributeError`` every time it was reached. A wrong
    p-value silently trains the Phase 8 feedback loop on noise, which is worse
    than a missing package, so this fails loudly instead of approximating.

    Formula: P(|T| > |t| | df) = I(df / (df + t²); df/2, 1/2)
    """
    from scipy.stats import t as t_dist  # type: ignore[import]

    return float(2 * t_dist.sf(abs(t_stat), df))


def welch_t_test(a_values: Sequence[float], b_values: Sequence[float]) -> Comparison:
    """Welch's two-sample t-test: is group A's mean higher than group B's?

    The caller is responsible for passing A as the presumed winner and B as
    the presumed loser.  ``p_value`` is the two-tailed probability.
    """
    sa = summarize(list(a_values))
    sb = summarize(list(b_values))

    # Lift: percentage-point change from b to a
    if sb.mean != 0:
        lift = (sa.mean - sb.mean) / abs(sb.mean) * 100
    else:
        lift = 0.0 if sa.mean == 0 else float("inf")

    # Degenerate case — not enough data for a t-test
    if sa.n < 2 or sb.n < 2:
        return Comparison(a=sa, b=sb, t=0.0, p_value=1.0, lift=lift, df=0.0)

    var_a = sa.std ** 2 / sa.n
    var_b = sb.std ** 2 / sb.n
    pooled = var_a + var_b

    if pooled == 0:
        # Both groups have zero variance — no meaningful test possible.
        # Two-tailed p for t=0 is 1.0 (no evidence of difference).
        # If means somehow differ despite zero variance, p → 0.0.
        p = 1.0 if sa.mean == sb.mean else 0.0
        return Comparison(a=sa, b=sb, t=0.0, p_value=p, lift=lift, df=0.0)

    t_stat = (sa.mean - sb.mean) / math.sqrt(pooled)

    # Welch–Satterthwaite degrees of freedom
    df = pooled ** 2 / (
        (var_a ** 2) / (sa.n - 1) + (var_b ** 2) / (sb.n - 1)
    )

    p_value = two_tailed_p(t_stat, df)

    return Comparison(a=sa, b=sb, t=t_stat, p_value=p_value, lift=lift, df=df)
