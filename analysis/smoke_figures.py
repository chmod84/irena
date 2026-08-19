#!/usr/bin/env python3
"""Render every changed figure from the four-tree numbers, with no CSVs.

Exercises the code paths the last run got wrong:
  * PANACEA lost on 34, so its bar in defender_baselines must be hatched;
  * the memory labels must not overprint on the 10-node group;
  * the sweep must highlight the lambda_D of the reported runs (1e-4), not the
    one re-derived from the instances PANACEA happened to win (1e-3).
"""

import math
from pathlib import Path

import importlib.util as _ilu
_spec = _ilu.spec_from_file_location(
    "make_figures", Path(__file__).resolve().parent / "make_figures.py")
C = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(C)

TREES = ["10", "25", "29", "34"]

PAN = {
    "10": {"defender_cost": 90.0,  "attacker_cost": 140.0, "winner": "defender"},
    "25": {"defender_cost": 120.0, "attacker_cost": 640.0, "winner": "defender"},
    "29": {"defender_cost": 120.0, "attacker_cost": 640.0, "winner": "defender"},
    "34": {"defender_cost": 600.0, "attacker_cost": 400.0, "winner": "attacker"},
}


def row(d, a, w, mb, tD):
    return {"defender_cost": float(d), "attacker_cost": float(a), "winner": w,
            "max_rss_kb": mb * 1024.0, "planning_time": tD,
            "defender_planning_time": tD, "lambda_D": 1e-4}


SERIES = {
    "irena": {
        "10": row(90, 140, "defender", 86, 0.0607),
        "25": row(660, 770, "defender", 95, 3.526),
        "29": row(240, 780, "defender", 102, 5.360),
        "34": row(830, 1880, "defender", 114, 9.625),
    },
    "lc": {
        "10": row(50, 70, "attacker", 86, 2.0e-5),
        "25": row(40, 120, "attacker", 90, 6.8e-5),
        "29": row(100, 220, "attacker", 96, 1.6e-4),
        "34": row(100, 220, "attacker", 100, 1.2e-4),
    },
    "ld": {
        "10": row(50, 70, "attacker", 86, 1.6e-5),
        "25": row(170, 680, "defender", 92, 1.1e-4),
        "29": row(390, 910, "defender", 99, 1.9e-4),
        "34": row(860, 910, "defender", 103, 2.2e-4),
    },
}

MEM = {"10": 137.0, "25": 915.0, "29": 12019.0, "34": 52983.0}

# (cost, winner) per lambda_D, reconstructed from the sweep the user ran.
SWEEP = {
    "10": {1e-4: (90, "defender"), 1e-3: (90, "defender"),
           1e-2: (90, "defender"), 1e-1: (90, "attacker")},
    "25": {1e-4: (660, "defender"), 1e-3: (180, "defender"),
           1e-2: (207, "defender"), 1e-1: (150, "attacker")},
    "29": {1e-4: (240, "defender"), 1e-3: (180, "defender"),
           1e-2: (140, "attacker"), 1e-1: (110, "attacker")},
    "34": {1e-4: (830, "defender"), 1e-3: (300, "attacker"),
           1e-2: (200, "attacker"), 1e-1: (150, "attacker")},
}

out = Path("smoke_out")
out.mkdir(exist_ok=True)

C.plot_baseline_costs(TREES, PAN, SERIES, out / "defender_baselines")
C.plot_memory(TREES, SERIES, MEM, out / "peak_memory")
C.plot_costs(TREES, PAN, SERIES["irena"], out / "cost_comparison", series=SERIES)

inferred = C._operating_point(["10", "25", "29"], SWEEP)
print(f"operating point inferred from the instances PANACEA won: {inferred:g}")
C.plot_lambda_sweep(TREES, SWEEP, PAN, out / "lambda_sweep_inferred")
C.plot_lambda_sweep(TREES, SWEEP, PAN, out / "lambda_sweep_reported",
                    reported_lam=1e-4)

assert inferred == 1e-3, inferred
for f in sorted(out.glob("*.png")):
    print(f"  {f}  {f.stat().st_size/1024:.0f} kB")
print("OK")
