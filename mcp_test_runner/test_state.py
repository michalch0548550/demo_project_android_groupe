import operator
from typing import Annotated, Any, Dict, List, TypedDict


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
    incoming_question: str
    question_rounds: int
    installation_answers: Annotated[List[Dict[str, str]], operator.add]
