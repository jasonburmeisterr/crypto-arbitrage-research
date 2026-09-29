"""
Funding Rate Arbitrage: THRESHOLD-GATED Backtest + Block Bootstrap Monte Carlo
--------------------------------------------------------------------------------
This is an upgrade over the naive "always in the trade" version. It tests the
ACTUAL rule your scanner implements:

  - ENTER only when annualized funding crosses ENTRY_THRESHOLD_ANNUALIZED_PCT
  - EXIT after EXIT_CONSECUTIVE_PERIODS consecutive unfavorable periods
  - Pay the round-trip fee split across each entry/exit, not just once overall
  - While out of position, you earn nothing and risk nothing (sit in cash)

The Monte Carlo step also upgrades from independent random draws to a BLOCK
BOOTSTRAP: instead of shuffling individual periods (which assumes no memory
between periods), it resamples contiguous chunks of real history. This better
preserves the tendency for calm or crowded market conditions to persist for
a while, rather than flickering randomly period to period.

Setup:
    pip install ccxt numpy matplotlib

Run:
    python backtest_threshold_strategy.py
"""

import csv
import time
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import ccxt


# ---------------------------------------------------------------------------
# Configuration -- should match your live scanner's settings
# ---------------------------------------------------------------------------

PERP_EXCHANGE_ID = "krakenfutures"
PERP_SYMBOL = "BTC/USD:USD"
LOOKBACK_DAYS = 90

FUNDING_PERIODS_PER_YEAR = 365 * 3    # adjust to match your contract's actual interval
ROUND_TRIP_FEE_PCT = 0.20             # total fee to open + close both legs

ENTRY_THRESHOLD_ANNUALIZED_PCT = 1.0  # matches what you're currently testing live
EXIT_CONSECUTIVE_PERIODS = 2

# Monte Carlo settings
N_SIMULATIONS = 5000
HORIZON_PERIODS = 90
BLOCK_SIZE = 10   # periods per resampled chunk -- preserves short-term persistence

CSV_FILENAME = "funding_rate_history.csv"
CHART_FILENAME = "monte_carlo_threshold_distribution.png"


# ---------------------------------------------------------------------------
# Step 1: Pull historical funding rate data (same as before)
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
    with open(CSV_FILENAME, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "datetime", "funding_rate_pct"])
        for r in records:
            writer.writerow([
                r["timestamp"],
                r.get("datetime", ""),
                round((r.get("fundingRate") or 0) * 100, 6),
            ])


# ---------------------------------------------------------------------------
# Step 2: Threshold-gated backtest engine
# ---------------------------------------------------------------------------

def run_threshold_strategy(funding_rates_pct, entry_threshold_annualized,
                            exit_consecutive_periods, periods_per_year, fee_pct):
    """
    Walks through funding_rates_pct in order, entering/exiting according to
    the same rule your live scanner uses. Returns total return and a log of
    individual trades, so you can see exactly when it entered and exited.
    """
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

            if rate_pct <= 0:
                consecutive_unfavorable += 1
            else:
                consecutive_unfavorable = 0

            if consecutive_unfavorable >= exit_consecutive_periods:
                current_trade_pnl -= exit_fee
                cumulative_pct -= exit_fee
                trades.append({
                    "start_period": current_trade_start,
                    "end_period": i,
                    "duration_periods": i - current_trade_start + 1,
                    "pnl_pct": current_trade_pnl,
                })
                in_position = False

    # If still in a position at the end of the data, close it out for accounting
    if in_position:
        current_trade_pnl -= exit_fee
        cumulative_pct -= exit_fee
        trades.append({
            "start_period": current_trade_start,
            "end_period": len(funding_rates_pct) - 1,
            "duration_periods": len(funding_rates_pct) - current_trade_start,
            "pnl_pct": current_trade_pnl,
        })

    periods_in_position = sum(t["duration_periods"] for t in trades)
    time_in_position_pct = (periods_in_position / len(funding_rates_pct) * 100
                             if funding_rates_pct else 0)

    return {
        "total_return_pct": cumulative_pct,
        "num_trades": len(trades),
        "trades": trades,
        "time_in_position_pct": time_in_position_pct,
    }


# ---------------------------------------------------------------------------
# Step 3: Block bootstrap Monte Carlo
# ---------------------------------------------------------------------------

def build_block_bootstrap_path(rates_array, horizon_periods, block_size):
    """Builds one synthetic path by stitching together randomly chosen
    contiguous blocks of real historical data, until reaching horizon_periods."""
    path = []
    max_start = len(rates_array) - block_size
    while len(path) < horizon_periods:
        start = np.random.randint(0, max_start + 1)
        block = rates_array[start:start + block_size]
        path.extend(block)
    return path[:horizon_periods]


def run_monte_carlo_threshold(funding_rates_pct, n_simulations, horizon_periods,
                               block_size, entry_threshold, exit_periods,
                               periods_per_year, fee_pct):
    rates_array = np.array(funding_rates_pct)
    results = np.zeros(n_simulations)

    for i in range(n_simulations):
        path = build_block_bootstrap_path(rates_array, horizon_periods, block_size)
        outcome = run_threshold_strategy(path, entry_threshold, exit_periods,
                                          periods_per_year, fee_pct)
        results[i] = outcome["total_return_pct"]

    return results


