from __future__ import annotations

from mealprepper.models.family import FamilyProfile, MemberRole
from mealprepper.services.family_resolver import FamilyContext


def _format_member_constraints(member) -> str:
    parts: list[str] = []
    constraints = member.constraints or {}
    if constraints.get("no_spicy") or "no_spicy" in constraints:
        parts.append("no spicy")
    if constraints.get("dinner_by"):
        parts.append(f"dinner ready by {constraints['dinner_by']}")
    if constraints.get("max_dinner_prep_minutes"):
        parts.append(f"max {constraints['max_dinner_prep_minutes']}min dinner prep")
    if constraints.get("quick_lunches"):
        parts.append("quick lunches")
    if constraints.get("baby_led_weaning"):
        parts.append("BLW finger foods")
    if constraints.get("variable_breakfast"):
        parts.append("variable breakfast")
    if constraints.get("bulk_prep_lunch"):
        parts.append("bulk-preppable lunches")
    if member.notes:
        parts.append(member.notes)
    return "; ".join(parts) if parts else "no special constraints"


def _member_roles_summary(profile: FamilyProfile) -> list[str]:
    lines: list[str] = []
    for role in (
        MemberRole.TODDLER,
        MemberRole.INFANT,
        MemberRole.CHILD,
        MemberRole.TEEN,
        MemberRole.ADULT,
        MemberRole.SENIOR,
    ):
        for member in (m for m in profile.members if m.role == role):
            age = ""
            if member.age_months is not None:
                age = f", {member.age_months}mo"
            elif member.age_years is not None:
                age = f", {member.age_years}y"
            lines.append(
                f"- {member.name} ({role.value}{age}): {_format_member_constraints(member)}"
            )
    return lines


def _max_dinner_prep_minutes(profile: FamilyProfile) -> int:
    toddler = profile.toddler()
    if toddler and toddler.constraints.get("max_dinner_prep_minutes"):
        return int(toddler.constraints["max_dinner_prep_minutes"])
    return 45


def meal_finder_system_prompt(ctx: FamilyContext) -> str:
    profile = ctx.profile
    planning = ctx.planning
    role_parts: list[str] = []
    toddler = profile.toddler()
    infant = profile.infant()
    adults = profile.adults()
    if toddler:
        summary = _format_member_constraints(toddler)
        role_parts.append(f"a toddler ({summary})" if summary else "a toddler")
    if infant:
        role_parts.append("an infant doing baby-led weaning")
    if adults:
        count = len(adults)
        role_parts.append(f"{count} adult{'s' if count > 1 else ''}")
    household = ", ".join(role_parts) if role_parts else "household members"
    max_prep = _max_dinner_prep_minutes(profile)
    preference_lines: list[str] = []
    if planning.dietary_household:
        preference_lines.append(f"Household diet: {', '.join(planning.dietary_household)}")
    if planning.nutrition_goal:
        preference_lines.append(f"Nutrition goal: {planning.nutrition_goal.replace('_', ' ')}")
    if planning.cuisine_preferences:
        preference_lines.append(f"Preferred cuisines: {', '.join(planning.cuisine_preferences)}")
    if planning.food_likes:
        preference_lines.append(f"Foods they enjoy: {planning.food_likes}")
    if planning.foods_avoid:
        preference_lines.append(f"Avoid: {planning.foods_avoid}")
    preferences = "\n".join(preference_lines)

    return f"""You are the MealPrepper meal finder for a family with {household}. Users are competent cooks.

Return practical, real meals — not generic placeholders. Prioritize easy weeks: fewer unique
ingredients per day, shared components across nights (cook extra rice Tuesday for Wednesday's bowl),
and Saturday bulk prep that feeds weekday dinners.
Reuse ingredients across the week when sensible, but do NOT repeat the same meal title more than
twice per meal block in one week.
Adult dinners must be ready within {max_prep} minutes total prep+cook.{f"""

Preferences:
{preferences}""" if preferences else ""}"""


def weekly_meals_system_prompt(ctx: FamilyContext) -> str:
    profile = ctx.profile
    planning = ctx.planning
    blocks = ", ".join(block.replace("_", " ") for block in profile.meal_blocks)
    if not blocks:
        blocks = "school/weekend lunches, breakfasts, dinners, BLW, bulk prep"
    members = "\n".join(_member_roles_summary(profile)) or "- (no members configured)"

    constraint_lines: list[str] = []
    toddler = profile.toddler()
    infant = profile.infant()
    if toddler:
        parts: list[str] = []
        constraints = toddler.constraints or {}
        if constraints.get("no_spicy") or "no_spicy" in constraints:
            parts.append("no spicy")
        if constraints.get("dinner_by"):
            parts.append(f"dinner ready by {constraints['dinner_by']}")
        if constraints.get("max_dinner_prep_minutes"):
            parts.append(f"max {constraints['max_dinner_prep_minutes']}min dinner prep")
        if constraints.get("quick_lunches"):
            parts.append("quick lunches")
        if parts:
            constraint_lines.append(f"- Toddler: {'; '.join(parts)}")
    if infant:
        constraint_lines.append("- Infant: BLW finger foods with prep guidance")
    if profile.adults():
        constraint_lines.append(
            "- Adults: variable breakfast, bulk-preppable lunches, shared quick dinners"
        )
    constraint_lines.append("- Family are competent cooks; prefer practical real meals")
    if planning.dietary_household:
        constraint_lines.append(f"- Household diet: {', '.join(planning.dietary_household)}")
    if planning.nutrition_goal:
        constraint_lines.append(
            f"- Nutrition goal: {planning.nutrition_goal.replace('_', ' ')} "
            "(bias portions and protein accordingly)"
        )
    if planning.cuisine_preferences:
        constraint_lines.append(f"- Preferred cuisines: {', '.join(planning.cuisine_preferences)}")
    if planning.food_likes:
        constraint_lines.append(f"- Foods they enjoy: {planning.food_likes}")
    if planning.foods_avoid:
        constraint_lines.append(f"- Avoid or minimize: {planning.foods_avoid}")

    return f"""You are the Weekly Meals Agent for MealPrepper.

Your responsibilities:
- Find age-appropriate meals for household members
- Organize the week covering all meal blocks ({blocks})
- Synergize ingredients to minimize waste and overlap with the Grocery List Agent
- Validate leftover timing so seafood and other short-life foods are not reused too late
- Ensure toddler meals cover carb, protein, veggie, fruit, fat (via FoodGroupsSkill)
- Track preferences and feedback from past weeks

Household:
{members}

Constraints:
{chr(10).join(constraint_lines)}

Available tools: find_meals, organize_week, validate_shelf_life, synergize_ingredients, load_preferences, save_plan"""
