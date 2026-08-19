#!/usr/bin/env python3
"""Align the R-ADT LD baseline with the reference implementation.

THE PROBLEM. Our LD ranks every enabled response by the distance of its target
dimension to the goal and takes the smallest. On the realistic instance that
picks `changeCredentials` first -- the response guarding `Access to MySQL`, one
step from the goal -- and the defender then LOSES, because the attacker walks
the other branch through `bufferOverflow`. The reference LD trace shipped with
the PANACEA artifact plays `deactivateSOCKS5Proxy` first and WINS at 330.

THE RULE, RECOVERED FROM THAT TRACE. The reference LD only considers responses
whose target dimension is CURRENTLY 1, i.e. a condition that actually holds:
an existing misconfiguration, an active proxy, files that are unencrypted, a
foothold the attacker has already taken. A response whose target is still 0
defends nothing yet. Among those candidates it takes the one closest to the
goal, ties broken by cost.

Checked against all three defender moves of the reference trace, using the
state vector printed in its own CSV:

  turn 1  active targets: UnencryptedFiles(d2,150) SOCKS5ProxyActive(d2,120)
          MisconfiguredApache(d3,50) CGIscriptsenabled(d4,45)
          VulnerableApache(d4,20)          -> deactivateSOCKS5Proxy   MATCH
  turn 2  SOCKS5 now blocked               -> encryptFile             MATCH
  turn 3  AccesstoSensitiveFiles now 1     -> changeFilePermissions   MATCH

`changeCredentials` never becomes a candidate because `Access to MySQL` never
reaches 1 in that run. That is exactly the difference.

LC IS NOT TOUCHED. Our LC already reproduces the reference LC trace move for
move, so its candidate set is left alone. Whether the reference applies the
same restriction to LC cannot be told from that trace: both responses it picks
happen to target dimensions that are already 1.

    python3 patch_ld_rule.py --dry-run
    python3 patch_ld_rule.py
    python3 patch_ld_rule.py path/to/run_vector_preana_experiment3.sh

Idempotent, backs the original up as <file>.orig the first time, and writes
nothing unless the block it expects is present verbatim.
"""

from __future__ import annotations

import argparse
import ast
import re
import shutil
import sys
from pathlib import Path

OLD_DOC = """        LC picks the cheapest response enabled in the current state. LD picks
        the enabled response that blocks the precondition closest to the
        attacker root, irrespective of cost; `Model.dist` already holds the
        BFS distance of every dimension to the goal, so "closest to the root"
        is the smallest distance. A precondition that cannot reach the goal at
        all has infinite distance and is therefore considered last, which is
        what "the defense that permits defending the system" excludes.
"""

NEW_DOC = """        LC picks the cheapest response enabled in the current state.

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
"""

OLD_BLOCK = """        BIG = 10 ** 9

        def root_distance(d: str) -> int:
            effect = str(self.m.defenses[d]["effect"])
            return int(self.m.dist.get(effect, BIG))

        if policy == "lc":
"""

NEW_BLOCK = """        BIG = 10 ** 9

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
"""

MARKER = "def choose_defense_rule(self, state: Mapping[str, int], policy: str):"


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("target", nargs="?", type=Path,
                   default=Path(__file__).resolve().parent / "run_irena.sh")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    if not args.target.exists():
        print(f"ERROR: {args.target} not found", file=sys.stderr)
        return 2

    original = args.target.read_text(encoding="utf-8")
    if MARKER not in original:
        print(f"ERROR: {MARKER} not found; this does not look like the runner",
              file=sys.stderr)
        return 3

    if "if policy == \"ld\":\n            # A response removes a condition" in original:
        print("Nothing to do: the file was already patched.")
        return 0

    text = original
    for label, old, new in (("docstring", OLD_DOC, NEW_DOC),
                            ("candidate filter", OLD_BLOCK, NEW_BLOCK)):
        n = text.count(old)
        if n != 1:
            print(f"ERROR: {label}: expected exactly 1 occurrence, found {n}."
                  f" Nothing written.\nExpected verbatim:\n"
                  + "".join(f"  | {l}\n" for l in old.splitlines()),
                  file=sys.stderr)
            return 3
        text = text.replace(old, new, 1)
        print(f"  patched  {label}")

    # ---- checks ------------------------------------------------------------
    print("\nchecks")
    m = re.search(r"^python3 - .*<<'PY'$(.*?)^PY$", text,
                  re.MULTILINE | re.DOTALL)
    body = m.group(1) if m else None
    if body is None:
        # the embedded program may be written to a temp file instead; fall back
        # to compiling the largest heredoc that mentions the marker
        blocks = re.findall(r"<<'PY'\n(.*?)\nPY\n", text, re.DOTALL)
        body = next((b for b in blocks if MARKER in b), None)
    if body is None:
        print("  WARNING: could not isolate the embedded Python to compile it")
    else:
        try:
            ast.parse(body)
            print("  embedded Python parses")
        except SyntaxError as exc:
            print(f"  SYNTAX ERROR in the embedded Python: {exc};"
                  f" nothing written", file=sys.stderr)
            return 4

    if text.count("candidates = active") != 1:
        print("  FAIL: the filter is not present exactly once; nothing written",
              file=sys.stderr)
        return 4
    print("  the LD candidate filter is in place exactly once")

    lc_key = 'key = lambda d: (self.m.defense_cost[d], root_distance(d), d)'
    if original.count(lc_key) != text.count(lc_key) or text.count(lc_key) != 1:
        print("  FAIL: the LC ordering changed; nothing written", file=sys.stderr)
        return 4
    print("  LC is untouched")

    added = len(text.splitlines()) - len(original.splitlines())
    print(f"  {added} line(s) added, none removed"
          if added > 0 else f"  line delta: {added}")

    if args.dry_run:
        print("\n--dry-run: not written")
        return 0

    backup = args.target.with_suffix(args.target.suffix + ".orig")
    if not backup.exists():
        shutil.copy2(args.target, backup)
        print(f"\nbackup: {backup}")
    args.target.write_text(text, encoding="utf-8")
    print(f"written: {args.target}")
    print("\nRe-run only the LD baseline; IRENA and LC are unaffected:\n"
          "  bash run_irena.sh 20 0.0001 0.0001 60 40 "
          "0.70 1.0 dynamic adaptive on attacker_first off ld")
    return 0


if __name__ == "__main__":
    sys.exit(main())
