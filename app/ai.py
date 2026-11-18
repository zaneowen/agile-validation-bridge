"""Calls Claude to rate a story and draft test cases.

If ANTHROPIC_API_KEY is not set, a deterministic mock is used so the app can be
demoed and tested offline.
"""

from __future__ import annotations

import os
import re

from .prompts import ANALYSIS_TOOL, SYSTEM_PROMPT, build_user_message

DEFAULT_MODEL = "claude-sonnet-5-5"

HIGH_RISK_WORDS = (
    "signature", "e-sign", "audit trail", "batch", "release", "dose", "patient",
    "electronic record", "calculation", "deviation", "capa", "lims",
)
LOW_RISK_WORDS = ("color", "colour", "font", "logo", "tooltip", "layout", "dark mode", "typo")


def use_mock() -> bool:
    return not os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("AVB_MOCK_AI") == "1"


def analyze_story(title: str, description: str, acceptance_criteria: list[str]) -> dict:
    if use_mock():
        return _mock_analysis(title, description, acceptance_criteria)

    import anthropic

    client = anthropic.Anthropic()
    response = client.messages.create(
        model=os.environ.get("AVB_MODEL", DEFAULT_MODEL),
        max_tokens=4000,
        system=SYSTEM_PROMPT,
        tools=[ANALYSIS_TOOL],
        tool_choice={"type": "tool", "name": ANALYSIS_TOOL["name"]},
        messages=[
            {"role": "user", "content": build_user_message(title, description, acceptance_criteria)}
        ],
    )
    for block in response.content:
        if block.type == "tool_use":
            return block.input
    raise RuntimeError("Model did not return a structured analysis.")


def _mock_analysis(title: str, description: str, acceptance_criteria: list[str]) -> dict:
    text = f"{title} {description} {' '.join(acceptance_criteria)}".lower()
    if any(w in text for w in HIGH_RISK_WORDS):
        risk, approach = "High", "scripted"
    elif any(w in text for w in LOW_RISK_WORDS):
        risk, approach = "Low", "ad hoc"
    else:
        risk, approach = "Medium", "unscripted"

    part11 = bool(re.search(r"signature|e-sign|electronic record|audit trail", text))
    criteria = acceptance_criteria or ["Story behaves as described"]
    cases = []
    for i, criterion in enumerate(criteria, start=1):
        case = {
            "id": f"TC-{i:02d}",
            "title": f"Verify: {criterion[:60]}",
            "covers_criterion": criterion,
            "pass_condition": f"The system meets the criterion: {criterion}",
        }
        if approach == "scripted":
            case["steps"] = [
                {"action": "Log in as a user with the required role", "expected": "Home page loads"},
                {"action": f"Perform the action described: {criterion}", "expected": criterion},
                {"action": "Capture a screenshot as objective evidence", "expected": "Evidence saved"},
            ]
        else:
            case["steps"] = []
            case["scenario"] = f"Exercise the feature around: {criterion}. Try edge cases."
        cases.append(case)

    return {
        "risk": risk,
        "risk_rationale": f"[Mock AI] Rated {risk} from keywords in the story. "
        "Set ANTHROPIC_API_KEY for a real assessment.",
        "part11_relevant": part11,
        "test_approach": approach,
        "test_cases": cases,
        "open_questions": [] if acceptance_criteria else ["Story has no acceptance criteria."],
    }
