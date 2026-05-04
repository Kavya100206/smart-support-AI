"""LangGraph autonomous resolution agent.

Architecture
------------
The agent runs as a loop over three nodes:

    call_llm ──► execute_tool ──► check_iterations
        ▲                               │
        └───────────────────────────────┘ (loop back if not done)
        └─── END (if final_action set or iterations ≥ MAX_ITERATIONS)

State
-----
AgentState is a TypedDict.  A fresh instance is created per request inside
``run_agent()`` and discarded as soon as that function returns.  No state is
held at module level.

LLM protocol
------------
The LLM (Groq llama-3.3-70b-versatile) is prompted to return ONLY a JSON
object with the shape:

    {
        "tool":       "get_order_status" | "check_refund_eligibility"
                      | "get_faq_answer" | "escalate_to_human" | "DONE",
        "args":       { ... },        // tool-specific keyword args
        "confidence": 0.0–1.0,        // self-reported certainty
        "resolution": "..."           // populated when tool == "DONE"
    }

Confidence gate
---------------
If the very first LLM response has confidence < CONFIDENCE_THRESHOLD (0.6),
the agent auto-escalates unconditionally without calling any tool.

Iteration cap
-------------
MAX_ITERATIONS = 3.  After 3 loop cycles the agent escalates regardless of
the LLM's next response.

Public API
----------
    run_agent(query, order_id) -> AgentResult
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, StateGraph
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)

MAX_ITERATIONS: int = 3
CONFIDENCE_THRESHOLD: float = 0.6

GROQ_MODEL = "llama-3.3-70b-versatile"

# ── System prompt ──────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """You are an autonomous e-commerce support agent.
Your job is to resolve a customer support ticket by calling the appropriate tool.

Available tools:
- get_order_status(order_id)         — fetch order financial and shipping status
- check_refund_eligibility(order_id) — check if the order is refund-eligible
- get_faq_answer(query)              — search the FAQ knowledge base
- escalate_to_human(reason)          — hand off to a human agent
- DONE                               — you have a complete resolution

Rules:
1. Call ONE tool per response.
2. If you have enough information to fully answer the customer, use DONE.
3. If you cannot resolve the issue, use escalate_to_human with a clear reason.
4. ALWAYS return ONLY a valid JSON object — no markdown, no explanation.

Response format (EXACTLY):
{
    "tool":       "<tool_name or DONE>",
    "args":       { "<arg_name>": "<value>" },
    "confidence": <float 0.0–1.0>,
    "resolution": "<customer-facing answer — only when tool is DONE, else empty string>"
}"""

_USER_TEMPLATE = """Customer ticket:
\"\"\"{query}\"\"\"

Order ID (may be empty): {order_id}

Tool results so far:
{tool_history}

