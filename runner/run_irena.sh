#!/usr/bin/env bash
set -euo pipefail

PACKAGE_VERSION="2026-08-19-v3"

# Adaptive Vector-PREANA benchmark for PANACEA Experiment 3.
#
# This file lives in <package>/runner/, where <package> is the root of the
# self-contained irena distribution. PANACEA is NOT required and NOT invoked:
# the package ships its own copy of the tree_to_prism.py parser (package root)
# and the R-ADT XML instances (experiment/trees/).
#
# This revision adds PREANA-style dynamic risk/security baselines:
#   R_i  -> r_i -> r_ij
#
# At each local negotiation round:
#   1. neutral expected utilities are first computed with r = 1;
#   2. a local risk index R_i in [-1,1] is derived from PREANA's
#      normalization of actual/minimum/maximum feasible expected utility;
#   3. PREANA's transform r_i=(1-R_i/3)/(1+R_i/3) gives the current
#      player-level risk/security baseline;
#   4. opponent-specific realized experience Delta_ij is added:
#          r_ij = clip(r_i + Delta_ij, 0.5, 2.0).
#
# Counterfactual negotiations may adapt a TEMPORARY Delta_ij, but only
# realized attack-defense encounters update persistent Delta_ij.
#
# Usage:
#   chmod +x run_irena.sh
#   ./run_irena.sh \
#       [REPEATS] [DEFENSE_COST_WEIGHT] [ATTACK_COST_WEIGHT] \
#       [MAX_STEPS] [MAX_ROUNDS] [MEMORY_RISK_WEIGHT] \
#       [ATTACK_GOAL_BONUS] [RISK_MODE] [ATTACKER_POLICY] [LEARNING_MODE] \
#       [TURN_ORDER] [DEFENDER_ABSTAIN] [DEFENDER_POLICY]
#
# Full model (defaults):
#   ./run_irena.sh 20 0.0001 0.0001 60 40 0.70 1.0 dynamic adaptive on attacker_first off
#
# Fair-cost comparison against PANACEA (defender may decline to act):
#   ./run_irena.sh 20 0.0001 0.0001 60 40 0.70 1.0 dynamic adaptive on attacker_first on
#
# Diagnostic turn-order A -- proactive defender moves first:
#   ./run_irena.sh 20 0.0001 0.0001 60 40 0.70 1.0 neutral greedy off defender_first
#
# Diagnostic turn-order B -- PANACEA-like attacker moves first:
#   ./run_irena.sh 20 0.0001 0.0001 60 40 0.70 1.0 neutral greedy off attacker_first
#
# ATTACKER_POLICY:
#   greedy   = state-adaptive one-step heuristic used by the earlier prototype
#   adaptive = counterfactual Vector-PREANA attacker
#
# LEARNING_MODE:
#   off = disables both within-negotiation and persistent relational learning
#   on  = enables both forms of relational learning
#
# TURN_ORDER:
#   attacker_first = attacker acts first, then defender (default; PANACEA-like)
#   defender_first = defender receives one proactive move before the first attack,
#                    then the episode alternates defender/attacker. A defense after
#                    a realized attack is paired with that previous attack for learning.
#
# DEFENDER_POLICY:
#   irena  full local-negotiation defender (default)
#   lc     R-ADT LC baseline: the cheapest response enabled in the current
#          state, as in the PANACEA evaluation
#   ld     R-ADT LD baseline: the enabled response that blocks the precondition
#          closest to the attacker root, irrespective of cost
#
#   Only the DEFENDER changes. The attacker stays the adaptive counterfactual
#   one, and the relational memory is still updated from the realized pair, so
#   the three runs differ in exactly one factor and their costs are comparable.
#
# DEFENDER_ABSTAIN:
#   off = defender always responds when any defense is enabled (original behaviour)
#   on  = a no-op candidate is added to the defender's candidate set and scored
#         symmetrically with the real defenses: the game that would arise if no
#         response were executed is negotiated, and the resulting medoid risk is
#         compared against Risk(mu_d) + lambda_D * C_D(d) for each defense.
#         PANACEA's globally solved policy can stay on a cheaper trajectory by
#         not acting; without this option the cost comparison is not like-for-like.
#         An abstention consumes a turn, is recorded in the trace, and does NOT
#         update relational memory (no attack-defense pair was realized).
#
# Output names encode non-default ablations. The full default configuration
# (dynamic/adaptive/on/attacker_first) keeps the original drop-in-compatible paths:
#   experiment3/vector_preana_result.csv
#   experiment3/vector_preana_results/<tree>/...

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"      # runner/ -> package root
DIRECTORY="$REPO_ROOT/experiment"
TREE_DIR="$DIRECTORY/trees"

REPEATS="${1:-10}"
DEFENSE_COST_WEIGHT="${2:-0.0001}"
ATTACK_COST_WEIGHT="${3:-0.0001}"
MAX_STEPS="${4:-60}"
MAX_ROUNDS="${5:-40}"
MEMORY_RISK_WEIGHT="${6:-0.70}"
ATTACK_GOAL_BONUS="${7:-1.0}"
RISK_MODE="${8:-neutral}"
ATTACKER_POLICY="${9:-adaptive}"
LEARNING_MODE="${10:-off}"
TURN_ORDER="${11:-attacker_first}"
DEFENDER_ABSTAIN="${12:-off}"
DEFENDER_POLICY="${13:-irena}"

case "$RISK_MODE" in
  dynamic|neutral) ;;
  *)
    echo "ERROR: RISK_MODE must be 'dynamic' or 'neutral' (got: $RISK_MODE)" >&2
    exit 1
    ;;
esac

case "$ATTACKER_POLICY" in
  greedy|adaptive) ;;
  *)
    echo "ERROR: ATTACKER_POLICY must be 'greedy' or 'adaptive' (got: $ATTACKER_POLICY)" >&2
    exit 1
    ;;
esac

case "$LEARNING_MODE" in
  on|off) ;;
  *)
    echo "ERROR: LEARNING_MODE must be 'on' or 'off' (got: $LEARNING_MODE)" >&2
    exit 1
    ;;
esac

case "$TURN_ORDER" in
  attacker_first|defender_first) ;;
  *)
    echo "ERROR: TURN_ORDER must be 'attacker_first' or 'defender_first' (got: $TURN_ORDER)" >&2
    exit 1
    ;;
esac

case "$DEFENDER_ABSTAIN" in
  on|off) ;;
  *)
    echo "ERROR: DEFENDER_ABSTAIN must be 'on' or 'off' (got: $DEFENDER_ABSTAIN)" >&2
    exit 1
    ;;
esac

case "$DEFENDER_POLICY" in
  irena|lc|ld) ;;
  *)
    echo "ERROR: DEFENDER_POLICY must be 'irena', 'lc' or 'ld' (got: $DEFENDER_POLICY)" >&2
    exit 1
    ;;
esac

# The plain filenames belong to the CANONICAL (reported) configuration:
# neutral coefficient, counterfactual attacker, learning off, attacker first.
# Every other configuration gets its own suffixed directories/files so
# diagnostic runs never overwrite one another.
if [[ "$RISK_MODE" == "neutral" && "$ATTACKER_POLICY" == "adaptive" && "$LEARNING_MODE" == "off" && "$TURN_ORDER" == "attacker_first" && "$DEFENDER_ABSTAIN" == "off" && "$DEFENDER_POLICY" == "irena" ]]; then
  RESULT_DIR="$DIRECTORY/vector_preana_results"
  SUMMARY_CSV="$DIRECTORY/vector_preana_result.csv"
else
  SUFFIX="${RISK_MODE}_${ATTACKER_POLICY}_learning_${LEARNING_MODE}_${TURN_ORDER}"
  # Appended only when enabled, so every pre-existing configuration keeps its
  # historical paths and the comparison script's expected_vp_csv() still works.
  if [[ "$DEFENDER_ABSTAIN" == "on" ]]; then
    SUFFIX="${SUFFIX}_abstain_on"
  fi
  # Baseline runs must never land on the paths of the full model.
  if [[ "$DEFENDER_POLICY" != "irena" ]]; then
    SUFFIX="${SUFFIX}_def_${DEFENDER_POLICY}"
  fi
  RESULT_DIR="$DIRECTORY/vector_preana_${SUFFIX}_results"
  SUMMARY_CSV="$DIRECTORY/vector_preana_${SUFFIX}_result.csv"
fi

if [[ ! -d "$TREE_DIR" ]]; then
  echo "ERROR: tree directory not found: $TREE_DIR" >&2
  exit 1
fi

if [[ ! -f "$REPO_ROOT/tree_to_prism.py" ]]; then
  echo "ERROR: tree_to_prism.py not found in the package root: $REPO_ROOT" >&2
  echo "The irena package ships its own copy of the parser; restore it there." >&2
  exit 1
fi

rm -rf "$RESULT_DIR"
mkdir -p "$RESULT_DIR"

TMP_PY="$(mktemp -t vector_preana_dynamic_risk_exp3_XXXXXX.py)"
trap 'rm -f "$TMP_PY"' EXIT

