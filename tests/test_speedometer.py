"""Тесты эталона для pine/tw_speedometer.pine («скорость + ускорение»)."""
import math
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "reference"))

import speedometer as sp  # noqa: E402
from synthetic import scenario  # noqa: E402


def bars_from_log(path, start=1704067200000):
    """Бары из пути 100*ln(цены): open = прошлый close, тени ±0.1 %."""
    out, prev = [], path[0]
    for i, x in enumerate(path):
        o, c = prev, x
        out.append(sp.Bar(start + i * 86_400_000, math.exp(o / 100), math.exp((max(o, c) + 0.1) / 100),
                          math.exp((min(o, c) - 0.1) / 100), math.exp(c / 100), 1000.0))
        prev = x
    return out


# --- фазы на простых профилях ---------------------------------------------
@pytest.mark.parametrize("path_fn,expected", [
    (lambda t: 0.004 * t * t, "рост ускоряется"),            # растёт всё быстрее
    (lambda t: 60 * math.sqrt(t), "рост замедляется"),        # растёт всё медленнее
    (lambda t: -0.004 * t * t, "спад ускоряется"),
    (lambda t: -60 * math.sqrt(t), "спад замедляется"),
])
def test_phase_on_simple_profiles(path_fn, expected):
    bars = bars_from_log([460 + path_fn(t) for t in range(300)])
    sr = sp.compute(bars)
    phases = {sp.phase4(sr["macd"][i], sr["hist"][i]) for i in range(150, 300)}
    assert phases == {expected}


def test_phase4_rules():
    assert sp.phase4(1, 0.1) == "рост ускоряется"
    assert sp.phase4(1, -0.1) == "рост замедляется"
    assert sp.phase4(0, 0) == "рост замедляется"
    assert sp.phase4(-1, -0.1) == "спад ускоряется"
    assert sp.phase4(-1, 0.1) == "спад замедляется"
    assert sp.phase4(sp.NA, 1) == "нет данных"


def test_accel_is_speed_minus_its_ema5():
    bars, _ = scenario("impulse_up")
    sr = sp.compute(bars)
    e = sp.ema(sr["macd"], 5)
    for i in range(len(bars)):
        assert sr["hist"][i] == pytest.approx(sr["macd"][i] - e[i])


# --- авто-масштаб ----------------------------------------------------------
def test_percentile_linear():
    assert sp.percentile_linear([1, 2, 3, 4, 5], 50) == 3
    assert sp.percentile_linear([1, 2, 3, 4, 5], 95) == pytest.approx(4.8)
    assert sp.percentile_linear([10], 95) == 10


def test_auto_scale_once_and_meaningful():
    bars, _ = scenario("impulse_up")
    sr = sp.compute(bars)
    m = sp.auto_scale(sr, 200)
    assert m > 1                                   # ускорение мельче скорости
    assert math.isnan(sp.auto_scale(sr, 10_000))   # мало баров → NA (в Pine — фиксированный множитель)
    ps = sp.percentile_linear([abs(x) for x in sr["macd"][-200:]], 95)
    pa = sp.percentile_linear([abs(x) for x in sr["hist"][-200:]], 95)
    assert m == pytest.approx(ps / pa)


# --- ранние сигналы --------------------------------------------------------
def test_crossings_match_ta_cross():
    assert sp.crossings([-1, 0, 1, -1, -2, 0.5]) == [(2, 1), (3, -1), (5, 1)]


def test_speed_sign_change_is_end_of_blind_zone():
    bars, _ = scenario("impulse_up")
    sr = sp.compute(bars)
    zones, _ = sp.blind_zones(bars, sr["ema_fast"], sr["ema_slow"])
    cross_bars = {b for b, _ in sp.crossings(sr["macd"])}
    assert {z.cross_bar for z in zones} <= cross_bars


# --- режим «Пики волн 1-3-5» -----------------------------------------------
@pytest.mark.parametrize("name,expected", [
    ("impulse_up", "импульс"),
    ("impulse_down", "импульс"),
    ("contracting", "сужающаяся диагональ"),
    ("expanding", "расширяющаяся диагональ"),
])
def test_peaks_135_profile(name, expected):
    bars, pts = scenario(name)
    r = sp.peaks_135(bars, sp.compute(bars), pts)
    assert r["active"] and r["profile"] == expected


def test_peaks_135_positions_and_values():
    bars, pts = scenario("impulse_up")
    sr = sp.compute(bars)
    r = sp.peaks_135(bars, sr, pts)
    for sg in r["segments"]:
        a, b = pts[sg["wave"] - 1], pts[sg["wave"]]
        seg = sr["macd"][a:b + 1]
        want = max(seg) if sg["wave"] % 2 == 1 else min(seg)
        assert sg["value"] == want and a <= sg["bar"] <= b
    # пик волны 3 — внутри волны 3, волна 4 у нуля
    assert abs(r["segments"][3]["value"]) < 0.35 * r["segments"][2]["value"]


def test_peaks_135_inactive_without_points():
    bars, pts = scenario("impulse_up")
    sr = sp.compute(bars)
    assert sp.peaks_135(bars, sr, [None] * 6) == {"active": False}
    partial = sp.peaks_135(bars, sr, pts[:4] + [None, None])
    assert partial["active"] and partial["profile"] is None
    assert partial["segments"][3] is None and partial["segments"][2] is not None


# --- дивергенции по скорости ------------------------------------------------
DS = dict(source="macd", range_len=100)


def test_regular_divergences_at_wave5():
    for name, kind in (("impulse_up", "bear_reg"), ("impulse_down", "bull_reg")):
        bars, pts = scenario(name)
        ds = sp.find_divergences(bars, sp.compute(bars), sp.DivSettings(hidden=False, **DS))
        assert [(d.kind, d.prev_bar, d.pivot_bar) for d in ds] == [(kind, pts[3], pts[5])]
        assert ds[0].confirm_bar - ds[0].pivot_bar == 5   # запаздывание = «баров справа»


def test_hidden_bullish_near_wave4():
    bars, pts = scenario("hidden_bull")
    ds = sp.find_divergences(bars, sp.compute(bars), sp.DivSettings(regular=False, **DS))
    hid = [d for d in ds if d.kind == "bull_hid"]
    assert len(hid) == 1 and abs(hid[0].pivot_bar - pts[4]) <= 3


def test_noise_filter_and_sign_filter():
    bars, _ = scenario("impulse_up")
    sr = sp.compute(bars)
    assert sp.find_divergences(bars, sr, sp.DivSettings(hidden=False, min_diff_pct=90, **DS)) == []
    shifted = dict(sr, macd=[x - 20 for x in sr["macd"]])      # медвежья ниже нуля → отсекается
    assert sp.find_divergences(bars, shifted, sp.DivSettings(hidden=False, **DS)) == []
    assert sp.find_divergences(bars, shifted, sp.DivSettings(hidden=False, zero_filter=False, **DS))


# --- линтер Pine для нового файла --------------------------------------------
def test_pine_file_passes_lint():
    import pine_lint
    path = os.path.join(ROOT, "pine", "tw_speedometer.pine")
    if not os.path.exists(path):
        pytest.skip("pine/tw_speedometer.pine ещё не создан")
    assert pine_lint.lint(path) == []
