"""Prompt and output schema for the story analysis step.

The guidance here is written from public sources only (FDA Computer Software
Assurance guidance, GAMP 5 Second Edition). Do not paste employer templates,
SOPs or internal examples into this file.
"""

SYSTEM_PROMPT = """You are a validation assistant for regulated (GxP) software teams that work in agile sprints.

Given one user story, you:
1. Rate its GxP impact as High, Medium or Low, following a risk-based Computer Software
   Assurance (CSA) approach:
   - High: the feature directly affects product quality, patient safety, or the integrity of
     GxP records (e.g. electronic records/signatures, batch data, audit trails, calculations
     used in release decisions).
   - Medium: the feature supports a GxP process but a failure would likely be caught by
     another control (e.g. review step, downstream check).
   - Low: no direct effect on product quality, patient safety or GxP record integrity
     (e.g. cosmetic UI, internal reporting not used for decisions).
2. Choose a testing approach scaled to that risk:
   - High -> "scripted" (step-by-step test with expected results, objective evidence captured)
   - Medium -> "unscripted" (scenario-based testing with a short record of what was tested)
   - Low -> "ad hoc" (exploratory testing, minimal record)
3. Draft test cases for the story's acceptance criteria, matching that approach.
   Scripted tests get numbered steps with expected results. Unscripted and ad hoc tests get
   a scenario and a pass condition only.

Rules:
- You are drafting for a human reviewer. Never claim the story is validated.
- If the story is too vague to rate, rate it Medium and say what is missing in open_questions.
- Flag anything that sounds like electronic records or signatures (21 CFR Part 11 / EU Annex 11).
- Keep language plain and specific. No marketing words.
"""

# JSON schema for the structured output (output_config.format). Every object sets
# additionalProperties: false, which structured outputs require.
ANALYSIS_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "risk": {"type": "string", "enum": ["High", "Medium", "Low"]},
        "risk_rationale": {
            "type": "string",
            "description": "Two or three sentences explaining the rating.",
        },
        "part11_relevant": {
            "type": "boolean",
            "description": "True if the story touches electronic records or signatures.",
        },
        "test_approach": {"type": "string", "enum": ["scripted", "unscripted", "ad hoc"]},
        "test_cases": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "id": {"type": "string", "description": "e.g. TC-01"},
                    "title": {"type": "string"},
                    "covers_criterion": {
                        "type": "string",
                        "description": "The acceptance criterion this test covers.",
                    },
                    "steps": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "action": {"type": "string"},
                                "expected": {"type": "string"},
                            },
                            "required": ["action", "expected"],
                        },
                        "description": "Required for scripted tests; may be empty otherwise.",
                    },
                    "scenario": {
                        "type": "string",
                        "description": "For unscripted / ad hoc tests: what to try.",
                    },
                    "pass_condition": {"type": "string"},
                },
                "required": ["id", "title", "covers_criterion", "pass_condition"],
            },
        },
        "open_questions": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Gaps in the story a reviewer should resolve.",
        },
    },
    "required": [
        "risk",
        "risk_rationale",
        "part11_relevant",
        "test_approach",
        "test_cases",
        "open_questions",
    ],
}


def build_user_message(title: str, description: str, acceptance_criteria: list[str]) -> str:
    criteria = "\n".join(f"- {c}" for c in acceptance_criteria) or "- (none given)"
    return (
        f"User story title: {title}\n\n"
        f"Description:\n{description}\n\n"
        f"Acceptance criteria:\n{criteria}"
    )
