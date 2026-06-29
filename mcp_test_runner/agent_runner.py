import os

from langchain_google_genai import ChatGoogleGenerativeAI

from .config import GEMINI_MODEL, INTEGRATE_SDK_TOOL

llm = ChatGoogleGenerativeAI(
    model=GEMINI_MODEL,
    google_api_key=os.getenv("GEMINI_API_KEY"),
)

llm_with_tools = llm.bind_tools([INTEGRATE_SDK_TOOL])


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
