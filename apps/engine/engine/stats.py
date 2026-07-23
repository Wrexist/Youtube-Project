"""Small statistics library.

The feedback loop rewrites the prompts that generate every future video. If it
learns from noise, the system gets *worse* over time and does so invisibly — the
findings will still read like confident sentences.

So comparisons go through a real significance test rather than "group A's average is
higher". Two groups of three videos will differ by chance essentially always.

Implemented by hand rather than pulling in scipy: this is ~80 lines, scipy is ~90MB,
and the engine already carries MoviePy and Whisper.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class Summary:
    n: int
    mean: float
    variance: float

    @property
    def stdev(self) -> float:
        return math.sqrt(self.variance)

    @property
    def stderr(self) -> float:
        return math.sqrt(self.variance / self.n) if self.n else float("inf")


def summarize(values: list[float]) -> Summary:
    n = len(values)
    if n == 0:
        return Summary(0, 0.0, 0.0)
    mean = sum(values) / n
    # Sample variance (n-1). With n=1 there is no spread to speak of.
    variance = sum((v - mean) ** 2 for v in values) / (n - 1) if n > 1 else 0.0
    return Summary(n, mean, variance)


@dataclass
class Comparison:
    a: Summary
    b: Summary
    t: float
    df: float
    p_value: float

    @property
    def lift(self) -> float:
        """Percentage difference of A over B."""
        return ((self.a.mean - self.b.mean) / self.b.mean * 100) if self.b.mean else 0.0

    @property
    def ci95(self) -> tuple[float, float]:
        """95% confidence interval on the difference in means.

        Reported alongside every finding — an interval spanning zero says
        "we don't know yet" far more clearly than a p-value does.
        """
        se = math.sqrt(self.a.stderr**2 + self.b.stderr**2)
        diff = self.a.mean - self.b.mean
        margin = 1.96 * se  # normal approximation; df is >30 in any usable finding
        return (diff - margin, diff + margin)


def welch_t_test(a: list[float], b: list[float]) -> Comparison:
    """Welch's t-test — unequal variances, unequal sample sizes.

    Student's t assumes equal variance, which is wrong here: a title strategy used
    twice and one used forty times will not have comparable spread.
    """
    sa, sb = summarize(a), summarize(b)
    if sa.n < 2 or sb.n < 2:
        return Comparison(sa, sb, t=0.0, df=0.0, p_value=1.0)

    se_sq = sa.variance / sa.n + sb.variance / sb.n
    if se_sq == 0:
        return Comparison(sa, sb, t=0.0, df=0.0, p_value=1.0)

    t = (sa.mean - sb.mean) / math.sqrt(se_sq)

    # Welch–Satterthwaite degrees of freedom.
    df = se_sq**2 / (
        (sa.variance / sa.n) ** 2 / (sa.n - 1) + (sb.variance / sb.n) ** 2 / (sb.n - 1)
    )

    return Comparison(sa, sb, t=t, df=df, p_value=two_tailed_p(abs(t), df))


def two_tailed_p(t: float, df: float) -> float:
    """P-value for a t statistic, via the regularized incomplete beta function."""
    if df <= 0:
        return 1.0
    x = df / (df + t * t)
    return _betainc(df / 2, 0.5, x)


def _betainc(a: float, b: float, x: float) -> float:
    """Regularized incomplete beta I_x(a, b), by continued fraction (Lentz's method).

    Standard Numerical Recipes formulation. The symmetry transform keeps the
    continued fraction in its fast-converging region.
    """
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0

    lbeta = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
    front = math.exp(lbeta + a * math.log(x) + b * math.log1p(-x))

    if x < (a + 1) / (a + b + 2):
        return front * _cf(a, b, x) / a
    return 1.0 - front * _cf(b, a, 1 - x) / b


def _cf(a: float, b: float, x: float, iterations: int = 200) -> float:
    tiny = 1e-30
    f, c, d = 1.0, 1.0, 0.0

    for i in range(iterations + 1):
        if i == 0:
            numerator = 1.0
        elif i % 2 == 0:
            m = i // 2
            numerator = m * (b - m) * x / ((a + 2 * m - 1) * (a + 2 * m))
        else:
            m = (i - 1) // 2
            numerator = -((a + m) * (a + b + m) * x) / ((a + 2 * m) * (a + 2 * m + 1))

        d = 1.0 + numerator * d
        d = tiny if abs(d) < tiny else d
        d = 1.0 / d

        c = 1.0 + numerator / c
        c = tiny if abs(c) < tiny else c

        delta = c * d
        f *= delta

        if abs(1.0 - delta) < 1e-10:
            break

    return f - 1.0
