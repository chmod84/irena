# Replicating the IRENA Evaluation

This guide takes a reviewer from unpacking the package (or cloning the
repository) to every number, table and figure in the paper. It assumes nothing
beyond a Linux machine and basic command-line use.

The package is self-contained and runs IRENA-side experiments only. It ships
the five R-ADT instances and the XML parser; every simulated experiment —
IRENA and both rule baselines, the λ_D sweep, the abstention study, the
victory attribution — and every figure is recomputed on your machine, on any
laptop, in roughly 30–45 minutes. Nothing in the package executes or models
PANACEA: the reference values the comparison uses (proven minimum defense
costs, model sizes, solver time and memory) are declared constants in
`analysis/panacea_reference.py`, transcribed from the reference runs the
paper reports (see §7 on their provenance).

## 1. Requirements

- Linux, x86-64 (any recent distribution).
- Python ≥ 3.10 with `pandas`, `numpy`, `matplotlib`, `lxml`, `networkx`
  (`pip install -r requirements.txt`).

## 2. Package layout

```
run.sh                    the one command (wraps pipeline.py run)
pipeline.py               THE ORCHESTRATOR -- start here
tree_to_prism.py          shipped copy of the R-ADT XML parser
tree.py                   its Node/Tree module (shipped with it)
experiment/
  trees/*.xml             the five R-ADT instances
  reports/                written by the pipeline: logs + MANIFEST.md
  figures/                written by the pipeline
runner/run_irena.sh       simulator: IRENA / R-ADT LC / R-ADT LD
analysis/                 figures, abstention report, attribution, and the
                          declared PANACEA reference values (see §7)
diagnostics/              optional inspection tools
```

The five instances are the four synthetic scalability trees (10, 25, 29, 34
nodes) and the published Apache/MySQL data-exfiltration use case
(`adt_nuovo`), the one instance whose action names and costs are semantically
meaningful.

## 3. Quick start

From the package root:

```bash
./run.sh                             # or: python3 pipeline.py run
```

`python3 pipeline.py status` first, if you prefer to inspect without
executing: one line per stage. Should the copy you received already contain
simulated results (every stage `ok`), reproduce them from scratch with

```bash
python3 pipeline.py run --force policies sweep abstention classify figures verify
```

`run` executes, logging each stage to `experiment/reports/<stage>.log`. On a
laptop expect roughly: policies ~10 min, sweep ~15–25 min (six extra λ_D
values across five instances), abstention ~5 min, everything else seconds.

When it finishes, read **`experiment/reports/MANIFEST.md`**. It contains a
PASS/FAIL line for every invariant the paper claims (see §5) and the
outcome/cost table at the operating point. If every line is PASS, you have
replicated the effectiveness results.

The pipeline is incremental and content-addressed: re-running `run` with
nothing changed does nothing; editing a script or deleting an output re-runs
exactly the stages that depend on it. `python3 pipeline.py run --force
<stage>` re-runs a stage unconditionally.

## 4. What to compare against the paper

**Effectiveness (Table E).** From `reports/MANIFEST.md` or
`experiment/vector_preana*_result.csv`. Operating point λ_D = 2×10⁻⁴,
repeats = 3. W/L = defender wins/loses. Proven minimum = defender objective of
the PANACEA strategy.

| instance | proven min | IRENA | R-ADT LD | R-ADT LC |
|---|---|---|---|---|
| 10 | 90 | 90 (W) | 90 (W) | 50 (L) |
| 25 | 120 | 180 (W) | 660 (W) | 40 (L) |
| 29 | 120 | 240 (W) | 280 (W) | 100 (L) |
| 34 | 610 | 830 (W) | 780 (W) | 100 (L) |
| adt_nuovo | 170 | 170 (W) | 330 (W) | 65 (L) |

Mean overhead over the proven minimum: IRENA 37%, R-ADT LD 141%; the 34-node
instance is the one on which LD beats IRENA. On `adt_nuovo`, IRENA's
trajectory must be exactly
`webRecon → deactivateSOCKS5Proxy → pathTraversal → reconfigureApache` — the
plan the model checker proves optimal (trace JSON under
`experiment/vector_preana_results/adt_nuovo/`).

**Performance (Table P).** The PANACEA side is the declared reference values
of `analysis/panacea_reference.py`, measured once on the reference machine
(times/memory vary ±~1/3 between identical solver runs).

| instance | states | transitions | model checking | peak RSS |
|---|---|---|---|---|
| 10 | 47 | 73 | ~0.02 s | ~160 MB |
| 25 | 100,157 | 286,281 | ~1.3 s | ~1.0 GB |
| 29 | 2,136,380 | 8,030,113 | ~38 s | ~16 GB |
| 34 | 9,434,582 | 37,012,309 | ~332 s | ~52 GB |

The three simulated defenders stay at 86–90 MB on every instance (a few MB
above the bare Python interpreter); their planning times are measured fresh on
your machine.

