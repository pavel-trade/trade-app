#!/usr/bin/env python3
"""
Синтетические OHLCV с заранее известной формой профиля скорости.

Цена строится по отрезкам в лог-шкале: каждый отрезок = (баров, изменение в %
логарифма, уровень объёма). Шум детерминированный (seed), небольшой, чтобы
пивоты оставались на ожидаемых местах.

  python reference/synthetic.py            # перезаписать reference/test_data.csv
  python reference/synthetic.py out.csv impulse_down

Сценарии (WAVES): impulse_up, impulse_down, contracting, expanding, hidden_bull.
Для каждого известны бары точек 0..5 (функция build возвращает их).
"""
from __future__ import annotations

import math
import os
import random
import sys

try:
    from speedometer import Bar, save_csv
except ImportError:  # запуск как модуля пакета
    from .speedometer import Bar, save_csv

START_MS = 1704067200000   # 01.01.2024 00:00 UTC
DAY_MS = 86_400_000

# (баров, изменение 100*ln, объём)
LEAD_IN = [(60, -18.0, 900), (20, 0.0, 700)]
WAVES = {
    # импульс: волна 3 самая быстрая, волна 4 гасит скорость к нулю, 5 медленнее 3
    "impulse_up": [(24, 12.0, 1200), (14, -5.0, 800), (36, 30.0, 2000), (18, -7.0, 900), (28, 9.0, 1100)],
    # сужающаяся диагональ: скорость затухает от волны 1 к 5
    "contracting": [(16, 16.0, 1500), (14, -9.0, 900), (24, 14.0, 1300), (16, -8.0, 800), (28, 9.0, 1000)],
    # расширяющаяся диагональ: каждый следующий пик выше
    "expanding": [(30, 7.0, 900), (10, -3.0, 700), (24, 12.0, 1200), (12, -6.0, 800), (18, 20.0, 1600)],
}
TAIL = [(40, -15.0, 900)]


def build(segments, lead=LEAD_IN, tail=TAIL, start_price=100.0, noise=0.05,
          seed=7, mirror=False, start_ms=START_MS, step_ms=DAY_MS):
    """Возвращает (bars, points) — points: бары точек 0..5 (концы отрезков волны)."""
    rnd = random.Random(seed)
    path = [100.0 * math.log(start_price)]
    vol = [lead[0][2] if lead else 1000]
    points = []
    all_segs = list(lead) + list(segments) + list(tail)
    first_wave = len(lead)
    for si, (n, chg, v) in enumerate(all_segs):
        if si == first_wave:
            points.append(len(path) - 1)
        step = chg / n
        for _ in range(n):
            path.append(path[-1] + step)
            vol.append(v)
        if first_wave <= si < first_wave + len(segments):
            points.append(len(path) - 1)
    bars = []
    prev = path[0]
    for i, x in enumerate(path):
        y = x + rnd.uniform(-noise, noise)
        if mirror:
            y = 2 * path[0] - y
        o = prev
        c = y
        hi = max(o, c) + 0.15 + rnd.uniform(0, 0.05)
        lo = min(o, c) - 0.15 - rnd.uniform(0, 0.05)
        bars.append(Bar(start_ms + i * step_ms, math.exp(o / 100), math.exp(hi / 100),
                        math.exp(lo / 100), math.exp(c / 100),
                        float(round(vol[i] * (1 + rnd.uniform(-0.1, 0.1))))))
        prev = c
    return bars, points


def scenario(name: str, **kw):
    if name == "impulse_down":
        return build(WAVES["impulse_up"], mirror=True, **kw)
    if name == "hidden_bull":
        # тренд вверх: мягкий откат A, затем резкий откат B с более высоким минимумом цены
        segs = [(30, 15.0, 1000), (15, -4.0, 800), (25, 12.0, 1000), (6, -9.0, 1200), (30, 14.0, 1000)]
        return build(segs, lead=[(40, 0.0, 800)], tail=[])
    return build(WAVES[name], **kw)


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    out = argv[0] if argv else os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_data.csv")
    name = argv[1] if len(argv) > 1 else "impulse_up"
    bars, pts = scenario(name)
    save_csv(out, bars)
    print(f"{out}: {len(bars)} баров, сценарий {name}, точки 0..5 на барах {pts}")


if __name__ == "__main__":
    main()