def summarize_monte_carlo(simulated_totals):
    return {
        "median_pct": np.percentile(simulated_totals, 50),
        "p5_pct": np.percentile(simulated_totals, 5),
        "p95_pct": np.percentile(simulated_totals, 95),
        "prob_of_loss": (simulated_totals < 0).mean() * 100,
        "prob_of_zero_trades": (simulated_totals == 0).mean() * 100,
        "mean_pct": simulated_totals.mean(),
        "std_pct": simulated_totals.std(),
    }


def plot_distribution(simulated_totals, filename):
    plt.figure(figsize=(9, 5))
    plt.hist(simulated_totals, bins=60, color="#3B82C4", edgecolor="white")
    plt.axvline(0, color="black", linestyle="--", linewidth=1, label="break-even")
    plt.axvline(np.median(simulated_totals), color="darkorange", linewidth=1.5,
                label=f"median ({np.median(simulated_totals):.3f}%)")
    plt.title(f"Threshold-gated strategy: {HORIZON_PERIODS}-period outcomes "
              f"({N_SIMULATIONS:,} block-bootstrap simulations)")
    plt.xlabel("Total return (%)")
    plt.ylabel("Number of simulations")
    plt.legend()
    plt.tight_layout()
    plt.savefig(filename, dpi=150)
    print(f"Saved chart to {filename}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    exchange = getattr(ccxt, PERP_EXCHANGE_ID)({"enableRateLimit": True})
    exchange.load_markets()

    print(f"Fetching {LOOKBACK_DAYS} days of funding rate history for "
          f"{PERP_SYMBOL} on {PERP_EXCHANGE_ID}...")
    records = fetch_historical_funding_rates(exchange, PERP_SYMBOL, LOOKBACK_DAYS)

    if not records:
        print("No historical funding rate data returned.")
        return

    save_history_to_csv(records)
    funding_rates_pct = [(r.get("fundingRate") or 0) * 100 for r in records]
    print(f"Pulled {len(funding_rates_pct)} historical funding periods\n")

    # --- Threshold-gated backtest on real history ---
    result = run_threshold_strategy(
        funding_rates_pct, ENTRY_THRESHOLD_ANNUALIZED_PCT,
        EXIT_CONSECUTIVE_PERIODS, FUNDING_PERIODS_PER_YEAR, ROUND_TRIP_FEE_PCT
    )

    print("=== THRESHOLD-GATED BACKTEST (actual historical order) ===")
    print(f"Entry threshold:      {ENTRY_THRESHOLD_ANNUALIZED_PCT}% annualized")
    print(f"Exit rule:            {EXIT_CONSECUTIVE_PERIODS} consecutive unfavorable periods")
    print(f"Total return:         {result['total_return_pct']:.4f}%")
    print(f"Number of trades:     {result['num_trades']}")
    print(f"Time in position:     {result['time_in_position_pct']:.1f}% of the window")
    if result["trades"]:
        avg_trade_pnl = np.mean([t["pnl_pct"] for t in result["trades"]])
        avg_duration = np.mean([t["duration_periods"] for t in result["trades"]])
        print(f"Avg trade PnL:        {avg_trade_pnl:.4f}%")
        print(f"Avg trade duration:   {avg_duration:.1f} periods")
    print()

    # --- Block bootstrap Monte Carlo of the SAME threshold rule ---
    print(f"Running {N_SIMULATIONS:,} block-bootstrap simulations "
          f"(block size {BLOCK_SIZE}, horizon {HORIZON_PERIODS} periods)...")
    simulated_totals = run_monte_carlo_threshold(
        funding_rates_pct, N_SIMULATIONS, HORIZON_PERIODS, BLOCK_SIZE,
        ENTRY_THRESHOLD_ANNUALIZED_PCT, EXIT_CONSECUTIVE_PERIODS,
        FUNDING_PERIODS_PER_YEAR, ROUND_TRIP_FEE_PCT
    )
    mc_summary = summarize_monte_carlo(simulated_totals)

    print("\n=== MONTE CARLO of the threshold-gated strategy ===")
    print(f"Median outcome:        {mc_summary['median_pct']:.4f}%")
    print(f"Mean outcome:          {mc_summary['mean_pct']:.4f}%")
    print(f"5th percentile:        {mc_summary['p5_pct']:.4f}%")
    print(f"95th percentile:       {mc_summary['p95_pct']:.4f}%")
    print(f"Std deviation:         {mc_summary['std_pct']:.4f}%")
    print(f"Probability of loss:   {mc_summary['prob_of_loss']:.1f}%")
    print(f"Simulations w/ 0 trades (threshold never hit): {mc_summary['prob_of_zero_trades']:.1f}%")

    plot_distribution(simulated_totals, CHART_FILENAME)


if __name__ == "__main__":
    main()
