import json
import os

from .agent_runner import llm_with_tools
from .android_project import create_sandbox_app, run_gradle_build, sdk_present_in_gradle
from .apply_sdk_changes import apply_sdk_changes
from .mcp_client import call_mcp, list_mcp_tools
from .prompts_agent import get_agent_prompt
from .response_agent import (
    MAX_QUESTION_ROUNDS,
    build_prompt_with_answers,
    classify_agent_response,
)
from .test_state import AgentTestState


def setup_environment(state: AgentTestState):
    print("[1] node: Setup Environment")
    logs = []

    if not os.path.exists(state["app_path"]):
        logs.append({"node": "setup", "status": "FAIL", "reason": f"Path missing: {state['app_path']}"})
        return {"nodes_logs": logs, "test_status": "FAIL"}

    original_app_path = state["app_path"]
    try:
        sandbox_app_path = create_sandbox_app(original_app_path)
    except Exception as exc:
        logs.append({
            "node": "setup",
            "status": "FAIL",
            "reason": f"Sandbox creation failed: {exc}",
        })
        return {"nodes_logs": logs, "test_status": "FAIL"}

    logs.append({
        "node": "setup",
        "status": "SUCCESS",
        "message": "Workspace verified.",
        "agent_mode": "GEMINI",
    })

    mcp_health = call_mcp("getVersion", {})
    if not mcp_health.get("success"):
        logs.append({
            "node": "setup",
            "status": "FAIL",
            "reason": f"MCP server unavailable: {mcp_health.get('error')}",
        })
        return {"nodes_logs": logs, "test_status": "FAIL"}

    mcp_tool_entry = {
        "tool": "getVersion",
        "args": {},
        "phase": "setup_health_check",
        "success": True,
        "is_error": mcp_health.get("is_error", False),
        "response_preview": (mcp_health.get("text") or "")[:200],
    }

    tools_list_result = list_mcp_tools()
    available_tools = tools_list_result.get("tools", []) if tools_list_result.get("success") else []

    logs.append({
        "node": "setup",
        "status": "SUCCESS",
        "message": "MCP server reachable (getVersion).",
        "mcp_version_preview": (mcp_health.get("text") or "")[:300],
        "mcp_tools_count": len(available_tools),
    })

    return {
        "nodes_logs": logs,
        "agent_mode": "GEMINI",
        "mcp_tools_used": [mcp_tool_entry],
        "mcp_tools_available": available_tools,
        "original_app_path": original_app_path,
        "app_path": sandbox_app_path,
        "sandbox_path": sandbox_app_path,
    }


def stream_listen(state: AgentTestState):
    """STREAM node: the ONLY node that talks to the installation LLM."""
    print("[2] node: Stream Listen (LLM radar)")
    logs = []

    question_rounds = state.get("question_rounds", 0)
    base_prompt = state.get("agent_base_prompt") or get_agent_prompt(state["app_path"])
    prompt = build_prompt_with_answers(base_prompt, state.get("installation_answers", []))

    resp = None
    for chunk in llm_with_tools.stream(prompt):
        resp = chunk if resp is None else resp + chunk

    if resp is None:
        logs.append({
            "node": "stream_listen",
            "status": "FAIL",
            "reason": "No response streamed from the LLM.",
        })
        return {
            "nodes_logs": logs,
            "agent_base_prompt": base_prompt,
            "test_status": "FAIL",
            "stream_decision": "fail_node",
        }

    raw = resp.content
    if isinstance(raw, list):
        raw = " ".join(getattr(x, "text", str(x)) for x in raw)
    agent_text = str(raw or "").strip()
    has_tool_call = bool(getattr(resp, "tool_calls", None))

    if has_tool_call:
        logs.append({
            "node": "stream_listen",
            "status": "SUCCESS",
            "message": "Agent returned tool call(s); routing to run_agent_prompt.",
            "tool_calls": [t.get("name") for t in resp.tool_calls],
            "ai_content_preview": agent_text[:200],
        })
        return {
            "last_agent_message": resp,
            "agent_base_prompt": base_prompt,
            "nodes_logs": logs,
            "stream_decision": "run_agent_prompt",
        }

    decision = classify_agent_response(state, agent_text)
    label = decision["label"]
    nxt = decision["next"]

    if (label == "QUESTION" or nxt == "answer_question") and question_rounds >= MAX_QUESTION_ROUNDS:
        logs.append({
            "node": "stream_listen",
            "status": "FAIL",
            "reason": f"Exceeded max question rounds ({MAX_QUESTION_ROUNDS}).",
            "question_rounds": question_rounds,
            "classifier": decision,
        })
        return {
            "last_agent_message": resp,
            "agent_base_prompt": base_prompt,
            "nodes_logs": logs,
            "test_status": "FAIL",
            "stream_decision": "fail_node",
        }

    logs.append({
        "node": "stream_listen",
        "status": "INFO",
        "message": f"Classified agent message as {label} -> {nxt}.",
        "classifier_label": label,
        "classifier_next": nxt,
        "classifier_reason": decision.get("reason", ""),
        "ai_content_preview": agent_text[:200],
    })

    result = {
        "last_agent_message": resp,
        "agent_base_prompt": base_prompt,
        "nodes_logs": logs,
        "stream_decision": nxt,
    }
    if label == "FAIL" or nxt == "fail_node":
        result["test_status"] = "FAIL"
    if label == "QUESTION" or nxt == "answer_question":
        result["incoming_question"] = decision.get("question") or agent_text

    return result


