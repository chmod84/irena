#!/usr/bin/env python3
"""One entry point for the whole IRENA experimental pipeline.

WHY. Reproducing the paper takes eight commands in a precise order, one of
which has thirteen positional arguments and two of which silently overwrite
each other's outputs (any full-model run rewrites vector_preana_result.csv,
whatever its lambda_D). Every re-run so far has been driven by a hand-written
checklist. This script IS the checklist: it knows the order, the arguments, the
file dance, and what "already done" looks like, so the whole evaluation is

    python3 pipeline.py status      # what exists, what is stale, what would run
    python3 pipeline.py run         # everything stale, in dependency order
    python3 pipeline.py run --only figures
    python3 pipeline.py run --force sweep abstention

The package is SELF-CONTAINED: unpack (or clone) and run, no PANACEA
checkout, no PRISM, no other download needed.

    <package>/
      pipeline.py        <- this file: the only command you need
      run.sh             the same, as a single shell command
      tree_to_prism.py   shipped copy of the R-ADT XML parser (+ tree.py)
      experiment/        trees/*.xml and everything the pipeline produces
                         (CSVs, traces, figures/, reports/)
      runner/            the simulator (run_irena.sh) and its one-shot patch
      analysis/          figures, abstention report, victory attribution,
                         and the declared PANACEA reference values
      diagnostics/       inspection tools (never required by the pipeline)
      docs/              REPLICATION.md, the reviewer guide

Nothing in this package executes, models or regenerates PANACEA. The
reference values every comparison uses (proven minimum costs, model sizes,
solver time and memory) are declared constants in
analysis/panacea_reference.py, transcribed from the reference runs the
paper reports.

STAGES, in order:

  preflight   sanity of the package: corrected parser, corrected LD rule,
              trees present. Runs always; costs nothing.
  policies    IRENA, R-ADT LC and R-ADT LD at the operating lambda_D.
  sweep       IRENA at every other lambda_D in the sweep list. The main CSV and
              results directory are stashed first and restored afterwards, even
              on failure -- the manual backup/restore dance, automated.
  abstention  the abstain=on run plus compare_abstention.py, report saved.
  classify    classify_terminations.py over the IRENA traces, report saved.
  ablations   negotiation ablation and attacker mismatch: the one-step
              defender at every ablation lambda_D; the greedy attacker against
              IRENA, LD, one-step and IRENA with the loss term; that loss term
              against the adaptive attacker. Report saved.
  figures     the paper figures, with the paper's perf/cost exclusions.
  verify      cross-checks the numbers and writes reports/MANIFEST.md: the one
              file to read (or send) after a run.

Staleness is content-based: each stage hashes the scripts and inputs it depends
on into experiment/.pipeline_state.json. Editing the runner re-runs the
simulated stages; editing nothing re-runs nothing. Outputs are also checked to
exist, so deleting a CSV re-runs its stage; and CSVs record their own
defense_cost_weight, so a file produced at the wrong lambda_D is stale no
matter how it is named.

CONFIG. Defaults below match the paper. To change them, `python3 pipeline.py
init` writes pipeline.json next to this script; edit it and re-run. The JSON
overrides keys, it does not replace the dict.

ROUND-TRIP. The package is one folder: zip it (minus __pycache__), send it,
run it anywhere with Python + pandas/numpy/matplotlib (~15 minutes for the
full simulated evaluation), zip it back. Outputs live inside experiment/, so
they travel with the folder, and existing outputs are adopted rather than
recomputed.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

HERE = Path(__file__).resolve().parent          # the package root
EXP = HERE / "experiment"                       # instances + outputs
REPORTS = EXP / "reports"
STATE_FILE = EXP / ".pipeline_state.json"
RUNNER = HERE / "runner" / "run_irena.sh"
ANALYSIS = HERE / "analysis"
PARSER = HERE / "tree_to_prism.py"              # shipped copy of the parser
TREEMOD = HERE / "tree.py"                      # its Node/Tree module

DEFAULTS: Dict[str, object] = {
    # the reported configuration ------------------------------------------
    "operating_lambda": 2e-4,
    "sweep_lambdas": [1e-4, 5e-4, 1e-3, 5e-3, 1e-2, 1e-1],
    # negotiation ablation: the one-step defender at these weights
    "ablation_lambdas": [1e-4, 2e-4, 5e-4],
    "repeats": 3,
    "attack_lambda": 1e-4,
    "max_steps": 60,
    "max_rounds": 40,
    "memory_risk_weight": 0.70,
    "attack_goal_bonus": 1.0,
    # The reported model is the MINIMAL one: neutral coefficient (r_ij = 1)
    # and no relational learning. The dynamic-risk and learning machinery
    # stays available in the runner as ablations; on the paper's five
    # instances it alters no outcome, cost or trajectory (verified).
    "risk_mode": "neutral",
    "attacker_policy": "adaptive",
    "learning_mode": "off",
    "turn_order": "attacker_first",
    # figures ---------------------------------------------------------------
    "trees": ["10", "25", "29", "34", "adt_nuovo"],
    "perf_exclude": ["adt_nuovo"],
    "cost_exclude": [],
    # dataset-specific invariants (IRENA defends everywhere, LC always loses,
    # the artifact traces on adt_nuovo). True for the paper's five instances;
    # set false when running the pipeline on a different tree set.
    "paper_checks": True,
}


def load_config() -> Dict[str, object]:
    cfg = dict(DEFAULTS)
    override = HERE / "pipeline.json"
    if override.exists():
        cfg.update(json.loads(override.read_text(encoding="utf-8")))
    return cfg


# --------------------------------------------------------------------------
# small utilities
# --------------------------------------------------------------------------

def sh(cmd: List[str], cwd: Path, log: Optional[Path] = None) -> int:
    """Run a command, teeing output to a log file when given."""
    print(f"    $ {' '.join(str(c) for c in cmd)}")
    if log:
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("w", encoding="utf-8") as fh:
            p = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, text=True)
            assert p.stdout is not None
            for line in p.stdout:
                fh.write(line)
            return p.wait()
    return subprocess.run(cmd, cwd=cwd).returncode


def file_hash(*paths: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(paths, key=str):
        h.update(str(p).encode())
        if p.exists():
            h.update(p.read_bytes())
        else:
            h.update(b"<missing>")
    return h.hexdigest()[:16]


def load_state() -> Dict[str, str]:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {}


def save_state(state: Dict[str, str]) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def read_csv_rows(path: Path) -> List[Dict[str, str]]:
    if not path.exists():
        return []
    rows = list(csv.DictReader(
        path.read_text(encoding="utf-8-sig", errors="replace").splitlines()))
    for r in rows:
        r.pop(None, None)
    return rows


def csv_covers(path: Path, trees: List[str], lam: Optional[float]) -> bool:
    """True when the CSV has a row for every tree, at the given lambda_D."""
    rows = read_csv_rows(path)
    have = {Path(str(r.get("tree", ""))).stem for r in rows}
    if not set(trees) <= have:
        return False
    if lam is not None:
        for r in rows:
            try:
                if abs(float(r["defense_cost_weight"]) - lam) > 1e-12:
                    return False
            except (KeyError, ValueError):
                return False
    return True


def runner_args(cfg: Dict[str, object], lam: float, abstain: str,
                policy: str) -> List[str]:
    """The runner's thirteen positional arguments, never typed by hand again."""
    return ["bash", str(RUNNER),
            str(cfg["repeats"]), f"{lam:g}", f"{cfg['attack_lambda']:g}",
            str(cfg["max_steps"]), str(cfg["max_rounds"]),
            str(cfg["memory_risk_weight"]), str(cfg["attack_goal_bonus"]),
            str(cfg["risk_mode"]), str(cfg["attacker_policy"]),
            str(cfg["learning_mode"]), str(cfg["turn_order"]),
            abstain, policy]


