# -*- coding: utf-8 -*-
"""견적서 메일 발송: 앱 안 발송 창 → Gmail(SMTP)로 견적서 Excel 을 첨부해 보낸다.

흐름
1. 카드 서식 미리보기의 [✉️ 이메일] → /api/download/excel?…&email=1 이 Excel 을 만들어 초안(draft)으로 잠시 보관하고
   받는 사람(견적처 이메일) · 참조(공급자 이메일) · 제목 · 본문을 채워 돌려준다.
2. 화면의 발송 창에서 고친 뒤 [보내기] → /api/mail/send 가 초안의 Excel 을 첨부해 Gmail 로 보낸다.

보내는 계정(Gmail 주소 + 앱 비밀번호)은 app_settings.json 의 mail_smtp 에 둔다(저장소에 올라가지 않음).
비밀번호는 Windows DPAPI 로 암호화해 이 PC · 이 Windows 사용자만 풀 수 있다.
"""
from __future__ import annotations

import base64
import ctypes
import re
import smtplib
import ssl
import threading
import time
import uuid
from ctypes import wintypes
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from typing import Any

from app.app_settings import get_setting, set_setting

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465
SMTP_TIMEOUT_SEC = 30
MAIL_KEY = "mail_smtp"
DRAFT_TTL_SEC = 60 * 60  # 발송 창을 열어 두고 1시간 안에 보내지 않으면 첨부 초안을 버린다

_EMAIL_RE = re.compile(r"^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+$")
_drafts_lock = threading.Lock()
_drafts: dict[str, dict[str, Any]] = {}


# ---------------------------------------------------------------------------
# 앱 비밀번호 암호화 (Windows DPAPI)
# ---------------------------------------------------------------------------
class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(data: bytes) -> _Blob:
    buf = ctypes.create_string_buffer(data, len(data))
    return _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))


def _dpapi(data: bytes, encrypt: bool) -> bytes:
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    src, out = _blob(data), _Blob()
    fn = crypt32.CryptProtectData if encrypt else crypt32.CryptUnprotectData
    if not fn(ctypes.byref(src), None, None, None, None, 0, ctypes.byref(out)):
        raise OSError("비밀번호 암호화/복호화에 실패했습니다(다른 Windows 사용자로 저장했을 수 있습니다).")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(out.pbData)


# ---------------------------------------------------------------------------
# 보내는 계정
# ---------------------------------------------------------------------------
def get_account() -> dict[str, Any]:
    """화면용: 비밀번호는 돌려주지 않는다."""
    cfg = get_setting(MAIL_KEY, {}) or {}
    user = str(cfg.get("user") or "")
    return {"user": user, "configured": bool(user and cfg.get("password"))}


def _password() -> str:
    cfg = get_setting(MAIL_KEY, {}) or {}
    enc = cfg.get("password") or ""
    if not enc:
        return ""
    return _dpapi(base64.b64decode(enc), encrypt=False).decode("utf-8")


def _login(user: str, password: str) -> smtplib.SMTP_SSL:
    smtp = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=SMTP_TIMEOUT_SEC, context=ssl.create_default_context())
    try:
        smtp.login(user, password)
    except (smtplib.SMTPAuthenticationError, smtplib.SMTPServerDisconnected) as e:
        # 잘못된 계정이면 Gmail 이 535 대신 연결을 끊어 버리기도 한다
        smtp.close()
        raise ValueError(
            "Gmail 로그인에 실패했습니다. 일반 비밀번호가 아니라 Google 계정의 '앱 비밀번호'(16자리)를 넣어 주세요."
        ) from e
    return smtp


def set_account(user: str, password: str) -> dict[str, Any]:
    """Gmail 에 실제로 로그인해 확인한 뒤 저장한다. 비밀번호가 비어 있으면 주소만 바꾼다(기존 비밀번호 유지)."""
    user = (user or "").strip()
    password = re.sub(r"\s+", "", password or "")  # 앱 비밀번호는 4자리씩 띄어 보여 준다
    if not _EMAIL_RE.match(user):
        raise ValueError("보내는 Gmail 주소를 확인해 주세요.")
    if not password:
        password = _password()
        if not password:
            raise ValueError("앱 비밀번호를 입력해 주세요.")
    _login(user, password).quit()
    set_setting(MAIL_KEY, {"user": user, "password": base64.b64encode(_dpapi(password.encode("utf-8"), True)).decode()})
    return get_account()


