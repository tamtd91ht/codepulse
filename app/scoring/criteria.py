"""Scores by weighted criteria sets (docs/specs/22): a pure function of a review's findings.

Each component of a set scores its own findings with the review formula of docs/specs/08/19
(`review_score` on the subset: severity weights, size factor, verification, caps); the final
score is the weighted sum, capped like the review score when the set says so. The model never
scores: it only places a finding in a component, and a finding it did not place goes by its
category, which every set maps to exactly one component.
"""

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.scoring.formula import (
    CRITICAL_SCORE_CAP,
    MIN_CONFIDENCE_UNVERIFIED,
    SETTLED,
    ScoredFinding,
    counts_for_score,
    penalty,
    review_score,
    rule_for,
)

CATEGORIES: tuple[str, ...] = (
    "security",
    "bug",
    "performance",
    "maintainability",
    "testing",
    "style",
    "docs",
)
CATEGORY_LABELS: dict[str, str] = {
    "security": "Bảo mật",
    "bug": "Lỗi chức năng",
    "performance": "Hiệu năng",
    "maintainability": "Dễ bảo trì",
    "testing": "Kiểm thử",
    "style": "Phong cách mã",
    "docs": "Tài liệu",
}
MIN_COMPONENTS, MAX_COMPONENTS = 2, 12
KEY = re.compile(r"^[a-z][a-z0-9_]{1,30}$")


class Component(BaseModel):
    """One part of a set (docs/specs/22 §1)."""

    model_config = ConfigDict(extra="forbid")

    key: str
    name: str = Field(min_length=1, max_length=60)
    description: str = Field(default="", max_length=500)
    weight: int = Field(ge=1, le=100)
    categories: list[str] = Field(default_factory=list)

    @field_validator("key")
    @classmethod
    def _key(cls, value: str) -> str:
        if not KEY.fullmatch(value):
            raise ValueError("key: chữ thường, số, gạch dưới; 2-31 ký tự, bắt đầu bằng chữ")
        return value

    @field_validator("name", "description")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()

    @field_validator("categories")
    @classmethod
    def _categories(cls, value: list[str]) -> list[str]:
        unknown = sorted(set(value) - set(CATEGORIES))
        if unknown:
            raise ValueError(f"loại finding không có: {', '.join(unknown)}")
        return list(dict.fromkeys(value))


class Components(BaseModel):
    """A set's components, checked as a whole: 2-12 of them, distinct keys, weights summing to
    100, and every finding category in exactly one."""

    model_config = ConfigDict(extra="forbid")

    components: list[Component]
    apply_caps: bool = True

    @model_validator(mode="after")
    def _whole(self) -> "Components":
        comps = self.components
        if not MIN_COMPONENTS <= len(comps) <= MAX_COMPONENTS:
            raise ValueError(f"cần {MIN_COMPONENTS}-{MAX_COMPONENTS} thành phần")
        keys = [c.key for c in comps]
        if len(set(keys)) != len(keys):
            raise ValueError("key thành phần bị trùng")
        names = [c.name.lower() for c in comps]
        if len(set(names)) != len(names):
            raise ValueError("tên thành phần bị trùng")
        total = sum(c.weight for c in comps)
        if total != 100:
            raise ValueError(f"tổng trọng số phải bằng 100 (đang {total})")
        seen: dict[str, str] = {}
        for c in comps:
            for cat in c.categories:
                if cat in seen:
                    raise ValueError(f"loại {cat} nằm ở cả {seen[cat]} và {c.key}")
                seen[cat] = c.key
        missing = [cat for cat in CATEGORIES if cat not in seen]
        if missing:
            raise ValueError(f"loại finding chưa thuộc thành phần nào: {', '.join(missing)}")
        return self


@dataclass(frozen=True)
class CriteriaFinding:
    """A finding as the criteria score reads it."""

    id: int
    category: str
    scored: ScoredFinding
    placed: str | None = None  # the component key the model chose for this set, if any


@dataclass
class ComponentScore:
    key: str
    name: str
    weight: int
    score: float
    penalty: float
    findings: list[dict[str, Any]] = field(default_factory=list)  # {id, source: ai|category}

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "name": self.name,
            "weight": self.weight,
            "score": self.score,
            "penalty": round(self.penalty, 2),
            "findings": self.findings,
        }


@dataclass
class CriteriaScore:
    score: float
    capped: bool
    components: list[ComponentScore]

    def breakdown(self) -> dict[str, Any]:
        return {"capped": self.capped, "components": [c.as_dict() for c in self.components]}


def place(finding: CriteriaFinding, components: Sequence[Component]) -> tuple[str, str]:
    """(component key, source): the model's choice when it is a key of this version, else the
    component holding the finding's category (always one, `Components` sees to it)."""
    keys = {c.key for c in components}
    if finding.placed in keys:
        return str(finding.placed), "ai"
    for c in components:
        if finding.category in c.categories:
            return c.key, "category"
    # A category the set does not know (a new one after the set was made): the heaviest part.
    return max(components, key=lambda c: c.weight).key, "category"


