#!/usr/bin/env python3
"""
Эталонная реализация «TW Спидометр Pro» (Python, только стандартная библиотека).

Повторяет вычисления из pine/tw_speedometer_pro.pine и pine/tw_blind_zone_pro.pine,
чтобы значения можно было сверить со значениями на TradingView:

  * EMA с seed = первое значение источника (формула pine_ema из справки Pine);
  * SMA, RMA (seed = SMA, как ta.rma);
  * источник = 100 * ln(цена) (опционально — цена как есть);
  * линейный MACD = EMA(fast) - EMA(slow), сигнальная = EMA(MACD, signal),
    гистограмма = MACD - сигнальная;
  * AO = SMA(5) - SMA(34) от 100 * ln(hl2);
  * нормировка в единицах ATR (MACD / ATR в тех же единицах);
  * пивоты как ta.pivothigh / ta.pivotlow;
  * дивергенции (обычные, скрытые, «три пика»);
  * слепые зоны (от экстремума режима до пересечения EMA fast/slow);
  * анализ волны: правило 100-140, пики по отрезкам, профиль, объём, соотношения.

Запуск на любом CSV (например, экспорт графика TradingView):

  python reference/speedometer.py data.csv --tail 5
  python reference/speedometer.py data.csv --at 2024-03-15
  python reference/speedometer.py data.csv --divs --zones
  python reference/speedometer.py data.csv --wave 2024-01-10,2024-02-01,...

Это инструмент проверки разметки, а не торговая система.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import math
import sys
from dataclasses import dataclass, field

NA = float("nan")


def isna(x) -> bool:
    return x is None or (isinstance(x, float) and math.isnan(x))


# ---------------------------------------------------------------------------
# Данные
# ---------------------------------------------------------------------------
@dataclass
class Bar:
    t: int          # время открытия бара, мс Unix UTC
    o: float
    h: float
    l: float
    c: float
    v: float = NA   # объём; NA, если недоступен


def _parse_time(raw: str) -> int:
    raw = raw.strip()
    try:
        x = float(raw)
        # секунды или миллисекунды
        return int(x if x > 1e11 else x * 1000)
    except ValueError:
        pass
    s = raw.replace("Z", "+00:00")
    try:
        d = dt.datetime.fromisoformat(s)
    except ValueError:
        d = dt.datetime.strptime(raw[:10], "%Y-%m-%d")
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return int(d.timestamp() * 1000)


def load_csv(path: str) -> list[Bar]:
    """Читает CSV с колонками time/date, open, high, low, close[, volume].

    Регистр и порядок колонок не важны. Время — Unix (с или мс) или ISO-строка.
    Подходит для экспорта TradingView («Экспорт данных графика»).
    """
    with open(path, newline="", encoding="utf-8-sig") as f:
        rd = csv.DictReader(f)
        cols = {c.lower().strip(): c for c in rd.fieldnames or []}

        def pick(*names):
            for n in names:
                if n in cols:
                    return cols[n]
            return None

        ct = pick("time", "date", "datetime", "timestamp")
        co, ch, cl, cc = pick("open"), pick("high"), pick("low"), pick("close")
        cv = pick("volume", "vol")
        if not all([ct, co, ch, cl, cc]):
            raise ValueError("нужны колонки time, open, high, low, close")
        bars = []
        for row in rd:
            if not row.get(cc):
                continue
            v = NA
            if cv and row.get(cv) not in (None, "", "NaN", "nan"):
                v = float(row[cv])
            bars.append(Bar(_parse_time(row[ct]), float(row[co]), float(row[ch]),
                            float(row[cl]), float(row[cc]), v))
    bars.sort(key=lambda b: b.t)
    return bars


def save_csv(path: str, bars: list[Bar]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["time", "open", "high", "low", "close", "volume"])
        for b in bars:
            iso = dt.datetime.fromtimestamp(b.t / 1000, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            w.writerow([iso, f"{b.o:.6f}", f"{b.h:.6f}", f"{b.l:.6f}", f"{b.c:.6f}",
                        "" if isna(b.v) else f"{b.v:.0f}"])


def tf_seconds(bars: list[Bar]) -> int:
    """Таймфрейм по медиане разницы времени соседних баров."""
    if len(bars) < 2:
        return 86400
    d = sorted(bars[i + 1].t - bars[i].t for i in range(len(bars) - 1))
    return int(d[len(d) // 2] / 1000)


# ---------------------------------------------------------------------------
# Примитивы, повторяющие ta.* в Pine
# ---------------------------------------------------------------------------
def ema(src, length):
    """pine_ema: sum := na(sum[1]) ? src : alpha*src + (1-alpha)*sum[1]."""
    alpha = 2.0 / (length + 1)
    out, prev = [], NA
    for x in src:
        if isna(x):
            cur = NA
        elif isna(prev):
            cur = x
        else:
            cur = alpha * x + (1 - alpha) * prev
        out.append(cur)
        prev = cur
    return out


def sma(src, length):
    out = []
    for i in range(len(src)):
        if i < length - 1:
            out.append(NA)
            continue
        w = src[i - length + 1:i + 1]
        out.append(NA if any(isna(v) for v in w) else sum(w) / length)
    return out


def rma(src, length):
    """ta.rma: seed = SMA(length), далее alpha = 1/length."""
    alpha = 1.0 / length
    s = sma(src, length)
    out, prev = [], NA
    for i, x in enumerate(src):
        if isna(prev):
            cur = s[i]
        else:
            cur = NA if isna(x) else alpha * x + (1 - alpha) * prev
        out.append(cur)
        prev = cur
    return out


def _window_extreme(src, length, fn):
    out = []
    for i in range(len(src)):
        if i < length - 1:
            out.append(NA)
            continue
        w = [v for v in src[i - length + 1:i + 1] if not isna(v)]
        out.append(fn(w) if w else NA)
    return out


def highest(src, length):
    return _window_extreme(src, length, max)


def lowest(src, length):
    return _window_extreme(src, length, min)


def pivothigh(src, left, right):
    """Значение на баре подтверждения (pivot + right), иначе NA.

    Центр строго больше всех левых баров и не меньше правых
    (из «плато» берётся самый левый бар).
    """
    out = [NA] * len(src)
    for i in range(left + right, len(src)):
        p = i - right
        c = src[p]
        win = src[p - left:i + 1]
        if isna(c) or any(isna(v) for v in win):
            continue
        if all(c > src[j] for j in range(p - left, p)) and all(c >= src[j] for j in range(p + 1, i + 1)):
            out[i] = c
    return out


def pivotlow(src, left, right):
    out = [NA] * len(src)
    for i in range(left + right, len(src)):
        p = i - right
        c = src[p]
        win = src[p - left:i + 1]
        if isna(c) or any(isna(v) for v in win):
            continue
        if all(c < src[j] for j in range(p - left, p)) and all(c <= src[j] for j in range(p + 1, i + 1)):
            out[i] = c
    return out


# ---------------------------------------------------------------------------
# Ядро спидометра
# ---------------------------------------------------------------------------
PRESETS = {
    "williams": (5, 34, 5),     # «Williams 5/34/5»
    "big": (13, 55, 13),        # «Крупная фигура 13/55/13»
    "classic": (12, 26, 9),     # «Классика 12/26/9»
}


@dataclass
class Settings:
    fast: int = 5
    slow: int = 34
    signal: int = 5
    use_log: bool = True
    norm: str = "pct"       # "pct" — % от логарифма (или цена), "atr" — единицы ATR
    atr_len: int = 14
    ao_fast: int = 5
    ao_slow: int = 34

    @classmethod
    def preset(cls, name: str, **kw) -> "Settings":
        f, s, g = PRESETS[name]
        return cls(fast=f, slow=s, signal=g, **kw)


def transform(x: float, use_log: bool) -> float:
    return 100.0 * math.log(x) if use_log else x


def compute(bars: list[Bar], st: Settings | None = None) -> dict:
    """Все ряды спидометра. Ключи: src, ema_fast, ema_slow, macd_raw, atr,
    macd, signal, hist, ao."""
    st = st or Settings()
    src = [transform(b.c, st.use_log) for b in bars]
    hi = [transform(b.h, st.use_log) for b in bars]
    lo = [transform(b.l, st.use_log) for b in bars]
    med = [transform((b.h + b.l) / 2.0, st.use_log) for b in bars]

    ef, es = ema(src, st.fast), ema(src, st.slow)
    macd_raw = [a - b for a, b in zip(ef, es)]

    tr = []
    for i in range(len(bars)):
        if i == 0:
            tr.append(hi[i] - lo[i])
        else:
            c1 = src[i - 1]
            tr.append(max(hi[i] - lo[i], abs(hi[i] - c1), abs(lo[i] - c1)))
    atr = rma(tr, st.atr_len)

    ao_raw = [a - b for a, b in zip(sma(med, st.ao_fast), sma(med, st.ao_slow))]

    if st.norm == "atr":
        def nrm(x, a):
            return NA if isna(x) or isna(a) or a == 0 else x / a
        macd = [nrm(x, a) for x, a in zip(macd_raw, atr)]
        ao = [nrm(x, a) for x, a in zip(ao_raw, atr)]
    else:
        macd, ao = list(macd_raw), ao_raw

    sig = ema(macd, st.signal)
    hist = [NA if isna(m) or isna(s) else m - s for m, s in zip(macd, sig)]
    return dict(src=src, ema_fast=ef, ema_slow=es, macd_raw=macd_raw, atr=atr,
                macd=macd, signal=sig, hist=hist, ao=ao)


def phase(cur: float, prev: float) -> str:
    """Фаза скорости, как в таблице MTF."""
    if isna(cur) or isna(prev):
        return "нет данных"
    if cur >= 0:
        return "рост ускоряется" if cur > prev else "рост замедляется"
    return "спад ускоряется" if cur < prev else "спад замедляется"


# ---------------------------------------------------------------------------
# Дивергенции
# ---------------------------------------------------------------------------
@dataclass
class DivSettings:
    source: str = "macd"        # "macd" | "hist" | "ao"
    left: int = 5
    right: int = 5
    min_dist: int = 5
    max_dist: int = 80
    price_win: int = 2          # окно поиска экстремума цены вокруг пивота, баров в каждую сторону
    zero_filter: bool = True    # только для обычных: бычьи ниже нуля, медвежьи выше
    min_diff_pct: float = 5.0   # мин. разница значений, % от диапазона индикатора
    range_len: int = 200
    regular: bool = True
    hidden: bool = True
    pairs: bool = True
    three_peaks: bool = False


@dataclass
class Divergence:
    kind: str           # bull_reg, bull_hid, bear_reg, bear_hid, three_fade, three_grow
    side: str           # "low" | "high"
    confirm_bar: int    # бар, на котором сигнал появился (pivot + right)
    pivot_bar: int
    prev_bar: int
    osc: float
    prev_osc: float
    price: float
    prev_price: float
    bars: tuple = ()    # для «трёх пиков» — три бара пивотов


def find_divergences(bars: list[Bar], series: dict, ds: DivSettings | None = None) -> list[Divergence]:
    ds = ds or DivSettings()
    osc = series[ds.source]
    n = len(bars)
    ph = pivothigh(osc, ds.left, ds.right)
    pl = pivotlow(osc, ds.left, ds.right)
    w = min(ds.price_win, ds.right)
    lows = lowest([b.l for b in bars], 2 * w + 1)
    highs = highest([b.h for b in bars], 2 * w + 1)
    rng = [NA if isna(a) or isna(b) else a - b
           for a, b in zip(highest(osc, ds.range_len), lowest(osc, ds.range_len))]

    out: list[Divergence] = []
    # состояние, как var в Pine: последние три пивота каждого типа
    hist_lo: list[tuple] = []   # (bar, osc, price)
    hist_hi: list[tuple] = []

    for i in range(n):
        for side in ("low", "high"):
            pv = pl[i] if side == "low" else ph[i]
            if isna(pv):
                continue
            k = i - (ds.right - w)
            px = (lows if side == "low" else highs)[k] if k >= 0 else NA
            cur = (i - ds.right, pv, px)
            store = hist_lo if side == "low" else hist_hi
            if store and ds.pairs and not isna(px):
                pb, po, pp = store[-1]
                dist = cur[0] - pb
                dist_ok = ds.min_dist <= dist <= ds.max_dist
                diff_ok = (not isna(rng[i])) and abs(pv - po) >= ds.min_diff_pct / 100.0 * rng[i]
                if dist_ok and diff_ok:
                    if side == "low":
                        zero_ok = (not ds.zero_filter) or (pv < 0 and po < 0)
                        if ds.regular and zero_ok and px < pp and pv > po:
                            out.append(Divergence("bull_reg", side, i, cur[0], pb, pv, po, px, pp))
                        if ds.hidden and px > pp and pv < po:
                            out.append(Divergence("bull_hid", side, i, cur[0], pb, pv, po, px, pp))
                    else:
                        zero_ok = (not ds.zero_filter) or (pv > 0 and po > 0)
                        if ds.regular and zero_ok and px > pp and pv < po:
                            out.append(Divergence("bear_reg", side, i, cur[0], pb, pv, po, px, pp))
                        if ds.hidden and px < pp and pv > po:
                            out.append(Divergence("bear_hid", side, i, cur[0], pb, pv, po, px, pp))
            store.append(cur)
            if len(store) > 3:
                store.pop(0)
            if ds.three_peaks and len(store) == 3 and not any(isna(s[2]) for s in store):
                (b1, o1, p1), (b2, o2, p2), (b3, o3, p3) = store
                if side == "high" and p1 < p2 < p3:
                    kind = "three_fade" if o1 > o2 > o3 else "three_grow" if o1 < o2 < o3 else None
                elif side == "low" and p1 > p2 > p3:
                    kind = "three_fade" if o1 < o2 < o3 else "three_grow" if o1 > o2 > o3 else None
                else:
                    kind = None
                if kind:
                    out.append(Divergence(kind, side, i, b3, b1, o3, o1, p3, p1, (b1, b2, b3)))
    return out


# ---------------------------------------------------------------------------
# Слепые зоны
# ---------------------------------------------------------------------------
@dataclass
class BlindZone:
    regime: int         # 1 — закончился режим роста (EMA fast > slow), -1 — режим спада
    ext_bar: int        # бар экстремума режима
    ext_price: float    # экстремум (high для роста, low для спада)
    opp_price: float    # противоположная граница от экстремума до пересечения
    cross_bar: int      # бар пересечения EMA fast/slow


def blind_zones(bars: list[Bar], ema_fast, ema_slow):
    """Возвращает (закрытые зоны, текущая открытая зона или None).

    Логика один-в-один с Pine (var-состояние, без циклов по истории):
    в режиме роста ведём максимум high и минимум low после него; при смене
    знака EMA fast - EMA slow фиксируем зону [бар максимума; бар пересечения].
    """
    zones: list[BlindZone] = []
    reg, ext, ext_bar, opp = 0, NA, -1, NA
    for i, b in enumerate(bars):
        d = ema_fast[i] - ema_slow[i]
        new_reg = 1 if d > 0 else -1 if d < 0 else reg
        # обновление экстремума в старом режиме (бар пересечения тоже входит в зону)
        if reg == 1:
            if b.h > ext:
                ext, ext_bar, opp = b.h, i, b.l
            else:
                opp = min(opp, b.l)
        elif reg == -1:
            if b.l < ext:
                ext, ext_bar, opp = b.l, i, b.h
            else:
                opp = max(opp, b.h)
        if new_reg != reg and new_reg != 0:
            if reg != 0:
                zones.append(BlindZone(reg, ext_bar, ext, opp, i))
            ext, ext_bar = (b.h, i) if new_reg == 1 else (b.l, i)
            opp = b.l if new_reg == 1 else b.h
        reg = new_reg
    live = BlindZone(reg, ext_bar, ext, opp, len(bars) - 1) if reg != 0 else None
    return zones, live


# ---------------------------------------------------------------------------
# Анализ волны
# ---------------------------------------------------------------------------
TF_LIST = [
    (60, "1м"), (180, "3м"), (300, "5м"), (900, "15м"), (1800, "30м"), (2700, "45м"),
    (3600, "1ч"), (7200, "2ч"), (10800, "3ч"), (14400, "4ч"), (21600, "6ч"),
    (28800, "8ч"), (43200, "12ч"), (86400, "1Д"), (172800, "2Д"), (259200, "3Д"),
    (604800, "1Н"), (1209600, "2Н"), (2592000, "1М"), (7776000, "3М"),
]


def tf_advice(n_bars: int, tf_sec: int, lo: int = 100, hi: int = 140) -> dict:
    """Правило 100-140 баров и совет по смене ТФ.

    Целевой ТФ = текущий * n / 120; выбирается ближайший (в лог-шкале)
    стандартный ТФ строго меньше (если баров мало) или больше (если много).
    """
    if lo <= n_bars <= hi:
        return dict(status="ok", factor=1.0, tf=None,
                    text=f"{n_bars} баров — в норме ({lo}–{hi})")
    factor = n_bars / 120.0
    target = tf_sec * factor
    cands = [t for t in TF_LIST if (t[0] < tf_sec if n_bars < lo else t[0] > tf_sec)]
    if not cands:
        return dict(status="small" if n_bars < lo else "large", factor=factor, tf=None,
                    text=f"{n_bars} баров — подходящего стандартного ТФ нет")
    best = min(cands, key=lambda t: abs(math.log(t[0]) - math.log(max(target, 1))))
    if n_bars < lo:
        mult = f"≈ ÷{1 / factor:.1f}" if factor > 0 else "≈ ÷?"
        why = "мало баров: индикатор покажет волну старшей степени"
        status = "small"
    else:
        mult = f"≈ ×{factor:.1f}"
        why = "много баров: индикатор разобьёт волну на младшие"
        status = "large"
    return dict(status=status, factor=factor, tf=best[1],
                text=f"{n_bars} баров — {why}; {mult} → используйте {best[1]}")


def time_to_bar(bars: list[Bar], t_ms: int, tf_sec: int) -> int | None:
    """Первый бар, у которого время закрытия > t (как в Pine: time_close > t)."""
    for i, b in enumerate(bars):
        tc = bars[i + 1].t if i + 1 < len(bars) else b.t + tf_sec * 1000
        if tc > t_ms:
            return i
    return None


def classify_profile(a1, a3, a5, c4, k=0.35) -> str:
    """a1, a3, a5 — пики спидометра в направлении волны (со знаком направления),
    c4 — экстремум спидометра в волне 4 против направления.

    импульс: a3 > a1 и a3 > a5 и |c4| < k*a3;
    сужающаяся диагональ: a1 > a3 > a5; расширяющаяся: a1 < a3 < a5.
    """
    if any(isna(x) for x in (a1, a3, a5, c4)):
        return "неопределённо"
    if a3 > a1 and a3 > a5 and a3 > 0:
        return "импульс" if abs(c4) < k * a3 else "неопределённо"
    if a1 > a3 > a5:
        return "сужающаяся диагональ"
    if a1 < a3 < a5:
        return "расширяющаяся диагональ"
    return "неопределённо"


def analyze_wave(bars: list[Bar], series: dict, points: list, tf_sec: int | None = None,
                 k: float = 0.35, rel_len: int = 20) -> dict:
    """points — список из 6 индексов баров (0..5); внутренние могут быть None.

    Если заданы только 0 и 5 — считается только правило 100-140.
    """
    tf_sec = tf_sec or tf_seconds(bars)
    macd = series["macd"]
    p0, p5 = points[0], points[5]
    if p0 is None or p5 is None:
        return dict(error="задайте как минимум точки 0 и 5")
    if p5 <= p0:
        return dict(error="точка 5 должна быть позже точки 0")
    n = p5 - p0
    res = dict(n_bars=n, rule=tf_advice(n, tf_sec), mode="ends")
    inner = points[1:5]
    if any(p is None for p in inner):
        return res
    pts = list(points)
    if any(pts[i + 1] <= pts[i] for i in range(5)):
        return dict(res, error="точки должны идти по времени и на разных свечах")
    res["mode"] = "full"
    s = 1 if bars[p5].c > bars[p0].c else -1
    res["direction"] = "вверх" if s == 1 else "вниз"

    vols = [b.v for b in bars]
    has_vol = any((not isna(v)) and v > 0 for v in vols)
    vsma = sma([0.0 if isna(v) else v for v in vols], rel_len)
    segs = []
    for kseg in range(1, 6):
        a, b = pts[kseg - 1], pts[kseg]
        m = [macd[j] for j in range(a, b + 1) if not isna(macd[j])]
        mx, mn = (max(m), min(m)) if m else (NA, NA)
        impulse_leg = kseg % 2 == 1
        if s == 1:
            peak = mx if impulse_leg else mn
        else:
            peak = mn if impulse_leg else mx
        vsum = sum(0.0 if isna(bars[j].v) else bars[j].v for j in range(a, b + 1))
        cnt = b - a + 1
        rv = [bars[j].v / vsma[j] for j in range(a, b + 1)
              if not isna(bars[j].v) and not isna(vsma[j]) and vsma[j] > 0]
        segs.append(dict(wave=kseg, bars=cnt, peak=peak, max=mx, min=mn,
                         vol_avg=vsum / cnt if has_vol else NA,
                         relvol_avg=sum(rv) / len(rv) if (has_vol and rv) else NA))
    v3 = segs[2]["vol_avg"]
    for sg in segs:
        sg["vol_to_3"] = sg["vol_avg"] / v3 if has_vol and v3 else NA
    a1, a3, a5 = (s * segs[i]["peak"] for i in (0, 2, 4))
    c4 = segs[3]["peak"]
    res["segments"] = segs
    res["profile"] = classify_profile(a1, a3, a5, c4, k)
    res["has_volume"] = has_vol
    res["volume_ok"] = has_vol and v3 > segs[0]["vol_avg"] and v3 > segs[4]["vol_avg"]

    # цены точек: для восходящей 0,2,4 — low, 1,3,5 — high
    def ppx(i):
        bb = bars[pts[i]]
        up_point = (i % 2 == 1) == (s == 1)
        return bb.h if up_point else bb.l
    px = [ppx(i) for i in range(6)]
    lens = [abs(math.log(px[i]) - math.log(px[i - 1])) * 100 for i in range(1, 6)]
    res["prices"] = px
    res["log_len"] = lens
    res["ratios"] = {
        "3/1": lens[2] / lens[0] if lens[0] else NA,
        "5/1": lens[4] / lens[0] if lens[0] else NA,
        "2/1": lens[1] / lens[0] if lens[0] else NA,
        "4/3": lens[3] / lens[2] if lens[2] else NA,
    }
    return res


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _fmt_t(ms: int) -> str:
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime("%Y-%m-%d %H:%M")


def _f(x, nd=4):
    return "na" if isna(x) else f"{x:.{nd}f}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="TW Спидометр Pro — эталонный расчёт")
    ap.add_argument("csv")
    ap.add_argument("--preset", choices=list(PRESETS), default="williams")
    ap.add_argument("--fast", type=int)
    ap.add_argument("--slow", type=int)
    ap.add_argument("--signal", type=int)
    ap.add_argument("--no-log", action="store_true", help="считать от цены, а не от логарифма")
    ap.add_argument("--atr", action="store_true", help="нормировка в единицах ATR")
    ap.add_argument("--tail", type=int, default=0, help="вывести последние N баров")
    ap.add_argument("--at", help="вывести бар по дате/времени (UTC), напр. 2024-03-15 или 2024-03-15T12:00")
    ap.add_argument("--divs", action="store_true", help="список дивергенций")
    ap.add_argument("--div-source", choices=["macd", "hist", "ao"], default="macd")
    ap.add_argument("--three", action="store_true", help="режим «три пика»")
    ap.add_argument("--zones", action="store_true", help="список слепых зон")
    ap.add_argument("--wave", help="6 дат через запятую (точки 0..5); пустые — не заданы")
    ap.add_argument("--k", type=float, default=0.35)
    a = ap.parse_args(argv)

    bars = load_csv(a.csv)
    st = Settings.preset(a.preset, use_log=not a.no_log, norm="atr" if a.atr else "pct")
    if a.fast:
        st.fast = a.fast
    if a.slow:
        st.slow = a.slow
    if a.signal:
        st.signal = a.signal
    sr = compute(bars, st)
    tfs = tf_seconds(bars)
    print(f"Баров: {len(bars)}, ТФ ≈ {tfs} с, настройки: {st}")

    rows = []
    if a.tail:
        rows = list(range(max(0, len(bars) - a.tail), len(bars)))
    if a.at:
        t = _parse_time(a.at)
        rows += [i for i, b in enumerate(bars) if b.t == t] or [time_to_bar(bars, t, tfs)]
    if rows:
        print(f"{'время (UTC)':17} {'close':>12} {'MACD':>10} {'сигн.':>10} {'гист.':>10} {'AO':>10}  фаза")
        for i in rows:
            if i is None:
                continue
            ph = phase(sr["macd"][i], sr["macd"][i - 1] if i else NA)
            print(f"{_fmt_t(bars[i].t):17} {bars[i].c:12.4f} {_f(sr['macd'][i]):>10} "
                  f"{_f(sr['signal'][i]):>10} {_f(sr['hist'][i]):>10} {_f(sr['ao'][i]):>10}  {ph}")

    if a.divs:
        ds = DivSettings(source=a.div_source, three_peaks=a.three)
        for d in find_divergences(bars, sr, ds):
            print(f"{d.kind:10} пивот {_fmt_t(bars[d.pivot_bar].t)} (пред. {_fmt_t(bars[d.prev_bar].t)}), "
                  f"сигнал на {_fmt_t(bars[d.confirm_bar].t)}: осц {_f(d.prev_osc)} → {_f(d.osc)}, "
                  f"цена {d.prev_price:.4f} → {d.price:.4f}")

    if a.zones:
        zs, live = blind_zones(bars, sr["ema_fast"], sr["ema_slow"])
        for z in zs:
            print(f"зона {'роста' if z.regime == 1 else 'спада'}: {_fmt_t(bars[z.ext_bar].t)} → "
                  f"{_fmt_t(bars[z.cross_bar].t)} ({z.cross_bar - z.ext_bar} бар.), "
                  f"экстремум {z.ext_price:.4f}, граница {z.opp_price:.4f}")
        if live:
            print(f"текущая: экстремум {_fmt_t(bars[live.ext_bar].t)} {live.ext_price:.4f}")

    if a.wave:
        parts = [p.strip() for p in a.wave.split(",")]
        parts += [""] * (6 - len(parts))
        pts = [time_to_bar(bars, _parse_time(p), tfs) if p else None for p in parts[:6]]
        r = analyze_wave(bars, sr, pts, tfs, a.k)
        if "error" in r:
            print("Ошибка:", r["error"])
        if "rule" in r:
            print("Правило 100–140:", r["rule"]["text"])
        if r.get("mode") == "full":
            print(f"Направление: {r['direction']}; профиль скорости (не разметка): {r['profile']}")
            for sg in r["segments"]:
                print(f"  волна {sg['wave']}: баров {sg['bars']:4}, пик {_f(sg['peak'])}, "
                      f"объём/бар {_f(sg['vol_avg'], 0)}, к волне 3 {_f(sg['vol_to_3'], 2)}")
            print("  соотношения (лог):", {k2: _f(v, 3) for k2, v in r["ratios"].items()})
    return 0


if __name__ == "__main__":
    sys.exit(main())
