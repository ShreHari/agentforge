"""Wire the five steps into a LangGraph graph.

    START -> validate -> research -> match -> draft -> save -> END

Print the flowchart (paste the output into https://mermaid.live to see it drawn):

    uv run python -m agent.graph
"""

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from agent import nodes
from agent.state import JobHuntState
from agent.tracker import InMemoryTracker, Tracker

STEPS = ["validate", "research", "match", "draft", "save"]


def build_graph(
    checkpointer: BaseCheckpointSaver | None = None,
    tracker: Tracker | None = None,
):
    """Return the compiled Job Hunt graph.

    checkpointer: where checkpoints go. PostgresSaver in real runs (they survive a crash),
                  InMemorySaver in tests, None for no checkpoints at all.
    tracker:      where the save step writes. Defaults to an in-memory tracker.
    """
    if tracker is None:
        tracker = InMemoryTracker()

    builder = StateGraph(JobHuntState)

    # Nodes: a name and the function to run.
    builder.add_node("validate", nodes.validate)
    builder.add_node("research", nodes.research)
    builder.add_node("match", nodes.match)
    builder.add_node("draft", nodes.draft)
    builder.add_node("save", nodes.make_save(tracker))
    # TODO(Day 5): add retry_policy=RetryPolicy(...) to research, match and draft so a
    # rate-limit error (429) or timeout from the model is retried with backoff and jitter.

    # Edges: the arrows. A straight line for now.
    builder.add_edge(START, "validate")
    builder.add_edge("validate", "research")
    builder.add_edge("research", "match")
    builder.add_edge("match", "draft")
    builder.add_edge("draft", "save")
    builder.add_edge("save", END)

    # compile() checks the wiring and returns something you can run with invoke()/stream().
    return builder.compile(checkpointer=checkpointer)


if __name__ == "__main__":
    print(build_graph().get_graph().draw_mermaid())
