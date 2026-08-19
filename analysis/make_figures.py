#!/usr/bin/env python3
"""Create PANACEA vs Vector-PREANA Experiment 3 figures.

This script is compatible with the current run_vector_preana_experiment3.sh,
including its configuration-dependent aggregate CSV names:

  full default:
    experiment3/vector_preana_result.csv

  ablations:
    experiment3/vector_preana_<risk>_<attacker>_learning_<on|off>_<turn_order>_result.csv

It generates two paper-style figures:
  1. cumulative attacker/defender action costs with W/L labels;
  2. PANACEA PRISM model-checking time vs Vector-PREANA cumulative median
     online planning time.

Place this file in PANACEA/experiments/, next to run_experiment3.sh.

Examples
--------
Defender-first diagnostic:
  python3 compare_panacea_vector_preana_experiment3.py \
      --risk-mode neutral --attacker-policy greedy \
      --learning-mode off --turn-order defender_first

Attacker-first diagnostic:
  python3 compare_panacea_vector_preana_experiment3.py \
      --risk-mode neutral --attacker-policy greedy \
      --learning-mode off --turn-order attacker_first

Full model:
  python3 compare_panacea_vector_preana_experiment3.py

By default, trees 10, 25, and 29 are plotted when available. Use --trees to
change the selection.
"""

from __future__ import annotations

import argparse
import csv
import math
import re
import shutil
import sys
import warnings
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import matplotlib.lines as mlines
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import numpy as np


# A plain `"_time" in path` test also matches "_no_time", because "_no_time"
# contains "_time". That silently discarded every untimed model whose name
# follows PANACEA's <tree>_no_time convention. The negative lookbehind keeps
# "<tree>_no_time" while still rejecting "<tree>_time".
TIMED_RE = re.compile(r"(?<!no)_time")


def is_timed_path(path) -> bool:
    s = str(path).lower()
    return bool(TIMED_RE.search(s)) or "/time" in s or "\\time" in s


def is_timed_label(label: str) -> bool:
    return bool(TIMED_RE.search(str(label).lower()))


# Okabe-Ito, chosen because the previous matplotlib-default palette failed a
# colour-vision-deficiency check: #2ca02c (green) and #ff7f0e (orange) separate by
# only dE 0.7 under protanopia, and in the cost figure those two are ADJACENT bars
# — the Vector-PREANA attacker and defender were indistinguishable for red-blind
# readers. The set below keeps the same role semantics and separates by dE >= 11.
PANACEA_ATTACKER_COLOR = "#D55E00"  # vermillion
PANACEA_DEFENDER_COLOR = "#0072B2"  # blue
VP_ATTACKER_COLOR = "#E69F00"       # orange
VP_DEFENDER_COLOR = "#009E73"       # bluish green


# ---------------------------------------------------------------------------
# Generic CSV helpers
# ---------------------------------------------------------------------------

def read_csv_rows(path: Path) -> List[Dict[str, str]]:
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    if not text.strip():
        return []
    try:
        dialect = csv.Sniffer().sniff(text[:8192], delimiters=",;\t")
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = ","
    rows = list(csv.DictReader(text.splitlines(), delimiter=delimiter))

    # csv.DictReader collects any values beyond the header width under the key
    # None (its restkey). Downstream code calls .lower() on column names, so a
    # single ragged row would crash the whole run. Drop the overflow and say so.
    ragged = sum(1 for r in rows if None in r)
    if ragged:
        print(f"note: {path.name}: {ragged} row(s) carry more fields than the "
              f"header; the surplus values are ignored", file=sys.stderr)
        for r in rows:
            r.pop(None, None)
    return rows


def normalize_name(value: object) -> str:
    s = str(value).strip()
    s = Path(s).stem
    return s


def as_float(value: object) -> float:
    if value is None:
        return math.nan
    s = str(value).strip().replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return math.nan


def first_matching_column(columns: Iterable[str], predicates: Sequence[Sequence[str]]) -> Optional[str]:
    # Guard against None / non-string keys produced by ragged CSV rows.
    cols = [c for c in columns if isinstance(c, str)]
    lowered = {c: c.lower().replace("_", " ") for c in cols}
    for required_words in predicates:
        for c in cols:
            lc = lowered[c]
            if all(word in lc for word in required_words):
                return c
    return None


# ---------------------------------------------------------------------------
# Vector-PREANA aggregate CSV
# ---------------------------------------------------------------------------

def expected_vp_csv(
    experiment_dir: Path,
    risk_mode: str,
    attacker_policy: str,
    learning_mode: str,
    turn_order: str,
) -> Path:
    if (
        risk_mode == "dynamic"
        and attacker_policy == "adaptive"
        and learning_mode == "on"
        and turn_order == "attacker_first"
    ):
        return experiment_dir / "vector_preana_result.csv"

    suffix = f"{risk_mode}_{attacker_policy}_learning_{learning_mode}_{turn_order}"
    return experiment_dir / f"vector_preana_{suffix}_result.csv"


def baseline_csv(
    experiment_dir: Path,
    risk_mode: str,
    attacker_policy: str,
    learning_mode: str,
    turn_order: str,
    policy: str,
) -> Path:
    """Path the runner writes for an R-ADT LC or LD baseline run.

    The runner appends `_def_<policy>` to the ablation suffix, so a baseline can
    never overwrite the paths of the full model even when every other parameter
    matches.
    """
    suffix = f"{risk_mode}_{attacker_policy}_learning_{learning_mode}_{turn_order}"
    return experiment_dir / f"vector_preana_{suffix}_def_{policy}_result.csv"


def load_vp(path: Path) -> Dict[str, Dict[str, object]]:
    rows = read_csv_rows(path)
    required = {
        "tree",
        "winner",
        "attacker_cost",
        "defender_cost",
        "planning_time_median_s",
    }
    if not rows:
        raise RuntimeError(f"Vector-PREANA CSV is empty: {path}")
    missing = required - set(rows[0])
    if missing:
        raise RuntimeError(
            f"Vector-PREANA CSV {path} is missing columns: {', '.join(sorted(missing))}"
        )

    result: Dict[str, Dict[str, object]] = {}
    for row in rows:
        tree = normalize_name(row.get("tree", ""))
        if not tree:
            continue
        result[tree] = {
            "winner": str(row.get("winner", "unknown")).strip().lower(),
            "attacker_cost": as_float(row.get("attacker_cost")),
            "defender_cost": as_float(row.get("defender_cost")),
            "planning_time": as_float(row.get("planning_time_median_s")),
            "defender_planning_time": as_float(
                row.get("defender_planning_time_median_s")),
            "max_rss_kb": as_float(row.get("max_rss_kb")),
            # The lambda_D the row was produced with. Kept so the sweep figure
            # can highlight the configuration the paper actually reports rather
            # than inferring an operating point from the sweep itself.
            "lambda_D": as_float(row.get("defense_cost_weight")),
            "defender_policy": str(row.get("defender_policy", "irena")).strip() or "irena",
            "risk_mode": str(row.get("risk_mode", "")),
            "attacker_policy": str(row.get("attacker_policy", "")),
            "learning_mode": str(row.get("learning_mode", "")),
            "turn_order": str(row.get("turn_order", "")),
        }
    return result


# ---------------------------------------------------------------------------
# PANACEA strategy extraction from DOT + PRISM
# ---------------------------------------------------------------------------

def parse_reward_block(prism_text: str, block_name: str) -> Dict[str, float]:
    block_re = re.compile(
        r'rewards\s+"' + re.escape(block_name) + r'"(.*?)endrewards',
        re.IGNORECASE | re.DOTALL,
    )
    m = block_re.search(prism_text)
    if not m:
        return {}
    block = m.group(1)

    rewards: Dict[str, float] = {}
    # Typical PANACEA line: [a14] true : 100;
    entry_re = re.compile(
        r"\[\s*([^\]]+)\s*\]\s*[^:;]*:\s*([-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)\s*;"
    )
    for action, value in entry_re.findall(block):
        rewards[action.strip()] = float(value)
    return rewards


def strip_dot_id(value: str) -> str:
    return value.strip().strip('"')


