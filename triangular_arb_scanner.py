"""
Triangular Arbitrage Scanner
------------------------------
Scans a single exchange for triangular arbitrage opportunities across
a loop of three trading pairs, e.g. USDT -> BTC -> ETH -> USDT.

This is a READ-ONLY scanner: it does not place trades. It's meant to
teach you the data plumbing, fee-aware math, and how to reason about
opportunity size, before you ever consider building execution logic.

Setup:
    pip install ccxt

Run:
    python triangular_arb_scanner.py
"""

import csv
import os
import time
import ccxt


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

EXCHANGE_ID = "kraken"           # US-accessible; binance.com blocks US IPs (HTTP 451)
TAKER_FEE = 0.0026               # Kraken's standard taker fee is ~0.26%; check your fee tier
STARTING_CAPITAL_USDT = 1000.0   # hypothetical amount, used only for sizing the printout
MIN_PROFIT_PCT = 0.05            # only report opportunities above this % (after fees)
SCAN_INTERVAL_SECONDS = 5

# CSV logging: every scan gets written here, so you can leave this running
# unattended and review the full session afterward in Excel/Google Sheets.
CSV_FILENAME = "arb_scan_log.csv"
CSV_HEADERS = [
    "date", "time", "direction", "profit_pct", "is_opportunity",
    "final_usdt", "leg1_symbol", "leg1_price",
    "leg2_symbol", "leg2_price", "leg3_symbol", "leg3_price",
]

# Define the triangle as three pairs. The loop is:
#   USD -> BTC   (buy BTC with USD)   using BTC/USD
#   BTC -> ETH   (buy ETH with BTC)   using ETH/BTC
#   ETH -> USD   (sell ETH for USD)   using ETH/USD
TRIANGLE = {
    "leg1": "BTC/USD",  # base/quote
    "leg2": "ETH/BTC",
    "leg3": "ETH/USD",
}


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def get_best_prices(exchange, symbol):
    """Fetch best bid/ask for a symbol from the order book (top of book)."""
    order_book = exchange.fetch_order_book(symbol, limit=5)
    best_bid = order_book["bids"][0][0] if order_book["bids"] else None
    best_ask = order_book["asks"][0][0] if order_book["asks"] else None
    return best_bid, best_ask


def compute_triangle_return(exchange):
    """
    Walk the triangle USDT -> BTC -> ETH -> USDT and compute the net
    multiplier after fees. A result > 1.0 means a profitable loop.
    """
    btc_usdt_bid, btc_usdt_ask = get_best_prices(exchange, TRIANGLE["leg1"])
    eth_btc_bid, eth_btc_ask = get_best_prices(exchange, TRIANGLE["leg2"])
    eth_usdt_bid, eth_usdt_ask = get_best_prices(exchange, TRIANGLE["leg3"])

    if None in (btc_usdt_ask, eth_btc_ask, eth_usdt_bid):
        return None  # missing liquidity on one side, skip this cycle

    capital = STARTING_CAPITAL_USDT

    # Step 1: buy BTC with USDT (pay the ask, since we're buying)
    btc_amount = (capital / btc_usdt_ask) * (1 - TAKER_FEE)

    # Step 2: buy ETH with BTC (pay the ask)
    eth_amount = (btc_amount / eth_btc_ask) * (1 - TAKER_FEE)

    # Step 3: sell ETH for USDT (receive the bid, since we're selling)
    final_usdt = (eth_amount * eth_usdt_bid) * (1 - TAKER_FEE)

    net_multiplier = final_usdt / capital
    profit_pct = (net_multiplier - 1) * 100

    return {
        "final_usdt": final_usdt,
        "profit_pct": profit_pct,
        "prices": {
            TRIANGLE["leg1"]: btc_usdt_ask,
            TRIANGLE["leg2"]: eth_btc_ask,
            TRIANGLE["leg3"]: eth_usdt_bid,
        },
    }


