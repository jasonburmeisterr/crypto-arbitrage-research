"""
Funding Rate Arbitrage: Backtest + Monte Carlo Simulation
-------------------------------------------------------------
Two separate analyses, built on the same historical funding rate data:

1. BACKTEST: replays actual historical funding payments in their real
   chronological order and computes what your cumulative return would
   have been, net of estimated fees.

2. MONTE CARLO: bootstrap-resamples those same historical per-period
   funding rates (randomly, with replacement) to generate many possible
   alternate outcomes, producing a distribution instead of one number.
   This answers "what's the range of plausible outcomes" rather than
   "what happened that one specific time."

Setup:
    pip install ccxt numpy matplotlib

Run:
    python backtest_funding_arbitrage.py

Output:
    - Printed backtest summary (total return, best/worst period, etc.)
    - Printed Monte Carlo summary (median, 5th/95th percentile, prob. of loss)
    - monte_carlo_distribution.png -- a histogram of simulated outcomes
    - funding_rate_history.csv -- the raw historical data used, for your
      own inspection in Excel
"""

import csv
import time
import numpy as np
import matplotlib
matplotlib.use("Agg")  # no display needed, just save to file
import matplotlib.pyplot as plt
import ccxt


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PERP_EXCHANGE_ID = "krakenfutures"
PERP_SYMBOL = "BTC/USD:USD"
LOOKBACK_DAYS = 90                   # how much history to pull

# Fee assumptions: opening AND closing both legs (spot buy/sell + perp
# short/cover) costs money. This is a rough combined estimate -- adjust
# to match your actual exchange fee tiers.
ROUND_TRIP_FEE_PCT = 0.20             # e.g. 0.20% total to open + close both legs

# Monte Carlo settings
N_SIMULATIONS = 5000                  # number of alternate histories to generate
HORIZON_PERIODS = 90                  # how many funding periods to simulate forward
                                       # (e.g. if funding is every 8h, 90 periods = 30 days)

CSV_FILENAME = "funding_rate_history.csv"
CHART_FILENAME = "monte_carlo_distribution.png"


# ---------------------------------------------------------------------------
# Step 1: Pull historical funding rate data
# ---------------------------------------------------------------------------

def fetch_historical_funding_rates(exchange, symbol, lookback_days):
    """Fetch historical funding rate records via ccxt's unified method.
    Returns a list of dicts sorted oldest -> newest."""
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
        time.sleep(exchange.rateLimit / 1000)  # be polite to the API

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
# Step 2: Backtest -- replay history in its real chronological order
# ---------------------------------------------------------------------------

def run_backtest(funding_rates_pct):
    """
    funding_rates_pct: list of per-period funding rates, as percentages
    (e.g. 0.001 means 0.001%, already *100 from the raw decimal).

    Returns a summary dict. This assumes you open the position once at
    the start and hold it through the entire historical window, paying
    the round-trip fee once at entry and once at exit.
    """
    cumulative_pct = -ROUND_TRIP_FEE_PCT / 2  # half the round-trip fee, paid at entry

    equity_curve = [cumulative_pct]
    for rate in funding_rates_pct:
        cumulative_pct += rate
        equity_curve.append(cumulative_pct)

    cumulative_pct -= ROUND_TRIP_FEE_PCT / 2  # remaining half, paid at exit
    equity_curve.append(cumulative_pct)

    return {
        "total_return_pct": cumulative_pct,
        "num_periods": len(funding_rates_pct),
        "best_period_pct": max(funding_rates_pct) if funding_rates_pct else None,
        "worst_period_pct": min(funding_rates_pct) if funding_rates_pct else None,
        "avg_period_pct": np.mean(funding_rates_pct) if funding_rates_pct else None,
        "equity_curve": equity_curve,
    }


# ---------------------------------------------------------------------------
# Step 3: Monte Carlo -- bootstrap resample to generate a distribution
# ---------------------------------------------------------------------------

