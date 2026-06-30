import json
import os

from android_project import create_sandbox_app, run_gradle_build, sdk_present_in_gradle
from apply_sdk_changes import _extract_json_array, apply_sdk_changes, GEMINI_MODEL
from config import REPORT_FILE
from llm_listener import invoke_agent_with_listener, invoke_plain_llm_with_listener, listener_on_text
from mcp_client import call_mcp, list_mcp_tools
from PromptsAgent import get_agent_prompt
from test_state import AgentTestState


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

    base_prompt = get_agent_prompt(state["app_path"])
    response, listener_updates = invoke_agent_with_listener(
        state, base_prompt, "run_agent_prompt"
    )
    logs.extend(listener_updates.get("nodes_logs", []))

    if listener_updates.get("test_status") == "FAIL":
        logs.append({
            "node": "agent_prompt",
            "status": "FAIL",
            "agent_mode": "GEMINI",
            "message": "Listener classified agent response as failure.",
            "prompt_used": base_prompt,
        })
        return {
            "last_agent_message": response,
            "nodes_logs": logs,
            "agent_mode": "GEMINI",
            "test_status": "FAIL",
            "question_rounds": listener_updates.get("question_rounds", state.get("question_rounds", 0)),
            "installation_answers": listener_updates.get("installation_answers", []),
        }

    has_tool_call = bool(response and response.tool_calls)
    logs.append({
        "node": "agent_prompt",
        "status": "SUCCESS" if has_tool_call else "FAIL",
        "agent_mode": "GEMINI",
        "message": f"Tool calls detected: {has_tool_call}",
        "prompt_used": base_prompt,
        "ai_content": response.content if response else None,
    })

    result = {
        "last_agent_message": response,
        "nodes_logs": logs,
        "agent_mode": "GEMINI",
        "question_rounds": listener_updates.get("question_rounds", state.get("question_rounds", 0)),
        "installation_answers": listener_updates.get("installation_answers", []),
    }
    return result


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
    listener_logs = []
    _, listener_updates = listener_on_text(state, "verify_mcp_activation", mcp_text)
    listener_logs.extend(listener_updates.get("nodes_logs", []))
    if listener_updates.get("test_status") == "FAIL":
        return {
            "mcp_triggered": False,
            "nodes_logs": logs + listener_logs,
            "test_status": "FAIL",
            "question_rounds": listener_updates.get("question_rounds", state.get("question_rounds", 0)),
            "installation_answers": listener_updates.get("installation_answers", []),
        }

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
        return {"mcp_triggered": False, "nodes_logs": logs + listener_logs, "mcp_tools_used": [mcp_tool_entry]}

    if mcp_result.get("is_error"):
        logs.append({
            "node": "verify_mcp",
            "status": "FAIL",
            "reason": "MCP returned error response.",
            "mcp_output_preview": mcp_text[:500],
        })
        return {"mcp_triggered": False, "nodes_logs": logs + listener_logs, "mcp_tools_used": [mcp_tool_entry]}

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
        "nodes_logs": logs + listener_logs,
        "mcp_tools_used": [mcp_tool_entry],
        "question_rounds": listener_updates.get("question_rounds", state.get("question_rounds", 0)),
        "installation_answers": listener_updates.get("installation_answers", []),
    }


def apply_sdk_changes_node(state: AgentTestState):
    from dotenv import load_dotenv
    from langchain_google_genai import ChatGoogleGenerativeAI

    load_dotenv()
    llm = ChatGoogleGenerativeAI(
        model=GEMINI_MODEL,
        google_api_key=os.getenv("GEMINI_API_KEY"),
    )
    listener_holder: dict = {"updates": {}}
    working_state = dict(state)

    def llm_invoker(prompt: str):
        def is_done(response) -> bool:
            content = response.content if hasattr(response, "content") else str(response)
            try:
                _extract_json_array(content)
                return True
            except ValueError:
                return False

        response, updates = invoke_plain_llm_with_listener(
            working_state,
            prompt,
            "apply_sdk_changes",
            llm.invoke,
            is_done=is_done,
        )
        listener_holder["updates"] = updates
        working_state["question_rounds"] = updates.get(
            "question_rounds", working_state.get("question_rounds", 0)
        )
        if updates.get("test_status") == "FAIL":
            raise ValueError("LISTENER_FAIL")
        return response

    try:
        result = apply_sdk_changes(state, llm_invoker=llm_invoker)
    except ValueError as exc:
        if str(exc) != "LISTENER_FAIL":
            raise
        result = {
            "files_modified": False,
            "applied_files": [],
            "nodes_logs": [],
            "test_status": "FAIL",
        }

    listener_updates = listener_holder.get("updates", {})
    result["nodes_logs"] = listener_updates.get("nodes_logs", []) + result.get("nodes_logs", [])
    if listener_updates.get("test_status") == "FAIL":
        result["test_status"] = "FAIL"
    if "question_rounds" in listener_updates:
        result["question_rounds"] = listener_updates["question_rounds"]
    if listener_updates.get("installation_answers"):
        result["installation_answers"] = listener_updates["installation_answers"]

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
    logs = [log]
    result: dict = {"compilation_passed": success, "nodes_logs": logs}

    build_text = ((log.get("stderr_tail") or "") + "\n" + (log.get("stdout_tail") or "")).strip()
    if build_text:
        _, listener_updates = listener_on_text(state, "check_compilation", build_text)
        logs.extend(listener_updates.get("nodes_logs", []))
        if listener_updates.get("test_status") == "FAIL":
            result["test_status"] = "FAIL"
        if "question_rounds" in listener_updates:
            result["question_rounds"] = listener_updates["question_rounds"]
        if listener_updates.get("installation_answers"):
            result["installation_answers"] = listener_updates["installation_answers"]

    return result


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
