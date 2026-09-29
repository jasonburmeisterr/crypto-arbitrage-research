"""
Funding Rate Arbitrage Scanner
--------------------------------
Monitors the funding rate on a perpetual futures contract and compares it
to the spot price of the same asset. This is a READ-ONLY / ALERTING tool:
it does not place trades or manage a hedge for you. It's meant to help you
decide WHEN a funding-rate arbitrage position would be worth opening or
closing, based on the risk-mitigation rules we discussed:

  - Alert when funding is attractive enough to be worth the fee/collateral cost
  - Track basis (perp price vs spot price) so you can see hedge drift
  - Flag when funding flips unfavorable for 2 CONSECUTIVE funding periods,
    which is the exit rule from our conversation

Setup:
    pip install ccxt

Run:
    python funding_rate_scanner.py

IMPORTANT: perpetual futures symbols vary a lot between exchanges. Run this
script once first -- it will print available BTC-related swap markets on
your chosen exchange so you can confirm/adjust PERP_SYMBOL below.
"""

import csv
import os
import time
import ccxt


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SPOT_EXCHANGE_ID = "kraken"          # for the spot price side of the hedge
PERP_EXCHANGE_ID = "krakenfutures"   # for the perpetual futures / funding rate
PERP_SYMBOL = "BTC/USD:USD"          # ccxt unified symbol for the perpetual
SPOT_SYMBOL = "BTC/USD"

# Funding rates are usually quoted PER FUNDING PERIOD (often 8 hours), not
# annualized. We convert to an annualized % so it's easier to reason about
# against a savings account or other benchmark.
FUNDING_PERIODS_PER_YEAR = 365 * 3   # 3 periods/day * 365 days, adjust if your
                                       # exchange uses a different interval

ENTRY_THRESHOLD_ANNUALIZED_PCT = 1.0   # only worth entering above this yield
EXIT_CONSECUTIVE_PERIODS = 2            # exit rule: N consecutive bad periods

SCAN_INTERVAL_SECONDS = 60  # funding rates don't change every second, so this
                             # can be much slower than the triangular scanner

CSV_FILENAME = "funding_rate_log.csv"
CSV_HEADERS = [
    "date", "time", "funding_rate_pct", "annualized_pct",
    "spot_price", "perp_mark_price", "basis_pct",
    "funding_timestamp", "signal",
]

# Simulated position state -- set this to True once you've manually opened
# a real hedge, so the script starts tracking consecutive unfavorable
# funding periods for the exit rule. This does NOT open or close anything
# for you; it's just a flag that changes what the script watches for.
IN_POSITION = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def list_available_perp_symbols(exchange, search="BTC"):
    """Print swap/perpetual markets matching a search string, to help you
    confirm the correct PERP_SYMBOL for your exchange."""
    swap_symbols = [
        m["symbol"] for m in exchange.markets.values()
        if m.get("swap") and search in m["symbol"]
    ]
    print(f"Available {search} perpetual symbols on {PERP_EXCHANGE_ID}:")
    for s in swap_symbols[:15]:
        print(f"   {s}")
    print()


