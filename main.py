#!/usr/bin/env python3
"""Forecast GTA VI's launch-day Twitch viewers hour by hour.

Method (log-ratio shape model, fitted at runtime from the case itself):
  * Work in log space, anchored on the case's hour-1 print p1.
  * For every comparable launch, build its release-day log-ratio curve
    d_h = ln(viewers_h) - ln(viewers_1) for h = 2..24.
  * Keep only console-only comparables (platforms without "PC"): their
    rolling-midnight unlock shapes match a console-only single-player
    launch; PC / simultaneous-unlock launches have wildly different
    curves and would blow up the spread.
  * Centre per hour: a three-predictor OLS fit of d_h on the target's
    pre-release features over the console comparables -- prepeak
    (ln(max pre-release hourly / mean of the last 24 pre-release
    hours)), r24 (ln(hour-1 print / mean of the last 24 pre-release
    hours)) and vph (ln(hour-1 viewers per live channel)).  A low
    viewers-per-channel at hour 1 means the big streamers have not
    started yet, so the day rises more; a depressed hour-1 relative
    to the pre-release level means the same.  Target features are
    clamped to the pool's observed range, so the fit never
    extrapolates beyond the launches it was fitted on.
  * Spread per hour: the residual standard deviation of that OLS fit
    (floored at 0.15), as a normal log-spread; quantiles
    q_tau = p1 * exp(centre + z_tau*sigma).
  * The target's prepeak uses its full 14-day pre-release window: for
    the GTA VI final the recorder only covers 2 days, so the peak
    also counts the GTA VI category's own pre-release series shipped
    in the case (same game, same measure, 5-17 November).
  * Fallbacks keep every case valid: too few console curves -> use all
    comparables; too few complete cases for the fit -> the console
    median curve with its IQR spread; no comparables at all -> the
    house persistence shape.

Only the Python standard library. Deterministic, no network, no clock.
Reads every *.in file in $MAKEFUN_INPUT_DIR (else /input), writes one
.out per case to $MAKEFUN_OUTPUT_DIR (else /output).
"""

import json
import math
import os
import sys
from datetime import datetime, timedelta

LEVELS = (0.05, 0.25, 0.5, 0.75, 0.95)
Z = {0.05: -1.6448536269514722, 0.25: -0.6744897501960817, 0.5: 0.0,
     0.75: 0.6744897501960817, 0.95: 1.6448536269514722}
HOURS = range(2, 25)
MIN_SIGMA = 0.15


def parse_time(text):
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def quantile(sorted_vals, tau):
    n = len(sorted_vals)
    if n == 0:
        return None
    if n == 1:
        return sorted_vals[0]
    pos = tau * (n - 1)
    lo = int(pos)
    hi = min(lo + 1, n - 1)
    frac = pos - lo
    return sorted_vals[lo] * (1.0 - frac) + sorted_vals[hi] * frac


def ols_slope(xs, ys):
    """Slope (and intercept) of the least-squares line y ~ a + b*x."""
    n = len(xs)
    if n < 2:
        return 0.0, (sum(ys) / n if n else 0.0)
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= 0:
        return 0.0, my
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    b = sxy / sxx
    return b, my - b * mx


def ols3(rows, ys):
    """Least-squares fit y ~ b0 + b1*x1 + b2*x2 + b3*x3.

    rows: list of (x1, x2, x3); ys: list of y.  Returns (beta, resid_sd)
    or None when the system is singular or too small to fit.
    """
    n = len(ys)
    if n < 7:
        return None
    dim = 4
    mat = [[0.0] * (dim + 1) for _ in range(dim)]
    for (x1, x2, x3), y in zip(rows, ys):
        feats = (1.0, x1, x2, x3)
        for i in range(dim):
            for j in range(dim):
                mat[i][j] += feats[i] * feats[j]
            mat[i][dim] += feats[i] * y
    for col in range(dim):
        piv = max(range(col, dim), key=lambda r: abs(mat[r][col]))
        mat[col], mat[piv] = mat[piv], mat[col]
        if abs(mat[col][col]) < 1e-12:
            return None
        for r in range(dim):
            if r == col:
                continue
            fac = mat[r][col] / mat[col][col]
            for cc in range(col, dim + 1):
                mat[r][cc] -= fac * mat[col][cc]
    beta = [mat[i][dim] / mat[i][i] for i in range(dim)]
    ss = 0.0
    for (x1, x2, x3), y in zip(rows, ys):
        pred = beta[0] + beta[1] * x1 + beta[2] * x2 + beta[3] * x3
        ss += (y - pred) ** 2
    sd = math.sqrt(ss / max(1, n - dim))
    return beta, sd


def release_day_hours(comparable):
    """{hour: viewers} on the comparable's release day (hours 1..24)."""
    day = parse_time(comparable["day_start"])
    by_hour = {}
    for point in comparable.get("hourly") or []:
        viewers = point.get("viewers")
        if not viewers or viewers <= 0:
            continue
        try:
            when = parse_time(point["from"])
        except (KeyError, ValueError):
            continue
        delta = (when - day).total_seconds() / 3600.0
        if 0 <= delta < 24:
            by_hour[int(delta) + 1] = float(viewers)
    return by_hour