def policy_csv(cfg: Dict[str, object], policy: str, abstain: str = "off") -> Path:
    """The summary CSV the runner writes for this configuration.

    Same rule as run_irena.sh: only the canonical configuration with the IRENA
    defender gets the plain name; everything else is suffixed.
    """
    canonical = (cfg["risk_mode"] == "neutral"
                 and cfg["attacker_policy"] == "adaptive"
                 and cfg["learning_mode"] == "off"
                 and cfg["turn_order"] == "attacker_first")
    if canonical and policy == "irena" and abstain == "off":
        return EXP / "vector_preana_result.csv"
    suffix = (f"{cfg['risk_mode']}_{cfg['attacker_policy']}"
              f"_learning_{cfg['learning_mode']}_{cfg['turn_order']}")
    if abstain == "on":
        suffix += "_abstain_on"
    if policy != "irena":
        suffix += f"_def_{policy}"
    return EXP / f"vector_preana_{suffix}_result.csv"


# --------------------------------------------------------------------------
# stages
# --------------------------------------------------------------------------

class Stage:
    def __init__(self, name: str, deps: List[str],
                 inputs: Callable[[Dict], str],
                 outputs_ok: Callable[[Dict], Optional[str]],
                 run: Callable[[Dict], None], adoptable: bool = True):
        self.name, self.deps = name, deps
        self.inputs, self.outputs_ok, self.run = inputs, outputs_ok, run
        # Cheap check stages are never "adopted" from a zip: re-running them
        # is free and their whole value is that they actually ran here.
        self.adoptable = adoptable


