#!/usr/bin/env python3
"""
Вклад пунктов чек-листа «Зона -> Истощение -> Триггер» (TW Карта рынка) на исторических данных.

Это проверка гипотезы, а не подбор параметров: настройки фиксированы (по умолчанию как
в индикаторе), соседние значения длины пивота показаны рядом только для оценки устойчивости.

Событие — бар, на котором закрытие впервые ушло за «первый триггер» (пункт 5 стал ✓)
при существующей структуре A-B-C без отмены. Для каждого события запоминаются пункты 1-9.
Сделка (только для оценки признаков):
  вход — open следующего бара; стоп — C -/+ ATR x m (на баре события);
  цель — 1.0 x волна 1 от C (лог-шкала, как в индикаторе);
  выход — стоп или цель; если оба внутри одного бара, считается стоп (консервативно);
  если за max-hold баров ничего — выход по close;
  комиссия 0.1 % за сторону, в R.

  python research/component_value.py data.csv [data2.csv ...] --split 2024-01-01
  python research/component_value.py --demo-random-walk          # нулевой тест на случайном блуждании

CSV: колонки time, open, high, low, close[, volume] (экспорт TradingView подходит).
"""
from __future__ import annotations

import argparse
import datetime as dt
import math
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "reference"))

import map as mm  # noqa: E402
from speedometer import Bar, _parse_time, isna, load_csv  # noqa: E402

ITEM_NAMES = {
    0: "1. откат 38.2-78.6 %",
    1: "2. коррекция медленнее",
    2: "3. конвергенция на C",
    3: "4. объём коррекции ниже",
    5: "6. ускорение по направлению",
    6: "7. конец слепой зоны",
    7: "8. цена за AVWAP от C",
    8: "9. фильтр SMA 200",
}
MIN_N = 30


# ---------------------------------------------------------------------------
def events(bars: list[Bar], st: mm.MapSettings) -> list[dict]:
    """События «закрытие за первым триггером» — по одному на структуру A-B-C."""
    recs = mm.MarketMap(bars, st).run()
    out, seen, prev5 = [], set(), mm.NODATA
    for r in recs:
        it5 = r["items"][4]
        if r["cand"] and not r["reason"] and it5 == mm.OK and prev5 != mm.OK:
            key = (r["A"], r["B"], r["C"])
            if key not in seen and not isna(r["stop"]):
                seen.add(key)
                out.append(r)
        prev5 = it5 if r["cand"] else mm.NODATA
    return out


def trade(bars: list[Bar], ev: dict, commission: float, max_hold: int) -> dict | None:
    i = ev["bar"]
    if i + 1 >= len(bars):
        return None
    d, stop, target = ev["d"], ev["stop"], ev["targets"][1.0]
    entry = bars[i + 1].o
    risk = d * (entry - stop)
    if risk <= 0 or d * (target - entry) <= 0:
        return None                     # вход уже за стопом или за целью — сделки нет
    exit_px, hit = None, False
    for j in range(i + 1, min(len(bars), i + 1 + max_hold)):
        b = bars[j]
        if (b.l <= stop) if d == 1 else (b.h >= stop):
            exit_px = stop
            break
        if (b.h >= target) if d == 1 else (b.l <= target):
            exit_px, hit = target, True
            break
    if exit_px is None:
        exit_px = bars[min(len(bars) - 1, i + max_hold)].c
    gross = d * (exit_px - entry) / risk
    cost = commission / 100.0 * (entry + exit_px) / risk
    return dict(R=gross - cost, hit=hit, t=bars[i].t, items=ev["items"], status=ev["status"])


def stats(trades: list[dict]) -> dict:
    n = len(trades)
    if n == 0:
        return dict(n=0, hit=math.nan, avgR=math.nan, pf=math.nan, dd=math.nan)
    rs = [t["R"] for t in trades]
    pos, neg = sum(r for r in rs if r > 0), -sum(r for r in rs if r < 0)
    eq = peak = dd = 0.0
    for r in rs:
        eq += r
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
    return dict(n=n, hit=sum(t["hit"] for t in trades) / n, avgR=sum(rs) / n,
                pf=pos / neg if neg > 0 else math.inf, dd=dd)


def fmt(s: dict) -> str:
    if s["n"] == 0:
        return "0 | — | — | — | —"
    flag = " ⚠ <30" if s["n"] < MIN_N else ""
    pf = "∞" if s["pf"] == math.inf else f"{s['pf']:.2f}"
    return f"{s['n']}{flag} | {s['hit'] * 100:.0f} % | {s['avgR']:+.2f} | {pf} | {s['dd']:.1f}"


def contradiction(a: dict, b: dict) -> bool:
    return a["n"] > 0 and b["n"] > 0 and (a["avgR"] > 0) != (b["avgR"] > 0)


def analyze(datasets: list[tuple[str, list[Bar]]], st: mm.MapSettings, split_ms: int,
            commission: float, max_hold: int) -> list[dict]:
    trades = []
    for name, bars in datasets:
        for ev in events(bars, st):
            t = trade(bars, ev, commission, max_hold)
            if t:
                t["src"] = name
                trades.append(t)
    return trades