def is_console_only(comparable):
    platforms = comparable.get("platforms") or ""
    return "PC" not in platforms


def target_history_points(case):
    """(when, viewers) pairs of the target's own pre-release history.

    Prefers history_hourly; if it is too thin, aggregates the raw history
    points (minute reads in the final, sparse points in practice) into
    whole-hour means, which is the same measure.
    """
    day = parse_time(case["day_start"])
    pts = []
    for point in case.get("history_hourly") or []:
        viewers = point.get("viewers")
        if not viewers or viewers <= 0:
            continue
        try:
            when = parse_time(point["from"])
        except (KeyError, ValueError):
            continue
        if when < day:
            pts.append((when, float(viewers)))
    if len(pts) >= 6:
        return pts
    by_hour = {}
    for point in case.get("history") or []:
        viewers = point.get("viewers")
        if not viewers or viewers <= 0:
            continue
        try:
            when = parse_time(point["at"])
        except (KeyError, ValueError):
            continue
        if when < day:
            by_hour.setdefault(when.replace(minute=0, second=0, microsecond=0), []).append(float(viewers))
    agg = [(when, sum(vs) / len(vs)) for when, vs in sorted(by_hour.items())]
    return agg if len(agg) >= 6 else pts


def target_prepeak(case):
    """ln(max pre-release hourly / mean of last-24h pre-release hourly).

    Computed from the case's own history (points before day_start).
    For the GTA VI final the recorder only covers 2 days, so the peak
    also counts the GTA VI category's own pre-release series shipped
    in the case (same game, same measure), restricted to the 14 days
    before day_start so the Aug-27 premiere spike stays out.
    Returns None when the history is too thin to say anything.
    """
    day = parse_time(case["day_start"])
    pre = target_history_points(case)
    if len(pre) < 6:
        return None
    last24 = [v for when, v in pre if day - timedelta(hours=24) <= when < day]
    base = last24 if len(last24) >= 6 else [v for _, v in pre]
    mean_base = sum(base) / len(base)
    peak = max(v for _, v in pre)
    if "grand theft auto" in (case.get("game") or {}).get("name", "").lower():
        start = day - timedelta(days=14)
        for comp in case.get("comparables") or []:
            if "grand theft auto" not in (comp.get("name") or "").lower():
                continue
            for point in comp.get("hourly") or []:
                viewers = point.get("viewers")
                if not viewers or viewers <= 0:
                    continue
                try:
                    when = parse_time(point["from"])
                except (KeyError, ValueError):
                    continue
                if start <= when < day and viewers > peak:
                    peak = float(viewers)
    if mean_base <= 0 or peak <= 0:
        return None
    return math.log(peak / mean_base)


def comparable_prepeak(comparable):
    """Same feature for a comparable, from its full hourly series."""
    day = parse_time(comparable["day_start"])
    pre = []
    for point in comparable.get("hourly") or []:
        viewers = point.get("viewers")
        if not viewers or viewers <= 0:
            continue
        try:
            when = parse_time(point["from"])
        except (KeyError, ValueError):
            continue
        if when < day:
            pre.append((when, float(viewers)))
    if len(pre) < 6:
        return None
    last24 = [v for when, v in pre if day - timedelta(hours=24) <= when < day]
    base = last24 if len(last24) >= 6 else [v for _, v in pre]
    mean_base = sum(base) / len(base)
    peak = max(v for _, v in pre)
    if mean_base <= 0 or peak <= 0:
        return None
    return math.log(peak / mean_base)


def comparable_r24(comparable):
    """ln(hour-1 viewers / mean of the last 24 pre-release hours)."""
    day = parse_time(comparable["day_start"])
    hours = release_day_hours(comparable)
    if not hours.get(1):
        return None
    pre24 = []
    for point in comparable.get("hourly") or []:
        viewers = point.get("viewers")
        if not viewers or viewers <= 0:
            continue
        try:
            when = parse_time(point["from"])
        except (KeyError, ValueError):
            continue
        if day - timedelta(hours=24) <= when < day:
            pre24.append(float(viewers))
    if not pre24:
        return None
    mean_pre = sum(pre24) / len(pre24)
    if mean_pre <= 0:
        return None
    return math.log(hours[1] / mean_pre)


def comparable_vph(comparable):
    """ln(hour-1 viewers per live channel)."""
    day = parse_time(comparable["day_start"])
    for point in comparable.get("hourly") or []:
        try:
            when = parse_time(point["from"])
        except (KeyError, ValueError):
            continue
        if when == day:
            viewers = point.get("viewers")
            channels = point.get("channels")
            if not viewers or not channels or viewers <= 0 or channels <= 0:
                return None
            return math.log(viewers / channels)
    return None