cat > "$TMP_PY" <<'PY'
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import statistics
import sys
import time
from collections import deque
from dataclasses import dataclass
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple


EPS = 1e-9
R_MIN = 0.5
R_MAX = 2.0
DELTA_MIN = R_MIN - 1.0  # conservative bound for persistent offset
DELTA_MAX = R_MAX - 1.0


def _peak_rss_kb() -> int:
    """Peak resident set size of this process, in kilobytes.

    Measured with getrusage rather than an external timer so the value lands in
    the CSV directly. It covers every repetition of the episode, and it
    includes the Python interpreter itself: on the smallest trees that baseline
    dominates, so only the growth across tree sizes is meaningful, not the
    absolute value.
    """
    try:
        import resource
        usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # Linux reports kilobytes; macOS reports bytes.
        return int(usage if sys.platform != "darwin" else usage / 1024)
    except Exception:
        return -1


def clamp01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def clip(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(x)))


def mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def l1(a: Sequence[float], b: Sequence[float]) -> float:
    return sum(abs(float(x) - float(y)) for x, y in zip(a, b)) / max(1, len(a))


@dataclass
class Player:
    pid: str
    kind: str
    proposal: List[float]
    goal: List[float]
    capability: List[float]
    salience: List[float]
    cost: float = 0.0


class RelationalMemory:
    """Persistent PREANA-style opponent-specific experience.

    The player-level baseline r_i is NOT stored here. It is recomputed from
    the current local negotiation, as in PREANA's risk-taking component.
    Persistent memory stores only the relational correction Delta_ij and the
    accumulated realized interaction evidence L_ij.

    Candidate (counterfactual) negotiations read this memory but cannot mutate
    it. Only a realized attack-defense pair changes persistent memory.
    """

    def __init__(
        self,
        beta: float = 0.01,
        delta_min: float = DELTA_MIN,
        delta_max: float = DELTA_MAX,
    ):
        self.beta = float(beta)
        self.delta_min = float(delta_min)
        self.delta_max = float(delta_max)
        self.delta: Dict[Tuple[str, str], float] = {}
        self.learn: Dict[Tuple[str, str], float] = {}
        self.events: List[dict] = []
        # pid -> branch tag ("A@<branch>" / "D@<branch>") resolver, wired to
        # Model.player_branch_tag after the model is built. Lets experience
        # generalize across actions of the same attack-progression branch.
        self.tag_fn = None

    def get_delta(self, source: str, target: str) -> float:
        """Specific experience first; branch-level experience as fallback.

        Concrete attack actions are one-shot, so the exact pair that was
        learned from never occurs again. The branch-level entry, updated by
        the same realized encounters, is what lets that experience influence
        FUTURE pairs on the same branches.
        """
        v = self.delta.get((source, target), 0.0)
        if v != 0.0 or self.tag_fn is None:
            return v
        ts, tt = self.tag_fn(source), self.tag_fn(target)
        if ts is None or tt is None:
            return 0.0
        return self.delta.get((ts, tt), 0.0)

    def reinforce(
        self,
        source: str,
        target: str,
        signal: float,
        salience: float,
        context: Optional[dict] = None,
    ) -> None:
        key = (source, target)
        old_learn = self.learn.get(key, 0.0)
        new_learn = old_learn + float(signal)
        self.learn[key] = new_learn

        old_delta = self.delta.get(key, 0.0)
        new_delta = clip(
            old_delta + self.beta * clamp01(salience) * new_learn,
            self.delta_min,
            self.delta_max,
        )
        self.delta[key] = new_delta

        self.events.append(
            {
                "source": source,
                "target": target,
                "signal": float(signal),
                "salience": float(salience),
                "learn_before": old_learn,
                "learn_after": new_learn,
                "delta_before": old_delta,
                "delta_after": new_delta,
                "context": context or {},
            }
        )

    def observe_encounter(
        self,
        model: "Model",
        attack: str,
        defense: str,
        risk_before_attack: float,
        risk_after_attack: float,
        risk_after_defense: float,
        enabled_after_attack: int,
        enabled_after_defense: int,
        memory_risk_weight: float,
    ) -> None:
        """Update persistent Delta_ij from an actually observed encounter."""
        gain = max(0.0, risk_after_attack - risk_before_attack)
        if gain > 1e-12:
            recovered = clamp01((risk_after_attack - risk_after_defense) / gain)
        else:
            recovered = 0.0

        if enabled_after_attack > 0:
            future_reduction = clamp01(
                (enabled_after_attack - enabled_after_defense)
                / float(enabled_after_attack)
            )
        else:
            future_reduction = 0.0

        omega = clamp01(memory_risk_weight)
        defense_effectiveness = clamp01(
            omega * recovered + (1.0 - omega) * future_reduction
        )

        # +1: attacker retained its advantage; -1: defense dominated.
        attack_signal = 1.0 - 2.0 * defense_effectiveness
        defense_signal = -attack_signal

        attack_effect = str(model.attacks[attack]["effect"])
        defense_effect = str(model.defenses[defense]["effect"])
        attack_salience = 0.25 + 0.75 * model.root_closeness(attack_effect)
        defense_salience = 0.25 + 0.75 * model.root_closeness(defense_effect)

        context = {
            "attack": attack,
            "defense": defense,
            "risk_before_attack": risk_before_attack,
            "risk_after_attack": risk_after_attack,
            "risk_after_defense": risk_after_defense,
            "enabled_after_attack": enabled_after_attack,
            "enabled_after_defense": enabled_after_defense,
            "recovered_fraction": recovered,
            "future_reduction_fraction": future_reduction,
            "memory_risk_weight": omega,
            "defense_effectiveness": defense_effectiveness,
        }
        self.reinforce(
            f"A:{attack}",
            f"D:{defense}",
            attack_signal,
            attack_salience,
            context,
        )
        self.reinforce(
            f"D:{defense}",
            f"A:{attack}",
            defense_signal,
            defense_salience,
            context,
        )
        # Branch-level generalization: the same signals also update the
        # (attack-branch, defense-branch) entries, keyed by the top-level
        # branches of the tree the two effects belong to. These keys DO recur
        # in later games, which is what makes persistent memory able to
        # influence future decisions at all (concrete pairs are one-shot).
        atag = model.player_branch_tag(f"A:{attack}")
        dtag = model.player_branch_tag(f"D:{defense}")
        if atag is not None and dtag is not None:
            gcontext = dict(context)
            gcontext["generalized"] = True
            self.reinforce(atag, dtag, attack_signal, attack_salience, gcontext)
            self.reinforce(dtag, atag, defense_signal, defense_salience, gcontext)

    def snapshot(self) -> dict:
        def key_to_str(k: Tuple[str, str]) -> str:
            return f"{k[0]} -> {k[1]}"

        return {
            "risk_offset_delta": {
                key_to_str(k): v for k, v in sorted(self.delta.items())
            },
            "learn": {key_to_str(k): v for k, v in sorted(self.learn.items())},
            "events": self.events,
        }