def preflight_run(cfg: Dict) -> None:
    problems: List[str] = []
    t2p = PARSER
    if not t2p.exists():
        problems.append(f"{t2p} not found: the package ships its own copy "
                        "of the parser; restore it")
    if not TREEMOD.exists():
        problems.append(f"{TREEMOD} not found: the parser needs it; restore it")
    else:
        src = t2p.read_text(encoding="utf-8", errors="replace")
        if "df.loc[df['Label'] == effect][\"Refinement\"]" in src:
            problems.append("tree_to_prism.py still has the refinement bug")
    if not RUNNER.exists():
        problems.append(f"{RUNNER} not found")
    else:
        src = RUNNER.read_text(encoding="utf-8", errors="replace")
        if 'if policy == "ld":' not in src or "candidates = active" not in src:
            problems.append("the runner's LD rule is the old one (ranks every "
                            "enabled defense); irena/runner/patch_ld_rule.py "
                            "fixes it")
    missing = [t for t in cfg["trees"]
               if not (EXP / "trees" / f"{t}.xml").exists()]
    if missing:
        problems.append(f"missing tree XML(s): {', '.join(missing)}")
    try:
        import pandas  # noqa: F401
    except ImportError:
        problems.append("pandas is not importable; the runner needs it")
    if problems:
        raise RuntimeError("preflight failed:\n  - " + "\n  - ".join(problems))


def policies_ok(cfg: Dict) -> Optional[str]:
    lam = float(cfg["operating_lambda"])
    for pol in ("irena", "lc", "ld"):
        p = policy_csv(cfg, pol)
        if not csv_covers(p, cfg["trees"], lam):
            return f"{p.name}: missing, incomplete, or not at lambda_D={lam:g}"
    return None


def policies_run(cfg: Dict) -> None:
    lam = float(cfg["operating_lambda"])
    for pol in ("irena", "lc", "ld"):
        if csv_covers(policy_csv(cfg, pol), cfg["trees"], lam):
            print(f"    {pol}: already at lambda_D={lam:g}, skipping")
            continue
        if sh(runner_args(cfg, lam, "off", pol), cwd=HERE,
              log=REPORTS / f"policy_{pol}.log"):
            raise RuntimeError(f"{pol} run failed; see reports/policy_{pol}.log")


def sweep_files(cfg: Dict) -> Dict[float, Path]:
    return {float(l): EXP / f"sweep_lambdaD_{float(l):g}.csv"
            for l in cfg["sweep_lambdas"]}


def sweep_ok(cfg: Dict) -> Optional[str]:
    for lam, path in sweep_files(cfg).items():
        if not csv_covers(path, cfg["trees"], lam):
            return f"{path.name}: missing, incomplete, or at the wrong lambda_D"
    return None


def sweep_run(cfg: Dict) -> None:
    """The stash dance the sweep has always required, made unforgettable.

    The runner writes the full model's output to vector_preana_result.csv and
    vector_preana_results/ REGARDLESS of lambda_D, so sweeping would destroy
    the operating point's CSV and traces. Stash both, run the sweep, restore
    in a finally block so even a crash cannot lose them.
    """
    main_csv = EXP / "vector_preana_result.csv"
    main_dir = EXP / "vector_preana_results"
    stash = EXP / ".sweep_stash"
    todo = {lam: p for lam, p in sweep_files(cfg).items()
            if not csv_covers(p, cfg["trees"], lam)}
    if not todo:
        return
    if stash.exists():
        shutil.rmtree(stash)
    stash.mkdir()
    stashed = False
    try:
        if main_csv.exists():
            shutil.copy2(main_csv, stash / main_csv.name)
            stashed = True
        if main_dir.exists():
            shutil.copytree(main_dir, stash / main_dir.name)
        for lam, path in sorted(todo.items()):
            print(f"    lambda_D = {lam:g}")
            if sh(runner_args(cfg, lam, "off", "irena"), cwd=HERE,
                  log=REPORTS / f"sweep_{lam:g}.log"):
                raise RuntimeError(f"sweep run at {lam:g} failed; "
                                   f"see reports/sweep_{lam:g}.log")
            shutil.move(str(main_csv), str(path))
    finally:
        if stashed:
            shutil.copy2(stash / main_csv.name, main_csv)
            if (stash / main_dir.name).exists():
                if main_dir.exists():
                    shutil.rmtree(main_dir)
                shutil.copytree(stash / main_dir.name, main_dir)
            shutil.rmtree(stash)
        elif stash.exists():
            shutil.rmtree(stash)


