# Solana Prime Zone Alert Bot

A lightweight Python bot that watches **Solana** pairs and alerts when:

1. Buy-side flow is greater than sell-side flow (proxy from recent trade counts).
2. Volume is high enough to avoid low-liquidity traps.
3. Liquidity/price action are within guardrails.

> This is an alerting bot, not an execution bot. It cannot guarantee wins.

## What it does

- Polls DexScreener pair endpoints for Solana pair addresses.
- Classifies each pair into:
  - `PRIME_ZONE` (high volume + buy pressure + stable momentum)
  - `WATCHLIST_ZONE`
  - `NEUTRAL_ZONE`
  - `LOW_VOLUME_ZONE` (avoid trading)
- Sends JSON alerts to stdout and optionally to a Discord webhook.

## Quick start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Fill in SOLANA_PAIR_ADDRESSES and optional webhook.
python bot.py
```

Or pass pairs directly:

```bash
python bot.py --pairs "PAIR_ADDR_1,PAIR_ADDR_2" --interval-seconds 30
```

## How to choose pair addresses

1. Open a Solana pair on DexScreener.
2. Copy the **pair address** from the URL.
3. Add that address to `SOLANA_PAIR_ADDRESSES`.

## Strategy knobs

Tune these in `.env`:

- `LOW_VOLUME_CUTOFF_USD_M5`: below this 5m volume, bot flags `LOW_VOLUME_ZONE`.
- `PRIME_VOLUME_FLOOR_USD_M5`: minimum 5m volume for strong setup scoring.
- `BUY_PRESSURE_THRESHOLD`: minimum net buy pressure in 5m.
- `MAX_NEGATIVE_CHANGE_M5`: rejects setups with sharp negative momentum.
- `MIN_LIQUIDITY_USD`: avoids illiquid pairs.

## Next improvements

- Add candle indicators (VWAP/EMA/ATR) from a market data API.
- Add Telegram alerts.
- Add SQLite logging + backtesting to calibrate thresholds.
