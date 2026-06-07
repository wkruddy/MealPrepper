from __future__ import annotations

from pydantic import BaseModel, Field

DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

WEEKDAY_SCHOOL_BLOCKS = {
    "monday": [
        "toddler_school_lunch",
        "toddler_breakfast",
        "adult_breakfast",
        "adult_lunch",
        "adult_dinner",
        "infant_blw",
    ],
    "tuesday": [
        "toddler_school_lunch",
        "toddler_breakfast",
        "adult_breakfast",
        "adult_lunch",
        "adult_dinner",
        "infant_blw",
    ],
    "wednesday": [
        "toddler_school_lunch",
        "toddler_breakfast",
        "adult_breakfast",
        "adult_lunch",
        "adult_dinner",
        "infant_blw",
    ],
    "thursday": [
        "toddler_school_lunch",
        "toddler_breakfast",
        "adult_breakfast",
        "adult_lunch",
        "adult_dinner",
        "infant_blw",
    ],
    "friday": [
        "toddler_school_lunch",
        "toddler_breakfast",
        "adult_breakfast",
        "adult_lunch",
        "adult_dinner",
        "infant_blw",
    ],
    "saturday": [
        "toddler_weekend_lunch",
        "toddler_breakfast",
        "adult_breakfast",
        "adult_lunch",
        "adult_dinner",
        "infant_blw",
        "bulk_meal_prep",
    ],
    "sunday": [
        "toddler_weekend_lunch",
        "toddler_breakfast",
        "adult_breakfast",
        "adult_lunch",
        "adult_dinner",
        "infant_blw",
    ],
}


class WeekMealOutline(BaseModel):
    day: str
    meal_block: str
    title: str
    key_ingredients: list[str] = Field(default_factory=list)
    prep_minutes: int = 30
    reuse_of_day: str | None = None
    reuse_of_block: str | None = None
    cook_note: str = ""
