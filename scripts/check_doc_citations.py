#!/usr/bin/env python3
"""CI guard — every commit-hash-like token cited in a tracked Markdown doc must resolve to a commit
in this repo's history.

Why: this repo's value is that its evidence chain is trustworthy — docs cite commit hashes as proof.
A history rewrite, a bad copy-paste, or a squashed-away commit can leave a citation pointing at a
commit that no longer exists. A dead reference is visibly broken; a wrong one silently misleads.
This check fails the build (non-zero exit) listing any citation that does not resolve, so the evidence
chain is machine-verified rather than assumed.

Notes:
- Runs against `git ls-files '*.md'` (the citation surface). No dependencies beyond git + python3.
- Requires FULL history — the workflow checks out with `fetch-depth: 0`; on a shallow clone older
  commits are absent and valid citations would false-fail.
- Word-boundary matching (7–40 lowercase-hex, not flanked by [0-9A-Za-z_]) so generation fingerprints
  like `fp_c881474fd1`, 64-char content hashes, and embedded ids are NOT matched.
- Runs in the CI checkout, which has no orphaned objects, so `git cat-file -e` is an honest
  reachability proxy there (an orphaned commit would not exist in a fresh clone).
"""
import re
import subprocess
import sys

HASH = re.compile(r"(?<![0-9A-Za-z_])[0-9a-f]{7,40}(?![0-9A-Za-z_])")


def tracked_markdown() -> list[str]:
    out = subprocess.run(["git", "ls-files", "*.md"], capture_output=True, text=True, check=True)
    return out.stdout.split()


def resolves(sha: str) -> bool:
    return subprocess.run(
        ["git", "cat-file", "-e", f"{sha}^{{commit}}"], capture_output=True
    ).returncode == 0


def main() -> int:
    dead: list[tuple[str, int, str]] = []
    checked = 0
    files = tracked_markdown()
    for f in files:
        for i, line in enumerate(open(f, encoding="utf-8").read().splitlines(), 1):
            for sha in sorted(set(HASH.findall(line))):
                checked += 1
                if not resolves(sha):
                    dead.append((f, i, sha))
    if dead:
        print("Dead commit-hash citation(s) — these do not resolve to a commit in history:")
        for f, i, sha in dead:
            print(f"  {f}:{i}: {sha}")
        print(
            "\nIf commit hashes were rewritten, remap the citations old->new "
            "(see the commit-hash note in eval/METRICS_HISTORY.md)."
        )
        return 1
    print(f"OK: all {checked} cited commit hashes across {len(files)} tracked docs resolve.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