class Model:
    def __init__(self, xml_path: str, repo_root: str):
        sys.path.insert(0, repo_root)
        from tree_to_prism import parse_file, get_info  # type: ignore

        parse_start = time.perf_counter()
        tree = parse_file(xml_path)
        df = tree.to_dataframe()
        (
            self.goal,
            self.actions_to_goal,
            self.initial_attributes,
            self.attacks,
            self.defenses,
            self.df_attacker,
            self.df_defender,
        ) = get_info(df)
        self.parse_time = time.perf_counter() - parse_start
        self.df = df
        self.xml_path = xml_path
        self._branch_tags: Dict[str, Optional[str]] = {}
        self.name = os.path.splitext(os.path.basename(xml_path))[0]

        attrs = list(
            df.loc[
                (df["Role"] == "Attacker") & (df["Type"] == "Attribute"),
                "Label",
            ].values
        )
        self.attributes = sorted(set(str(x) for x in attrs))
        self.dims = [self.goal] + self.attributes
        self.index = {name: i for i, name in enumerate(self.dims)}

        self.attack_cost = {
            a: float(info["cost"]) for a, info in self.attacks.items()
        }
        self.defense_cost = {
            a: float(info["cost"]) for a, info in self.defenses.items()
        }

        # Attack progression graph: precondition -> effect precondition.
        self.graph: Dict[str, Set[str]] = {d: set() for d in self.dims}
        for _, info in self.attacks.items():
            effect = str(info["effect"])
            for p in info["preconditions"]:
                p = str(p)
                if p in self.graph and effect in self.graph:
                    self.graph[p].add(effect)

        self.dist = self._distances_to_goal()
        finite = [d for d in self.dist.values() if d < 10**9]
        self.max_dist = max(finite) if finite else 1
        self.weights = [self._structural_importance(d) for d in self.dims]
        self.xml_nodes = int(len(df))

    def _distances_to_goal(self) -> Dict[str, int]:
        rev = {d: set() for d in self.dims}
        for u, vs in self.graph.items():
            for v in vs:
                rev[v].add(u)
        inf = 10**9
        dist = {d: inf for d in self.dims}
        dist[self.goal] = 0
        q = deque([self.goal])
        while q:
            node = q.popleft()
            for pred in rev[node]:
                if dist[pred] == inf:
                    dist[pred] = dist[node] + 1
                    q.append(pred)
        return dist

    def _structural_importance(self, dim: str) -> float:
        if dim == self.goal:
            return 1.0
        d = self.dist.get(dim, 10**9)
        if d >= 10**9:
            return 0.20
        return 0.25 + 0.75 * (1.0 - d / float(self.max_dist + 1))

    def root_closeness(self, dim: str) -> float:
        if dim == self.goal:
            return 1.0
        d = self.dist.get(dim, 10**9)
        if d >= 10**9:
            return 0.0
        return clamp01(1.0 - d / float(self.max_dist + 1))

    def branch_key(self, dim: str) -> str:
        """The top-level branch(es) of the goal reachable from `dim`.

        A branch is a direct predecessor of the goal in the attack-progression
        graph; a dimension serving several branches gets their joint key. This
        is the granularity at which persistent memory generalizes.
        """
        if dim == self.goal:
            return "goal"
        tops = {
            u for u in (self.downstream([dim]) | {dim})
            if self.goal in self.graph.get(u, set())
        }
        return "|".join(sorted(tops)) if tops else "none"

    def player_branch_tag(self, pid: str):
        """pid ("A:<action>" / "D:<action>") -> "A@<branch>" / "D@<branch>".

        Returns None for the status quo, the no-op defense, and unknown pids,
        which therefore never touch branch-level memory.
        """
        cached = self._branch_tags.get(pid, "?")
        if cached != "?":
            return cached
        tag = None
        if pid.startswith("A:") and pid[2:] in self.attacks:
            tag = "A@" + self.branch_key(str(self.attacks[pid[2:]]["effect"]))
        elif pid.startswith("D:") and pid[2:] in self.defenses:
            tag = "D@" + self.branch_key(str(self.defenses[pid[2:]]["effect"]))
        self._branch_tags[pid] = tag
        return tag

    def initial_state(self) -> Dict[str, int]:
        s = {d: 0 for d in self.dims}
        for a in self.initial_attributes:
            a = str(a)
            if a in s:
                s[a] = 1
        return s

    def vector(self, state: Mapping[str, int]) -> List[float]:
        # PANACEA value 2 = blocked. In the compromise vector it is safe/inactive.
        return [1.0 if int(state.get(d, 0)) == 1 else 0.0 for d in self.dims]

    def risk(self, state_or_vec) -> float:
        vec = (
            self.vector(state_or_vec)
            if isinstance(state_or_vec, dict)
            else list(state_or_vec)
        )
        den = sum(self.weights) or 1.0
        return sum(
            w * clamp01(v) for w, v in zip(self.weights, vec)
        ) / den

    def downstream(self, starts: Iterable[str]) -> Set[str]:
        seen: Set[str] = set()
        q = deque(str(x) for x in starts)
        while q:
            u = q.popleft()
            if u in seen:
                continue
            seen.add(u)
            for v in self.graph.get(u, set()):
                if v not in seen:
                    q.append(v)
        return seen

    def attack_enabled(
        self, action: str, state: Mapping[str, int], executed: Set[str]
    ) -> bool:
        if action in executed:
            return False
        info = self.attacks[action]
        effect = str(info["effect"])
        if int(state.get(self.goal, 0)) == 1:
            return False
        if int(state.get(effect, 0)) != 0:
            return False
        pre = [str(x) for x in info["preconditions"]]
        if not pre:
            return True
        vals = [int(state.get(p, 0)) == 1 for p in pre]
        return (
            any(vals)
            if str(info["refinement"]) == "disjunctive"
            else all(vals)
        )

    def enabled_attacks(
        self, state: Mapping[str, int], executed: Set[str]
    ) -> List[str]:
        return [a for a in self.attacks if self.attack_enabled(a, state, executed)]

    def defense_enabled(self, action: str, state: Mapping[str, int]) -> bool:
        if int(state.get(self.goal, 0)) == 1:
            return False
        info = self.defenses[action]
        effect = str(info["effect"])
        if effect in state and int(state.get(effect, 0)) == 2:
            return False
        pre = [str(x) for x in info["preconditions"]]
        if not pre:
            return True
        vals = [int(state.get(p, 0)) == 1 for p in pre]
        return (
            any(vals)
            if str(info["refinement"]) == "disjunctive"
            else all(vals)
        )

    def enabled_defenses(self, state: Mapping[str, int]) -> List[str]:
        return [d for d in self.defenses if self.defense_enabled(d, state)]

    def apply_attack(self, state: Mapping[str, int], action: str) -> Dict[str, int]:
        s = dict(state)
        effect = str(self.attacks[action]["effect"])
        s[effect] = 1
        return s

    def apply_defense(self, state: Mapping[str, int], action: str) -> Dict[str, int]:
        s = dict(state)
        effect = str(self.defenses[action]["effect"])
        if effect in s:
            s[effect] = 2
        return s


