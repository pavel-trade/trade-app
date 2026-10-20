# TW Speedometer Pro

**A tool for checking Elliott wave counts against price speed.** It is not a trading system:
it gives no buy/sell signals and makes no claims about profitability or accuracy. It measures
and displays how fast price moves; you decide what that means for your wave count.

## What it calculates

- **Speedometer line** — a linear MACD: EMA(5) − EMA(34) of `100 × ln(close)`. Taking the
  logarithm makes the value a percentage, so peaks are comparable at any price level
  (for example, BTC at 20k and at 120k). You can turn the logarithm off.
- **Signal line** — EMA(5) of the speedometer line; **histogram** = line − signal.
- **Awesome Oscillator** — SMA(5) − SMA(34) of `100 × ln(hl2)`.
- **Optional ATR normalisation** — all values divided by ATR(14) in the same units, which
  makes different instruments comparable.
- **Phase colours** — a bright bar means speed is increasing, a pale bar means it is
  decreasing. The palette is colour-blind friendly (Okabe-Ito), and every signal also has
  a text label or line style, so colour is never the only cue.
- **Presets** — Williams 5/34/5 (default), Large pattern 13/55/13, Classic 12/26/9, Custom.
  Display presets ("line only", "histogram only") let you add the indicator twice.

## Features

- **Multi-timeframe table** — sign and phase of speed on three timeframes (accelerating /
  decelerating up or down). It uses three `request.security` calls with `lookahead_off`
  and takes the last *closed* higher-timeframe bar on history, so it does not repaint.
  The cost is a lag of one higher-timeframe bar.
- **Divergences** — regular and hidden, on the line, histogram or AO. Pivots come from
  `ta.pivothigh/pivotlow`. Price is compared using the high/low in a window around the
  pivot (not the close). Filters: zero line for regular divergences, minimum distance
  between pivots, and a minimum difference as a share of the oscillator's range.
  A **three-peak mode** compares waves 1-3-5 and labels the result "fading" or "growing".
  Divergences appear only after the "right bars" of the pivot have closed. This is a
  confirmation delay, not repainting, and the indicator does not anticipate anything.
- **Blind zone** — the stretch from the price extreme to the EMA 5/34 crossover, during
  which the linear MACD still shows the old trend. It is drawn as boxes or as background.
- **Wave analysis table** — you enter points 0-5 of a wave. The table shows:
  - the 100-140 bar rule, with a suggested timeframe (e.g. "≈ ×2.1 → use 2h");
  - speedometer peaks per leg and the speed profile ("impulse", "contracting diagonal",
    "expanding diagonal" or "undetermined"), shown as *a speed profile, not a wave count*;
  - average volume per bar and its ratio to wave 3;
  - log-scale wave ratios (3/1, 5/1, depth of 2 and 4) for comparison with Fibonacci levels.
- **Volume** — average volume per leg and relative volume (volume / SMA 20). The volume
  block switches itself off for symbols that have no volume data.
- **Alerts** — `alertcondition` for each event, plus `alert()` messages with symbol,
  timeframe and value, sent on confirmed bars only.

A companion overlay, **TW Blind Zone Pro**, draws EMA 5/34 (log-based by default) with a fill
between them, blind-zone rectangles tracked bar by bar, SMA 200 / EMA 20 / EMA 50, an optional
anchored VWAP and crossover alerts.

## Speed profile templates (for reading the panel)

| Pattern | Speedometer |
|---|---|
| Impulse | main peak in the middle of wave 3; wave 4 returns to zero; wave 5 peak is lower |
| Contracting diagonal | strongest peak in wave 1, then fading |
| Expanding diagonal | every peak is higher than the previous one, no divergence |
| Contracting / expanding triangle | the outline narrows / widens on both sides of zero |
| Correction | noticeably slower than the preceding impulse |

The wave you study should take 100-140 bars of the current timeframe. With fewer bars the
speedometer shows a wave of higher degree. With more bars it splits the wave into lower degrees.

## Verification

An open reference implementation in Python (with tests on synthetic data) reproduces every
value, so you can compare any bar against TradingView's Data Window.

## Limitations

- The indicator does not count waves. It only checks the speed profile of the points you enter.
- Only four profiles are classified automatically. Triangles and corrections are read visually.
- Higher-timeframe values lag by one higher-timeframe bar, by design.

## Credits

The method is based on the speedometer approach described at tradewaves.ru
(https://tradewaves.ru/teoriya/macd/), Bill Williams' Awesome Oscillator (5/34) and research
by pr0 / RSWA.

## Disclaimer

This script is for educational and analytical purposes only. It is not financial advice and
not a recommendation to buy or sell any instrument. Past behaviour of an indicator does not
predict future results. Trading involves risk of loss. You are solely responsible for your
decisions.
