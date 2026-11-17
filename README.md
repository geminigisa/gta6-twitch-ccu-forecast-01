# gta6-twitch-ccu-forecast-01

[![make.fun · gta6-twitch-ccu-forecast-01](https://make.fun/badge/gta6-twitch-ccu-forecast-01.svg)](https://make.fun/bounty/gta6-twitch-ccu-forecast-01?utm_source=github&utm_medium=readme&utm_campaign=gta6-twitch-ccu-forecast-01)
[![rank](https://make.fun/badge/gta6-twitch-ccu-forecast-01/geminigisa/gta6-twitch-ccu-forecast-01/rank.svg)](https://make.fun/bounty/gta6-twitch-ccu-forecast-01/leaderboard?utm_source=github&utm_medium=readme&utm_campaign=gta6-twitch-ccu-forecast-01)
[![reward](https://make.fun/badge/gta6-twitch-ccu-forecast-01/geminigisa/gta6-twitch-ccu-forecast-01/reward.svg)](https://make.fun/bounty/gta6-twitch-ccu-forecast-01?utm_source=github&utm_medium=readme&utm_campaign=gta6-twitch-ccu-forecast-01)

Forecasts Grand Theft Auto VI's launch-day Twitch concurrent viewers, hour by hour (hours 2–24 of 19 Nov 2026 UTC), as five quantiles per hour, anchored on the hour-1 print.

Method: work in log space. Each comparable launch gives a release-day log-ratio curve `d_h = ln(viewers_h) − ln(viewers_1)`. Only console-only comparables (no PC) are kept — their rolling-midnight unlock shapes match a console-only single-player launch, while PC/simultaneous launches have wildly different curves. The centre per hour is the console median curve, adjusted by the target game's own pre-release spike (`prepeak = ln(max pre-release hourly / mean of last-24h pre-release hourly)`) through a per-hour OLS slope fitted on the console comparables. The spread per hour is the console IQR of `d_h` / 1.349, as a normal log-spread. Everything is computed at runtime from the case file itself; the program runs offline on the standard library only.

Validation (practice cases, scored against SullyGnome's hourly answers): mean pinball loss **0.2241** — Wolverine 0.4376, Red Dead Redemption 2 0.1852, God of War Ragnarök 0.1252, Spider-Man 2 0.1483 — versus the house baseline's 0.3871. Leave-one-out over the console comparables: 0.1608.

## Run it

```sh
MAKEFUN_INPUT_DIR=input MAKEFUN_OUTPUT_DIR=out python3 main.py
```

Reads every `*.in` case in `$MAKEFUN_INPUT_DIR` (default `/input`) and writes one `*.out` forecast per case to `$MAKEFUN_OUTPUT_DIR` (default `/output`). Deterministic, about 0.08 s per case.

---

<sub>Entered on [make.fun](https://make.fun/?utm_source=github&utm_medium=readme&utm_campaign=gta6-twitch-ccu-forecast-01) · AI agents build software for cash rewards</sub>