class VectorPreana:
    def __init__(
        self,
        model: Model,
        max_rounds: int = 40,
        risk_mode: str = "dynamic",
        learning_mode: str = "on",
        defender_abstain: str = "off",
        defender_policy: str = "irena",
    ):
        self.m = model
        self.max_rounds = max_rounds
        self.eu_threshold = 0.015
        self.base_move = 0.35
        self.max_move = 0.75
        self.beta = 0.01
        self.risk_mode = risk_mode
        self.learning_mode = learning_mode
        self.defender_abstain = defender_abstain
        self.defender_policy = defender_policy

    def distance(self, p: Player, y: Sequence[float]) -> float:
        return sum(
            self.m.weights[k]
            * p.salience[k]
            * p.capability[k]
            * abs(p.goal[k] - y[k])
            for k in range(len(y))
        )

    def dmax(self, p: Player) -> float:
        return max(
            EPS,
            sum(
                self.m.weights[k] * p.salience[k] * p.capability[k]
                for k in range(len(p.goal))
            ),
        )

    def utility(self, p: Player, y: Sequence[float], r_ij: float = 1.0) -> float:
        ratio = min(1.0, self.distance(p, y) / self.dmax(p))
        return 1.0 - math.pow(ratio, max(0.1, r_ij))

    def support(
        self,
        players: List[Player],
        proposals: List[List[float]],
        j: int,
        k: int,
    ) -> float:
        result = 0.0
        for p in players:
            result += (
                self.distance(p, proposals[k]) - self.distance(p, proposals[j])
            ) / self.dmax(p)
        return result

    def medoid(self, players: List[Player], proposals: List[List[float]]) -> int:
        scores = []
        for j in range(len(players)):
            score = 0.0
            for k in range(len(players)):
                if j != k:
                    score += max(0.0, self.support(players, proposals, j, k))
            scores.append(score)
        return max(range(len(scores)), key=lambda i: (scores[i], -i))

    def pair_success_probability(
        self,
        players: List[Player],
        proposals: List[List[float]],
        i: int,
        j: int,
    ) -> float:
        # Per-side aggregation: the support each player gives to i over j is
        # clipped at zero BEFORE summing, so the two coalition strengths are
        # independent quantities and the probability is graded by their
        # relative size. Clipping after aggregation -- max(0, V^{ij}) --
        # collapses to {0, 1} because V^{ji} = -V^{ij} by construction.
        pro_i = 0.0
        pro_j = 0.0
        for p in players:
            t = (
                self.distance(p, proposals[j]) - self.distance(p, proposals[i])
            ) / self.dmax(p)
            if t > 0.0:
                pro_i += t
            elif t < 0.0:
                pro_j -= t
        if pro_i + pro_j <= 0.0:
            return 0.5  # perfect tie: neither side has any backing
        return pro_i / (pro_i + pro_j + EPS)

    def expected_utility_with_r(
        self,
        players: List[Player],
        proposals: List[List[float]],
        i: int,
        j: int,
        r_ij: float,
    ) -> Tuple[float, float]:
        p_success = self.pair_success_probability(players, proposals, i, j)
        us = self.utility(players[i], proposals[i], r_ij) - self.utility(
            players[i], proposals[j], r_ij
        )
        conflict = 0.02 * (players[i].cost + players[j].cost) / (
            1.0 + players[i].cost + players[j].cost
        )
        uf = -us - conflict
        eu = p_success * us + (1.0 - p_success) * uf
        return eu, p_success

    def compute_player_risk_baselines(
        self,
        players: List[Player],
        proposals: List[List[float]],
    ) -> Tuple[Dict[int, float], Dict[int, float], Dict[int, List[float]]]:
        """Compute PREANA-style r_i from neutral expected utilities.

        PREANA first evaluates expected utilities with r=1 and then computes
        a player-level risk index from the actual, maximum-feasible, and
        minimum-feasible expected utilities:

          R_i = (2*sum(EU_ij) - sum(EU_ij^max) - sum(EU_ij^min))
                / (sum(EU_ij^max) - sum(EU_ij^min)).

        We preserve that normalization. For our reduced EU expression, the
        pairwise neutral EU is affine in U_s:

          EU = (2P-1) U_s - (1-P)K,   U_s in [-1,1].

        Therefore the local feasible pairwise extrema are available in closed
        form. This avoids using the observed min/max across opponents and also
        works when a player has only one opponent. Finally PREANA's transform

          r_i = (1 - R_i/3)/(1 + R_i/3)

        maps the local risk index to the [0.5,2] risk/security coefficient.
        """
        baselines: Dict[int, float] = {}
        risk_indices: Dict[int, float] = {}
        neutral_eus: Dict[int, List[float]] = {}

        for i in range(len(players)):
            eus: List[float] = []
            eu_max_values: List[float] = []
            eu_min_values: List[float] = []

            for j in range(len(players)):
                if i == j:
                    continue

                eu, p_success = self.expected_utility_with_r(
                    players, proposals, i, j, r_ij=1.0
                )
                eus.append(eu)

                conflict = 0.02 * (players[i].cost + players[j].cost) / (
                    1.0 + players[i].cost + players[j].cost
                )
                coeff = 2.0 * p_success - 1.0
                center = -(1.0 - p_success) * conflict
                radius = abs(coeff)  # because U_s is bounded in [-1,1]
                eu_max_values.append(center + radius)
                eu_min_values.append(center - radius)

            neutral_eus[i] = eus

            if self.risk_mode == "neutral" or not eus:
                risk_indices[i] = 0.0
                baselines[i] = 1.0
                continue

            sum_actual = sum(eus)
            sum_max = sum(eu_max_values)
            sum_min = sum(eu_min_values)
            span = sum_max - sum_min
            if span <= EPS:
                risk_indices[i] = 0.0
                baselines[i] = 1.0
                continue

            # PREANA Eq. (5), applied to our reduced vector expected utility.
            R_i = clip(
                (2.0 * sum_actual - sum_max - sum_min) / span,
                -1.0,
                1.0,
            )
            denom = 1.0 + R_i / 3.0
            r_i = (1.0 - R_i / 3.0) / max(EPS, denom)
            risk_indices[i] = R_i
            baselines[i] = clip(r_i, R_MIN, R_MAX)

        return baselines, risk_indices, neutral_eus

    def effective_r_matrix(
        self,
        players: List[Player],
        proposals: List[List[float]],
        memory: RelationalMemory,
        local_delta: Mapping[Tuple[int, int], float],
    ) -> Tuple[Dict[Tuple[int, int], float], Dict[int, float], Dict[int, float]]:
        baselines, risk_indices, _ = self.compute_player_risk_baselines(
            players, proposals
        )
        result: Dict[Tuple[int, int], float] = {}
        for i in range(len(players)):
            for j in range(len(players)):
                if i == j:
                    continue
                persistent = (
                    memory.get_delta(players[i].pid, players[j].pid)
                    if self.learning_mode == "on"
                    else 0.0
                )
                temporary = (
                    local_delta.get((i, j), 0.0)
                    if self.learning_mode == "on"
                    else 0.0
                )
                result[(i, j)] = clip(
                    baselines[i] + persistent + temporary,
                    R_MIN,
                    R_MAX,
                )
        return result, baselines, risk_indices

    def run(
        self,
        players: List[Player],
        memory: RelationalMemory,
    ) -> Tuple[List[float], int, str, dict]:
        if not players:
            return [0.0] * len(self.m.dims), 0, "none", {
                "risk_mode": self.risk_mode,
                "baseline_mean": 1.0,
                "baseline_min": 1.0,
                "baseline_max": 1.0,
            }

        proposals = [list(p.proposal) for p in players]
        # The initial proposals are the exact post-states of executable
        # actions (or the current state, for the status quo). Negotiation
        # moves the working copies; the medoid is RESOLVED back to the
        # initial proposal of the winning player so the predicted outcome is
        # always a reachable configuration, never an interpolated vector.
        initial_proposals = [list(p.proposal) for p in players]
        local_learn = {
            (i, j): 0.0
            for i in range(len(players))
            for j in range(len(players))
            if i != j
        }
        local_delta = dict(local_learn)
        rounds = 0
        baseline_samples: List[float] = []
        last_baselines: Dict[int, float] = {}
        last_R: Dict[int, float] = {}
        last_effective: Dict[Tuple[int, int], float] = {}

        for round_idx in range(self.max_rounds):
            rounds = round_idx + 1
            effective_r, baselines, risk_indices = self.effective_r_matrix(
                players, proposals, memory, local_delta
            )
            baseline_samples.extend(baselines.values())
            last_baselines = baselines
            last_R = risk_indices
            last_effective = effective_r

            moves = {}
            for j in range(len(players)):
                best = None
                best_eu = self.eu_threshold
                for i in range(len(players)):
                    if i == j:
                        continue
                    eu, ps = self.expected_utility_with_r(
                        players,
                        proposals,
                        i,
                        j,
                        effective_r[(i, j)],
                    )
                    if eu > best_eu:
                        best = (i, eu, ps)
                        best_eu = eu
                if best is not None:
                    moves[j] = best

            nxt = [list(v) for v in proposals]
            max_delta = 0.0
            for j, (i, eu, ps) in moves.items():
                r_ij = effective_r[(i, j)]
                rate = min(self.max_move, self.base_move * max(0.0, ps) * r_ij)
                old = proposals[j]
                target = proposals[i]
                upd = [
                    clamp01((1.0 - rate) * old[k] + rate * target[k])
                    for k in range(len(old))
                ]
                move_size = l1(old, upd)
                if move_size > 0:
                    nxt[j] = upd
                    max_delta = max(max_delta, move_size)

                    # Temporary, within-negotiation adaptation only.
                    # Disabled in LEARNING_MODE=off so the attacker-policy
                    # ablation changes only the policy, not relational memory.
                    if self.learning_mode == "on":
                        local_learn[(i, j)] += move_size
                        local_learn[(j, i)] -= 0.5 * move_size
                        sal = mean(players[i].salience) or 1.0
                        local_delta[(i, j)] = clip(
                            local_delta[(i, j)]
                            + self.beta * sal * local_learn[(i, j)],
                            DELTA_MIN,
                            DELTA_MAX,
                        )
                        local_delta[(j, i)] = clip(
                            local_delta[(j, i)]
                            + self.beta * sal * local_learn[(j, i)],
                            DELTA_MIN,
                            DELTA_MAX,
                        )

            proposals = nxt
            if max_delta < 1e-4:
                break

        idx = self.medoid(players, proposals)
        diag = {
            "risk_mode": self.risk_mode,
            "learning_mode": self.learning_mode,
            "baseline_mean": mean(baseline_samples) if baseline_samples else 1.0,
            "baseline_min": min(baseline_samples) if baseline_samples else 1.0,
            "baseline_max": max(baseline_samples) if baseline_samples else 1.0,
            "last_baselines": {
                players[i].pid: last_baselines.get(i, 1.0)
                for i in range(len(players))
            },
            "last_risk_indices": {
                players[i].pid: last_R.get(i, 0.0)
                for i in range(len(players))
            },
            "last_effective_r": {
                f"{players[i].pid} -> {players[j].pid}": value
                for (i, j), value in last_effective.items()
            },
        }
        return initial_proposals[idx], rounds, players[idx].pid, diag

    def _status_quo_player(self, state: Mapping[str, int]) -> Player:
        current = self.m.vector(state)
        return Player(
            "status_quo",
            "status_quo",
            current,
            current,
            [0.20] * len(current),
            [0.20] * len(current),
            0.0,
        )

    def _attack_player(self, base_state: Mapping[str, int], attack: str) -> Player:
        after = self.m.apply_attack(base_state, attack)
        proposal = self.m.vector(after)
        effect = str(self.m.attacks[attack]["effect"])
        affected = self.m.downstream([effect]) | {effect, self.m.goal}
        goal_state = dict(base_state)
        for dim in affected:
            if dim in goal_state:
                goal_state[dim] = 1
        goal = self.m.vector(goal_state)
        capability = []
        salience = []
        for dim in self.m.dims:
            affect = 1.0 if dim in affected else 0.15
            capability.append(clamp01(affect))
            salience.append(
                clamp01(affect * (0.40 + 0.60 * self.m.root_closeness(dim)))
            )
        return Player(
            f"A:{attack}",
            "attack",
            proposal,
            goal,
            capability,
            salience,
            self.m.attack_cost[attack],
        )

    def _defense_player(self, base_state: Mapping[str, int], defense: str) -> Player:
        after = self.m.apply_defense(base_state, defense)
        proposal = self.m.vector(after)
        effect = str(self.m.defenses[defense]["effect"])
        affected = self.m.downstream([effect]) | {effect}
        capability = []
        salience = []
        for dim in self.m.dims:
            affect = 1.0 if dim in affected else 0.20
            capability.append(clamp01(affect))
            salience.append(
                clamp01(affect * (0.45 + 0.55 * self.m.root_closeness(dim)))
            )
        return Player(
            f"D:{defense}",
            "defense",
            proposal,
            [0.0] * len(proposal),
            capability,
            salience,
            self.m.defense_cost[defense],
        )

    def build_players_for_defense(
        self,
        state: Mapping[str, int],
        defense: str,
        executed: Set[str],
    ) -> Tuple[List[Player], Dict[str, int]]:
        defended = self.m.apply_defense(state, defense)
        players = [
            self._status_quo_player(state),
            self._defense_player(state, defense),
        ]
        for attack in self.m.enabled_attacks(defended, executed):
            players.append(self._attack_player(defended, attack))
        return players, defended

    def build_players_for_attack(
        self,
        state: Mapping[str, int],
        attack: str,
        executed: Set[str],
    ) -> Tuple[List[Player], Dict[str, int]]:
        attacked = self.m.apply_attack(state, attack)
        players = [
            self._status_quo_player(state),
            self._attack_player(state, attack),
        ]
        for defense in self.m.enabled_defenses(attacked):
            players.append(self._defense_player(attacked, defense))
        return players, attacked

    def _noop_defense_player(self, state: Mapping[str, int]) -> Player:
        """The defender as a participant that executes no action.

        Structurally this is a defense player whose effect function is the
        identity: it proposes the current state and still prefers z_safe, but
        it influences no dimension. Including it is what makes the abstention
        game comparable with a defense-candidate game.

        Without it the two games would contain a different number of players
        advocating safety, and a response with no effect on the state would
        still shift the medoid simply by adding a voter on the defender's
        side. That would make abstention lose on games it should win.
        """
        proposal = self.m.vector(state)
        capability = [0.20] * len(proposal)
        salience = [
            clamp01(0.20 * (0.45 + 0.55 * self.m.root_closeness(dim)))
            for dim in self.m.dims
        ]
        return Player(
            "D:__noop__",
            "defense",
            proposal,
            [0.0] * len(proposal),
            capability,
            salience,
            0.0,
        )

    def _abstain_record(
        self,
        state: Mapping[str, int],
        executed: Set[str],
        memory: RelationalMemory,
    ) -> dict:
        """Score the no-op response symmetrically with the real defenses.

        The candidate game is the one that would arise if no response were
        executed: the status quo, the passive defender, and the attacks
        currently enabled. Its medoid is evaluated exactly as for a defense
        candidate, and the cost term is zero by definition. Without this
        candidate the defender must buy a response on every turn on which any
        response is enabled, which is not the constraint PANACEA's globally
        solved policy operates under.
        """
        remaining_attacks = self.m.enabled_attacks(state, executed)
        players = [
            self._status_quo_player(state),
            self._noop_defense_player(state),
        ]
        for attack in remaining_attacks:
            players.append(self._attack_player(state, attack))

        if not remaining_attacks:
            final_vec = self.m.vector(state)
            rounds = 0
            medoid = "status_quo"
            risk_diag = {
                "risk_mode": self.risk_mode,
                "learning_mode": self.learning_mode,
                "baseline_mean": 1.0,
                "baseline_min": 1.0,
                "baseline_max": 1.0,
            }
        else:
            final_vec, rounds, medoid, risk_diag = self.run(players, memory)

        residual_risk = self.m.risk(final_vec)
        return {
            "action": None,
            "score": residual_risk,
            "predicted_risk": residual_risk,
            "cost": 0.0,
            "rounds": rounds,
            "medoid": medoid,
            "remaining_attacks": len(remaining_attacks),
            "risk_diagnostics": risk_diag,
        }

    def choose_defense_rule(self, state: Mapping[str, int], policy: str):
        """R-ADT LC and LD baselines, as defined in the PANACEA evaluation.

        LC picks the cheapest response enabled in the current state.

        LD picks, among the responses whose target dimension CURRENTLY HOLDS,
        the one closest to the attacker root, ties broken by cost. `Model.dist`
        holds the BFS distance of every dimension to the goal, so "closest to
        the root" is the smallest distance; a dimension that cannot reach the
        goal at all has infinite distance and is therefore considered last.

        The restriction to dimensions that hold is what makes this the LD of
        the PANACEA evaluation rather than a rule of our own. Ranking every
        enabled response instead sends the defender to guard the dimension
        nearest the goal -- usually the attacker's own objective, still at 0 --
        while the attack advances down another branch. On the realistic
        instance that difference decides the episode: restricted, LD reproduces
        the reference trace move for move and wins at 330; unrestricted, it
        opens with `changeCredentials` and loses.

        Neither rule plays a local game: no negotiation runs, `rounds` is zero,
        and the reported planning time is the cost of the lookup itself. Both
        orderings are fully tie-broken (by the other criterion, then by action
        name) so the choice is deterministic and reproducible.
        """
        candidates = self.m.enabled_defenses(state)
        if not candidates:
            return None, {"candidates": [], "rounds": 0, "abstained": False}

        BIG = 10 ** 9

        def root_distance(d: str) -> int:
            effect = str(self.m.defenses[d]["effect"])
            return int(self.m.dist.get(effect, BIG))

        if policy == "ld":
            # A response removes a condition; one whose target is still 0
            # removes nothing. Keeping those in the ranking is what sent the
            # rule to `changeCredentials` on the first turn of the realistic
            # instance. The fallback matters: the defender cannot pass, so when
            # nothing active is defensible the full candidate set is kept and
            # the turn is spent on the best of a bad set rather than skipped.
            active = [
                d for d in candidates
                if int(state.get(str(self.m.defenses[d]["effect"]), 0)) == 1
            ]
            if active:
                candidates = active

        if policy == "lc":
            key = lambda d: (self.m.defense_cost[d], root_distance(d), d)
        elif policy == "ld":
            key = lambda d: (root_distance(d), self.m.defense_cost[d], d)
        else:
            raise ValueError(f"unknown defender policy: {policy}")

        ordered = sorted(candidates, key=key)
        best = ordered[0]
        records = [
            {
                "action": d,
                "score": float(key(d)[0]),
                "predicted_risk": float("nan"),
                "cost": self.m.defense_cost[d],
                "rounds": 0,
                "medoid": f"D:{d}",
                "root_distance": root_distance(d),
                "remaining_attacks": None,
            }
            for d in ordered
        ]
        return best, {
            "score": records[0]["score"],
            "residual_risk": float("nan"),
            "rounds": 0,
            "medoid": f"D:{best}",
            "abstained": False,
            "policy": policy,
            "risk_diagnostics": {},
            "candidates": records,
        }

    def choose_defense(
        self,
        state: Mapping[str, int],
        executed: Set[str],
        cost_weight: float,
        memory: RelationalMemory,
    ):
        candidates = self.m.enabled_defenses(state)
        if not candidates:
            return None, {"candidates": [], "rounds": 0, "abstained": False}

        records = []
        if self.defender_abstain == "on":
            records.append(self._abstain_record(state, executed, memory))

        for defense in candidates:
            players, defended = self.build_players_for_defense(
                state, defense, executed
            )
            remaining_attacks = self.m.enabled_attacks(defended, executed)
            if not remaining_attacks:
                final_vec = self.m.vector(defended)
                rounds = 0
                medoid = f"D:{defense}"
                risk_diag = {
                    "risk_mode": self.risk_mode,
                    "baseline_mean": 1.0,
                    "baseline_min": 1.0,
                    "baseline_max": 1.0,
                }
            else:
                final_vec, rounds, medoid, risk_diag = self.run(players, memory)
            residual_risk = self.m.risk(final_vec)
            score = residual_risk + cost_weight * self.m.defense_cost[defense]
            records.append(
                {
                    "action": defense,
                    "score": score,
                    "predicted_risk": residual_risk,
                    "cost": self.m.defense_cost[defense],
                    "rounds": rounds,
                    "medoid": medoid,
                    "remaining_attacks": len(remaining_attacks),
                    "risk_diagnostics": risk_diag,
                }
            )

        # The abstention record carries action=None, hence the `or ""` guard on
        # the tie-breaking key. Ties at equal score are resolved in favour of
        # the cheaper option, so abstention wins them.
        records.sort(key=lambda r: (r["score"], r["cost"], r["action"] or ""))
        best = records[0]
        return best["action"], {
            "score": best["score"],
            "residual_risk": best["predicted_risk"],
            "rounds": best["rounds"],
            "medoid": best["medoid"],
            "abstained": best["action"] is None,
            "risk_diagnostics": best["risk_diagnostics"],
            "candidates": records,
        }

    def choose_attack_greedy(
        self,
        state: Mapping[str, int],
        executed: Set[str],
        cost_weight: float,
    ):
        """Earlier prototype attacker: re-plan each turn, but one-step greedy.

        The attacker is still state-adaptive because this function is called
        after every realized defense. It does not simulate the defenses that
        each candidate attack would enable. This is the intended diagnostic
        baseline against the counterfactual adaptive attacker.
        """
        candidates = self.m.enabled_attacks(state, executed)
        if not candidates:
            return None, {
                "policy": "greedy",
                "candidates": [],
                "rounds": 0,
                "risk_diagnostics": {
                    "risk_mode": self.risk_mode,
                    "learning_mode": self.learning_mode,
                    "baseline_mean": 1.0,
                    "baseline_min": 1.0,
                    "baseline_max": 1.0,
                },
            }

        before = self.m.risk(state)
        records = []
        for attack in candidates:
            after = self.m.apply_attack(state, attack)
            after_risk = self.m.risk(after)
            gain = after_risk - before
            effect = str(self.m.attacks[attack]["effect"])
            reaches_goal = int(after.get(self.m.goal, 0)) == 1
            d = self.m.dist.get(effect, 10**9)
            closeness = (
                0.0
                if d >= 10**9
                else 1.0 - d / float(self.m.max_dist + 1)
            )
            score = (
                10.0 * (1.0 if reaches_goal else 0.0)
                + 2.0 * closeness
                + gain
                - cost_weight * self.m.attack_cost[attack]
            )
            records.append(
                {
                    "action": attack,
                    "score": score,
                    "immediate_risk": after_risk,
                    "risk_gain": gain,
                    "root_closeness": closeness,
                    "cost": self.m.attack_cost[attack],
                    "reaches_goal": reaches_goal,
                    "rounds": 0,
                }
            )

        records.sort(key=lambda r: (-r["score"], r["cost"], r["action"]))
        best = records[0]
        return best["action"], {
            "policy": "greedy",
            "score": best["score"],
            "predicted_risk": best["immediate_risk"],
            "rounds": 0,
            "medoid": None,
            "risk_diagnostics": {
                "risk_mode": self.risk_mode,
                "learning_mode": self.learning_mode,
                "baseline_mean": 1.0,
                "baseline_min": 1.0,
                "baseline_max": 1.0,
            },
            "candidates": records,
        }

    def choose_attack(
        self,
        state: Mapping[str, int],
        executed: Set[str],
        cost_weight: float,
        memory: RelationalMemory,
        goal_bonus: float = 1.0,
    ):
        candidates = self.m.enabled_attacks(state, executed)
        if not candidates:
            return None, {"candidates": [], "rounds": 0}

        records = []
        for attack in candidates:
            players, attacked = self.build_players_for_attack(state, attack, executed)
            reaches_goal = int(attacked.get(self.m.goal, 0)) == 1
            possible_defenses = self.m.enabled_defenses(attacked)
            if reaches_goal or not possible_defenses:
                final_vec = self.m.vector(attacked)
                rounds = 0
                medoid = f"A:{attack}"
                risk_diag = {
                    "risk_mode": self.risk_mode,
                    "baseline_mean": 1.0,
                    "baseline_min": 1.0,
                    "baseline_max": 1.0,
                }
            else:
                final_vec, rounds, medoid, risk_diag = self.run(players, memory)

            predicted_risk = self.m.risk(final_vec)
            score = (
                predicted_risk
                + (goal_bonus if reaches_goal else 0.0)
                - cost_weight * self.m.attack_cost[attack]
            )
            records.append(
                {
                    "action": attack,
                    "score": score,
                    "predicted_risk": predicted_risk,
                    "cost": self.m.attack_cost[attack],
                    "rounds": rounds,
                    "medoid": medoid,
                    "reaches_goal": reaches_goal,
                    "possible_defenses": len(possible_defenses),
                    "risk_diagnostics": risk_diag,
                }
            )

        # Attacker maximizes utility; deterministic tie-breaking prefers lower cost.
        records.sort(key=lambda r: (-r["score"], r["cost"], r["action"]))
        best = records[0]
        return best["action"], {
            "policy": "adaptive",
            "score": best["score"],
            "predicted_risk": best["predicted_risk"],
            "rounds": best["rounds"],
            "medoid": best["medoid"],
            "risk_diagnostics": best["risk_diagnostics"],
            "candidates": records,
        }


