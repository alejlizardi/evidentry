"""Statistics for evidence-grade eval reporting.

A bare pass rate on 25 items is weak evidence. These helpers attach the
uncertainty that a reviewer (or auditor) should expect to see:

- Wilson score intervals for pass rates (better small-sample behavior than
  the normal approximation, never escapes [0, 1]).
- Fisher's exact test for run-over-run drift detection — exact at every
  sample size, including the n=4-8 suites real configs actually have, where
  a two-proportion z-test is anti-conservative.
- Holm-Bonferroni adjustment so testing many suites at once doesn't
  manufacture false drift.
- Exact binomial sample-size certificates: how many more items would settle
  an unsettled verdict.
- Cluster-adjusted intervals for datasets whose items share a source
  (the same document, scenario, or template) and are therefore correlated.

Pure stdlib; every number in a report should be recomputable by hand.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

Z_95 = 1.959963984540054  # two-sided 95%

# Hard cap for the sample-size search. Verdicts that would take more items
# than this to settle are reported as unreachable rather than estimated.
_CERTIFICATE_N_MAX = 2_000_000


def _wilson_from_rate(p_hat: float, n: float, z: float = Z_95) -> tuple[float, float]:
    """Wilson interval from a rate and an (possibly non-integer) sample size."""
    if n <= 0:
        return (0.0, 1.0)
    denom = 1 + z * z / n
    centre = p_hat + z * z / (2 * n)
    margin = z * math.sqrt(p_hat * (1 - p_hat) / n + z * z / (4 * n * n))
    low = (centre - margin) / denom
    high = (centre + margin) / denom
    return (max(0.0, low), min(1.0, high))


def wilson_interval(successes: int, n: int, z: float = Z_95) -> tuple[float, float]:
    """Wilson score confidence interval for a binomial proportion."""
    if n == 0:
        return (0.0, 1.0)
    if successes < 0 or successes > n:
        raise ValueError("successes must be between 0 and n")
    return _wilson_from_rate(successes / n, n, z)


def _log_comb(n: int, k: int) -> float:
    if k < 0 or k > n:
        return -math.inf
    return math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1)


def fisher_exact_pvalue(successes_a: int, n_a: int, successes_b: int, n_b: int) -> float:
    """Two-sided Fisher's exact test for a 2x2 table of pass/fail by run.

    Conditions on both margins and sums the probability of every table at
    least as extreme (no more probable) than the observed one. Exact at any
    sample size — no large-n approximation to go wrong at n=5.
    """
    if n_a == 0 or n_b == 0:
        raise ValueError("both runs must contain at least one item")
    if not (0 <= successes_a <= n_a and 0 <= successes_b <= n_b):
        raise ValueError("successes must be between 0 and n for each run")
    total_pass = successes_a + successes_b
    total = n_a + n_b
    log_denom = _log_comb(total, total_pass)

    def logpmf(k: int) -> float:
        return _log_comb(n_a, k) + _log_comb(n_b, total_pass - k) - log_denom

    observed = logpmf(successes_a)
    cutoff = observed + 1e-7  # tolerance for float ties
    k_min = max(0, total_pass - n_b)
    k_max = min(total_pass, n_a)
    p = 0.0
    for k in range(k_min, k_max + 1):
        lp = logpmf(k)
        if lp <= cutoff:
            p += math.exp(lp)
    return min(1.0, p)


@dataclass
class DriftResult:
    rate_a: float
    rate_b: float
    p_value: float
    significant: bool
    method: str = "fisher_exact"


def drift_test(
    successes_a: int, n_a: int, successes_b: int, n_b: int, alpha: float = 0.05
) -> DriftResult:
    """Did the pass rate change between two runs? Fisher's exact, two-sided.

    Frames ongoing monitoring as a falsifiable statistical question rather
    than a side-by-side eyeball comparison. `significant` is the single-test
    decision; when several suites are compared at once, apply
    `holm_adjust` to the family of p-values instead.
    """
    p_a = successes_a / n_a if n_a else 0.0
    p_b = successes_b / n_b if n_b else 0.0
    p_value = fisher_exact_pvalue(successes_a, n_a, successes_b, n_b)
    return DriftResult(p_a, p_b, p_value, p_value < alpha)


def holm_adjust(p_values: list[float]) -> list[float]:
    """Holm-Bonferroni step-down adjusted p-values (controls FWER).

    Returns adjusted p-values in the input order; reject H_i at level alpha
    iff adjusted[i] < alpha. With 10 suites at alpha=.05, unadjusted testing
    has a ~40% family-wise false-drift rate; Holm caps it at 5% with no
    independence assumption.
    """
    m = len(p_values)
    order = sorted(range(m), key=lambda i: p_values[i])
    adjusted = [0.0] * m
    running = 0.0
    for rank, idx in enumerate(order):
        running = max(running, (m - rank) * p_values[idx])
        adjusted[idx] = min(1.0, running)
    return adjusted


def _binom_logpmf(k: int, n: int, p: float) -> float:
    if p <= 0.0:
        return 0.0 if k == 0 else -math.inf
    if p >= 1.0:
        return 0.0 if k == n else -math.inf
    return _log_comb(n, k) + k * math.log(p) + (n - k) * math.log1p(-p)


def _binom_cdf(k: int, n: int, p: float) -> float:
    """P[X <= k] for X ~ Binomial(n, p), summed away from the mode."""
    if k < 0:
        return 0.0
    if k >= n:
        return 1.0
    mode = int((n + 1) * p)
    if k >= mode:
        return 1.0 - _binom_sf(k + 1, n, p)
    # k below the mode: terms shrink as j decreases, so the sum terminates early.
    term = math.exp(_binom_logpmf(k, n, p))
    total = term
    for j in range(k, 0, -1):
        # pmf(j-1) = pmf(j) * j*(1-p) / ((n-j+1)*p)
        term *= j * (1.0 - p) / ((n - j + 1) * p)
        total += term
        if term < total * 1e-17:
            break
    return min(1.0, total)


def _binom_sf(k: int, n: int, p: float) -> float:
    """P[X >= k] for X ~ Binomial(n, p), summed away from the mode."""
    if k <= 0:
        return 1.0
    if k > n:
        return 0.0
    mode = int((n + 1) * p)
    if k <= mode:
        return 1.0 - _binom_cdf(k - 1, n, p)
    term = math.exp(_binom_logpmf(k, n, p))
    total = term
    for j in range(k, n):
        # pmf(j+1) = pmf(j) * (n-j)*p / ((j+1)*(1-p))
        term *= (n - j) * p / ((j + 1) * (1.0 - p))
        total += term
        if term < total * 1e-17:
            break
    return min(1.0, total)


def _min_passes_to_settle_pass(n: int, threshold: float) -> int | None:
    """Smallest k with wilson_low(k, n) >= threshold, or None."""
    if wilson_interval(n, n)[0] < threshold:
        return None
    lo, hi = 0, n
    while lo < hi:
        mid = (lo + hi) // 2
        if wilson_interval(mid, n)[0] >= threshold:
            hi = mid
        else:
            lo = mid + 1
    return lo


def _max_passes_to_settle_fail(n: int, threshold: float) -> int | None:
    """Largest k with wilson_high(k, n) < threshold, or None."""
    if wilson_interval(0, n)[1] >= threshold:
        return None
    lo, hi = 0, n
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if wilson_interval(mid, n)[1] < threshold:
            lo = mid
        else:
            hi = mid - 1
    return lo


def _settle_power(n: int, planning_rate: float, threshold: float, direction: str) -> float:
    """P[the verdict settles at sample size n] if items are iid Bernoulli(planning_rate)."""
    if direction == "PASS":
        k = _min_passes_to_settle_pass(n, threshold)
        return 0.0 if k is None else _binom_sf(k, n, planning_rate)
    k = _max_passes_to_settle_fail(n, threshold)
    return 0.0 if k is None else _binom_cdf(k, n, planning_rate)


def sample_size_certificate(
    successes: int, n: int, threshold: float, power: float = 0.80
) -> dict:
    """How many items would it take to settle this verdict?

    A PASS settles when the Wilson lower bound clears the threshold; a FAIL
    settles when the upper bound drops below it. This computes, by exact
    binomial power at the observed rate, the smallest total sample size at
    which the verdict settles with the requested probability — the honest
    answer to "is my eval evidence sufficient, and if not, how far off am I?"

    Assumes future items behave like the observed ones (iid at the observed
    rate). The binomial sawtooth means the boundary can wobble by a few
    items; the certificate is a planning number, not a guarantee.
    """
    if n <= 0:
        raise ValueError("n must be positive")
    rate = successes / n
    direction = "PASS" if rate >= threshold else "FAIL"
    result = {
        "target_verdict": direction,
        "planning_rate": rate,
        "requested_power": power,
        "n_current": n,
    }
    low, high = wilson_interval(successes, n)
    if (direction == "PASS" and low >= threshold) or (
        direction == "FAIL" and high < threshold
    ):
        result.update(status="already_settled", n_required=n, additional_items=0)
        return result
    if rate == threshold:
        # At the boundary the settle probability tends to ~2.5%, not 80%.
        result.update(
            status="unreachable",
            note="the observed rate sits exactly on the threshold; more items cannot settle the verdict",
        )
        return result

    # Exponential search for a sample size with enough power, then binary
    # refinement. Power is monotone up to a small binomial sawtooth.
    n_hi = max(n, 8)
    while n_hi <= _CERTIFICATE_N_MAX and _settle_power(n_hi, rate, threshold, direction) < power:
        n_hi *= 2
    if n_hi > _CERTIFICATE_N_MAX:
        result.update(
            status="unreachable",
            note=(
                "the observed rate is too close to the threshold to settle "
                f"this verdict within {_CERTIFICATE_N_MAX:,} items"
            ),
        )
        return result
    lo, hi = n, n_hi
    while lo < hi:
        mid = (lo + hi) // 2
        if _settle_power(mid, rate, threshold, direction) >= power:
            hi = mid
        else:
            lo = mid + 1
    n_required = lo
    result.update(
        status="ok",
        n_required=n_required,
        additional_items=max(0, n_required - n),
        achieved_power=_settle_power(n_required, rate, threshold, direction),
    )
    return result


def _betacf(a: float, b: float, x: float) -> float:
    """Continued fraction for the regularized incomplete beta (Lentz)."""
    tiny = 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < tiny:
        d = tiny
    d = 1.0 / d
    h = d
    for m in range(1, 300):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-14:
            break
    return h


def _betainc_reg(a: float, b: float, x: float) -> float:
    """Regularized incomplete beta function I_x(a, b)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    ln_front = (
        math.lgamma(a + b)
        - math.lgamma(a)
        - math.lgamma(b)
        + a * math.log(x)
        + b * math.log1p(-x)
    )
    front = math.exp(ln_front)
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


