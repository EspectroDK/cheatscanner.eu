"""Mouse-input lattice: view changes a mouse cannot produce, concentrated before shots.

**Why.** With raw mouse input, every view change is a whole number of mouse
counts times ``sensitivity * m_yaw`` (0.022 deg by default). Tick-level view
changes add up several user commands, so they stay whole multiples of that
divisor. Measured on Valve matchmaking demos, 27 of 30 players had 95-100% of
their unscoped view changes on such a lattice, with a sensitivity that stayed
identical across matches. An aimbot that writes view angles adds steps that are
not whole mouse counts. The same idea drives the sensitivity/GCD checks of
server-side Minecraft anti-cheats (Grim, MX-Project).

**What is measured.** Per player and axis the divisor ``d`` is the largest step
that places most small unscoped view changes on a lattice (fine grid search;
the largest divisor within ``fit_margin`` of the best fit, so ``d/2`` and other
fractions are not chosen). A view change is off the lattice when
``|delta/d - round(delta/d)| > tolerance`` on either axis. Off-lattice rates
are compared between the ``pre_shot_ms`` before gun shots and the rest of the
player's own movement ("idle"), so each player is their own baseline.

**False-positive controls.**

* Players whose idle movement does not fit a lattice (controller, mouse
  acceleration, raw input off, sensitivity changed mid-match) are recorded as
  ``lattice_valid = False`` and never produce evidence.
* Scoped ticks (zoom changes the divisor), dead or non-consecutive ticks,
  freeze time and teleports (``max_step_deg``) are excluded.
* Evidence needs many off-lattice moves before shots, a rate well above the
  player's own idle rate, and a one-sided binomial test.
* Idle movement must itself be clean (``max_idle_off_rate``): noisy input
  that is off the lattice everywhere is not evidence. The idle rate checked
  here is the median over rounds, because a cheat that is switched on for a
  few rounds also moves the view between shots in those rounds; noisy input
  is off the lattice in every round. The ratio and the binomial test still
  use the idle rate over the whole match.
* Calibrated on 121 CS2CD Mirage matches (docs/validation/cs2cd): legitimate
  players have essentially no off-lattice moves before shots (99th percentile
  0.08% on 46 matchmaking and pro demos); the rule fires for 14 labelled
  cheaters, 1 clean and 2 unlabelled players, all with idle movement on the
  lattice and aim before shots off it. Sensitivity is recorded per player as
  a fingerprint for comparisons across matches.
"""

from __future__ import annotations

import numpy as np
from scipy.stats import binomtest

from cs2_analyzer.detectors.base import AnalysisContext, Detector, EvidenceAxis, EvidenceEvent, EvidenceGroup, ramp

M_YAW = 0.022  # CS2 default degrees per mouse count at sensitivity 1


def _lattice_fit(a: np.ndarray, divisors: np.ndarray, tol: float) -> np.ndarray:
    """Fraction of |deltas| ``a`` within ``tol`` (in units of d) of a whole multiple, per divisor."""
    out = np.empty(len(divisors))
    for i in range(0, len(divisors), 256):
        d = divisors[i:i + 256, None]
        x = a[None, :] / d
        out[i:i + 256] = (np.abs(x - np.round(x)) <= tol).mean(axis=1)
    return out


