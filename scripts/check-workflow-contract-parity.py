#!/usr/bin/env python3
"""Cross-app workflow-contract parity check.

apps/canvus-mcp and apps/lab-agent are two independent uv projects (separate
venvs/lockfiles, no shared runtime dependency) that must still agree on the
experiment-workflow marker contract: the title-prefix markers canvus-mcp's
detector recognises (``ExpMarkers``/``Settings`` defaults) must match what
lab-agent's orchestrator writes (``nodes.py`` constants and the note-body first
lines), and both must match what docs/experiment-workflow.md documents.

It proves that alignment *without* coupling the apps: it never imports either
package — every value is read as plain text from the source files with regexes.

Usage: python scripts/check-workflow-contract-parity.py

Exit codes: 0 = self-test passed and the repo is aligned; 1 = the self-test
failed to catch its injected mismatch (checker untrustworthy) or a real
mismatch was found.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Fields shared by canvus-mcp's ExpMarkers dataclass and its Settings class
# (Settings supplies the configurable default that ExpMarkers is built from).
_MARKER_NAMES = ("ragcluster", "robot", "setup", "result", "idea", "closed")


class ParityError(Exception):
    """Raised for a single contract mismatch; the message is the diagnostic."""


def _read(path: Path) -> str:
    if not path.exists():
        raise ParityError(f"missing file: {path}")
    return path.read_text(encoding="utf-8")


def _extract_fields(text: str, names: tuple[str, ...], path: Path) -> dict[str, str]:
    """Pull ``name: str = "value"`` or ``name = "value"`` literals out of source text."""
    pattern = re.compile(
        r'^\s*(?P<name>' + "|".join(re.escape(n) for n in names) + r')'
        r'(?:\s*:\s*str)?\s*=\s*"(?P<value>[^"]*)"',
        re.MULTILINE,
    )
    found = {m.group("name"): m.group("value") for m in pattern.finditer(text)}
    missing = set(names) - set(found)
    if missing:
        raise ParityError(f"{path}: could not find field(s) {sorted(missing)} (source layout changed?)")
    return found


def canvus_expmarkers_defaults(canvus_mcp_root: Path) -> dict[str, str]:
    """canvus-mcp's ``ExpMarkers`` dataclass field defaults (the detector's source of truth)."""
    path = canvus_mcp_root / "canvus_mcp" / "experiment_widgets.py"
    return _extract_fields(_read(path), _MARKER_NAMES, path)


def canvus_settings_defaults(canvus_mcp_root: Path) -> dict[str, str]:
    """canvus-mcp's ``Settings`` field defaults — must mirror ``ExpMarkers`` 1:1
    (``tools/experiments.py:_markers()`` builds an ``ExpMarkers`` from these)."""
    path = canvus_mcp_root / "canvus_mcp" / "config.py"
    text = _read(path)
    settings_names = {
        "ragcluster": "mcp_ragcluster_marker",
        "robot": "mcp_robot_marker",
        "setup": "mcp_exp_setup_marker",
        "result": "mcp_exp_result_marker",
        "idea": "mcp_idea_marker",
        "closed": "mcp_exp_closed_marker",
    }
    pattern = re.compile(
        r'(?P<name>' + "|".join(re.escape(n) for n in settings_names.values()) + r')'
        r':\s*\w+\s*=\s*Field\(\s*default="(?P<value>[^"]*)"',
        re.DOTALL,
    )
    found = {m.group("name"): m.group("value") for m in pattern.finditer(text)}
    missing = set(settings_names.values()) - set(found)
    if missing:
        raise ParityError(f"{path}: could not find setting(s) {sorted(missing)} (source layout changed?)")
    return {short: found[long] for short, long in settings_names.items()}


def lab_agent_node_markers(lab_agent_root: Path) -> dict[str, str]:
    """lab-agent's ``nodes.py`` title-prefix constants (the writer's source of truth)."""
    path = lab_agent_root / "lab_agent" / "nodes.py"
    return _extract_fields(_read(path), ("EXP_SETUP", "EXP_RESULT", "CLOSED"), path)


DOC_MARKER_TOKENS = {
    "ragcluster": "RAGCluster_",
    "robot": "Robot_",
    "setup": "[EXP:Setup",
    "result": "[EXP:Result",
    "idea": "{idea:",
    "CLOSED": "[EXP:Closed]",
}
DOC_BODY_FRAGMENTS = ("Idea: <idea_widget_id>", "Setup: <setup_widget_id>", "Round: <round_number>")


def check_alignment(
    canvus_markers: dict[str, str],
    settings_markers: dict[str, str],
    lab_markers: dict[str, str],
    writer_text: str,
    docs_text: str,
) -> list[str]:
    """Return a list of mismatch diagnostics; empty means fully aligned."""
    problems: list[str] = []

    # 1. canvus-mcp's Settings defaults must exactly mirror its own ExpMarkers
    #    defaults (tools/experiments.py builds ExpMarkers from Settings).
    for key in _MARKER_NAMES:
        if canvus_markers.get(key) != settings_markers.get(key):
            problems.append(
                f"canvus-mcp: ExpMarkers.{key}={canvus_markers.get(key)!r} != "
                f"Settings default {settings_markers.get(key)!r}"
            )

    # 2. lab-agent must write the exact prefixes canvus-mcp's detector matches
    #    against (startswith): EXP_SETUP/"[EXP:Setup vNNN]" and
    #    EXP_RESULT/"[EXP:Result vNNN]" both use bare prefixes (no trailing
    #    bracket), while CLOSED closes its own bracket immediately.
    if lab_markers.get("EXP_SETUP") != canvus_markers.get("setup"):
        problems.append(
            f"setup marker mismatch: canvus-mcp ExpMarkers.setup={canvus_markers.get('setup')!r} "
            f"lab-agent nodes.EXP_SETUP={lab_markers.get('EXP_SETUP')!r}"
        )
    if lab_markers.get("EXP_RESULT") != canvus_markers.get("result"):
        problems.append(
            f"result marker mismatch: canvus-mcp ExpMarkers.result={canvus_markers.get('result')!r} "
            f"lab-agent nodes.EXP_RESULT={lab_markers.get('EXP_RESULT')!r}"
        )
    # CLOSED closes its own bracket immediately (a complete literal, not a
    # versioned bare prefix like setup/result), so this is a direct equality
    # check rather than the prefix-style comparison above.
    if lab_markers.get("CLOSED") != canvus_markers.get("closed"):
        problems.append(
            f"closed marker mismatch: canvus-mcp ExpMarkers.closed={canvus_markers.get('closed')!r} "
            f"lab-agent nodes.CLOSED={lab_markers.get('CLOSED')!r}"
        )

    # 3. Every marker used in code must be documented in experiment-workflow.md
    #    (a docs table token like "[EXP:Setup vNNN]" must start with the code's
    #    bare prefix, so this checks containment, not equality).
    for key, value in {**canvus_markers, **lab_markers}.items():
        expected_token = DOC_MARKER_TOKENS.get(key)
        if expected_token is None:
            continue
        if value not in docs_text:
            problems.append(f"marker {value!r} (from {key}) not found in docs/experiment-workflow.md")
        if not expected_token.startswith(value):
            problems.append(
                f"docs token {expected_token!r} does not match code marker {value!r} for {key!r}"
            )

    # 4. The note-body first-line contract: the lab-agent writer's literal
    #    f-string fragments ("Idea: {idea_id}" etc.) must be present in source
    #    (they live in the orchestrator/orchestrator_support write helpers), and
    #    the documented placeholders ("Idea: <idea_widget_id>" etc.) in the docs.
    writer_fragments = ("Idea: {idea_id}", "Setup: {setup_id}", "Round: {round_index}")
    for fragment in writer_fragments:
        if fragment not in writer_text:
            problems.append(
                f"lab-agent writer source no longer contains the body first-line fragment {fragment!r}"
            )
    for fragment in DOC_BODY_FRAGMENTS:
        if fragment not in docs_text:
            problems.append(f"docs/experiment-workflow.md no longer documents the first line {fragment!r}")

    return problems


def self_test() -> None:
    """Prove ``check_alignment`` actually catches a mismatch, using an in-memory
    fixture only — never touches the real repo files. If this fails, the
    checker itself is untrustworthy and the whole script must fail loudly."""
    good_canvus = {"ragcluster": "RAGCluster_", "robot": "Robot_", "setup": "[EXP:Setup",
                    "result": "[EXP:Result", "idea": "{idea:", "closed": "[EXP:Closed]"}
    good_settings = dict(good_canvus)
    good_lab = {"EXP_SETUP": "[EXP:Setup", "EXP_RESULT": "[EXP:Result", "CLOSED": "[EXP:Closed]"}
    good_orchestrator = 'body = f"Idea: {idea_id}\\nRound: {round_index}\\n\\n...'
    good_orchestrator += ' body = f"Setup: {setup_id}\\nRound: {round_index}\\n\\n...'
    good_docs = (
        "RAGCluster_ ... {idea: ...} ... [EXP:Setup vNNN] ... Robot_ ... "
        "[EXP:Result vNNN] ... [EXP:Closed] ...\n"
        "Idea: <idea_widget_id>\nRound: <round_number>\n"
        "Setup: <setup_widget_id>\nRound: <round_number>\n"
    )

    baseline = check_alignment(good_canvus, good_settings, good_lab, good_orchestrator, good_docs)
    if baseline:
        raise ParityError(f"self-test fixture should be aligned but found: {baseline}")

    # Inject a deliberate drift: lab-agent's writer marker no longer matches
    # canvus-mcp's detector marker (exactly the class of bug this script
    # exists to catch — one app renames a marker and the other silently stops
    # recognising its own notes).
    broken_lab = dict(good_lab)
    broken_lab["EXP_SETUP"] = "[EXP:Setupp"
    problems = check_alignment(good_canvus, good_settings, broken_lab, good_orchestrator, good_docs)
    if not problems:
        raise ParityError("self-test FAILED: check_alignment did not catch an injected marker mismatch")


def main() -> int:
    try:
        self_test()
    except ParityError as exc:
        print(f"SELF-TEST FAILED (checker is untrustworthy): {exc}", file=sys.stderr)
        return 1
    print("self-test: OK (checker catches an injected marker mismatch)")

    canvus_mcp_root = REPO_ROOT / "apps" / "canvus-mcp"
    lab_agent_root = REPO_ROOT / "apps" / "lab-agent"
    docs_path = REPO_ROOT / "docs" / "experiment-workflow.md"

    try:
        canvus_markers = canvus_expmarkers_defaults(canvus_mcp_root)
        settings_markers = canvus_settings_defaults(canvus_mcp_root)
        lab_markers = lab_agent_node_markers(lab_agent_root)
        # The note-body writes live in the orchestrator's write helpers, which
        # now sit in orchestrator_support.py; read both writer modules.
        writer_dir = lab_agent_root / "lab_agent"
        writer_text = "\n".join(
            _read(writer_dir / name) for name in ("orchestrator.py", "orchestrator_support.py")
        )
        docs_text = _read(docs_path)
    except ParityError as exc:
        print(f"PARITY CHECK ERROR: {exc}", file=sys.stderr)
        return 1

    problems = check_alignment(canvus_markers, settings_markers, lab_markers, writer_text, docs_text)
    if problems:
        print("PARITY CHECK FAILED:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    print("parity check: OK (canvus-mcp markers, lab-agent markers, and docs are aligned)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