# ---------------------------------------------------------------------------
# 초안 (첨부 Excel 을 발송할 때까지 잠시 보관)
# ---------------------------------------------------------------------------
def compose_fields(info: dict[str, Any], model: str, total: int, quote_date: str) -> dict[str, str]:
    """받는 사람 = 견적처 이메일, 참조 = 공급자(나) 이메일. 제목 · 본문은 견적정보로 채운다."""
    customer = (info.get("customer_name") or "").strip()
    manager = (info.get("customer_manager") or "").strip()
    supplier = (info.get("supplier_name") or "").strip()
    to_addr = (info.get("customer_email") or "").strip()
    cc_addr = (info.get("supplier_email") or "").strip()
    if cc_addr.lower() == to_addr.lower():
        cc_addr = ""
    greet = " ".join(x for x in (customer, manager) if x) or "고객"
    signature = "\n".join(
        x
        for x in (
            " ".join(y for y in (supplier, (info.get("supplier_manager") or "").strip()) if y),
            " / ".join(y for y in ((info.get("supplier_phone") or "").strip(), (info.get("supplier_email") or "").strip()) if y),
        )
        if x
    )
    body = (
        f"{greet}님 안녕하세요.\n\n"
        "요청하신 견적서를 첨부해 드립니다.\n\n"
        f"- 모델: {model}\n"
        f"- 견적 금액: {total:,}원 (VAT 포함)\n"
        f"- 견적일: {quote_date}\n\n"
        "검토하신 뒤 궁금한 점이 있으시면 편하게 연락 주세요.\n감사합니다.\n"
    )
    if signature:
        body += f"\n{signature}\n"
    return {
        "to": to_addr,
        "cc": cc_addr,
        "subject": f"[견적서] {model} 견적 안내" + (f" - {supplier}" if supplier else ""),
        "body": body,
    }


def add_draft(xlsx_bytes: bytes, filename: str) -> str:
    now = time.time()
    draft_id = uuid.uuid4().hex
    with _drafts_lock:
        for key in [k for k, d in _drafts.items() if now - d["at"] > DRAFT_TTL_SEC]:
            _drafts.pop(key, None)
        _drafts[draft_id] = {"at": now, "data": xlsx_bytes, "filename": filename}
    return draft_id


def _split_addrs(text: str) -> list[str]:
    addrs = [a.strip() for a in re.split(r"[,;\s]+", text or "") if a.strip()]
    bad = [a for a in addrs if not _EMAIL_RE.match(a)]
    if bad:
        raise ValueError(f"이메일 주소를 확인해 주세요: {', '.join(bad)}")
    return addrs


def send_draft(draft_id: str, to: str, cc: str, subject: str, body: str) -> dict[str, Any]:
    with _drafts_lock:
        draft = _drafts.get(draft_id)
    if not draft:
        raise LookupError("첨부할 견적서가 만료되었습니다. 📥 엑셀 견적서에서 ✉️ 이메일을 다시 눌러 주세요.")
    account = get_account()
    if not account["configured"]:
        raise PermissionError("보내는 Gmail 계정이 설정되어 있지 않습니다.")
    to_list, cc_list = _split_addrs(to), _split_addrs(cc)
    if not to_list:
        raise ValueError("받는 사람 이메일을 입력해 주세요.")

    msg = EmailMessage()
    msg["From"] = account["user"]
    msg["To"] = ", ".join(to_list)
    if cc_list:
        msg["Cc"] = ", ".join(cc_list)
    msg["Subject"] = (subject or "").strip() or "견적서"
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid()
    msg.set_content(body or "")
    msg.add_attachment(
        draft["data"],
        maintype="application",
        subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=draft["filename"],
    )
    smtp = _login(account["user"], _password())
    try:
        smtp.send_message(msg)
    finally:
        smtp.quit()
    with _drafts_lock:
        _drafts.pop(draft_id, None)
    return {"ok": True, "from": account["user"], "to": to_list, "cc": cc_list}
