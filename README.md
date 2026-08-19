# IRENA — self-contained prototype and evaluation

IRENA (Intrusion Response by Expected-utility Negotiation over Attack-defense
trees) is a game-theoretic intrusion-response planner that operates directly
on R-ADTs, evaluated against the PANACEA stochastic-game approach and its two
rule baselines.

This package is **self-contained and IRENA-only**: it ships the R-ADT
instances and the XML parser, and it runs exclusively the IRENA, R-ADT LC and
R-ADT LD experiments. Nothing has to be downloaded — no PANACEA checkout, no
PRISM — and nothing in the package executes or models PANACEA: the reference
values used by the comparison figures are declared constants
(`analysis/panacea_reference.py`), transcribed from the reference runs the
paper reports.

## Quick start

```bash
pip install -r requirements.txt     # pandas, numpy, matplotlib, lxml, networkx
./run.sh                            # or: python3 pipeline.py run
```

That single command runs every experiment of the evaluation in dependency
order — the three defense policies, the λ_D sweep, the abstention study, the
victory attribution, the figures — and ends with a verification pass. Read
`experiment/reports/MANIFEST.md` when it finishes: one PASS/FAIL line per
claim, plus the outcome/cost table. `python3 pipeline.py status` shows what
would run without executing anything. Full expected numbers, per-stage manual
commands and troubleshooting: `docs/REPLICATION.md`.

## Layout

```
run.sh                    the one command (wraps pipeline.py run)
pipeline.py               the orchestrator: stages, staleness, verification
tree_to_prism.py          shipped copy of the R-ADT XML parser
tree.py                   its Node/Tree module (shipped with it)
experiment/
  trees/*.xml             the five R-ADT instances (+ tree drawings as PDF)
  ...                     everything the pipeline writes lands here
                          (CSVs, traces, figures/, reports/)
runner/
  run_irena.sh            the simulator: IRENA, R-ADT LC, R-ADT LD
  patch_ld_rule.py        one-shot fix, already applied to run_irena.sh
analysis/
  make_run_figures.py     every figure: IRENA/LC/LD from the experiment
                          CSVs, PANACEA from the declared reference values
  panacea_reference.py    those reference values, as data (see below)
  make_figures.py         the plotting/loading library behind it
  compare_abstention.py   abstention study report
  classify_terminations.py  victory attribution (blocked/exhausted)
  smoke_figures.py        quick visual test of the figure code
diagnostics/
  diagnose_tree.py        inspect one R-ADT instance; never invoked
docs/REPLICATION.md       the reviewer guide
```

## What the pipeline guarantees

Stages are content-addressed: each hashes the scripts and inputs it depends
on, so re-running with nothing changed does nothing, and editing the runner
re-runs exactly the simulated stages. Outputs already present (e.g. received
inside a copied folder) are adopted, not recomputed. The λ_D sweep stashes and
restores the operating-point outputs automatically, even on failure. Outcomes
and costs are deterministic — repetitions exist only to steady timing
statistics.

## The PANACEA reference values

The comparison figures show PANACEA's proven minimum costs, model sizes, and
solver time/memory next to the simulated policies. Those values are **data,
not computation**: they live in `analysis/panacea_reference.py` as declared
constants, transcribed from the reference runs reported in the paper. This
package never executes, models or regenerates PANACEA; reproducing those
reference numbers requires the PANACEA repository, PRISM-games and ~64 GB of
RAM, and is outside the scope of this artifact.
