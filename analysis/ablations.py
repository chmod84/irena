#!/usr/bin/env python3
"""Ablations report: what the local game contributes, and attacker mismatch.

Two questions, both raised by the reviewers of the paper and answered in its
Negotiation Ablation subsection and among its Limitations:

  1. Is it the negotiation that produces the results, or would a simpler local
     lookahead do as well? The `onestep` defender keeps everything of IRENA but
     the local game: each response d is scored by the risk of the state it
     immediately produces, Risk(F_d(z)) + lambda_D * C_D(d). It is run at every
     weight in --ablation-lambdas and compared with IRENA and R-ADT LD at the
     operating point.

  2. The adaptive attacker forecasts with IRENA's own model. Do the results
     hold against an attacker that does not? The greedy one-step attacker is
     run against IRENA, LD and the one-step defender at the operating point,
     together with `irena_loss`: IRENA whose defender score carries a loss term
     symmetric to the attacker's goal bonus.

Everything is read from the CSVs and JSON traces the pipeline writes; nothing
is simulated here. Comparisons "move for move" compare the full sequence of
executed attack and defense actions.

Usage (from the package root; the pipeline runs it as the `ablations` stage):

  python3 analysis/ablations.py
  python3 analysis/ablations.py --operating-lambda 2e-4 --ablation-lambdas 1e-4 2e-4 5e-4
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from panacea_reference import PANACEA  # noqa: E402  proven minimum costs


def runner_csv(exp: Path, risk: str, attacker: str, learning: str, turn: str,
               policy: str, abstain: str = "off") -> Path:
    """The summary CSV run_irena.sh writes for a configuration (same rule)."""
    canonical = (risk == "neutral" and attacker == "adaptive"
                 and learning == "off" and turn == "attacker_first")
    if canonical and policy == "irena" and abstain == "off":
        return exp / "vector_preana_result.csv"
    suffix = f"{risk}_{attacker}_learning_{learning}_{turn}"
    if abstain == "on":
        suffix += "_abstain_on"
    if policy != "irena":
        suffix += f"_def_{policy}"
    return exp / f"vector_preana_{suffix}_result.csv"


def traces_dir(csv_path: Path) -> Path:
    name = csv_path.name
    if name.endswith("_result.csv"):
        return csv_path.parent / name.replace("_result.csv", "_results")
    return csv_path.parent / name.replace(".csv", "_results")


def load_rows(path: Path) -> Dict[str, Dict[str, str]]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return {Path(str(r["tree"])).stem: r for r in csv.DictReader(fh)}


def moves(csv_path: Path, tree: str) -> Optional[List[Tuple[str, str]]]:
    tj = traces_dir(csv_path) / tree / f"{tree}_trace.json"
    if not tj.exists():
        return None
    data = json.loads(tj.read_text(encoding="utf-8"))
    return [(str(e.get("player")), str(e.get("action")))
            for e in data.get("trace", []) if e.get("action")]


def cell(row: Optional[Dict[str, str]]) -> str:
    if not row:
        return "--"
    won = row["winner"] == "defender"
    return f"{float(row['defender_cost']):g} ({'W' if won else 'L'})"


def summary(rows: Dict[str, Dict[str, str]], trees: List[str]) -> str:
    won = [t for t in trees if t in rows and rows[t]["winner"] == "defender"]
    over = []
    for t in won:
        ref = PANACEA.get(t, {}).get("defender_cost")
        if ref:
            over.append((float(rows[t]["defender_cost"]) - ref) / ref * 100)
    mean = f"{sum(over) / len(over):+.0f}%" if over else "n/a"
    return f"{len(won)}/{len(trees)}  {mean}"


def same_outcome(a: Optional[Dict[str, str]], b: Optional[Dict[str, str]]) -> bool:
    return bool(a and b and a["winner"] == b["winner"]
                and float(a["defender_cost"]) == float(b["defender_cost"]))


def first_divergence(a: Optional[list], b: Optional[list]) -> Optional[str]:
    if a is None or b is None:
        return "trace missing"
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y:
            return f"move {i + 1} of {len(a)}"
    if len(a) != len(b):
        return f"move {min(len(a), len(b)) + 1} of {len(a)}"
    return None


def table(title: str, entries: List[Tuple[str, Dict[str, Dict[str, str]]]],
          trees: List[str]) -> List[str]:
    w = max(12, max(len(n) for n, _ in entries))
    out = [title, "", f"  {'':{w}}" + "".join(f"{t:>12}" for t in trees)
           + "   defended  mean overhead"]
    for name, rows in entries:
        out.append(f"  {name:{w}}" + "".join(f"{cell(rows.get(t)):>12}" for t in trees)
                   + "   " + summary(rows, trees))
    return out + [""]


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--experiment-dir", default=str(HERE.parent / "experiment"))
    ap.add_argument("--trees", nargs="+", default=["10", "25", "29", "34", "adt_nuovo"])
    ap.add_argument("--operating-lambda", type=float, default=2e-4)
    ap.add_argument("--ablation-lambdas", type=float, nargs="+", default=[1e-4, 2e-4, 5e-4])
    ap.add_argument("--risk-mode", default="neutral")
    ap.add_argument("--learning-mode", default="off")
    ap.add_argument("--turn-order", default="attacker_first")
    args = ap.parse_args(argv)

    exp = Path(args.experiment_dir)
    trees, lam = args.trees, args.operating_lambda

    def path(policy: str, attacker: str = "adaptive") -> Path:
        return runner_csv(exp, args.risk_mode, attacker, args.learning_mode,
                          args.turn_order, policy)

    irena, ld = path("irena"), path("ld")
    onestep = {l: exp / f"ablation_onestep_lambdaD_{l:g}.csv" for l in args.ablation_lambdas}
    greedy = {p: path(p, "greedy") for p in ("irena", "ld", "onestep", "irena_loss")}
    loss = path("irena_loss")

    needed = [irena, ld, loss, *onestep.values(), *greedy.values()]
    missing = [p.name for p in needed if not p.exists()]
    if missing:
        print("missing inputs (run the pipeline's ablations stage first):")
        for m in missing:
            print(f"  {m}")
        return 1

    R = {"irena": load_rows(irena), "ld": load_rows(ld), "loss": load_rows(loss)}
    O = {l: load_rows(p) for l, p in onestep.items()}
    G = {p: load_rows(f) for p, f in greedy.items()}

    out = [f"experiment dir: <package-root>/{exp.name}",
           f"operating lambda_D = {lam:g}   model = {args.risk_mode} / "
           f"learning {args.learning_mode} / {args.turn_order}", ""]

    # 1 -- negotiation ablation ------------------------------------------------
    out += table("1. NEGOTIATION ABLATION -- one-step defender, adaptive attacker",
                 [(f"IRENA {lam:g}", R["irena"])]
                 + [(f"one-step {l:g}", O[l]) for l in args.ablation_lambdas]
                 + [("R-ADT LD", R["ld"])], trees)
    for l in args.ablation_lambdas:
        same_ld = [t for t in trees if first_divergence(moves(onestep[l], t), moves(ld, t)) is None]
        matches = all(same_outcome(O[l].get(t), R["irena"].get(t)) for t in trees)
        out.append(f"  one-step at {l:g}: same moves as LD on {len(same_ld)}/{len(trees)}"
                   f" ({', '.join(same_ld) or 'none'}); matches IRENA everywhere: "
                   f"{'yes' if matches else 'no'}")
    if lam in onestep:
        out.append(f"  one-step vs IRENA at the operating point, first divergence:")
        for t in trees:
            d = first_divergence(moves(irena, t), moves(onestep[lam], t))
            out.append(f"    {t:>9}: {'none (same trajectory)' if d is None else d}")
    never = all(not all(same_outcome(O[l].get(t), R["irena"].get(t)) for t in trees)
                for l in args.ablation_lambdas)
    out += ["", "  VERDICT: " + ("no tested lambda_D lets the one-step defender match IRENA."
                                 if never else "some tested lambda_D lets the one-step "
                                 "defender match IRENA on every instance."), ""]

    # 2 -- attacker mismatch ---------------------------------------------------
    out += table(f"2. ATTACKER MISMATCH -- greedy one-step attacker, lambda_D = {lam:g}",
                 [("IRENA", G["irena"]), ("R-ADT LD", G["ld"]),
                  ("one-step", G["onestep"]), ("IRENA + loss", G["irena_loss"])], trees)
    adaptive = {"irena": R["irena"], "ld": R["ld"], "onestep": O.get(lam, {})}
    for p, label in (("irena", "IRENA"), ("ld", "R-ADT LD"), ("onestep", "one-step")):
        keep = [t for t in trees if same_outcome(G[p].get(t), adaptive[p].get(t))]
        lost = [t for t in trees if G[p].get(t, {}).get("winner") == "attacker"]
        out.append(f"  {label:9} outcome and cost as with the adaptive attacker on "
                   f"{', '.join(keep) or 'none'}; lost: {', '.join(lost) or 'none'}")
    out.append("")

    # 3 -- loss term ---------------------------------------------------------------
    same = [t for t in trees if first_divergence(moves(irena, t), moves(loss, t)) is None]
    out += ["3. LOSS TERM -- IRENA with B_g * [root holds in mu_d] in the defender score", "",
            f"  adaptive attacker: same trajectory as IRENA on {len(same)}/{len(trees)}"
            f" ({', '.join(same) or 'none'})",
            "  greedy attacker:   " + ", ".join(f"{t} {cell(G['irena_loss'].get(t))}" for t in trees),
            ""]
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
