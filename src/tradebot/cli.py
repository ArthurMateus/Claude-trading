"""`tradebot` command line.

  tradebot validate              run the Backtest gate for every setup
  tradebot cycle                 run one pipeline cycle and print the report
  tradebot run                   loop forever (paper by default)
  tradebot report                journal summary, promotion verdict, LLM spend
  tradebot export trades.csv     export the trade journal
  tradebot reset-halt            clear a persisted HALT (human action)
Global flags: --config PATH, --offline (no LLM calls; deterministic heuristics), --mode simulated|paper|live
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from dataclasses import asdict

from .brokers.simulated import SimulatedBroker
from .config import load_dotenv, load_settings
from .data.providers import make_provider, SyntheticProvider
from .journal import Journal
from .llm import LLMClient
from .pipeline import Pipeline

LIVE_ACK = "I_UNDERSTAND_THIS_USES_REAL_MONEY"


def build(args) -> Pipeline:
    load_dotenv()
    s = load_settings(args.config)
    if args.mode:
        s.mode = args.mode
    if s.mode == "simulated":
        s.broker = "simulated"
        if not os.environ.get("ALPACA_API_KEY"):
            s.data_provider = "synthetic"
    journal = Journal(s.journal_path)
    llm = LLMClient(s.llm, journal, offline=args.offline)
    provider = make_provider(s.data_provider)
    if s.broker == "simulated":
        broker = SimulatedBroker(10_000, s.costs.taker_fee_bps, s.costs.sim_slippage_bps)
    else:
        from .brokers.alpaca import AlpacaBroker
        if s.mode == "live":
            if os.environ.get("TRADEBOT_ALLOW_LIVE") != LIVE_ACK:
                sys.exit(f"Refusing live mode: set TRADEBOT_ALLOW_LIVE={LIVE_ACK}")
            promo = journal.get_state("promotion", {}) or {}
            if promo.get("verdict") != "PROMOTE":
                sys.exit(f"Refusing live mode: paper-trading gate verdict is {promo.get('verdict', 'missing')}")
        broker = AlpacaBroker(paper=s.mode != "live", fee_bps=s.costs.taker_fee_bps)
    return Pipeline(s, provider, broker, journal, llm)


def cmd_report(p: Pipeline) -> None:
    j = p.journal
    closed = j.closed_trades()
    opened = j.open_trades()
    pnl = sum(t.pnl_usd or 0 for t in closed)
    wins = sum(t.result == "WIN" for t in closed)
    print(f"mode={p.s.mode} open={len(opened)} closed={len(closed)} wins={wins} net_pnl={pnl:.2f} USD")
    print("validated setups:", list(p.backtester.validated_setups()))
    print("agent weights:", j.get_state("agent_weights", p.s.fusion_weights))
    print("promotion:", json.dumps(j.get_state("promotion", {}), default=str))
    print(f"LLM spend today: ${j.llm_spend_today():.2f} / ${p.s.llm.daily_budget_usd:.2f}")
    print("halt:", j.get_state("halt"))
    for t in closed[:10]:
        print(f"  {t.closed_at:%m-%d %H:%M} {t.asset:9} {t.setup:18} {t.result:9} R={t.r_multiple or 0:+.2f} "
              f"ret={t.actual_return_pct or 0:+.2f}% slip={t.slippage_bps or 0:.1f}bps  {t.exit_reason}")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="tradebot")
    ap.add_argument("--config")
    ap.add_argument("--offline", action="store_true", help="no LLM calls; agents use deterministic heuristics")
    ap.add_argument("--mode", choices=["simulated", "paper", "live"])
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("validate")
    sub.add_parser("cycle")
    sub.add_parser("run")
    sub.add_parser("report")
    sub.add_parser("reset-halt")
    ex = sub.add_parser("export")
    ex.add_argument("path", nargs="?", default="data/trades.csv")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    p = build(args)
    if args.cmd == "validate":
        for name, r in p.backtester.validate(p.provider).items():
            print(f"{name:18} {'PASS' if r['passed'] else 'FAIL'}  full={r['full']}  fails={r['fails']}")
    elif args.cmd == "cycle":
        print(json.dumps(asdict(p.run_cycle()), default=str, indent=2))
    elif args.cmd == "run":
        p.run_forever()
    elif args.cmd == "report":
        cmd_report(p)
    elif args.cmd == "export":
        print(f"exported {p.journal.export_csv(args.path)} trades to {args.path}")
    elif args.cmd == "reset-halt":
        p.journal.set_state("halt", None)
        p.journal.log_event("kill_switch", "halt reset by human", "WARN")
        print("halt cleared")


if __name__ == "__main__":
    main()
