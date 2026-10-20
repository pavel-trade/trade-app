import math
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import speedometer as sp  # noqa: E402
from synthetic import scenario  # noqa: E402

DS = dict(range_len=100)  # синтетика короткая: диапазон 200 баров ещё не набран


# --- примитивы -------------------------------------------------------------
def test_ema_seed_is_first_value():
    out = sp.ema([10.0, 20.0, 30.0], 3)
    assert out[0] == 10.0
    assert out[1] == pytest.approx(0.5 * 20 + 0.5 * 10)
    assert out[2] == pytest.approx(0.5 * 30 + 0.5 * 15)


def test_ema_reseeds_after_na():
    out = sp.ema([sp.NA, 5.0, 7.0], 3)
    assert math.isnan(out[0]) and out[1] == 5.0 and out[2] == pytest.approx(6.0)


def test_sma_and_rma_seed():
    src = [1.0, 2.0, 3.0, 4.0]
    s = sp.sma(src, 3)
    assert math.isnan(s[1]) and s[2] == pytest.approx(2.0) and s[3] == pytest.approx(3.0)
    r = sp.rma(src, 3)
    assert r[2] == pytest.approx(2.0)                      # seed = SMA
    assert r[3] == pytest.approx(4 / 3 + 2 * 2 / 3)        # alpha = 1/3


def test_pivots_strict_left_nonstrict_right():
    src = [0, 1, 5, 5, 1, 0, 0]
    ph = sp.pivothigh(src, 2, 2)
    assert ph[4] == 5           # первый бар плато (индекс 2), подтверждён на 4
    assert math.isnan(ph[5])    # второй бар плато — не пивот
    pl = sp.pivotlow([5, 4, 1, 4, 5], 2, 2)
    assert pl[4] == 1


def test_log_makes_scale_invariant():
    bars, _ = scenario("impulse_up")
    scaled = [sp.Bar(b.t, b.o * 1000, b.h * 1000, b.l * 1000, b.c * 1000, b.v) for b in bars]
    a, b = sp.compute(bars), sp.compute(scaled)
    for x, y in zip(a["macd"][50:], b["macd"][50:]):
        assert x == pytest.approx(y, abs=1e-9)
    raw = sp.compute(bars, sp.Settings(use_log=False))
    raw2 = sp.compute(scaled, sp.Settings(use_log=False))
    assert raw2["macd"][150] == pytest.approx(1000 * raw["macd"][150])


def test_hist_is_macd_minus_signal_and_ao_defined():
    bars, _ = scenario("impulse_up")
    sr = sp.compute(bars)
    for i in range(len(bars)):
        assert sr["hist"][i] == pytest.approx(sr["macd"][i] - sr["signal"][i])
    assert math.isnan(sr["ao"][32]) and not math.isnan(sr["ao"][33])


def test_atr_normalization():
    bars, _ = scenario("impulse_up")
    st = sp.Settings(norm="atr")
    sr = sp.compute(bars, st)
    assert math.isnan(sr["macd"][12]) and not math.isnan(sr["macd"][13])
    i = 150
    assert sr["macd"][i] == pytest.approx(sr["macd_raw"][i] / sr["atr"][i])


def test_presets():
    st = sp.Settings.preset("big")
    assert (st.fast, st.slow, st.signal) == (13, 55, 13)


def test_phase_names():
    assert sp.phase(2, 1) == "рост ускоряется"
    assert sp.phase(1, 2) == "рост замедляется"
    assert sp.phase(-2, -1) == "спад ускоряется"
    assert sp.phase(-1, -2) == "спад замедляется"


# --- классификация профиля -------------------------------------------------
@pytest.mark.parametrize("name,expected", [
    ("impulse_up", "импульс"),
    ("impulse_down", "импульс"),
    ("contracting", "сужающаяся диагональ"),
    ("expanding", "расширяющаяся диагональ"),
])
def test_profile_classification(name, expected):
    bars, pts = scenario(name)
    r = sp.analyze_wave(bars, sp.compute(bars), pts)
    assert r["mode"] == "full"
    assert r["profile"] == expected


