import os

from dotenv import load_dotenv

load_dotenv()

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
DEFAULT_APP_ID = "com.appsflyer.onelink.appsflyeronelinkbasicapp"
REPORT_FILE = "mcp_test_report.json"

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