def init_csv():
    if not os.path.exists(CSV_FILENAME):
        with open(CSV_FILENAME, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(CSV_HEADERS)


def log_to_csv(row):
    with open(CSV_FILENAME, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(row)


def get_funding_and_prices(perp_exchange, spot_exchange):
    """Fetch the current funding rate, perp mark price, and spot price."""
    funding = perp_exchange.fetch_funding_rate(PERP_SYMBOL)
    spot_ticker = spot_exchange.fetch_ticker(SPOT_SYMBOL)

    funding_rate = funding.get("fundingRate")          # e.g. 0.0001 = 0.01%
    funding_timestamp = funding.get("fundingTimestamp") or funding.get("timestamp")
    perp_mark_price = funding.get("markPrice") or funding.get("indexPrice")
    spot_price = spot_ticker.get("last")

    if funding_rate is None or spot_price is None or perp_mark_price is None:
        return None

    annualized_pct = funding_rate * FUNDING_PERIODS_PER_YEAR * 100
    basis_pct = ((perp_mark_price - spot_price) / spot_price) * 100

    return {
        "funding_rate_pct": funding_rate * 100,
        "annualized_pct": annualized_pct,
        "spot_price": spot_price,
        "perp_mark_price": perp_mark_price,
        "basis_pct": basis_pct,
        "funding_timestamp": funding_timestamp,
    }


# ---------------------------------------------------------------------------
# Main scan loop
# ---------------------------------------------------------------------------

def main():
    perp_exchange = getattr(ccxt, PERP_EXCHANGE_ID)({"enableRateLimit": True})
    spot_exchange = getattr(ccxt, SPOT_EXCHANGE_ID)({"enableRateLimit": True})

    perp_exchange.load_markets()
    spot_exchange.load_markets()
    init_csv()

    list_available_perp_symbols(perp_exchange, search="BTC")

    print(f"Watching funding rate: {PERP_SYMBOL} on {PERP_EXCHANGE_ID}")
    print(f"Comparing against spot: {SPOT_SYMBOL} on {SPOT_EXCHANGE_ID}")
    print(f"Entry threshold: {ENTRY_THRESHOLD_ANNUALIZED_PCT}% annualized")
    print(f"Exit rule: {EXIT_CONSECUTIVE_PERIODS} consecutive unfavorable periods")
    print(f"In position: {IN_POSITION}")
    print(f"Logging to: {os.path.abspath(CSV_FILENAME)}\n")

    last_funding_timestamp = None
    consecutive_unfavorable_periods = 0

    while True:
        try:
            data = get_funding_and_prices(perp_exchange, spot_exchange)
            timestamp = time.strftime("%H:%M:%S")

            if data is None:
                print(f"[{timestamp}] Could not fetch complete data, skipping")
                time.sleep(SCAN_INTERVAL_SECONDS)
                continue

            signal = "watching"

            # --- Entry signal: not in position, funding looks attractive ---
            if not IN_POSITION:
                if data["annualized_pct"] > ENTRY_THRESHOLD_ANNUALIZED_PCT:
                    signal = "ENTRY OPPORTUNITY"
                    print(f"[{timestamp}] ENTRY OPPORTUNITY: "
                          f"{data['annualized_pct']:.2f}% annualized "
                          f"(funding {data['funding_rate_pct']:.4f}% this period, "
                          f"basis {data['basis_pct']:.3f}%)")
                else:
                    print(f"[{timestamp}] funding {data['funding_rate_pct']:.4f}%  "
                          f"(~{data['annualized_pct']:.2f}% annualized, below entry threshold)")

            # --- Exit signal: in position, track consecutive bad periods ---
            else:
                is_new_period = data["funding_timestamp"] != last_funding_timestamp
                if is_new_period:
                    last_funding_timestamp = data["funding_timestamp"]
                    if data["funding_rate_pct"] <= 0:
                        consecutive_unfavorable_periods += 1
                    else:
                        consecutive_unfavorable_periods = 0

                print(f"[{timestamp}] funding {data['funding_rate_pct']:.4f}%  "
                      f"(~{data['annualized_pct']:.2f}% annualized) | "
                      f"unfavorable periods in a row: {consecutive_unfavorable_periods}")

                if consecutive_unfavorable_periods >= EXIT_CONSECUTIVE_PERIODS:
                    signal = "EXIT SIGNAL"
                    print(f"[{timestamp}] EXIT SIGNAL: funding unfavorable for "
                          f"{consecutive_unfavorable_periods} consecutive periods")

            log_to_csv([
                time.strftime("%Y-%m-%d"),
                timestamp,
                round(data["funding_rate_pct"], 5),
                round(data["annualized_pct"], 3),
                data["spot_price"],
                data["perp_mark_price"],
                round(data["basis_pct"], 4),
                data["funding_timestamp"],
                signal,
            ])

        except ccxt.NetworkError as e:
            print(f"Network error, retrying: {e}")
        except ccxt.ExchangeError as e:
            print(f"Exchange error: {e}")

        time.sleep(SCAN_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
