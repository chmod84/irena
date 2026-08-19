#!/usr/bin/env python3
"""Step 9: does letting the defender decline to act change the outcome?

Vector-PREANA, as run for every result in the draft, forces the defender to buy
a response on every turn on which any response is enabled. PANACEA's globally
solved policy operates under no such constraint: it may leave the defense idle.
The `defender_abstain=on` configuration removes the constraint by adding a
no-op candidate to the defender's decision, scored symmetrically with the real
defenses (see `_abstain_record` in run_vector_preana_experiment3.sh).

Only one question matters here:

  * the winner is the SAME on every instance -> the forced-response constraint
    was not doing the work; the comparison against PANACEA is like-for-like and
    the cost figures stand. The cost drop, if any, is a bonus result.

  * some instance flips defender -> attacker -> the instances are in the
    proactive regime: Vector-PREANA was winning partly because it was obliged
    to spend. That is not a like-for-like comparison against PANACEA and must
    not be published as one until C1 (the three-valued state encoding) is
    resolved.

The two CSVs must differ in `defender_abstain` and in NOTHING else. The script
refuses to compare runs whose lambda_D, repeats, or model configuration differ,
because the runner names its output by configuration only -- lambda_D does not
appear in the filename, so two runs at different weights are trivially easy to
compare by mistake.

Usage
-----
  python3 compare_abstention.py \
      --baseline experiment3/vector_preana_lambdaD_1e-3_result.csv \
      --abstain  experiment3/vector_preana_dynamic_adaptive_learning_on_attacker_first_abstain_on_result.csv

With no arguments both files are looked up under experiment3/, and the abstain
file is the one the runner writes for the default model.
"""

from __future__ import annotations

import argparse
import csv
import math
import sys
from pathlib import Path
from typing import Dict, List, Optional

# Everything that must match for the two runs to be comparable. lambda_D is in
# this list precisely because it is NOT in the output filename.
PINNED = [
    "defense_cost_weight",
    "attack_cost_weight",
    "repeats",
    "memory_risk_weight",
    "attack_goal_bonus",
    "risk_mode",
    "attacker_policy",
    "learning_mode",
    "turn_order",
]

# Nothing is excluded by default. There used to be a hard-coded {"34"} here,
# from a period when that instance had no usable PANACEA reference. It outlived
# the reason for it and would have quietly dropped a row from every future
# comparison. An exclusion is now a per-run argument, so it appears in the
# command and in the output instead of in the source.


def read_rows(path: Path) -> List[Dict[str, str]]:
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    rows = list(csv.DictReader(text.splitlines()))
    for r in rows:
        r.pop(None, None)
    return rows


def as_float(v: object) -> float:
    try:
        return float(str(v).strip().replace(",", "."))
    except (TypeError, ValueError):
        return math.nan


def by_tree(rows: List[Dict[str, str]]) -> Dict[str, Dict[str, str]]:
    return {Path(str(r.get("tree", ""))).stem: r
            for r in rows if str(r.get("tree", "")).strip()}