def extract_strategy_actions(dot_text: str, known_actions: Sequence[str]) -> List[str]:
    """Follow the deterministic strategy path from DOT node 0.

    PANACEA/PRISM-games strategy DOTs typically use state -> point edges labelled
    "choice:action" and point -> state edges labelled with probabilities.
    """
    known = set(known_actions)
    edge_re = re.compile(
        r'^\s*("[^"\n]+"|[A-Za-z0-9_.-]+)\s*->\s*'
        r'("[^"\n]+"|[A-Za-z0-9_.-]+)\s*\[(.*?)\]\s*;?\s*$',
        re.MULTILINE,
    )
    label_re = re.compile(r'label\s*=\s*"([^"]*)"')

    outgoing: Dict[str, List[Tuple[str, str]]] = {}
    for src_raw, dst_raw, attrs in edge_re.findall(dot_text):
        src = strip_dot_id(src_raw)
        dst = strip_dot_id(dst_raw)
        lm = label_re.search(attrs)
        label = lm.group(1).strip() if lm else ""
        outgoing.setdefault(src, []).append((dst, label))

    # Prefer the conventional initial state 0. Otherwise use the smallest
    # numeric source node that exists in the strategy graph.
    if "0" in outgoing:
        current = "0"
    else:
        numeric = sorted(
            (int(n), n) for n in outgoing if re.fullmatch(r"\d+", n)
        )
        if not numeric:
            return []
        current = numeric[0][1]

    actions: List[str] = []
    visited_states = set()

    for _ in range(10000):
        if current in visited_states:
            break
        visited_states.add(current)

        edges = outgoing.get(current, [])
        action_edge: Optional[Tuple[str, str, str]] = None

        for dst, label in edges:
            candidate = ""
            if ":" in label:
                candidate = label.split(":", 1)[1].strip()
            elif label in known:
                candidate = label
            if candidate in known:
                action_edge = (dst, label, candidate)
                break

        if action_edge is None:
            break

        intermediate, _label, action = action_edge
        actions.append(action)

        # Most strategy DOTs have an intermediate point with a probability edge.
        next_edges = outgoing.get(intermediate, [])
        if not next_edges:
            current = intermediate
            continue

        # Prefer a numeric-probability edge; otherwise deterministic first edge.
        next_state = None
        for dst, label in next_edges:
            if re.fullmatch(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", label):
                next_state = dst
                break
        if next_state is None:
            next_state = next_edges[0][0]

        current = next_state

    # Fallback for a DOT variant that cannot be walked with the structure above:
    # preserve textual order of action-labelled edges. This is less rigorous but
    # still works for the deterministic linear strategies exported in Experiment 3.
    #
    # It is also dangerous: it collects EVERY action-labelled edge in the file,
    # not just those on the strategy path, so the resulting costs are sums over
    # the whole graph. It must never fire unnoticed.
    if not actions:
        for _src, _dst, attrs in edge_re.findall(dot_text):
            lm = label_re.search(attrs)
            if not lm:
                continue
            label = lm.group(1).strip()
            candidate = label.split(":", 1)[1].strip() if ":" in label else label
            if candidate in known:
                actions.append(candidate)
        if actions:
            warnings.warn(
                f"strategy walk failed; fell back to textual order and "
                f"collected {len(actions)} action-labelled edge(s). The costs "
                f"derived from this trace are sums over the whole DOT and are "
                f"NOT the cost of the strategy path. Verify before publishing.",
                RuntimeWarning,
                stacklevel=2,
            )
            print("  !! FALLBACK USED: costs below are not path costs",
                  file=sys.stderr)

    return actions


RSS_RE = re.compile(r"Maximum resident set size \(kbytes\):\s*(\d+)")
WALL_RE = re.compile(r"Elapsed \(wall clock\) time[^:]*:\s*([0-9:.]+)")


def _wall_to_seconds(text: str) -> float:
    parts = text.split(":")
    try:
        vals = [float(p) for p in parts]
    except ValueError:
        return math.nan
    seconds = 0.0
    for v in vals:
        seconds = seconds * 60.0 + v
    return seconds


def read_panacea_log(dot_path: Path) -> Dict[str, float]:
    """Peak memory and wall-clock time, from the PRISM log beside the strategy.

    run_experiment3.sh wraps PRISM in `/usr/bin/time -v`, so the log carries
    "Maximum resident set size" and the elapsed wall clock. Neither reaches
    result.csv, yet both are what a scalability claim actually needs: the
    model-checking timer alone excludes model construction, which is precisely
    the cost Vector-PREANA does not pay.
    """
    out: Dict[str, float] = {}
    for cand in (dot_path.with_suffix(".log"),
                 *dot_path.parent.glob("*.log")):
        if not cand.exists():
            continue
        text = cand.read_text(encoding="utf-8", errors="replace")
        m = RSS_RE.search(text)
        if m:
            out["memory_mb"] = float(m.group(1)) / 1024.0
        w = WALL_RE.search(text)
        if w:
            out["wall_time_s"] = _wall_to_seconds(w.group(1))
        if out:
            out["log"] = 1.0
            break
    return out


def score_panacea_strategy(prism_path: Path, dot_path: Path) -> Dict[str, object]:
    prism_text = prism_path.read_text(encoding="utf-8", errors="replace")
    dot_text = dot_path.read_text(encoding="utf-8", errors="replace")

    attacker_rewards = parse_reward_block(prism_text, "attacker")
    defender_rewards = parse_reward_block(prism_text, "defender")
    if not attacker_rewards and not defender_rewards:
        raise RuntimeError(f"Could not find attacker/defender reward blocks in {prism_path}")

    actions = extract_strategy_actions(
        dot_text, list(attacker_rewards.keys()) + list(defender_rewards.keys())
    )
    if not actions:
        raise RuntimeError(f"Could not recover a strategy action trace from {dot_path}")

    attacker_cost = sum(attacker_rewards.get(a, 0.0) for a in actions)
    defender_cost = sum(defender_rewards.get(a, 0.0) for a in actions)

    # In PANACEA's untimed deterministic traces, reaching the root terminates on
    # an attacker action, while making further attack progress impossible terminates
    # after a defender action. Ignore any labels not in the reward structures.
    last_action = actions[-1]
    if last_action in attacker_rewards:
        winner = "attacker"
    elif last_action in defender_rewards:
        winner = "defender"
    else:
        winner = "unknown"

    return {
        "winner": winner,
        "attacker_cost": attacker_cost,
        "defender_cost": defender_cost,
        "actions": actions,
    }


def choose_untimed_model_files(experiment_dir: Path, tree: str) -> Tuple[Path, Path]:
    """Locate the untimed PRISM model and strategy DOT for one PANACEA tree.

    Models and strategy exports need not share a directory. Layouts such as

        experiment3/prism/29.prism
        experiment3/results/29/<name>.dot

    are supported: files are paired by tree name across the whole experiment
    directory. The previous version only looked for a .dot next to the .prism
    and therefore found nothing under that layout.
    """
    candidates: List[Tuple[int, Path, Path]] = []
    # Vector-PREANA writes its own <tree>/<tree>.dot under vector_preana*_results/.
    # Those files have exactly the same stem and parent-directory name as
    # PANACEA's strategy exports, so they score identically and used to win the
    # tie by filesystem order. They are never a PANACEA strategy: exclude them.
    # A name-based exclusion is fragile: renaming a results directory to
    # sweep_lambdaD_* or archiving it under another prefix silently makes its
    # DOTs eligible again. The reliable signature is the trace file, which only
    # the simulator writes, so any directory containing *_trace.json is ours.
    def _is_simulator_output(d: Path) -> bool:
        if any(part.startswith("vector_preana") for part in d.parts):
            return True
        for parent in (d.parent, d.parent.parent):
            try:
                if any(parent.glob("*_trace.json")):
                    return True
            except OSError:
                continue
        return False

    all_dots = [
        d for d in experiment_dir.rglob("*.dot")
        if not is_timed_path(d) and not _is_simulator_output(d)
    ]

    for prism in experiment_dir.rglob("*.prism"):
        if is_timed_path(prism):
            continue
        stem = prism.stem
        parent = prism.parent.name
        # <tree>_no_time.prism is the untimed model for <tree>.
        stem_norm = re.sub(r"_no_time$", "", normalize_name(stem))
        parent_norm = re.sub(r"_no_time$", "", normalize_name(parent))
        if stem_norm != tree and parent_norm != tree:
            continue

        # Score every untimed DOT in the experiment tree against this model.
        for dot in all_dots:
            score = 0
            if re.sub(r"_no_time$", "", dot.stem) == tree:
                score += 4
            if dot.parent.name == tree:
                score += 4
            if any(part == tree for part in dot.parts[:-1]):
                score += 2
            if dot.stem == prism.stem:
                score += 3
            if "results" in dot.parts:
                score += 1
            if score > 0:
                candidates.append((score, prism, dot))

    if not candidates:
        # Last chance: use conventional Experiment 3 paths.
        for base in (experiment_dir / tree, experiment_dir):
            for stem in (tree, f"{tree}_no_time"):
                prism = base / f"{stem}.prism"
                dot = base / f"{stem}.dot"
                if prism.exists() and dot.exists():
                    return prism, dot
        n_prism = sum(1 for _ in experiment_dir.rglob("*.prism"))
        n_dot = sum(1 for _ in experiment_dir.rglob("*.dot"))
        raise FileNotFoundError(
            f"Could not locate untimed PANACEA .prism/.dot files for tree {tree} "
            f"under {experiment_dir} ({n_prism} .prism and {n_dot} .dot present "
            f"in total). If both counts are zero, run PANACEA's own "
            f"run_experiment3.sh first: the models and strategy exports are "
            f"build artefacts and are not part of the checked-in tree."
        )

    # Deterministic tie-break on both paths; filesystem order must never decide.
    candidates.sort(key=lambda x: (-x[0], str(x[1]), str(x[2])))
    return candidates[0][1], candidates[0][2]


# ---------------------------------------------------------------------------
# PANACEA planning time
# ---------------------------------------------------------------------------

def row_matches_tree(row: Dict[str, str], tree: str) -> bool:
    # First try likely experiment/name columns.
    for key, value in row.items():
        lk = key.lower()
        if any(token in lk for token in ("experiment", "tree", "name", "model")):
            v = normalize_name(value)
            if v == tree or v == f"{tree}_no_time":
                return True
    # Then inspect all cells, but explicitly exclude timed variants.
    for value in row.values():
        raw = str(value).strip().lower()
        if "time" in raw and "no_time" not in raw:
            continue
        v = normalize_name(value)
        if v == tree or v == f"{tree}_no_time":
            return True
    return False


TIME_PROVENANCE: Dict[str, str] = {}

# states / transitions / construction time / memory, per tree, when available.
PANACEA_STATS: Dict[str, Dict[str, float]] = {}

# PANACEA's experiment3/result.csv carries NO header row. extract_data.sh emits
#   name, states, transitions, construction_time, model_checking_time, result
# where the last field is PRISM's "Result:" value, NOT memory. Example:
#   29,2136380,8030113,18.237,31.878,260.0
# csv.DictReader consumes the first data row as the header, after which no
# column name matches "model checking time" and the file is skipped in silence.
# extract_data.sh also writes a leading blank line, which simply never matches.
HEADERLESS_ROW_RE = re.compile(
    r"^\s*(?P<name>[A-Za-z0-9_]+)\s*,"
    r"\s*(?P<states>\d+)\s*,"
    r"\s*(?P<transitions>\d+)\s*,"
    r"\s*(?P<build>[-+0-9.eE]+)\s*,"
    r"\s*(?P<check>[-+0-9.eE]+)\s*,"
    r"\s*(?P<result>[-+0-9.eE]*)\s*$"
)


def parse_headerless_result_csv(path: Path) -> Dict[str, float]:
    """Read a headerless PANACEA result.csv. Timed variants are skipped."""
    times: Dict[str, float] = {}
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return times
    for line in text.splitlines():
        m = HEADERLESS_ROW_RE.match(line)
        if not m:
            continue
        name = m.group("name")
        if is_timed_label(name):
            continue
        tree = re.sub(r"_no_time$", "", name)
        try:
            times[tree] = float(m.group("check"))
            PANACEA_STATS[tree] = {
                "states": float(m.group("states")),
                "transitions": float(m.group("transitions")),
                "build_time_s": float(m.group("build")),
                "prism_result": float(m.group("result") or "nan"),
            }
            TIME_PROVENANCE[tree] = f"{path.name}:headerless row '{name}' [col 5]"
        except ValueError:
            continue
    return times


def extract_panacea_model_checking_times(experiment_dir: Path) -> Dict[str, float]:
    """Read PANACEA's original result CSV, tolerating small schema variations."""
    result_files = [p for p in experiment_dir.glob("*.csv") if "vector_preana" not in p.name]
    result_files += [
        p for p in experiment_dir.rglob("result.csv")
        if "vector_preana" not in str(p)
    ]

    # De-duplicate while keeping a deterministic order; prefer experiment3/result.csv.
    unique = []
    seen = set()
    preferred = experiment_dir / "result.csv"
    if preferred.exists():
        unique.append(preferred)
        seen.add(preferred.resolve())
    for p in sorted(result_files):
        rp = p.resolve()
        if rp not in seen:
            unique.append(p)
            seen.add(rp)

    times: Dict[str, float] = {}

    def try_headerless(path: Path) -> None:
        recovered = parse_headerless_result_csv(path)
        fresh = {k: v for k, v in recovered.items() if k not in times}
        if fresh:
            print(f"note: {path.name} has no header row; parsed "
                  f"{len(fresh)} untimed model(s) positionally "
                  f"(column 5 = model-checking time)", file=sys.stderr)
            times.update(fresh)

    for path in unique:
        rows = read_csv_rows(path)
        if not rows:
            # A single-line headerless file reaches this branch: DictReader
            # promotes its only line to a header and leaves zero data rows.
            # Skipping here therefore lost one-tree result files entirely --
            # multi-tree files survived only because their remaining lines
            # kept `rows` non-empty. Try the headerless layout first.
            try_headerless(path)
            continue
        columns = rows[0].keys()
        time_col = first_matching_column(
            columns,
            [
                ("model", "checking", "time"),
                ("checking", "time"),
                ("model", "check", "time"),
                ("verification", "time"),
            ],
        )
        if not time_col:
            # No usable header. Try the headerless layout before giving up,
            # instead of skipping the file without a word.
            try_headerless(path)
            continue

        for row in rows:
            val = as_float(row.get(time_col))
            if not math.isfinite(val):
                continue
            # Learn tree labels from likely columns/cells. Experiment 3 names are
            # commonly simple numbers such as 10, 25, 29, 34.
            matched = None
            source = None
            for key, cell in row.items():
                lk = key.lower()
                if any(token in lk for token in ("experiment", "tree", "name", "model")):
                    v = normalize_name(cell)
                    m = re.fullmatch(r"(\d+)(?:_no_time)?", v)
                    # `"_time" not in v` used to reject "<n>_no_time" as well,
                    # contradicting the regex that explicitly accepts it.
                    if m and not is_timed_label(v):
                        matched = m.group(1)
                        source = f"{path.name}:{key}={v}"
                        break
            if matched is None:
                for key, cell in row.items():
                    v = normalize_name(cell)
                    m = re.fullmatch(r"(\d+)(?:_no_time)?", v)
                    if m and not is_timed_label(v):
                        matched = m.group(1)
                        source = f"{path.name}:{key}={v}"
                        break
            # First file wins. experiment3/result.csv is scanned first, so a
            # stray CSV later in the list can no longer silently override the
            # authoritative model-checking times.
            if matched is not None and matched not in times:
                times[matched] = val
                TIME_PROVENANCE[matched] = f"{source} [{time_col}]"

    return times


# ---------------------------------------------------------------------------
# Plotting
# ---------------------------------------------------------------------------

def tick_label(tree: str) -> str:
    """Axis label for one instance.

    The instances have been named by node count, so "25" reads as "25 nodes".
    A named instance -- the realistic tree, whose file is not a number -- gets
    the reader-facing name the paper uses, never its internal file name.
    """
    t = str(tree).strip()
    if t.isdigit():
        return f"{t} nodes"
    return DISPLAY_NAMES.get(t, t)


# Internal instance names -> the names the paper's figures show.
DISPLAY_NAMES = {
    "adt_nuovo": "Apache/MySQL\ncase study",
}
# Compact variant for legends and outcome strips, where space is scarce.
DISPLAY_SHORT = {
    "adt_nuovo": "Apache/MySQL",
}


def short_label(tree: str) -> str:
    """Compact instance name for legends and strips (no 'nodes' suffix)."""
    t = str(tree).strip()
    return DISPLAY_SHORT.get(t, t)


def wl(role: str, winner: str) -> str:
    if winner not in {"attacker", "defender"}:
        return "?"
    return "W" if role == winner else "L"


def annotate_bar(ax, bar, label: str) -> None:
    h = bar.get_height()
    if not math.isfinite(float(h)):
        return
    yoff = max(2.0, abs(h) * 0.025)
    ax.text(
        bar.get_x() + bar.get_width() / 2.0,
        h + yoff,
        label,
        ha="center",
        va="bottom",
        fontsize=9,
        fontweight="bold",
    )


# Okabe-Ito again, one hue per defender. PANACEA is the blue already used for
# the defender elsewhere in the paper; the two rule baselines take the two
# remaining hues that stay separable under protanopia and deuteranopia.
BASELINE_COLORS = {
    "panacea": "#0072B2",   # blue
    "irena":   "#009E73",   # bluish green
    "lc":      "#E69F00",   # orange
    "ld":      "#CC79A7",   # reddish purple
}
BASELINE_LABELS = {
    "panacea": "PANACEA",
    "irena":   "IRENA",
    "lc":      "R-ADT LC",
    "ld":      "R-ADT LD",
}


def plot_costs(
    trees: Sequence[str],
    panacea: Dict[str, Dict[str, object]],
    vp: Dict[str, Dict[str, object]],
    out_base: Path,
    series: Optional[Dict[str, Dict[str, Dict[str, object]]]] = None,
) -> None:
    """Cumulative attacker and defender cost, one panel each.

    Eight bars in a single panel (four policies x two roles) are unreadable, so
    the roles are split: colour identifies the policy consistently across both
    panels, and W/L marks who won that run. Splitting also keeps the panels on
    independent y scales, which matters because attacker costs run an order of
    magnitude above defender costs on the larger instances.

    `series` carries the simulated defenders. When it holds only IRENA the
    figure degrades to the original two-policy comparison.
    """
    pol = {"panacea": panacea, "irena": vp}
    for key in ("lc", "ld"):
        if series and key in series:
            pol[key] = series[key]
    order = [k for k in ("panacea", "irena", "lc", "ld") if k in pol]

    x = np.arange(len(trees), dtype=float)
    width = 0.8 / len(order)
    fig, axes = plt.subplots(2, 1, sharex=True, figsize=(7.2, 6.2),
                             gridspec_kw={"hspace": 0.13})

    for ax, role in zip(axes, ("attacker", "defender")):
        for i, key in enumerate(order):
            offs = x + (i - (len(order) - 1) / 2.0) * width
            vals, marks = [], []
            for t in trees:
                row = pol[key].get(t)
                vals.append(float(row[f"{role}_cost"]) if row else math.nan)
                marks.append(wl(role, str(row["winner"])) if row else "")
            bars = ax.bar(offs, vals, width * 0.92,
                          label=BASELINE_LABELS.get(key, key.upper()),
                          color=BASELINE_COLORS.get(key), edgecolor="white",
                          linewidth=0.6)
            for b, v, m in zip(bars, vals, marks):
                if math.isfinite(v):
                    annotate_bar(ax, b, m)
        ax.set_ylabel(f"Cumulative {role} cost")
        ax.grid(axis="y", alpha=0.22)
        ax.margins(y=0.14)

    axes[1].set_xlabel("R-ADT size")
    axes[1].set_xticks(x)
    axes[1].set_xticklabels(trees)
    axes[0].legend(ncol=len(order), fontsize=8, frameon=False, loc="lower center",
                   bbox_to_anchor=(0.5, 1.005), columnspacing=1.2, handlelength=1.3)
    # No tight_layout(): hspace is set explicitly in gridspec_kw, which
    # tight_layout overrides while warning that it may be wrong. bbox_inches
    # ="tight" at save time already crops the margins.
    for ext in ("pdf", "png"):
        fig.savefig(out_base.with_suffix(f".{ext}"), dpi=300, bbox_inches="tight")
    plt.close(fig)


# One hue per R-ADT, validated for colour-vision deficiency alongside the rest.
# Okabe-Ito, in an order that keeps adjacent entries far apart in hue. Three
# entries were enough while the sweep covered three instances; with five the
# `i % len` wrap silently gave two curves the same colour, which on a figure
# whose whole point is per-instance behaviour is not a cosmetic problem.
SWEEP_COLORS = ["#0072B2", "#D55E00", "#009E73", "#CC79A7", "#E69F00",
                "#56B4E9", "#000000"]


def load_sweep(paths: Sequence[Path]) -> Dict[str, Dict[float, Tuple[float, str]]]:
    """Collect (lambda_D -> defense cost, winner) per tree from Vector-PREANA CSVs.

    lambda_D comes from the `defense_cost_weight` column, never from the file
    name, so a renamed or copied sweep file cannot mislabel a curve. Files that
    lack the column (PANACEA's own result.csv) are skipped silently; a second
    file offering a DIFFERENT value for the same (tree, lambda_D) is reported,
    because that means two incompatible runs are being merged into one curve.
    """
    out: Dict[str, Dict[float, Tuple[float, str]]] = {}
    source: Dict[Tuple[str, float], str] = {}
    for p in paths:
        if not p.exists():
            continue
        for row in read_csv_rows(p):
            tree = normalize_name(row.get("tree", ""))
            lam = as_float(row.get("defense_cost_weight"))
            cost = as_float(row.get("defender_cost"))
            winner = str(row.get("winner", "")).strip().lower()
            if not tree or not math.isfinite(lam) or not math.isfinite(cost):
                continue
            prev = out.setdefault(tree, {}).get(lam)
            if prev is not None and prev != (cost, winner):
                print(f"warning: tree {tree} at lambda_D={lam:g} appears twice "
                      f"with different results: {prev} in "
                      f"{source.get((tree, lam), '?')} vs {(cost, winner)} in "
                      f"{p.name}; keeping the first", file=sys.stderr)
                continue
            out[tree][lam] = (cost, winner)
            source[(tree, lam)] = p.name
    return out


def _operating_point(usable: Sequence[str],
                     sweep: Dict[str, Dict[float, Tuple[float, str]]]) -> Optional[float]:
    """Largest lambda_D at which the defender still wins on every instance.

    Derived from the data instead of hard-coded, so the highlighted band follows
    the measurements if the sweep is extended or re-run.
    """
    lams = sorted({l for t in usable for l in sweep[t]})
    best = None
    for lam in lams:
        entries = [sweep[t].get(lam) for t in usable]
        if any(e is None for e in entries):
            continue
        if all(e[1] == "defender" for e in entries):
            best = lam
    return best


def plot_lambda_sweep(
    trees: Sequence[str],
    sweep: Dict[str, Dict[float, Tuple[float, str]]],
    panacea: Dict[str, Dict[str, object]],
    out_base: Path,
    reported_lam: Optional[float] = None,
    cost_trees: Optional[Sequence[str]] = None,
) -> None:
    """Defense cost against lambda_D, expressed as overhead over the minimum.

    Two panels sharing the x axis.

    The upper panel plots the overhead rather than the raw cost, which collapses
    three different proven minima onto one reference line at 0%, so a single
    horizontal rule means "optimal" for every instance. It shows ONLY the runs
    the defender won. When the attack succeeds the defense cost stops being
    comparable to a minimum that was defined as "the cheapest defense that stops
    the attack": the earlier version of this figure drew those runs on the same
    axis and they landed *below* the reference line, which reads as beating a
    proven lower bound when in fact the defender had simply given up. The line
    breaks (NaN) at every lost run instead of interpolating across it.

    The lower strip carries the win/loss outcome, which is categorical and does
    not belong on a cost axis, for every (instance, lambda_D) pair.
    """
    # An instance PANACEA lost has no proven minimum: its defender cost is
    # simply what the losing run happened to spend, so an overhead measured
    # against it is meaningless. Such instances are dropped from this figure
    # and the omission is reported rather than silently applied.
    # `cost_trees` restricts the COST axis only. An instance reported for
    # performance but deliberately kept out of the cost comparison still
    # belongs in the outcome strip, because its win/loss is what constrains the
    # operating point; drawing its overhead would compare a cost the paper does
    # not compare anywhere else.
    on_cost = set(trees if cost_trees is None else cost_trees)
    strip, usable, no_bound, not_compared = [], [], [], []
    for t in trees:
        if not sweep.get(t):
            continue
        strip.append(t)
        if t not in panacea or float(panacea[t]["defender_cost"]) <= 0:
            continue
        if str(panacea[t].get("winner", "")).strip().lower() != "defender":
            no_bound.append(t)
            continue
        if t not in on_cost:
            not_compared.append(t)
            continue
        usable.append(t)
    if not_compared:
        print(f"note: {', '.join(not_compared)} appear in the lambda_D outcome "
              f"strip but not on its cost axis (--cost-exclude)",
              file=sys.stderr)
    if no_bound:
        print(f"note: {', '.join(no_bound)} excluded from the lambda_D sweep "
              f"figure: PANACEA did not defend them, so there is no proven "
              f"minimum to measure an overhead against", file=sys.stderr)
    if not usable or not strip:
        return

    fig, (ax, axo) = plt.subplots(
        2, 1, sharex=True, figsize=(6.8, 4.6),
        gridspec_kw={"height_ratios": [3.2, 0.85], "hspace": 0.10},
        layout="constrained")

    all_lams = sorted({l for t in strip for l in sweep[t]})
    # The band marks the configuration the reported runs were produced with,
    # taken from the main CSV. It is NOT re-derived from this figure, because
    # `usable` drops every instance PANACEA lost: a value inferred from what is
    # left is the best lambda_D for a subset of the instances, and it can differ
    # from the one behind every table and every other figure. When that happens
    # the sweep silently contradicts the rest of the paper. Falling back to the
    # inferred value only when the CSV carries no lambda_D.
    opt_lam = (reported_lam
               if reported_lam is not None and math.isfinite(reported_lam)
               else _operating_point(usable, sweep))

    # Highlight the operating point on both panels, behind everything else.
    if opt_lam is not None:
        for a in (ax, axo):
            a.axvspan(opt_lam / 1.42, opt_lam * 1.42, color="#EDEDED",
                      linewidth=0, zorder=0)

    if len(strip) > len(SWEEP_COLORS):
        print(f"WARNING: {len(strip)} instances but {len(SWEEP_COLORS)} "
              f"colours; curves will share a colour and the figure cannot be "
              f"read unambiguously", file=sys.stderr)

    # One legend entry per instance that actually has a curve. Inline labels
    # were tried first and kept fighting the geometry: without the horizontal
    # dodge the leftmost marker sits on the axis edge, so a label to its left
    # ran into the y-axis title and one to its right ran into the curve. The
    # upper right of this panel is empty in every configuration -- the costly
    # instances peak on the left -- so the names go there.
    legend_entries: List[Tuple[str, str]] = []
    plotted = False

    # Iterate over the STRIP set so that colour and row order stay consistent
    # between the two panels; the cost curve is drawn only for the subset the
    # cost axis admits.
    for i, tree in enumerate(strip):
        series = sweep[tree]
        lams = sorted(series)
        colour = SWEEP_COLORS[i % len(SWEEP_COLORS)]
        won = {l: series[l][1] == "defender" for l in lams}
        on_axis = tree in usable
        rho = ({l: (series[l][0] - float(panacea[tree]["defender_cost"]))
                / float(panacea[tree]["defender_cost"]) * 100.0 for l in lams}
               if on_axis else {})

        # Every marker sits on its own lambda_D. An earlier version dodged the
        # series horizontally so that coincident points would not hide each
        # other, but the displacement read as a measurement at a slightly
        # different weight, which is worse than an occluded marker: the reader
        # cannot tell a dodge from a data point. Where two instances agree the
        # markers now genuinely coincide, and the outcome strip below resolves
        # which instances are present at that weight.
        if on_axis:
            ax.plot(list(lams),
                    [rho[l] if won[l] else float("nan") for l in lams],
                    linewidth=2.0, color=colour, zorder=2)
            ax.scatter([l for l in lams if won[l]],
                       [rho[l] for l in lams if won[l]],
                       marker="o", s=58, color=colour, zorder=3,
                       edgecolors="white", linewidths=1.0)
            if any(won.values()):
                legend_entries.append((short_label(tree), colour))
                plotted = True

        # Outcome strip: one row per instance, top to bottom in `trees` order.
        # Rows are separated vertically, so the markers can sit exactly on the
        # weight they were measured at.
        axo.plot([min(all_lams), max(all_lams)], [i, i], color="#DDDDDD",
                 linewidth=0.9, zorder=1)
        axo.scatter([l for l in lams if won[l]], [i] * sum(won.values()),
                    marker="o", s=52, color=colour, zorder=3,
                    edgecolors="white", linewidths=0.8)
        axo.scatter([l for l in lams if not won[l]],
                    [i] * (len(lams) - sum(won.values())),
                    marker="X", s=64, color="#9A9A9A", zorder=3,
                    edgecolors="white", linewidths=0.8)

    if not plotted:
        plt.close(fig)
        return

    ax.legend(handles=[mlines.Line2D([], [], color=c, marker="o",
                                     markersize=6, markeredgecolor="white",
                                     linewidth=2.0, label=name)
                       for name, c in legend_entries],
              loc="upper right", frameon=False, fontsize=9,
              handlelength=1.6, labelspacing=0.35, borderaxespad=0.8)

    ax.axhline(0.0, color="#555555", linewidth=1.2, linestyle="--", zorder=1)
    ax.annotate("PANACEA optimum", (all_lams[-1], 0.0),
                textcoords="offset points", xytext=(0, 6),
                ha="right", va="bottom", fontsize=8, color="#555555")

    # No per-point percentage callouts: the y axis already reads the overhead
    # off, and printing selected values on top of the curves both duplicates
    # the axis and implies those points matter more than the others.

    if opt_lam is not None:
        ax.annotate(f"operating point  $\\lambda_D={opt_lam:g}$",
                    (opt_lam, 1.0), xycoords=("data", "axes fraction"),
                    textcoords="offset points", xytext=(0, 4),
                    ha="center", va="bottom", fontsize=8.5, color="#444444")

    ax.set_ylabel("Defense cost overhead\nover the minimum (%)", fontsize=10)
    ax.grid(axis="y", alpha=0.22)
    ax.margins(x=0.13, y=0.13)
    lo, hi = ax.get_ylim()
    ax.set_ylim(min(-0.10 * hi, lo * 0.35), hi)

    axo.set_xscale("log")
    axo.set_yticks(range(len(strip)))
    axo.set_yticklabels([short_label(t) for t in strip], fontsize=9)
    axo.set_ylim(len(strip) - 0.5, -0.5)           # first instance on top
    axo.set_ylabel("R-ADT", fontsize=9)
    axo.set_xlabel(r"Defense cost weight $\lambda_D$")
    axo.grid(axis="x", alpha=0.18)
    axo.tick_params(axis="y", length=0)
    for side in ("top", "right", "left"):
        axo.spines[side].set_visible(False)

    axo.legend(
        handles=[
            plt.Line2D([], [], marker="o", linestyle="none", color="#555555",
                       markersize=6.5, markeredgecolor="white"),
            plt.Line2D([], [], marker="X", linestyle="none", color="#9A9A9A",
                       markersize=7.5, markeredgecolor="white"),
        ],
        labels=["defender wins", "attacker wins  (cost omitted above)"],
        ncol=2, fontsize=8, frameon=False, loc="upper center",
        bbox_to_anchor=(0.5, -0.78), columnspacing=1.4, handlelength=1.2)

    for ext in ("pdf", "png"):
        fig.savefig(out_base.with_suffix(f".{ext}"), dpi=300, bbox_inches="tight")
    plt.close(fig)


def plot_defender_vs_optimum(
    trees: Sequence[str],
    panacea: Dict[str, Dict[str, object]],
    vp: Dict[str, Dict[str, object]],
    out_base: Path,
) -> None:
    """Defense cost only, against PANACEA's value as a proven lower bound.

    The four-bar figure invites a comparison of attacker costs that does not
    hold: PANACEA's attacker expenditure is whatever the attacker happens to
    spend along the DEFENDER-optimal strategy, not its own optimum. The defender
    column, by contrast, is exactly the optimal value PRISM-games reports for
    R{"defender"}min, so it is a genuine lower bound and the overhead above it
    is the quantity worth plotting.
    """
    x = np.arange(len(trees), dtype=float)
    width = 0.34
    opt = [float(panacea[t]["defender_cost"]) for t in trees]
    got = [float(vp[t]["defender_cost"]) for t in trees]

    fig, ax = plt.subplots(figsize=(6.4, 4.0))
    pan_won = [str(panacea[t].get("winner", "")).strip().lower() == "defender"
               for t in trees]
    b1 = ax.bar(x - width / 2, opt, width,
                label=("Proven minimum (PANACEA)" if all(pan_won)
                       else "PANACEA defender cost"),
                color=PANACEA_DEFENDER_COLOR)
    for idx, won in enumerate(pan_won):
        if not won:
            b1[idx].set_hatch("//")
            b1[idx].set_alpha(0.55)
    b2 = ax.bar(x + width / 2, got, width, label="IRENA",
                color=VP_DEFENDER_COLOR)

    for idx, tree in enumerate(trees):
        annotate_bar(ax, b1[idx], f"{opt[idx]:g}" + ("" if pan_won[idx] else "\nL"))
        if not pan_won[idx]:
            # No bound to compare against: PANACEA lost this instance too.
            annotate_bar(ax, b2[idx], f"{got[idx]:g}"
                         + ("" if str(vp[trees[idx]].get("winner","")).lower() == "defender" else "\nL"))
            continue
        overhead = (got[idx] - opt[idx]) / opt[idx] * 100.0 if opt[idx] else float("nan")
        label = (f"{got[idx]:g}" if abs(overhead) < 1e-9
                 else f"{got[idx]:g}  ({overhead:+.0f}%)")
        annotate_bar(ax, b2[idx], label)

    ax.set_xlabel("R-ADT size")
    ax.set_ylabel("Cumulative defense cost")
    ax.set_xticks(x)
    ax.set_xticklabels(trees)
    ax.grid(axis="y", alpha=0.22)
    ax.legend(ncol=2, fontsize=9, frameon=False, loc="lower center",
              bbox_to_anchor=(0.5, 1.005))
    ax.margins(y=0.16)
    fig.tight_layout()
    for ext in ("pdf", "png"):
        fig.savefig(out_base.with_suffix(f".{ext}"), dpi=300, bbox_inches="tight")
    plt.close(fig)


def plot_state_space(trees: Sequence[str], out_base: Path) -> None:
    """The state explosion Vector-PREANA never pays for."""
    have = [t for t in trees if t in PANACEA_STATS]
    if not have:
        return
    x = np.arange(len(have), dtype=float)
    states = [PANACEA_STATS[t]["states"] for t in have]
    trans = [PANACEA_STATS[t]["transitions"] for t in have]

    fig, ax = plt.subplots(figsize=(6.4, 4.0))
    ax.plot(x, states, marker="o", linewidth=2.0, color=PANACEA_DEFENDER_COLOR,
            label="PANACEA states")
    ax.plot(x, trans, marker="^", linewidth=2.0, linestyle=":",
            color=PANACEA_ATTACKER_COLOR, label="PANACEA transitions")
    # Below the states line: on a log axis the transitions line sits only a
    # short visual distance above, and labels offset upward collided with it.
    for xi, s in zip(x, states):
        ax.annotate(f"{s:,.0f}", (xi, s), textcoords="offset points",
                    xytext=(0, -12), ha="center", va="top", fontsize=8)
    ax.set_yscale("log")
    ax.set_xlabel("R-ADT size")
    ax.set_ylabel("Explicit model size (count)")
    ax.set_xticks(x)
    ax.set_xticklabels(have)
    ax.grid(axis="y", which="both", alpha=0.22)
    ax.legend(ncol=2, fontsize=9, frameon=False, loc="lower center",
              bbox_to_anchor=(0.5, 1.005))
    ax.margins(y=0.18)
    fig.tight_layout()
    for ext in ("pdf", "png"):
        fig.savefig(out_base.with_suffix(f".{ext}"), dpi=300, bbox_inches="tight")
    plt.close(fig)


def plot_baseline_costs(
    trees: Sequence[str],
    panacea: Dict[str, Dict[str, object]],
    series: Dict[str, Dict[str, Dict[str, object]]],
    out_base: Path,
) -> None:
    """Defender cost of every policy against the proven minimum.

    One group per instance, one bar per defender. The attacker is the same
    adaptive one in all three simulated runs, so the only factor that varies
    between IRENA, LC and LD is the response rule; the PANACEA bar is the lower
    bound none of them can beat while still stopping the attack -- but only on
    the instances PANACEA actually defends.

    A bar whose run the defender LOST is hatched and marked, because its cost is
    not comparable with the others: a policy that stops defending is cheap for
    the wrong reason. This applies to PANACEA exactly as it applies to the
    simulated policies. On an instance where the strategy PRISM exports lets the
    attacker through, what that strategy spent is not a minimum over the
    defenses that stop the attack, and drawing it as an unmarked reference bar
    would assert a lower bound that was never proven.
    """
    order = [k for k in ("panacea", "irena", "lc", "ld")
             if k == "panacea" or k in series]
    if len(order) < 2:
        return

    x = np.arange(len(trees), dtype=float)
    width = 0.8 / len(order)
    fig, ax = plt.subplots(figsize=(7.0, 3.9))

    for i, key in enumerate(order):
        offs = x + (i - (len(order) - 1) / 2.0) * width
        vals, lost = [], []
        for t in trees:
            if key == "panacea":
                vals.append(float(panacea[t]["defender_cost"]))
                lost.append(
                    str(panacea[t].get("winner", "")).strip().lower() != "defender")
            else:
                row = series[key].get(t)
                vals.append(float(row["defender_cost"]) if row else math.nan)
                lost.append(bool(row) and str(row["winner"]) != "defender")
        bars = ax.bar(offs, vals, width * 0.92, label=BASELINE_LABELS[key],
                      color=BASELINE_COLORS[key], edgecolor="white", linewidth=0.6)
        for b, v, l in zip(bars, vals, lost):
            if not math.isfinite(v):
                continue
            if l:
                b.set_hatch("//")
                b.set_alpha(0.55)
            ax.annotate(f"{v:.0f}" + ("\nL" if l else ""),
                        (b.get_x() + b.get_width() / 2, v),
                        textcoords="offset points", xytext=(0, 2),
                        ha="center", va="bottom", fontsize=7.5,
                        color="#333333" if not l else "#8a1b1b")

    ax.set_xticks(x)
    ax.set_xticklabels([tick_label(t) for t in trees])
    ax.set_ylabel("Cumulative defense cost")
    ax.grid(axis="y", alpha=0.22)
    ax.margins(y=0.20)
    # Explicit handles: the bars are mutated in place to hatch the lost runs,
    # and matplotlib would otherwise take the first bar of each container as the
    # legend swatch, so a policy that lost on the first instance appeared
    # hatched in the legend as if it always lost.
    ax.legend(handles=[mpatches.Patch(facecolor=BASELINE_COLORS[k],
                                      edgecolor="white",
                                      label=BASELINE_LABELS[k]) for k in order],
              ncol=len(order), fontsize=8, frameon=False, loc="lower center",
              bbox_to_anchor=(0.5, 1.005), columnspacing=1.2, handlelength=1.3)
    ax.annotate("hatched: the defender lost, so the cost is not comparable",
                (0.5, -0.17), xycoords="axes fraction", ha="center",
                fontsize=7.5, color="#666666")
    fig.tight_layout()
    for ext in ("pdf", "png"):
        fig.savefig(out_base.with_suffix(f".{ext}"), dpi=300, bbox_inches="tight")
    plt.close(fig)


def plot_memory(
    trees: Sequence[str],
    series: Dict[str, Dict[str, Dict[str, object]]],
    panacea_memory_mb: Dict[str, float],
    out_base: Path,
) -> None:
    """Peak resident memory, PANACEA against the simulated defenders.

    PANACEA figures come from `Maximum resident set size` in its own
    /usr/bin/time -v log; the simulated ones from getrusage inside the runner.
    Both are peak RSS of the whole process, so they are the same quantity, but
    the Python interpreter contributes a fixed floor of a few tens of megabytes
    that dominates the small instances. The log scale and the annotation make
    that floor visible instead of hiding it.
    """
    have_panacea = any(math.isfinite(panacea_memory_mb.get(t, math.nan))
                       for t in trees)
    keys = [k for k in ("irena", "lc", "ld") if k in series]
    if not keys:
        return

    order = (["panacea"] if have_panacea else []) + keys
    x = np.arange(len(trees), dtype=float)
    width = 0.8 / len(order)
    fig, ax = plt.subplots(figsize=(6.6, 3.6))
    floor = math.inf

    for i, key in enumerate(order):
        offs = x + (i - (len(order) - 1) / 2.0) * width
        vals = []
        for t in trees:
            if key == "panacea":
                vals.append(float(panacea_memory_mb.get(t, math.nan)))
            else:
                row = series[key].get(t)
                kb = float(row["max_rss_kb"]) if row else math.nan
                v = kb / 1024.0 if math.isfinite(kb) and kb > 0 else math.nan
                vals.append(v)
                if math.isfinite(v):
                    floor = min(floor, v)
        # In the memory figure PANACEA is just PANACEA: "proven minimum" is a
        # statement about defense cost and would be nonsense on this axis.
        label = "PANACEA" if key == "panacea" else BASELINE_LABELS[key]
        bars = ax.bar(offs, vals, width * 0.92, label=label,
                      color=BASELINE_COLORS[key], edgecolor="white", linewidth=0.6)
        for j, (b, v) in enumerate(zip(bars, vals)):
            if math.isfinite(v):
                lab = f"{v/1024:.1f} GB" if v >= 1024 else f"{v:.0f} MB"
                # A horizontal label is far wider than the bar it sits on, so
                # neighbouring labels overprinted ("137 MB86 MB" on the smallest
                # instance, where PANACEA and IRENA differ by less than the
                # height of one line of text on a log axis). Staggering the
                # vertical offset does not fix it, because on a log axis the
                # gap between two close values is itself only a few points.
                # Rotating the text makes each label about eight points wide,
                # which fits inside the bar whatever the values are.
                ax.annotate(lab, (b.get_x() + b.get_width() / 2, v),
                            textcoords="offset points", xytext=(0, 3),
                            ha="center", va="bottom", fontsize=7.0,
                            rotation=90, color="#333333")

    ax.set_yscale("log")
    ax.set_xticks(x)
    ax.set_xticklabels([tick_label(t) for t in trees])
    ax.set_ylabel("Peak resident memory (MB, log scale)")
    ax.grid(axis="y", alpha=0.22, which="both")
    # Rotated labels need vertical room: "51.7 GB" is about 34 points tall.
    ax.margins(y=0.30)
    lo, hi = ax.get_ylim()
    ax.set_ylim(lo, hi * 2.6)

    handles = [mlines.Line2D([], [], color=BASELINE_COLORS[k], linewidth=6,
                             label=("PANACEA" if k == "panacea"
                                    else BASELINE_LABELS[k]))
               for k in order]
    if math.isfinite(floor):
        ax.axhline(floor, color="#999999", linewidth=0.9, linestyle=":", zorder=1)
        # The floor line used to be labelled with text inside the axes. Every
        # position for it overlapped a bar, because the bars span the whole
        # height below their value and the line sits near the bottom. It goes in
        # the legend instead, where nothing can collide with it.
        handles.append(mlines.Line2D([], [], color="#999999", linewidth=0.9,
                                     linestyle=":",
                                     label="Python interpreter floor"))
    ax.legend(handles=handles, ncol=len(handles), fontsize=8, frameon=False,
              loc="lower center", bbox_to_anchor=(0.5, 1.005),
              columnspacing=1.2, handlelength=1.6)
    fig.tight_layout()
    for ext in ("pdf", "png"):
        fig.savefig(out_base.with_suffix(f".{ext}"), dpi=300, bbox_inches="tight")
    plt.close(fig)


def plot_times(
    trees: Sequence[str],
    panacea_times: Dict[str, float],
    vp: Dict[str, Dict[str, object]],
    out_base: Path,
    panacea_label: str = "PANACEA model checking",
) -> None:
    x = np.arange(len(trees), dtype=float)
    pt = [float(panacea_times[t]) for t in trees]
    vt = [float(vp[t]["planning_time"]) for t in trees]

    fig, ax = plt.subplots(figsize=(6.8, 4.0))
    ax.plot(
        x,
        pt,
        marker="o",
        linewidth=2.0,
        label=panacea_label,
        color=PANACEA_ATTACKER_COLOR,
    )
    ax.plot(
        x,
        vt,
        marker="s",
        linewidth=2.0,
        linestyle="--",
        label="IRENA planning",
        color=VP_DEFENDER_COLOR,
    )
    ax.set_yscale("log")
    ax.set_xlabel("R-ADT size")
    ax.set_ylabel("Planning time (s)")
    ax.set_xticks(x)
    ax.set_xticklabels(trees)
    ax.grid(axis="y", which="both", alpha=0.22)
    ax.legend(frameon=False)
    fig.tight_layout()

    for ext in ("pdf", "png"):
        fig.savefig(out_base.with_suffix(f".{ext}"), dpi=300, bbox_inches="tight")
    plt.close(fig)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument(
        "--experiment-dir",
        type=Path,
        default=None,
        help="PANACEA experiment3 directory (default: <script_dir>/experiment3)",
    )
    p.add_argument("--vp-csv", type=Path, default=None, help="Explicit Vector-PREANA aggregate CSV")
    p.add_argument("--risk-mode", choices=["dynamic", "neutral"], default="dynamic")
    p.add_argument("--attacker-policy", choices=["greedy", "adaptive"], default="adaptive")
    p.add_argument("--learning-mode", choices=["on", "off"], default="on")
    p.add_argument("--turn-order", choices=["attacker_first", "defender_first"], default="attacker_first")
    p.add_argument(
        "--trees",
        nargs="+",
        default=["10", "25", "29"],
        help="Tree names/sizes to plot. Default: 10 25 29",
    )
    p.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Output directory (default: experiment3/figures)",
    )
    p.add_argument(
        "--paper-aliases",
        action="store_true",
        help="Also create cost_comparison_validated.* and planning_time_comparison_validated.* aliases",
    )
    p.add_argument(
        "--sweep-glob",
        default="*lambdaD*.csv",
        help="Glob, relative to the experiment directory, matching the "
             "Vector-PREANA CSVs of a lambda_D sweep. The default catches both "
             "sweep_lambdaD_*.csv and vector_preana_lambdaD_*_result.csv. "
             "Combined with the main CSV to build lambda_sweep.*; lambda_D is "
             "read from each file's defense_cost_weight column, not its name, "
             "and the values actually used are printed.",
    )
    p.add_argument(
        "--lc-csv", type=Path, default=None,
        help="R-ADT LC baseline CSV; auto-discovered from the run configuration "
             "when omitted",
    )
    p.add_argument(
        "--ld-csv", type=Path, default=None,
        help="R-ADT LD baseline CSV; auto-discovered when omitted",
    )
    p.add_argument(
        "--perf-exclude", nargs="*", default=None, metavar="TREE",
        help="instances to keep in the cost figures but leave out of the "
             "performance ones. The scalability figures plot a size ladder, "
             "and an instance that is not a point on that ladder -- the "
             "semantic case study -- makes the trend line bend for no reason.",
    )
    p.add_argument(
        "--cost-exclude", nargs="*", default=None, metavar="TREE",
        help="instances to keep in the performance figures but leave out of "
             "every cost figure. Use it for an instance included for "
             "scalability whose costs the paper does not compare. Whatever is "
             "passed is echoed, so a dropped instance is never silent.",
    )
    p.add_argument(
        "--no-baselines", action="store_true",
        help="ignore the R-ADT LC and LD runs even if their CSVs exist",
    )
    p.add_argument(
        "--extra-figures",
        action="store_true",
        help="Also emit defender_cost_vs_optimum.* (defense cost against the "
             "proven minimum) and state_space_growth.* (the explicit model "
             "size PANACEA builds and Vector-PREANA does not).",
    )
    p.add_argument(
        "--panacea-time",
        choices=["checking", "total"],
        default="checking",
        help="Which PANACEA cost to plot. 'checking' is PRISM's model-checking "
             "timer alone (what the draft used). 'total' adds model "
             "construction, i.e. the whole cost of obtaining the policy, which "
             "is the part Vector-PREANA never pays.",
    )
    return p.parse_args()