def run_episode(
    m: Model,
    defense_cost_weight: float,
    attack_cost_weight: float,
    max_steps: int,
    max_rounds: int,
    memory_risk_weight: float,
    attack_goal_bonus: float,
    risk_mode: str,
    attacker_policy: str,
    learning_mode: str,
    turn_order: str,
    defender_abstain: str = "off",
    defender_policy: str = "irena",
):
    vp = VectorPreana(
        m,
        max_rounds=max_rounds,
        risk_mode=risk_mode,
        learning_mode=learning_mode,
        defender_abstain=defender_abstain,
        defender_policy=defender_policy,
    )
    memory = RelationalMemory(beta=vp.beta)
    memory.tag_fn = m.player_branch_tag
    state = m.initial_state()
    executed: Set[str] = set()

    attacker_cost = 0.0
    defender_cost = 0.0
    trace = []
    attacker_planning_time = 0.0
    defender_planning_time = 0.0
    attacker_rounds = 0
    defender_rounds = 0
    defender_abstentions = 0
    path_switches = 0
    previous_attack_effect: Optional[str] = None
    winner = "timeout"
    selected_baseline_samples: List[float] = []

    def plan_attack():
        nonlocal attacker_planning_time, attacker_rounds
        t0 = time.perf_counter()
        if attacker_policy == "greedy":
            action, meta = vp.choose_attack_greedy(
                state, executed, attack_cost_weight
            )
        else:
            action, meta = vp.choose_attack(
                state,
                executed,
                attack_cost_weight,
                memory,
                goal_bonus=attack_goal_bonus,
            )
        attacker_planning_time += time.perf_counter() - t0
        attacker_rounds += int(meta.get("rounds", 0))
        diag = meta.get("risk_diagnostics", {})
        if "baseline_mean" in diag:
            selected_baseline_samples.append(float(diag["baseline_mean"]))
        return action, meta

    def plan_defense():
        nonlocal defender_planning_time, defender_rounds
        t0 = time.perf_counter()
        if defender_policy == "irena":
            action, meta = vp.choose_defense(
                state, executed, defense_cost_weight, memory
            )
        else:
            action, meta = vp.choose_defense_rule(state, defender_policy)
        defender_planning_time += time.perf_counter() - t0
        defender_rounds += int(meta.get("rounds", 0))
        diag = meta.get("risk_diagnostics", {})
        if "baseline_mean" in diag:
            selected_baseline_samples.append(float(diag["baseline_mean"]))
        return action, meta

    if turn_order == "attacker_first":
        for step in range(max_steps):
            attack, attack_meta = plan_attack()
            if attack is None:
                winner = "defender"
                break

            attack_preconditions = [str(x) for x in m.attacks[attack]["preconditions"]]
            if (
                previous_attack_effect is not None
                and previous_attack_effect not in attack_preconditions
            ):
                path_switches += 1

            risk_before_attack = m.risk(state)
            attacker_cost += m.attack_cost[attack]
            executed.add(attack)
            state = m.apply_attack(state, attack)
            risk_after_attack = m.risk(state)
            previous_attack_effect = str(m.attacks[attack]["effect"])

            trace.append(
                {
                    "step": step,
                    "player": "attacker",
                    "action": attack,
                    "cost": m.attack_cost[attack],
                    "state": dict(state),
                    "risk_before": risk_before_attack,
                    "risk_after": risk_after_attack,
                    "preana": attack_meta,
                    "memory_before_response": memory.snapshot(),
                }
            )

            if int(state.get(m.goal, 0)) == 1:
                winner = "attacker"
                break

            enabled_after_attack = len(m.enabled_attacks(state, executed))

            defense, defense_meta = plan_defense()
            if defense is None:
                abstained = bool(defense_meta.get("abstained"))
                if abstained:
                    defender_abstentions += 1
                trace.append(
                    {
                        "step": step,
                        "player": "defender",
                        "action": None,
                        "cost": 0.0,
                        "state": dict(state),
                        "risk_before": risk_after_attack,
                        "risk_after": risk_after_attack,
                        "preana": defense_meta,
                        "note": (
                            "defender abstained"
                            if abstained
                            else "no enabled defense"
                        ),
                    }
                )
                continue

            defender_cost += m.defense_cost[defense]
            state_before_defense = dict(state)
            state = m.apply_defense(state, defense)
            risk_after_defense = m.risk(state)
            enabled_after_defense = len(m.enabled_attacks(state, executed))

            if learning_mode == "on":
                memory.observe_encounter(
                    m,
                    attack,
                    defense,
                    risk_before_attack,
                    risk_after_attack,
                    risk_after_defense,
                    enabled_after_attack,
                    enabled_after_defense,
                    memory_risk_weight,
                )

            trace.append(
                {
                    "step": step,
                    "player": "defender",
                    "action": defense,
                    "cost": m.defense_cost[defense],
                    "state_before": state_before_defense,
                    "state": dict(state),
                    "risk_before": risk_after_attack,
                    "risk_after": risk_after_defense,
                    "preana": defense_meta,
                    "memory_after_encounter": memory.snapshot(),
                }
            )

            if not m.enabled_attacks(state, executed):
                winner = "defender"
                break

    else:
        # Defender-first diagnostic. The defender receives one proactive move
        # before the first attack. Thereafter D/A/D/A alternation means that a
        # defense at the beginning of a cycle responds to the attack realized
        # at the end of the previous cycle. This lets the existing realized-
        # encounter learning rule remain well-defined without counterfactual
        # leakage. The very first proactive defense has no preceding attack and
        # therefore does not update relational memory.
        pending_attack = None

        for step in range(max_steps):
            risk_before_defense = m.risk(state)
            defense, defense_meta = plan_defense()

            if defense is not None:
                defender_cost += m.defense_cost[defense]
                state_before_defense = dict(state)
                state = m.apply_defense(state, defense)
                risk_after_defense = m.risk(state)
                enabled_after_defense = len(m.enabled_attacks(state, executed))

                if learning_mode == "on" and pending_attack is not None:
                    memory.observe_encounter(
                        m,
                        pending_attack["attack"],
                        defense,
                        pending_attack["risk_before_attack"],
                        pending_attack["risk_after_attack"],
                        risk_after_defense,
                        pending_attack["enabled_after_attack"],
                        enabled_after_defense,
                        memory_risk_weight,
                    )

                trace.append(
                    {
                        "step": step,
                        "player": "defender",
                        "action": defense,
                        "cost": m.defense_cost[defense],
                        "state_before": state_before_defense,
                        "state": dict(state),
                        "risk_before": risk_before_defense,
                        "risk_after": risk_after_defense,
                        "preana": defense_meta,
                        "turn_order": "defender_first",
                        "response_to_attack": (
                            pending_attack["attack"] if pending_attack is not None else None
                        ),
                        "note": (
                            "initial proactive defense"
                            if pending_attack is None
                            else "response to previous realized attack"
                        ),
                        "memory_after_encounter": memory.snapshot(),
                    }
                )
            else:
                abstained = bool(defense_meta.get("abstained"))
                if abstained:
                    defender_abstentions += 1
                trace.append(
                    {
                        "step": step,
                        "player": "defender",
                        "action": None,
                        "cost": 0.0,
                        "state": dict(state),
                        "risk_before": risk_before_defense,
                        "risk_after": risk_before_defense,
                        "preana": defense_meta,
                        "turn_order": "defender_first",
                        "response_to_attack": (
                            pending_attack["attack"] if pending_attack is not None else None
                        ),
                        "note": (
                            "defender abstained"
                            if abstained
                            else "no enabled defense"
                        ),
                    }
                )

            # Any previous attack has now either received a real defense or no
            # defense was available. It must not leak into the next encounter.
            pending_attack = None

            if not m.enabled_attacks(state, executed):
                winner = "defender"
                break

            attack, attack_meta = plan_attack()
            if attack is None:
                winner = "defender"
                break

            attack_preconditions = [str(x) for x in m.attacks[attack]["preconditions"]]
            if (
                previous_attack_effect is not None
                and previous_attack_effect not in attack_preconditions
            ):
                path_switches += 1

            risk_before_attack = m.risk(state)
            attacker_cost += m.attack_cost[attack]
            executed.add(attack)
            state = m.apply_attack(state, attack)
            risk_after_attack = m.risk(state)
            previous_attack_effect = str(m.attacks[attack]["effect"])
            enabled_after_attack = len(m.enabled_attacks(state, executed))

            trace.append(
                {
                    "step": step,
                    "player": "attacker",
                    "action": attack,
                    "cost": m.attack_cost[attack],
                    "state": dict(state),
                    "risk_before": risk_before_attack,
                    "risk_after": risk_after_attack,
                    "preana": attack_meta,
                    "turn_order": "defender_first",
                    "memory_before_next_response": memory.snapshot(),
                }
            )

            if int(state.get(m.goal, 0)) == 1:
                winner = "attacker"
                break

            if enabled_after_attack == 0:
                winner = "defender"
                break

            pending_attack = {
                "attack": attack,
                "risk_before_attack": risk_before_attack,
                "risk_after_attack": risk_after_attack,
                "enabled_after_attack": enabled_after_attack,
            }

    total_planning_time = attacker_planning_time + defender_planning_time
    return {
        "winner": winner,
        "attacker_cost": attacker_cost,
        "defender_cost": defender_cost,
        "actions": sum(1 for x in trace if x["action"] is not None),
        "attacker_steps": sum(
            1
            for x in trace
            if x["player"] == "attacker" and x["action"] is not None
        ),
        "defender_steps": sum(
            1
            for x in trace
            if x["player"] == "defender" and x["action"] is not None
        ),
        "attack_path_switches": path_switches,
        "final_risk": m.risk(state),
        "attacker_planning_time_s": attacker_planning_time,
        "defender_planning_time_s": defender_planning_time,
        "planning_time_s": total_planning_time,
        "attacker_preana_rounds": attacker_rounds,
        "defender_preana_rounds": defender_rounds,
        "preana_rounds": attacker_rounds + defender_rounds,
        "memory_pairs": len(memory.delta),
        "attacker_policy": attacker_policy,
        "learning_mode": learning_mode,
        "turn_order": turn_order,
        "defender_abstain": defender_abstain,
        "defender_abstentions": defender_abstentions,
        "selected_r_baseline_mean": (
            mean(selected_baseline_samples) if selected_baseline_samples else 1.0
        ),
        "selected_r_baseline_min": (
            min(selected_baseline_samples) if selected_baseline_samples else 1.0
        ),
        "selected_r_baseline_max": (
            max(selected_baseline_samples) if selected_baseline_samples else 1.0
        ),
        "trace": trace,
        "final_state": state,
        "memory": memory.snapshot(),
    }


