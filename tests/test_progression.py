from bot.progression import deload, e1rm, next_weight, scaled_increment


def linear(weight, reps, inc=2.5, step=2.5):
    return next_weight(rule="linear", weight=weight, reps=reps, reps_lo=5, reps_hi=5, amrap=True,
                       increment=inc, step=step, fails=0)


def double(weight, reps, fails=0, inc=5, step=2.5):
    return next_weight(rule="double", weight=weight, reps=reps, reps_lo=8, reps_hi=12, amrap=False,
                       increment=inc, step=step, fails=fails)


def test_linear_success_adds_increment():
    o = linear(60, [5, 5, 7])
    assert (o.weight, o.verdict) == (62.5, "up")


def test_linear_amrap_over_10_doubles_increment():
    o = linear(60, [5, 5, 11])
    assert (o.weight, o.verdict) == (65, "up2")


def test_linear_amrap_exactly_10_is_normal_step():
    assert linear(60, [5, 5, 10]).weight == 62.5


def test_linear_miss_deloads_10_percent_to_plate_step():
    o = linear(60, [5, 4, 3])
    assert (o.weight, o.verdict) == (52.5, "deload")  # 54 -> rounded down to 2.5 step


def test_deload_always_goes_down_at_least_one_step():
    assert deload(20, 2.5) == 17.5
    assert deload(5, 2.5) == 2.5
    assert deload(0, 2.5) == 0


def test_double_top_of_range_goes_up():
    o = double(40, [12, 12, 12])
    assert (o.weight, o.verdict, o.fails) == (45, "up", 0)


def test_double_in_range_holds_and_resets_fails():
    o = double(40, [12, 10, 9], fails=1)
    assert (o.weight, o.verdict, o.fails) == (40, "hold", 0)


def test_double_below_range_twice_deloads():
    first = double(40, [8, 7, 6])
    assert (first.weight, first.verdict, first.fails) == (40, "fail", 1)
    second = double(40, [8, 7, 6], fails=first.fails)
    assert (second.weight, second.verdict, second.fails) == (35, "deload", 0)


def test_scaled_increment_never_below_step():
    assert scaled_increment(5, 2.5, 0.5) == 2.5
    assert scaled_increment(2.5, 2.5, 0.5) == 2.5
    assert scaled_increment(2, 1, 0.5) == 1


def test_e1rm():
    assert e1rm(100, 1) == 100
    assert round(e1rm(60, 8), 1) == 76.0
    assert e1rm(0, 5) == 0