def score(
    findings: Iterable[CriteriaFinding],
    components: Sequence[Component],
    *,
    loc_reviewed: int,
    weights: Mapping[str, float],
    rubric_version: str | None,
    apply_caps: bool = True,
    min_confidence_unverified: float = MIN_CONFIDENCE_UNVERIFIED,
) -> CriteriaScore:
    """docs/specs/22 §2."""
    items = list(findings)
    by_key: dict[str, list[tuple[CriteriaFinding, str]]] = {c.key: [] for c in components}
    for f in items:
        key, source = place(f, components)
        by_key[key].append((f, source))
    rule = rule_for(rubric_version)
    parts: list[ComponentScore] = []
    for c in components:
        mine = by_key[c.key]
        scored = [f.scored for f, _ in mine]
        counted = [s for s in scored if counts_for_score(s, rule, min_confidence_unverified)]
        parts.append(
            ComponentScore(
                key=c.key,
                name=c.name,
                weight=c.weight,
                score=review_score(
                    scored, loc_reviewed, weights, rubric_version, min_confidence_unverified
                ),
                penalty=penalty(counted, weights),
                findings=[{"id": f.id, "source": src} for f, src in mine],
            )
        )
    final = round(sum(p.weight * p.score for p in parts) / 100, 2)
    capped = False
    if apply_caps:
        cap = _cap([f.scored for f in items], rubric_version, min_confidence_unverified)
        if cap is not None and final > cap:
            final, capped = cap, True
    return CriteriaScore(final, capped, parts)


def _cap(
    findings: Sequence[ScoredFinding], rubric_version: str | None, min_confidence: float
) -> float | None:
    """The cap `review_score` would put on this review (critical 50, high 80 from v3)."""
    rule = rule_for(rubric_version)
    counted = [f for f in findings if counts_for_score(f, rule, min_confidence)]
    capping = [f for f in counted if f.verified in SETTLED] if rule.verified_only else counted
    if any(f.severity == "critical" for f in capping):
        return CRITICAL_SCORE_CAP
    if rule.high_cap is not None and any(f.severity == "high" for f in capping):
        return rule.high_cap
    return None


# docs/specs/22 §8: starting points offered by "Tạo bộ", not rows of the database.
TEMPLATES: dict[str, dict[str, Any]] = {
    "chuan_chung": {
        "name": "Chuẩn chung",
        "description": "Cân bằng giữa đúng chức năng, bảo mật và chất lượng mã.",
        "components": [
            {
                "key": "correctness",
                "name": "Đúng chức năng",
                "weight": 30,
                "categories": ["bug"],
                "description": "Logic sai, thiếu xử lý lỗi, sai điều kiện biên, race condition.",
            },
            {
                "key": "security",
                "name": "Bảo mật",
                "weight": 25,
                "categories": ["security"],
                "description": "Lộ secret, phân quyền, injection, dữ liệu nhạy cảm, tách tenant.",
            },
            {
                "key": "performance",
                "name": "Hiệu năng",
                "weight": 10,
                "categories": ["performance"],
                "description": "Truy vấn N+1, vòng lặp nặng, rò rỉ tài nguyên, thiếu phân trang.",
            },
            {
                "key": "maintainability",
                "name": "Dễ bảo trì",
                "weight": 20,
                "categories": ["maintainability", "style"],
                "description": "Cấu trúc, đặt tên, trùng lặp, độ phức tạp, quy ước của team.",
            },
            {
                "key": "testing",
                "name": "Kiểm thử",
                "weight": 10,
                "categories": ["testing"],
                "description": "Thiếu test cho thay đổi, test sai hoặc dễ vỡ.",
            },
            {
                "key": "docs",
                "name": "Tài liệu",
                "weight": 5,
                "categories": ["docs"],
                "description": "Tài liệu, chú thích, hướng dẫn chạy và cấu hình.",
            },
        ],
    },
    "bao_mat_cao": {
        "name": "Bảo mật cao",
        "description": "Cho hệ thống xử lý tiền, dữ liệu cá nhân hoặc nhiều tenant.",
        "components": [
            {
                "key": "security",
                "name": "Bảo mật",
                "weight": 45,
                "categories": ["security"],
                "description": "Lộ secret, phân quyền, injection, dữ liệu nhạy cảm, tách tenant.",
            },
            {
                "key": "correctness",
                "name": "Đúng chức năng",
                "weight": 25,
                "categories": ["bug"],
                "description": "Logic sai, thiếu xử lý lỗi, sai điều kiện biên.",
            },
            {
                "key": "testing",
                "name": "Kiểm thử",
                "weight": 15,
                "categories": ["testing"],
                "description": "Test cho nhánh lỗi và quyền truy cập.",
            },
            {
                "key": "quality",
                "name": "Chất lượng mã",
                "weight": 15,
                "categories": ["performance", "maintainability", "style", "docs"],
                "description": "Hiệu năng, cấu trúc, đặt tên, tài liệu.",
            },
        ],
    },
    "san_pham_moi": {
        "name": "Sản phẩm mới",
        "description": "Ưu tiên chạy đúng và nhanh, vẫn giữ nền bảo mật.",
        "components": [
            {
                "key": "correctness",
                "name": "Đúng chức năng",
                "weight": 40,
                "categories": ["bug", "testing"],
                "description": "Logic đúng, có test cho luồng chính.",
            },
            {
                "key": "security",
                "name": "Bảo mật",
                "weight": 20,
                "categories": ["security"],
                "description": "Lộ secret, phân quyền, injection.",
            },
            {
                "key": "performance",
                "name": "Tốc độ & hiệu năng",
                "weight": 15,
                "categories": ["performance"],
                "description": "Phản hồi nhanh, không truy vấn thừa.",
            },
            {
                "key": "quality",
                "name": "Chất lượng mã",
                "weight": 25,
                "categories": ["maintainability", "style", "docs"],
                "description": "Cấu trúc, đặt tên, tài liệu đủ để người khác tiếp quản.",
            },
        ],
    },
}
