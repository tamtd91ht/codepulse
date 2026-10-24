"""The JSON contract between the LLM and CodePulse (docs/specs/06 "Output JSON")."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Severity = Literal["critical", "high", "medium", "low", "info"]
Category = Literal["security", "bug", "performance", "maintainability", "testing", "style", "docs"]


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
