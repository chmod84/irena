#!/usr/bin/env python3
"""Classify Vector-PREANA defender victories as blocked / exhausted / stalled.

The simulator declares a defender victory when no attack is enabled in the
reached state. That covers three operationally different situations, which the
aggregate outcome metric does not separate:

  BLOCKED    at least one unused attack would have been executable had the
             defenses not blocked a precondition. The defense was decisive.

  EXHAUSTED  every attack action in the R-ADT has already been executed.
             The attacker ran out of unrepeatable actions; the defenses were
             not what stopped it.

  STALLED    unused attacks remain, none of them is affected by any defense,
             and their preconditions were simply never reached. The attacker
             was structurally stuck.

Only BLOCKED supports an effectiveness claim.

The decision is made with a counterfactual: every precondition that a defense
set to "blocked" is restored to the value it held immediately before that
defense (recovered from the trace), and the enabled-attack set is recomputed.

Usage
-----
  python3 classify_terminations.py \
      --repo-root /path/to/PANACEA \
      --results-dir /path/to/PANACEA/experiments/experiment3/vector_preana_results

Optional:
  --trees-dir  default <results-dir>/../trees
  --csv OUT    also write a machine-readable summary
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Set, Tuple


# --------------------------------------------------------------------------
# layout discovery
# --------------------------------------------------------------------------

def find_repo_root(explicit: Optional[Path] = None) -> Path:
    """Locate the PANACEA root: the directory that holds tree_to_prism.py."""
    if explicit is not None:
        root = Path(explicit).resolve()
        if not (root / "tree_to_prism.py").exists():
            raise SystemExit(f"ERROR: no tree_to_prism.py under {root}")
        return root
    seen: List[Path] = []
    here = Path(__file__).resolve()
    for base in [here.parent, *here.parents,
                 Path.cwd().resolve(), *Path.cwd().resolve().parents]:
        if base in seen:
            continue
        seen.append(base)
        if (base / "tree_to_prism.py").exists():
            return base
    raise SystemExit(
        "ERROR: could not locate the PANACEA root. Pass --repo-root explicitly.")


def find_experiment_dir(repo_root: Path) -> Path:
    for c in (repo_root / "experiment",
              repo_root / "experiments" / "experiment3", repo_root / "experiment3"):
        if c.is_dir():
            return c
    return repo_root / "experiments" / "experiment3"


def find_trees_dir(repo_root: Path, experiment_dir: Path,
                   explicit: Optional[Path] = None) -> Path:
    if explicit is not None:
        return Path(explicit).resolve()
    for c in (experiment_dir / "trees", repo_root / "experiments" / "trees"):
        if c.is_dir() and any(c.glob("*.xml")):
            return c
    return experiment_dir / "trees"


# --------------------------------------------------------------------------
# R-ADT access (mirrors the prototype's Model, kept standalone)
# --------------------------------------------------------------------------

def load_attacks(repo_root: Path, xml_path: Path):
    sys.path.insert(0, str(repo_root))
    from tree_to_prism import parse_file, get_info  # type: ignore

    df = parse_file(str(xml_path)).to_dataframe()
    (goal, _a2g, _init, attacks, defenses, _dfa, _dfd) = get_info(df)
    return goal, attacks, defenses


def attack_enabled(goal: str, attacks, action: str,
                   state: Mapping[str, int], executed: Set[str]) -> bool:
    """Verbatim reimplementation of the prototype's attack_enabled()."""
    if action in executed:
        return False
    info = attacks[action]
    effect = str(info["effect"])
    if int(state.get(goal, 0)) == 1:
        return False
    if int(state.get(effect, 0)) != 0:
        return False
    pre = [str(x) for x in info["preconditions"]]
    if not pre:
        return True
    vals = [int(state.get(p, 0)) == 1 for p in pre]
    return any(vals) if str(info["refinement"]) == "disjunctive" else all(vals)


# --------------------------------------------------------------------------
# trace analysis
# --------------------------------------------------------------------------

def replay(trace: List[dict]) -> Tuple[Set[str], Dict[str, int]]:
    """Recover the executed-attack set and the pre-block value of each dim.

    A defense sets one precondition to 2. The value it held immediately before
    is read from that step's `state_before`, which the simulator records.
    """
    executed: Set[str] = set()
    pre_block: Dict[str, int] = {}

    for step in trace:
        action = step.get("action")
        if action is None:
            continue
        if step.get("player") == "attacker":
            executed.add(str(action))
            continue

        before = step.get("state_before") or {}
        after = step.get("state") or {}
        for dim, val in after.items():
            if int(val) == 2 and int(before.get(dim, 0)) != 2:
                pre_block[str(dim)] = int(before.get(dim, 0))
    return executed, pre_block