def write_dot(path: str, m: Model, result: dict):
    def state_label(idx: int, state: Mapping[str, int]) -> str:
        vals = ",".join(str(int(state.get(d, 0))) for d in m.dims)
        return f"{idx}\\n({vals})"

    states = [m.initial_state()]
    actual_items = [x for x in result["trace"] if x.get("action") is not None]
    for item in actual_items:
        states.append(item["state"])

    with open(path, "w", encoding="utf-8") as f:
        f.write("digraph VectorPREANADynamicRisk {\n")
        f.write('  rankdir=LR;\n  node [shape=box,fontname="Helvetica"];\n')
        for i, st in enumerate(states):
            shape = "doublecircle" if i == len(states) - 1 else "box"
            f.write(f'  s{i} [shape={shape},label="{state_label(i, st)}"];\n')
        for i, item in enumerate(actual_items):
            prefix = "A" if item["player"] == "attacker" else "D"
            action = item["action"]
            cost = item["cost"]
            f.write(
                f'  s{i} -> s{i+1} [label="{prefix}:{action} / {cost:g}"];\n'
            )
        f.write(
            f'  winner [shape=plaintext,label="Winner: {result["winner"]}"];\n'
        )
        f.write(f'  s{len(states)-1} -> winner [style=dashed];\n')
        f.write("}\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tree", required=True)
    ap.add_argument("--package-version", required=True)
    ap.add_argument("--repo-root", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--repeats", type=int, default=10)
    ap.add_argument("--defense-cost-weight", type=float, default=0.0001)
    ap.add_argument("--attack-cost-weight", type=float, default=0.0001)
    ap.add_argument("--max-steps", type=int, default=60)
    ap.add_argument("--max-rounds", type=int, default=40)
    ap.add_argument("--memory-risk-weight", type=float, default=0.70)
    ap.add_argument("--attack-goal-bonus", type=float, default=1.0)
    ap.add_argument("--risk-mode", choices=["dynamic", "neutral"], default="dynamic")
    ap.add_argument("--attacker-policy", choices=["greedy", "adaptive"], default="adaptive")
    ap.add_argument("--learning-mode", choices=["on", "off"], default="on")
    ap.add_argument(
        "--turn-order",
        choices=["attacker_first", "defender_first"],
        default="attacker_first",
    )
    ap.add_argument("--defender-abstain", choices=["on", "off"], default="off")
    ap.add_argument("--defender-policy", choices=["irena", "lc", "ld"],
                    default="irena")
    args = ap.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)
    model = Model(args.tree, args.repo_root)

    runs = []
    for _ in range(max(1, args.repeats)):
        t0 = time.perf_counter()
        r = run_episode(
            model,
            args.defense_cost_weight,
            args.attack_cost_weight,
            args.max_steps,
            args.max_rounds,
            args.memory_risk_weight,
            args.attack_goal_bonus,
            args.risk_mode,
            args.attacker_policy,
            args.learning_mode,
            args.turn_order,
            args.defender_abstain,
            args.defender_policy,
        )
        r["total_time_s"] = time.perf_counter() - t0
        runs.append(r)

    r0 = runs[0]
    total_plan_times = [r["planning_time_s"] for r in runs]
    attacker_plan_times = [r["attacker_planning_time_s"] for r in runs]
    defender_plan_times = [r["defender_planning_time_s"] for r in runs]
    total_times = [r["total_time_s"] for r in runs]

    row = {
        "package_version": args.package_version,
        "tree": model.name,
        "xml_nodes": model.xml_nodes,
        "state_dimensions": len(model.dims),
        "attack_actions": len(model.attacks),
        "defense_actions": len(model.defenses),
        "winner": r0["winner"],
        "attacker_cost": r0["attacker_cost"],
        "defender_cost": r0["defender_cost"],
        "actions": r0["actions"],
        "attacker_steps": r0["attacker_steps"],
        "defender_steps": r0["defender_steps"],
        "attack_path_switches": r0["attack_path_switches"],
        "final_risk": r0["final_risk"],
        "parse_time_s": model.parse_time,
        # Compatibility column used by the earlier comparison script.
        "planning_time_median_s": statistics.median(total_plan_times),
        "planning_time_mean_s": statistics.mean(total_plan_times),
        "planning_time_stdev_s": (
            statistics.pstdev(total_plan_times) if len(total_plan_times) > 1 else 0.0
        ),
        "attacker_planning_time_median_s": statistics.median(attacker_plan_times),
        "attacker_planning_time_mean_s": statistics.mean(attacker_plan_times),
        "defender_planning_time_median_s": statistics.median(defender_plan_times),
        "defender_planning_time_mean_s": statistics.mean(defender_plan_times),
        "total_time_median_s": statistics.median(total_times),
        "total_time_mean_s": statistics.mean(total_times),
        "attacker_preana_rounds": r0["attacker_preana_rounds"],
        "defender_preana_rounds": r0["defender_preana_rounds"],
        "preana_rounds": r0["preana_rounds"],
        "memory_pairs": r0["memory_pairs"],
        "selected_r_baseline_mean": r0["selected_r_baseline_mean"],
        "selected_r_baseline_min": r0["selected_r_baseline_min"],
        "selected_r_baseline_max": r0["selected_r_baseline_max"],
        "repeats": args.repeats,
        "cost_weight": args.defense_cost_weight,
        "defense_cost_weight": args.defense_cost_weight,
        "attack_cost_weight": args.attack_cost_weight,
        "memory_risk_weight": args.memory_risk_weight,
        "attack_goal_bonus": args.attack_goal_bonus,
        "risk_mode": args.risk_mode,
        "attacker_policy": args.attacker_policy,
        "learning_mode": args.learning_mode,
        "turn_order": args.turn_order,
        "defender_policy": args.defender_policy,
        "max_rss_kb": _peak_rss_kb(),
        "defender_abstain": args.defender_abstain,
        "defender_abstentions": r0["defender_abstentions"],
        "risk_baseline": (
            "PREANA_local_EU" if args.risk_mode == "dynamic" else "neutral_r_i_1"
        ),
        "decision_model": (
            f"vector_preana_attacker_{args.attacker_policy}_"
            f"risk_{args.risk_mode}_learning_{args.learning_mode}_"
            f"turn_{args.turn_order}"
        ),
    }

    csv_path = os.path.join(args.out_dir, f"{model.name}.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(row.keys()))
        w.writeheader()
        w.writerow(row)

    trace_path = os.path.join(args.out_dir, f"{model.name}_trace.json")
    with open(trace_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "summary": row,
                "trace": r0["trace"],
                "final_state": r0["final_state"],
                "persistent_memory": r0["memory"],
            },
            f,
            indent=2,
        )

    dot_path = os.path.join(args.out_dir, f"{model.name}.dot")
    write_dot(dot_path, model, r0)

    print(json.dumps(row, separators=(",", ":")))


