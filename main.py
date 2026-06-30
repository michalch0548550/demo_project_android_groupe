from workflow_builder import build_workflow, initial_state, print_mcp_tools_summary


def main() -> None:
    print("\n--- MCP Test Runner | Agent: GEMINI ---\n")

    workflow = build_workflow()
    final_result = workflow.invoke(initial_state())

    print(f"\nTest finished. Status: {final_result['test_status']}")
    print(f"Report: {final_result['report_path']}")
    print_mcp_tools_summary(final_result)
    
if __name__ == "__main__":
    main()
