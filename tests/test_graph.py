"""The graph, in memory: no database needed."""

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import ValidationError

from agent import nodes
from agent.graph import STEPS, build_graph
from agent.schemas import RequirementMatch
from agent.tracker import InMemoryTracker


def start(job_ad: str, cv: str, run_id: str = "run-1") -> dict:
    return {"run_id": run_id, "user_id": "test-user", "job_ad": job_ad, "cv": cv}


def config(run_id: str = "run-1") -> dict:
    return {"configurable": {"thread_id": run_id}}


def test_full_run(job_ad, cv):
    tracker = InMemoryTracker()
    graph = build_graph(InMemorySaver(), tracker)

    result = graph.invoke(start(job_ad, cv), config(), durability="sync")

    assert result["steps_done"] == STEPS
    assert result["company"] == "Fernhill Analytics Ltd"
    assert 0 <= result["match"]["score"] <= 100
    assert len(result["draft"]["cv_bullets"]) <= 5
    assert result["tracker_row_created"] is True
    assert len(tracker.rows) == 1


def test_bad_input_stops_at_validate(job_ad):
    tracker = InMemoryTracker()
    graph = build_graph(InMemorySaver(), tracker)

    with pytest.raises(ValidationError):
        graph.invoke(start(job_ad, cv="too short"), config())

    assert tracker.rows == {}  # nothing after validate ran


def test_resume_after_failure_skips_finished_steps(monkeypatch, job_ad, cv):
    """A step that fails is re-run on resume; steps that finished are NOT re-run."""
    calls = {"validate": 0, "research": 0}
    real_validate, real_research = nodes.validate, nodes.research

    def counting_validate(state):
        calls["validate"] += 1
        return real_validate(state)

    def research_fails_once(state):
        calls["research"] += 1
        if calls["research"] == 1:
            raise ConnectionError("simulated network failure")
        return real_research(state)

    # Swap the real steps for these test versions (undone automatically after the test).
    monkeypatch.setattr(nodes, "validate", counting_validate)
    monkeypatch.setattr(nodes, "research", research_fails_once)
    graph = build_graph(InMemorySaver(), InMemoryTracker())

    with pytest.raises(ConnectionError):
        graph.invoke(start(job_ad, cv), config(), durability="sync")
    assert graph.get_state(config()).next == (
        "research",)  # stopped before research

    result = graph.invoke(None, config(), durability="sync")  # None = resume

    assert calls == {"validate": 1, "research": 2}
    assert result["steps_done"] == STEPS


def test_save_twice_keeps_one_row():
    """The idempotency guarantee: the save step can run twice, the tracker gets one row."""
    tracker = InMemoryTracker()
    save = nodes.make_save(tracker)
    state = {"run_id": "run-1", "user_id": "u",
             "company": "Acme", "match": {"score": 50}}

    first = save(state)
    second = save(state)

    assert first["tracker_row_created"] is True
    assert second["tracker_row_created"] is False
    assert len(tracker.rows) == 1


def test_requirements_are_read_from_bullets(job_ad):
    requirements = nodes.find_requirements(job_ad)
    assert requirements[0] == "Python for production services"
    assert len(requirements) == 8


def test_real_match_uses_the_model_answer(monkeypatch, job_ad, cv):
    """With FAKE_LLM=false, match uses the model's ratings and our code scores them.

    The model call is replaced with a pretend answer, so the test never calls the AI.
    """
    pretend_answer = [
        RequirementMatch(requirement="Python", strength="strong",
                         evidence="Built Python services"),
        RequirementMatch(requirement="Kubernetes", strength="missing"),
    ]
    monkeypatch.setenv("FAKE_LLM", "false")
    nodes.get_settings.cache_clear()
    monkeypatch.setattr(nodes, "ask_model_for_matches",
                        lambda job_ad, cv: pretend_answer)

    result = nodes.match({"job_ad": job_ad, "cv": cv})

    assert [m["requirement"]
            for m in result["match"]["matches"]] == ["Python", "Kubernetes"]
    assert result["match"]["score"] == 50  # 1 strong + 0 missing, out of 2


def test_score_matches():
    def m(strength):
        return RequirementMatch(requirement="x", strength=strength)

    assert nodes.score_matches([]) == 0
    assert nodes.score_matches(
        [m("strong"), m("partial"), m("missing"), m("missing")]) == 38
