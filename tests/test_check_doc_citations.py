"""Unit tests for scripts/check_doc_citations.py's relative-link validation.

Pure/unit — no network, no git: they exercise the link helpers directly (iter_links, is_relative_target,
relative_target_dead) against tmp_path files. Every branch of the NEW relative-link code is covered here,
because the corpus itself exercises almost none of them — notably `path#frag` has zero real instances in
the repo, so that branch ships on these tests alone. The commit-hash path is unchanged and is covered by
its own CI run over real history.
"""

from scripts.check_doc_citations import (
    is_relative_target,
    iter_links,
    relative_target_dead,
)

# --- iter_links: extraction, line numbers, fenced-block skipping ---


def test_inline_link_extracted_with_lineno():
    text = "intro line\nsee [the build](evaluation-first-rag.md) here\n"
    assert list(iter_links(text)) == [(2, "evaluation-first-rag.md")]


def test_link_inside_backtick_fence_is_ignored():
    text = "\n".join(
        [
            "before [real](real.md)",
            "```bash",
            "echo [not-a-link](totally-fake.md)  # illustrative, must be skipped",
            "```",
            "after [also-real](also.md)",
        ]
    )
    assert [t for _, t in iter_links(text)] == ["real.md", "also.md"]


def test_link_inside_tilde_fence_is_ignored():
    text = "~~~\n[x](fenced-fake.md)\n~~~\n[y](outside.md)\n"
    assert [t for _, t in iter_links(text)] == ["outside.md"]


def test_nested_image_badge_targets_are_remote():
    # [![alt](img)](href): the inner image target is captured; both are remote here, so nothing local.
    text = "[![CI](https://ex.com/b.svg)](https://ex.com/ci)\n"
    captured = [t for _, t in iter_links(text)]
    assert captured  # something matched
    assert all(not is_relative_target(t) for t in captured)  # remote -> never validated


# --- is_relative_target: classification ---


def test_pure_fragment_is_not_relative():
    assert is_relative_target("#reproducibility") is False


def test_scheme_uris_are_not_relative():
    assert is_relative_target("https://example.com/x") is False
    assert is_relative_target("http://example.com") is False
    assert is_relative_target("mailto:me@example.com") is False


def test_relative_paths_are_relative():
    assert is_relative_target("../eval/METRICS_HISTORY.md") is True
    assert is_relative_target("render.yaml") is True  # non-.md is still a relative target
    assert is_relative_target("sibling.md#section") is True  # path#frag -> path is relative


# --- relative_target_dead: resolution + existence (fragments, parent-relative, non-.md) ---


def test_valid_relative_link_not_dead(tmp_path):
    (tmp_path / "target.md").write_text("x")
    assert relative_target_dead("target.md", str(tmp_path)) is False


def test_missing_relative_link_is_dead(tmp_path):
    assert relative_target_dead("nope.md", str(tmp_path)) is True


def test_parent_relative_resolves(tmp_path):
    (tmp_path / "eval").mkdir()
    (tmp_path / "eval" / "M.md").write_text("x")
    base = tmp_path / "blog"
    base.mkdir()
    assert relative_target_dead("../eval/M.md", str(base)) is False


def test_path_fragment_validates_path_only(tmp_path):
    (tmp_path / "doc.md").write_text("x")
    assert relative_target_dead("doc.md#a-section", str(tmp_path)) is False  # path exists, frag ignored


def test_path_fragment_dead_when_path_missing(tmp_path):
    assert relative_target_dead("gone.md#a-section", str(tmp_path)) is True


def test_pure_fragment_never_dead(tmp_path):
    assert relative_target_dead("#same-page", str(tmp_path)) is False


def test_scheme_uris_never_dead(tmp_path):
    assert relative_target_dead("https://example.com/missing", str(tmp_path)) is False
    assert relative_target_dead("mailto:x@example.com", str(tmp_path)) is False


def test_non_markdown_relative_targets(tmp_path):
    # The trap: .yml/.py/.json/.yaml targets must be validated, not skipped for being non-.md.
    (tmp_path / "render.yaml").write_text("x")
    assert relative_target_dead("render.yaml", str(tmp_path)) is False
    assert relative_target_dead("src/config.py", str(tmp_path)) is True  # missing non-.md -> dead
