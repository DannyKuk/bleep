import pytest


@pytest.fixture(autouse=True)
def own_stack_lock(tmp_path, monkeypatch):
    """Every `EvalSettings` a test builds locks a file of its own, never the machine's: a test
    must not wait on a real eval round, nor write under the home folder."""
    monkeypatch.setenv("EVAL_STACK_LOCK", str(tmp_path / "demo-stack.lock"))
