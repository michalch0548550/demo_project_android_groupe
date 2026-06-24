import os
import json
import re
import asyncio
import operator
import subprocess
from typing import TypedDict, List, Dict, Any, Annotated, Optional

from langgraph.graph import StateGraph, END
from langchain_core.messages import AIMessage
from dotenv import load_dotenv
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client, get_default_environment

load_dotenv()

# =========================
# CONFIG
# =========================
# עקיפת Gemini: USE_MOCK_AGENT=true (ברירת מחדל) — MCP, קומפילציה ודוח רצים אמיתית
USE_MOCK_AGENT = os.getenv("USE_MOCK_AGENT", "true").lower() in ("1", "true", "yes")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
DEFAULT_APP_ID = "com.appsflyer.onelink.appsflyeronelinkbasicapp"


def build_mcp_server_params() -> StdioServerParameters:
    env = get_default_environment()
    dev_key = os.getenv("DEV_KEY")
    app_id = os.getenv("APP_ID", DEFAULT_APP_ID)
    if dev_key:
        env["DEV_KEY"] = dev_key.strip('"').strip("'")
    if app_id:
        env["APP_ID"] = app_id.strip('"').strip("'")
    return StdioServerParameters(
        command="npx",
        args=["-y", "@appsflyer/sdk-mcp-server"],
        env=env,
    )

INTEGRATE_SDK_TOOL = {
    "name": "integrateSdk",
    "description": "Integrate AppsFlyer SDK into Android or iOS project.",
    "parameters": {
        "type": "object",
        "properties": {
            "platform": {"type": "string", "enum": ["android", "ios"]},
            "useResponseListener": {"type": "boolean"},
        },
        "required": ["platform"],
    },
}

llm_with_tools: Optional[Any] = None
if not USE_MOCK_AGENT:
    from langchain_google_genai import ChatGoogleGenerativeAI

    llm = ChatGoogleGenerativeAI(
        model=GEMINI_MODEL,
        google_api_key=os.getenv("GEMINI_API_KEY"),
    )
    llm_with_tools = llm.bind_tools([INTEGRATE_SDK_TOOL])


def _tool_content_text(content_blocks) -> str:
    parts = []
    for block in content_blocks or []:
        if hasattr(block, "text"):
            parts.append(block.text)
        else:
            parts.append(str(block))
    return "\n".join(parts)


async def _list_mcp_tools_async() -> dict:
    params = build_mcp_server_params()
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools_result = await session.list_tools()
            tools = [
                {
                    "name": t.name,
                    "description": (t.description or "").strip().split("\n")[0][:120],
                }
                for t in tools_result.tools
            ]
            return {"success": True, "tools": tools}


def list_mcp_tools() -> dict:
    try:
        return asyncio.run(_list_mcp_tools_async())
    except Exception as e:
        return {"success": False, "error": str(e), "tools": []}


async def _call_mcp_tool_async(tool_name: str, args: dict) -> dict:
    params = build_mcp_server_params()
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool_name, args or {})
            text = _tool_content_text(result.content)
            return {
                "success": True,
                "tool": tool_name,
                "text": text,
                "is_error": bool(result.isError),
            }


def call_mcp(tool_name: str, args: dict) -> dict:
    try:
        return asyncio.run(_call_mcp_tool_async(tool_name, args))
    except Exception as e:
        return {"success": False, "error": str(e), "tool": tool_name}


def mock_agent_response(app_path: str) -> AIMessage:
    """סימולציה של איגנט שקורא ל-MCP integrateSdk (ללא Gemini)."""
    return AIMessage(
        content=f"Integrating AppsFlyer SDK for Android in {app_path}",
        tool_calls=[
            {
                "name": "integrateSdk",
                "args": {"platform": "android", "useResponseListener": False},
                "id": "mock-agent-1",
                "type": "tool_call",
            }
        ],
    )


def invoke_agent(prompt: str, app_path: str) -> tuple[Any, str, Optional[str]]:
    if USE_MOCK_AGENT:
        return mock_agent_response(app_path), "MOCK", None

    try:
        resp = llm_with_tools.invoke(prompt)
        return resp, "GEMINI", None
    except Exception as e:
        return mock_agent_response(app_path), "MOCK_FALLBACK", str(e)


def gradle_wrapper_cmd(app_path: str) -> Optional[str]:
    if os.path.exists(os.path.join(app_path, "gradlew.bat")):
        return os.path.join(app_path, "gradlew.bat")
    if os.path.exists(os.path.join(app_path, "gradlew")):
        return os.path.join(app_path, "gradlew")
    return None


def sdk_present_in_gradle(app_path: str) -> bool:
    gradle_files = [
        os.path.join(app_path, "app", "build.gradle"),
        os.path.join(app_path, "build.gradle"),
    ]
    dep_pattern = re.compile(r"implementation\s+.*appsflyer", re.IGNORECASE)
    for path in gradle_files:
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                if dep_pattern.search(f.read()):
                    return True
    return False

