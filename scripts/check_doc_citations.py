#!/usr/bin/env python3
"""CI guard — the docs' evidence chain is machine-verified: every cited commit hash must resolve to a
commit in history, AND every relative markdown link must point at a file that exists in the repo.

Why (commit hashes): this repo's value is that its evidence chain is trustworthy — docs cite commit
hashes as proof. A history rewrite, a bad copy-paste, or a squashed-away commit can leave a citation
pointing at a commit that no longer exists. A dead reference is visibly broken; a wrong one silently
misleads. This check fails the build (non-zero exit) listing any citation that does not resolve.

Why (relative links): the same trust argument, one level up. The write-ups cross-link each other and
cite eval artifacts, scripts, and workflows by relative path (`../eval/METRICS_HISTORY.md`,
`../.github/workflows/post-deploy-wire-smoke.yml`). A renamed or deleted target turns cited proof into
a 404 a reader only finds by clicking. This check resolves every relative link target and fails the
build listing any that does not exist — so "the link works" is verified, not assumed.

Scope & handling (relative links):
- Runs against `git ls-files '*.md'` (the doc surface). No dependencies beyond git + python3.
- INLINE links only: `[text](target)`. Reference-style links (`[text][ref]` + `[ref]: target`) are NOT
  supported — there are none in this repo, and one added later would be silently unchecked, so if you
  add one, extend this guard rather than assume it is covered.
- Fenced code blocks (``` or ~~~) are STRIPPED before extraction: a link-shaped string inside a code
  sample is illustration, not a link. Corollary: write illustrative link syntax in a fenced block, not
  an inline `code` span — inline-code spans are deliberately NOT stripped, so a real broken link that
  merely happens to be backticked is still caught.
- Targets are classified by prefix: a pure `#fragment` (same-page anchor) is skipped; a `path#frag` is
  validated by its `path` (fragment ignored); any URI scheme (`http://`, `https://`, `mailto:`, ...) is
  remote and skipped. Everything else is a relative file target, resolved against the containing file's
  directory — for ANY extension, not just `.md` (README cites `src/config.py`, `render.yaml`,
  `data/manifest.json`, and workflow `.yml` files, and those must be validated too).
- Existence is checked with `os.path.exists` against the checkout. This is deliberately a DIFFERENT
  mechanism than a `git ls-files` membership test, so an independent by-hand audit that agrees with it
  is real corroboration, not the same code run twice. Two nuances follow, both making CI (a clean,
  Linux, tracked-only checkout) the authority — a green LOCAL run is not dispositive:
    * `os.path.exists` is case-sensitive on Linux (CI) but not on macOS, so a wrong-case link passes
      locally and fails in CI;
    * locally it also sees UNTRACKED files, so a link to a file you forgot to `git add` passes locally
      and fails in CI. Both are the right outcome; just don't trust a local green as final.

Notes (commit hashes, unchanged):
- Requires FULL history — the workflow checks out with `fetch-depth: 0`; on a shallow clone older
  commits are absent and valid citations would false-fail.
- Word-boundary matching (7-40 lowercase-hex, not flanked by [0-9A-Za-z_]) so generation fingerprints
  like `fp_c881474fd1`, 64-char content hashes, and embedded ids are NOT matched.
- Runs in the CI checkout, which has no orphaned objects, so `git cat-file -e` is an honest
  reachability proxy there (an orphaned commit would not exist in a fresh clone).
"""
import os
import re
import subprocess
import sys

HASH = re.compile(r"(?<![0-9A-Za-z_])[0-9a-f]{7,40}(?![0-9A-Za-z_])")
# Inline markdown link: capture the target inside `](...)`. Reference-style links are unsupported (see
# the module docstring). The nested case `[![alt](img)](href)` yields the inner `img`; both its targets
# are remote in this corpus, so nothing local is missed.
LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
# A leading URI scheme (`http:`, `https:`, `mailto:`, ...) marks an absolute/remote target we skip.
SCHEME = re.compile(r"^[a-z][a-z0-9+.\-]*:")


def tracked_markdown() -> list[str]:
    out = subprocess.run(["git", "ls-files", "*.md"], capture_output=True, text=True, check=True)
    return out.stdout.split()


def resolves(sha: str) -> bool:
    return subprocess.run(
        ["git", "cat-file", "-e", f"{sha}^{{commit}}"], capture_output=True
    ).returncode == 0


def iter_links(text: str):
    """Yield (lineno, target) for each inline [text](target) link, skipping fenced code blocks.

    Line numbers are preserved (fenced lines are skipped in place, not deleted) so a dead link reports
    its true file:line. Fence state toggles on any line whose first non-space run is ``` or ~~~; this
    assumes balanced fences (true for this corpus).
    """
    in_fence = False
    for i, line in enumerate(text.splitlines(), 1):
        stripped = line.lstrip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        for m in LINK.finditer(line):
            yield i, m.group(1)


def _target_path(target: str) -> str:
    """The path portion of a link target, with any #fragment removed ('' for a pure #anchor)."""
    return target.strip().split("#", 1)[0]


def is_relative_target(target: str) -> bool:
    """True if `target` is a relative file path this guard should validate — i.e. not a pure #anchor
    and not an absolute URI (http(s)/mailto/any scheme)."""
    path = _target_path(target)
    return bool(path) and not SCHEME.match(path.lower())


def relative_target_dead(target: str, base_dir: str) -> bool:
    """True iff `target` is a relative link whose file does not exist under `base_dir`. Non-relative
    targets (pure anchors, scheme URIs) are never dead (return False)."""
    if not is_relative_target(target):
        return False
    resolved = os.path.normpath(os.path.join(base_dir, _target_path(target)))
    return not os.path.exists(resolved)


def main() -> int:
    files = tracked_markdown()
    dead_hashes: list[tuple[str, int, str]] = []
    dead_links: list[tuple[str, int, str]] = []
    hashes_checked = 0
    links_checked = 0
    for f in files:
        text = open(f, encoding="utf-8").read()
        base = os.path.dirname(f)
        # (1) commit-hash citations — behavior unchanged.
        for i, line in enumerate(text.splitlines(), 1):
            for sha in sorted(set(HASH.findall(line))):
                hashes_checked += 1
                if not resolves(sha):
                    dead_hashes.append((f, i, sha))
        # (2) relative markdown link targets.
        for i, target in iter_links(text):
            if not is_relative_target(target):
                continue
            links_checked += 1
            if relative_target_dead(target, base):
                dead_links.append((f, i, target))

    if dead_hashes:
        print("Dead commit-hash citation(s) — these do not resolve to a commit in history:")
        for f, i, sha in dead_hashes:
            print(f"  {f}:{i}: {sha}")
        print(
            "\nIf commit hashes were rewritten, remap the citations old->new "
            "(see the commit-hash note in eval/METRICS_HISTORY.md)."
        )
    else:
        print(f"OK: all {hashes_checked} cited commit hashes across {len(files)} tracked docs resolve.")

    if dead_links:
        print("\nDead relative link(s) — these targets do not exist in the checkout:")
        for f, i, target in dead_links:
            print(f"  {f}:{i}: {target}")
        print(
            "\nFix the link or the target. Illustrative link syntax belongs in a fenced code block "
            "(which this check skips), not an inline `code` span."
        )
    else:
        print(f"OK: all {links_checked} relative markdown links across {len(files)} tracked docs resolve.")

    return 1 if (dead_hashes or dead_links) else 0


if __name__ == "__main__":
    sys.exit(main())
