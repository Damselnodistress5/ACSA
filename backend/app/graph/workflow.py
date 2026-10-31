import logging
from langgraph.graph import END, START, StateGraph

from app.graph.nodes import (
    code_architect_node,
    code_reviewer_node,
    code_writer_node,
    requirements_analyst_node,
    tester_node,
)
from app.graph.state import AgentState

logger = logging.getLogger("acsa.workflow")


def build_acsa_graph():
    """
    Builds and compiles the ACSA multi-agent state graph with nodes:
    1. Requirements Analyst
    2. Code Architect
    3. Code Writer
    4. Tester
    5. Code Reviewer
    """
    workflow = StateGraph(AgentState)

    # 1. Add nodes
    workflow.add_node("analyst", requirements_analyst_node)
    workflow.add_node("architect", code_architect_node)
    workflow.add_node("writer", code_writer_node)
    workflow.add_node("tester", tester_node)
    workflow.add_node("reviewer", code_reviewer_node)

    # 2. Edges: stop as soon as a node marks the run failed (no silent fallbacks downstream)
    def stop_if_failed(next_node: str):
        def route(state: AgentState) -> str:
            return END if state.get("status") == "failed" else next_node
        return route

    workflow.add_edge(START, "analyst")
    workflow.add_conditional_edges("analyst", stop_if_failed("architect"), ["architect", END])
    workflow.add_conditional_edges("architect", stop_if_failed("writer"), ["writer", END])
    workflow.add_conditional_edges("writer", stop_if_failed("tester"), ["tester", END])
    workflow.add_conditional_edges("tester", stop_if_failed("reviewer"), ["reviewer", END])

    # 3. Repair loop: the Reviewer sends a failing run back to the Writer (bounded by MAX_REPAIR_ATTEMPTS)
    def after_review(state: AgentState) -> str:
        if state.get("status") != "failed" and state.get("current_step") == "writer":
            return "writer"
        return END

    workflow.add_conditional_edges("reviewer", after_review, ["writer", END])

    logger.info("Compiled ACSA Multi-Agent StateGraph successfully.")
    return workflow.compile()


# Pre-compiled graph instance for execution
acsa_graph = build_acsa_graph()
