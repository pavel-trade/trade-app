#!/usr/bin/env python3
"""
Эталон расчётов «TW Карта рынка» (pine/tw_market_map.pine). Только стандартная библиотека.

Расчёт ведётся бар за баром, тем же конечным автоматом, что и в Pine (var-состояние,
без заглядывания вперёд), чтобы любой бар можно было сверить с TradingView.

Модули:
  M1  EMA 5/34 (лог), режим и слепые зоны — reference/speedometer.blind_zones
  M2  свинги: пивоты длины L или процентный ZigZag, чередование, статистика ног
  M3  проверка жёстких правил ручной разметки и лог-соотношения (check_wave)
  M4  авто-структура A -> B -> C, зоны Фибоначчи, цели, «первый триггер»
  M5  каналы Эллиотта (channel_wave4, channel_base)
  M6  Anchored VWAP (anchored_vwap)
  M7  профиль объёма фиксированного диапазона (volume_profile)
  M8  правило 100-140 (rule_100_140)
  M9  риск и размер позиции (position_size)
  M10 чек-лист «Зона -> Истощение -> Триггер» (MarketMap.run)

Это подсказка для анализа, а не торговая система.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, replace

try:
    from speedometer import (NA, Bar, ema, highest, isna, lowest, pivothigh, pivotlow,
                             rma, sma, time_to_bar)
except ImportError:  # pragma: no cover
    from .speedometer import (NA, Bar, ema, highest, isna, lowest, pivothigh, pivotlow,  # noqa: F401
                              rma, sma, time_to_bar)

OK, FAIL, NODATA = 1, 0, -1          # ✓ / ✗ / —
STATUS = ("Нет сетапа", "Зона", "Истощение", "Триггер")


# ---------------------------------------------------------------------------
# Настройки
# ---------------------------------------------------------------------------
@dataclass
class MapSettings:
    ema_log: bool = True        # EMA M1 от логарифма цены
    fast: int = 5
    slow: int = 34
    sig: int = 5
    zz_mode: str = "pivot"      # "pivot" | "pct"
    zz_len: int = 10
    zz_pct: float = 5.0
    minor_len: int = 3          # малые пивоты для «первого триггера» и конвергенции
    fib_log: bool = True        # зоны и цели в лог-шкале
    zone_lo: float = 0.382
    zone_hi: float = 0.786
    k_slow: float = 0.7         # коррекция медленнее импульса: |скорость B->C| < K x пик A->B
    conv_margin: float = 0.10   # конвергенция: скорость на C слабее экстремума скорости B->C на 10 %
    atr_len: int = 14
    atr_mult: float = 0.5
    max_swings: int = 50
    max_minor: int = 40


# ---------------------------------------------------------------------------
# Статистика ноги (полуоткрытый отрезок (начало; конец])
# ---------------------------------------------------------------------------
@dataclass
class Leg:
    mx: float = NA      # максимум скорости
    mn: float = NA      # минимум скорости
    vol: float = 0.0    # сумма объёма
    n: int = 0          # баров

    def add(self, spd: float, vol: float) -> None:
        if not isna(spd):
            self.mx = spd if isna(self.mx) else max(self.mx, spd)
            self.mn = spd if isna(self.mn) else min(self.mn, spd)
        self.vol += 0.0 if isna(vol) else vol
        self.n += 1

    def merged(self, o: "Leg") -> "Leg":
        def cmb(a, b, fn):
            return b if isna(a) else a if isna(b) else fn(a, b)
        return Leg(cmb(self.mx, o.mx, max), cmb(self.mn, o.mn, min), self.vol + o.vol, self.n + o.n)

    def copy(self) -> "Leg":
        return replace(self)


@dataclass
class Point:
    bar: int
    price: float
    dir: int            # 1 — максимум, -1 — минимум
    speed: float        # скорость на баре точки
    pv0: float          # сумма hlc3*vol ДО бара точки (для AVWAP от точки)
    v0: float           # сумма vol ДО бара точки
    leg: Leg = field(default_factory=Leg)   # нога, закончившаяся в этой точке


# ---------------------------------------------------------------------------
# Вспомогательные расчёты
# ---------------------------------------------------------------------------
def fwd(x: float, lg: bool) -> float:
    return math.log(x) if lg else x


def inv(y: float, lg: bool) -> float:
    return math.exp(y) if lg else y


def fib_levels(a: float, b: float, lg: bool = True, ratios=(0.382, 0.5, 0.618, 0.786)) -> dict:
    """Уровни коррекции ноги a -> b: цена, где откат = ratio."""
    fa, fb = fwd(a, lg), fwd(b, lg)
    return {r: inv(fb - r * (fb - fa), lg) for r in ratios}


def targets(a: float, b: float, c: float, lg: bool = True, ks=(1.0, 1.618, 2.618)) -> dict:
    """Цели волны 3: C + k x (B - A) (в лог-шкале — умножение на (B/A)^k)."""
    fa, fb, fc = fwd(a, lg), fwd(b, lg), fwd(c, lg)
    return {k: inv(fc + k * (fb - fa), lg) for k in ks}


def retracement(a: float, b: float, c: float, lg: bool = True) -> float:
    fa, fb, fc = fwd(a, lg), fwd(b, lg), fwd(c, lg)
    return NA if fb == fa else (fb - fc) / (fb - fa)


def atr(bars: list[Bar], length: int = 14) -> list[float]:
    """ta.atr: RMA истинного диапазона; на первом баре TR = high - low."""
    tr = []
    for i, b in enumerate(bars):
        if i == 0:
            tr.append(b.h - b.l)
        else:
            c1 = bars[i - 1].c
            tr.append(max(b.h - b.l, abs(b.h - c1), abs(b.l - c1)))
    return rma(tr, length)


def cum_pv(bars: list[Bar]):
    """Накопленные суммы hlc3*vol и vol (на баре i — включая бар i)."""
    pv, v, s1, s2 = [], [], 0.0, 0.0
    for b in bars:
        vv = 0.0 if isna(b.v) else b.v
        s1 += (b.h + b.l + b.c) / 3.0 * vv
        s2 += vv
        pv.append(s1)
        v.append(s2)
    return pv, v


def anchored_vwap(bars: list[Bar], anchor: int) -> list[float]:
    """VWAP по hlc3 от бара anchor (включительно). До якоря и без объёма — NA."""
    out, s1, s2 = [], 0.0, 0.0
    for i, b in enumerate(bars):
        if i < anchor:
            out.append(NA)
            continue
        vv = 0.0 if isna(b.v) else b.v
        s1 += (b.h + b.l + b.c) / 3.0 * vv
        s2 += vv
        out.append(s1 / s2 if s2 > 0 else NA)
    return out


def has_volume(bars: list[Bar]) -> bool:
    return any((not isna(b.v)) and b.v > 0 for b in bars)


def volume_profile(bars: list[Bar], a: int, b: int, n: int = 40, va_share: float = 0.70) -> dict | None:
    """Профиль объёма бары a..b включительно, n уровней. Объём бара делится между уровнями
    пропорционально пересечению [low; high] с уровнем (бар без диапазона — целиком в свой уровень).
    Value Area — расширение от POC в сторону большего соседнего объёма (при равенстве — вверх)."""
    seg = bars[a:b + 1]
    if not seg or not has_volume(seg):
        return None
    lo, hi = min(x.l for x in seg), max(x.h for x in seg)
    if hi <= lo:
        return None
    step = (hi - lo) / n
    vols = [0.0] * n
    for x in seg:
        vv = 0.0 if isna(x.v) else x.v
        if vv == 0:
            continue
        if x.h <= x.l:
            k = min(int((x.l - lo) / step), n - 1)
            vols[k] += vv
            continue
        k0 = max(0, min(int((x.l - lo) / step), n - 1))
        k1 = max(0, min(int((x.h - lo) / step), n - 1))
        for k in range(k0, k1 + 1):
            bot, top = lo + k * step, lo + (k + 1) * step
            ov = min(x.h, top) - max(x.l, bot)
            if ov > 0:
                vols[k] += vv * ov / (x.h - x.l)
    total = sum(vols)
    poc = max(range(n), key=lambda k: (vols[k], -k))   # первый максимум
    inc, up, dn, s = {poc}, poc + 1, poc - 1, vols[poc]
    while s < va_share * total and (up < n or dn >= 0):
        vu = vols[up] if up < n else -1.0
        vd = vols[dn] if dn >= 0 else -1.0
        if vu >= vd:
            inc.add(up)
            s += vu
            up += 1
        else:
            inc.add(dn)
            s += vd
            dn -= 1
    return dict(lo=lo, hi=hi, step=step, vols=vols, poc=poc,
                poc_price=lo + (poc + 0.5) * step,
                vah=lo + (max(inc) + 1) * step, val=lo + min(inc) * step)


def tf_text(sec: float) -> str:
    if sec < 3600:
        return f"{sec / 60:.0f} мин"
    if sec < 86400:
        return f"{sec / 3600:.1f} ч"
    return f"{sec / 86400:.1f} д"


def rule_100_140(n_bars: int, tf_sec: int) -> dict:
    status = "мало" if n_bars < 100 else "много" if n_bars > 140 else "норма"
    sec = tf_sec * n_bars / 120.0
    return dict(n=n_bars, status=status, tf_sec=sec, tf=tf_text(sec))


def position_size(deposit: float, risk_pct: float, entry: float, stop: float) -> dict:
    risk_money = deposit * risk_pct / 100.0
    per_unit = abs(entry - stop)
    return dict(risk_money=risk_money, qty=risk_money / per_unit if per_unit > 0 else NA)


def rr(entry: float, stop: float, target: float) -> float:
    risk = abs(entry - stop)
    return NA if risk == 0 else (target - entry) / (entry - stop)


# ---------------------------------------------------------------------------
# M5. Каналы (точки — (бар, цена))
# ---------------------------------------------------------------------------
def line_at(p1, p2, x):
    (x1, y1), (x2, y2) = p1, p2
    return y1 + (y2 - y1) * (x - x1) / (x2 - x1)


def channel_wave4(p1, p2, p3, p4):
    """Линия 2-4 и параллели через 3 (цель волны 5) и через 1. Возвращает функции цены от бара."""
    slope = (p4[1] - p2[1]) / (p4[0] - p2[0])
    base = lambda x: p2[1] + slope * (x - p2[0])                  # noqa: E731
    via3 = lambda x: p3[1] + slope * (x - p3[0])                  # noqa: E731
    via1 = lambda x: p1[1] + slope * (x - p1[0])                  # noqa: E731
    return dict(base=base, via3=via3, via1=via1, slope=slope)


def channel_base(p1, p2, p3):
    """«Норма о каналах»: линия 1-3, параллель через 2, средняя линия (мин. цель волны 4)
    и удвоенный канал (граница)."""
    slope = (p3[1] - p1[1]) / (p3[0] - p1[0])
    l13 = lambda x: p1[1] + slope * (x - p1[0])                   # noqa: E731
    par2 = lambda x: p2[1] + slope * (x - p2[0])                  # noqa: E731
    width = l13(p2[0]) - p2[1]
    mid = lambda x: l13(x) - width / 2.0                          # noqa: E731
    dbl = lambda x: par2(x) - width                               # noqa: E731
    return dict(l13=l13, par2=par2, mid=mid, double=dbl, slope=slope, width=width)


# ---------------------------------------------------------------------------
# M3. Проверка жёстких правил ручной разметки
# ---------------------------------------------------------------------------
def wave_prices(bars: list[Bar], pts: list[int], kind: str) -> tuple[list[float], int]:
    """Цена точки — экстремум бара по направлению. Направление — по close первой и последней точки.
    Импульс/диагональ (0..5): у восходящей 0,2,4 — low, 1,3,5 — high. Коррекция 0,A,B,C:
    у нисходящей (A ниже 0) 0 и B — high, A и C — low."""
    d = 1 if bars[pts[-1]].c > bars[pts[0]].c else -1
    if kind in ("zigzag", "flat"):
        d = 1 if bars[pts[1]].c > bars[pts[0]].c else -1
    out = []
    for i, p in enumerate(pts):
        up_pt = (i % 2 == 1) == (d == 1)
        out.append(bars[p].h if up_pt else bars[p].l)
    return out, d


def _llen(a, b):
    return abs(math.log(b) - math.log(a))


def check_wave(prices: list[float], d: int, kind: str) -> dict:
    """Жёсткие правила. prices — цены точек 0..5 (импульс/диагональ) или 0,A,B,C.
    Возвращает dict(rules=[(название, OK/FAIL/NODATA, пояснение)], ratios={...}, subtype=...)."""
    s = d                       # знак: для нисходящей разметки сравнения зеркальны
    P = prices
    n = len(P)
    rules, ratios = [], {}
    L = [_llen(P[i], P[i + 1]) for i in range(n - 1)]

    def st(cond):
        return OK if cond else FAIL

    if kind in ("impulse", "diagonal"):
        if n >= 3:
            rules.append(("2 не за началом 1", st(s * (P[2] - P[0]) > 0), "минимум 2 выше начала 1" if s == 1 else "максимум 2 ниже начала 1"))
        if kind == "impulse":
            if n >= 6:
                rules.append(("3 не самая короткая", st(not (L[2] < L[0] and L[2] < L[4])), "сравнение длин 1, 3, 5 (лог)"))
            else:
                rules.append(("3 не самая короткая", NODATA, "нужна точка 5"))
            if n >= 5:
                rules.append(("4 не заходит в зону 1", st(s * (P[4] - P[1]) > 0), "4 не перекрывает вершину 1"))
        else:
            if n >= 5:
                rules.append(("4 заходит в зону 1", st(s * (P[4] - P[1]) < 0), "у диагонали перекрытие обязательно"))
                rules.append(("4 не за вершиной 2", st(s * (P[4] - P[2]) > 0), "4 не уходит за конец 2"))
            sub = "—"
            if n >= 6:
                if L[0] > L[2] > L[4] and L[1] > L[3]:
                    sub = "сужающаяся"
                elif L[0] < L[2] < L[4] and L[1] < L[3]:
                    sub = "расширяющаяся"
                else:
                    sub = "не сужается и не расширяется"
                rules.append(("форма: 1>3>5 и 2>4 или 1<3<5 и 2<4", st(sub in ("сужающаяся", "расширяющаяся")), sub))
            ratios["subtype"] = sub
        if n >= 4:
            ratios["3/1"] = L[2] / L[0]
            ratios["глубина 2"] = L[1] / L[0]
        if n >= 5:
            ratios["глубина 4"] = L[3] / L[2]
        if n >= 6:
            ratios["5/1"] = L[4] / L[0]
            ratios["5/3"] = L[4] / L[2]
    elif kind == "zigzag":
        if n >= 3:
            rules.append(("B не за началом A", st(s * (P[2] - P[0]) > 0), "откат B меньше 100 % A"))
        if n >= 4:
            ok = s * (P[3] - P[1]) > 0
            rules.append(("C за концом A", st(ok), "C обновил конец A" if ok else "усечение: C не дошёл до конца A"))
    elif kind == "flat":
        if n >= 3:
            rb = L[1] / L[0]
            rules.append(("B >= 90 % A", st(rb >= 0.9), f"B = {rb * 100:.0f} % A"))
            sub = "—"
            if n >= 4:
                c_beyond = s * (P[3] - P[1]) > 0
                if rb <= 1.05:
                    sub = "регулярная"
                elif c_beyond:
                    sub = "расширенная"
                else:
                    sub = "бегущая"
            ratios["subtype"] = sub
    if kind in ("zigzag", "flat") and n >= 3:
        ratios["B/A"] = L[1] / L[0]
        if n >= 4:
            ratios["C/A"] = L[2] / L[0]
    return dict(rules=rules, ratios=ratios)


# ---------------------------------------------------------------------------
# M2 + M4 + M10: бар за баром
# ---------------------------------------------------------------------------
class MarketMap:
    """Конечный автомат «TW Карта рынка». run() возвращает список записей по барам."""

    def __init__(self, bars: list[Bar], st: MapSettings | None = None):
        self.b = bars
        self.st = st or MapSettings()
        s = self.st
        lc = [100.0 * math.log(x.c) for x in bars]
        ef, es = ema(lc, s.fast), ema(lc, s.slow)
        self.speed = [a - b for a, b in zip(ef, es)]
        e = ema(self.speed, s.sig)
        self.accel = [a - b for a, b in zip(self.speed, e)]
        self.atr = atr(bars, s.atr_len)
        self.sma200 = sma([x.c for x in bars], 200)
        self.cpv, self.cv = cum_pv(bars)
        self.vol = [0.0 if isna(x.v) else x.v for x in bars]
        L, m = s.zz_len, s.minor_len
        self.ph = pivothigh([x.h for x in bars], L, L)
        self.pl = pivotlow([x.l for x in bars], L, L)
        self.mph = pivothigh([x.h for x in bars], m, m)
        self.mpl = pivotlow([x.l for x in bars], m, m)
        self.spd_hi_L = highest(self.speed, L)
        self.spd_lo_L = lowest(self.speed, L)

    # -- точки
    def _pt(self, bar, price, d, leg=None) -> Point:
        return Point(bar, price, d, self.speed[bar],
                     self.cpv[bar - 1] if bar > 0 else 0.0,
                     self.cv[bar - 1] if bar > 0 else 0.0, leg or Leg())

    def run(self) -> list[dict]:
        s, bars = self.st, self.b
        L = s.zz_len
        swings: list[Point] = []
        minors_hi: list[Point] = []
        minors_lo: list[Point] = []
        acc = Leg()                 # pivot: лаг L баров; pct: нога (последний свинг; сейчас]
        run: Point | None = None    # текущий экстремум против последнего свинга (незавершённый)
        # pct-режим
        zdir, snap, since = 1, Leg(), Leg()
        cand: Point | None = None
        last_cross, prev_status = -1, 0
        has_vol = False
        out = []
        for i, b in enumerate(bars):
            new_swing = False
            if (not isna(b.v)) and b.v > 0:
                has_vol = True
            sp = self.speed[i]
            if i > 0 and not isna(sp) and not isna(self.speed[i - 1]):
                p0 = self.speed[i - 1]
                if (sp > 0 >= p0) or (sp < 0 <= p0):
                    last_cross = i

            if s.zz_mode == "pivot":
                if i >= L:
                    acc.add(self.speed[i - L], self.vol[i - L])
                # обновление текущего экстремума текущим баром
                if run is not None and swings:
                    if swings[-1].dir == 1 and b.l < run.price:
                        run = self._pt(i, b.l, -1)
                    elif swings[-1].dir == -1 and b.h > run.price:
                        run = self._pt(i, b.h, 1)
                order = (-1, 1) if (swings and swings[-1].dir == 1) else (1, -1)
                for d in order:
                    pv = self.ph[i] if d == 1 else self.pl[i]
                    if isna(pv):
                        continue
                    p = i - L
                    if swings and swings[-1].dir == d:
                        last = swings[-1]
                        if not (pv > last.price if d == 1 else pv < last.price):
                            continue
                        leg = last.leg.merged(acc)
                        swings[-1] = self._pt(p, pv, d, leg)
                    else:
                        swings.append(self._pt(p, pv, d, acc.copy()))
                        if len(swings) > s.max_swings:
                            swings.pop(0)
                    acc = Leg()
                    new_swing = True
                    # экстремум против нового свинга на (p; i]
                    win = range(p + 1, i + 1)
                    if d == 1:
                        k = min(win, key=lambda j: (bars[j].l, j))
                        run = self._pt(k, bars[k].l, -1)
                    else:
                        k = max(win, key=lambda j: (bars[j].h, -j))
                        run = self._pt(k, bars[k].h, 1)
                if swings:
                    rl = acc.copy()
                    lo_i = max(0, i - L + 1)
                    for j in range(lo_i, i + 1):
                        rl.add(self.speed[j], self.vol[j])
                    run_leg = rl
                else:
                    run_leg = None
            else:  # процентный ZigZag
                acc.add(sp, self.vol[i])
                since.add(sp, self.vol[i])
                thr = s.zz_pct / 100.0
                if cand is None:
                    cand = self._pt(i, b.h, 1)
                    snap, since = acc.copy(), Leg()
                elif zdir == 1:
                    if b.h > cand.price:
                        cand = self._pt(i, b.h, 1)
                        snap, since = acc.copy(), Leg()
                    elif b.l <= cand.price * (1 - thr):
                        cand.leg = snap
                        swings.append(cand)
                        new_swing = True
                        acc = since
                        zdir = -1
                        cand = self._pt(i, b.l, -1)
                        snap, since = acc.copy(), Leg()
                else:
                    if b.l < cand.price:
                        cand = self._pt(i, b.l, -1)
                        snap, since = acc.copy(), Leg()
                    elif b.h >= cand.price * (1 + thr):
                        cand.leg = snap
                        swings.append(cand)
                        new_swing = True
                        acc = since
                        zdir = 1
                        cand = self._pt(i, b.h, 1)
                        snap, since = acc.copy(), Leg()
                if len(swings) > s.max_swings:
                    swings.pop(0)
                run = cand if swings else None
                run_leg = acc.copy() if swings else None

            # малые пивоты
            m = s.minor_len
            if not isna(self.mph[i]):
                minors_hi.append(self._pt(i - m, self.mph[i], 1))
                if len(minors_hi) > s.max_minor:
                    minors_hi.pop(0)
            if not isna(self.mpl[i]):
                minors_lo.append(self._pt(i - m, self.mpl[i], -1))
                if len(minors_lo) > s.max_minor:
                    minors_lo.pop(0)

            rec = self._evaluate(i, swings, run, run_leg, minors_hi, minors_lo, last_cross, has_vol)
            rec["new_swing"] = new_swing
            rec["trigger_event"] = rec["status"] == 3 and prev_status < 3
            prev_status = rec["status"]
            rec["swings"] = [(p.bar, p.price, p.dir) for p in swings[-6:]]
            out.append(rec)
        return out

    def _evaluate(self, i, swings, run, run_leg, mhi, mlo, last_cross, has_vol) -> dict:
        s, b, lg = self.st, self.b[i], self.st.fib_log
        rec = dict(bar=i, status=0, score=0, items=[NODATA] * 9, reason="нет структуры",
                   A=None, B=None, C=None, d=0, ret=NA, trigger=NA, cand=0)
        A = B = C = None
        legAB = legBC = None
        cand = 0
        if len(swings) >= 3:
            s3, s2, s1 = swings[-3], swings[-2], swings[-1]
            r = retracement(s3.price, s2.price, s1.price, lg)
            if not isna(r) and 0 < r < 1:
                A, B, C, legAB, legBC, cand = s3, s2, s1, s2.leg, s1.leg, 2
        if cand == 0 and len(swings) >= 2 and run is not None:
            A, B, C, legAB, legBC, cand = swings[-2], swings[-1], run, swings[-1].leg, run_leg, 1
        if cand == 0:
            return rec
        d = 1 if B.price > A.price else -1
        r = retracement(A.price, B.price, C.price, lg)
        it = [NODATA] * 9
        # 1. зона
        it[0] = OK if (not isna(r) and s.zone_lo <= r <= s.zone_hi and r < 1) else FAIL
        # 2. коррекция медленнее импульса
        imp = legAB.mx if d == 1 else (NA if isna(legAB.mn) else -legAB.mn)
        cor = (NA if isna(legBC.mn) else -legBC.mn) if d == 1 else legBC.mx
        if not isna(imp) and not isna(cor):
            it[1] = OK if (imp > 0 and max(cor, 0.0) < s.k_slow * imp) else FAIL
        # 3. конвергенция на последнем экстремуме коррекции: цена дошла до нового экстремума C,
        #    а экстремум скорости коррекции остался раньше — на C скорость слабее его на conv_margin
        ext = legBC.mn if d == 1 else legBC.mx
        if not isna(ext) and not isna(C.speed) and d * ext < 0:
            it[2] = OK if d * C.speed > d * ext + s.conv_margin * abs(ext) else FAIL
        # 4. объём коррекции ниже импульса
        if has_vol and legAB.n > 0 and legBC.n > 0:
            it[3] = OK if legBC.vol / legBC.n < legAB.vol / legAB.n else FAIL
        # 5. слом структуры: закрытие за «первым триггером»
        tpool = mhi if d == 1 else mlo
        tr = [p for p in tpool if B.bar < p.bar < C.bar]
        trig = tr[-1].price if tr else NA
        if not isna(trig):
            it[4] = OK if (i > C.bar and d * (b.c - trig) > 0) else FAIL
        # 6. ускорение по направлению
        if not isna(self.accel[i]):
            it[5] = OK if d * self.accel[i] > 0 else FAIL
        # 7. конец слепой зоны по направлению (пересечение EMA 5/34 после C)
        if not isna(self.speed[i]):
            it[6] = OK if (d * self.speed[i] > 0 and last_cross > C.bar) else FAIL
        # 8. цена по нужную сторону AVWAP от C
        avwap = NA
        dv = self.cv[i] - C.v0
        if has_vol and dv > 0:
            avwap = (self.cpv[i] - C.pv0) / dv
            it[7] = OK if d * (b.c - avwap) > 0 else FAIL
        # 9. фильтр SMA 200
        if not isna(self.sma200[i]):
            it[8] = OK if d * (b.c - self.sma200[i]) > 0 else FAIL

        zone = it[0] == OK and it[1] == OK
        exh = zone and it[2] == OK and it[3] != FAIL
        trg = exh and it[4] == OK
        status = 3 if trg else 2 if exh else 1 if zone else 0
        reason = ""
        if cand == 1 and not isna(r) and r >= 1:
            status, reason = 0, "коррекция ушла за A — отмена"
        elif cand == 2 and d * (b.c - C.price) < 0:
            status, reason = 0, "цена за C — стоп идеи"
        tg = targets(A.price, B.price, C.price, lg)
        stop = C.price - d * self.atr[i] * s.atr_mult if not isna(self.atr[i]) else NA
        rec.update(status=status, score=sum(1 for x in it[:8] if x == OK), items=it, reason=reason,
                   A=(A.bar, A.price), B=(B.bar, B.price), C=(C.bar, C.price), d=d, ret=r,
                   trigger=trig, cand=cand, targets=tg, stop=stop, avwap_c=avwap,
                   fib=fib_levels(A.price, B.price, lg), legAB=legAB, legBC=legBC)
        return rec
