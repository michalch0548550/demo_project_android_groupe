import os

import json

import re

import asyncio

import operator

import subprocess

import shutil

from pathlib import Path

from datetime import datetime

from typing import TypedDict, List, Dict, Any, Annotated


from responseAgent import classify_question  # אם תרצי להשתמש בו לזיהוי

from responseAgent import build_prompt_with_answers

from responseAgent import classify_agent_response, MAX_QUESTION_ROUNDS

from langgraph.graph import StateGraph, END

from langchain_google_genai import ChatGoogleGenerativeAI

from dotenv import load_dotenv

from mcp import ClientSession, StdioServerParameters

from mcp.client.stdio import stdio_client, get_default_environment



from apply_sdk_changes import apply_sdk_changes
from PromptsAgent import get_agent_prompt

load_dotenv()



# =========================

# CONFIG

# =========================

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

        "required": ["platform", "useResponseListener"],

    },

}



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





def invoke_agent(prompt: str):

    return llm_with_tools.invoke(prompt)


def agent_content_text(content) -> str:

    if isinstance(content, str):

        return content

    if isinstance(content, list):

        parts = []

        for item in content:

            if isinstance(item, dict):

                parts.append(str(item.get("text") or item))

            else:

                parts.append(str(item))

        return "\n".join(parts)

    return str(content or "")


def force_integrate_sdk_prompt(app_path: str) -> str:

    return (
        "Proceed without asking any clarification questions. "
        "Call the AppsFlyer MCP tool integrateSdk now with exactly these arguments: "
        "platform=android and useResponseListener=false. "
        f"The Android project path is `{app_path}`."
    )





def gradle_wrapper_cmd(app_path: str) -> str | None:

    if os.path.exists(os.path.join(app_path, "gradlew.bat")):

        return os.path.join(app_path, "gradlew.bat")

    if os.path.exists(os.path.join(app_path, "gradlew")):

        return os.path.join(app_path, "gradlew")

    return None



def create_sandbox_app(original_app_path: str) -> str:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    sandbox_root = Path("sandboxes").resolve() / f"run_{timestamp}"
    sandbox_app_path = sandbox_root / Path(original_app_path).name
    def ignore_dirs(_, names):
        return {
            name for name in names
            if name in {"build", ".gradle", ".git", "__pycache__", ".venv"}
        }
    shutil.copytree(original_app_path, sandbox_app_path, ignore=ignore_dirs)
    return str(sandbox_app_path.resolve())


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

    original_app_path: str

    sandbox_path: str

    agent_mode: str

    mcp_triggered: bool

    mcp_integration_text: str

    files_modified: bool

    applied_files: List[str]

    compilation_passed: bool

    sdk_verified: bool

    test_status: str

    last_agent_message: Any

    mcp_tools_used: Annotated[List[Dict[str, Any]], operator.add]

    mcp_tools_available: List[Dict[str, Any]]

    nodes_logs: Annotated[List[Dict[str, Any]], operator.add]

    report_path: str


    stream_decision: str

    agent_base_prompt: str


# =========================

# NODES (צמתים עם בדיקות מובנות)

# =========================



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
    """STREAM node: the ONLY node that talks to the installation LLM.

    Streams the agent's reply chunk-by-chunk, then routes:
      - tool call(s)        -> run_agent_prompt
      - SUCCESS text        -> run_agent_prompt
      - FAIL text           -> fail_node
      - technical QUESTION  -> answer_question (max MAX_QUESTION_ROUNDS, else fail_node)
    """
    print("[2] צומת: Stream Listen (LLM radar)")
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

    print("[2c] צומת: Run Agent Prompt (verify tool calls only)")

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

    logs = []

    app_path = state["app_path"]

    gradlew = gradle_wrapper_cmd(app_path)



    if gradlew:

        gradle_user_home = Path(app_path) / ".gradle-user-home"

        gradle_user_home.mkdir(parents=True, exist_ok=True)

        gradle_env = os.environ.copy()

        gradle_env["GRADLE_USER_HOME"] = str(gradle_user_home)

        result = subprocess.run(

            [gradlew, "--no-daemon", "assembleDebug"],

            cwd=app_path,

            capture_output=True,

            text=True,

            encoding="utf-8",

            errors="replace",

            env=gradle_env,

            shell=os.name == "nt",

        )

        success = result.returncode == 0

        logs.append({

            "node": "compilation",

            "status": "SUCCESS" if success else "FAIL",

            "stdout_tail": (result.stdout or "")[-500:],

            "stderr_tail": (result.stderr or "")[-500:],

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



# =========================

# BUILD GRAPH

# =========================

workflow = StateGraph(AgentTestState)



workflow.add_node("setup_environment", setup_environment)

workflow.add_node("stream_listen", stream_listen)

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

workflow.add_conditional_edges("run_agent_prompt", route_after_agent)

workflow.add_conditional_edges("verify_mcp_activation", route_after_mcp)

workflow.add_conditional_edges("apply_sdk_changes", route_after_apply)

workflow.add_conditional_edges("check_compilation", route_after_compilation)



workflow.add_edge("pass_node", "end_report")

workflow.add_edge("fail_node", "end_report")

workflow.add_edge("end_report", END)



compiled = workflow.compile()



# =========================

# EXECUTION

# =========================

if __name__ == "__main__":

    dummy_app = os.path.abspath("./basic_app")

    os.makedirs(dummy_app, exist_ok=True)



    initial_state = {

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

    }



    print("\n--- MCP Test Runner | Agent: GEMINI ---\n")



    final_result = compiled.invoke(initial_state)

    print(f"\nTest finished. Status: {final_result['test_status']}")

    print(f"Report: {final_result['report_path']}")

    print_mcp_tools_summary(final_result)


