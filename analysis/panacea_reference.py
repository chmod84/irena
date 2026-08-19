"""The PANACEA reference values used by the comparison figures.

These are the numbers of the PANACEA reference runs reported in the paper:
the proven minimum defense cost and the attacker cost along the exported
strategy, and the solver's model size, times and peak memory. They are
shipped as declared constants so that this package needs neither the PANACEA
repository nor PRISM to draw any comparison -- nothing here executes PANACEA.

Provenance: measured once on the reference machine from the corrected
translation of the five R-ADT instances and transcribed verbatim. Costs,
states and transitions are exact quantities; solver time and memory are
single-run measurements (repeated identical runs vary by up to ~1/3).
"""

# proven minimum defense cost and attacker cost along the exported strategy
PANACEA = {
    "10":        {"attacker_cost": 140.0, "defender_cost": 90.0},
    "25":        {"attacker_cost": 640.0, "defender_cost": 120.0},
    "29":        {"attacker_cost": 640.0, "defender_cost": 120.0},
    "34":        {"attacker_cost": 760.0, "defender_cost": 610.0},
    "adt_nuovo": {"attacker_cost": 25.0,  "defender_cost": 170.0},
}
for _r in PANACEA.values():
    _r["winner"] = "defender"

# states, transitions, model build s, model-checking s, peak RSS MB
STATS = {
    "10": (47, 73, 0.031, 0.017, 160.0),
    "25": (100_157, 286_281, 0.721, 1.286, 1052.0),
    "29": (2_136_380, 8_030_113, 18.407, 37.732, 16206.0),
    "34": (9_434_582, 37_012_309, 155.975, 332.115, 52836.0),
}

TIME = {t: c for t, (_s, _tr, _b, c, _m) in STATS.items()}
MEM = {t: m for t, (_s, _tr, _b, _c, m) in STATS.items()}
