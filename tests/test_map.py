"""Тесты эталона «TW Карта рынка» (reference/map.py)."""
import math
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "reference"))

import map as mm  # noqa: E402
from speedometer import NA, Bar  # noqa: E402
from synthetic import build  # noqa: E402

LEAD = [(60, -25.0, 1000), (10, 0.0, 900)]
ST = mm.MapSettings(zz_len=20)   # внутренние ноги коррекции a-b-c — «малые», крупные свинги — A, B, C


def setup_scenario(c_change=-11.72, vol_corr=(1200, 900, 700), rally=45.0, noise=0.05):
    """Импульс A->B (+40 % лог), коррекция a-b-c (по умолчанию 61.8 %), затем ралли."""
    va, vb, vc = vol_corr
    segs = [(70, 40.0, 2500), (14, -18.0, va), (5, 5.0, vb), (30, c_change, vc), (60, rally, 1500)]
    return build(segs, lead=LEAD, tail=[], noise=noise)


def statuses(recs):
    seq = []
    for r in recs:
        if not seq or seq[-1] != r["status"]:
            seq.append(r["status"])
    return seq


# --- M10: «Зона -> Истощение -> Триггер» -----------------------------------
def test_impulse_and_618_correction_passes_zone_exhaustion_trigger():
    bars, pts = setup_scenario()
    recs = mm.MarketMap(bars, ST).run()
    assert statuses(recs) == [0, 1, 2, 3]
    trig = [r for r in recs if r["trigger_event"]]
    assert len(trig) == 1                                   # метка «Триггер» — один раз
    t = trig[0]
    A, B, C = t["A"][0], t["B"][0], t["C"][0]
    assert abs(A - pts[0]) <= 2 and abs(B - pts[1]) <= 2 and abs(C - pts[4]) <= 2
    assert t["ret"] == pytest.approx(0.618, abs=0.02)
    assert t["d"] == 1 and t["bar"] > C
    it = t["items"]
    assert it[0] == mm.OK and it[1] == mm.OK            # зона
    assert it[2] == mm.OK and it[3] == mm.OK            # истощение: конвергенция, объём ниже
    assert it[4] == mm.OK                               # слом структуры
    assert bars[t["bar"]].c > t["trigger"]
    assert t["score"] >= 5
    # статусы идут строго по порядку
    first = {s: next(r["bar"] for r in recs if r["status"] == s) for s in (1, 2, 3)}
    assert first[1] < first[2] < first[3]


def test_correction_beyond_a_cancels_setup():
    bars, pts = setup_scenario(c_change=-32.0)          # откат > 100 % волны 1
    recs = mm.MarketMap(bars, ST).run()
    after_b = [r for r in recs if r["bar"] > pts[1] + 25]
    assert any("отмена" in r["reason"] for r in after_b)
    assert not any(r["status"] == 3 for r in after_b if r["B"] and r["B"][0] == pytest.approx(pts[1], abs=2))
    # в момент выхода за A статус сбрасывается
    cancel = next(r for r in after_b if "отмена" in r["reason"])
    assert cancel["status"] == 0 and cancel["ret"] >= 1


def test_slow_correction_condition_fails_when_correction_is_fast():
    bars, _ = setup_scenario()
    st = mm.MapSettings(zz_len=20, k_slow=0.05)         # почти любая коррекция «быстрая»
    recs = mm.MarketMap(bars, st).run()
    assert 3 not in statuses(recs)
    assert any(r["items"][1] == mm.FAIL for r in recs if r["cand"])


def test_volume_condition_and_no_volume_disables_vwap_and_profile():
    bars, pts = setup_scenario(vol_corr=(4000, 4000, 4000))   # объём коррекции выше импульса
    recs = mm.MarketMap(bars, ST).run()
    r = next(r for r in recs if r["cand"] and r["C"] and r["C"][0] > pts[3] - 3 and r["bar"] > pts[3])
    assert r["items"][3] == mm.FAIL

    nov = [Bar(b.t, b.o, b.h, b.l, b.c, NA) for b in setup_scenario()[0]]
    recs = mm.MarketMap(nov, ST).run()
    assert all(r["items"][3] == mm.NODATA and r["items"][7] == mm.NODATA for r in recs)
    assert mm.volume_profile(nov, 0, len(nov) - 1) is None                    # M7 отключён
    assert all(math.isnan(x) for x in mm.anchored_vwap(nov, 10))             # M6 отключён
    assert not mm.has_volume(nov)


