#!/usr/bin/env python3
"""Solana volume-pressure alert bot.

This bot does NOT place trades. It watches Solana pairs and alerts when
buy-side pressure + volume regime match configured thresholds.
"""

from __future__ import annotations

import os
import sys
import time
import json
import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests
from dotenv import load_dotenv

DEXSCREENER_BASE = "https://api.dexscreener.com/latest/dex/pairs/solana"


@dataclass
class PairSnapshot:
    symbol: str
    pair_address: str
    price_usd: float
    volume_m5: float
    buys_m5: int
    sells_m5: int
    change_m5: float
    liquidity_usd: float
    url: str


@dataclass
class Signal:
    level: str
    score: int
    reason: str


class DexScreenerClient:
    def __init__(self, timeout_seconds: int = 10) -> None:
        self.timeout_seconds = timeout_seconds

    def fetch_pair(self, pair_address: str) -> Optional[PairSnapshot]:
        url = f"{DEXSCREENER_BASE}/{pair_address}"
        try:
            response = requests.get(url, timeout=self.timeout_seconds)
            response.raise_for_status()
            payload = response.json()
            pairs = payload.get("pairs") or []
            if not pairs:
                return None
            pair = pairs[0]

            base_token = pair.get("baseToken", {})
            txns = pair.get("txns", {}).get("m5", {})
            volume = pair.get("volume", {}).get("m5", 0)
            price_change = pair.get("priceChange", {}).get("m5", 0)
            liquidity = pair.get("liquidity", {}).get("usd", 0)

            return PairSnapshot(
                symbol=str(base_token.get("symbol", "UNKNOWN")),
                pair_address=pair_address,
                price_usd=float(pair.get("priceUsd", 0) or 0),
                volume_m5=float(volume or 0),
                buys_m5=int(txns.get("buys", 0) or 0),
                sells_m5=int(txns.get("sells", 0) or 0),
                change_m5=float(price_change or 0),
                liquidity_usd=float(liquidity or 0),
                url=str(pair.get("url", "")),
            )
        except requests.RequestException as exc:
            print(f"[WARN] Failed to fetch {pair_address}: {exc}", file=sys.stderr)
            return None


class Strategy:
    """Scores each pair into volume zones.

    There are no guaranteed-win zones. This model only flags probabilistic setups.
    """

    def __init__(
        self,
        min_liquidity_usd: float,
        low_volume_cutoff: float,
        prime_volume_floor: float,
        buy_pressure_threshold: float,
        max_negative_change_m5: float,
    ) -> None:
        self.min_liquidity_usd = min_liquidity_usd
        self.low_volume_cutoff = low_volume_cutoff
        self.prime_volume_floor = prime_volume_floor
        self.buy_pressure_threshold = buy_pressure_threshold
        self.max_negative_change_m5 = max_negative_change_m5

    @staticmethod
    def buy_pressure(snapshot: PairSnapshot) -> float:
        total = snapshot.buys_m5 + snapshot.sells_m5
        if total == 0:
            return 0.0
        return (snapshot.buys_m5 - snapshot.sells_m5) / total

    def evaluate(self, snapshot: PairSnapshot) -> Signal:
        if snapshot.liquidity_usd < self.min_liquidity_usd:
            return Signal(
                level="IGNORE",
                score=0,
                reason=(
                    f"Liquidity too low (${snapshot.liquidity_usd:,.0f} < "
                    f"${self.min_liquidity_usd:,.0f})"
                ),
            )

        pressure = self.buy_pressure(snapshot)

        if snapshot.volume_m5 < self.low_volume_cutoff:
            return Signal(
                level="LOW_VOLUME_ZONE",
                score=15,
                reason=(
                    f"Avoid: low volume zone (${snapshot.volume_m5:,.0f} in 5m). "
                    "Thin books can fake momentum."
                ),
            )

        score = 0
        reasons: List[str] = []

        if snapshot.volume_m5 >= self.prime_volume_floor:
            score += 35
            reasons.append("Strong 5m volume")

        if pressure >= self.buy_pressure_threshold:
            score += 35
            reasons.append(
                f"Buy pressure {pressure:.2f} >= {self.buy_pressure_threshold:.2f}"
            )

        if snapshot.change_m5 >= self.max_negative_change_m5:
            score += 20
            reasons.append(f"Price change stable ({snapshot.change_m5:+.2f}% in 5m)")

        if snapshot.buys_m5 > snapshot.sells_m5:
            score += 10
            reasons.append("More buys than sells")

        if score >= 80:
            level = "PRIME_ZONE"
        elif score >= 50:
            level = "WATCHLIST_ZONE"
        else:
            level = "NEUTRAL_ZONE"

        return Signal(level=level, score=score, reason="; ".join(reasons) or "No edge")


