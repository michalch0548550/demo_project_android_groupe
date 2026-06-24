import json
import os
import re
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI

load_dotenv()

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

DEFAULT_FILES_TO_READ = [
    "app/build.gradle",
    "build.gradle",
    "settings.gradle",
    "app/src/main/AndroidManifest.xml",
]

APPLY_PROMPT = """You are a code applier for an Android project.
The AppsFlyer MCP server returned SDK integration instructions.
Apply those instructions to the project files below.

Project root: {app_path}
DEV_KEY (use this exact value in code): {dev_key}
APP_ID (use this exact value in code): {app_id}

=== MCP INTEGRATION INSTRUCTIONS ===
{mcp_instructions}

=== CURRENT PROJECT FILES ===
{files_block}

Return ONLY a valid JSON array. Each item must have:
- "file": path relative to project root (forward slashes)
- "content": the complete new file content after applying the integration

Rules:
- Include every file that must change for the SDK integration.
- Do not include files that stay unchanged.
- Do not wrap the JSON in markdown fences.
- Do not add explanations outside the JSON.
"""


def _find_application_java(app_path: str) -> Optional[str]:
    java_root = os.path.join(app_path, "app", "src", "main", "java")
    if not os.path.isdir(java_root):
        return None
    for root, _, files in os.walk(java_root):
        for name in files:
            if name.endswith("Application.java") or name.endswith("App.java"):
                rel = os.path.relpath(os.path.join(root, name), app_path)
                return rel.replace("\\", "/")
    return None


def collect_project_context(app_path: str) -> Dict[str, str]:
    files: Dict[str, str] = {}
    candidates = list(DEFAULT_FILES_TO_READ)
    app_java = _find_application_java(app_path)
    if app_java:
        candidates.append(app_java)

    for rel_path in candidates:
        full_path = os.path.join(app_path, rel_path.replace("/", os.sep))
        if os.path.isfile(full_path):
            with open(full_path, encoding="utf-8") as f:
                files[rel_path] = f.read()
    return files


def _extract_json_array(text: str) -> List[Dict[str, Any]]:
    text = (text or "").strip()
    if not text:
        raise ValueError("Empty model response")

    attempts = [text]
    fenced = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL | re.IGNORECASE)
    if fenced:
        attempts.insert(0, fenced.group(1).strip())

    array_match = re.search(r"\[\s*\{.*\}\s*\]", text, re.DOTALL)
    if array_match:
        attempts.append(array_match.group(0))

    for candidate in attempts:
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(data, list):
            return data

    raise ValueError("Could not parse JSON array from model response")


def request_file_changes(
    mcp_instructions: str,
    project_files: Dict[str, str],
    app_path: str,
    dev_key: str,
    app_id: str,
) -> List[Dict[str, str]]:
    files_block = "\n\n".join(
        f"--- {rel_path} ---\n{content}" for rel_path, content in project_files.items()
    )
    prompt = APPLY_PROMPT.format(
        app_path=app_path,
        dev_key=dev_key,
        app_id=app_id,
        mcp_instructions=mcp_instructions,
        files_block=files_block,
    )

    llm = ChatGoogleGenerativeAI(
        model=GEMINI_MODEL,
        google_api_key=os.getenv("GEMINI_API_KEY"),
    )
    response = llm.invoke(prompt)
    content = response.content if hasattr(response, "content") else str(response)
    raw_changes = _extract_json_array(content)

    changes: List[Dict[str, str]] = []
    for item in raw_changes:
        if not isinstance(item, dict):
            continue
        rel_path = str(item.get("file", "")).replace("\\", "/").lstrip("/")
        file_content = item.get("content")
        if rel_path and file_content is not None:
            changes.append({"file": rel_path, "content": str(file_content)})
    return changes


def apply_file_changes(app_path: str, changes: List[Dict[str, str]]) -> List[str]:
    applied: List[str] = []
    app_root = os.path.abspath(app_path)

    for change in changes:
        rel_path = change["file"]
        full_path = os.path.abspath(os.path.join(app_root, rel_path.replace("/", os.sep)))
        if os.path.commonpath([app_root, full_path]) != app_root:
            continue

        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(change["content"])
        applied.append(rel_path)

    return applied


def apply_sdk_changes(state: dict) -> dict:
    """LangGraph node: translate MCP instructions into project file edits."""
    print("[4] Node: Apply SDK Changes")
    logs: List[Dict[str, Any]] = []
    app_path = state["app_path"]
    mcp_text = (state.get("mcp_integration_text") or "").strip()

    if not mcp_text:
        logs.append({
            "node": "apply_sdk",
            "status": "FAIL",
            "reason": "No MCP integration text available to apply.",
        })
        return {"files_modified": False, "applied_files": [], "nodes_logs": logs}

    if mcp_text.startswith("❌") or "parameter is required" in mcp_text.lower():
        logs.append({
            "node": "apply_sdk",
            "status": "FAIL",
            "reason": "MCP returned an error instead of integration instructions.",
            "mcp_output_preview": mcp_text[:500],
        })
        return {"files_modified": False, "applied_files": [], "nodes_logs": logs}

    dev_key = (os.getenv("DEV_KEY") or "").strip('"').strip("'")
    app_id = (os.getenv("APP_ID") or state.get("app_id") or "").strip('"').strip("'")

    try:
        project_files = collect_project_context(app_path)
        changes = request_file_changes(mcp_text, project_files, app_path, dev_key, app_id)
        applied_files = apply_file_changes(app_path, changes)
    except Exception as exc:
        logs.append({
            "node": "apply_sdk",
            "status": "FAIL",
            "reason": str(exc),
        })
        return {"files_modified": False, "applied_files": [], "nodes_logs": logs}

    if not applied_files:
        logs.append({
            "node": "apply_sdk",
            "status": "FAIL",
            "reason": "Gemini returned no applicable file changes.",
        })
        return {"files_modified": False, "applied_files": [], "nodes_logs": logs}

    logs.append({
        "node": "apply_sdk",
        "status": "SUCCESS",
        "message": f"Applied SDK integration to {len(applied_files)} file(s).",
        "applied_files": applied_files,
    })

    return {
        "files_modified": True,
        "applied_files": applied_files,
        "nodes_logs": logs,
    }