def abstention_ok(cfg: Dict) -> Optional[str]:
    lam = float(cfg["operating_lambda"])
    p = policy_csv(cfg, "irena", abstain="on")
    if not csv_covers(p, cfg["trees"], lam):
        return f"{p.name}: missing, incomplete, or not at lambda_D={lam:g}"
    if not (REPORTS / "abstention.txt").exists():
        return "reports/abstention.txt missing"
    return None


def abstention_run(cfg: Dict) -> None:
    lam = float(cfg["operating_lambda"])
    p = policy_csv(cfg, "irena", abstain="on")
    if not csv_covers(p, cfg["trees"], lam):
        if sh(runner_args(cfg, lam, "on", "irena"), cwd=HERE,
              log=REPORTS / "policy_abstain.log"):
            raise RuntimeError("abstention run failed; "
                               "see reports/policy_abstain.log")
    if sh(["python3", str(ANALYSIS / "compare_abstention.py"),
           "--experiment-dir", str(EXP)], cwd=HERE,
          log=REPORTS / "abstention.txt"):
        raise RuntimeError("compare_abstention.py failed; "
                           "see reports/abstention.txt")


def classify_ok(cfg: Dict) -> Optional[str]:
    return None if (REPORTS / "classify.txt").exists() else \
        "reports/classify.txt missing"


def classify_run(cfg: Dict) -> None:
    if sh(["python3", str(ANALYSIS / "classify_terminations.py"),
           "--repo-root", str(HERE),
           "--results-dir", str(EXP / "vector_preana_results"),
           "--trees-dir", str(EXP / "trees")], cwd=HERE,
          log=REPORTS / "classify.txt"):
        raise RuntimeError("classify_terminations.py failed; "
                           "see reports/classify.txt")


def greedy_cfg(cfg: Dict) -> Dict:
    """The reported configuration with the greedy one-step attacker."""
    return dict(cfg, attacker_policy="greedy")


def results_dir(csv_path: Path) -> Path:
    """The trace directory that goes with a summary CSV."""
    n = csv_path.name
    if n.endswith("_result.csv"):
        return csv_path.parent / (n[: -len("_result.csv")] + "_results")
    return csv_path.parent / (n[: -len(".csv")] + "_results")


def ablation_files(cfg: Dict) -> Dict[str, Path]:
    """Every CSV the ablations stage produces, by label."""
    files = {f"onestep@{float(l):g}":
             EXP / f"ablation_onestep_lambdaD_{float(l):g}.csv"
             for l in cfg["ablation_lambdas"]}
    files["irena_loss"] = policy_csv(cfg, "irena_loss")
    for pol in ("irena", "ld", "onestep", "irena_loss"):
        files[f"greedy/{pol}"] = policy_csv(greedy_cfg(cfg), pol)
    return files


ABLATION_LABELS = {"irena_loss": "IRENA + loss term (adaptive attacker)",
                   "greedy/irena": "greedy attacker vs IRENA",
                   "greedy/ld": "greedy attacker vs R-ADT LD",
                   "greedy/onestep": "greedy attacker vs one-step",
                   "greedy/irena_loss": "greedy attacker vs IRENA + loss term"}


def ablation_label(key: str) -> str:
    if key.startswith("onestep@"):
        return f"one-step defender, lambda_D = {key.split('@')[1]}"
    return ABLATION_LABELS.get(key, key)


def ablations_ok(cfg: Dict) -> Optional[str]:
    lam = float(cfg["operating_lambda"])
    for key, path in ablation_files(cfg).items():
        want = float(key.split("@")[1]) if key.startswith("onestep@") else lam
        if not csv_covers(path, cfg["trees"], want):
            return f"{path.name}: missing, incomplete, or at the wrong lambda_D"
    if not (REPORTS / "ablations.txt").exists():
        return "reports/ablations.txt missing"
    return None


