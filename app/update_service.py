# -*- coding: utf-8 -*-
"""GitHub 업데이트: 원격 확인 → 승인 후 pull → 재시작."""
import os
import subprocess
import threading
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# GitHub 저장소 이름이 바뀌면 예전 이름을 여기에 추가한다.
REPO_NAME = "Computer_Quote_X.16"
OLD_REPO_NAMES = ("Computer_Quote_9.0", "Computer_Quote_X.15")
# 공개 저장소라 로그인 없이 받을 수 있다. 다른 PC 의 origin 이 SSH 주소이거나
# 사용자 이름이 붙은 주소(https://아이디@github.com/...)면 Git 이 로그인 창을 띄우므로,
# 업데이트는 항상 이 익명 HTTPS 주소에서 받는다.
REPO_URL = f"https://github.com/Kimjinsoo62/{REPO_NAME}.git"

# 로그인 창 · 터미널 암호 입력을 모두 막는 환경 변수(실패하면 바로 오류로 돌아온다).
_NO_PROMPT_ENV = {
    "GIT_TERMINAL_PROMPT": "0",
    "GCM_INTERACTIVE": "never",
    "GIT_ASKPASS": "echo",
    "SSH_ASKPASS": "echo",
}
# 전역 자격 증명 도우미(Git Credential Manager · gh)를 이 명령에서만 끈다.
_NO_CREDENTIAL = ("-c", "credential.helper=", "-c", "credential.interactive=false")


def _git(*args: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *_NO_CREDENTIAL, *args],
        cwd=BASE_DIR,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        creationflags=_NO_WINDOW,
        env={**os.environ, **_NO_PROMPT_ENV},
    )


# GitHub 에서 ZIP 으로 받은 설치본에는 .git 폴더가 없어 fetch/pull 을 할 수 없다.
# 어느 커밋에서 받은 ZIP 인지 알 수 없으므로 저장소로 바꾸지 않고 안내만 한다.
ZIP_INSTALL_MESSAGE = (
    "ZIP 파일로 받은 설치본이라 [업데이트]를 쓸 수 없습니다.\n\n"
    "GitHub 에서 새 ZIP 을 받아 압축을 풀고 install.bat → START.bat 으로 다시 설치하거나,\n"
    "git clone 으로 설치하면 다음부터 [업데이트] 버튼을 쓸 수 있습니다.\n"
    "(견적정보 · 거래처 · 작업내역서 · 보관함 파일은 새 폴더로 복사해 옮기세요)"
)


def _is_git_install() -> bool:
    return (BASE_DIR / ".git").exists()


def _upstream() -> str:
    r = _git("rev-parse", "--abbrev-ref", "@{u}")
    return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else "origin/main"


def _migrate_remote(remote: str) -> None:
    """원격 주소를 로그인이 필요 없는 공개 HTTPS 주소(REPO_URL)로 맞춘다.

    예전 저장소 이름 · SSH 주소(git@github.com:...) · 아이디가 붙은 주소 모두 바꾼다
    (GitHub 리다이렉트나 자격 증명에 기대지 않도록). 다른 저장소를 가리키면 건드리지 않는다.
    """
    url = _git("remote", "get-url", remote).stdout.strip()
    if not url:
        _git("remote", "add", remote, REPO_URL)
        return
    names = (REPO_NAME, *OLD_REPO_NAMES)
    tail = url.lower().rstrip("/")
    if tail.endswith(".git"):
        tail = tail[:-4]
    if "github.com" in tail and any(tail.endswith("/" + n.lower()) for n in names):
        if url != REPO_URL:
            _git("remote", "set-url", remote, REPO_URL)


def _fetch(remote: str, branch: str, timeout: int) -> subprocess.CompletedProcess:
    """REPO_URL 에서 익명으로 받아 원격 추적 브랜치(origin/main)를 갱신한다."""
    return _git("fetch", REPO_URL, f"+refs/heads/{branch}:refs/remotes/{remote}/{branch}", timeout=timeout)


def _auth_hint(text: str) -> str:
    low = text.lower()
    if any(k in low for k in ("authentication", "password", "could not read username", "terminal prompts disabled", "403", "401")):
        return text + "\n\n(저장소가 비공개로 바뀌었거나 주소가 잘못되었습니다. 저장소가 공개 상태인지 확인하세요.)"
    return text


def check_update() -> dict:
    """원격 저장소를 fetch 해 받을 커밋이 있는지 알려 준다."""
    if not _is_git_install():
        return {"ok": False, "message": ZIP_INSTALL_MESSAGE}
    try:
        upstream = _upstream()
        remote, branch = upstream.split("/", 1)
        _migrate_remote(remote)
        f = _fetch(remote, branch, timeout=90)
        if f.returncode != 0:
            return {"ok": False, "message": "원격 저장소 확인 실패:\n" + _auth_hint((f.stderr or f.stdout).strip())}
        log = _git("log", "--pretty=format:%h %ad %s", "--date=short", f"HEAD..{upstream}")
        commits = [line for line in log.stdout.splitlines() if line.strip()]
        dirty = bool(_git("status", "--porcelain", "--untracked-files=no").stdout.strip())
        return {
            "ok": True,
            "upstream": upstream,
            "behind": len(commits),
            "commits": commits[:30],
            "dirty": dirty,
        }
    except FileNotFoundError:
        return {"ok": False, "message": "Git이 설치되어 있지 않습니다."}
    except subprocess.TimeoutExpired:
        return {"ok": False, "message": "원격 저장소 확인 시간이 초과되었습니다. 네트워크를 확인하세요."}


def apply_update() -> dict:
    """fast-forward pull 로 갱신하고, 의존성 파일이 바뀌었는지 알려 준다."""
    if not _is_git_install():
        return {"ok": False, "message": ZIP_INSTALL_MESSAGE}
    try:
        upstream = _upstream()
        remote, branch = upstream.split("/", 1)
        _migrate_remote(remote)
        before = _git("rev-parse", "HEAD").stdout.strip()
        f = _fetch(remote, branch, timeout=180)
        if f.returncode != 0:
            return {"ok": False, "message": "업데이트(pull) 실패:\n" + _auth_hint((f.stderr or f.stdout).strip())}
        p = _git("merge", "--ff-only", upstream, timeout=120)
        output = (f.stdout + f.stderr + p.stdout + p.stderr).strip()
        if p.returncode != 0:
            return {"ok": False, "message": "업데이트(pull) 실패:\n" + output}
        changed = _git("diff", "--name-only", before, "HEAD").stdout.splitlines()
        return {
            "ok": True,
            "output": output,
            "changed_files": len(changed),
            "deps_changed": "requirements.txt" in changed,
        }
    except FileNotFoundError:
        return {"ok": False, "message": "Git이 설치되어 있지 않습니다."}
    except subprocess.TimeoutExpired:
        return {"ok": False, "message": "업데이트 시간이 초과되었습니다. 네트워크를 확인하세요."}


def schedule_restart() -> dict:
    """update.bat restart 를 새 창에서 실행(의존성 확인 후 START.bat)하고 현재 서버를 끝낸다."""
    script = BASE_DIR / "update.bat"
    if not script.exists():
        return {"ok": False, "message": "update.bat 파일을 찾을 수 없습니다."}

    def run():
        time.sleep(1)
        subprocess.Popen(
            ["cmd.exe", "/c", "start", "cmd.exe", "/c", str(script), "restart"],
            cwd=BASE_DIR,
        )
        os._exit(0)

    threading.Thread(target=run, daemon=True).start()
    return {"ok": True, "message": "재시작을 시작했습니다."}
