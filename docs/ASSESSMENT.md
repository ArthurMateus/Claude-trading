# Does this design make sense? Can it be profitable?

Honest review of the 15-agent pipeline for a ~99% autonomous, low-risk, high-profit crypto swing bot
(holds of minutes to ~4 hours) driven by Claude (Opus 5.5 orchestrator, cheaper sub-agents).

## Verdict in one paragraph

The **architecture is sound**. It follows the same layout institutional systems use: data → many weak
signals → fusion → validation → independent risk → execution → audit → feedback. Fifteen agents are more than a
first version needs, though, and several add little for intraday crypto. **Profitability is possible
but not likely by default.** Fees, slippage, and how hard it is to find a real edge on a 1–4h horizon kill most
retail bots, LLM or not. The design's real value is that it **refuses to trade without evidence**, keeps losses
small while it learns, and leaves a complete record of every decision. Expect weeks of paper trading before you
know whether you have an edge. Nobody can promise one.

## What is good about the design

| Strength | Why it matters |
|---|---|
| Independent Risk + Kill-Switch layers | The most important part. They are code, not prompts, so a bad LLM day can't blow up the account. |
| Backtest → Paper → Live gates | Most failed bots skip these. Here they are enforced by the software. |
| Post-trade journal + learning loop | Without it you can't tell skill from luck, or which agents help. |
| Many weak signals + fusion | Works better than a single "AI predicts price" call, *if* agents are weighted by their measured accuracy. |

## Problems found and how this build handles them

1. **Profit vs. fees.** Alpaca crypto charges roughly 0.15–0.25% per side at the base tier (check the current
   schedule). A round trip with slippage costs about 0.4–0.6%, which is often most of a 2-hour BTC move. The
   three starter setups **fail** the backtest gate on random-walk data for exactly this reason.
   → Every backtest charges taker fees and slippage on both sides. Consider maker (limit) entries or a cheaper
   venue later (see OPEN_QUESTIONS).
2. **LLM judgment can't be backtested honestly.** The models have read the history you would test on, so any
   backtest of their past "calls" leaks the future.
   → LLMs may only trade **named, rule-based setups** that pass a fees-included, out-of-sample backtest. Paper
   trading is the only honest test of the LLM layer.
3. **"1–5% risk per trade" vs "low risk".** An uncalibrated model saying "90% sure" means nothing yet.
   → **Earned sizing.** Every trade risks 1% until a confidence bucket proves over 30+ trades that it wins as
   often as it claims. Only then can it scale toward 5%.
4. **5–8 positions × 5% = up to 40% open risk**, against a 5% daily loss limit.
   → **6% portfolio-heat cap**, further limited to today's remaining loss budget, plus a **correlated-cluster
   cap** (BTC and ETH move together and count as one).
5. **Spot crypto has no leverage**, so with the tight stops a 1–4h trade needs, 1–5% risk would require 100–500%
   of equity in one position. → Each position is capped at **30% of equity**, so real risk per trade is often
   0.3–1% for now. That is safer, but it limits profit. Leverage is an open question.
6. **Alpaca crypto is long-only** and has no bracket orders. → `allow_short: false`. A resting stop order is
   placed at the broker the moment an entry fills, so the stop survives a crash of the bot. Take-profit and
   time-stop exits are managed every cycle.
7. **LLM cost can exceed the profit, especially on a $500 account.** Calling every agent on every asset every
   5 minutes costs about $50/day. → The intelligence layer and Opus only run when a **validated setup fires**,
   per-cycle reviews are off, and the daily budget is **$2** (X/Twitter reads count against it too). Even so,
   $60/month is 12% of $500, so the LLM layer cannot pay for itself at this size. Treat the paper period as a
   paid experiment: the question is whether the LLM layer improves results over the free heuristics
   (`--offline`). Scale capital only if it clearly does.
8. **Prompt injection via news.** Headlines are untrusted text. → Prompts treat third-party text as data, and
   no LLM output can raise risk above the hard limits.
9. **Agents with little value for crypto intraday.** Fundamental (no data source, and fundamentals rarely move
   a 2-hour trade) stays disabled, and options flow (no options data on Alpaca) is reduced to order-book flow.
   Replication follows the system's own best-performing agents and setups. External traders count only after
   their past calls are scored and clear a bar, because most public "alpha" is noise or marketing.
10. **More setups means more chances of a lucky pass.** Eight setups are tested; each needs an out-of-sample
    pass, and real paper results decide in the end.

## Is ~99% autonomy realistic?

Yes, for operation. The bot scans, decides, sizes, executes, exits, journals and learns on its own. The ~1% that
stays human by design:
- switching paper → live (needs an env-var acknowledgement **and** a passing paper gate)
- resetting a hard HALT (drawdown ≥ 15%, runaway orders)
- approving any change to guardrail values or strategy code (via PR review)
- a short weekly review of the journal and proposals (the `weekly-review` skill drafts it)

## What "highly profitable with low risk" realistically means

Low risk and maximum profit pull against each other. This build sits at the conservative end until the data
earns more risk. A realistic success path:
1. Paper, 4+ weeks: positive expectancy after fees over 100+ trades, max drawdown under 10%, slippage near the model.
2. Small live (10–20% of intended capital) for 1–3 months, tracking paper results.
3. Scale size only through calibrated confidence buckets. Never raise the hard caps because of a hot streak.

If paper trading shows no edge, the system has done its job: it found that out without losing real money.
