import json
import os

from .agent_runner import agent_content_text, force_integrate_sdk_prompt, invoke_agent
from .android_project import create_sandbox_app, run_gradle_build, sdk_present_in_gradle
from .apply_sdk_changes import apply_sdk_changes
from .config import REPORT_FILE
from .mcp_client import call_mcp, list_mcp_tools
from .prompts_agent import get_agent_prompt
from .test_state import AgentTestState


def setup_environment(state: AgentTestState):
    print("[1] node: Setup Environment")
    logs = []

    if not os.path.exists(state["app_path"]):
        logs.append({
            "node": "setup",
            "status": "FAIL",
            "reason": f"Path missing: {state['app_path']}",
        })
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


def run_agent_prompt(state: AgentTestState):
    print("[2] node: Run Agent Prompt")
    logs = []

    prompt = get_agent_prompt(state["app_path"])
    response = invoke_agent(prompt)
    has_tool_call = bool(response.tool_calls)

    if not has_tool_call:
        question_text = agent_content_text(response.content).strip()
        retry_prompt = force_integrate_sdk_prompt(state["app_path"])
        logs.append({
            "node": "agent_prompt",
            "status": "INFO",
            "agent_mode": "GEMINI",
            "message": "Agent asked a clarification question; retrying with default answer.",
            "prompt_used": prompt,
            "ide_question": question_text,
            "default_answer": "useResponseListener=false",
        })

        prompt = retry_prompt
        response = invoke_agent(prompt)
        has_tool_call = bool(response.tool_calls)

    logs.append({
        "node": "agent_prompt",
        "status": "SUCCESS" if has_tool_call else "FAIL",
        "agent_mode": "GEMINI",
        "message": f"Tool calls detected: {has_tool_call}",
        "prompt_used": prompt,
        "ai_content": response.content,
    })

    return {"last_agent_message": response, "nodes_logs": logs, "agent_mode": "GEMINI"}


def verify_mcp_activation(state: AgentTestState):
    print("[3] node: Verify MCP Activation")
    logs = []
    message = state["last_agent_message"]

    if not message or not message.tool_calls:
        logs.append({"node": "verify_mcp", "status": "FAIL", "reason": "No tool call from agent."})
        return {"mcp_triggered": False, "nodes_logs": logs}

    tool = message.tool_calls[0]
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

    path = os.path.abspath(REPORT_FILE)
    with open(path, "w", encoding="utf-8") as file:
        json.dump(report, file, indent=4, ensure_ascii=False)

    return {"report_path": path}
