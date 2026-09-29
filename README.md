# Crypto Arbitrage Research: Triangular & Funding Rate Strategies

A research project testing two classic crypto arbitrage strategies against real market data, to determine whether either offered an exploitable edge net of fees. Both were rejected — this repo documents the methodology and the honest reasoning behind that conclusion, rather than presenting a "profitable" bot that hasn't actually been proven to work.

> This is a research and learning project, not a live trading system. Nothing here places real trades. It exists to demonstrate rigorous testing of a trading hypothesis: build it, measure it, and reject it if the data says to.

## Summary of findings

| Strategy | Method | Result |
|---|---|---|
| Triangular arbitrage (BTC/ETH/USD, Kraken) | Live scan, 2,074 samples | Every sample was unprofitable. Best case: **-0.73%**. Worst: **-0.87%**. Fee drag consistently exceeded any pricing mismatch. |
| Funding rate arbitrage (BTC perpetual, Kraken Futures) | Historical backtest, block-bootstrap Monte Carlo, and 50-combination parameter sweep | 0 of 50 entry/exit configurations showed a real edge. The "best" result was +0.003%, from a single trade — statistical noise, not a repeatable strategy. |

**Bottom line: neither strategy cleared its own transaction costs over the tested period, on this exchange, for this asset.** That's a real, defensible conclusion, not a dead end — it's what disciplined testing is supposed to produce most of the time.

## Part 1: Triangular arbitrage

**Hypothesis:** Prices for BTC/USD, ETH/BTC, and ETH/USD on a single exchange occasionally drift out of alignment, creating a loop (USD → BTC → ETH → USD) that returns more than you started with.

**Method:** `triangular_arb_scanner.py` polls Kraken's public order book every 5 seconds, computes the net return of the loop (accounting for taker fees on all 3 legs) in both directions, and logs every scan to CSV.

**Result:** Across 2,074 live scans, the net return after fees never once approached breakeven. The tight clustering of results (-0.73% to -0.87%) indicates a stable, efficient market during the sampling window — not a lack of samples, but a genuine absence of exploitable mispricing. The magnitude of the loss also closely tracked the assumed ~0.78% round-trip fee cost of 3 trades, suggesting fees — not lack of price movement — were the dominant obstacle.

**Files:** `triangular_arb_scanner.py`, `arb_scan_log.csv`

## Part 2: Funding rate arbitrage

**Hypothesis:** A delta-neutral position (long spot BTC + short BTC perpetual futures) collects a funding payment whenever the perpetual trades above spot, generating yield with theoretically minimal price exposure. Unlike triangular arbitrage, only 2 legs are involved instead of 3, and the "opportunity window" is hours, not milliseconds — making it plausible for a non-professional setup to capture.

**Method, in three stages:**

1. **Live monitoring** (`funding_rate_scanner.py`) — watches the current funding rate and basis (perp vs. spot price gap) in real time, alerting when annualized funding crosses a configurable entry threshold, and tracking consecutive unfavorable periods against an exit rule.

2. **Threshold-gated backtest + Monte Carlo** (`backtest_threshold_strategy.py`) — replays real historical funding data (90 days, Kraken Futures) using the exact entry/exit rule from the live scanner, then runs a **block-bootstrap Monte Carlo** (resampling contiguous chunks of real history, not independent random draws, to preserve the tendency for calm or crowded periods to persist) to generate a distribution of plausible outcomes rather than a single historical result.

3. **Parameter sweep** (`parameter_sweep.py`) — tests 50 combinations of entry threshold (1% to 50% annualized) and exit rule (1 to 5 consecutive unfavorable periods) against the same historical data, to check whether *any* configuration — not just the one initially guessed — showed a real edge.

**Results:**

- At a naive "always-in-the-trade" backtest, funding income averaged well below the round-trip fee cost, producing negative returns in **100% of 5,000 Monte Carlo simulations**.
- The threshold-gated version (matching the live scanner's actual logic) also showed a negative median return, with the entire simulated distribution sitting below breakeven.
- The full 50-combination parameter sweep confirmed this wasn't a matter of picking the wrong threshold: thresholds above 7% annualized **never triggered a single trade** in the 90-day window (funding never got that high), thresholds of 1-3% triggered frequently but lost money on fee drag (up to -11.26% at the most aggressive setting), and the only "positive" results (+0.003% to +0.0007%) came from a single lucky trade each — well within noise, not a repeatable signal.

**Files:** `funding_rate_scanner.py`, `backtest_funding_arbitrage.py`, `backtest_threshold_strategy.py`, `parameter_sweep.py`, `funding_rate_history.csv`, `parameter_sweep_results.csv`, `parameter_sweep_heatmap.png`

## Why leverage wouldn't have changed this

A natural question: if the edge is small, doesn't leverage make it worthwhile? No — leverage scales gains and losses by the same factor, since fees and funding payments are both calculated on notional exposure. A -0.555% return becomes roughly a -2.775% return on your posted margin at 5x leverage, not a positive one. Leverage amplifies an existing edge; it cannot create one that doesn't exist, and it adds real liquidation risk that a percentage-return backtest doesn't capture at all.

## What this project demonstrates

- API integration with live exchange data (order books, funding rates) via [ccxt](https://github.com/ccxt/ccxt)
- Fee-aware arbitrage math across multi-leg trades
- Historical backtesting with correct chronological replay (no lookahead bias)
- Monte Carlo simulation via block bootstrap, chosen specifically to avoid the false independence assumption of naive random resampling
- Systematic parameter sweeps to distinguish a real edge from a lucky single data point
- Willingness to reject a strategy based on evidence, rather than deploying capital on a hunch

## Setup

```bash
pip install ccxt numpy matplotlib
```

## Possible next directions

- [ ] Extend the funding rate lookback to a full year, to test whether calmer recent conditions are masking a real edge that appears during more volatile market regimes
- [ ] Repeat the funding rate test on a mid-cap altcoin perpetual, where less competition from professional market makers could leave more exploitable dislocation
- [ ] Pivot to statistical arbitrage / pairs trading, a fundamentally different (mean-reversion based) structure that doesn't face the same fee-per-trade drag

## Disclaimer

This project is for educational and research purposes only. It does not constitute financial advice. No live trades were placed as part of this research, and the conclusion — that neither tested strategy showed a reliable edge over the sampled period — should not be read as a general claim about crypto arbitrage being universally unprofitable, only as an honest result for the specific asset, exchange, and time window tested here.
