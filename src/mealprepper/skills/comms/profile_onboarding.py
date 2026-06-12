from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from mealprepper.services.family_settings import DIET_CONSTRAINT_KEYS, FamilySettingsService

PROFILE_ONBOARDING_COMPLETE = "complete"

PROFILE_ONBOARDING_STEPS: tuple[str, ...] = ("diet", "fitness", "cuisines", "eaters")

SKIP_WORDS = frozenset({"skip", "none", "no", "n/a", "na", "nothing"})

DIET_ALIASES: dict[str, str] = {
    "gluten free": "gluten_free",
    "gluten-free": "gluten_free",
    "glutenfree": "gluten_free",
    "dairy free": "dairy_free",
    "dairy-free": "dairyfree",
    "dairyfree": "dairy_free",
    "no dairy": "dairy_free",
    "no spicy": "no_spicy",
    "nut allergy": "nut_allergy",
    "nut allergies": "nut_allergy",
    "peanut": "nut_allergy",
    "tree nut": "nut_allergy",
    "shellfish": "shellfish_allergy",
    "fish allergy": "shellfish_allergy",
}

FITNESS_ALIASES: dict[str, str] = {
    "maintain": "maintain",
    "maintenance": "maintain",
    "maintaining": "maintain",
    "cut": "cut",
    "cutting": "cut",
    "fat loss": "cut",
    "lose weight": "cut",
    "weight loss": "cut",
    "bulk": "bulk",
    "bulking": "bulk",
    "muscle gain": "bulk",
    "gain muscle": "bulk",
    "weightlifting": "weightlifting",
    "weight lifting": "weightlifting",
    "lifting": "weightlifting",
    "lift": "weightlifting",
    "strength": "weightlifting",
    "gym": "weightlifting",
}


@dataclass(frozen=True)
class ProfileQuestion:
    step: str
    prompt: str


PROFILE_QUESTIONS: dict[str, ProfileQuestion] = {
    "diet": ProfileQuestion(
        step="diet",
        prompt=(
            "Any dietary restrictions or allergies?\n\n"
            "Examples: `gluten free`, `vegetarian`, `dairy free`, `no spicy`, "
            "`nut allergy — no peanuts`\n"
            "Reply `skip` if none."
        ),
    ),
    "fitness": ProfileQuestion(
        step="fitness",
        prompt=(
            "What's your main nutrition goal?\n\n"
            "Reply with one: `maintain`, `cut`, `bulk`, or `weightlifting`\n"
            "(`cut` = fat loss, `bulk` = muscle gain, `weightlifting` = high-protein strength focus)\n"
            "Reply `skip` to decide later."
        ),
    ),
    "cuisines": ProfileQuestion(
        step="cuisines",
        prompt=(
            "What kinds of food do you enjoy?\n\n"
            "Examples: `Mexican, Mediterranean, comfort food, Asian stir-fry`\n"
            "You can also mention foods to avoid, e.g. `love Italian, avoid seafood`\n"
            "Reply `skip` if you're open to anything."
        ),
    ),
    "eaters": ProfileQuestion(
        step="eaters",
        prompt=(
            "Who should meals be planned for?\n\n"
            "Examples:\n"
            "• `just me`\n"
            "• `me and my partner`\n"
            "• `family of 4` (2 adults + 2 kids)\n"
            "• `2 adults and a toddler`\n"
            "Reply `skip` to start with one adult."
        ),
    ),
}

PROFILE_ONBOARDING_INTRO = (
    "A few quick questions so plans fit your household. "
    "Reply in this thread — say `skip setup` anytime to finish later."
)

PROFILE_ONBOARDING_DONE = (
    "You're all set! Your preferences are saved.\n\n"
    "Try `settings` to review, `plan-week` to generate a week, or `help` for commands."
)


def next_profile_step(step: str) -> str | None:
    try:
        index = PROFILE_ONBOARDING_STEPS.index(step)
    except ValueError:
        return None
    if index + 1 >= len(PROFILE_ONBOARDING_STEPS):
        return PROFILE_ONBOARDING_COMPLETE
    return PROFILE_ONBOARDING_STEPS[index + 1]


def question_for_step(step: str) -> str:
    question = PROFILE_QUESTIONS.get(step)
    if not question:
        return ""
    return question.prompt


def is_skip_answer(text: str) -> bool:
    cleaned = text.strip().lower().strip(".,!")
    if not cleaned:
        return True
    if cleaned in SKIP_WORDS:
        return True
    return cleaned.startswith("skip")


def primary_name_from_household(household_name: str) -> str:
    name = household_name.strip()
    for suffix in ("'s family", "'s house", " family", " household", " house"):
        if name.lower().endswith(suffix):
            name = name[: -len(suffix)].strip()
    if name.lower().endswith("'s"):
        name = name[:-2].strip()
    first = name.split()[0] if name else ""
    return first or "Me"


def parse_diet_answer(text: str) -> tuple[list[str], str]:
    if is_skip_answer(text):
        return [], ""
    lowered = text.lower()
    found: list[str] = []
    for alias, key in DIET_ALIASES.items():
        if alias in lowered and key not in found:
            found.append(key)
    for key in DIET_CONSTRAINT_KEYS:
        if re.search(rf"\b{re.escape(key.replace('_', ' '))}\b", lowered) or key in lowered:
            if key not in found:
                found.append(key)
    notes = text.strip()
    return found, notes


