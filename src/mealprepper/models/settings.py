from __future__ import annotations

from pydantic import BaseModel, Field


class MacroGoals(BaseModel):
    """Per-member or household-default macro targets."""

    track_macros: bool = False
    protein_g: float | None = None
    carbs_g: float | None = None
    fat_g: float | None = None
    calories: float | None = None
    notes: str = ""


class MacroTrackingConfig(BaseModel):
    """Household-level macro tracking settings."""

    enabled: bool = False
    show_in_daily: bool = True
    show_in_weekly: bool = False
    default_protein_g: float | None = None
    default_carbs_g: float | None = None
    default_fat_g: float | None = None
    default_calories: float | None = None
    data_source: str = "llm_estimate"  # llm_estimate | usda | manual


class NutritionInfo(BaseModel):
    """Per-serving nutrition for a recipe."""

    calories: float | None = None
    protein_g: float | None = None
    carbs_g: float | None = None
    fat_g: float | None = None
    fiber_g: float | None = None
    servings: int = 1
    source: str = "llm_estimate"  # llm_estimate | usda | manual
    confidence: str = "medium"  # low | medium | high
    per_ingredient: dict[str, dict[str, float]] = Field(default_factory=dict)


class DailyMacroSummary(BaseModel):
    """Planned or actual macros for one member on one day."""

    member_id: str
    log_date: str
    protein_g: float = 0.0
    carbs_g: float = 0.0
    fat_g: float = 0.0
    calories: float = 0.0
    meals: list[str] = Field(default_factory=list)
