# -*- coding: utf-8 -*-
"""🛒 컴퓨존 장바구니 담기 작업 프로그램 (서버가 `python -m app.compuzone_cart_runner` 로 따로 띄운다).

Playwright 로 이 PC 의 Edge 를 **보이는 창**으로 띄워(앱 전용 프로필 data/AI-PC-Quote-Compuzone) 사람이 쓰듯
컴퓨존에 로그인하고, 상품 페이지마다 컴퓨존 자체 [장바구니 담기] 버튼을 눌러 담은 뒤 장바구니 화면을 열어 둔다.

- 작업은 stdin 으로 한 줄에 JSON 하나씩 받는다: {"job": id, "user": 아이디, "password": 비밀번호, "items": [{pno, qty, name}]}
  (비밀번호는 명령줄 인자로 넘기지 않는다 — 다른 프로그램이 명령줄을 볼 수 있으므로)
- 진행은 stdout 으로 한 줄에 JSON 하나씩 낸다: {"job", "type": "log"|"progress"|"need_user"|"done"|"error", ...}
- 입력 · 클릭 사이에 사람처럼 무작위 간격을 둔다(컴퓨존 서버에 무리를 주지 않게). 캡차 · 추가 인증을 우회하지 않는다:
  로그인 중 보안 확인이 나오면 멈추고 창에서 직접 마치도록 알린 뒤 로그인이 될 때까지 기다린다.
- 작업이 끝나도 창은 열어 둔다(장바구니 확인 · 주문). 창을 닫으면 다음 작업 때 새로 띄운다.
- 컴퓨존 화면 구조(2026-11 확인): 로그인 #member_id · #member_password · #login-btn, 완제품 조립PC 는
  a.cart[href*=delivery](비회원도 가능, 담으면 장바구니로 이동), 단품은 a.cart → buy_direct(…'basket_insert_detail2'…)
  (회원만, 수량은 button.bt_increase[onclick*='last_<pno>']), 장바구니 행 input[name="SelectOrderCartNo[]"][data-pno].
"""
from __future__ import annotations

import json
import os
import random
import sys
import time
from typing import Any

BASE = "https://www.compuzone.co.kr"
LOGIN_URL = BASE + "/login/login.htm"
MEMBER_PROBE_URL = BASE + "/mypage/wish_list.htm"  # 회원 전용: 비회원이면 login.htm 으로 보낸다(2026-11 확인)
BASKET_URL = BASE + "/bsk/basket_main.htm"
PRODUCT_URL = BASE + "/product/product_detail.htm?ProductNo={pno}"
USER_WAIT_SEC = 300  # 보안 확인 · 직접 로그인을 기다리는 시간
# 로그인 상태 유지: 컴퓨존은 로그인 · 장바구니를 '세션 쿠키'(CSID, OrderCartCode 등)로 잡는데, 이 쿠키는 창을 닫으면 지워진다
# (2026-11 확인: 창을 닫았다 다시 열면 장바구니가 비었다). 그래서 컴퓨존 쿠키를 Windows DPAPI 로 암호화해 프로필 폴더에
# 두고, 창을 새로 열 때 되살린다. 컴퓨존 서버 쪽 세션이 아직 살아 있으면 로그인을 건너뛴다(끝났으면 평소처럼 로그인).
SESSION_FILE_NAME = "cz_session.bin"



def emit(job: str, typ: str, **kw: Any) -> None:
    sys.stdout.write(json.dumps({"job": job, "type": typ, **kw}, ensure_ascii=False) + "\n")
    sys.stdout.flush()


_PAGE = None  # 지금 쓰는 페이지(기다리는 동안 Playwright 가 응답 · 대화상자 이벤트를 처리하도록 page 로 기다린다)


def pause(a: float = 0.4, b: float = 1.1) -> None:
    ms = int(random.uniform(a, b) * 1000)
    if _PAGE is not None:
        try:
            _PAGE.wait_for_timeout(ms)
            return
        except Exception:
            pass
    time.sleep(ms / 1000)


def wait(sec: float) -> None:
    pause(sec, sec)