def test_profile_rules_direct():
    assert sp.classify_profile(5, 10, 4, 1, 0.35) == "импульс"
    assert sp.classify_profile(5, 10, 4, 5, 0.35) == sp.UNDEF_W4   # волна 4 не у нуля
    assert sp.classify_profile(9, 6, 3, 0, 0.35) == "сужающаяся диагональ"
    assert sp.classify_profile(3, 6, 9, 0, 0.35) == "расширяющаяся диагональ"
    assert sp.classify_profile(5, 3, 6, 0, 0.35) == "неопределённо"


def test_impulse_wave_table_values():
    bars, pts = scenario("impulse_up")
    r = sp.analyze_wave(bars, sp.compute(bars), pts)
    assert r["direction"] == "вверх"
    assert r["n_bars"] == 120 and r["rule"]["status"] == "ok"
    assert r["volume_ok"] is True
    assert r["segments"][2]["vol_to_3"] == pytest.approx(1.0)
    assert r["ratios"]["3/1"] == pytest.approx(30 / 12, rel=0.1)


def test_ends_only_mode():
    bars, pts = scenario("impulse_up")
    r = sp.analyze_wave(bars, sp.compute(bars), [pts[0], None, None, None, None, pts[5]])
    assert r["mode"] == "ends" and r["n_bars"] == 120 and "profile" not in r


def test_bad_order():
    bars, pts = scenario("impulse_up")
    p = list(pts)
    p[2], p[3] = p[3], p[2]
    assert "error" in sp.analyze_wave(bars, sp.compute(bars), p)


# --- правило 100-140 -------------------------------------------------------
def test_tf_advice():
    assert sp.tf_advice(120, 3600)["status"] == "ok"
    a = sp.tf_advice(250, 3600)
    assert a["status"] == "large" and a["tf"] == "2ч" and "×2.1" in a["text"]
    b = sp.tf_advice(60, 3600)
    assert b["status"] == "small" and b["tf"] == "30м" and "÷2.0" in b["text"]
    c = sp.tf_advice(110 * 4, 86400)          # 440 дней на 1Д
    assert c["tf"] == "3Д"
    d = sp.tf_advice(99, 3600)                # ближайший меньший, не текущий
    assert d["tf"] == "45м"


# --- дивергенции -----------------------------------------------------------
def kinds(name, **kw):
    bars, pts = scenario(name)
    ds = sp.DivSettings(**{**DS, **kw})
    return pts, sp.find_divergences(bars, sp.compute(bars), ds)


def test_bearish_regular_at_wave5_of_impulse():
    pts, ds = kinds("impulse_up", hidden=False)
    assert [(d.kind, d.prev_bar, d.pivot_bar) for d in ds] == [("bear_reg", pts[3], pts[5])]
    d = ds[0]
    assert d.confirm_bar == d.pivot_bar + 5          # задержка = «баров справа»
    assert d.price > d.prev_price and d.osc < d.prev_osc


def test_bullish_regular_at_wave5_of_down_impulse():
    pts, ds = kinds("impulse_down", hidden=False)
    assert [(d.kind, d.prev_bar, d.pivot_bar) for d in ds] == [("bull_reg", pts[3], pts[5])]


def test_hidden_bullish():
    pts, ds = kinds("hidden_bull", regular=False)
    hid = [d for d in ds if d.kind == "bull_hid"]
    assert len(hid) == 1
    d = hid[0]
    assert abs(d.pivot_bar - pts[4]) <= 3 and abs(d.prev_bar - pts[2]) <= 3
    assert d.price > d.prev_price and d.osc < d.prev_osc


def test_zero_filter_blocks_regular():
    # медвежья у импульса — выше нуля, фильтр её пропускает; а если требовать «ниже нуля»
    # для медвежьих (зеркальный случай), её бы не было — проверяем на бычьей версии без фильтра
    _, with_f = kinds("impulse_up", hidden=False, zero_filter=True)
    assert with_f
    bars, _ = scenario("impulse_up")
    sr = sp.compute(bars)
    sr_neg = dict(sr, macd=[x - 20 for x in sr["macd"]])   # сдвигаем всё ниже нуля
    res = sp.find_divergences(bars, sr_neg, sp.DivSettings(hidden=False, **DS))
    assert res == []