def compute_reverse_triangle_return(exchange):
    """
    Also check the reverse loop: USDT -> ETH -> BTC -> USDT.
    Arbitrage can appear in either direction.
    """
    eth_usdt_bid, eth_usdt_ask = get_best_prices(exchange, TRIANGLE["leg3"])
    eth_btc_bid, eth_btc_ask = get_best_prices(exchange, TRIANGLE["leg2"])
    btc_usdt_bid, btc_usdt_ask = get_best_prices(exchange, TRIANGLE["leg1"])

    if None in (eth_usdt_ask, eth_btc_bid, btc_usdt_bid):
        return None

    capital = STARTING_CAPITAL_USDT

    # Step 1: buy ETH with USDT
    eth_amount = (capital / eth_usdt_ask) * (1 - TAKER_FEE)

    # Step 2: sell ETH for BTC
    btc_amount = (eth_amount * eth_btc_bid) * (1 - TAKER_FEE)

    # Step 3: sell BTC for USDT
    final_usdt = (btc_amount * btc_usdt_bid) * (1 - TAKER_FEE)

    net_multiplier = final_usdt / capital
    profit_pct = (net_multiplier - 1) * 100

    return {
        "final_usdt": final_usdt,
        "profit_pct": profit_pct,
        "prices": {
            TRIANGLE["leg3"]: eth_usdt_ask,
            TRIANGLE["leg2"]: eth_btc_bid,
            TRIANGLE["leg1"]: btc_usdt_bid,
        },
    }


def init_csv():
    """Create the CSV with headers if it doesn't already exist."""
    if not os.path.exists(CSV_FILENAME):
        with open(CSV_FILENAME, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(CSV_HEADERS)


def log_result_to_csv(direction, result):
    """Append one scan result (forward or reverse) as a row in the CSV."""
    if result is None:
        return

    now = time.localtime()
    prices = result["prices"]
    # prices dict preserves insertion order matching leg1/leg2/leg3 for that direction
    price_items = list(prices.items())

    row = [
        time.strftime("%Y-%m-%d", now),
        time.strftime("%H:%M:%S", now),
        direction,
        round(result["profit_pct"], 5),
        result["profit_pct"] > MIN_PROFIT_PCT,
        round(result["final_usdt"], 2),
        price_items[0][0], price_items[0][1],
        price_items[1][0], price_items[1][1],
        price_items[2][0], price_items[2][1],
    ]

    with open(CSV_FILENAME, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(row)


# ---------------------------------------------------------------------------
# Main scan loop
# ---------------------------------------------------------------------------

def main():
    exchange = getattr(ccxt, EXCHANGE_ID)({"enableRateLimit": True})
    exchange.load_markets()
    init_csv()

    print(f"Scanning {EXCHANGE_ID} for triangular arbitrage: "
          f"USDT <-> {TRIANGLE['leg1']} <-> {TRIANGLE['leg2']} <-> {TRIANGLE['leg3']}")
    print(f"Fee assumption: {TAKER_FEE * 100:.3f}% per trade "
          f"({TAKER_FEE * 3 * 100:.3f}% total for 3 legs)")
    print(f"Reporting threshold: {MIN_PROFIT_PCT:.2f}% net profit")
    print(f"Logging every scan to: {os.path.abspath(CSV_FILENAME)}\n")

    while True:
        try:
            forward = compute_triangle_return(exchange)
            reverse = compute_reverse_triangle_return(exchange)

            timestamp = time.strftime("%H:%M:%S")

            if forward and forward["profit_pct"] > MIN_PROFIT_PCT:
                print(f"[{timestamp}] FORWARD opportunity: "
                      f"{forward['profit_pct']:.4f}% net profit | "
                      f"prices={forward['prices']}")
            elif forward:
                print(f"[{timestamp}] forward: {forward['profit_pct']:+.4f}%  "
                      f"(below threshold)")

            if reverse and reverse["profit_pct"] > MIN_PROFIT_PCT:
                print(f"[{timestamp}] REVERSE opportunity: "
                      f"{reverse['profit_pct']:.4f}% net profit | "
                      f"prices={reverse['prices']}")
            elif reverse:
                print(f"[{timestamp}] reverse: {reverse['profit_pct']:+.4f}%  "
                      f"(below threshold)")

            log_result_to_csv("forward", forward)
            log_result_to_csv("reverse", reverse)

        except ccxt.NetworkError as e:
            print(f"Network error, retrying: {e}")
        except ccxt.ExchangeError as e:
            print(f"Exchange error: {e}")

        time.sleep(SCAN_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
