"""
Answer-only node for AppsFlyer SDK installation questions (Android).

Single responsibility:
    Receive ONE practical installation question (a prompt the upstream LLM/MCP
    asked the user) and return an answer.

This node does NOT decide whether the incoming text is a "practical question".
That filtering is the job of a separate upstream node. Success / failure
messages never reach this node - only practical questions do.

Fixed developer decisions for this project:
    - Platform:               Android
    - Deep Linking / OneLink: NO
    - Response Listener:      YES (useResponseListener = True)
    - Dev Key:                use the real DEV_KEY from .env
"""
import os
import re
import json
from typing import Any, Dict, List, Optional

from langchain_core.prompts import PromptTemplate
from langchain_google_genai import ChatGoogleGenerativeAI

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
MAX_QUESTION_ROUNDS = int(os.getenv("MAX_QUESTION_ROUNDS", "4"))

QUESTION_HINTS = {
    "platform": [
        r"\bandroid\b.*\bios\b",
        r"\bios\b.*\bandroid\b",
        r"which platform",
        r"identify the platform",
        r"פלטפורמה",
    ],
    "response_listener": [
        r"response\s*listener",
        r"useresponselistener",
        r"attribution callback",
        r"אתחול.*listener",
    ],
    "onelink_deeplink": [
        r"onelink",
        r"deep\s*link",
        r"deeplink",
        r"הרחבת.*הטמעה",
    ],
    "dev_key": [
        r"dev\s*key",
        r"devkey",
        r"placeholder",
        r"מפתח",
        r"access key",
    ],
}

ANSWER_PROMPT = PromptTemplate.from_template(
    "You answer AppsFlyer SDK installation questions on behalf of the developer.\n"
    "Be short, decisive, and technical. Return ONLY the answer text.\n\n"
    "Fixed developer decisions for this project:\n"
    "- Platform: Android\n"
    "- Deep Linking / OneLink: NO\n"
    "- Response Listener: YES (useResponseListener = true)\n"
    "- Dev Key: use the real DEV_KEY from .env\n\n"
    "Question:\n{question}\n\n"
    "Project / environment context:\n{context}\n\n"
    "Rules:\n"
    "- Always stay consistent with the fixed developer decisions above.\n"
    "- If the question expects a True/False answer, reply with just True or False.\n"
    "- Prefer concrete values from the context when available.\n"
    "- If truly unknown, say: Use your best professional judgment and proceed.\n"
)


def _llm() -> ChatGoogleGenerativeAI:
    return ChatGoogleGenerativeAI(
        model=GEMINI_MODEL,
        temperature=0.1,
        google_api_key=os.getenv("GEMINI_API_KEY"),
    )


def classify_question(question: str) -> str:
    lower = (question or "").lower()
    for category, patterns in QUESTION_HINTS.items():
        if any(re.search(p, lower) for p in patterns):
            return category
    return "general"


VALID_LABEL = {"SUCCESS", "FAIL", "QUESTION"}
VALID_NEXT = {"run_agent_prompt", "fail_node", "answer_question"}
LABEL_TO_NEXT = {
    "SUCCESS": "run_agent_prompt",
    "FAIL": "fail_node",
    "QUESTION": "answer_question",
}

CLASSIFY_PROMPT = PromptTemplate.from_template(
    "You are a strict classifier inside an automated AppsFlyer SDK installation pipeline.\n"
    "An installation agent (another LLM) is trying to install the AppsFlyer Android SDK.\n"
    "Read its latest message and decide what the pipeline should do next.\n\n"
    "Labels:\n"
    "- SUCCESS: the agent says it finished / completed the SDK installation.\n"
    "- FAIL: the agent says it could NOT install the SDK / gave up / hit an unrecoverable error.\n"
    "- QUESTION: the agent is asking the developer ANY technical question it needs answered to continue.\n\n"
    "Routing (the 'next' node):\n"
    "- SUCCESS -> run_agent_prompt\n"
    "- FAIL -> fail_node\n"
    "- QUESTION -> answer_question\n\n"
    "If the message is ambiguous or unexpected, use the STATE snapshot below to pick the safest next step, "
    "and still return one of the allowed 'next' values.\n"
    "Allowed next values: run_agent_prompt, fail_node, answer_question.\n\n"
    "Agent message:\n{agent_text}\n\n"
    "STATE snapshot (JSON):\n{state_snapshot}\n\n"
    "Return ONLY a JSON object (no markdown, no extra text) with exactly these keys:\n"
    '{{"label": "SUCCESS|FAIL|QUESTION", "question": "<the question text or empty>", '
    '"next": "run_agent_prompt|fail_node|answer_question", "reason": "<short reason>"}}\n'
)