def test_min_diff_filter():
    _, ds = kinds("impulse_up", hidden=False, min_diff_pct=90.0)
    assert ds == []


def test_three_peaks():
    pts, ds = kinds("contracting", pairs=False, three_peaks=True)
    fade = [d for d in ds if d.kind == "three_fade" and d.side == "high"]
    assert len(fade) == 1 and fade[0].bars == (pts[1], pts[3], pts[5])
    pts, ds = kinds("expanding", pairs=False, three_peaks=True)
    grow = [d for d in ds if d.kind == "three_grow" and d.side == "high"]
    assert len(grow) == 1 and grow[0].bars == (pts[1], pts[3], pts[5])
    # у импульса пики 1-3-5 не монотонны
    _, ds = kinds("impulse_up", pairs=False, three_peaks=True)
    assert not [d for d in ds if d.side == "high"]


# --- слепые зоны -----------------------------------------------------------
def test_blind_zone_from_extreme_to_cross():
    bars, pts = scenario("impulse_up")
    sr = sp.compute(bars)
    zones, live = sp.blind_zones(bars, sr["ema_fast"], sr["ema_slow"])
    up = [z for z in zones if z.regime == 1]
    last = up[-1]
    assert abs(last.ext_bar - pts[5]) <= 1                  # экстремум — вершина волны 5
    diff = [a - b for a, b in zip(sr["ema_fast"], sr["ema_slow"])]
    assert diff[last.cross_bar] < 0 <= diff[last.cross_bar - 1]
    assert all(diff[j] > 0 for j in range(last.ext_bar, last.cross_bar))  # линия ещё «старая»
    assert last.ext_price == max(b.h for b in bars[last.ext_bar:last.cross_bar + 1])
    assert last.opp_price == min(b.l for b in bars[last.ext_bar:last.cross_bar + 1])
    assert live is not None and live.regime == -1


# --- объём -----------------------------------------------------------------
def test_no_volume():
    bars, pts = scenario("impulse_up")
    nov = [sp.Bar(b.t, b.o, b.h, b.l, b.c, sp.NA) for b in bars]
    r = sp.analyze_wave(nov, sp.compute(nov), pts)
    assert r["has_volume"] is False and r["volume_ok"] is False
    assert math.isnan(r["segments"][0]["vol_avg"])


# --- CSV и CLI -------------------------------------------------------------
def test_csv_roundtrip_and_cli(tmp_path, capsys):
    bars, pts = scenario("impulse_up")
    p = tmp_path / "x.csv"
    sp.save_csv(str(p), bars)
    back = sp.load_csv(str(p))
    assert len(back) == len(bars) and back[10].t == bars[10].t
    wave = ",".join(sp._fmt_t(bars[i].t)[:10] for i in pts)
    assert sp.main([str(p), "--tail", "2", "--divs", "--zones", "--wave", wave]) == 0
    out = capsys.readouterr().out
    assert "импульс" in out and "bear_reg" in out


def test_tradingview_export_format(tmp_path):
    p = tmp_path / "tv.csv"
    p.write_text("time,open,high,low,close,Volume\n1704067200,1,2,0.5,1.5,10\n1704153600,1.5,2,1,1.8,\n")
    b = sp.load_csv(str(p))
    assert b[0].t == 1704067200000 and b[0].v == 10 and math.isnan(b[1].v)


def test_bundled_test_data_matches_generator():
    path = os.path.join(os.path.dirname(HERE), "test_data.csv")
    bars = sp.load_csv(path)
    gen, pts = scenario("impulse_up")
    assert len(bars) == len(gen)
    r = sp.analyze_wave(bars, sp.compute(bars), pts)
    assert r["profile"] == "импульс"