def ablations_run(cfg: Dict) -> None:
    """Negotiation ablation and attacker mismatch.

    The one-step runs land on the same runner paths whatever lambda_D is (as
    in the sweep), so each CSV and its traces are moved to
    ablation_onestep_lambdaD_<l>.csv / ..._results before the next run. The
    greedy-attacker and loss-term runs have configuration-specific paths.
    """
    lam = float(cfg["operating_lambda"])
    files = ablation_files(cfg)
    run_csv = policy_csv(cfg, "onestep")
    for l in (float(x) for x in cfg["ablation_lambdas"]):
        dest = files[f"onestep@{l:g}"]
        if csv_covers(dest, cfg["trees"], l):
            print(f"    one-step at lambda_D={l:g}: present, skipping")
            continue
        if sh(runner_args(cfg, l, "off", "onestep"), cwd=HERE,
              log=REPORTS / f"ablation_onestep_{l:g}.log"):
            raise RuntimeError(f"one-step run at {l:g} failed; "
                               f"see reports/ablation_onestep_{l:g}.log")
        if results_dir(dest).exists():
            shutil.rmtree(results_dir(dest))
        shutil.move(str(run_csv), str(dest))
        shutil.move(str(results_dir(run_csv)), str(results_dir(dest)))
    jobs = [("irena_loss", cfg, "irena_loss")] + \
        [(f"greedy/{p}", greedy_cfg(cfg), p)
         for p in ("irena", "ld", "onestep", "irena_loss")]
    for key, c, pol in jobs:
        if csv_covers(files[key], cfg["trees"], lam):
            print(f"    {key}: present, skipping")
            continue
        log = REPORTS / f"ablation_{key.replace('/', '_')}.log"
        if sh(runner_args(c, lam, "off", pol), cwd=HERE, log=log):
            raise RuntimeError(f"{key} run failed; see reports/{log.name}")
    if sh(["python3", str(ANALYSIS / "ablations.py"),
           "--experiment-dir", str(EXP), "--trees", *cfg["trees"],
           "--operating-lambda", f"{lam:g}",
           "--ablation-lambdas",
           *[f"{float(l):g}" for l in cfg["ablation_lambdas"]],
           "--risk-mode", str(cfg["risk_mode"]),
           "--learning-mode", str(cfg["learning_mode"]),
           "--turn-order", str(cfg["turn_order"])],
          cwd=HERE, log=REPORTS / "ablations.txt"):
        raise RuntimeError("ablations.py failed; see reports/ablations.txt")


def figures_ok(cfg: Dict) -> Optional[str]:
    figdir = EXP / "figures"
    # The exact names the paper's main.tex includes, so a reviewer can point
    # Overleaf at figures/ and rebuild the PDF unchanged.
    for stem in ("perf_state_space", "perf_planning_time", "perf_peak_memory",
                 "eff_defender_cost", "eff_lambda_sweep",
                 "eff_cost_breakdown", "eff_cost_vs_optimum"):
        if not (figdir / f"{stem}.pdf").exists():
            return f"figures/{stem}.pdf missing"
    return None


def figures_run(cfg: Dict) -> None:
    cmd = ["python3", str(ANALYSIS / "make_run_figures.py"),
           "--experiment-dir", str(EXP),
           "--trees", *cfg["trees"],
           "--operating-lambda", f"{float(cfg['operating_lambda']):g}",
           "--risk-mode", str(cfg["risk_mode"]),
           "--attacker-policy", str(cfg["attacker_policy"]),
           "--learning-mode", str(cfg["learning_mode"]),
           "--turn-order", str(cfg["turn_order"])]
    if cfg["perf_exclude"]:
        cmd += ["--perf-exclude", *cfg["perf_exclude"]]
    if cfg["cost_exclude"]:
        cmd += ["--cost-exclude", *cfg["cost_exclude"]]
    if sh(cmd, cwd=HERE, log=REPORTS / "figures.txt"):
        raise RuntimeError("figure generation failed; see reports/figures.txt")


# Reference traces from the PANACEA artifact, used to re-validate the
# baselines on every run. Hard-coded because they are published data.
REF_LC = ["webRecon", "updateApache", "bufferOverflow",
          "disableCGIScripts", "exfiltrateData"]
REF_LD = ["webRecon", "deactivateSOCKS5Proxy", "pathTraversal",
          "encryptFile", "getFiles", "changeFilePermissions"]


def trace_actions(trace_json: Path) -> List[str]:
    data = json.loads(trace_json.read_text(encoding="utf-8"))
    out = []
    for e in data.get("trace", []):
        a = e.get("action") or e.get("defense") or e.get("attack")
        if a:
            out.append(str(a))
    return out