def report(datasets, base: mm.MapSettings, split_ms: int, commission: float, max_hold: int,
           neighbors: list[int], title: str) -> str:
    lines = [f"# {title}", ""]
    lines.append(f"Данные: {', '.join(f'{n} ({len(b)} баров)' for n, b in datasets)}. "
                 f"Разделение периодов: {dt.datetime.fromtimestamp(split_ms / 1000, dt.timezone.utc):%Y-%m-%d}. "
                 f"Комиссия {commission} % за сторону, стоп C ∓ ATR×{base.atr_mult}, цель 1.0 × волна 1, "
                 f"макс. удержание {max_hold} баров.")
    lines.append("")
    lines.append("Колонки: сделок | доля целей | средний R (после комиссии) | PF | макс. просадка, R. "
                 f"«⚠ <30» — выборка меньше {MIN_N}, вывод ненадёжен.")
    lines.append("")
    # устойчивость к соседним настройкам
    lines.append("## Все события и статус «Триггер» при соседних длинах пивота")
    lines.append("")
    lines.append("| L | подмножество | период 1 | период 2 | весь период |")
    lines.append("|---|---|---|---|---|")
    main_trades = None
    for L in neighbors:
        st = mm.MapSettings(**{**base.__dict__, "zz_len": L})
        tr = analyze(datasets, st, split_ms, commission, max_hold)
        if L == base.zz_len:
            main_trades = tr
        for nm, sub in (("все события", tr), ("статус «Триггер»", [t for t in tr if t["status"] == 3])):
            p1 = stats([t for t in sub if t["t"] < split_ms])
            p2 = stats([t for t in sub if t["t"] >= split_ms])
            mark = " ⚠ периоды противоречат" if contradiction(p1, p2) else ""
            lines.append(f"| {L} | {nm}{mark} | {fmt(p1)} | {fmt(p2)} | {fmt(stats(sub))} |")
    lines.append("")
    lines.append(f"## Вклад пунктов (L = {base.zz_len})")
    lines.append("")
    lines.append("Для каждого пункта — события, где он ✓, и где ✗ («—» не входит ни туда, ни туда).")
    lines.append("")
    lines.append("| пункт | с признаком: период 1 | период 2 | без признака: период 1 | период 2 | замечание |")
    lines.append("|---|---|---|---|---|---|")
    tr = main_trades or []
    for k, name in ITEM_NAMES.items():
        w = [t for t in tr if t["items"][k] == mm.OK]
        wo = [t for t in tr if t["items"][k] == mm.FAIL]
        s = {(a, p): stats([t for t in grp if (t["t"] < split_ms) == (p == 1)])
             for a, grp in (("w", w), ("wo", wo)) for p in (1, 2)}
        notes = []
        if any(v["n"] < MIN_N for v in s.values()):
            notes.append("мало данных")
        d1 = s[("w", 1)]["avgR"] - s[("wo", 1)]["avgR"]
        d2 = s[("w", 2)]["avgR"] - s[("wo", 2)]["avgR"]
        if not (math.isnan(d1) or math.isnan(d2)) and (d1 > 0) != (d2 > 0):
            notes.append("вклад меняет знак между периодами")
        lines.append(f"| {name} | {fmt(s[('w', 1)])} | {fmt(s[('w', 2)])} | {fmt(s[('wo', 1)])} | {fmt(s[('wo', 2)])} | "
                     f"{', '.join(notes) or '—'} |")
    lines.append("")
    return "\n".join(lines)


def random_walk(n: int, seed: int, start_ms: int, step_ms: int, vol: float = 0.03) -> list[Bar]:
    """Геометрическое случайное блуждание без тренда: на нём преимущества быть не должно."""
    rnd = random.Random(seed)
    bars, p = [], 20000.0
    for i in range(n):
        o = p
        c = o * math.exp(rnd.gauss(0, vol))
        h = max(o, c) * math.exp(abs(rnd.gauss(0, vol / 2)))
        lo = min(o, c) * math.exp(-abs(rnd.gauss(0, vol / 2)))
        bars.append(Bar(start_ms + i * step_ms, o, h, lo, c, float(rnd.randint(500, 1500))))
        p = c
    return bars


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv", nargs="*", help="CSV с OHLCV (несколько — объединяются по сделкам)")
    ap.add_argument("--split", default="2024-01-01", help="дата разделения на два периода (UTC)")
    ap.add_argument("--zz-len", type=int, default=10)
    ap.add_argument("--neighbors", default="", help="соседние L через запятую (по умолчанию L-2, L, L+2)")
    ap.add_argument("--atr-mult", type=float, default=0.5)
    ap.add_argument("--commission", type=float, default=0.1, help="% за сторону")
    ap.add_argument("--max-hold", type=int, default=200)
    ap.add_argument("--out", default=os.path.join(HERE, "report.md"))
    ap.add_argument("--demo-random-walk", action="store_true", help="нулевой тест на случайном блуждании")
    a = ap.parse_args(argv)

    base = mm.MapSettings(zz_len=a.zz_len, atr_mult=a.atr_mult)
    nb = [int(x) for x in a.neighbors.split(",") if x] or [max(2, a.zz_len - 2), a.zz_len, a.zz_len + 2]
    split_ms = _parse_time(a.split)
    if a.demo_random_walk:
        start = _parse_time("2022-01-01")
        datasets = [(f"случайное блуждание #{s} (4ч)", random_walk(8760, s, start, 4 * 3600_000)) for s in (1, 2, 3)]
        title = "Нулевой тест: случайное блуждание (не реальные данные)"
    else:
        if not a.csv:
            ap.error("укажите CSV или --demo-random-walk")
        datasets = [(os.path.basename(p), load_csv(p)) for p in a.csv]
        title = "Вклад пунктов чек-листа на реальных данных"
    text = report(datasets, base, split_ms, a.commission, a.max_hold, nb, title)
    print(text)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(text + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
