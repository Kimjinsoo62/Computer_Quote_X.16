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
        self.page = self.ctx.pages[0] if self.ctx.pages else self.ctx.new_page()
        self.ctx.on("page", lambda p: self._watch(p))
        self._watch(self.page)
        _PAGE = self.page

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
        pause(0.8, 1.6)

    def human_scroll(self) -> None:
        for _ in range(random.randint(1, 3)):
            self.page.mouse.wheel(0, random.randint(250, 650))
            pause(0.2, 0.6)

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
            emit(job, "log", msg="이미 로그인되어 있습니다.")
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
            self.page.locator("#login-btn").first.click()
            for _ in range(30):
                wait(0.5)
                if self.dialogs or "login.htm" not in self.page.url:
                    break
            try:
                self.page.wait_for_load_state("domcontentloaded", timeout=20000)
            except Exception:
                pass
        finally:
            self.auto_dialogs = False
        if "login.htm" not in self.page.url and self.logged_in(job):
            emit(job, "log", msg="로그인했습니다.")
            return True
        msg = self.dialogs[-1] if self.dialogs else ""
        if msg and any(k in msg for k in ("일치", "확인", "잘못", "틀", "없는")):
            emit(job, "error", msg=f"로그인 실패: {msg} — ⚙️ 설정에서 컴퓨존 아이디 · 비밀번호를 확인해 주세요.")
            return False
        emit(job, "need_user", msg="로그인에 보안 확인(캡차 · 추가 인증 등)이 필요합니다. 열린 창에서 직접 로그인을 마쳐 주세요. "
                                   f"{USER_WAIT_SEC // 60}분 동안 기다립니다.")
        return self.wait_user_login(job)

    def wait_user_login(self, job: str) -> bool:
        t0 = time.time()
        while time.time() - t0 < USER_WAIT_SEC:
            wait(3)
            try:
                if "login.htm" not in self.page.url and self.logged_in():
                    emit(job, "log", msg="로그인을 확인했습니다. 이어서 담습니다.")
                    return True
            except Exception:
                pass
        emit(job, "error", msg="로그인을 확인하지 못해 담기를 멈췄습니다.")
        return False

    # -- 담기 -------------------------------------------------------------------
    def add_item(self, job: str, item: dict[str, Any]) -> tuple[bool, str]:
        pno, qty = int(item["pno"]), max(1, int(item.get("qty") or 1))
        self.goto(PRODUCT_URL.format(pno=pno))
        if "product_detail" not in self.page.url:
            return False, "판매 중인 상품이 아닙니다(상품 페이지가 열리지 않음)"
        self.human_scroll()
        if qty > 1:
            plus = self.page.locator(f"button.bt_increase[onclick*='last_{pno}']").first
            if plus.count():
                for _ in range(qty - 1):
                    plus.click()
                    pause(0.25, 0.6)
            else:
                return False, f"수량({qty}) 칸을 찾지 못했습니다"
        btn = None
        loc = self.page.locator("a.cart, button.cart, a[title*='장바구니 담기']")
        for i in range(loc.count()):
            el = loc.nth(i)
            try:
                if el.is_visible():
                    btn = el
                    break
            except Exception:
                continue
        if btn is None:
            return False, "장바구니 담기 버튼을 찾지 못했습니다"
        self.dialogs.clear()
        self.auto_dialogs = True
        sent: list[str] = []   # 담기 요청이 나갔는지(완제품은 폼 전송 → 장바구니로 이동, 단품은 백그라운드 요청)
        done: list[int] = []

        def is_cart(u: str) -> bool:
            return "order_function" in u or "/bsk/" in u or "basket" in u.lower()

        def on_req(r):
            if is_cart(r.url) and r.method == "POST":
                sent.append(r.url)

        def on_resp(r):
            if is_cart(r.url) and r.request.method == "POST":
                done.append(r.status)
        self.page.on("request", on_req)
        self.page.on("response", on_resp)
        try:
            btn.scroll_into_view_if_needed()
            pause(0.3, 0.8)
            btn.click()
            t0 = time.time()
            while time.time() - t0 < 15:      # 요청이 나가기를 기다린다
                wait(0.4)
                if sent or "basket_main" in self.page.url:
                    break
            t0 = time.time()
            while sent and not done and time.time() - t0 < 40:   # 요청 응답(느릴 수 있음)
                wait(0.5)
            try:
                self.page.wait_for_load_state("domcontentloaded", timeout=40000)
            except Exception:
                pass
        finally:
            self.auto_dialogs = False
            for ev, fn in (("request", on_req), ("response", on_resp)):
                try:
                    self.page.remove_listener(ev, fn)
                except Exception:
                    pass
        hits = done or ([200] if "basket_main" in self.page.url else [])
        # 비회원이면 컴퓨존이 '해당 상품은 로그인 후 구매하실 수 있습니다' 창(#limit_login_lypop)을 띄운다
        try:
            if self.page.locator("#limit_login_lypop").first.is_visible():
                return False, "로그인이 필요합니다"
        except Exception:
            pass
        if any(d for d in self.dialogs if "로그인" in d):
            return False, "로그인이 필요합니다"
        if not hits and "basket_main" not in self.page.url:
            note = self.dialogs[-1] if self.dialogs else "컴퓨존이 응답하지 않았습니다"
            return False, note[:120]
        pause(0.8, 1.6)
        return True, ""

    def basket_counts(self) -> dict[int, int]:
        """장바구니의 상품 번호 → 수량(수량 0 인 선택 서비스 줄은 뺀다). 담기 전후를 비교해 실제로 담겼는지 본다."""
        self.goto(BASKET_URL)
        try:
            rows = self.page.evaluate("""() => Array.from(document.querySelectorAll('input[name="SelectOrderCartNo[]"]')).map(cb => {
                const row = cb.closest('tr') || cb.closest('li') || cb.parentElement;
                const ea = row ? row.querySelector('input[name^="ea["]') : null;
                return [cb.dataset.pno, ea ? ea.value : '1'];
            })""")
        except Exception:
            return {}
        out: dict[int, int] = {}
        for pno, ea in rows:
            try:
                n = int(str(ea).replace(",", "") or 0)
            except ValueError:
                n = 0
            if pno and str(pno).isdigit() and n > 0:
                out[int(pno)] = out.get(int(pno), 0) + n
        return out

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
            before = self.basket_counts()
            emit(job, "log", msg=f"담기 전 장바구니: {len(before)}종")
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
                emit(job, "log", msg=("· 담기 버튼 누름: " if ok else "✕ ") + label[:70] + ("" if ok else f" — {why}"))
                pause(0.6, 1.5)
            after = self.basket_counts()
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
            emit(job, "done", added=len(added), failed=failed, qty_diff=qty_diff, already=already,
                 missing=[], in_cart=len(after))
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
            runner.ctx.close()
        if runner.pw:
            runner.pw.stop()
    except Exception:
        pass


if __name__ == "__main__":
    main()
