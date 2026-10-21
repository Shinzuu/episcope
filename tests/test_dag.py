"""ENG-4: the pipeline DAG runs stages in dependency order, retries a failed stage and stops when it keeps failing."""

import importlib.util
import pathlib

import pytest

spec = importlib.util.spec_from_file_location("dag", pathlib.Path(__file__).resolve().parents[1] / "scripts" / "dag.py")
dag = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dag)


def test_order_puts_dependencies_first():
    o = dag.order()
    for stage, deps in dag.DAG.items():
        assert all(o.index(d) < o.index(stage) for d in deps)
    assert dag.order(only=["gold", "serve"]) == ["gold", "serve"]


def test_cycle_is_rejected():
    with pytest.raises(ValueError, match="cycle"):
        dag.order({"a": ["b"], "b": ["a"]})


def test_retry_then_success():
    calls, waits = [], []
    results = iter([1, 0, 0])          # silver fails once, then everything succeeds
    recs = dag.run(["silver", "gold"], lambda s: s, retries=2, sleep=waits.append,
                   runner=lambda cmd: calls.append(cmd) or next(results))
    assert calls == ["silver", "silver", "gold"]
    assert [r["attempts"] for r in recs] == [2, 1] and all(r["status"] == "ok" for r in recs)
    assert waits == [dag.BACKOFF_SECONDS[0]]


def test_stops_after_retries_run_out():
    calls = []
    recs = dag.run(["bronze", "silver"], lambda s: s, retries=1, sleep=lambda s: None,
                   runner=lambda cmd: calls.append(cmd) or 1)
    assert calls == ["bronze", "bronze"]             # silver never starts
    assert recs[-1] == {**recs[-1], "stage": "bronze", "attempts": 2, "status": "failed"}