def verify_run(cfg: Dict) -> None:
    lam = float(cfg["operating_lambda"])
    checks: List[str] = []
    failed = False

    def check(name: str, ok: bool, detail: str = "") -> None:
        nonlocal failed
        mark = "PASS" if ok else "FAIL"
        failed |= not ok
        checks.append(f"- {mark}  {name}" + (f" -- {detail}" if detail else ""))

    rows = {pol: {Path(str(r["tree"])).stem: r
                  for r in read_csv_rows(policy_csv(cfg, pol))}
            for pol in ("irena", "lc", "ld")}

    check("every policy CSV is at the operating lambda_D",
          all(csv_covers(policy_csv(cfg, p), cfg["trees"], lam)
              for p in ("irena", "lc", "ld")))

    if cfg.get("paper_checks", True):
        check("IRENA defends every instance",
              all(rows["irena"][t]["winner"] == "defender"
                  for t in cfg["trees"]))
        check("R-ADT LC loses every instance (cost-blind rule)",
              all(rows["lc"][t]["winner"] == "attacker" for t in cfg["trees"]))
        # baseline validation against the artifact traces, on the semantic tree
        for pol, ref in (("lc", REF_LC), ("ld", REF_LD)):
            suffix = policy_csv(cfg, pol).name.replace("_result.csv",
                                                       "_results")
            tj = EXP / suffix / "adt_nuovo" / "adt_nuovo_trace.json"
            if tj.exists():
                got = trace_actions(tj)
                check(f"{pol.upper()} reproduces the reference trace "
                      f"on adt_nuovo", got == ref,
                      "got: " + " -> ".join(got) if got != ref else "")
            else:
                check(f"{pol.upper()} trace present for adt_nuovo", False,
                      str(tj.relative_to(EXP)))
    else:
        checks.append("- SKIP  paper-specific invariants (paper_checks=false)")

    # ---- ablations (paper: Negotiation Ablation; Limitations) -------------
    abl = ablation_files(cfg)

    def by_tree(path: Path) -> Dict[str, Dict[str, str]]:
        return {Path(str(r["tree"])).stem: r for r in read_csv_rows(path)}

    def outcome(r):
        return (r["winner"], float(r["defender_cost"])) if r else None

    def moves(path: Path, tree: str):
        tj = results_dir(path) / tree / f"{tree}_trace.json"
        return trace_actions(tj) if tj.exists() else None

    abl_rows = {k: by_tree(p) for k, p in abl.items()}
    have = all(csv_covers(p, cfg["trees"], None) for p in abl.values())
    check("ablation outputs present (one-step, greedy attacker, loss term)", have)
    if have and cfg.get("paper_checks", True):
        T, ir, ldr = cfg["trees"], rows["irena"], rows["ld"]
        lams = [float(l) for l in cfg["ablation_lambdas"]]
        check("no tested lambda_D lets the one-step defender match IRENA",
              all(any(outcome(abl_rows[f"onestep@{l:g}"].get(t))
                      != outcome(ir.get(t)) for t in T) for l in lams))
        if 1e-4 in lams:
            check("at lambda_D = 1e-4 the one-step defender reproduces "
                  "R-ADT LD move for move",
                  all(moves(abl["onestep@0.0001"], t)
                      == moves(policy_csv(cfg, "ld"), t) for t in T))
        op = abl_rows.get(f"onestep@{lam:g}")
        if op:
            same = [t for t in T if outcome(op.get(t)) == outcome(ir.get(t))]
            check("at the operating point the one-step defender matches IRENA "
                  "on 10, 25 and adt_nuovo, spends more on 29, loses 34",
                  same == ["10", "25", "adt_nuovo"]
                  and op["29"]["winner"] == "defender"
                  and float(op["29"]["defender_cost"])
                  > float(ir["29"]["defender_cost"])
                  and op["34"]["winner"] == "attacker",
                  "matches IRENA on: " + (", ".join(same) or "none"))
            adaptive = {"irena": ir, "ld": ldr, "onestep": op}
            check("greedy attacker: IRENA, LD and one-step keep outcome and "
                  "cost on every instance but 34",
                  all(outcome(abl_rows[f"greedy/{p}"].get(t))
                      == outcome(adaptive[p].get(t))
                      for p in adaptive for t in T if t != "34"))
            check("greedy attacker: IRENA, LD and one-step all lose the "
                  "34-node instance",
                  all(abl_rows[f"greedy/{p}"]["34"]["winner"] == "attacker"
                      for p in adaptive))
        check("loss term: IRENA with the loss term reproduces IRENA move for move",
              all(moves(abl["irena_loss"], t) == moves(policy_csv(cfg, "irena"), t)
                  for t in T))
        check("loss term: the 34-node instance is defended against the greedy "
              "attacker",
              abl_rows["greedy/irena_loss"]["34"]["winner"] == "defender")

    # the sweep and the operating point must agree where they overlap
    ok_line = (REPORTS / "figures.txt").exists() and \
        f"lambda_D={lam:g}" in (REPORTS / "figures.txt").read_text(
            errors="replace")
    check("figures were generated at the reported configuration", ok_line)

    cls = (REPORTS / "classify.txt")
    if cls.exists():
        text = cls.read_text(errors="replace")
        m = re.search(r"BLOCKED=(\d+)", text)
        check("every defender victory is BLOCKED (attributable)",
              bool(m) and int(m.group(1)) == len(cfg["trees"]),
              m.group(0) if m else "verdict line not found")
    else:
        check("classification report present", False)

    # ---- manifest --------------------------------------------------------
    lines = ["# Pipeline manifest", "",
             f"Generated by pipeline.py on {time.strftime('%Y-%m-%d %H:%M')}",
             f"Operating point lambda_D = {lam:g}, repeats = {cfg['repeats']}",
             "", "## Checks", "", *checks, "",
             "## Outcome and defense cost at the operating point", "",
             "| tree | IRENA | LC | LD |", "|---|---|---|---|"]
    for t in cfg["trees"]:
        cells = []
        for pol in ("irena", "lc", "ld"):
            r = rows[pol].get(t)
            cells.append(f"{float(r['defender_cost']):g} "
                         f"({'W' if r['winner'] == 'defender' else 'L'})"
                         if r else "--")
        lines.append(f"| {t} | " + " | ".join(cells) + " |")
    lines += ["", "## Ablations (detail in reports/ablations.txt)", "",
              "| run | " + " | ".join(cfg["trees"]) + " |",
              "|---|" + "---|" * len(cfg["trees"])]
    for key, path in ablation_files(cfg).items():
        rws = {Path(str(r["tree"])).stem: r for r in read_csv_rows(path)}
        cells = [f"{float(rws[t]['defender_cost']):g} "
                 f"({'W' if rws[t]['winner'] == 'defender' else 'L'})"
                 if t in rws else "--" for t in cfg["trees"]]
        lines.append(f"| {ablation_label(key)} | " + " | ".join(cells) + " |")
    lines += ["", "## Reports", ""]
    for name in ("abstention.txt", "classify.txt", "ablations.txt", "figures.txt"):
        lines.append(f"- reports/{name}"
                     + ("" if (REPORTS / name).exists() else "  (missing)"))
    (REPORTS / "MANIFEST.md").write_text("\n".join(lines) + "\n",
                                         encoding="utf-8")
    print("    wrote reports/MANIFEST.md")
    if failed:
        raise RuntimeError("verification checks failed; see reports/MANIFEST.md")


