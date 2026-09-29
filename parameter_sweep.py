"""
Parameter Sweep: Entry Threshold x Exit Rule
------------------------------------------------
Tests a GRID of entry thresholds and exit rules against the same real
historical funding rate data, to answer: "is there ANY configuration of
this strategy that would have been profitable?" -- rather than continuing
to guess at one threshold at a time.

For each combination, this runs the exact same threshold-gated backtest
logic as backtest_threshold_strategy.py. The best-performing combination
found here is then validated with a full Monte Carlo run, since a single
historical backtest result -- even the best one in a grid -- can still be
a fluke of that particular 90-day window.

Setup:
    pip install ccxt numpy matplotlib

Run:
    python parameter_sweep.py

Output:
    - parameter_sweep_results.csv -- every combination tested, sorted best to worst
    - parameter_sweep_heatmap.png -- visual grid of total return by combination
    - A Monte Carlo validation of the single best combination found
"""

import csv
import time
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import ccxt


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PERP_EXCHANGE_ID = "krakenfutures"
PERP_SYMBOL = "BTC/USD:USD"
LOOKBACK_DAYS = 90

FUNDING_PERIODS_PER_YEAR = 365 * 3
ROUND_TRIP_FEE_PCT = 0.20

# The grid being swept
ENTRY_THRESHOLDS_TO_TEST = [1, 2, 3, 5, 7, 10, 15, 20, 30, 50]   # % annualized
EXIT_PERIODS_TO_TEST = [1, 2, 3, 4, 5]                            # consecutive periods

# Monte Carlo validation of the best combo found
N_SIMULATIONS = 5000
HORIZON_PERIODS = 90
BLOCK_SIZE = 10

CSV_HISTORY_FILENAME = "funding_rate_history.csv"
CSV_SWEEP_FILENAME = "parameter_sweep_results.csv"
HEATMAP_FILENAME = "parameter_sweep_heatmap.png"
MC_CHART_FILENAME = "best_combo_monte_carlo.png"


# ---------------------------------------------------------------------------
# Data fetching (same as previous scripts)
# ---------------------------------------------------------------------------

def fetch_historical_funding_rates(exchange, symbol, lookback_days):
    since = exchange.milliseconds() - (lookback_days * 24 * 60 * 60 * 1000)
    all_records = []
    while True:
        batch = exchange.fetch_funding_rate_history(symbol, since=since, limit=200)
        if not batch:
            break
        all_records.extend(batch)
        since = batch[-1]["timestamp"] + 1
        if len(batch) < 200:
            break
        time.sleep(exchange.rateLimit / 1000)
    all_records.sort(key=lambda r: r["timestamp"])
    return all_records


def save_history_to_csv(records):
    with open(CSV_HISTORY_FILENAME, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "datetime", "funding_rate_pct"])
        for r in records:
            writer.writerow([r["timestamp"], r.get("datetime", ""),
                              round((r.get("fundingRate") or 0) * 100, 6)])


# ---------------------------------------------------------------------------
# Core strategy logic (same as backtest_threshold_strategy.py)
# ---------------------------------------------------------------------------

def run_threshold_strategy(funding_rates_pct, entry_threshold_annualized,
                            exit_consecutive_periods, periods_per_year, fee_pct):
    in_position = False
    consecutive_unfavorable = 0
    entry_fee = fee_pct / 2
    exit_fee = fee_pct / 2
    cumulative_pct = 0.0
    trades = []
    current_trade_start = None
    current_trade_pnl = 0.0

    for i, rate_pct in enumerate(funding_rates_pct):
        annualized = rate_pct * periods_per_year
        if not in_position:
            if annualized > entry_threshold_annualized:
                in_position = True
                consecutive_unfavorable = 0
                current_trade_start = i
                current_trade_pnl = -entry_fee
                cumulative_pct -= entry_fee
        else:
            current_trade_pnl += rate_pct
            cumulative_pct += rate_pct
            consecutive_unfavorable = consecutive_unfavorable + 1 if rate_pct <= 0 else 0
            if consecutive_unfavorable >= exit_consecutive_periods:
                current_trade_pnl -= exit_fee
                cumulative_pct -= exit_fee
                trades.append({"pnl_pct": current_trade_pnl,
                                "duration_periods": i - current_trade_start + 1})
                in_position = False

    if in_position:
        current_trade_pnl -= exit_fee
        cumulative_pct -= exit_fee
        trades.append({"pnl_pct": current_trade_pnl,
                        "duration_periods": len(funding_rates_pct) - current_trade_start})

    return {"total_return_pct": cumulative_pct, "num_trades": len(trades), "trades": trades}


def build_block_bootstrap_path(rates_array, horizon_periods, block_size):
    path = []
    max_start = len(rates_array) - block_size
    while len(path) < horizon_periods:
        start = np.random.randint(0, max_start + 1)
        path.extend(rates_array[start:start + block_size])
    return path[:horizon_periods]


# ---------------------------------------------------------------------------
# The sweep itself
# ---------------------------------------------------------------------------

def run_sweep(funding_rates_pct):
    results = []
    for entry_threshold in ENTRY_THRESHOLDS_TO_TEST:
        for exit_periods in EXIT_PERIODS_TO_TEST:
            outcome = run_threshold_strategy(
                funding_rates_pct, entry_threshold, exit_periods,
                FUNDING_PERIODS_PER_YEAR, ROUND_TRIP_FEE_PCT
            )
            results.append({
                "entry_threshold_pct": entry_threshold,
                "exit_periods": exit_periods,
                "total_return_pct": outcome["total_return_pct"],
                "num_trades": outcome["num_trades"],
            })
    return results