**Sweep.** Seven λ_D values. 2×10⁻⁴ is the largest weight at which every
instance is defended; above it they fall in the order 34 (at 5×10⁻⁴),
adt_nuovo (10⁻³), 29 (10⁻²), all (10⁻¹). IRENA attains the 170 optimum on
adt_nuovo at 10⁻⁴, 2×10⁻⁴ and 5×10⁻⁴.

**Abstention** (`reports/abstention.txt`): the winner changes on no instance;
exactly one abstention, on the 29-node instance, lowering its cost 240 → 210.

**Attribution** (`reports/classify.txt`): verdict BLOCKED on 5/5; on
adt_nuovo, undoing the defenses reopens exactly `bufferOverflow` and
`getFiles`, the entry points of the two routes to exfiltration.

**Baseline validity** (automatic, in MANIFEST): the LC and LD trajectories on
adt_nuovo reproduce, action for action, the reference traces distributed with
the PANACEA artifact (LC spends 65 and loses; LD spends 330 and wins).

**Figures.** `experiment/figures/` contains, under the exact names the
paper's LaTeX includes: `perf_state_space.pdf`, `perf_planning_time.pdf`,
`perf_peak_memory.pdf` (performance, synthetic suite 10–34) and
`eff_defender_cost.pdf`, `eff_lambda_sweep.pdf` (effectiveness, all five
instances). Pointing the paper source at this directory rebuilds the PDF with
your figures.

## 5. The stages, and how to run them by hand

Every stage is a thin wrapper over one auditable command, listed here so the
orchestrator never has to be trusted blindly. Run from the package root.

**preflight** (automatic, no side effects) verifies the package: the shipped
parser contains the refinement correction — an action's refinement read
from its own row rather than from its effect node —
and the runner implements the validated LD rule (candidates restricted to
defenses whose target condition currently holds).

**policies** — the simulator's thirteen positional arguments are
`REPEATS λ_D λ_A MAX_STEPS MAX_ROUNDS ω B_g RISK ATTACKER LEARNING ORDER
ABSTAIN POLICY`:

```bash
bash runner/run_irena.sh 3 0.0002 0.0001 60 40 0.70 1.0 \
     neutral adaptive off attacker_first off irena     # then: lc, ld
```

**sweep** — one such run per λ_D with `irena`, each time moving
`experiment/vector_preana_result.csv` to
`experiment/sweep_lambdaD_<λ>.csv`. Caution when doing this manually: the
runner writes the same output paths regardless of λ_D, so the operating-point
CSV and traces must be saved first and restored afterwards. The pipeline does
this stash/restore automatically, inside a `finally` block.

**abstention**:

```bash
bash runner/run_irena.sh 3 0.0002 0.0001 60 40 0.70 1.0 \
     neutral adaptive off attacker_first on irena
python3 analysis/compare_abstention.py
```

**classify**:

```bash
python3 analysis/classify_terminations.py --repo-root . \
        --results-dir experiment/vector_preana_results
```

**figures**:

```bash
python3 analysis/make_run_figures.py \
        --trees 10 25 29 34 adt_nuovo --perf-exclude adt_nuovo
```

The exclusion implements the paper's scoping: the case-study instance is not
part of the synthetic size suite, so it stays out of the performance figures.
Costs are compared on all five instances.

**verify** — recomputes every claim in §4 from the CSVs and traces and writes
`reports/MANIFEST.md`; exits non-zero if any check fails.

## 6. Determinism

Outcomes, trajectories and costs are deterministic: the simulator and its tie
breaking contain no randomness. Repetitions (3) exist only to steady the
timing statistics. If any cost in Table E differs on your machine, that is a
replication failure worth reporting — not noise. Times and memory are the only
quantities expected to vary.

## 7. Provenance of the PANACEA reference values (out of scope)

The constants in `analysis/panacea_reference.py` — proven minimum defense
cost and attacker cost per instance, model sizes, solver time and peak
memory — were measured once from the PANACEA reference runs on the corrected
translation of the same five R-ADT XML files, and transcribed verbatim.
Reproducing them requires the PANACEA repository, PRISM-games 3.2.1, Java
11+, GNU time and ~64 GB of RAM for the 34-node instance; none of that is
part of, or needed by, this package.

## 8. Troubleshooting

- **`preflight failed`** — the message names the file and the missing
  correction; the package is not intact as shipped.
- **A stage keeps re-running** — its output check names the file it finds
  missing or inconsistent (e.g. a CSV whose recorded `defense_cost_weight`
  does not match the configured λ_D).
- **Start over from a clean slate** — delete
  `experiment/.pipeline_state.json` and `experiment/reports/`, then
  `./run.sh`: existing experiment outputs are adopted where valid, the rest
  is recomputed.
- **Different tree set / parameters** — `python3 pipeline.py init` writes
  `pipeline.json`; edit it. Set `"paper_checks": false` when running on
  instances other than the paper's five, so dataset-specific invariants are
  skipped rather than reported as failures.