# =========================
# STATE (מבנה שטוח ופשוט ללוגים)
# =========================
class AgentTestState(TypedDict):
    app_id: str
    app_path: str
    agent_mode: str
    mcp_triggered: bool
    compilation_passed: bool
    sdk_verified: bool
    test_status: str
    last_agent_message: Any
    mcp_tools_used: Annotated[List[Dict[str, Any]], operator.add]
    mcp_tools_available: List[Dict[str, Any]]
    nodes_logs: Annotated[List[Dict[str, Any]], operator.add]
    report_path: str

# =========================
# NODES (צמתים עם בדיקות מובנות)
# =========================

def setup_environment(state: AgentTestState):
    print("[1] צומת: Setup Environment")
    logs = []

    if not os.path.exists(state["app_path"]):
        logs.append({"node": "setup", "status": "FAIL", "reason": f"Path missing: {state['app_path']}"})
        return {"nodes_logs": logs, "test_status": "FAIL"}

    logs.append({
        "node": "setup",
        "status": "SUCCESS",
        "message": "Workspace verified.",
        "agent_mode": "MOCK" if USE_MOCK_AGENT else "GEMINI",
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
        "agent_mode": "MOCK" if USE_MOCK_AGENT else "GEMINI",
        "mcp_tools_used": [mcp_tool_entry],
        "mcp_tools_available": available_tools,
    }


def run_agent_prompt(state: AgentTestState):
    print("[2] צומת: Run Agent Prompt")
    logs = []

    prompt = (
        f"Integrate the AppsFlyer SDK into the Android project at: {state['app_path']}. "
        "Use the integrateSdk tool with platform android."
    )

    resp, mode, fallback_reason = invoke_agent(prompt, state["app_path"])
    has_tool_call = bool(resp.tool_calls)

    log_entry = {
        "node": "agent_prompt",
        "status": "SUCCESS" if has_tool_call else "FAIL",
        "agent_mode": mode,
        "message": f"Tool calls detected: {has_tool_call}",
        "ai_content": resp.content,
    }
    if fallback_reason:
        log_entry["gemini_error"] = fallback_reason
        log_entry["message"] += " (Gemini failed — used mock agent)"
    logs.append(log_entry)

    return {"last_agent_message": resp, "nodes_logs": logs, "agent_mode": mode}


def verify_mcp_activation(state: AgentTestState):
    print("[3] צומת: Verify MCP Activation")
    logs = []
    msg = state["last_agent_message"]

    if not msg or not msg.tool_calls:
        logs.append({"node": "verify_mcp", "status": "FAIL", "reason": "No tool call from agent."})
        return {"mcp_triggered": False, "nodes_logs": logs}

    tool = msg.tool_calls[0]
    logs.append({"node": "verify_mcp", "status": "INFO", "message": f"Agent selected tool: {tool['name']}"})

    mcp_result = call_mcp(tool["name"], tool["args"])
    mcp_tool_entry = {
        "tool": tool["name"],
        "args": tool["args"],
        "phase": "agent_integration",
        "success": mcp_result.get("success", False),
        "is_error": mcp_result.get("is_error", False),
        "response_preview": (mcp_result.get("text") or "")[:200],
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
            "mcp_output_preview": (mcp_result.get("text") or "")[:500],
        })
        return {"mcp_triggered": False, "nodes_logs": logs, "mcp_tools_used": [mcp_tool_entry]}

    response_text = (mcp_result.get("text") or "").lower()
    mcp_has_sdk_content = "appsflyer" in response_text or "gradle" in response_text
    sdk_in_files = sdk_present_in_gradle(state["app_path"])

    logs.append({
        "node": "verify_mcp",
        "status": "SUCCESS",
        "message": "MCP tool executed successfully.",
        "mcp_output_preview": (mcp_result.get("text") or "")[:500],
        "mcp_has_sdk_content": mcp_has_sdk_content,
        "sdk_in_gradle_files": sdk_in_files,
    })

    return {
        "mcp_triggered": True,
        "sdk_verified": sdk_in_files or mcp_has_sdk_content,
        "nodes_logs": logs,
        "mcp_tools_used": [mcp_tool_entry],
    }


def check_compilation(state: AgentTestState):
    print("[4] צומת: Check Compilation")
    logs = []
    app_path = state["app_path"]
    gradlew = gradle_wrapper_cmd(app_path)

    if gradlew:
        result = subprocess.run(
            [gradlew, "assembleDebug"],
            cwd=app_path,
            capture_output=True,
            text=True,
            shell=os.name == "nt",
        )
        success = result.returncode == 0
        logs.append({
            "node": "compilation",
            "status": "SUCCESS" if success else "FAIL",
            "stdout_tail": result.stdout[-500:],
            "stderr_tail": result.stderr[-500:],
        })
    else:
        success = True
        logs.append({
            "node": "compilation",
            "status": "SUCCESS",
            "message": "No gradle wrapper found — skipped.",
        })

    return {"compilation_passed": success, "nodes_logs": logs}


