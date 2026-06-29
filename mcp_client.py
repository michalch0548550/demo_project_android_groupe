import asyncio
import os

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import get_default_environment, stdio_client

from config import DEFAULT_APP_ID


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
                    "name": tool.name,
                    "description": (tool.description or "").strip().split("\n")[0][:120],
                }
                for tool in tools_result.tools
            ]
            return {"success": True, "tools": tools}


def list_mcp_tools() -> dict:
    try:
        return asyncio.run(_list_mcp_tools_async())
    except Exception as exc:
        return {"success": False, "error": str(exc), "tools": []}


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
    except Exception as exc:
        return {"success": False, "error": str(exc), "tool": tool_name}
