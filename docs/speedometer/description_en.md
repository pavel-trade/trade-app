# TW Speedometer — Speed + Acceleration

**A tool for checking Elliott wave counts against price speed.** It is not a trading system: it gives
no buy/sell signals and makes no claims about profitability or accuracy. It measures and displays how
fast price moves and whether that movement is speeding up or slowing down. You decide what that means
for your wave count.

## What it calculates

- **Speed** — a linear MACD: EMA(5) − EMA(34) of `100 × ln(close)`. Because it is based on the
  logarithm, the value is a percentage, and peaks are comparable at any price level. You can turn
  the logarithm off.
- **Acceleration** — speed minus EMA(speed, 5), which is the MACD 5/34/5 histogram.
- **Columns** — the height is speed and the brightness is acceleration in the direction of the move.
  This gives four phases: rising and accelerating, rising and decelerating, falling and accelerating,
  falling and decelerating. The palette is colour-blind friendly (Okabe-Ito). The phase is also
  written out in the status table, so colour is never the only cue.
- **Presets** — Williams 5/34/5 (default), Large pattern 13/55/13, Classic 12/26/9, Custom.

## Features

- **Acceleration display** has three modes:
  - an overlay line with a fixed multiplier (×3 by default);
  - auto-scale: a multiplier computed **once**, on the last bar, as the ratio of the 95th percentiles
    of |speed| and |acceleration| over the last N bars, so the scale is the same across all drawn history;
  - a thin band at zero that shows only the sign and the strength.

  A label at the right edge shows the scale in use.
- **Early signals** — markers when acceleration changes sign, and when speed changes sign. A change in
  the sign of speed is the EMA 5/34 crossover, which marks the end of the "blind zone". Optionally, markers
  appear only after the bar closes.
- **Divergences on speed** — regular and hidden, from `ta.pivothigh/pivotlow`. Price is compared using
  the high/low in a window around the pivot. There is a noise filter (minimum difference as a share of
  the speed range) and a sign filter. Divergences appear only after the pivot's "right bars" have
  closed. This is a confirmation delay, not repainting, and the indicator does not anticipate anything.
- **Wave peaks 1-3-5** — you enter dates for points 0-5. The indicator marks the speed extreme of every
  leg, joins peaks 1-3-5 with a line and classifies the profile as "impulse", "contracting diagonal",
  "expanding diagonal" or "undetermined". It is labelled as *a speed profile, not a wave count*. With
  no points entered, the mode draws nothing.
- **Status table** — the phase in words, speed, acceleration and current speed as a percentage of the
  recent peak. It can also show the phase on up to three higher timeframes. These use `request.security`
  with `lookahead_off` and take the last closed higher-timeframe bar on history, so they do not repaint.
  The cost is a lag of one higher-timeframe bar.
- **Alerts** — `alertcondition` for sign changes and divergences, plus `alert()` messages with symbol,
  timeframe, phase and values, sent on closed bars only.

## Verification

An open Python reference implementation reproduces speed, acceleration, phases, pivots, divergences
and the 1-3-5 classification, with tests on synthetic data. You can compare any bar against
TradingView's Data Window.

## Limitations

- The indicator does not count waves. It checks the speed profile of the points you enter.
- Only three profiles are classified automatically (impulse and two diagonals). Triangles and
  corrections are read visually.
- Divergences lag by the pivot's right bars; higher-timeframe values lag by one higher-timeframe bar.

## Credits

The method is based on the speedometer approach at tradewaves.ru (https://tradewaves.ru/teoriya/macd/),
Bill Williams' 5/34 oscillator ideas, and research by pr0 / RSWA.

## Disclaimer

This script is for educational and analytical purposes only. It is not financial advice and not a
recommendation to buy or sell any instrument. Past behaviour of an indicator does not predict future
results. Trading involves risk of loss. You are solely responsible for your decisions.
