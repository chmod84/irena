#!/usr/bin/env python3
"""Build every figure from the experiment CSVs of THIS package.

IRENA, R-ADT LC and R-ADT LD curves come from the CSVs the pipeline produced
in experiment/ (real runs on this machine). The PANACEA side of every
comparison comes from the declared constants in panacea_reference.py -- the
reference values reported in the paper -- so no PANACEA file or execution is
involved anywhere.

    python3 analysis/make_run_figures.py \\
        --trees 10 25 29 34 adt_nuovo --perf-exclude adt_nuovo

Outputs land in <experiment>/figures/ under the exact names the paper's LaTeX
includes (perf_*, eff_*).
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path
from typing import Dict, Tuple

HERE = Path(__file__).resolve().parent


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


C = _load("make_figures")          # the plotting/loading library
REF = _load("panacea_reference")   # the declared PANACEA reference values


def find_experiment_dir(explicit) -> Path:
    return C.find_experiment_dir(explicit)


def load_sweep(exp: Path, trees, operating: float) -> Dict[str, Dict[float, Tuple[float, str]]]:
    """sweep files + the operating-point CSV -> {tree: {lambda: (cost, winner)}}."""
    sweep: Dict[str, Dict[float, Tuple[float, str]]] = {t: {} for t in trees}

    def absorb(path: Path) -> None:
        vp = C.load_vp(path)
        for t, row in vp.items():
            if t not in sweep:
                continue
            lam = float(row["lambda_D"])
            sweep[t][lam] = (float(row["defender_cost"]), str(row["winner"]))

    for p in sorted(exp.glob("sweep_lambdaD_*.csv")):
        absorb(p)
    absorb(exp / "vector_preana_result.csv")   # the operating point itself
    missing = [t for t in trees if operating not in sweep[t]]
    if missing:
        raise RuntimeError(
            f"no row at the operating lambda_D={operating:g} for: "
            + ", ".join(missing))
    return sweep


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--experiment-dir", type=Path, default=None)
    ap.add_argument("--output-dir", type=Path, default=None)
    ap.add_argument("--trees", nargs="+",
                    default=["10", "25", "29", "34", "adt_nuovo"])
    ap.add_argument("--perf-exclude", nargs="*", default=[], metavar="TREE",
                    help="instances left out of the performance figures")
    ap.add_argument("--cost-exclude", nargs="*", default=[], metavar="TREE",
                    help="instances left out of the cost figures")
    ap.add_argument("--operating-lambda", type=float, default=2e-4)
    ap.add_argument("--risk-mode", default="neutral")
    ap.add_argument("--attacker-policy", default="adaptive")
    ap.add_argument("--learning-mode", default="off")
    ap.add_argument("--turn-order", default="attacker_first")
    args = ap.parse_args()

    exp = find_experiment_dir(args.experiment_dir)
    out = (args.output_dir or (exp / "figures")).resolve()
    out.mkdir(parents=True, exist_ok=True)
    trees = [C.normalize_name(t) for t in args.trees]
    perf = [t for t in trees if t not in {C.normalize_name(x)
                                          for x in args.perf_exclude}]
    cost = [t for t in trees if t not in {C.normalize_name(x)
                                          for x in args.cost_exclude}]
    op = float(args.operating_lambda)
    print(f"experiment dir: {exp}")
    print(f"figures at lambda_D={op:g} for trees: {', '.join(trees)}")

    # ---- the three policies, from the CSVs of this package's runs ----------
    suffix = (f"{args.risk_mode}_{args.attacker_policy}"
              f"_learning_{args.learning_mode}_{args.turn_order}")
    series = {"irena": C.load_vp(exp / "vector_preana_result.csv")}
    for pol in ("lc", "ld"):
        series[pol] = C.load_vp(
            exp / f"vector_preana_{suffix}_def_{pol}_result.csv")
    for pol, rows in series.items():
        missing = [t for t in trees if t not in rows]
        if missing:
            raise RuntimeError(f"{pol}: no CSV row for {', '.join(missing)}")
        for t in trees:
            got = float(rows[t]["lambda_D"])
            if abs(got - op) > 1e-12:
                raise RuntimeError(
                    f"{pol}/{t}: CSV is at lambda_D={got:g}, expected {op:g}")

    # ---- invariants tying the comparison together --------------------------
    for t in cost:
        if series["irena"][t]["winner"] == "defender":
            assert float(series["irena"][t]["defender_cost"]) >= \
                float(REF.PANACEA[t]["defender_cost"]), \
                (t, "IRENA below the proven minimum: impossible")

    # ---- PANACEA reference: declared constants -----------------------------
    C.PANACEA_STATS.update({
        t: {"states": s, "transitions": tr, "build_time_s": b,
            "prism_result": 0.0}
        for t, (s, tr, b, _c, _m) in REF.STATS.items()})

    sweep = load_sweep(exp, trees, op)

    C.plot_state_space(perf, out / "perf_state_space")
    C.plot_times(perf, REF.TIME, series["irena"], out / "perf_planning_time",
                 panacea_label="PANACEA model checking")
    C.plot_memory(perf, series, REF.MEM, out / "perf_peak_memory")
    C.plot_baseline_costs(cost, REF.PANACEA, series, out / "eff_defender_cost")
    C.plot_costs(cost, REF.PANACEA, series["irena"],
                 out / "eff_cost_breakdown", series=series)
    C.plot_defender_vs_optimum(cost, REF.PANACEA, series["irena"],
                               out / "eff_cost_vs_optimum")
    C.plot_lambda_sweep(trees, sweep, REF.PANACEA, out / "eff_lambda_sweep",
                        reported_lam=op, cost_trees=cost)

    for f in sorted(out.glob("*.pdf")):
        print(f"  {f}  {f.stat().st_size / 1024:.0f} kB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
