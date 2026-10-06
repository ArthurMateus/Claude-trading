"""Markdown + PNG report for the 2026 out-of-sample test (research/results/2026/REPORT.md)."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from .test2026 import RESULTS

# Reference palette (dataviz skill): ordinal blue ramp for ordered risk levels, blue<->red diverging for returns.
ORDINAL_BLUE = ["#86b6ef", "#6da7ec", "#5598e7", "#3987e5", "#2a78d6", "#1c5cab", "#184f95", "#0d366b"]
DIVERGING = ["#d03b3b", "#e66767", "#f2a7a7", "#f0efec", "#9ec5f4", "#3987e5", "#184f95"]
SURFACE, INK, INK_2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e6e5e1"


def _fmt(v, pct=False):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "-"
    if isinstance(v, (bool, np.bool_)):
        return "yes" if v else "no"
    if isinstance(v, (int, np.integer)):
        return str(v)
    if isinstance(v, str):
        return v
    if isinstance(v, float) and np.isinf(v):
        return "inf"
    return f"{v:+.1f}%" if pct else f"{v:.2f}"


def _table(df: pd.DataFrame, pct_cols=()) -> str:
    if df.empty:
        return "_none_\n"
    head = "| " + " | ".join(df.columns) + " |\n|" + "---|" * len(df.columns) + "\n"
    rows = ["| " + " | ".join(_fmt(v, c in pct_cols) for c, v in r.items()) + " |" for _, r in df.iterrows()]
    return head + "\n".join(rows) + "\n"


def _chart_equity(curves: pd.DataFrame, key_prefix: str, lev: int, path: Path) -> bool:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    cols = [c for c in curves.columns if c.startswith(key_prefix) and c.endswith(f"|x{lev}")]
    if not cols:
        return False
    fig, ax = plt.subplots(figsize=(10, 5), facecolor=SURFACE)
    ax.set_facecolor(SURFACE)
    for color, col in zip(ORDINAL_BLUE, cols):
        s = curves[col].dropna()
        risk = col.split("|r")[1].split("|")[0]
        ax.plot(s.index, s.values, color=color, linewidth=2, label=f"{risk}% risk")
    ax.axhline(500, color=INK_2, linewidth=1, linestyle=(0, (4, 3)))
    ax.set_yscale("log")
    from matplotlib.ticker import FuncFormatter, LogLocator
    ax.yaxis.set_major_locator(LogLocator(base=10, subs=(1, 2, 3, 5, 7)))
    ax.yaxis.set_minor_locator(LogLocator(base=10, subs=()))
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"${v:,.0f}"))
    ax.set_ylabel("Equity (log scale; dashed = $500 start)", color=INK_2)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=INK_2)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.legend(frameon=False, labelcolor=INK, ncol=8, loc="upper center", bbox_to_anchor=(0.5, -0.08),
              fontsize=9, handlelength=1.5)
    ax.set_title(f"2026 out-of-sample equity by risk per trade ({key_prefix.split('|')[0]}, {lev}x)",
                 color=INK, loc="left")
    fig.tight_layout()
    fig.savefig(path, dpi=130, facecolor=SURFACE)
    plt.close(fig)
    return True


def _chart_heatmap(grid: pd.DataFrame, path: Path, title: str) -> bool:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import LinearSegmentedColormap, TwoSlopeNorm
    if grid.empty:
        return False
    piv = grid.pivot(index="risk_pct", columns="leverage", values="return_pct").sort_index()
    vals = piv.to_numpy(dtype=float)
    lo, hi = min(-1.0, np.nanmin(vals)), max(1.0, np.nanmax(vals))
    cmap = LinearSegmentedColormap.from_list("div", DIVERGING)
    fig, ax = plt.subplots(figsize=(8, 5.5), facecolor=SURFACE)
    ax.imshow(vals, cmap=cmap, norm=TwoSlopeNorm(vmin=lo, vcenter=0, vmax=hi), aspect="auto")
    ax.set_xticks(range(len(piv.columns)), [f"{c}x" for c in piv.columns], color=INK_2)
    ax.set_yticks(range(len(piv.index)), [f"{r}%" for r in piv.index], color=INK_2)
    ax.set_xlabel("Leverage cap", color=INK_2)
    ax.set_ylabel("Risk per trade", color=INK_2)
    for i in range(vals.shape[0]):
        for j in range(vals.shape[1]):
            ax.text(j, i, f"{vals[i, j]:+.0f}%", ha="center", va="center", fontsize=9, color=INK)
    for s in ax.spines.values():
        s.set_visible(False)
    ax.set_title(title, color=INK, loc="left")
    fig.tight_layout()
    fig.savefig(path, dpi=130, facecolor=SURFACE)
    plt.close(fig)
    return True


def write(out_dir: Path = RESULTS) -> Path:
    meta = json.loads((out_dir / "meta.json").read_text())
    strategies = pd.read_csv(out_dir / "strategies.csv")
    grid = pd.read_csv(out_dir / "portfolio_grid.csv") if (out_dir / "portfolio_grid.csv").stat().st_size > 1 else pd.DataFrame()
    try:
        curves = pd.read_csv(out_dir / "equity_curves.csv", index_col=0, parse_dates=True)
    except Exception:
        curves = pd.DataFrame()
    lines = [
        "# 2026 out-of-sample test",
        "",
        f"- Test window: **{meta['test_start']} → {meta['test_end']}** ({meta['test_days']} days), assets: "
        f"{', '.join(meta['assets'])}, start equity ${meta['start_equity']:.0f}",
        f"- Strategies frozen at {meta['frozen_created_at']} (sha256 `{meta['frozen_sha256'][:16]}…`) after "
        f"searching **{meta['configs_searched']} configurations** on 2022–2024 and selecting on 2025. "
        "No 2026 data was loaded before freezing.",
        "- Costs: `alpaca_spot` = 0.25% fee + 0.05% slippage per side, long-only, 1x. `perp` = 0.05% fee + "
        "0.03% slippage per side + 0.01%/8h funding, long and short, up to 20x with liquidation modeled.",
        "",
        "## Strategy champions (one per family and side, per venue)",
        "",
    ]
    for cm in strategies["cost_model"].unique():
        s = strategies[strategies["cost_model"] == cm]
        lev = 1 if cm == "alpaca_spot" else 3
        cols = ["family", "side", "tf", "validated", "train_profit_factor", "val_profit_factor", "test_trades",
                "test_win_rate", "test_profit_factor", "test_expectancy_bps"]
        ret_cols = [c for c in (f"ret_r1_x{lev}", f"ret_r5_x{lev}", f"ret_r10_x{lev}", f"ret_r20_x{lev}") if c in s]
        view = s[cols + ret_cols].rename(columns={c: c.replace("ret_r", "ret@").replace(f"_x{lev}", f"%,{lev}x")
                                                  for c in ret_cols})
        lines += [f"### `{cm}`", "", _table(view, pct_cols=[c for c in view.columns if c.startswith("ret@")]), ""]
    if not grid.empty:
        lines += ["## Combined portfolios: return by risk per trade × leverage", ""]
        for (cm, pf), g in grid.groupby(["cost_model", "portfolio"]):
            for guard in (False, True):
                gg = g[g["guardrails"] == guard]
                piv = gg.pivot(index="risk_pct", columns="leverage", values="return_pct")
                dd = gg.pivot(index="risk_pct", columns="leverage", values="max_drawdown_pct")
                tag = "with the bot's guardrails (5% daily stop, 15% DD halt)" if guard else "raw"
                lines += [f"### `{cm}` · {pf} · {tag} ({int(gg['strategies'].iloc[0])} strategies)", "",
                          "Return % (max drawdown %):", ""]
                table = pd.DataFrame({f"{c}x": [f"{piv.loc[r, c]:+.1f}% ({dd.loc[r, c]:.0f}%)" for r in piv.index]
                                      for c in piv.columns}, index=[f"{r}% risk" for r in piv.index])
                lines += [_table(table.reset_index().rename(columns={"index": "risk"})), ""]
                if not guard:
                    img = out_dir / f"heatmap_{cm}_{pf}.png"
                    if _chart_heatmap(gg, img, f"{cm} · {pf}: 2026 return by risk and leverage"):
                        lines += [f"![heatmap]({img.name})", ""]
                    lev = 1 if cm == "alpaca_spot" else 3
                    eq = out_dir / f"equity_{cm}_{pf}.png"
                    if not curves.empty and _chart_equity(curves, f"{cm}|{pf}|", lev, eq):
                        lines += [f"![equity]({eq.name})", ""]
                ruined = gg[gg["ruined"]]
                if len(ruined):
                    lines += [f"Ruined (equity < 1% of start): " + ", ".join(
                        f"{r.risk_pct}% @ {r.leverage}x" for r in ruined.itertuples()), ""]
    else:
        lines += ["## Combined portfolios", "", "_No strategy produced test trades._", ""]
    path = out_dir / "REPORT.md"
    path.write_text("\n".join(lines))
    return path