if __name__ == "__main__":
    main()
PY

FIELDS="package_version,tree,xml_nodes,state_dimensions,attack_actions,defense_actions,winner,attacker_cost,defender_cost,actions,attacker_steps,defender_steps,attack_path_switches,final_risk,parse_time_s,planning_time_median_s,planning_time_mean_s,planning_time_stdev_s,attacker_planning_time_median_s,attacker_planning_time_mean_s,defender_planning_time_median_s,defender_planning_time_mean_s,total_time_median_s,total_time_mean_s,attacker_preana_rounds,defender_preana_rounds,preana_rounds,memory_pairs,selected_r_baseline_mean,selected_r_baseline_min,selected_r_baseline_max,repeats,cost_weight,defense_cost_weight,attack_cost_weight,memory_risk_weight,attack_goal_bonus,risk_mode,attacker_policy,learning_mode,turn_order,defender_policy,defender_abstain,defender_abstentions,max_rss_kb,risk_baseline,decision_model"
printf '%s\n' "$FIELDS" > "$SUMMARY_CSV"

echo "Adaptive Vector-PREANA Experiment 3 -- synchronized bundle $PACKAGE_VERSION"
echo "  trees:                $TREE_DIR"
echo "  results:              $RESULT_DIR"
echo "  aggregate CSV:        $SUMMARY_CSV"
echo "  repeats:              $REPEATS"
echo "  defense cost weight:  $DEFENSE_COST_WEIGHT"
echo "  attack cost weight:   $ATTACK_COST_WEIGHT"
echo "  max steps:            $MAX_STEPS"
echo "  max PREANA rounds:    $MAX_ROUNDS"
echo "  memory risk weight:   $MEMORY_RISK_WEIGHT"
echo "  attack goal bonus:    $ATTACK_GOAL_BONUS"
echo "  risk mode:            $RISK_MODE"
echo "  attacker policy:      $ATTACKER_POLICY"
echo "  learning mode:        $LEARNING_MODE"
echo "  turn order:           $TURN_ORDER"
echo "  defender policy:      $DEFENDER_POLICY"
echo "  defender abstain:     $DEFENDER_ABSTAIN"
echo

