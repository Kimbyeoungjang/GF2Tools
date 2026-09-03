from gfl2tool.cooking import cooking_progress, exact_completion_options, ingredient_requirements, load_permanent_dishes


def test_permanent_cooking_reference_has_all_stat_targets():
    dishes = load_permanent_dishes()
    assert len(dishes) == 10
    effects = {row["effect"] for row in dishes}
    assert {"공격력", "방어력", "체력", "물리 보너스", "연소 보너스", "탁류 보너스", "빙결 보너스", "전도 보너스", "산성 보너스", "모든 위상 속성 보너스"} == effects
    assert sum(len(row["recipes"]) for row in dishes) == 89


def test_cooking_progress_uses_four_and_five_points_and_level_breakpoints():
    progress = cooking_progress(2, 1)
    assert progress.points == 13
    assert progress.level == 1
    assert progress.next_threshold == 25
    assert progress.remaining_to_next == 12
    assert progress.remaining_total == 37
    assert progress.min_more == 8
    assert progress.max_more == 10


def test_cooking_progress_caps_at_fifty():
    progress = cooking_progress(20, 20)
    assert progress.points == 50
    assert progress.level == 3
    assert progress.remaining_total == 0
    assert progress.min_more == 0
    assert progress.max_more == 0


def test_ingredient_requirements_preserve_duplicate_ingredients():
    totals = ingredient_requirements(["고구마", "고구마", "양파"], 7)
    assert totals == {"고구마": 14, "양파": 7}


def test_exact_completion_options_are_enough_and_minimal_first():
    options = exact_completion_options(9)
    great, normal, excess = options[0]
    assert great + normal == 2
    assert great * 5 + normal * 4 >= 9
    assert excess == great * 5 + normal * 4 - 9


def test_level_and_fullness_state_converts_to_cumulative_points():
    from gfl2tool.cooking import cooking_progress_from_state, points_from_level_fullness

    assert points_from_level_fullness(1, 5) == 15
    assert points_from_level_fullness(2, 7) == 32
    progress = cooking_progress_from_state(1, 5)
    assert progress.points == 15
    assert progress.level == 1
    assert progress.remaining_to_next == 10
    assert progress.remaining_total == 35


def test_level_fullness_plus_new_cooking_results_are_combined():
    from gfl2tool.cooking import cooking_progress_from_state

    progress = cooking_progress_from_state(1, 5, normal_successes=1, great_successes=2)
    assert progress.points == 29
    assert progress.level == 2
    assert progress.remaining_to_next == 21