def find_default(exp: Path, pattern: str) -> Optional[Path]:
    hits = sorted(exp.glob(pattern))
    return hits[0] if len(hits) == 1 else None


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--experiment-dir", type=Path, default=None,
                    help="PANACEA experiment3 directory (default: located by "
                         "walking up from this file / the working directory)")
    ap.add_argument("--baseline", type=Path, default=None,
                    help="CSV of the abstain=off run at the SAME lambda_D")
    ap.add_argument("--abstain", type=Path, default=None,
                    help="CSV of the abstain=on run")
    ap.add_argument("--allow-mismatch", action="store_true",
                    help="compare anyway when the configurations differ")
    ap.add_argument("--exclude", nargs="*", default=None, metavar="TREE",
                    help="instances to leave out of the comparison. Empty by "
                         "default; whatever is passed is echoed in the output, "
                         "so a dropped row is never silent.")
    args = ap.parse_args()

    exp = args.experiment_dir
    if exp is None:
        for base in [Path.cwd().resolve(), *Path.cwd().resolve().parents,
                     *Path(__file__).resolve().parents]:
            for cand in (base / "experiment",
                         base / "experiments" / "experiment3",
                         base / "experiment3"):
                if cand.is_dir():
                    exp = cand
                    break
            if exp is not None:
                break
        else:
            exp = Path("experiment3")
    abstain_path = args.abstain or find_default(
        exp, "vector_preana_*abstain_on_result.csv")
    baseline_path = args.baseline or (exp / "vector_preana_result.csv")

    for label, p in (("abstain=on", abstain_path), ("baseline", baseline_path)):
        if p is None or not Path(p).exists():
            print(f"ERROR: {label} CSV not found"
                  + (f": {p}" if p else " (pass it explicitly)"), file=sys.stderr)
            return 2

    base = by_tree(read_rows(Path(baseline_path)))
    abst = by_tree(read_rows(Path(abstain_path)))
    if not base or not abst:
        print("ERROR: one of the CSVs has no data rows", file=sys.stderr)
        return 2

    print(f"baseline   : {baseline_path}")
    print(f"abstain=on : {abstain_path}")
    print()

    b0, a0 = next(iter(base.values())), next(iter(abst.values()))

    # The abstain flag must actually differ, or the run was mislabelled.
    flags = (str(b0.get("defender_abstain", "off")).strip().lower(),
             str(a0.get("defender_abstain", "")).strip().lower())
    if flags[1] != "on":
        print(f"ERROR: the abstain CSV reports defender_abstain={flags[1]!r}; "
              f"it is not an abstention run.", file=sys.stderr)
        return 2
    if flags[0] != "off":
        print(f"ERROR: the baseline CSV reports defender_abstain={flags[0]!r}; "
              f"it is not a baseline.", file=sys.stderr)
        return 2

    mismatch = [(k, b0.get(k), a0.get(k)) for k in PINNED
                if k in b0 and k in a0
                and str(b0.get(k)).strip() != str(a0.get(k)).strip()]
    if mismatch:
        print("CONFIGURATION MISMATCH -- these runs are not comparable:")
        for k, x, y in mismatch:
            print(f"  {k:22} baseline={x!r}  abstain={y!r}")
        print("\nRe-run the baseline at the same settings, or pass "
              "--allow-mismatch if you know what you are doing.")
        if not args.allow_mismatch:
            return 3
        print()

    lam = b0.get("defense_cost_weight", "?")
    print(f"lambda_D = {lam}   repeats = {b0.get('repeats', '?')}   "
          f"model = {b0.get('risk_mode','?')}/{b0.get('attacker_policy','?')}/"
          f"learning {b0.get('learning_mode','?')}/{b0.get('turn_order','?')}")
    print()

    exclude = set(args.exclude or ())
    trees = [t for t in sorted(set(base) & set(abst), key=lambda s: (len(s), s))
             if t not in exclude]
    skipped = sorted((set(base) ^ set(abst)) | (set(base) & set(abst) & exclude))
    if exclude:
        print(f"excluded by --exclude: {', '.join(sorted(exclude))}\n")
    if not trees:
        print("ERROR: no instance present in both CSVs", file=sys.stderr)
        return 2

    # Column width from the data: instance names used to be two-digit node
    # counts, and a named instance ("adt_nuovo") pushed every later column out
    # of alignment.
    w = max(6, max(len(x) for x in trees))
    print(f"{'tree':>{w}} {'winner off':>11} {'winner on':>10} {'D_cost off':>11} "
          f"{'D_cost on':>10} {'delta':>9} {'abstain':>8} {'actions':>8}")
    print("-" * (w + 76))

    flips: List[str] = []
    saved: List[float] = []
    for t in trees:
        b, a = base[t], abst[t]
        wb = str(b.get("winner", "?")).strip().lower()
        wa = str(a.get("winner", "?")).strip().lower()
        cb, ca = as_float(b.get("defender_cost")), as_float(a.get("defender_cost"))
        d = ca - cb
        rel = (d / cb * 100.0) if cb else math.nan
        nab = a.get("defender_abstentions", "-")
        flag = "" if wb == wa else "   <-- FLIP"
        if wb != wa:
            flips.append(f"{t}: {wb} -> {wa}")
        elif wb == "defender" and math.isfinite(rel):
            saved.append(rel)
        print(f"{t:>{w}} {wb:>11} {wa:>10} {cb:>11.0f} {ca:>10.0f} "
              f"{rel:>8.0f}% {str(nab):>8} "
              f"{str(b.get('actions','?')) + '/' + str(a.get('actions','?')):>8}{flag}")

    if skipped:
        print(f"\nnot compared: {', '.join(skipped)}"
              + ("  (34 excluded by decision)" if "34" in skipped else ""))

    print()
    if flips:
        print("VERDICT: the outcome CHANGES when the defender is allowed to abstain.")
        for f in flips:
            print(f"  {f}")
        print("\nThese instances are in the proactive regime: part of the defender's\n"
              "success came from being obliged to spend, not from choosing well.\n"
              "The comparison against PANACEA is NOT like-for-like and must not be\n"
              "published as one until C1 is resolved. Report the abstention result\n"
              "explicitly as a limitation.")
        return 1

    print("VERDICT: the winner is unchanged on every instance.")
    print("The forced-response constraint was not doing the work: the comparison\n"
          "against PANACEA stands as a like-for-like one.")
    if saved:
        mean = sum(saved) / len(saved)
        if mean < -1.0:
            print(f"\nBonus: allowing abstention also lowers the defense cost by "
                  f"{-mean:.0f}% on average ({', '.join(f'{s:+.0f}%' for s in saved)}). "
                  f"Worth one sentence in Section V.")
        elif abs(mean) <= 1.0:
            print("\nDefense cost is unchanged: the defender never wanted to abstain "
                  "on a turn where it mattered.")
        else:
            print(f"\nNote: defense cost is {mean:+.0f}% on average WITH abstention "
                  f"available, i.e. the no-op candidate wins turns that then cost "
                  f"more later. Mention it or drop it, but do not hide it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