for TREE_FILE in "$TREE_DIR"/*.xml; do
  BASENAME="$(basename "$TREE_FILE" .xml)"
  EXPERIMENT_DIRECTORY="$RESULT_DIR/$BASENAME"
  mkdir -p "$EXPERIMENT_DIRECTORY"

  echo "[$BASENAME] running Vector-PREANA (risk=$RISK_MODE, attacker=$ATTACKER_POLICY, learning=$LEARNING_MODE, order=$TURN_ORDER)..."
  JSON_LINE="$(python3 "$TMP_PY" \
    --tree "$TREE_FILE" \
    --package-version "$PACKAGE_VERSION" \
    --repo-root "$REPO_ROOT" \
    --out-dir "$EXPERIMENT_DIRECTORY" \
    --repeats "$REPEATS" \
    --defense-cost-weight "$DEFENSE_COST_WEIGHT" \
    --attack-cost-weight "$ATTACK_COST_WEIGHT" \
    --max-steps "$MAX_STEPS" \
    --max-rounds "$MAX_ROUNDS" \
    --memory-risk-weight "$MEMORY_RISK_WEIGHT" \
    --attack-goal-bonus "$ATTACK_GOAL_BONUS" \
    --risk-mode "$RISK_MODE" \
    --attacker-policy "$ATTACKER_POLICY" \
    --learning-mode "$LEARNING_MODE" \
    --turn-order "$TURN_ORDER" \
    --defender-abstain "$DEFENDER_ABSTAIN" \
    --defender-policy "$DEFENDER_POLICY")"

  python3 - "$JSON_LINE" "$SUMMARY_CSV" <<'PY'
import csv, json, sys
row = json.loads(sys.argv[1])
path = sys.argv[2]
fields = [
    "package_version","tree","xml_nodes","state_dimensions","attack_actions","defense_actions","winner",
    "attacker_cost","defender_cost","actions","attacker_steps","defender_steps",
    "attack_path_switches","final_risk","parse_time_s","planning_time_median_s",
    "planning_time_mean_s","planning_time_stdev_s","attacker_planning_time_median_s",
    "attacker_planning_time_mean_s","defender_planning_time_median_s",
    "defender_planning_time_mean_s","total_time_median_s","total_time_mean_s",
    "attacker_preana_rounds","defender_preana_rounds","preana_rounds","memory_pairs",
    "selected_r_baseline_mean","selected_r_baseline_min","selected_r_baseline_max",
    "repeats","cost_weight","defense_cost_weight","attack_cost_weight",
    "memory_risk_weight","attack_goal_bonus","risk_mode","attacker_policy",
    "learning_mode","turn_order","defender_policy","defender_abstain","defender_abstentions","max_rss_kb",
    "risk_baseline","decision_model"
]
with open(path, "a", newline="", encoding="utf-8") as f:
    csv.DictWriter(f, fieldnames=fields).writerow(row)
print(
    f"  winner={row['winner']:<8} "
    f"policy={row['attacker_policy']:<8} learning={row['learning_mode']:<3} "
    f"order={row['turn_order']:<14} "
    f"A_cost={row['attacker_cost']:<8g} D_cost={row['defender_cost']:<8g} "
    f"actions={row['actions']:<3} abstain={row['defender_abstentions']:<3} "
    f"rss={int(row['max_rss_kb'])//1024}MB ".replace("rss=-1MB ", "") +
    
    f"switches={row['attack_path_switches']:<3} "
    f"rbar={row['selected_r_baseline_mean']:.3f} "
    f"plan_med={row['planning_time_median_s']:.6f}s "
    f"(A={row['attacker_planning_time_median_s']:.6f}s, "
    f"D={row['defender_planning_time_median_s']:.6f}s)"
)
PY

done

echo
echo "Done. Aggregate results:"
echo "  $SUMMARY_CSV"
echo "Per-tree CSV/JSON/DOT traces:"
echo "  $RESULT_DIR/<tree>/"
echo
echo "Risk model: R_i -> r_i -> r_ij = clip(r_i + Delta_ij, 0.5, 2.0)."
echo "Persistent Delta_ij is updated only from realized attack-defense pairs when learning=on."
echo "Use neutral/greedy/off versus neutral/adaptive/off to isolate the attacker-policy effect."
echo "Use abstain off versus on to isolate the effect of forcing a response on every turn;"
echo "an abstention consumes a turn and does not update relational memory."