def save_sweep_results(results):
    sorted_results = sorted(results, key=lambda r: r["total_return_pct"], reverse=True)
    with open(CSV_SWEEP_FILENAME, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["entry_threshold_pct", "exit_periods",
                                                 "total_return_pct", "num_trades"])
        writer.writeheader()
        writer.writerows(sorted_results)
    return sorted_results


def plot_heatmap(results):
    grid = np.zeros((len(EXIT_PERIODS_TO_TEST), len(ENTRY_THRESHOLDS_TO_TEST)))
    for r in results:
        row = EXIT_PERIODS_TO_TEST.index(r["exit_periods"])
        col = ENTRY_THRESHOLDS_TO_TEST.index(r["entry_threshold_pct"])
        grid[row, col] = r["total_return_pct"]

    max_abs = max(abs(grid.min()), abs(grid.max()), 0.01)
    plt.figure(figsize=(10, 5))
    im = plt.imshow(grid, cmap="RdYlGn", vmin=-max_abs, vmax=max_abs, aspect="auto")
    plt.colorbar(im, label="Total return (%)")
    plt.xticks(range(len(ENTRY_THRESHOLDS_TO_TEST)),
               [f"{t}%" for t in ENTRY_THRESHOLDS_TO_TEST])
    plt.yticks(range(len(EXIT_PERIODS_TO_TEST)), EXIT_PERIODS_TO_TEST)
    plt.xlabel("Entry threshold (annualized %)")
    plt.ylabel("Exit: consecutive unfavorable periods")
    plt.title(f"Parameter sweep: total return %  (green = profitable, red = loss)")
    for row in range(grid.shape[0]):
        for col in range(grid.shape[1]):
            plt.text(col, row, f"{grid[row, col]:.2f}", ha="center", va="center", fontsize=8)
    plt.tight_layout()
    plt.savefig(HEATMAP_FILENAME, dpi=150)
    print(f"Saved heatmap to {HEATMAP_FILENAME}")


def validate_best_combo(funding_rates_pct, best):
    print(f"\nValidating best combo with Monte Carlo: "
          f"entry={best['entry_threshold_pct']}%, exit={best['exit_periods']} periods")
    rates_array = np.array(funding_rates_pct)
    results = np.zeros(N_SIMULATIONS)
    for i in range(N_SIMULATIONS):
        path = build_block_bootstrap_path(rates_array, HORIZON_PERIODS, BLOCK_SIZE)
        outcome = run_threshold_strategy(path, best["entry_threshold_pct"],
                                          best["exit_periods"],
                                          FUNDING_PERIODS_PER_YEAR, ROUND_TRIP_FEE_PCT)
        results[i] = outcome["total_return_pct"]

    prob_of_loss = (results < 0).mean() * 100
    print(f"Median: {np.percentile(results, 50):.4f}%  |  "
          f"5th pct: {np.percentile(results, 5):.4f}%  |  "
          f"95th pct: {np.percentile(results, 95):.4f}%  |  "
          f"Prob of loss: {prob_of_loss:.1f}%")

    plt.figure(figsize=(9, 5))
    plt.hist(results, bins=60, color="#3B82C4", edgecolor="white")
    plt.axvline(0, color="black", linestyle="--", linewidth=1, label="break-even")
    plt.axvline(np.median(results), color="darkorange", linewidth=1.5,
                label=f"median ({np.median(results):.3f}%)")
    plt.title(f"Best combo Monte Carlo: entry={best['entry_threshold_pct']}%, "
              f"exit={best['exit_periods']} periods")
    plt.xlabel("Total return (%)")
    plt.ylabel("Number of simulations")
    plt.legend()
    plt.tight_layout()
    plt.savefig(MC_CHART_FILENAME, dpi=150)
    print(f"Saved chart to {MC_CHART_FILENAME}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    exchange = getattr(ccxt, PERP_EXCHANGE_ID)({"enableRateLimit": True})
    exchange.load_markets()

    print(f"Fetching {LOOKBACK_DAYS} days of funding rate history...")
    records = fetch_historical_funding_rates(exchange, PERP_SYMBOL, LOOKBACK_DAYS)
    if not records:
        print("No historical data returned.")
        return
    save_history_to_csv(records)
    funding_rates_pct = [(r.get("fundingRate") or 0) * 100 for r in records]
    print(f"Pulled {len(funding_rates_pct)} periods\n")

    total_combos = len(ENTRY_THRESHOLDS_TO_TEST) * len(EXIT_PERIODS_TO_TEST)
    print(f"Testing {total_combos} combinations "
          f"({len(ENTRY_THRESHOLDS_TO_TEST)} thresholds x {len(EXIT_PERIODS_TO_TEST)} exit rules)...")
    results = run_sweep(funding_rates_pct)
    sorted_results = save_sweep_results(results)

    print(f"\nSaved all results to {CSV_SWEEP_FILENAME}\n")
    print("=== TOP 5 COMBINATIONS ===")
    for r in sorted_results[:5]:
        print(f"entry={r['entry_threshold_pct']}%  exit={r['exit_periods']} periods  "
              f"-> {r['total_return_pct']:.4f}%  ({r['num_trades']} trades)")

    profitable = [r for r in sorted_results if r["total_return_pct"] > 0]
    print(f"\nProfitable combinations out of {total_combos}: {len(profitable)}")

    plot_heatmap(results)

    best = sorted_results[0]
    validate_best_combo(funding_rates_pct, best)


if __name__ == "__main__":
    main()