def target_r24(case):
    """ln(hour-1 print / mean of the last 24 pre-release hours)."""
    known = case.get("known") or []
    if not known:
        return None
    try:
        p1 = float(known[0].get("viewers") or 0)
    except (TypeError, ValueError):
        return None
    if p1 <= 0:
        return None
    day = parse_time(case["day_start"])
    pre = target_history_points(case)
    if len(pre) < 6:
        return None
    last24 = [v for when, v in pre if day - timedelta(hours=24) <= when < day]
    base = last24 if len(last24) >= 6 else [v for _, v in pre]
    mean_base = sum(base) / len(base)
    if mean_base <= 0:
        return None
    return math.log(p1 / mean_base)


def target_vph(case):
    """ln(hour-1 viewers per live channel)."""
    known = case.get("known") or []
    if not known:
        return None
    try:
        viewers = float(known[0].get("viewers") or 0)
        channels = float(known[0].get("channels") or 0)
    except (TypeError, ValueError):
        return None
    if viewers <= 0 or channels <= 0:
        return None
    return math.log(viewers / channels)


def forecast_case(case):
    known = case.get("known") or []
    p1 = 1.0
    if known:
        try:
            p1 = max(1.0, float(known[0].get("viewers") or 1))
        except (TypeError, ValueError):
            p1 = 1.0

    # Pool: release-day curves of the comparables (skip the target game's
    # own pre-release history entry, which has no release day).
    curves = []
    for comp in case.get("comparables") or []:
        name = (comp.get("name") or "").lower()
        if "grand theft auto" in name:
            continue
        hours = release_day_hours(comp)
        if hours.get(1):
            curves.append({
                "console": is_console_only(comp),
                "hours": hours,
                "prepeak": comparable_prepeak(comp),
                "r24": comparable_r24(comp),
                "vph": comparable_vph(comp),
            })
    if not curves:
        return baseline_rows(p1)

    console = [c for c in curves if c["console"]]
    pool = console if len(console) >= 4 else curves

    # Target features, clamped to the pool's observed range (a missing
    # one falls back to the pool mean, i.e. no adjustment from it).
    feats = ("prepeak", "r24", "vph")
    target = {
        "prepeak": target_prepeak(case),
        "r24": target_r24(case),
        "vph": target_vph(case),
    }
    for key in feats:
        vals = [c[key] for c in pool if c[key] is not None]
        if not vals:
            target[key] = None
            continue
        if target[key] is None:
            target[key] = sum(vals) / len(vals)
        else:
            target[key] = min(max(target[key], min(vals)), max(vals))

    # Per-hour centre and spread: a three-predictor OLS fit of the
    # log-ratio curve on the target's pre-release features, over the
    # console pool.  Falls back to the pool median + IQR spread when
    # too few complete cases carry the hour.
    centre = {}
    sigma = {}
    for h in HOURS:
        vals = sorted(math.log(c["hours"][h]) - math.log(c["hours"][1])
                      for c in pool if h in c["hours"])
        if not vals:
            centre[h] = 0.0
            sigma[h] = 0.5
            continue
        centre[h] = quantile(vals, 0.5)
        spread = quantile(vals, 0.75) - quantile(vals, 0.25)
        sigma[h] = max(MIN_SIGMA, spread / 1.349)
        sel = [c for c in pool
               if h in c["hours"] and all(c[k] is not None for k in feats)]
        if len(sel) >= 7:
            fit = ols3([(c["prepeak"], c["r24"], c["vph"]) for c in sel],
                       [math.log(c["hours"][h]) - math.log(c["hours"][1])
                        for c in sel])
            if fit is not None:
                beta, sd = fit
                centre[h] = (beta[0] + beta[1] * target["prepeak"]
                             + beta[2] * target["r24"]
                             + beta[3] * target["vph"])
                sigma[h] = max(sd, MIN_SIGMA)

    rows = []
    for h in HOURS:
        qs = []
        for tau in LEVELS:
            q = p1 * math.exp(centre[h] + Z[tau] * sigma[h])
            qs.append(q)
        # Enforce non-decreasing quantiles, round, clip to the valid range.
        fixed = []
        prev = 0.0
        for q in qs:
            q = round(max(q, prev), 2)
            q = min(max(q, 0.01), 1e9)
            fixed.append(q)
            prev = q
        rows.append({"hour": h, "q": fixed})
    return rows


def baseline_rows(p1):
    """House persistence shape, used only when no comparable is usable."""
    rows = []
    for h in HOURS:
        spread = 0.15 * math.sqrt(h - 1)
        qs = [round(min(max(p1 * math.exp(Z[t] * spread), 0.01), 1e9), 2)
              for t in LEVELS]
        rows.append({"hour": h, "q": qs})
    return rows


def main():
    in_dir = os.environ.get("MAKEFUN_INPUT_DIR") or "/input"
    out_dir = os.environ.get("MAKEFUN_OUTPUT_DIR") or "/output"
    os.makedirs(out_dir, exist_ok=True)
    names = sorted(n for n in os.listdir(in_dir) if n.endswith(".in"))
    for name in names:
        case_id = name[:-3]
        with open(os.path.join(in_dir, name), "r", encoding="utf-8") as fh:
            case = json.load(fh)
        rows = forecast_case(case)
        payload = {"hours": rows}
        out_path = os.path.join(out_dir, case_id + ".out")
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, separators=(",", ":"))
            fh.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