def test_items_are_tristate_and_score_counts_ok():
    bars, _ = setup_scenario()
    for r in mm.MarketMap(bars, ST).run():
        assert all(x in (mm.OK, mm.FAIL, mm.NODATA) for x in r["items"])
        assert r["score"] == sum(1 for x in r["items"][:8] if x == mm.OK)


# --- M2: свинги ------------------------------------------------------------
def test_pivot_swings_alternate():
    bars, _ = setup_scenario()
    recs = mm.MarketMap(bars, mm.MapSettings(zz_len=5)).run()
    sw = recs[-1]["swings"]
    assert all(sw[i][2] != sw[i + 1][2] for i in range(len(sw) - 1))


def test_two_highs_in_a_row_keep_the_higher():
    # два пивота максимума без минимума между ними (по L): остаётся больший
    path = [10, 11, 12, 13, 12.5, 12.8, 13.6, 13, 12, 11, 10, 9, 8, 7, 8, 9]
    bars = [Bar(i * 86_400_000, p, p + 0.1, p - 0.1, p, 100.0) for i, p in enumerate(path)]
    recs = mm.MarketMap(bars, mm.MapSettings(zz_len=2)).run()
    highs = [s for s in recs[-1]["swings"] if s[2] == 1]
    assert highs and highs[-1][1] == pytest.approx(13.7)


def test_pct_zigzag_threshold_and_alternation():
    bars, pts = setup_scenario()
    recs = mm.MarketMap(bars, mm.MapSettings(zz_mode="pct", zz_pct=8.0)).run()
    sw = recs[-1]["swings"]
    assert all(sw[i][2] != sw[i + 1][2] for i in range(len(sw) - 1))
    for a, b in zip(sw, sw[1:]):
        assert abs(b[1] / a[1] - 1) >= 0.08 * 0.999 or abs(a[1] / b[1] - 1) >= 0.08 * 0.999
    assert any(abs(s[0] - pts[1]) <= 2 and s[2] == 1 for s in sw)          # вершина импульса найдена


def test_leg_stats_match_direct_computation():
    bars, _ = setup_scenario()
    m = mm.MarketMap(bars, ST)
    m.run()
    # пересчёт напрямую по полуоткрытым отрезкам (prev; cur]
    st = mm.MapSettings(zz_len=20)
    eng = mm.MarketMap(bars, st)
    recs = eng.run()
    r = next(r for r in recs if r["cand"] == 2)
    legAB = r["legAB"]
    A, B = r["A"][0], r["B"][0]
    seg = eng.speed[A + 1:B + 1]
    assert legAB.mx == pytest.approx(max(seg)) and legAB.n == B - A
    assert legAB.vol == pytest.approx(sum(eng.vol[A + 1:B + 1]))


# --- M3: жёсткие правила ручной разметки --------------------------------------
def rules(prices, d, kind):
    return {name: (state, note) for name, state, note in mm.check_wave(prices, d, kind)["rules"]}


def test_impulse_rules_ok_and_violations():
    good = [100, 120, 110, 150, 135, 160]
    r = rules(good, 1, "impulse")
    assert all(v[0] == mm.OK for v in r.values())
    r = rules([100, 120, 110, 150, 118, 160], 1, "impulse")      # 4 зашла в зону 1
    assert r["4 не заходит в зону 1"][0] == mm.FAIL
    assert r["2 не за началом 1"][0] == mm.OK
    r = rules([100, 120, 98, 150, 135, 160], 1, "impulse")       # 2 за началом 1
    assert r["2 не за началом 1"][0] == mm.FAIL
    r = rules([100, 130, 120, 128, 124, 170], 1, "impulse")      # 3 самая короткая
    assert r["3 не самая короткая"][0] == mm.FAIL
    # нисходящий импульс — зеркально
    r = rules([160, 135, 150, 110, 120, 100], -1, "impulse")
    assert all(v[0] == mm.OK for v in r.values())


