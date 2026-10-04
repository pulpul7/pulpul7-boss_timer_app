"""User-approved retries always run a new, fully verified operation."""
from github_request_policy import GITHUB_REQUEST_GATE


def offer_verified_retry(app, title, error, retry, *, attempt=0):
    parent = app.schedule_window or app.root
    settings = app._get_github_data_settings()
    remaining = GITHUB_REQUEST_GATE.remaining(settings, settings.get("token", ""), "PUT")
    if retry is None or attempt >= 2 or remaining:
        detail = str(error)
        if remaining:
            detail += f"\nGitHub 요청 제한 대기 중입니다. {remaining}초 후 상태를 확인하고 다시 요청하세요."
        app._show_centered_messagebox("showerror", title, detail, parent=parent)
        return False
    approved = app._show_centered_messagebox("askyesno", title,
        str(error) + "\n\n현재 연결·담당자·서버 자료를 다시 확인한 뒤 재시도할까요?",
        parent=parent)
    if approved:
        app.root.after(0, retry)
    return bool(approved)
