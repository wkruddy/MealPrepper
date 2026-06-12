from __future__ import annotations

import json
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator


class MemberRole(str, Enum):
    TODDLER = "toddler"
    INFANT = "infant"
    CHILD = "child"
    TEEN = "teen"
    ADULT = "adult"
    SENIOR = "senior"


class MealBlock(str, Enum):
    TODDLER_SCHOOL_LUNCH = "toddler_school_lunch"
    TODDLER_WEEKEND_LUNCH = "toddler_weekend_lunch"
    TODDLER_BREAKFAST = "toddler_breakfast"
    ADULT_BREAKFAST = "adult_breakfast"
    ADULT_LUNCH = "adult_lunch"
    ADULT_DINNER = "adult_dinner"
    INFANT_BLW = "infant_blw"
    BULK_MEAL_PREP = "bulk_meal_prep"


class DayOfWeek(str, Enum):
    MONDAY = "monday"
    TUESDAY = "tuesday"
    WEDNESDAY = "wednesday"
    THURSDAY = "thursday"
    FRIDAY = "friday"
    SATURDAY = "saturday"
    SUNDAY = "sunday"


class FamilyMember(BaseModel):
    id: str
    name: str
    role: MemberRole
    age_years: float | None = None
    age_months: float | None = None
    constraints: dict[str, Any] = Field(default_factory=dict)
    notes: str = ""

    @model_validator(mode="before")
    @classmethod
    def _normalize_constraints(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        constraints = data.get("constraints")
        if isinstance(constraints, list):
            normalized: dict[str, Any] = {}
            for item in constraints:
                if isinstance(item, str):
                    normalized[item] = True
                elif isinstance(item, dict):
                    normalized.update(item)
            data["constraints"] = normalized
        return data


class FamilyProfile(BaseModel):
    timezone: str = "America/New_York"
    members: list[FamilyMember] = Field(default_factory=list)
    meal_blocks: list[str] = Field(default_factory=list)
    schedule: dict[str, str] = Field(default_factory=dict)

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> FamilyProfile:
        members = [FamilyMember.model_validate(m) for m in config.get("members", [])]
        return cls(
            timezone=config.get("timezone", "America/New_York"),
            members=members,
            meal_blocks=config.get("meal_blocks", []),
            schedule=config.get("schedule", {}),
        )

    @classmethod
    def from_db(
        cls,
        *,
        timezone: str,
        members: list[dict[str, Any]],
        meal_blocks: list[str],
        schedule: dict[str, str],
    ) -> FamilyProfile:
        parsed_members = []
        for row in members:
            constraints_raw = row.get("constraints_json") or row.get("constraints") or "{}"
            if isinstance(constraints_raw, str):
                constraints = json.loads(constraints_raw) if constraints_raw else {}
            else:
                constraints = constraints_raw
            parsed_members.append(
                FamilyMember(
                    id=row["id"],
                    name=row.get("display_name") or row.get("name") or row["id"],
                    role=MemberRole(row["role"]),
                    age_years=row.get("age_years"),
                    age_months=row.get("age_months"),
                    constraints=constraints,
                    notes=row.get("notes") or "",
                )
            )
        return cls(
            timezone=timezone,
            members=parsed_members,
            meal_blocks=meal_blocks,
            schedule=schedule,
        )

    def member_ids(self) -> list[str]:
        return [m.id for m in self.members]

    def toddler(self) -> FamilyMember | None:
        return next((m for m in self.members if m.role == MemberRole.TODDLER), None)

    def infant(self) -> FamilyMember | None:
        return next((m for m in self.members if m.role == MemberRole.INFANT), None)

    def adults(self) -> list[FamilyMember]:
        return [m for m in self.members if m.role == MemberRole.ADULT]