def pass_node(state: AgentTestState):
    return {"test_status": "PASS", "nodes_logs": [{"node": "final_status", "status": "PASS"}]}

def fail_node(state: AgentTestState):
    return {"test_status": "FAIL", "nodes_logs": [{"node": "final_status", "status": "FAIL"}]}


def end_report(state: AgentTestState):
    print("[5] צומת: End Report")
    report = {
        "app_id": state["app_id"],
        "final_status": state["test_status"],
        "agent_mode": state.get("agent_mode", "UNKNOWN"),
        "use_mock_agent": USE_MOCK_AGENT,
        "mcp_triggered": state["mcp_triggered"],
        "sdk_verified": state["sdk_verified"],
        "compilation_passed": state["compilation_passed"],
        "mcp_tools_used": state.get("mcp_tools_used", []),
        "mcp_tools_available": state.get("mcp_tools_available", []),
        "files_modified_by_runner": False,
        "detailed_steps": state["nodes_logs"],
    }

    path = os.path.abspath("mcp_test_report.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=4, ensure_ascii=False)

    return {"report_path": path}

def print_mcp_tools_summary(final_result: dict) -> None:
    available = final_result.get("mcp_tools_available", [])
    used = final_result.get("mcp_tools_used", [])

    print("\n" + "=" * 60)
    print("MCP TOOLS — ALL AVAILABLE ON SERVER")
    print("=" * 60)
    if available:
        for i, tool in enumerate(available, 1):
            desc = tool.get("description", "")
            suffix = f" — {desc}" if desc else ""
            print(f"  {i:2}. {tool['name']}{suffix}")
        print(f"\nTotal available: {len(available)}")
    else:
        print("  (Could not load tool list — MCP may have failed at setup)")

    print("\n" + "=" * 60)
    print("MCP TOOLS — USED IN THIS RUN")
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

    print("\nNote: this runner calls MCP tools but does NOT edit project files.")
    print("      integrateSdk returns instructions; file changes need a separate apply step.")
    print("=" * 60)


def route_after_setup(state):
    return "run_agent_prompt" if state.get("test_status") != "FAIL" else "fail_node"

def route_after_agent(state):
    # אם המודל לא קרא לכלי, עוברים מיד לכישלון
    msg = state.get("last_agent_message")
    return "verify_mcp_activation" if (msg and msg.tool_calls) else "fail_node"

def route_after_mcp(state):
    return "check_compilation" if state["mcp_triggered"] else "fail_node"

def route_after_compilation(state):
    return "pass_node" if state["compilation_passed"] else "fail_node"

# =========================
# BUILD GRAPH
# =========================
workflow = StateGraph(AgentTestState)

workflow.add_node("setup_environment", setup_environment)
workflow.add_node("run_agent_prompt", run_agent_prompt)
workflow.add_node("verify_mcp_activation", verify_mcp_activation)
workflow.add_node("check_compilation", check_compilation)
workflow.add_node("pass_node", pass_node)
workflow.add_node("fail_node", fail_node)
workflow.add_node("end_report", end_report)

workflow.set_entry_point("setup_environment")

# הגדרת הניתובים עם תנאי עצירה בכל שלב
workflow.add_conditional_edges("setup_environment", route_after_setup)
workflow.add_conditional_edges("run_agent_prompt", route_after_agent)
workflow.add_conditional_edges("verify_mcp_activation", route_after_mcp)
workflow.add_conditional_edges("check_compilation", route_after_compilation)

workflow.add_edge("pass_node", "end_report")
workflow.add_edge("fail_node", "end_report")
workflow.add_edge("end_report", END)

compiled = workflow.compile()

# =========================
# EXECUTION
# =========================
if __name__ == "__main__":
    # יצירת תיקיית דמה לבדיקה אם היא לא קיימת
    dummy_app = os.path.abspath("./basic_app")
    os.makedirs(dummy_app, exist_ok=True)

    initial_state = {
        "app_id": "appsflyer-demo-test",
        "app_path": os.path.abspath("./basic_app"),
        "agent_mode": "MOCK" if USE_MOCK_AGENT else "GEMINI",
        "mcp_triggered": False,
        "compilation_passed": False,
        "sdk_verified": False,
        "test_status": "UNKNOWN",
        "last_agent_message": None,
        "mcp_tools_used": [],
        "mcp_tools_available": [],
        "nodes_logs": [],
        "report_path": "",
    }

    mode_label = "MOCK (no Gemini)" if USE_MOCK_AGENT else "GEMINI"
    print(f"\n--- MCP Test Runner | Agent mode: {mode_label} ---\n")

    final_result = compiled.invoke(initial_state)
    print(f"\nTest finished. Status: {final_result['test_status']}")
    print(f"Report: {final_result['report_path']}")
    print_mcp_tools_summary(final_result)