Decide the next action."""


# ── AgentState ─────────────────────────────────────────────────────────────────

class AgentState(TypedDict):
    query: str
    order_id: str
    # List of {"tool": str, "args": dict, "result": dict}
    tools_trace: list[dict]
    iterations: int
    final_action: str          # "resolved" | "escalated" | "" (empty = still running)
    resolution: str
    escalation_reason: str
    confidence_score: float | None
    # Internal sentinel set by _node_call_llm, consumed by _node_execute_tool.
    # MUST be declared here so LangGraph does not strip it from state updates.
    _pending_tool: Any


# ── AgentResult (returned to the view) ────────────────────────────────────────

class AgentResult(TypedDict):
    final_action: str          # "resolved" | "escalated"
    resolution: str
    escalation_reason: str
    tools_trace: list[dict]
    iterations: int
    confidence_score: float | None


# ── Groq call helper ───────────────────────────────────────────────────────────

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=4),
    reraise=True,
)
def _call_groq(messages: list[dict]) -> str:
    """Call the Groq API and return the raw text content of the response.

    Phase 3: wrapped with tenacity retry — 2 retries (3 total attempts) with
    exponential backoff (1 s → 2 s → 4 s cap). On final failure the exception
    propagates to ``_node_call_llm`` which catches it and auto-escalates,
    storing the error string in the decision trace.
    """
    from groq import Groq  # noqa: PLC0415

    api_key = os.environ.get("GROQ_API_KEY", "")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY environment variable is not set.")

    client = Groq(api_key=api_key)
    completion = client.chat.completions.create(
        model=GROQ_MODEL,
        messages=messages,
        temperature=0.1,
        max_tokens=512,
    )
    return completion.choices[0].message.content.strip()


def _parse_llm_json(raw: str) -> dict:
    """Extract and parse the first JSON object from *raw* LLM output."""
    # Strip markdown fences if the model disobeys the prompt.
    cleaned = re.sub(r"```(?:json)?", "", raw).strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}") + 1
    if start == -1 or end <= start:
        raise ValueError(f"No JSON object found in LLM output: {raw!r}")
    return json.loads(cleaned[start:end])


# ── Graph nodes ────────────────────────────────────────────────────────────────

def _node_call_llm(state: AgentState) -> AgentState:
    """Ask the LLM what to do next given the current tool history."""
    tool_history_text = (
        json.dumps(state["tools_trace"], indent=2)
        if state["tools_trace"]
        else "None yet."
    )
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {
            "role": "user",
            "content": _USER_TEMPLATE.format(
                query=state["query"],
                order_id=state["order_id"] or "(none)",
                tool_history=tool_history_text,
            ),
        },
    ]

    try:
        raw = _call_groq(messages)
        parsed = _parse_llm_json(raw)
    except Exception as exc:
        logger.warning("_node_call_llm: LLM call/parse failed: %s", exc)
        # Treat parse failure as an escalation trigger.
        return {
            **state,
            "final_action": "escalated",
            "escalation_reason": f"LLM call failed: {exc}",
            "confidence_score": None,
        }

    tool_name: str = parsed.get("tool", "").strip()
    args: dict = parsed.get("args") or {}
    confidence: float = float(parsed.get("confidence", 0.0))
    resolution: str = parsed.get("resolution", "").strip()

    # ── Confidence gate (first check only) ────────────────────────────────────
    # If this is the first iteration and confidence is below threshold,
    # auto-escalate unconditionally before calling any tool.
    if state["iterations"] == 0 and confidence < CONFIDENCE_THRESHOLD:
        return {
            **state,
            "final_action": "escalated",
            "escalation_reason": (
                f"Initial confidence {confidence:.2f} is below threshold "
                f"{CONFIDENCE_THRESHOLD}. Escalating without attempting resolution."
            ),
            "confidence_score": confidence,
        }

    # ── DONE ──────────────────────────────────────────────────────────────────
    if tool_name == "DONE":
        return {
            **state,
            "final_action": "resolved",
            "resolution": resolution,
            "confidence_score": confidence,
        }

    # ── Escalate tool called directly ─────────────────────────────────────────
    if tool_name == "escalate_to_human":
        reason = args.get("reason", "Agent chose to escalate.")
        return {
            **state,
            "final_action": "escalated",
            "escalation_reason": reason,
            "confidence_score": confidence,
        }

    # ── Store pending tool call in state for execute_tool node ────────────────
    # We use a sentinel entry with result=None to indicate "not yet executed".
    pending_entry: dict = {"tool": tool_name, "args": args, "result": None}
    return {
        **state,
        "_pending_tool": pending_entry,  # consumed by _node_execute_tool
        "confidence_score": confidence,
    }


def _node_execute_tool(state: AgentState) -> AgentState:
    """Execute the tool chosen by the LLM and record result in tools_trace."""
    from tickets.services.agent_tools import TOOL_REGISTRY  # noqa: PLC0415

    pending: dict | None = state.get("_pending_tool")  # type: ignore[call-overload]
    if not pending:
        # Nothing to execute — shouldn't happen in normal flow.
        return state

    tool_name: str = pending["tool"]
    args: dict = pending["args"]

    tool_fn = TOOL_REGISTRY.get(tool_name)
    if tool_fn is None:
        result = {"error": f"Unknown tool: {tool_name!r}"}
        logger.warning("_node_execute_tool: unknown tool %r", tool_name)
    else:
        try:
            result = tool_fn(**args)
        except Exception as exc:
            result = {"error": str(exc)}
            logger.warning("_node_execute_tool: tool %r raised: %s", tool_name, exc)

    trace_entry = {"tool": tool_name, "args": args, "result": result}
    new_trace = list(state["tools_trace"]) + [trace_entry]

    updated = {**state, "tools_trace": new_trace, "iterations": state["iterations"] + 1}
    # Remove the sentinel so it is not carried into the next cycle.
    updated.pop("_pending_tool", None)
    return updated


def _node_check_iterations(state: AgentState) -> AgentState:
    """Auto-escalate if MAX_ITERATIONS is reached."""
    if state["iterations"] >= MAX_ITERATIONS and not state["final_action"]:
        return {
            **state,
            "final_action": "escalated",
            "escalation_reason": (
                f"Reached maximum iterations ({MAX_ITERATIONS}) without resolution."
            ),
        }
    return state


# ── Conditional edge ───────────────────────────────────────────────────────────

def _should_continue(state: AgentState) -> str:
    """Return 'end' when a final_action has been set, 'continue' otherwise."""
    if state.get("final_action"):
        return "end"
    # Also stop if the LLM set a pending tool but something went wrong.
    return "continue"


# ── Graph builder ──────────────────────────────────────────────────────────────

def build_graph():
    """Build and compile the LangGraph StateGraph.

    Called fresh per request inside run_agent(). The compiled graph object
    is ephemeral — it is never stored at module level.
    """
    graph = StateGraph(AgentState)

    graph.add_node("call_llm", _node_call_llm)
    graph.add_node("execute_tool", _node_execute_tool)
    graph.add_node("check_iterations", _node_check_iterations)

    graph.set_entry_point("call_llm")

    # call_llm → execute_tool (always, so tool is executed)
    # but if final_action is already set (DONE / escalate / confidence gate)
    # we short-circuit to END via the conditional edge.
    graph.add_conditional_edges(
        "call_llm",
        _should_continue,
        {"end": END, "continue": "execute_tool"},
    )

    # execute_tool → check_iterations → back to call_llm or END
    graph.add_edge("execute_tool", "check_iterations")
    graph.add_conditional_edges(
        "check_iterations",
        _should_continue,
        {"end": END, "continue": "call_llm"},
    )

    return graph.compile()


# ── Public entry point ─────────────────────────────────────────────────────────

def run_agent(*, query: str, order_id: str) -> AgentResult:
    """Run the autonomous resolution agent for a single ticket.

    Creates a fresh StateGraph per call and discards it immediately. No state
    persists between calls.

    Args:
        query:    The ticket description / customer message.
        order_id: Shopify order ID associated with the ticket (may be empty).

    Returns:
        AgentResult TypedDict with final_action, resolution, escalation_reason,
        tools_trace, iterations, and confidence_score.
    """
    initial_state: AgentState = {
        "query": query,
        "order_id": order_id or "",
        "tools_trace": [],
        "iterations": 0,
        "final_action": "",
        "resolution": "",
        "escalation_reason": "",
        "confidence_score": None,
        "_pending_tool": None,
    }

    compiled = build_graph()
    final_state: AgentState = compiled.invoke(initial_state)

    return AgentResult(
        final_action=final_state.get("final_action", "escalated"),
        resolution=final_state.get("resolution", ""),
        escalation_reason=final_state.get("escalation_reason", ""),
        tools_trace=final_state.get("tools_trace", []),
        iterations=final_state.get("iterations", 0),
        confidence_score=final_state.get("confidence_score"),
    )