def test_impulse_partial_points():
    r = rules([100, 120, 110, 150, 135], 1, "impulse")
    assert r["3 не самая короткая"][0] == mm.NODATA


def test_diagonal_rules():
    contracting = [100, 130, 112, 135, 125, 138]                 # 1>3>5, 2>4, перекрытие
    res = mm.check_wave(contracting, 1, "diagonal")
    r = {n: s for n, s, _ in res["rules"]}
    assert all(v == mm.OK for v in r.values()) and res["ratios"]["subtype"] == "сужающаяся"
    expanding = [100, 110, 104, 120, 105, 140]
    res = mm.check_wave(expanding, 1, "diagonal")
    assert res["ratios"]["subtype"] == "расширяющаяся"
    no_overlap = [100, 130, 112, 135, 131, 138]                  # 4 не зашла в зону 1
    r = rules(no_overlap, 1, "diagonal")
    assert r["4 заходит в зону 1"][0] == mm.FAIL
    below2 = [100, 130, 112, 135, 110, 138]                      # 4 ниже конца 2
    assert rules(below2, 1, "diagonal")["4 не за вершиной 2"][0] == mm.FAIL


def test_zigzag_and_flat():
    r = rules([100, 80, 90, 75], -1, "zigzag")
    assert r["B не за началом A"][0] == mm.OK and r["C за концом A"][0] == mm.OK
    r = rules([100, 80, 90, 85], -1, "zigzag")
    assert r["C за концом A"][0] == mm.FAIL and "усечение" in r["C за концом A"][1]
    assert rules([100, 80, 102, 75], -1, "zigzag")["B не за началом A"][0] == mm.FAIL
    flat = mm.check_wave([100, 80, 99, 79], -1, "flat")
    assert flat["rules"][0][1] == mm.OK and flat["ratios"]["subtype"] == "регулярная"
    assert mm.check_wave([100, 80, 104, 76], -1, "flat")["ratios"]["subtype"] == "расширенная"
    assert mm.check_wave([100, 80, 104, 84], -1, "flat")["ratios"]["subtype"] == "бегущая"
    assert mm.check_wave([100, 80, 90, 79], -1, "flat")["rules"][0][1] == mm.FAIL   # B < 90 % A


def test_wave_ratios_log():
    res = mm.check_wave([100, 120, 110, 150, 135, 160], 1, "impulse")
    L1, L3 = math.log(1.2), math.log(150 / 110)
    assert res["ratios"]["3/1"] == pytest.approx(L3 / L1)


def test_wave_prices_by_direction():
    bars = [Bar(i, 10, 11 + i, 9 - i * 0.1, 10 + i, 1) for i in range(6)]
    prices, d = mm.wave_prices(bars, [0, 1, 2, 3, 4, 5], "impulse")
    assert d == 1 and prices[1] == bars[1].h and prices[2] == bars[2].l


# --- M4/M5/M7/M8/M9 ------------------------------------------------------------
def test_fib_and_targets_log_and_linear():
    lv = mm.fib_levels(100, 200, lg=False)
    assert lv[0.5] == pytest.approx(150) and lv[0.618] == pytest.approx(138.2)
    lv = mm.fib_levels(100, 200, lg=True)
    assert lv[0.5] == pytest.approx(math.sqrt(100 * 200))
    t = mm.targets(100, 200, 150, lg=True)
    assert t[1.0] == pytest.approx(300) and t[1.618] == pytest.approx(150 * 2 ** 1.618)
    assert mm.targets(100, 200, 150, lg=False)[1.0] == pytest.approx(250)
    assert mm.retracement(100, 200, 150, lg=False) == pytest.approx(0.5)


def test_channels_parallel():
    p1, p2, p3, p4 = (10, 120), (20, 110), (40, 150), (50, 135)
    ch = mm.channel_wave4(p1, p2, p3, p4)
    assert ch["base"](20) == pytest.approx(110) and ch["base"](50) == pytest.approx(135)
    assert ch["via3"](40) == pytest.approx(150)
    assert ch["via3"](60) - ch["base"](60) == pytest.approx(ch["via3"](40) - ch["base"](40))
    cb = mm.channel_base(p1, p2, p3)
    assert cb["l13"](40) == pytest.approx(150) and cb["par2"](20) == pytest.approx(110)
    assert cb["mid"](30) == pytest.approx((cb["l13"](30) + cb["par2"](30)) / 2)
    assert cb["double"](30) == pytest.approx(cb["par2"](30) - cb["width"])


