"""The JSON contract between the LLM and CodePulse (docs/specs/06 "Output JSON"; docs/specs/19
§4.2 for the agentic pass, which adds `rule_id`, `evidence`, `applied_rules` and `project_fit`).
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.review.prepare import redact

Severity = Literal["critical", "high", "medium", "low", "info"]
MAX_FINDING_LINES = 200
Category = Literal["security", "bug", "performance", "maintainability", "testing", "style", "docs"]
# docs/specs/19 §4.2: a finding without a rule is charged to the rubric's last question.
DEFAULT_RULE_ID = "rubric:4"
# A critical/high finding the agent could not point at (no evidence) is at most this sure.
MAX_CONFIDENCE_WITHOUT_EVIDENCE = 0.6


class LLMFinding(BaseModel):
    model_config = ConfigDict(extra="ignore")

    severity: Severity
    category: Category
    file_path: str = Field(min_length=1)
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=200)
    message: str = Field(min_length=1)
    suggestion: str = ""
    confidence: float = Field(ge=0, le=1)
    # Heading of the team rule this finding breaks (docs/specs/17), else null.
    rule_id: str | None = None

    @field_validator("rule_id", mode="before")
    @classmethod
    def _short_rule(cls, value: object) -> str | None:
        # Advisory field: clip rather than fail the whole (paid) review on a long value.
        return str(value).strip()[:120] or None if value is not None else None

    @model_validator(mode="after")
    def _ordered_lines(self) -> "LLMFinding":
        if self.line_end < self.line_start:
            self.line_start, self.line_end = self.line_end, self.line_start
        # A finding spans a hunk, not a file: an absurd range would be iterated later.
        self.line_end = min(self.line_end, self.line_start + MAX_FINDING_LINES)
        return self


class Intent(BaseModel):
    """Design & intent notes (docs/specs/17): advisory, never scored."""

    model_config = ConfigDict(extra="ignore")

    summary: str = ""
    matches_intent: bool | None = None
    altitude: Literal["over", "right", "under"] | None = None
    concerns: list[str] = Field(default_factory=list)

    # Never fail a review over advisory text: clip everything instead of rejecting it.
    @field_validator("summary", mode="before")
    @classmethod
    def _short_summary(cls, value: object) -> str:
        return str(value)[:1500] if value is not None else ""

    @field_validator("altitude", mode="before")
    @classmethod
    def _known_altitude(cls, value: object) -> str | None:
        return value if value in ("over", "right", "under") else None

    @field_validator("concerns", mode="before")
    @classmethod
    def _short_concerns(cls, value: object) -> list[str]:
        if not isinstance(value, list):
            return []
        cleaned = [str(c).strip()[:300] for c in value if c is not None and str(c).strip()]
        return cleaned[:5]


class LLMReview(BaseModel):
    model_config = ConfigDict(extra="ignore")

    summary: str
    # Reference only: the stored score comes from the formula in docs/specs/08.
    score: int = Field(ge=0, le=100)
    score_breakdown: dict[str, int] = Field(default_factory=dict)
    findings: list[LLMFinding] = Field(default_factory=list)
    intent: Intent | None = None


def output_json_schema() -> dict[str, Any]:
    """Schema passed to the provider's structured-output mode."""
    return LLMReview.model_json_schema()


# -- agentic pass (docs/specs/19 §4.2) -------------------------------------------------------------


class AppliedRule(BaseModel):
    """What the agent says about one rule it was given: does it concern this change, where."""

    model_config = ConfigDict(extra="ignore")

    rule_id: str = Field(min_length=1, max_length=120)
    applies: bool = False
    where: str = ""
    why: str = ""

    @field_validator("rule_id", mode="before")
    @classmethod
    def _short_id(cls, value: object) -> str:
        return str(value).strip()[:120] if value is not None else ""

    @field_validator("applies", mode="before")
    @classmethod
    def _bool(cls, value: object) -> bool:
        if isinstance(value, str):
            return value.strip().lower() in ("true", "yes", "1")
        return bool(value)

    @field_validator("where", "why", mode="before")
    @classmethod
    def _short_text(cls, value: object) -> str:
        return redact(str(value)).strip()[:200] if value is not None else ""


class AgentFinding(LLMFinding):
    """A finding of the agentic pass: `rule_id` is mandatory (missing -> rubric:4) and a
    critical/high finding without `evidence` is capped at MAX_CONFIDENCE_WITHOUT_EVIDENCE."""

    rule_id: str = DEFAULT_RULE_ID  # narrower than LLMFinding: never None here
    evidence: str = ""  # file:lines (or a short quote) that shows the problem

    @field_validator("rule_id", mode="before")
    @classmethod
    def _rule_or_default(cls, value: object) -> str:
        text = str(value).strip()[:120] if value is not None else ""
        return text or DEFAULT_RULE_ID

    @field_validator("evidence", mode="before")
    @classmethod
    def _short_evidence(cls, value: object) -> str:
        return redact(str(value)).strip()[:500] if value is not None else ""

    @model_validator(mode="after")
    def _evidence_backs_severity(self) -> "AgentFinding":
        if self.severity in ("critical", "high") and not self.evidence:
            self.confidence = min(self.confidence, MAX_CONFIDENCE_WITHOUT_EVIDENCE)
        return self


class AgentReview(LLMReview):
    """The agentic pass answers the first-pass contract plus the rules it applied and how the
    change fits the project. Everything it says may quote repository files: redacted."""

    score: int = Field(default=0, ge=0, le=100)
    findings: list[AgentFinding] = Field(default_factory=list)  # type: ignore[assignment]
    applied_rules: list[AppliedRule] = Field(default_factory=list)
    project_fit: list[str] = Field(default_factory=list)

    @field_validator("summary", mode="before")
    @classmethod
    def _redacted_summary(cls, value: object) -> str:
        return redact(str(value))[:4000] if value is not None else ""

    @field_validator("applied_rules", mode="before")
    @classmethod
    def _rules_list(cls, value: object) -> list[Any]:
        if not isinstance(value, list):
            return []
        return [v for v in value if isinstance(v, dict) and v.get("rule_id")][:100]

    @field_validator("project_fit", mode="before")
    @classmethod
    def _fit(cls, value: object) -> list[str]:
        if not isinstance(value, list):
            return []
        return [redact(str(v)).strip()[:300] for v in value if v is not None and str(v).strip()][:6]


def agent_json_schema() -> dict[str, Any]:
    return AgentReview.model_json_schema()
