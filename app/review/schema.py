"""The JSON contract between the LLM and CodePulse (docs/specs/06 "Output JSON")."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

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

    @model_validator(mode="after")
    def _ordered_lines(self) -> "LLMFinding":
        if self.line_end < self.line_start:
            self.line_start, self.line_end = self.line_end, self.line_start
        return self


class LLMReview(BaseModel):
    model_config = ConfigDict(extra="ignore")

    summary: str
    # Reference only: the stored score comes from the formula in docs/specs/08.
    score: int = Field(ge=0, le=100)
    score_breakdown: dict[str, int] = Field(default_factory=dict)
    findings: list[LLMFinding] = Field(default_factory=list)


def output_json_schema() -> dict[str, Any]:
    """Schema passed to the provider's structured-output mode."""
    return LLMReview.model_json_schema()
