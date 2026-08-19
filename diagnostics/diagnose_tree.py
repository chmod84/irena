#!/usr/bin/env python3
"""Explain why a Vector-PREANA episode ended the way it did.

Written to settle one question: when an episode ends after a single attack with
an attacker victory, is that

  (H1) a property of the benchmark -- the R-ADT contains an attack whose effect
       is the attacker goal and whose preconditions already hold in the initial
       state, so under `attacker_first` the attacker wins before the defender
       has ever moved. The draft anticipates exactly this ("a synthetic tree can
       contain unusual game structures, including an attack to the root that is
       already enabled at the initial state"). Not a bug.

  (H2) a bug -- get_info returns an EMPTY precondition list for some attacks,
       and attack_enabled() reads

           pre = [str(x) for x in info["preconditions"]]
           if not pre:
               return True

       so those attacks are enabled unconditionally. In an R-ADT every action
       node has exactly one precondition node as its parent, so an empty list
       means "not populated by the parser", not "no precondition". This is the
       same pattern already confirmed for defenses, where every defense carries
       zero preconditions and is therefore always enabled.

The two are distinguished by one number: how many attacks have an empty
precondition list, and whether the attack that ended the episode is one of them.

The script also reports whether a defender-first turn order would have changed
the outcome, and which configuration actually produced the archived trace.

Usage
-----
  python3 diagnose_tree.py --repo-root /path/to/PANACEA \\
      --tree 29 \\
      --trees-dir  /path/to/PANACEA/experiments/experiment3/trees \\
      --results-dir /path/to/PANACEA/experiments/experiment3/BACKUP_results

--results-dir is optional; without it only the static analysis is printed.
Repeat --tree to inspect several trees in one run.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Mapping, Set


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
    for c in (repo_root / "experiments" / "experiment3", repo_root / "experiment3"):
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


def load(repo_root: Path, xml: Path):
    sys.path.insert(0, str(repo_root))
    from tree_to_prism import parse_file, get_info  # type: ignore

    df = parse_file(str(xml)).to_dataframe()
    (goal, _a2g, initial, attacks, defenses, _x, _y) = get_info(df)
    attrs = sorted({
        str(v) for v in
        df.loc[(df["Role"] == "Attacker") & (df["Type"] == "Attribute"), "Label"].values
    })
    return {
        "goal": str(goal),
        "dims": [str(goal)] + attrs,
        "initial": [str(a) for a in initial],
        "attacks": attacks,
        "defenses": defenses,
    }


def initial_state(m) -> Dict[str, int]:
    s = {d: 0 for d in m["dims"]}
    for a in m["initial"]:
        if a in s:
            s[a] = 1
    return s


def attack_enabled(m, action, state: Mapping[str, int], executed: Set[str]) -> bool:
    if action in executed:
        return False
    info = m["attacks"][action]
    effect = str(info["effect"])
    if int(state.get(m["goal"], 0)) == 1:
        return False
    if int(state.get(effect, 0)) != 0:
        return False
    pre = [str(x) for x in info["preconditions"]]
    if not pre:
        return True
    vals = [int(state.get(p, 0)) == 1 for p in pre]
    return any(vals) if str(info["refinement"]) == "disjunctive" else all(vals)


def defense_enabled(m, action, state: Mapping[str, int]) -> bool:
    if int(state.get(m["goal"], 0)) == 1:
        return False
    info = m["defenses"][action]
    effect = str(info["effect"])
    if effect in state and int(state.get(effect, 0)) == 2:
        return False
    pre = [str(x) for x in info["preconditions"]]
    if not pre:
        return True
    vals = [int(state.get(p, 0)) == 1 for p in pre]
    return any(vals) if str(info["refinement"]) == "disjunctive" else all(vals)


def apply_defense(m, state, action) -> Dict[str, int]:
    s = dict(state)
    eff = str(m["defenses"][action]["effect"])
    if eff in s:
        s[eff] = 2
    return s


def diagnose(repo_root: Path, xml: Path, trace_path: Path | None) -> None:
    name = xml.stem
    print()
    print("=" * 74)
    print(f"TREE {name}")
    print("=" * 74)

    m = load(repo_root, xml)
    goal = m["goal"]
    st0 = initial_state(m)
    attacks, defenses = m["attacks"], m["defenses"]

    print(f"  goal (root)          : {goal}")
    print(f"  state dimensions     : {len(m['dims'])}")
    print(f"  attacks / defenses   : {len(attacks)} / {len(defenses)}")
    print(f"  initially active     : {sorted(m['initial'])}")

    # ---- H2 evidence -----------------------------------------------------
    empty_pre_a = [a for a, i in attacks.items() if not list(i["preconditions"])]
    empty_pre_d = [d for d, i in defenses.items() if not list(i["preconditions"])]
    print()
    print(f"  attacks with EMPTY precondition list : "
          f"{len(empty_pre_a)}/{len(attacks)}  {sorted(empty_pre_a)[:12]}")
    print(f"  defenses with EMPTY precondition list: "
          f"{len(empty_pre_d)}/{len(defenses)}")

    # ---- who can act at t=0 ---------------------------------------------
    enabled0 = [a for a in attacks if attack_enabled(m, a, st0, set())]
    root_hits = [a for a, i in attacks.items() if str(i["effect"]) == goal]
    root_hits_enabled0 = [a for a in root_hits if a in enabled0]

    print()
    print(f"  attacks enabled at t=0               : {len(enabled0)} "
          f"{sorted(enabled0)[:12]}")
    print(f"  attacks whose effect IS the root     : {len(root_hits)} "
          f"{sorted(root_hits)}")
    print(f"  ...of which enabled at t=0           : {len(root_hits_enabled0)} "
          f"{sorted(root_hits_enabled0)}")

    for a in sorted(root_hits):
        i = attacks[a]
        flag = "ENABLED AT t=0" if a in enabled0 else "not yet enabled"
        print(f"      {a}: pre={[str(x) for x in i['preconditions']]} "
              f"ref={i['refinement']} cost={i['cost']}  -> {flag}")

    # ---- verdict on H1 vs H2 --------------------------------------------
    print()
    if root_hits_enabled0:
        via_empty = [a for a in root_hits_enabled0 if a in empty_pre_a]
        if via_empty:
            print("  >>> H2 (BUG). A root-reaching attack is enabled at t=0 ONLY")
            print("      because its precondition list came back empty:")
            print(f"        {sorted(via_empty)}")
            print("      In an R-ADT an action node has exactly one precondition")
            print("      parent, so the empty list is a parser gap, not a genuine")
            print("      absence of preconditions. attack_enabled()'s")
            print("      `if not pre: return True` then hands the attacker an")
            print("      immediate win. Fix the enablement before rerunning.")
        else:
            print("  >>> H1 (benchmark property). A root-reaching attack has")
            print("      genuine preconditions that already hold at t=0:")
            for a in root_hits_enabled0:
                print(f"        {a}: pre="
                      f"{[str(x) for x in attacks[a]['preconditions']]}")
            print("      Under attacker_first the attacker wins before the")
            print("      defender moves. Not a code bug; the tree is a poor")
            print("      intrusion-response scenario, as the draft already notes.")
    else:
        print("  >>> No root-reaching attack is enabled at t=0. An immediate")
        print("      attacker victory cannot be explained by the initial state;")
        print("      inspect the trace below.")

    # ---- would defender_first have helped? -------------------------------
    if root_hits_enabled0:
        print()
        d0 = [d for d in defenses if defense_enabled(m, d, st0)]
        savers = []
        for d in d0:
            after = apply_defense(m, st0, d)
            if not [a for a in root_hits if attack_enabled(m, a, after, set())]:
                savers.append((d, defenses[d]["cost"]))
        print(f"  defenses enabled at t=0              : {len(d0)}")
        if savers:
            savers.sort(key=lambda x: x[1])
            print("  defenses that would close EVERY root-reaching attack:")
            for d, c in savers[:8]:
                print(f"      {d} (cost {c})")
            print("  => a defender-first turn order changes the outcome on this")
            print("     tree. Whichever order is used must be stated in the paper")
            print("     and used consistently for both frameworks.")
        else:
            print("  => no single enabled defense closes every root-reaching")
            print("     attack; defender_first alone would not save the episode.")

    # ---- what the archived trace actually did ----------------------------
    if trace_path and trace_path.exists():
        data = json.loads(trace_path.read_text(encoding="utf-8"))
        s = data.get("summary", {})
        print()
        print("  ---- archived trace ----")
        keys = ["package_version", "winner", "attacker_cost", "defender_cost",
                "actions", "risk_mode", "attacker_policy", "learning_mode",
                "turn_order", "defense_cost_weight", "attack_cost_weight",
                "attack_goal_bonus", "repeats"]
        for k in keys:
            if k in s:
                print(f"    {k:<22}= {s[k]}")
        print("    steps:")
        for step in data.get("trace", []):
            print(f"      {step['player'][:3].upper()}: "
                  f"{str(step.get('action')):<10} cost={step.get('cost', 0):<7g}"
                  f"{'  ' + step['note'] if step.get('note') else ''}")

        first = next((x for x in data.get("trace", [])
                      if x.get("player") == "attacker" and x.get("action")), None)
        if first and s.get("winner") == "attacker":
            a = str(first["action"])
            print()
            print(f"    first executed attack = {a}")
            if a in attacks:
                print(f"      effect={attacks[a]['effect']}  "
                      f"pre={[str(x) for x in attacks[a]['preconditions']]}  "
                      f"empty_pre={a in empty_pre_a}")
        if str(s.get("turn_order")) != "attacker_first":
            print()
            print(f"    !! this trace was produced with turn_order="
                  f"{s.get('turn_order')}, which is NOT the order described by")
            print("       Algorithm 1 in the draft.")
    elif trace_path:
        print(f"\n  (no trace at {trace_path})")


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo-root", type=Path, default=None,
                    help="PANACEA root; auto-detected when omitted")
    ap.add_argument("--trees-dir", type=Path, default=None)
    ap.add_argument("--results-dir", type=Path, default=None,
                    help="default: experiment3/vector_preana_results")
    ap.add_argument("--tree", action="append", default=None,
                    help="tree name without extension; repeatable. "
                         "Default: 10, 25, 29")
    args = ap.parse_args()

    repo_root = find_repo_root(args.repo_root)
    experiment_dir = find_experiment_dir(repo_root)
    trees_dir = find_trees_dir(repo_root, experiment_dir, args.trees_dir)
    results_dir = (args.results_dir.resolve() if args.results_dir
                   else experiment_dir / "vector_preana_results")

    print(f"repo root:   {repo_root}")
    print(f"trees dir:   {trees_dir}")
    print(f"results dir: {results_dir}"
          f"{'' if results_dir.is_dir() else '  (assente: solo analisi statica)'}")

    trees = args.tree or ["10", "25", "29"]
    for t in trees:
        xml = trees_dir / f"{t}.xml"
        if not xml.exists():
            print(f"missing {xml}", file=sys.stderr)
            continue
        trace = results_dir / t / f"{t}_trace.json"
        diagnose(repo_root, xml, trace if results_dir.is_dir() else None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