def _state_snapshot(state: dict) -> str:
    """Compact, JSON-safe view of the run so the classifier can decide edge cases."""
    msg = state.get("last_agent_message")
    snapshot = {
        "mcp_triggered": state.get("mcp_triggered", False),
        "question_rounds": state.get("question_rounds", 0),
        "installation_answers_count": len(state.get("installation_answers", []) or []),
        "files_modified": state.get("files_modified", False),
        "last_message_had_tool_calls": bool(getattr(msg, "tool_calls", None)),
    }
    return json.dumps(snapshot, ensure_ascii=False)


def _parse_json_object(text: str) -> Optional[dict]:
    """Best-effort JSON extraction: try whole string, then the first {...} block."""
    if not text:
        return None
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        pass
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except (ValueError, TypeError):
            return None
    return None


def classify_agent_response(state: dict, agent_text: str) -> Dict[str, str]:
    """LLM #2: classify the installation agent's message into SUCCESS / FAIL / QUESTION.

    Always returns a dict with keys: label, question, next, reason.
    On broken JSON / invalid routing it falls back to fail_node (safe default).
    """
    agent_text = (agent_text or "").strip()
    if not agent_text:
        return {
            "label": "FAIL",
            "question": "",
            "next": "fail_node",
            "reason": "Empty agent message - nothing to classify.",
        }

    prompt = CLASSIFY_PROMPT.format(
        agent_text=agent_text[:4000],
        state_snapshot=_state_snapshot(state),
    )
    try:
        response = _llm().invoke(prompt)
        raw = response.content if hasattr(response, "content") else str(response)
        if isinstance(raw, list):
            raw = " ".join(str(getattr(x, "text", x)) for x in raw)
    except Exception as e:
        return {
            "label": "FAIL",
            "question": "",
            "next": "fail_node",
            "reason": f"Classifier LLM call failed: {e}",
        }

    parsed = _parse_json_object(str(raw).strip())
    if not isinstance(parsed, dict):
        return {
            "label": "FAIL",
            "question": "",
            "next": "fail_node",
            "reason": "Classifier returned invalid JSON.",
        }

    label = str(parsed.get("label", "")).strip().upper()
    nxt = str(parsed.get("next", "")).strip()
    question = str(parsed.get("question", "")).strip()
    reason = str(parsed.get("reason", "")).strip()

    if label not in VALID_LABEL:
        label = ""
    if nxt not in VALID_NEXT:
        nxt = LABEL_TO_NEXT.get(label, "")
    if not nxt:
        return {
            "label": label or "FAIL",
            "question": question,
            "next": "fail_node",
            "reason": reason or "Unrecognized label/next - defaulting to fail_node.",
        }
    if not label:
        label = {v: k for k, v in LABEL_TO_NEXT.items()}.get(nxt, "QUESTION")

    return {"label": label, "question": question, "next": nxt, "reason": reason}


def _wants_boolean(question: str) -> bool:
    lower = (question or "").lower()
    return bool(re.search(r"true\s*/\s*false", lower)) or "(true/false)" in lower


def _dev_key() -> str:
    return (os.getenv("DEV_KEY") or "").strip('"').strip("'")


def _deterministic_answer(category: str, question: str) -> Optional[str]:
    if category == "platform":
        return "Android."

    if category == "response_listener":
        if _wants_boolean(question):
            return "True"
        return (
            "Yes - enable the Response Listener (useResponseListener=true) "
            "when initializing the AppsFlyer SDK on Android."
        )

    if category == "onelink_deeplink":
        return (
            "No - do not add OneLink / Deep Linking. "
            "Keep the basic AppsFlyer SDK integration only."
        )

    if category == "dev_key":
        dev_key = _dev_key()
        if dev_key:
            return (
                f"Use the real Dev Key from the project's .env: {dev_key}. "
                "Keep it in .env (do not hardcode or commit it) and load it via "
                "environment / BuildConfig."
            )
        return "Use the DEV_KEY value configured in the project's .env file."

    return None


def scan_app_files(app_path: str) -> Dict[str, Any]:
    """Read real project files so the LLM can ground answers to non-standard questions."""
    info: Dict[str, Any] = {
        "has_gradle": False,
        "appsflyer_sdk_installed": False,
        "application_class": None,
        "has_conversion_data_activity": False,
        "has_onelink_activities": False,
        "package": None,
    }
    if not app_path or not os.path.isdir(app_path):
        return info

    manifest = os.path.join(app_path, "app", "src", "main", "AndroidManifest.xml")
    if os.path.isfile(manifest):
        try:
            with open(manifest, encoding="utf-8") as f:
                manifest_text = f.read()
            info["has_conversion_data_activity"] = "ConversionDataActivity" in manifest_text
            info["has_onelink_activities"] = any(
                name in manifest_text
                for name in ("ApplesActivity", "BananasActivity", "PeachesActivity", "FruitActivity")
            )
            if not info["package"]:
                m = re.search(r'package="([^"]+)"', manifest_text)
                if m:
                    info["package"] = m.group(1)
        except OSError:
            pass

    java_root = os.path.join(app_path, "app", "src", "main", "java")
    if os.path.isdir(java_root):
        for root, _, files in os.walk(java_root):
            for name in files:
                if name.endswith("Application.java") or name.endswith("App.java"):
                    info["application_class"] = os.path.relpath(
                        os.path.join(root, name), app_path
                    ).replace("\\", "/")
                    break
            if info["application_class"]:
                break

    return info


