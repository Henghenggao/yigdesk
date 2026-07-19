"""Fail closed if private-product or domain-specific code crosses a public seam."""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
PUBLIC_NEUTRAL_FILES = [
    ROOT / "yigdesk" / "static" / "session.js",
    ROOT / "yigdesk" / "static" / "yig-grid.js",
    ROOT / "yigdesk" / "static" / "yig-model-inspector.js",
]
CAPABILITY_BOUNDARY_FILES = [
    ROOT / "yigdesk" / "app.py",
    ROOT / "yigdesk" / "cli.py",
    *PUBLIC_NEUTRAL_FILES,
]


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_public_sdk_and_components_remain_domain_neutral():
    forbidden = re.compile(
        r"finance|financial|deal|discount|\barr\b|margin|cogs|cfo|revenue|renewal|northstar|marie|elena",
        re.IGNORECASE,
    )
    hits = {
        str(path.relative_to(ROOT)): sorted(set(forbidden.findall(_text(path))))
        for path in PUBLIC_NEUTRAL_FILES
        if forbidden.search(_text(path))
    }
    assert not hits, f"Business vocabulary crossed the domain-neutral public seam: {hits}"


def test_public_runtime_has_no_commercial_mutation_or_trust_service():
    # Approvals + human-gated resolution are the standalone blackboard's core product
    # (the deterministic gate); this guard now targets SOURCE mutation + external
    # commercial/trust services only (not the decision-approval ops the blackboard owns).
    forbidden = re.compile(
        r"write[-_ ]?back|vault|attest|audit[-_ ]?trail|reopen|sign(?:ing|ature)|"
        r"multi[-_ ]?tenant|\bsso\b",
        re.IGNORECASE,
    )
    hits = {
        str(path.relative_to(ROOT)): sorted(set(forbidden.findall(_text(path))))
        for path in CAPABILITY_BOUNDARY_FILES
        if forbidden.search(_text(path))
    }
    assert not hits, f"A commercial capability crossed into the public runtime seam: {hits}"


def test_private_kernel_signals_are_absent_from_public_runtime():
    forbidden = re.compile(
        r"governed_seq|raw_ledger_rev|mapping_revision|policy_revision|op[-_ ]?log|"
        r"ConsequencePacketV1|\bspine[./\\]",
        re.IGNORECASE,
    )
    runtime = [
        path
        for path in (ROOT / "yigdesk").rglob("*")
        if path.suffix in {".py", ".js", ".html"}
    ]
    hits = {
        str(path.relative_to(ROOT)): sorted(set(forbidden.findall(_text(path))))
        for path in runtime
        if forbidden.search(_text(path))
    }
    assert not hits, f"Private kernel signal found in the public runtime: {hits}"


def test_legacy_brand_and_personal_machine_markers_are_absent():
    legacy_brand = "proof" + "desk"
    machine_root = "C:" + "\\Users\\"
    personal_name = "gao" + "yu"
    forbidden = re.compile(
        rf"{legacy_brand}|{re.escape(machine_root)}|\b{personal_name}\b",
        re.IGNORECASE,
    )
    excluded = {".git", "node_modules", "runtime", ".pytest_cache", "test-results", "playwright-report"}
    files = [
        path
        for path in ROOT.rglob("*")
        if path.is_file()
        and not any(part in excluded for part in path.parts)
        and path.suffix.lower()
        in {".py", ".js", ".ts", ".json", ".md", ".yml", ".yaml", ".html", ".css"}
    ]
    hits = [str(path.relative_to(ROOT)) for path in files if forbidden.search(_text(path))]
    assert not hits, f"Legacy brand or personal machine marker found: {hits}"


def test_visual_system_uses_named_tokens_and_specific_transitions():
    css = _text(ROOT / "yigdesk" / "static" / "styles.css")
    for token in (
        "--font-display",
        "--font-sans",
        "--font-mono",
        "--canvas",
        "--surface-raised",
        "--forest",
        "--evidence",
        "--hold",
    ):
        assert token in css
    assert "transition: all" not in css
    assert "font-variant-numeric: tabular-nums" in css
    assert "prefers-reduced-motion" in css


def test_private_engine_name_is_absent_from_shipped_surface():
    """Yigdesk is standalone: the private parent engine's name must not appear
    anywhere in the shipped surface. The forbidden token is assembled from a
    split literal so this guard file itself never carries a copy of it."""
    forbidden = re.compile("yig" + "rid", re.IGNORECASE)
    skip_dirs = {
        ".git",
        "node_modules",
        "runtime",
        ".pytest_cache",
        "test-results",
        "playwright-report",
        "__pycache__",
    }

    def _skipped(path: Path) -> bool:
        return any(part in skip_dirs for part in path.relative_to(ROOT).parts)

    # docs/superpowers/** are internal planning/spec artifacts, not shipped, and
    # legitimately discuss the separation from the private engine.
    superpowers = ROOT / "docs" / "superpowers"
    surface: list[Path] = [ROOT / "README.md"]
    surface += [
        path
        for path in (ROOT / "yigdesk").rglob("*")
        if path.is_file()
        and path.suffix in {".py", ".js", ".html", ".css"}
        and not _skipped(path)
    ]
    surface += [
        path
        for path in (ROOT / "docs").rglob("*.md")
        if superpowers not in path.parents and not _skipped(path)
    ]
    surface += [path for path in (ROOT / "tests").rglob("*.py") if not _skipped(path)]
    # The council Domain App surface (personas, skill, agent contract, notice) is
    # shipped too and must stay free of the private engine's name.
    surface += [ROOT / "AGENTS.md", ROOT / "NOTICE"]
    for config_dir in (ROOT / ".codex", ROOT / ".agents"):
        surface += [
            path
            for path in config_dir.rglob("*")
            if path.is_file()
            and path.suffix in {".toml", ".md", ".yaml", ".yml"}
            and not _skipped(path)
        ]

    hits = sorted(
        str(path.relative_to(ROOT))
        for path in surface
        if path.exists() and forbidden.search(_text(path))
    )
    assert not hits, (
        "Private parent-engine name leaked into the shipped surface: " + ", ".join(hits)
    )
