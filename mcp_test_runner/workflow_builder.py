import os

from langgraph.graph import END, StateGraph

from .response_agent import answer_question_node
from .test_state import AgentTestState
from .workflow_nodes import (
    apply_sdk_changes_node,
    check_compilation,
    end_report,
    fail_node,
    pass_node,
    run_agent_prompt,
    setup_environment,
    stream_listen,
    verify_mcp_activation,
)


def route_after_setup(state):
    return "stream_listen" if state.get("test_status") != "FAIL" else "fail_node"


def route_after_stream(state):
    return state.get("stream_decision", "fail_node")


def route_after_agent(state):
    msg = state.get("last_agent_message")
    if msg and getattr(msg, "tool_calls", None):
        return "verify_mcp_activation"
    return "fail_node"


def route_after_answer(state):
    return "fail_node" if state.get("test_status") == "FAIL" else "stream_listen"


def route_after_mcp(state):
    return "apply_sdk_changes" if state["mcp_triggered"] else "fail_node"


def route_after_apply(state):
    return "check_compilation" if state.get("files_modified") else "fail_node"


def route_after_compilation(state):
    return "pass_node" if state["compilation_passed"] else "fail_node"


def build_workflow():
    workflow = StateGraph(AgentTestState)

    workflow.add_node("setup_environment", setup_environment)
    workflow.add_node("stream_listen", stream_listen)
    workflow.add_node("answer_question", answer_question_node)
    workflow.add_node("run_agent_prompt", run_agent_prompt)
    workflow.add_node("verify_mcp_activation", verify_mcp_activation)
    workflow.add_node("apply_sdk_changes", apply_sdk_changes_node)
    workflow.add_node("check_compilation", check_compilation)
    workflow.add_node("pass_node", pass_node)
    workflow.add_node("fail_node", fail_node)
    workflow.add_node("end_report", end_report)

    workflow.set_entry_point("setup_environment")

    workflow.add_conditional_edges("setup_environment", route_after_setup)
    workflow.add_conditional_edges("stream_listen", route_after_stream)
    workflow.add_conditional_edges("answer_question", route_after_answer)
    workflow.add_conditional_edges("run_agent_prompt", route_after_agent)
    workflow.add_conditional_edges("verify_mcp_activation", route_after_mcp)
    workflow.add_conditional_edges("apply_sdk_changes", route_after_apply)
    workflow.add_conditional_edges("check_compilation", route_after_compilation)

    workflow.add_edge("pass_node", "end_report")
    workflow.add_edge("fail_node", "end_report")
    workflow.add_edge("end_report", END)

    return workflow.compile()


def initial_state() -> AgentTestState:
    dummy_app = os.path.abspath("./basic_app")
    os.makedirs(dummy_app, exist_ok=True)

    return {
        "app_id": "appsflyer-demo-test",
        "app_path": os.path.abspath("./basic_app"),
        "original_app_path": "",
        "sandbox_path": "",
        "agent_mode": "GEMINI",
        "mcp_triggered": False,
        "mcp_integration_text": "",
        "files_modified": False,
        "applied_files": [],
        "compilation_passed": False,
        "sdk_verified": False,
        "test_status": "UNKNOWN",
        "last_agent_message": None,
        "mcp_tools_used": [],
        "mcp_tools_available": [],
        "nodes_logs": [],
        "report_path": "",
        "stream_decision": "",
        "agent_base_prompt": "",
        "incoming_question": "",
        "question_rounds": 0,
        "installation_answers": [],
    }


def print_mcp_tools_summary(final_result: dict) -> None:
    available = final_result.get("mcp_tools_available", [])
    used = final_result.get("mcp_tools_used", [])

    print("\n" + "=" * 60)
    print("MCP TOOLS - ALL AVAILABLE ON SERVER")
    print("=" * 60)
    if available:
        for i, tool in enumerate(available, 1):
            desc = tool.get("description", "")
            suffix = f" - {desc}" if desc else ""
            print(f"  {i:2}. {tool['name']}{suffix}")
        print(f"\nTotal available: {len(available)}")
    else:
        print("  (Could not load tool list - MCP may have failed at setup)")

    print("\n" + "=" * 60)
    print("MCP TOOLS - USED IN THIS RUN")
    print("=" * 60)
    if used:
        for i, entry in enumerate(used, 1):
            status = "OK" if entry.get("success") and not entry.get("is_error") else "FAIL"
            print(f"  {i}. [{status}] {entry['tool']}")
            print(f"       phase: {entry.get('phase')}")
            print(f"       args:  {entry.get('args', {})}")
            preview = entry.get("response_preview", "")
            if preview:
                print(f"       response: {preview[:120]}...")
        print(f"\nTotal used: {len(used)}")
    else:
        print("  (No tools were called)")

    applied = final_result.get("applied_files", [])
    print("\n" + "=" * 60)
    print("SDK APPLY STEP")
    print("=" * 60)
    if final_result.get("files_modified"):
        print(f"  Files modified: {len(applied)}")
        for path in applied:
            print(f"    - {path}")
    else:
        print("  No project files were modified.")
    print("=" * 60)