def _project_context(state: dict) -> str:
    app_path = state.get("app_path", "")
    dev_key = _dev_key()
    app_id = (os.getenv("APP_ID") or state.get("app_id") or "").strip('"').strip("'")
    scan = scan_app_files(app_path)
    mcp_text = (state.get("mcp_integration_text") or "").strip()

    lines = [
        "platform: android",
        f"app_path: {app_path}" if app_path else "",
        f"app_id: {app_id}" if app_id else "",
        f"package: {scan['package']}" if scan.get("package") else "",
        f"dev_key_configured: {bool(dev_key)}",
        "deep_linking: disabled",
        "response_listener: enabled",
        f"appsflyer_sdk_installed: {scan['appsflyer_sdk_installed']}",
        f"application_class: {scan['application_class']}" if scan.get("application_class") else "",
        f"has_conversion_data_activity: {scan['has_conversion_data_activity']}",
        f"has_onelink_activities: {scan['has_onelink_activities']}",
    ]
    context = "\n".join(line for line in lines if line)

    if mcp_text:
        context += f"\n\nMCP integration instructions (already returned):\n{mcp_text[:1500]}"

    return context


def answer_question(state: dict, question: str) -> str:
    category = classify_question(question)
    print(f"[2b] answer context: category={category}")

    deterministic = _deterministic_answer(category, question)
    if deterministic:
        print("[2b] answer source: deterministic")
        return deterministic

    print("[2b] answer source: llm")
    prompt = ANSWER_PROMPT.format(
        question=question.strip(),
        context=_project_context(state),
    )
    response = _llm().invoke(prompt)
    content = response.content if hasattr(response, "content") else str(response)
    answer = (content or "").strip() or "Use your best professional judgment and proceed."
    print(f"[2b] answer source: llm end: answer_len={len(answer)}")
    return answer


def build_prompt_with_answers(base_prompt: str, installation_answers: List[Dict[str, str]]) -> str:
    if not installation_answers:
        return base_prompt
    qa_lines = [f"Q: {e['question']}\nA: {e['answer']}" for e in installation_answers]
    return (
        f"{base_prompt}\n\n"
        "Installation clarifications already provided by the developer:\n"
        f"{chr(10).join(qa_lines)}\n\n"
        "Use these answers and call the integrateSdk MCP tool now. "
        "Do not ask the same questions again."
    )


def answer_question_node(state: dict) -> dict:
    """LangGraph node: answer ONE practical installation question.

    Reads the question from state["incoming_question"].
    Does NOT filter / validate whether it is a practical question.
    """
    print("[2b] Node: Answer Practical Question")
    logs: List[Dict[str, Any]] = []

    question = (state.get("incoming_question") or "").strip()
    if not question:
        logs.append({
            "node": "answer_question",
            "status": "SKIP",
            "reason": "No incoming_question provided.",
        })
        return {"nodes_logs": logs}

    question_rounds = state.get("question_rounds", 0) + 1
    if question_rounds > MAX_QUESTION_ROUNDS:
        logs.append({
            "node": "answer_question",
            "status": "FAIL",
            "reason": f"Exceeded max question rounds ({MAX_QUESTION_ROUNDS}). Human intervention required.",
            "question_rounds": question_rounds,
        })
        print(f"[2b] exit: FAIL reason=max_rounds rounds={question_rounds}")
        return {"nodes_logs": logs, "question_rounds": question_rounds, "test_status": "FAIL"}

    category = classify_question(question)
    answer = answer_question(state, question)

    qa_entry = {
        "question": question[:500],
        "answer": answer,
        "category": category,
        "round": question_rounds,
    }

    logs.append({
        "node": "answer_question",
        "status": "SUCCESS",
        "message": f"Answered installation question (round {question_rounds}).",
        "category": category,
        "question_preview": question[:200],
        "answer": answer,
    })
    print(f"[2b] exit: SUCCESS round={question_rounds} category={category}")

    return {
        "nodes_logs": logs,
        "question_rounds": question_rounds,
        "installation_answers": [qa_entry],
    }