def run_agent_prompt(state: AgentTestState):
    print("[2] node: Run Agent Prompt (verify tool calls only)")
    logs = []
    msg = state.get("last_agent_message")
    has_tool_call = bool(getattr(msg, "tool_calls", None))

    logs.append({
        "node": "agent_prompt",
        "status": "SUCCESS" if has_tool_call else "FAIL",
        "agent_mode": "GEMINI",
        "message": f"Tool calls detected: {has_tool_call}",
        "ai_content": getattr(msg, "content", None),
    })

    if not has_tool_call:
        return {"nodes_logs": logs, "test_status": "FAIL"}
    return {"nodes_logs": logs}


def verify_mcp_activation(state: AgentTestState):
    print("[3] node: Verify MCP Activation")
    logs = []
    msg = state["last_agent_message"]

    if not msg or not msg.tool_calls:
        logs.append({"node": "verify_mcp", "status": "FAIL", "reason": "No tool call from agent."})
        return {"mcp_triggered": False, "nodes_logs": logs}

    tool = msg.tool_calls[0]
    logs.append({"node": "verify_mcp", "status": "INFO", "message": f"Agent selected tool: {tool['name']}"})

    tool_args = dict(tool.get("args") or {})
    if tool["name"] == "integrateSdk" and tool_args.get("platform") == "android":
        tool_args.setdefault("useResponseListener", False)

    mcp_result = call_mcp(tool["name"], tool_args)
    mcp_text = mcp_result.get("text") or ""
    mcp_tool_entry = {
        "tool": tool["name"],
        "args": tool_args,
        "phase": "agent_integration",
        "success": mcp_result.get("success", False),
        "is_error": mcp_result.get("is_error", False),
        "response_preview": mcp_text[:200],
    }

    if not mcp_result.get("success"):
        logs.append({
            "node": "verify_mcp",
            "status": "FAIL",
            "reason": f"MCP execution failed: {mcp_result.get('error')}",
        })
        return {"mcp_triggered": False, "nodes_logs": logs, "mcp_tools_used": [mcp_tool_entry]}

    if mcp_result.get("is_error"):
        logs.append({
            "node": "verify_mcp",
            "status": "FAIL",
            "reason": "MCP returned error response.",
            "mcp_output_preview": mcp_text[:500],
        })
        return {"mcp_triggered": False, "nodes_logs": logs, "mcp_tools_used": [mcp_tool_entry]}

    logs.append({
        "node": "verify_mcp",
        "status": "SUCCESS",
        "message": "MCP tool executed successfully.",
        "mcp_output_preview": mcp_text[:500],
        "mcp_response_length": len(mcp_text),
    })

    return {
        "mcp_triggered": True,
        "mcp_integration_text": mcp_text,
        "nodes_logs": logs,
        "mcp_tools_used": [mcp_tool_entry],
    }


def apply_sdk_changes_node(state: AgentTestState):
    result = apply_sdk_changes(state)
    sdk_verified = sdk_present_in_gradle(state["app_path"]) if result.get("files_modified") else False
    result["sdk_verified"] = sdk_verified
    if result.get("files_modified") and not sdk_verified:
        result["nodes_logs"] = result.get("nodes_logs", []) + [{
            "node": "apply_sdk",
            "status": "WARN",
            "message": "Files were written but AppsFlyer dependency was not detected in Gradle.",
        }]
    return result


def check_compilation(state: AgentTestState):
    print("[5] node: Check Compilation")
    success, log = run_gradle_build(state["app_path"])
    return {"compilation_passed": success, "nodes_logs": [log]}


def pass_node(state: AgentTestState):
    return {"test_status": "PASS", "nodes_logs": [{"node": "final_status", "status": "PASS"}]}


def fail_node(state: AgentTestState):
    return {"test_status": "FAIL", "nodes_logs": [{"node": "final_status", "status": "FAIL"}]}


def end_report(state: AgentTestState):
    print("[6] node: End Report")
    report = {
        "app_id": state["app_id"],
        "final_status": state["test_status"],
        "agent_mode": state.get("agent_mode", "GEMINI"),
        "mcp_triggered": state["mcp_triggered"],
        "sdk_verified": state["sdk_verified"],
        "compilation_passed": state["compilation_passed"],
        "mcp_tools_used": state.get("mcp_tools_used", []),
        "mcp_tools_available": state.get("mcp_tools_available", []),
        "files_modified_by_runner": state.get("files_modified", False),
        "applied_files": state.get("applied_files", []),
        "detailed_steps": state["nodes_logs"],
    }

    path = os.path.abspath("mcp_test_report.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=4, ensure_ascii=False)

    return {"report_path": path}