def t_quantile(q: float, df: int) -> float:
    """Quantile of Student's t (q in (0.5, 1)), by bisection on the CDF.

    Used for cluster-adjusted intervals: the cluster-robust variance is
    estimated from G clusters, so the critical value carries G-1 degrees
    of freedom instead of pretending the variance is known.
    """
    if not 0.5 < q < 1.0:
        raise ValueError("q must be in (0.5, 1)")
    if df < 1:
        raise ValueError("df must be >= 1")

    def cdf(x: float) -> float:
        if x == 0.0:
            return 0.5
        ib = _betainc_reg(df / 2.0, 0.5, df / (df + x * x))
        return 1.0 - 0.5 * ib if x > 0 else 0.5 * ib

    lo, hi = 0.0, 1e6
    for _ in range(200):
        mid = (lo + hi) / 2
        if cdf(mid) < q:
            lo = mid
        else:
            hi = mid
        if hi - lo < 1e-12 * max(1.0, lo):
            break
    return (lo + hi) / 2


def cluster_diagnostics(item_passed: list[bool], clusters: list[str]) -> dict:
    """Design effect and effective sample size for cluster-correlated items.

    Eval items often share a source — several questions about the same
    document, scenarios from the same template. Treating them as independent
    understates uncertainty. This computes the one-way cluster-robust
    variance of the pass rate (linearization estimator),

        var_cl = G/(G-1) * sum_g (s_g - n_g * p)^2 / n^2,

    the design effect DEFF = var_cl / (p(1-p)/n), and the effective sample
    size n_eff = n / max(DEFF, 1). DEFF below 1 (negative intra-cluster
    correlation) is reported but not exploited: n_eff never exceeds n, so
    the adjustment can only widen the interval.
    """
    n = len(item_passed)
    if n == 0 or len(clusters) != n:
        raise ValueError("item_passed and clusters must be equal-length and non-empty")
    p = sum(item_passed) / n
    groups: dict[str, list[bool]] = {}
    for passed, cluster in zip(item_passed, clusters):
        groups.setdefault(cluster, []).append(passed)
    n_clusters = len(groups)
    var_iid = p * (1 - p) / n
    if n_clusters < 2 or n_clusters == n or var_iid == 0.0:
        deff = 1.0
    else:
        g = n_clusters
        var_cl = (
            g / (g - 1)
            * sum((sum(items) - len(items) * p) ** 2 for items in groups.values())
            / (n * n)
        )
        deff = var_cl / var_iid
    n_eff = n / max(deff, 1.0)
    return {"n_clusters": n_clusters, "deff": deff, "n_eff": n_eff}


