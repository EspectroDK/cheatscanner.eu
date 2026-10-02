import numpy as np

from cs2_analyzer.detectors.input_lattice import M_YAW, estimate_divisor, off_lattice, round_median_rate


def _mouse_deltas(sens, n=4000, seed=1):
    rng = np.random.default_rng(seed)
    counts = rng.integers(-40, 41, n)
    counts = counts[counts != 0]
    return counts * sens * M_YAW


def test_divisor_recovers_sensitivity():
    for sens in (0.27, 1.0, 1.289, 2.5, 3.87):
        d, fit = estimate_divisor(_mouse_deltas(sens))
        assert abs(d / M_YAW - sens) < 0.01 * sens, (sens, d / M_YAW)
        assert fit > 0.99


def test_fraction_of_divisor_is_not_chosen():
    # d/2 and d/3 fit a lattice of d equally well; the largest must win
    d, _ = estimate_divisor(_mouse_deltas(1.8))
    assert abs(d - 1.8 * M_YAW) < 0.001


def test_written_angles_are_off_lattice():
    sens = 1.3
    human = _mouse_deltas(sens, 1000)
    rng = np.random.default_rng(2)
    bot = rng.uniform(-2, 2, 1000)  # arbitrary angles, not whole mouse counts
    d, _ = estimate_divisor(human)
    assert off_lattice(human, d, 0.1).mean() < 0.01
    assert off_lattice(bot, d, 0.1).mean() > 0.6


def test_no_lattice_without_mouse_quantisation():
    d, fit = estimate_divisor(np.random.default_rng(3).uniform(-3, 3, 4000))
    assert fit < 0.5


def test_round_median_rate_ignores_a_few_cheating_rounds():
    rounds = np.repeat(np.arange(1, 11), 300)
    base = np.ones(rounds.size, dtype=bool)
    hit = np.zeros(rounds.size, dtype=bool)
    hit[(rounds <= 3) & (np.arange(rounds.size) % 10 == 0)] = True  # 10% off the lattice in rounds 1-3 only
    assert hit.mean() > 0.01
    assert round_median_rate(hit, base, rounds, 200) == 0.0
    noisy = np.arange(rounds.size) % 20 == 0  # 5% in every round: noisy input stays noisy
    assert abs(round_median_rate(noisy, base, rounds, 200) - 0.05) < 1e-9
    assert np.isnan(round_median_rate(hit, base, rounds, 1000))  # too few qualifying rounds