def find_experiment_dir(explicit: Optional[Path] = None) -> Path:
    """Locate experiment3 without depending on the working directory.

    Anchored on tree_to_prism.py so the script behaves the same whether it is
    run from the repository root or from experiments/.
    """
    if explicit is not None:
        return Path(explicit).resolve()
    seen: List[Path] = []
    here = Path(__file__).resolve()
    for base in [here.parent, *here.parents,
                 Path.cwd().resolve(), *Path.cwd().resolve().parents]:
        if base in seen:
            continue
        seen.append(base)
        if (base / "tree_to_prism.py").exists():
            for c in (base / "experiment",
                      base / "experiments" / "experiment3", base / "experiment3"):
                if c.is_dir():
                    return c
    fallback = (here.parent / "experiment3").resolve()
    if fallback.is_dir():
        return fallback
    raise SystemExit(
        "ERROR: could not locate experiment3. Pass --experiment-dir explicitly.")


def main() -> None:
    args = parse_args()
    experiment_dir = find_experiment_dir(args.experiment_dir)
    output_dir = (args.output_dir or (experiment_dir / "figures")).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"experiment dir: {experiment_dir}")
    print(f"output dir:     {output_dir}")

    vp_csv = args.vp_csv
    if vp_csv is None:
        vp_csv = expected_vp_csv(
            experiment_dir,
            args.risk_mode,
            args.attacker_policy,
            args.learning_mode,
            args.turn_order,
        )
    vp_csv = vp_csv.resolve()
    if not vp_csv.exists():
        raise FileNotFoundError(
            f"Vector-PREANA CSV not found: {vp_csv}\n"
            "Run run_vector_preana_experiment3.sh with the same configuration, "
            "or pass --vp-csv explicitly."
        )

    vp = load_vp(vp_csv)

    # R-ADT LC and LD baselines. They are optional: the script still produces
    # every pre-existing figure when they have not been run.
    series: Dict[str, Dict[str, Dict[str, object]]] = {"irena": vp}
    if not args.no_baselines:
        for policy, explicit in (("lc", args.lc_csv), ("ld", args.ld_csv)):
            path = explicit or baseline_csv(
                experiment_dir, args.risk_mode, args.attacker_policy,
                args.learning_mode, args.turn_order, policy)
            if path and Path(path).exists():
                loaded = load_vp(Path(path))
                declared = {str(r.get("defender_policy", "")) for r in loaded.values()}
                if declared and declared != {policy}:
                    print(f"warning: {Path(path).name} declares defender_policy="
                          f"{sorted(declared)} but was loaded as the {policy.upper()} "
                          f"baseline; check the file", file=sys.stderr)
                series[policy] = loaded
                print(f"{policy.upper()} baseline:      {path}")
            else:
                print(f"{policy.upper()} baseline:      not found ({path});"
                      f" run with defender policy '{policy}' to add it",
                      file=sys.stderr)
    panacea_times = extract_panacea_model_checking_times(experiment_dir)

    requested = [normalize_name(t) for t in args.trees]
    trees = [t for t in requested if t in vp]
    if not trees:
        raise RuntimeError(
            f"None of the requested trees {requested} were found in {vp_csv}. "
            f"Available: {sorted(vp)}"
        )

    panacea: Dict[str, Dict[str, object]] = {}
    missing_panacea = []
    for tree in trees:
        try:
            prism, dot = choose_untimed_model_files(experiment_dir, tree)
            panacea[tree] = score_panacea_strategy(prism, dot)
            panacea[tree]["prism"] = str(prism)
            panacea[tree]["dot"] = str(dot)
        except (FileNotFoundError, RuntimeError) as exc:
            missing_panacea.append((tree, str(exc)))

    if missing_panacea:
        details = "\n".join(f"  {t}: {msg}" for t, msg in missing_panacea)
        raise RuntimeError("Could not recover PANACEA strategy data:\n" + details)

    missing_time = [t for t in trees if t not in panacea_times]
    if missing_time:
        raise RuntimeError(
            "Could not find PANACEA model-checking time for: " + ", ".join(missing_time) + "\n"
            "Expected the original PANACEA Experiment 3 result.csv somewhere under experiment3/."
        )

    tag = (
        "full"
        if (
            args.risk_mode == "dynamic"
            and args.attacker_policy == "adaptive"
            and args.learning_mode == "on"
            and args.turn_order == "attacker_first"
            and args.vp_csv is None
        )
        else f"{args.risk_mode}_{args.attacker_policy}_learning_{args.learning_mode}_{args.turn_order}"
    )

    plotted_times = dict(panacea_times)
    if args.panacea_time == "total":
        for tree in trees:
            s = PANACEA_STATS.get(tree)
            if s and math.isfinite(s.get("build_time_s", math.nan)):
                plotted_times[tree] = panacea_times[tree] + s["build_time_s"]

    suffix = "" if args.panacea_time == "checking" else "_totaltime"
    # Two families of figures, because they answer different questions and the
    # paper reports them in different sections. PERFORMANCE covers every
    # instance: model size, planning time and memory are properties of the
    # computation and do not depend on what the costs mean. EFFECTIVENESS
    # covers only the instances whose costs the paper actually compares, which
    # `--cost-exclude` narrows: an instance kept for scalability alone has no
    # business on a cost axis.
    cost_exclude = {normalize_name(x) for x in (args.cost_exclude or ())}
    unknown = sorted(cost_exclude - set(trees))
    if unknown:
        print(f"WARNING: --cost-exclude names {', '.join(unknown)}, which are "
              f"not among the plotted instances", file=sys.stderr)
    cost_trees = [t for t in trees if t not in cost_exclude]
    perf_exclude = {normalize_name(x) for x in (args.perf_exclude or ())}
    perf_trees = [t for t in trees if t not in perf_exclude]
    if perf_exclude:
        print(f"performance figures exclude: "
              f"{', '.join(t for t in trees if t in perf_exclude)} "
              f"(they are not part of the size ladder)")
    if not perf_trees:
        raise RuntimeError("--perf-exclude removed every instance from the "
                           "performance figures")
    if cost_exclude:
        print(f"cost figures exclude: "
              f"{', '.join(t for t in trees if t in cost_exclude)} "
              f"(performance figures still cover every instance)")
    if not cost_trees:
        raise RuntimeError("--cost-exclude removed every instance from the "
                           "cost figures")

    perf_bases: List[Path] = []
    eff_bases: List[Path] = []

    time_base = output_dir / f"perf_planning_time_{tag}{suffix}"
    cost_base = output_dir / f"eff_cost_breakdown_{tag}"

    # ---- effectiveness -----------------------------------------------------
    plot_costs(cost_trees, panacea, vp, cost_base, series=series)
    eff_bases.append(cost_base)
    if len(series) > 1:
        plot_baseline_costs(cost_trees, panacea, series,
                            output_dir / f"eff_defender_cost_{tag}")
        eff_bases.append(output_dir / f"eff_defender_cost_{tag}")

    # ---- performance -------------------------------------------------------
    panacea_memory_mb = {}
    for tree in trees:
        extra = read_panacea_log(Path(str(panacea[tree]["dot"])))
        if "memory_mb" in extra:
            panacea_memory_mb[tree] = float(extra["memory_mb"])
    plot_memory(perf_trees, series, panacea_memory_mb,
                output_dir / f"perf_peak_memory_{tag}")
    perf_bases.append(output_dir / f"perf_peak_memory_{tag}")
    perf_bases.append(time_base)
    plot_times(
        perf_trees, plotted_times, vp, time_base,
        panacea_label=("PANACEA model construction + checking"
                       if args.panacea_time == "total"
                       else "PANACEA model checking"),
    )
    if args.extra_figures:
        plot_defender_vs_optimum(
            cost_trees, panacea, vp, output_dir / f"eff_cost_vs_optimum_{tag}")
        eff_bases.append(output_dir / f"eff_cost_vs_optimum_{tag}")
        if PANACEA_STATS:
            plot_state_space(
                perf_trees, output_dir / f"perf_state_space_{tag}")
            perf_bases.append(output_dir / f"perf_state_space_{tag}")

        sweep_paths = sorted(set(experiment_dir.glob(args.sweep_glob)) | {vp_csv})
        sweep = load_sweep(sweep_paths)
        lambdas = sorted({l for s in sweep.values() for l in s})
        print()
        print(f"lambda_D sweep, from glob '{args.sweep_glob}' plus the main CSV:")
        for p in sweep_paths:
            print(f"    read {p.name}")
        for lam in lambdas:
            covered = sorted(t for t in trees if lam in sweep.get(t, {}))
            print(f"    lambda_D={lam:<8g} trees {', '.join(covered) or '-'}")
        missing = [t for t in trees
                   if len(sweep.get(t, {})) < len(lambdas)]
        if missing:
            print(f"    note: {', '.join(missing)} lack a point at some "
                  f"lambda_D; those curves will be shorter", file=sys.stderr)
        # lambda_D of the reported runs, read from the main CSV. The sweep
        # figure highlights THIS value; see plot_lambda_sweep for why it must
        # not be re-derived from the sweep.
        reported = sorted({v for t in trees
                           if math.isfinite(
                               v := as_float(vp.get(t, {}).get("lambda_D")))})
        reported_lam = reported[0] if len(reported) == 1 else None
        if len(reported) == 1:
            print(f"    reported configuration: lambda_D={reported_lam:g} "
                  f"(from {vp_csv.name}); highlighted in the sweep figure")
        elif len(reported) > 1:
            print(f"    WARNING: {vp_csv.name} mixes lambda_D values "
                  f"({', '.join(f'{v:g}' for v in reported)}); the sweep figure "
                  f"falls back to the inferred operating point and the reported "
                  f"numbers are not one configuration", file=sys.stderr)
        else:
            print(f"    note: {vp_csv.name} carries no defense_cost_weight "
                  f"column; the operating point is inferred from the sweep",
                  file=sys.stderr)
        if len(lambdas) >= 2:
            plot_lambda_sweep(trees, sweep, panacea,
                              output_dir / f"eff_lambda_sweep_{tag}",
                              reported_lam=reported_lam,
                              cost_trees=cost_trees)
            eff_bases.append(output_dir / f"eff_lambda_sweep_{tag}")
        else:
            print(f"lambda_D sweep skipped: only {len(lambdas)} value of "
                  f"defense_cost_weight found. Check that the sweep CSVs match "
                  f"'{args.sweep_glob}' in {experiment_dir}.", file=sys.stderr)

    if args.paper_aliases:
        # main.tex includes the *_validated names, so every figure it can show
        # needs one. The extra figures are aliased only when they were produced.
        aliases = [(cost_base, "eff_cost_breakdown"),
                   (time_base, "perf_planning_time"),
                   (output_dir / f"eff_defender_cost_{tag}",
                    "eff_defender_cost"),
                   (output_dir / f"perf_peak_memory_{tag}", "perf_peak_memory")]
        if args.extra_figures:
            aliases += [
                (output_dir / f"perf_state_space_{tag}", "perf_state_space"),
                (output_dir / f"eff_lambda_sweep_{tag}", "eff_lambda_sweep"),
                (output_dir / f"eff_cost_vs_optimum_{tag}",
                 "eff_cost_vs_optimum"),
            ]
        for base, alias in aliases:
            for ext in ("pdf", "png"):
                src = base.with_suffix(f".{ext}")
                if src.exists():
                    shutil.copy2(src, output_dir / f"{alias}.{ext}")

    print(f"Vector-PREANA CSV: {vp_csv}")
    print(f"Trees plotted:       {', '.join(trees)}")
    print()
    for tree in trees:
        p = panacea[tree]
        v = vp[tree]
        print(
            f"{tree:>4} | PANACEA {str(p['winner']):>8} "
            f"A={float(p['attacker_cost']):g} D={float(p['defender_cost']):g} "
            f"t={panacea_times[tree]:.6g}s | "
            f"VP {str(v['winner']):>8} "
            f"A={float(v['attacker_cost']):g} D={float(v['defender_cost']):g} "
            f"t={float(v['planning_time']):.6g}s"
        )
    if len(series) > 1:
        print()
        print("Defender policies (same adaptive attacker in all simulated runs):")
        for tree in trees:
            parts = []
            for key in ("irena", "lc", "ld"):
                row = series.get(key, {}).get(tree)
                if not row:
                    continue
                mark = "" if str(row["winner"]) == "defender" else " LOST"
                # Defender-side planning time, not the total: the attacker is
                # the same counterfactual one in all three runs, so the total
                # is dominated by a component the defender policy does not
                # control and would make the rules look far slower than they are.
                dt = float(row.get("defender_planning_time", math.nan))
                dts = f" t_D={dt:.6g}s" if math.isfinite(dt) else ""
                parts.append(f"{key.upper()} D={float(row['defender_cost']):g}"
                             f" A={float(row['attacker_cost']):g}{dts}{mark}")
            opt = float(panacea[tree]["defender_cost"])
            print(f"  {tree:>4} min={opt:g} | " + " | ".join(parts))

    print()
    print("Peak resident memory:")
    for tree in trees:
        pan = panacea_memory_mb.get(tree)
        bits = [f"PANACEA={pan:.0f}MB" if pan else "PANACEA=n/a"]
        for key in ("irena", "lc", "ld"):
            row = series.get(key, {}).get(tree)
            if row and math.isfinite(float(row["max_rss_kb"])) and float(row["max_rss_kb"]) > 0:
                bits.append(f"{key.upper()}={float(row['max_rss_kb'])/1024:.0f}MB")
        print(f"  {tree:>4} " + "  ".join(bits))

    print()
    print("PANACEA sources (verify these are the UNTIMED models):")
    for tree in trees:
        # Relative to the experiment directory, not just <parent>/<name>: with
        # both experiment3/34/34.dot and experiment3/results/34/34.dot on disk
        # the short form printed "34/34.dot" for either of them, hiding the one
        # component that says which run this row came from. The line exists to
        # let the provenance be checked, so it has to show the whole path.
        def _rel(p: Path) -> str:
            try:
                return str(p.resolve().relative_to(experiment_dir.resolve()))
            except ValueError:
                return str(p)

        _pp = Path(str(panacea[tree]["prism"]))
        _dp = Path(str(panacea[tree]["dot"]))
        print(f"  {tree:>4} prism={_rel(_pp)} "
              f"dot={_rel(_dp)} "
              f"actions={len(panacea[tree]['actions'])} "
              f"time<-{TIME_PROVENANCE.get(tree, 'unknown')}")
    if PANACEA_STATS:
        print()
        print("PANACEA model size (the state explosion Vector-PREANA avoids):")
        for tree in trees:
            s = PANACEA_STATS.get(tree)
            if not s:
                continue
            extra = read_panacea_log(Path(str(panacea[tree]["dot"])))
            mem = (f" memory={extra['memory_mb']:.0f}MB"
                   if "memory_mb" in extra else "")
            wall = (f" wall={extra['wall_time_s']:g}s"
                    if "wall_time_s" in extra else "")
            print(f"  {tree:>4} states={s['states']:.0f} "
                  f"transitions={s['transitions']:.0f} "
                  f"build={s['build_time_s']:g}s "
                  f"check={panacea_times[tree]:g}s "
                  f"build+check={s['build_time_s'] + panacea_times[tree]:g}s"
                  f"{mem}{wall} prism_result={s['prism_result']:g}")
        print(f"  plotted PANACEA time = "
              f"{'model construction + model checking' if args.panacea_time == 'total' else 'model checking only'}")
    print()
    print("Generated:")
    seen: set = set()
    for label, bases, scope in (
            ("performance (size ladder)", perf_bases, perf_trees),
            ("effectiveness (cost-compared instances)", eff_bases, cost_trees)):
        print(f"  {label}: {', '.join(scope)}")
        for base in bases:
            if base in seen:
                continue
            seen.add(base)
            for ext in ("pdf", "png"):
                path = base.with_suffix(f".{ext}")
                if path.exists():
                    print(f"    {path}")
                else:
                    print(f"    MISSING {path}", file=sys.stderr)
    if args.paper_aliases:
        print("  aliases used by main.tex:")
        for path in sorted(output_dir.glob("eff_*.pdf")) + \
                sorted(output_dir.glob("perf_*.pdf")):
            if "_" in path.stem and not path.stem.endswith(tag):
                print(f"    {path}")


if __name__ == "__main__":
    main()