def threshold_verdict(
    successes: int,
    n: int,
    threshold: float,
    item_passed: list[bool] | None = None,
    clusters: list[str] | None = None,
) -> dict:
    """Point estimate plus interval-aware verdict against a threshold.

    'PASS' means even the lower confidence bound clears the threshold;
    'PASS (point)' means the point estimate clears it but the interval
    does not — i.e. the sample is too small to call it settled evidence.

    When `clusters` is given, the interval (and therefore the verdict) uses
    the cluster-adjusted effective sample size, so correlated items can't
    masquerade as independent evidence.
    """
    rate = successes / n if n else 0.0
    cluster_info: dict | None = None
    if clusters is not None and item_passed is not None and n > 0:
        cluster_info = cluster_diagnostics(item_passed, clusters)
        # The cluster-robust variance is estimated from G clusters, so the
        # critical value carries G-1 degrees of freedom: with few clusters
        # a normal critical value materially under-covers (verified in
        # research/eval_evidence_foundations/).
        g = cluster_info["n_clusters"]
        crit = t_quantile(0.975, g - 1) if g >= 2 else Z_95
        low, high = _wilson_from_rate(rate, cluster_info["n_eff"], z=crit)
        cluster_info["critical_value"] = crit
    else:
        low, high = wilson_interval(successes, n)
    if low >= threshold:
        verdict = "PASS"
    elif rate >= threshold:
        verdict = "PASS (point)"
    elif high < threshold:
        verdict = "FAIL"
    else:
        verdict = "FAIL (point)"
    out = {
        "pass_rate": rate,
        "ci95_low": low,
        "ci95_high": high,
        "threshold": threshold,
        "verdict": verdict,
    }
    if cluster_info is not None:
        out["ci_method"] = "wilson_cluster_adjusted"
        out.update(cluster_info)
    else:
        out["ci_method"] = "wilson"
    return out
