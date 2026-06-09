from mealprepper.skills.recipe_matching import MIN_RECIPE_MATCH_SCORE, recipe_match_score


def test_exact_and_substring_matches_rank_high():
    assert recipe_match_score("hummus veggie pinwheels", "Hummus Veggie Pinwheels") == 1000
    assert recipe_match_score("smash burger", "Classic Smash Burger") >= 500


def test_partial_token_match_is_weak():
    assert recipe_match_score("chicken salad", "Sheet Pan Lemon Herb Chicken") == 50
    assert recipe_match_score("chicken salad", "Sheet Pan Lemon Herb Chicken") < MIN_RECIPE_MATCH_SCORE


def test_all_query_tokens_must_match_for_strong_score():
    assert recipe_match_score("chicken salad", "Chicken Salad") == 1000
    assert recipe_match_score("hummus veggie pinwheels", "Quesadilla Sheet Bake") == 0