def test_volume_profile_poc_and_value_area():
    bars = [Bar(i, 100, 101, 99, 100, 10.0) for i in range(10)] + [Bar(10, 105, 110, 104, 106, 1.0)]
    vp = mm.volume_profile(bars, 0, 10, n=11)
    assert 99 <= vp["poc_price"] <= 101
    assert vp["val"] <= 99.01 and vp["vah"] >= 100.99 and vp["vah"] < 104
    assert sum(vp["vols"]) == pytest.approx(101.0)


def test_anchored_vwap():
    bars = [Bar(i, 10, 12, 8, 10, 1.0) for i in range(3)] + [Bar(3, 20, 22, 18, 20, 3.0)]
    v = mm.anchored_vwap(bars, 2)
    assert math.isnan(v[1]) and v[2] == pytest.approx(10) and v[3] == pytest.approx((10 + 60) / 4)


def test_rule_100_140_and_tf_text():
    r = mm.rule_100_140(60, 3600)
    assert r["status"] == "мало" and r["tf"] == "30 мин"
    assert mm.rule_100_140(120, 3600)["status"] == "норма"
    r = mm.rule_100_140(300, 3600)
    assert r["status"] == "много" and r["tf"] == "2.5 ч"
    assert mm.rule_100_140(240, 86400)["tf"] == "2.0 д"


def test_position_size_and_rr():
    p = mm.position_size(10_000, 1.0, entry=100, stop=95)
    assert p["risk_money"] == pytest.approx(100) and p["qty"] == pytest.approx(20)
    assert mm.rr(100, 95, 115) == pytest.approx(3.0)


def test_stop_uses_atr_multiplier():
    bars, _ = setup_scenario()
    eng = mm.MarketMap(bars, ST)
    r = next(r for r in eng.run() if r["status"] == 3)
    assert r["stop"] == pytest.approx(r["C"][1] - eng.atr[r["bar"]] * 0.5)


# --- Pine-файл проходит линтер --------------------------------------------------
def test_market_map_pine_lint():
    import pine_lint
    path = os.path.join(ROOT, "pine", "tw_market_map.pine")
    if not os.path.exists(path):
        pytest.skip("pine/tw_market_map.pine ещё не создан")
    assert pine_lint.lint(path) == []


# --- research/component_value.py ---------------------------------------------
def test_research_stats_and_trade_logic():
    sys.path.insert(0, os.path.join(ROOT, "research"))
    import component_value as cv
    s = cv.stats([dict(R=2.0, hit=True), dict(R=-1.0, hit=False), dict(R=-1.0, hit=False)])
    assert s["n"] == 3 and s["avgR"] == pytest.approx(0.0) and s["pf"] == pytest.approx(1.0) and s["dd"] == pytest.approx(2.0)
    # сделка: стоп и цель в одном баре — считается стоп
    bars = [Bar(i, 100, 101, 99, 100, 1.0) for i in range(3)] + [Bar(3, 100, 130, 80, 100, 1.0)]
    ev = dict(bar=1, d=1, stop=90.0, targets={1.0: 120.0}, items=[1] * 9, status=3)
    t = cv.trade(bars, ev, commission=0.0, max_hold=10)
    assert t["hit"] is False and t["R"] == pytest.approx(-1.0)
    t = cv.trade(bars, ev, commission=0.1, max_hold=10)
    assert t["R"] < -1.0                                   # комиссия ухудшает R


def test_research_random_walk_has_no_edge():
    sys.path.insert(0, os.path.join(ROOT, "research"))
    import component_value as cv
    bars = cv.random_walk(3000, 7, 1640995200000, 4 * 3600_000)
    tr = cv.analyze([("rw", bars)], mm.MapSettings(), 1672531200000, 0.1, 200)
    s = cv.stats(tr)
    assert s["n"] > 30
    assert abs(s["avgR"]) < 0.5                            # на шуме нет устойчивого преимущества