class Runner:
    def __init__(self, profile_dir: str):
        self.profile_dir = profile_dir
        self.pw = None
        self.ctx = None
        self.page = None
        self.dialogs: list[str] = []
        self.auto_dialogs = False  # 자동으로 입력 · 담는 동안에만 대화상자를 '확인'한다(사람이 창을 쓸 때는 그대로 둠)

    # -- 브라우저 ---------------------------------------------------------------
    def ensure_browser(self) -> None:
        global _PAGE
        if self.ctx is not None:
            try:
                if self.ctx.pages:
                    self.page = self.ctx.pages[-1]
                    self.page.bring_to_front()
                    _PAGE = self.page  # 새 탭 · 닫힌 탭 뒤에도 기다리는 동안 이벤트가 처리되게
                    return
            except Exception:
                pass
            self.ctx = None
        from playwright.sync_api import sync_playwright

        if self.pw is None:
            self.pw = sync_playwright().start()
        self.ctx = self.pw.chromium.launch_persistent_context(
            # CZ_HEADLESS=1 은 시험 전용(화면 없이). 앱에서는 늘 보이는 창으로 띄운다
            self.profile_dir, channel="msedge", headless=os.environ.get("CZ_HEADLESS") == "1", locale="ko-KR",
            no_viewport=True, args=["--start-maximized"],
        )
        self.restored = self.restore_session()
        self.page = self.ctx.pages[0] if self.ctx.pages else self.ctx.new_page()
        self.ctx.on("page", lambda p: self._watch(p))
        self._watch(self.page)
        _PAGE = self.page

    def _session_path(self):
        from pathlib import Path

        return Path(self.profile_dir) / SESSION_FILE_NAME

    def save_session(self) -> None:
        """컴퓨존 쿠키(세션 쿠키 포함)를 암호화해 저장한다. 실패해도 작업은 계속한다."""
        try:
            from app.mail_send import _dpapi

            cookies = [c for c in self.ctx.cookies() if "compuzone" in c.get("domain", "")]
            if not cookies:
                return
            data = json.dumps(cookies, ensure_ascii=False).encode("utf-8")
            self._session_path().write_bytes(_dpapi(data, True))
        except Exception:
            pass

    def restore_session(self) -> int:
        """저장한 컴퓨존 쿠키를 새 창에 넣는다. 넣은 쿠키 수(없거나 못 풀면 0)."""
        try:
            from app.mail_send import _dpapi

            p = self._session_path()
            if not p.is_file():
                return 0
            cookies = json.loads(_dpapi(p.read_bytes(), False).decode("utf-8"))
            now = time.time()
            keep = []
            for c in cookies:
                exp = c.get("expires", -1)
                if exp not in (-1, None) and exp < now:
                    continue  # 이미 만료된 쿠키는 버린다
                keep.append({k: c[k] for k in ("name", "value", "domain", "path", "expires", "httpOnly", "secure", "sameSite") if k in c})
            if keep:
                self.ctx.add_cookies(keep)
            return len(keep)
        except Exception:
            return 0

    def _watch(self, page) -> None:
        def on_dialog(d):
            self.dialogs.append(d.message)
            if not self.auto_dialogs:
                return  # 사람이 창을 쓰는 동안(직접 로그인 · 장바구니에서 삭제 · 주문)은 사람이 누르게 둔다
            try:
                d.accept()
            except Exception:
                pass
        page.on("dialog", on_dialog)

    def goto(self, url: str) -> None:
        self.page.goto(url, wait_until="domcontentloaded", timeout=45000)
        pause(0.25, 0.7)

    def human_scroll(self) -> None:
        # 한두 번 짧게 굴린다(사람처럼 보이되 느리지 않게)
        for _ in range(random.randint(1, 2)):
            self.page.mouse.wheel(0, random.randint(200, 550))
            pause(0.08, 0.25)

    def type_like_human(self, selector: str, text: str) -> None:
        box = self.page.locator(selector).first
        box.click()
        pause(0.2, 0.5)
        box.fill("")
        for ch in text:
            self.page.keyboard.type(ch, delay=random.randint(60, 170))
            if random.random() < 0.08:
                pause(0.2, 0.6)

    # -- 로그인 -----------------------------------------------------------------
    def logged_in(self, job: str = "") -> bool:
        """회원 전용 페이지를 따로 연 탭에서 열어 본다(비회원이면 login.htm 으로 간다). 사람이 쓰는 탭은 건드리지 않는다."""
        probe = None
        try:
            probe = self.ctx.new_page()
            probe.on("dialog", lambda d: d.accept())  # 비회원이면 '로그인 후 이용 가능합니다' 알림
            probe.goto(MEMBER_PROBE_URL, wait_until="domcontentloaded", timeout=30000)
            probe.wait_for_timeout(800)
            ok = "login.htm" not in probe.url
            if job:
                emit(job, "log", msg=f"로그인 확인: {'로그인됨' if ok else '로그인 안 됨'} ({probe.url[:70]})")
            return ok
        except Exception as e:
            if job:
                emit(job, "log", msg=f"로그인 확인 실패: {e}")
            return False
        finally:
            if probe is not None:
                try:
                    probe.close()
                except Exception:
                    pass
            try:
                self.page.bring_to_front()
            except Exception:
                pass

    def login(self, job: str, user: str, password: str) -> bool:
        self.goto(BASE + "/main/main.htm")
        if self.logged_in(job):
            emit(job, "log", msg="이미 로그인되어 있습니다" + (" (저장해 둔 로그인 상태를 이어서 씁니다)." if getattr(self, "restored", 0) else "."))
            self.save_session()
            return True
        if not user or not password:
            emit(job, "need_user", msg="저장된 컴퓨존 계정이 없습니다. 열린 창에서 직접 로그인해 주세요.")
            return self.wait_user_login(job)
        emit(job, "log", msg="컴퓨존 로그인 화면을 엽니다.")
        self.goto(LOGIN_URL)
        self.dialogs.clear()
        self.auto_dialogs = True
        try:
            self.type_like_human("#member_id", user)
            pause(0.3, 0.8)
            self.type_like_human("#member_password", password)
            pause(0.5, 1.2)
            # 입력한 값이 저장된 값과 똑같은지 되읽어 확인한다(기록에는 길이만 남긴다). 다르면 그대로 다시 넣는다
            got_id = self.page.locator("#member_id").first.input_value()
            got_pw = self.page.locator("#member_password").first.input_value()
            if got_id != user or got_pw != password:
                emit(job, "log", msg=f"입력 확인: 칸 값이 달라 다시 넣습니다(아이디 {len(got_id)}/{len(user)}자 · 비밀번호 {len(got_pw)}/{len(password)}자)")
                self.page.locator("#member_id").first.fill(user)
                self.page.locator("#member_password").first.fill(password)
                pause(0.3, 0.7)
            else:
                emit(job, "log", msg=f"입력 확인: 아이디 {len(user)}자 · 비밀번호 {len(password)}자 — 저장된 값과 같음")
            self.page.locator("#login-btn").first.click()
            # 제출하면 login_function.php 로 갔다가, 틀렸으면 그 페이지에서 알림을 띄우고 로그인 화면으로 돌아온다.
            # 알림이 늦게 뜰 수 있으므로 '알림이 떴다' 또는 '로그인 · 처리 페이지가 아닌 곳에 도착했다' 중 하나가 될 때까지 기다린다
            for _ in range(40):
                wait(0.5)
                url = self.page.url
                if self.dialogs or ("login.htm" not in url and "login_function" not in url):
                    break
            try:
                self.page.wait_for_load_state("domcontentloaded", timeout=20000)
            except Exception:
                pass
            wait(1.0)  # 늦게 뜨는 알림까지 받는다
        finally:
            self.auto_dialogs = False
        if "login.htm" not in self.page.url and self.logged_in(job):
            emit(job, "log", msg="로그인했습니다. 로그인 상태를 저장해 다음에 창을 다시 열어도 이어서 씁니다.")
            self.save_session()
            return True
        msg = self.dialogs[-1] if self.dialogs else ""
        if msg and any(k in msg for k in ("일치", "확인", "잘못", "틀", "없는")):
            # 컴퓨존이 아이디 · 비밀번호를 거절했다. 다시 자동으로 넣지 않는다(5회 넘게 틀리면 컴퓨존이 보안 인증을 요구한다).
            # 대신 열린 창에서 직접 로그인하면 이어서 담는다
            emit(job, "need_user", msg=f"컴퓨존이 로그인을 거절했습니다: '{msg.strip()[:60]}'. 저장된 아이디 · 비밀번호가 컴퓨존과 다릅니다"
                                       "(카카오 · 네이버로 가입한 계정은 컴퓨존 비밀번호가 따로 없습니다). 열린 창에서 직접 로그인하시면 이어서 담습니다 — "
                                       f"{USER_WAIT_SEC // 60}분 동안 기다립니다. ⚙️ 설정의 컴퓨존 계정도 고쳐 주세요.")
            return self.wait_user_login(job)
        emit(job, "need_user", msg="로그인에 보안 확인(캡차 · 추가 인증 등)이 필요합니다. 열린 창에서 직접 로그인을 마쳐 주세요. "
                                   f"{USER_WAIT_SEC // 60}분 동안 기다립니다.")
        return self.wait_user_login(job)

    def wait_user_login(self, job: str) -> bool:
        t0 = time.time()
        while time.time() - t0 < USER_WAIT_SEC:
            wait(3)
            try:
                if "login.htm" not in self.page.url and self.logged_in():
                    emit(job, "log", msg="로그인을 확인했습니다. 로그인 상태를 저장하고 이어서 담습니다.")
                    self.save_session()
                    return True
            except Exception:
                pass
        emit(job, "error", msg="로그인을 확인하지 못해 담기를 멈췄습니다.")
        return False

    # -- 담기 -------------------------------------------------------------------
    def _cart_button(self):
        loc = self.page.locator("a.cart, button.cart, a[title*='장바구니 담기']")
        for i in range(loc.count()):
            el = loc.nth(i)
            try:
                if el.is_visible():
                    return el
            except Exception:
                continue
        return None

    def _click_add(self, btn) -> tuple[bool, str]:
        """담기 버튼을 한 번 눌러 담기 요청이 나가고 응답이 올 때까지 기다린다.

        컴퓨존 담기 요청(2026-11 확인): 완제품은 order_function.php 로 폼 POST(장바구니 화면으로 이동),
        단품은 숨은 iframe(#common_iframe)으로 GET ../order/order_function.php?actype=basket_insert_detail2&… .
        그래서 방식(GET/POST)과 상관없이 order_function · 장바구니 주소로 가는 요청을 본다(iframe 요청도 잡힘).
        """
        sent: list[str] = []
        done: list[int] = []

        def is_cart(u: str) -> bool:
            return "order_function" in u or "/bsk/" in u or "basket_insert" in u

        def on_req(r):
            if is_cart(r.url):
                sent.append(r.url)

        def on_resp(r):
            if is_cart(r.url):
                done.append(r.status)

        self.dialogs.clear()
        self.auto_dialogs = True
        self.page.on("request", on_req)
        self.page.on("response", on_resp)
        start_url = self.page.url
        try:
            btn.scroll_into_view_if_needed()
            pause(0.1, 0.3)
            btn.click()
            t0 = time.time()
            login_box = self.page.locator("#limit_login_lypop").first
            while time.time() - t0 < 6 and not sent and self.page.url == start_url:   # 요청은 보통 바로 나간다
                wait(0.15)
                if self.dialogs:
                    break
                try:
                    if login_box.is_visible():   # 비회원: '로그인 후 구매' 창이 바로 뜬다 — 더 기다릴 필요 없음
                        break
                except Exception:
                    pass
            t0 = time.time()
            while sent and not done and time.time() - t0 < 15:                         # 응답(보통 1초 안)
                wait(0.15)
            if self.page.url != start_url:   # 완제품은 장바구니 화면으로 넘어간다
                try:
                    self.page.wait_for_load_state("domcontentloaded", timeout=30000)
                except Exception:
                    pass
            wait(0.3)  # 담김 뒤 뜨는 알림까지 받는다
        finally:
            self.auto_dialogs = False
            for ev, fn in (("request", on_req), ("response", on_resp)):
                try:
                    self.page.remove_listener(ev, fn)
                except Exception:
                    pass
        try:
            if self.page.locator("#limit_login_lypop").first.is_visible():
                return False, "로그인이 필요합니다"
        except Exception:
            pass
        if any("로그인" in d for d in self.dialogs):
            return False, "로그인이 필요합니다"
        if done or "basket_main" in self.page.url:
            return True, ""
        if sent:
            return True, "응답 확인 전"
        return False, (self.dialogs[-1][:120] if self.dialogs else "담기 요청이 나가지 않았습니다(버튼이 반응하지 않음)")

    def add_item(self, job: str, item: dict[str, Any]) -> tuple[bool, str]:
        pno, qty = int(item["pno"]), max(1, int(item.get("qty") or 1))
        self.goto(PRODUCT_URL.format(pno=pno))
        if "product_detail" not in self.page.url:
            return False, "판매 중인 상품이 아닙니다(상품 페이지가 열리지 않음)"
        self.human_scroll()
        # 수량: 컴퓨존 담기 버튼은 수량 1 을 고정으로 보내므로(+ 버튼이 담기 수량에 안 들어감), 같은 상품을 qty 번 담는다
        # (같은 상품을 다시 담으면 컴퓨존이 장바구니 수량을 1 씩 늘린다 — 확인함)
        for n in range(qty):
            if n:
                if "product_detail" not in self.page.url:   # 장바구니로 넘어갔으면 상품 페이지로 돌아온다
                    self.goto(PRODUCT_URL.format(pno=pno))
                pause(0.2, 0.5)
            btn = self._cart_button()
            if btn is None:
                return False, "장바구니 담기 버튼을 찾지 못했습니다"
            ok, why = self._click_add(btn)
            if not ok:
                return False, why
        return True, ""

    def basket_state(self) -> dict[str, Any]:
        """장바구니: 상품 번호 → 수량 · 줄 금액(판매가 × 수량), 전체 합계. 수량 0 인 선택 서비스 줄은 뺀다.
        (2026-11 확인: 행 input[name="SelectOrderCartNo[]"][data-pno], 수량 input[name="ea[<장바구니번호>]"],
         줄 금액 #list_price_<장바구니번호>, 합계 .CardTotalPrice)"""
        self.goto(BASKET_URL)
        try:
            data = self.page.evaluate("""() => {
                const num = (t) => parseInt(String(t || '').replace(/[^0-9]/g, ''), 10) || 0;
                const rows = Array.from(document.querySelectorAll('input[name="SelectOrderCartNo[]"]')).map(cb => {
                    const row = cb.closest('tr') || cb.closest('li') || cb.parentElement;
                    const ea = row ? row.querySelector('input[name^="ea["]') : null;
                    const amt = cb.value ? document.getElementById('list_price_' + cb.value) : null;
                    return [cb.dataset.pno, ea ? ea.value : '1', amt ? num(amt.innerText) : 0];
                });
                const tot = document.querySelector('.CardTotalPrice');
                return {rows, total: tot ? num(tot.innerText) : 0};
            }""")
        except Exception:
            return {"counts": {}, "amounts": {}, "total": 0}
        counts: dict[int, int] = {}
        amounts: dict[int, int] = {}
        for pno, ea, amt in data.get("rows") or []:
            try:
                n = int(str(ea).replace(",", "") or 0)
            except ValueError:
                n = 0
            if pno and str(pno).isdigit() and n > 0:
                counts[int(pno)] = counts.get(int(pno), 0) + n
                amounts[int(pno)] = amounts.get(int(pno), 0) + int(amt or 0)
        return {"counts": counts, "amounts": amounts, "total": int(data.get("total") or 0)}

    def basket_counts(self) -> dict[int, int]:
        return self.basket_state()["counts"]

    def run(self, task: dict[str, Any]) -> None:
        job = str(task.get("job") or "")
        items = [i for i in task.get("items") or [] if str(i.get("pno") or "").isdigit()]
        total = len(items)
        try:
            self.ensure_browser()
            emit(job, "progress", step=0, total=total + 2, msg="컴퓨존 창을 열었습니다.")
            if task.get("guest_test") and os.environ.get("CZ_HEADLESS") == "1":
                emit(job, "log", msg="(시험) 로그인 없이 비회원으로 진행")
            elif not self.login(job, str(task.get("user") or ""), str(task.get("password") or "")):
                return
            emit(job, "progress", step=1, total=total + 2, msg="로그인 확인")
            before_st = self.basket_state()
            before = before_st["counts"]
            emit(job, "log", msg=f"담기 전 장바구니: {len(before)}종 · 합계 {before_st['total']:,}원")
            clicked: dict[int, str] = {}
            for k, it in enumerate(items, 1):
                label = f"{it.get('category') or ''} {it.get('name') or it['pno']}".strip()
                emit(job, "progress", step=1 + k, total=total + 2, msg=f"담는 중 ({k}/{total}) {label[:60]}")
                ok, why = self.add_item(job, it)
                if not ok and why == "로그인이 필요합니다":
                    # 세션이 끊겼으면 한 번 다시 로그인하고 다시 시도. 그래도 안 되면 남은 상품도 안 되므로 멈춘다
                    if not self.login(job, str(task.get("user") or ""), str(task.get("password") or "")):
                        return  # login() 이 이미 오류를 알렸다
                    ok, why = self.add_item(job, it)
                clicked[int(it["pno"])] = "" if ok else why
                emit(job, "log", msg=("· 담기 요청 보냄: " if ok else "✕ ") + label[:70] + ("" if ok else f" — {why}"))
                pause(0.15, 0.5)
            after_st = self.basket_state()
            after = after_st["counts"]
            # 판정은 장바구니 전후 수량 차이로 한다(버튼 응답만 보고 '담겼다'고 하지 않는다)
            added, failed, qty_diff, already = [], [], [], []
            for it in items:
                pno, want = int(it["pno"]), max(1, int(it.get("qty") or 1))
                label = f"{it.get('category') or ''} {it.get('name') or pno}".strip()
                delta = after.get(pno, 0) - before.get(pno, 0)
                if delta >= want:
                    added.append(pno)
                elif delta > 0:
                    qty_diff.append({"pno": pno, "name": label, "want": want, "got": delta})
                elif pno in after and pno in before:
                    already.append({"pno": pno, "name": label, "qty": after[pno]})
                else:
                    failed.append({"pno": pno, "name": label, "why": clicked.get(pno) or "장바구니에서 찾지 못했습니다"})
            for x in qty_diff:
                emit(job, "log", msg=f"⚠️ {x['name'][:60]} — 수량 {x['want']}개 중 {x['got']}개만 늘었습니다")
            for x in already:
                emit(job, "log", msg=f"ℹ️ {x['name'][:60]} — 이미 장바구니에 있었고 수량이 늘지 않았습니다(현재 {x['qty']}개)")
            emit(job, "progress", step=total + 2, total=total + 2, msg="장바구니를 열었습니다.")
            # 금액: 이번에 늘어난 줄 금액(컴퓨존 판매가) 합계와 장바구니 전체 합계(원래 있던 상품 포함)
            added_amount = 0
            for it in items:
                pno = int(it["pno"])
                added_amount += max(0, after_st["amounts"].get(pno, 0) - before_st["amounts"].get(pno, 0))
            emit(job, "log", msg=f"담은 상품 컴퓨존 합계 {added_amount:,}원 · 장바구니 전체 합계 {after_st['total']:,}원")
            self.save_session()
            emit(job, "done", added=len(added), failed=failed, qty_diff=qty_diff, already=already,
                 missing=[], in_cart=len(after), added_amount=added_amount, basket_total=after_st["total"],
                 before_total=before_st["total"], before_kinds=len(before))
            try:
                self.page.bring_to_front()
            except Exception:
                pass
        except Exception as e:  # noqa: BLE001 - 작업 하나가 실패해도 다음 작업을 받을 수 있게
            emit(job, "error", msg=f"장바구니 담기 중 오류: {e}")


def main() -> None:
    profile = sys.argv[1]
    runner = Runner(profile)
    emit("", "ready")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            task = json.loads(line)
        except ValueError:
            continue
        if task.get("cmd") == "quit":
            break
        runner.run(task)
    try:
        if runner.ctx:
            runner.save_session()  # 앱이 창을 닫을 때도 최신 로그인 상태를 남긴다
            runner.ctx.close()
        if runner.pw:
            runner.pw.stop()
    except Exception:
        pass


if __name__ == "__main__":
    main()