def classify(goal: str, attacks, final_state: Mapping[str, int],
             executed: Set[str], pre_block: Mapping[str, int]) -> dict:
    all_attacks = set(attacks)
    unused = sorted(all_attacks - executed)

    still_enabled = [a for a in all_attacks
                     if attack_enabled(goal, attacks, a, final_state, executed)]

    # Counterfactual: undo every block, keep everything the attacker achieved.
    unblocked = dict(final_state)
    for dim, prev in pre_block.items():
        if int(unblocked.get(dim, 0)) == 2:
            unblocked[dim] = int(prev)

    would_be_enabled = [a for a in unused
                        if attack_enabled(goal, attacks, a, unblocked, executed)]

    if still_enabled:
        verdict = "NOT-TERMINAL"
    elif not unused:
        verdict = "EXHAUSTED"
    elif would_be_enabled:
        verdict = "BLOCKED"
    else:
        verdict = "STALLED"

    reasons: Dict[str, int] = {}
    for a in unused:
        eff = str(attacks[a]["effect"])
        pre = [str(x) for x in attacks[a]["preconditions"]]
        if int(final_state.get(eff, 0)) == 2:
            key = "effect blocked by defense"
        elif int(final_state.get(eff, 0)) == 1:
            key = "effect already achieved"
        elif any(int(final_state.get(p, 0)) == 2 for p in pre):
            key = "precondition blocked by defense"
        else:
            key = "precondition never reached"
        reasons[key] = reasons.get(key, 0) + 1

    return {
        "verdict": verdict,
        "n_attacks": len(all_attacks),
        "n_executed": len(executed),
        "n_unused": len(unused),
        "n_blocked_open": len(would_be_enabled),
        "blocked_dims": len(pre_block),
        "reasons": reasons,
        "would_be_enabled": would_be_enabled[:10],
    }


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo-root", type=Path, default=None,
                    help="PANACEA root; auto-detected when omitted")
    ap.add_argument("--results-dir", type=Path, default=None,
                    help="default: experiment3/vector_preana_results")
    ap.add_argument("--trees-dir", type=Path, default=None)
    ap.add_argument("--csv", type=Path, default=None)
    args = ap.parse_args()

    repo_root = find_repo_root(args.repo_root)
    experiment_dir = find_experiment_dir(repo_root)
    results_dir = (args.results_dir.resolve() if args.results_dir
                   else experiment_dir / "vector_preana_results")
    trees_dir = find_trees_dir(repo_root, experiment_dir, args.trees_dir)

    print(f"repo root:   {repo_root}")
    print(f"results dir: {results_dir}")
    print(f"trees dir:   {trees_dir}")
    print()

    traces = sorted(results_dir.rglob("*_trace.json"))
    if not traces:
        print(f"ERROR: no *_trace.json under {results_dir}", file=sys.stderr)
        return 2

    # One results directory per configuration. Mixing them produces duplicate
    # rows for the same tree with different verdicts.
    configs = {p.parent.parent for p in traces}
    if len(configs) > 1:
        print(f"WARNING: traces come from {len(configs)} different result "
              f"directories; pass --results-dir to isolate one configuration.",
              file=sys.stderr)

    rows = []
    # Column width from the data. A named instance ("adt_nuovo") is longer than
    # the two-digit node counts the fixed width of 8 was sized for, and it
    # shifted every column on its own row.
    w = max(8, max((len(tp.stem.replace("_trace", "")) for tp in traces),
                   default=8))
    print(f"{'tree':>{w}} {'winner':>9} {'verdict':>12} "
          f"{'exec/tot':>9} {'reopened':>9}  reasons")
    print("-" * (w + 88))

    for tp in traces:
        data = json.loads(tp.read_text(encoding="utf-8"))
        summary = data.get("summary", {})
        tree = str(summary.get("tree") or tp.stem.replace("_trace", ""))
        winner = str(summary.get("winner", "?"))

        xml = trees_dir / f"{tree}.xml"
        if not xml.exists():
            print(f"{tree:>{w}} {winner:>9} {'NO-XML':>12}  (expected {xml})")
            continue

        goal, attacks, _defenses = load_attacks(repo_root, xml)
        executed, pre_block = replay(data.get("trace", []))
        res = classify(goal, attacks, data.get("final_state", {}),
                       executed, pre_block)

        if winner != "defender":
            res["verdict"] = f"n/a ({winner})"

        reasons = ", ".join(f"{k}: {v}" for k, v in sorted(res["reasons"].items()))
        ratio = f"{res['n_executed']}/{res['n_attacks']}"
        print(f"{tree:>{w}} {winner:>9} {res['verdict']:>12} "
              f"{ratio:>9} "
              f"{res['n_blocked_open']:>9}  {reasons}")
        if res["would_be_enabled"]:
            print(f"{'':>{w}} attacks reopened without the defenses: "
                  f"{', '.join(res['would_be_enabled'])}")

        rows.append({
            "tree": tree, "winner": winner, "verdict": res["verdict"],
            "attacks_total": res["n_attacks"], "attacks_executed": res["n_executed"],
            "attacks_unused": res["n_unused"],
            "attacks_reopened_without_defenses": res["n_blocked_open"],
            "preconditions_blocked": res["blocked_dims"],
            "defender_cost": summary.get("defender_cost"),
            "attacker_cost": summary.get("attacker_cost"),
            "actions": summary.get("actions"),
        })

    print()
    counts: Dict[str, int] = {}
    for r in rows:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    print("verdicts: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))

    blocked = counts.get("BLOCKED", 0)
    if blocked == len(rows) and rows:
        print("\nEvery defender victory is attributable to the defenses. "
              "The effectiveness claim in the paper stands as written.")
    elif blocked == 0 and rows:
        print("\nNo defender victory is attributable to the defenses. "
              "The effectiveness claim must be reformulated: the attacker was "
              "exhausted or stuck, not stopped.")
    else:
        print("\nMixed outcome. Report the per-instance verdict rather than an "
              "aggregate 'defender victory' statement.")

    if args.csv and rows:
        with open(args.csv, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
        print(f"\nwritten: {args.csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