def parse_fitness_answer(text: str) -> str:
    if is_skip_answer(text):
        return ""
    lowered = text.lower().strip()
    for alias, goal in sorted(FITNESS_ALIASES.items(), key=lambda item: -len(item[0])):
        if alias in lowered:
            return goal
    first = lowered.split()[0] if lowered.split() else ""
    if first in {"maintain", "cut", "bulk", "weightlifting"}:
        return first
    return lowered.replace(" ", "_")


def parse_cuisine_answer(text: str) -> tuple[list[str], str, str]:
    if is_skip_answer(text):
        return [], "", ""
    raw = text.strip()
    avoid = ""
    likes_part = raw
    avoid_match = re.search(r"\bavoid[:\s]+(.+)$", raw, flags=re.IGNORECASE)
    if avoid_match:
        avoid = avoid_match.group(1).strip().rstrip(".")
        likes_part = raw[: avoid_match.start()].strip().rstrip(",").strip()
    cuisines: list[str] = []
    if likes_part:
        for part in re.split(r"[,;/]| and ", likes_part):
            cleaned = part.strip(" .")
            cleaned = re.sub(r"^(love|like|likes|enjoy)\s+", "", cleaned, flags=re.IGNORECASE)
            if cleaned:
                cuisines.append(cleaned)
    return cuisines, raw, avoid


def parse_eaters_answer(text: str, household_name: str) -> list[tuple[str, str]]:
    if is_skip_answer(text):
        return [(primary_name_from_household(household_name), "adult")]

    lowered = text.lower()
    primary = primary_name_from_household(household_name)

    if re.search(r"\bjust me\b|\bonly me\b|^me$", lowered):
        return [(primary, "adult")]
    if "partner" in lowered or "couple" in lowered or "spouse" in lowered:
        return [(primary, "adult"), ("Partner", "adult")]
    if "toddler" in lowered and "adult" in lowered:
        count_adults = 2 if "2 adult" in lowered or "two adult" in lowered else 1
        members = [(primary, "adult")]
        if count_adults > 1:
            members.append(("Adult 2", "adult"))
        members.append(("Toddler", "toddler"))
        return members
    if "family of 4" in lowered or "4 people" in lowered or "four people" in lowered:
        return [
            (primary, "adult"),
            ("Adult 2", "adult"),
            ("Child 1", "child"),
            ("Child 2", "child"),
        ]
    if "family of 3" in lowered or "3 people" in lowered:
        return [(primary, "adult"), ("Adult 2", "adult"), ("Child", "child")]
    if re.search(r"\b2 adults\b|\btwo adults\b", lowered):
        return [(primary, "adult"), ("Adult 2", "adult")]
    if "kid" in lowered or "child" in lowered:
        return [(primary, "adult"), ("Child", "child")]

    return [(primary, "adult")]


class ProfileOnboardingFlow:
    """Persisted post-signup questionnaire stored on family_settings."""

    def __init__(self, settings_service: FamilySettingsService) -> None:
        self.settings_service = settings_service

    def start(self, family_id: str, *, household_name: str, thread_ts: str = "") -> BotReplyText:
        self.settings_service.start_profile_onboarding(
            family_id,
            household_name=household_name,
            thread_ts=thread_ts,
        )
        return BotReplyText(
            f"{PROFILE_ONBOARDING_INTRO}\n\n{question_for_step('diet')}",
        )

    def handle_answer(
        self,
        family_id: str,
        step: str,
        text: str,
        data: dict[str, Any],
    ) -> BotReplyText:
        command, _ = _split_command(text)
        if text.strip().lower() in {"skip setup", "skip onboarding"} or command in {
            "skip-setup",
            "skip_setup",
        }:
            self.settings_service.complete_profile_onboarding(family_id)
            return BotReplyText(PROFILE_ONBOARDING_DONE)

        household_name = str(data.get("household_name") or "Household")
        if step == "diet":
            diets, notes = parse_diet_answer(text)
            self.settings_service.apply_profile_diet_answer(family_id, diets, notes)
        elif step == "fitness":
            goal = parse_fitness_answer(text)
            self.settings_service.apply_profile_fitness_answer(family_id, goal)
        elif step == "cuisines":
            cuisines, likes, avoid = parse_cuisine_answer(text)
            self.settings_service.apply_profile_cuisine_answer(
                family_id,
                cuisines,
                likes=likes,
                avoid=avoid,
            )
        elif step == "eaters":
            members = parse_eaters_answer(text, household_name)
            self.settings_service.apply_profile_eaters_answer(family_id, members)
        else:
            self.settings_service.complete_profile_onboarding(family_id)
            return BotReplyText(PROFILE_ONBOARDING_DONE)

        next_step = next_profile_step(step)
        if not next_step or next_step == PROFILE_ONBOARDING_COMPLETE:
            self.settings_service.complete_profile_onboarding(family_id)
            return BotReplyText(PROFILE_ONBOARDING_DONE)

        self.settings_service.set_profile_onboarding_step(family_id, next_step, data)
        return BotReplyText(question_for_step(next_step))


@dataclass
class BotReplyText:
    text: str
    success: bool = True


def _split_command(text: str) -> tuple[str, str]:
    parts = text.strip().split(maxsplit=1)
    command = parts[0].lower() if parts else ""
    args = parts[1] if len(parts) > 1 else ""
    return command, args