class Notifier:
    def __init__(self, webhook_url: Optional[str]) -> None:
        self.webhook_url = webhook_url

    def send(self, message: str) -> None:
        print(message)
        if not self.webhook_url:
            return
        try:
            requests.post(self.webhook_url, json={"content": message}, timeout=8)
        except requests.RequestException as exc:
            print(f"[WARN] webhook failed: {exc}", file=sys.stderr)


def format_alert(snapshot: PairSnapshot, signal: Signal) -> str:
    pressure = Strategy.buy_pressure(snapshot)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    payload: Dict[str, Any] = {
        "time_utc": ts,
        "symbol": snapshot.symbol,
        "pair": snapshot.pair_address,
        "level": signal.level,
        "score": signal.score,
        "price_usd": snapshot.price_usd,
        "volume_m5": snapshot.volume_m5,
        "buys_m5": snapshot.buys_m5,
        "sells_m5": snapshot.sells_m5,
        "buy_pressure": round(pressure, 4),
        "price_change_m5_pct": snapshot.change_m5,
        "liquidity_usd": snapshot.liquidity_usd,
        "reason": signal.reason,
        "url": snapshot.url,
    }
    return json.dumps(payload, separators=(",", ":"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Solana market-prime alert bot (volume + order-flow proxy)."
    )
    parser.add_argument(
        "--pairs",
        required=False,
        default=os.getenv("SOLANA_PAIR_ADDRESSES", ""),
        help="Comma-separated DexScreener pair addresses.",
    )
    parser.add_argument(
        "--interval-seconds",
        type=int,
        default=int(os.getenv("POLL_INTERVAL_SECONDS", "45")),
        help="Polling interval in seconds.",
    )
    return parser.parse_args()


def main() -> int:
    load_dotenv()
    args = parse_args()

    pair_addresses = [p.strip() for p in args.pairs.split(",") if p.strip()]
    if not pair_addresses:
        print(
            "No pair addresses provided. Set SOLANA_PAIR_ADDRESSES in .env "
            "or pass --pairs.",
            file=sys.stderr,
        )
        return 1

    strategy = Strategy(
        min_liquidity_usd=float(os.getenv("MIN_LIQUIDITY_USD", "50000")),
        low_volume_cutoff=float(os.getenv("LOW_VOLUME_CUTOFF_USD_M5", "3000")),
        prime_volume_floor=float(os.getenv("PRIME_VOLUME_FLOOR_USD_M5", "10000")),
        buy_pressure_threshold=float(os.getenv("BUY_PRESSURE_THRESHOLD", "0.2")),
        max_negative_change_m5=float(os.getenv("MAX_NEGATIVE_CHANGE_M5", "-1.25")),
    )

    notifier = Notifier(webhook_url=os.getenv("DISCORD_WEBHOOK_URL"))
    client = DexScreenerClient()

    print(f"Watching {len(pair_addresses)} Solana pair(s) every {args.interval_seconds}s...")
    while True:
        for pair in pair_addresses:
            snapshot = client.fetch_pair(pair)
            if not snapshot:
                continue

            signal = strategy.evaluate(snapshot)
            if signal.level in {"PRIME_ZONE", "LOW_VOLUME_ZONE"}:
                notifier.send(format_alert(snapshot, signal))

        time.sleep(args.interval_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