def build_stages(cfg: Dict) -> List[Stage]:
    trees = [EXP / "trees" / f"{t}.xml" for t in cfg["trees"]]
    lam_key = f"{float(cfg['operating_lambda']):g}|{cfg['repeats']}"
    return [
        Stage("preflight", [],
              lambda c: file_hash(PARSER, TREEMOD, RUNNER, *trees),
              lambda c: None, preflight_run, adoptable=False),
        Stage("policies", ["preflight"],
              lambda c: file_hash(RUNNER, *trees) + lam_key,
              policies_ok, policies_run),
        Stage("sweep", ["preflight"],
              lambda c: file_hash(RUNNER, *trees)
              + "|".join(f"{float(l):g}" for l in c["sweep_lambdas"])
              + f"|{c['repeats']}",
              sweep_ok, sweep_run),
        # Downstream stages hash the CSVs they consume, not just the scripts:
        # a policies re-run changes those files, which makes every consumer
        # stale automatically -- no manual "now redo the figures".
        Stage("abstention", ["policies"],
              lambda c: file_hash(RUNNER, ANALYSIS / "compare_abstention.py",
                                  policy_csv(c, "irena")) + lam_key,
              abstention_ok, abstention_run),
        Stage("classify", ["policies"],
              lambda c: file_hash(ANALYSIS / "classify_terminations.py",
                                  policy_csv(c, "irena")) + lam_key,
              classify_ok, classify_run),
        Stage("ablations", ["policies"],
              lambda c: file_hash(RUNNER, ANALYSIS / "ablations.py",
                                  ANALYSIS / "panacea_reference.py", *trees,
                                  policy_csv(c, "irena"), policy_csv(c, "ld"))
              + lam_key + "|"
              + "|".join(f"{float(l):g}" for l in c["ablation_lambdas"]),
              ablations_ok, ablations_run),
        Stage("figures", ["policies", "sweep"],
              lambda c: file_hash(
                  ANALYSIS / "make_run_figures.py",
                  ANALYSIS / "make_figures.py",
                  ANALYSIS / "panacea_reference.py",
                  *(policy_csv(c, p) for p in ("irena", "lc", "ld")),
                  *sweep_files(c).values())
              + lam_key + ",".join(c["trees"]) + ",".join(c["cost_exclude"])
              + ",".join(c["perf_exclude"]),
              figures_ok, figures_run),
        Stage("verify", ["policies", "abstention", "classify", "ablations",
                         "figures"],
              lambda c: "always",
              lambda c: None if (REPORTS / "MANIFEST.md").exists()
              else "reports/MANIFEST.md missing",
              verify_run, adoptable=False),
    ]


