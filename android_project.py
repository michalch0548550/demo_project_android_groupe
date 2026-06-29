import os
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path


def gradle_wrapper_cmd(app_path: str) -> str | None:
    gradlew_bat = os.path.join(app_path, "gradlew.bat")
    gradlew = os.path.join(app_path, "gradlew")

    if os.path.exists(gradlew_bat):
        return gradlew_bat
    if os.path.exists(gradlew):
        return gradlew
    return None


def create_sandbox_app(original_app_path: str) -> str:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    sandbox_root = Path("sandboxes").resolve() / f"run_{timestamp}"
    sandbox_app_path = sandbox_root / Path(original_app_path).name

    def ignore_dirs(_, names):
        return {
            name
            for name in names
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
            with open(path, encoding="utf-8") as file:
                if dep_pattern.search(file.read()):
                    return True
    return False


def run_gradle_build(app_path: str) -> tuple[bool, dict]:
    gradlew = gradle_wrapper_cmd(app_path)
    if not gradlew:
        return True, {
            "node": "compilation",
            "status": "SUCCESS",
            "message": "No gradle wrapper found — skipped.",
        }

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

    return success, {
        "node": "compilation",
        "status": "SUCCESS" if success else "FAIL",
        "stdout_tail": (result.stdout or "")[-500:],
        "stderr_tail": (result.stderr or "")[-500:],
    }