def run_monte_carlo(funding_rates_pct, n_simulations, horizon_periods):
    """
    Randomly resamples (with replacement) from the historical per-period
    funding rates to build many alternate possible paths, each of length
    `horizon_periods`. Returns the array of simulated total returns.
    """
    rates_array = np.array(funding_rates_pct)
    entry_fee = ROUND_TRIP_FEE_PCT / 2
    exit_fee = ROUND_TRIP_FEE_PCT / 2

    # Draw a (n_simulations x horizon_periods) grid of random historical
    # rates, each drawn independently with replacement -- this is the
    # "bootstrap" step. Then sum each row to get one simulated total return.
    sampled = np.random.choice(rates_array, size=(n_simulations, horizon_periods), replace=True)
    simulated_totals = sampled.sum(axis=1) - entry_fee - exit_fee

    return simulated_totals


def summarize_monte_carlo(simulated_totals):
    return {
        "median_pct": np.percentile(simulated_totals, 50),
        "p5_pct": np.percentile(simulated_totals, 5),
        "p95_pct": np.percentile(simulated_totals, 95),
        "prob_of_loss": (simulated_totals < 0).mean() * 100,
        "mean_pct": simulated_totals.mean(),
        "std_pct": simulated_totals.std(),
    }


def plot_distribution(simulated_totals, filename):
    plt.figure(figsize=(9, 5))
    plt.hist(simulated_totals, bins=60, color="#3B82C4", edgecolor="white")
    plt.axvline(0, color="black", linestyle="--", linewidth=1, label="break-even")
    plt.axvline(np.median(simulated_totals), color="darkorange", linewidth=1.5,
                label=f"median ({np.median(simulated_totals):.2f}%)")
    plt.title(f"Monte Carlo: simulated {HORIZON_PERIODS}-period returns "
              f"({N_SIMULATIONS:,} simulations)")
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
        print("No historical funding rate data returned. Try a different "
              "symbol, exchange, or check that fetch_funding_rate_history "
              "is supported for this market.")
        return

    save_history_to_csv(records)
    print(f"Pulled {len(records)} historical funding periods, saved to {CSV_FILENAME}\n")

    funding_rates_pct = [(r.get("fundingRate") or 0) * 100 for r in records]

    # --- Backtest ---
    backtest = run_backtest(funding_rates_pct)
    print("=== BACKTEST (actual historical order) ===")
    print(f"Periods:          {backtest['num_periods']}")
    print(f"Total return:     {backtest['total_return_pct']:.3f}%  "
          f"(after {ROUND_TRIP_FEE_PCT}% round-trip fee)")
    print(f"Average period:   {backtest['avg_period_pct']:.5f}%")
    print(f"Best period:      {backtest['best_period_pct']:.5f}%")
    print(f"Worst period:     {backtest['worst_period_pct']:.5f}%\n")

    # --- Monte Carlo ---
    print(f"Running {N_SIMULATIONS:,} Monte Carlo simulations over "
          f"{HORIZON_PERIODS} periods each...")
    simulated_totals = run_monte_carlo(funding_rates_pct, N_SIMULATIONS, HORIZON_PERIODS)
    mc_summary = summarize_monte_carlo(simulated_totals)

    print("\n=== MONTE CARLO (bootstrap-resampled scenarios) ===")
    print(f"Median outcome:        {mc_summary['median_pct']:.3f}%")
    print(f"Mean outcome:          {mc_summary['mean_pct']:.3f}%")
    print(f"5th percentile:        {mc_summary['p5_pct']:.3f}%  (a bad-case scenario)")
    print(f"95th percentile:       {mc_summary['p95_pct']:.3f}%  (a good-case scenario)")
    print(f"Std deviation:         {mc_summary['std_pct']:.3f}%")
    print(f"Probability of loss:   {mc_summary['prob_of_loss']:.1f}%")

    plot_distribution(simulated_totals, CHART_FILENAME)


if __name__ == "__main__":
    main()