def estimate_divisor(deltas: np.ndarray, *, lo: float = 0.0015, hi: float = 0.2, max_delta: float = 5.0,
                     min_samples: int = 300, tol: float = 0.1, fit_margin: float = 0.02,
                     max_samples: int = 5000, seed: int = 0) -> tuple[float, float]:
    """Return ``(divisor, fit)`` for the view-change lattice, or ``(nan, nan)``.

    The grid is relative (a divisor error of e shifts the k-th multiple by k*e),
    so the coarse pass uses only small changes, few counts each, and picks the
    largest divisor within ``fit_margin`` of the best fit; a fine pass on all
    changes then locates it precisely.
    """
    a = np.abs(np.asarray(deltas, dtype=np.float64))
    a = a[np.isfinite(a) & (a > 0.003) & (a < max_delta)]
    if a.size < min_samples:
        return float("nan"), float("nan")
    rng = np.random.default_rng(seed)
    if a.size > max_samples:
        a = rng.choice(a, max_samples, replace=False)
    small = a[a < 0.15]
    if small.size < min_samples // 3:
        small = np.sort(a)[: max(min_samples // 3, 1)]
    coarse = np.geomspace(lo, hi, 2000)  # 0.2% steps
    fc = _lattice_fit(small, coarse, tol)
    good = fc >= fc.max() - fit_margin
    j = np.nonzero(good)[0][-1]
    i0 = j
    while i0 > 0 and good[i0 - 1]:
        i0 -= 1
    d0 = float(np.sqrt(coarse[i0] * coarse[j]))  # centre of the plateau, not its edge
    fine = d0 * np.linspace(0.97, 1.03, 1201)
    ff = _lattice_fit(a, fine, tol)
    d = float(fine[int(np.argmax(ff))])
    d = refine_divisor(np.abs(np.asarray(deltas, dtype=np.float64)), d, tol)
    return d, float(_lattice_fit(a, np.array([d]), tol)[0])


def refine_divisor(a: np.ndarray, d: float, tol: float = 0.1, max_delta: float = 40.0) -> float:
    """Least-squares refinement with progressively larger changes.

    A relative error e in ``d`` moves the k-th multiple by k*e counts, so a
    30 deg flick (~1000 counts) falls off the lattice unless ``d`` is known to
    about 1e-5. Each pass fits ``d = sum(a*k) / sum(k^2)`` on the changes that
    are already on the lattice, then admits larger ones.
    """
    a = a[np.isfinite(a) & (a > 0.003) & (a < max_delta)]
    for limit in (0.5, 2.0, 8.0, max_delta):
        x = a[a < limit]
        if x.size < 20:
            continue
        k = np.round(x / d)
        on = (k > 0) & (np.abs(x / d - k) <= tol)
        if on.sum() >= 20:
            d = float(np.sum(x[on] * k[on]) / np.sum(k[on] ** 2))
    return d


def off_lattice(delta: np.ndarray, d: float, tol: float) -> np.ndarray:
    """True where a non-zero view change is not a whole multiple of ``d``."""
    x = np.abs(delta) / d
    return (np.abs(delta) > 0.003) & (np.abs(x - np.round(x)) > tol)


def round_median_rate(hit: np.ndarray, base: np.ndarray, round_of: np.ndarray, min_n: int,
                      min_rounds: int = 3) -> float:
    """Median over rounds of ``hit.sum() / base.sum()``, using rounds with at least ``min_n``
    base ticks; ``nan`` when fewer than ``min_rounds`` rounds qualify."""
    r = np.asarray(round_of)[base]
    n = np.bincount(r)
    k = np.bincount(np.asarray(round_of)[hit & base], minlength=n.size)
    use = n >= min_n
    if use.sum() < min_rounds:
        return float("nan")
    return float(np.median(k[use] / n[use]))


def _wrap(a):
    return (a + 180.0) % 360.0 - 180.0


class InputLatticeDetector(Detector):
    name = "input_lattice"
    axis = EvidenceAxis.IMPOSSIBLE_MECHANICS
    group = EvidenceGroup.IMPOSSIBLE
    default_reliability = 0.6

    def analyze(self, ctx: AnalysisContext) -> list[EvidenceEvent]:
        cfg = ctx.cfg(self.name)
        w = ctx.world
        tol = float(cfg.get("tolerance", 0.1))
        max_step = float(cfg.get("max_step_deg", 40.0))
        min_fit = float(cfg.get("min_lattice_fit", 0.95))
        pre = w.ticks_for_ms(float(cfg.get("pre_shot_ms", 500)))
        post = w.ticks_for_ms(float(cfg.get("post_shot_ms", 250)))
        min_pre_moves = int(cfg.get("min_pre_moves", 100))
        min_off_rate = float(cfg.get("min_off_rate", 0.1))
        full_off_rate = float(cfg.get("full_off_rate", 0.4))
        min_ratio = float(cfg.get("min_rate_ratio", 3.0))
        max_idle = float(cfg.get("max_idle_off_rate", 0.01))
        max_p = float(cfg.get("max_p_value", 1e-6))
        min_round_idle = int(cfg.get("min_round_idle_moves", 200))
        emit = bool(cfg.get("emit_events", False))
        events = []
        for p in range(w.P):
            if not ctx.analyze_player(p):
                continue
            dyaw = np.r_[np.nan, _wrap(np.diff(w.yaw[p]))]
            dpitch = np.r_[np.nan, np.diff(w.pitch[p])]
            ok = (w.alive[p] & np.r_[False, w.alive[p][:-1]] & w.live
                  & ~w.scoped[p] & np.r_[False, ~w.scoped[p][:-1]]
                  & np.isfinite(dyaw) & np.isfinite(dpitch)
                  & (np.abs(dyaw) < max_step) & (np.abs(dpitch) < max_step))
            moving = ok & ((np.abs(dyaw) > 0.003) | (np.abs(dpitch) > 0.003))

            st = w.shot_ticks.get(p, np.array([], dtype=int))
            near = np.zeros(w.T, dtype=bool)
            pre_mask = np.zeros(w.T, dtype=bool)
            for s in st:
                pre_mask[max(0, s - pre):s + 1] = True
                near[max(0, s - pre):min(w.T, s + post + 1)] = True
            idle = moving & ~near

            d_yaw, fit_yaw = estimate_divisor(dyaw[idle])
            d_pitch, fit_pitch = estimate_divisor(dpitch[idle])
            if not np.isfinite(d_pitch) and np.isfinite(d_yaw):
                d_pitch, fit_pitch = d_yaw, float("nan")  # m_pitch == m_yaw by default
            valid = bool(np.isfinite(d_yaw) and fit_yaw >= min_fit and (not np.isfinite(fit_pitch) or fit_pitch >= min_fit))

            summary = {"divisor_yaw": d_yaw, "divisor_pitch": d_pitch,
                       "sensitivity": d_yaw / M_YAW if np.isfinite(d_yaw) else float("nan"),
                       "fit_yaw": fit_yaw, "fit_pitch": fit_pitch, "lattice_valid": valid,
                       "idle_moves": int(idle.sum()), "shots": int(st.size)}
            if not np.isfinite(d_yaw):
                ctx.observe(f"{self.name}_summary", p, **summary)
                continue

            off = off_lattice(dyaw, d_yaw, tol) | off_lattice(dpitch, d_pitch, tol)
            pre_moves = moving & pre_mask
            n_idle, k_idle = int(idle.sum()), int((off & idle).sum())
            n_pre, k_pre = int(pre_moves.sum()), int((off & pre_moves).sum())
            idle_rate = k_idle / n_idle if n_idle else float("nan")
            idle_round_rate = round_median_rate(off & idle, idle, w.round_of, min_round_idle)
            if not np.isfinite(idle_round_rate):
                idle_round_rate = idle_rate
            pre_rate = k_pre / n_pre if n_pre else float("nan")
            p_value = float("nan")
            if n_pre and n_idle:
                p_value = float(binomtest(k_pre, n_pre, max(idle_rate, 1e-4), alternative="greater").pvalue)
            step = np.hypot(dyaw, dpitch)

            shots_with_off = 0
            for s in st:
                win = slice(max(0, s - pre), s + 1)
                mv, of = moving[win], (off & moving)[win]
                n_off = int(of.sum())
                shots_with_off += n_off > 0
                ctx.observe(self.name, p, tick=w.tick(int(s)), round=int(w.round_of[s]), weapon=w.weapon[p, s],
                            moves=int(mv.sum()), off_moves=n_off,
                            off_deg=float(np.nansum(step[win][of])),
                            max_off_step_deg=float(np.nanmax(step[win][of])) if n_off else 0.0)

            summary.update(idle_off_rate=idle_rate, idle_off_rate_round_median=idle_round_rate, pre_moves=n_pre,
                           pre_off_rate=pre_rate, shots_with_off=int(shots_with_off), p_value=p_value)
            ctx.observe(f"{self.name}_summary", p, **summary)

            if not (emit and valid and n_pre >= min_pre_moves and np.isfinite(pre_rate)):
                continue
            if (pre_rate >= min_off_rate and pre_rate >= min_ratio * max(idle_rate, 1e-3) and idle_round_rate <= max_idle
                    and p_value <= max_p):
                sev = 0.3 + 0.7 * ramp(pre_rate, min_off_rate, full_off_rate)
                idx = np.nonzero(off & pre_moves)[0]
                metrics = {k: summary[k] for k in ("sensitivity", "fit_yaw", "idle_off_rate", "pre_off_rate",
                                                   "pre_moves", "shots_with_off", "p_value")}
                events.append(self.event(
                    ctx, p, int(idx[0]), int(idx[len(idx) // 2]), int(idx[-1]), sev, 1.0, None, metrics,
                    {"scope": "match"},
                    f"{pre_rate * 100:.0f}% of view changes in the {pre * w.dt * 1000:.0f} ms before shots were not whole "
                    f"mouse counts at sensitivity {summary['sensitivity']:.2f}, against {idle_rate * 100:.1f}% of the "
                    f"player's other movement (p = {p_value:.1e})."))
        return events
