import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parent
REPOSITORY = "https://github.com/hfm4050/OPPA.git"


def main():
    env = os.environ.copy()
    data = Path(env.get("OPPA_STUDIO_DATA", ROOT)).resolve()
    env["OPPA_STUDIO_DATA"] = str(data)
    git_env = {**env, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "Never"}
    checkout = data / ".oppa-update"
    downloaded = checkout / "web.py"
    trusted_checkout = False
    try:
        if (checkout / ".git").is_dir():
            remote = subprocess.check_output(
                ["git", "-C", str(checkout), "remote", "get-url", "origin"],
                env=git_env, text=True, timeout=5,
            ).strip()
            if remote != REPOSITORY:
                raise ValueError("업데이트 저장소 주소가 일치하지 않습니다.")
            trusted_checkout = True
            command = ["git", "-C", str(checkout), "pull", "--ff-only"]
        else:
            command = ["git", "clone", "--depth", "1", REPOSITORY, str(checkout)]
        print("OPPA STUDIO 업데이트 확인 중…", flush=True)
        subprocess.run(command, env=git_env, check=True, timeout=35)
        trusted_checkout = True
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        print(f"업데이트 확인에 실패했습니다. 보관된 버전을 실행합니다. ({error})", flush=True)
    entrypoint = downloaded if trusted_checkout and downloaded.is_file() else ROOT / "web.py"
    return subprocess.call([sys.executable, "-X", "utf8", str(entrypoint)], cwd=entrypoint.parent, env=env)


if __name__ == "__main__":
    sys.exit(main())