# --------------------------------------------------------------------------


def stage_status(st: Stage, cfg: Dict, state: Dict[str, str]) -> str:
    bad = st.outputs_ok(cfg)
    if bad:
        return f"stale: {bad}"
    if st.name not in state and st.adoptable:
        # Outputs exist but no run of THIS pipeline produced them -- the
        # normal situation right after unpacking a zip built elsewhere. Adopt
        # them instead of re-running: for the PANACEA stage a re-run would
        # cost an hour and 53 GB to rebuild something already on disk. The
        # hash is recorded on the next `run`, and from then on input changes
        # are detected as usual. (.pipeline_state.json lives inside
        # experiment/ precisely so it travels with the folder.)
        return "ok (existing outputs adopted; state recorded on next run)"
    if state.get(st.name) != st.inputs(cfg):
        return ("stale: inputs changed since the recorded run"
                if st.name in state else "stale: never ran here")
    return "ok"


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="show every stage's state; run nothing")
    sub.add_parser("init", help="write pipeline.json with the defaults")
    runp = sub.add_parser("run", help="run stale stages in dependency order")
    runp.add_argument("--only", nargs="*", default=None,
                      help="restrict to these stages (deps must be ok)")
    runp.add_argument("--force", nargs="*", default=[],
                      help="treat these stages as stale even if ok")
    runp.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    cfg = load_config()
    REPORTS.mkdir(parents=True, exist_ok=True)
    stages = build_stages(cfg)
    by_name = {s.name: s for s in stages}
    state = load_state()

    if args.cmd == "init":
        out = HERE / "pipeline.json"
        if out.exists():
            print(f"{out} already exists; not overwriting")
            return 1
        out.write_text(json.dumps(DEFAULTS, indent=2), encoding="utf-8")
        print(f"wrote {out}; edit it and re-run")
        return 0

    if args.cmd == "status":
        print(f"package: {HERE}")
        print(f"operating lambda_D = {cfg['operating_lambda']:g}, "
              f"repeats = {cfg['repeats']}, "
              f"sweep = {[f'{float(l):g}' for l in cfg['sweep_lambdas']]}")
        for st in stages:
            print(f"  {st.name:<11} {stage_status(st, cfg, state)}")
        return 0

    unknown = [n for n in (args.only or []) + args.force if n not in by_name]
    if unknown:
        print(f"unknown stage(s): {', '.join(unknown)}", file=sys.stderr)
        return 2

    todo: List[Stage] = []
    for st in stages:
        adopted = st.adoptable and \
            stage_status(st, cfg, state).startswith("ok (existing")
        if adopted and st.name not in args.force:
            state[st.name] = st.inputs(cfg)
            save_state(state)
            print(f"[{st.name}] adopted existing outputs")
            continue
        if args.only is not None and st.name not in args.only:
            continue
        if st.name in args.force or not stage_status(st, cfg, state).startswith("ok"):
            todo.append(st)
    if not todo:
        print("nothing to do: every requested stage is up to date")
        return 0

    print("will run: " + " -> ".join(s.name for s in todo))
    if args.dry_run:
        return 0

    for st in todo:
        for dep in st.deps:
            if not stage_status(by_name[dep], cfg, state).startswith("ok") \
                    and by_name[dep] not in todo:
                print(f"ERROR: {st.name} depends on {dep}, which is stale and "
                      f"not selected", file=sys.stderr)
                return 2

    for st in todo:
        print(f"[{st.name}]")
        t0 = time.time()
        try:
            st.run(cfg)
        except Exception as exc:  # a stage must never crash the driver silently
            print(f"\nFAILED in {st.name}: {exc}", file=sys.stderr)
            return 1
        state[st.name] = st.inputs(cfg)
        save_state(state)
        print(f"    done in {time.time() - t0:.1f}s")
    print("\nall requested stages completed; read reports/MANIFEST.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
