from evals.run import EvalSettings


def test_the_stack_lock_of_a_test_survives_the_clearing_of_the_shell(tmp_path):
    assert EvalSettings().stack_lock == tmp_path / "demo-stack.lock"
