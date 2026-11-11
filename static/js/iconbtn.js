/* 아이콘 메뉴 — 이모지로 시작하는 메뉴 버튼의 한글 글자를 숨기고 이모지만 보여 준다.
 * 마우스를 올리면 한글 제목(+ 원래 title 설명)을 작은 말풍선으로 바로 보여 준다.
 *
 * - 대상: button, a.btn, .store-tab 중 글자가 이모지로 시작하는 것 (화면 전체, 나중에 생긴 버튼 포함)
 * - 한글 글자는 지우지 않고 <span class="ib-label">로 감싸 CSS 로 숨긴다. 그래서 app.js 가
 *   라벨 span(#monitorToggleLabel 등)이나 버튼 textContent 를 바꿔도 그대로 동작하고,
 *   말풍선은 마우스를 올린 그 순간의 글자를 읽는다.
 * - 데이터 표시(마진 · 건수 배지 · 포함/제외 토글 상태)는 IB_KEEP 으로 계속 보이게 둔다.
 *   📇 거래처명 · 🌐 IP 주소는 숨기고 말풍선에 보여 준다.
 * - 제외: 게임/해상도 카드 · 서식 카드 · 견적 탭 · 폼 탭 등 내용 카드(IB_SKIP), 또는 data-ib-skip.
 * - data-tip="제목" 을 주면 그 글자를 말풍선 제목으로 쓰고, 숨긴 글자가 있으면 뒤에 붙인다(예: "네트워크 접속 주소 192.168.0.13:8090").
 */
(function () {
  "use strict";

  const IB_CANDIDATE = "button, a.btn, .store-tab";
  const IB_SKIP = ".game-card, .resolution-card, .mq-form-card, .tab-btn, .fg-tab, .game-step-tab, .game-chip-x, [data-ib-skip]";
  const IB_KEEP = ".ib-keep, #marginCycleLabel, .archive-count, [data-hub-count]";
  // 맨 앞 이모지(변형 선택자 · ZWJ 결합 포함) + 메뉴에 쓰는 기호
  const LEAD_RE = /^\s*((?:\p{Extended_Pictographic}|[✕×↺⬅＋←])(?:️|‍(?:\p{Extended_Pictographic}))*)/u;

  function leadingIcon(text) {
    const m = LEAD_RE.exec(text || "");
    return m ? m[1] : "";
  }

  function wrapLabel(text) {
    const s = document.createElement("span");
    s.className = "ib-label";
    s.textContent = text;
    return s;
  }

  /** 버튼 한 개를 아이콘 버튼으로 바꾼다. 여러 번 불러도 결과가 같다. */
  function iconify(btn) {
    if (!btn || btn.nodeType !== 1 || btn.matches(IB_SKIP)) return;
    const text = (btn.textContent || "").trim();
    if (!leadingIcon(text)) {
      // "저장 중…" 처럼 잠깐 이모지 없는 글자가 들어오면 그대로 보여 준다
      btn.classList.remove("ib");
      return;
    }
    let iconDone = false;
    for (const node of Array.from(btn.childNodes)) {
      if (node.nodeType === 3) {
        const t = node.nodeValue;
        if (!t.trim()) continue;
        if (!iconDone) {
          iconDone = true;
          const icon = leadingIcon(t);
          const rest = t.slice(t.indexOf(icon) + icon.length);
          if (rest.trim()) {
            node.nodeValue = icon;
            node.after(wrapLabel(rest.trim()));
          } else {
            node.nodeValue = icon;
          }
        } else {
          node.replaceWith(wrapLabel(t.trim()));
        }
      } else if (node.nodeType === 1) {
        if (node.matches(IB_KEEP) || node.querySelector(IB_KEEP)) { iconDone = true; continue; }
        if (!iconDone && leadingIcon(node.textContent) && !node.classList.contains("ib-label")) {
          iconDone = true; // <span class="btn-icon">📋</span> 같은 아이콘 span
          continue;
        }
        if (node.textContent.trim()) node.classList.add("ib-label");
        iconDone = true;
      }
    }
    btn.classList.add("ib");
    // 숨긴 글자는 화면낭독기도 못 읽으므로 aria-label 로 넘긴다 (원래 있던 aria-label 은 그대로, 라벨이 바뀌면 갱신)
    if (!btn.getAttribute("aria-label") || btn.dataset.ibAria === "1") {
      const tip = tipTitle(btn);
      if (tip) { btn.setAttribute("aria-label", tip); btn.dataset.ibAria = "1"; }
    }
  }

  function tipTitle(btn) {
    const hidden = Array.from(btn.querySelectorAll(".ib-label"))
      .map((el) => el.textContent.trim())
      .filter(Boolean)
      .join(" ")
      .replace(/\s+/g, " ")
      .trim();
    if (btn.dataset.tip) return hidden ? `${btn.dataset.tip} ${hidden}` : btn.dataset.tip;
    return hidden;
  }

  function scan(root) {
    if (!root || root.nodeType !== 1) return;
    if (root.matches(IB_CANDIDATE)) iconify(root);
    root.querySelectorAll(IB_CANDIDATE).forEach(iconify);
  }

  // ── 말풍선 ──────────────────────────────────────────
  let tipEl = null;
  let tipFor = null;

  function ensureTip() {
    if (tipEl) return tipEl;
    tipEl = document.createElement("div");
    tipEl.id = "ibTip";
    tipEl.setAttribute("role", "tooltip");
    document.body.appendChild(tipEl);
    return tipEl;
  }

  function showTip(btn) {
    const title = tipTitle(btn);
    // 원래 title(긴 설명)은 브라우저 기본 툴팁과 겹치지 않게 잠시 옮겨 둔다
    if (btn.title) { btn.dataset.ibDesc = btn.title; btn.title = ""; }
    const desc = btn.dataset.ibDesc || "";
    if (!title && !desc) return;
    const el = ensureTip();
    el.innerHTML = "";
    if (title) {
      const b = document.createElement("strong");
      b.textContent = title;
      el.appendChild(b);
    }
    if (desc && desc !== title) {
      const d = document.createElement("span");
      d.textContent = desc;
      el.appendChild(d);
    }
    el.style.left = "0px";
    el.style.top = "0px";
    el.classList.add("on");
    const r = btn.getBoundingClientRect();
    const tr = el.getBoundingClientRect();
    let left = r.left + r.width / 2 - tr.width / 2;
    left = Math.max(4, Math.min(left, window.innerWidth - tr.width - 4));
    let top = r.bottom + 6;
    if (top + tr.height > window.innerHeight - 4) top = r.top - tr.height - 6;
    el.style.left = `${Math.round(left)}px`;
    el.style.top = `${Math.round(Math.max(4, top))}px`;
    tipFor = btn;
  }

  function hideTip() {
    if (tipFor && !tipFor.title && tipFor.dataset.ibDesc) tipFor.title = tipFor.dataset.ibDesc;
    tipFor = null;
    if (tipEl) tipEl.classList.remove("on");
  }

  document.addEventListener("mouseover", (e) => {
    const btn = e.target.closest && e.target.closest(".ib");
    if (btn === tipFor) return;
    hideTip();
    if (btn) showTip(btn);
  });
  document.addEventListener("mouseout", (e) => {
    if (!tipFor) return;
    if (e.relatedTarget && tipFor.contains(e.relatedTarget)) return;
    hideTip();
  });
  document.addEventListener("mousedown", hideTip, true);
  window.addEventListener("scroll", hideTip, true);

  // ── 시작 + 이후 변화 감시 ─────────────────────────────
  let observer = null;
  function onMutations(records) {
    const targets = new Set();
    for (const rec of records) {
      const t = rec.target.nodeType === 1 ? rec.target : rec.target.parentElement;
      const btn = t && t.closest && t.closest(IB_CANDIDATE);
      if (btn) targets.add(btn);
      rec.addedNodes.forEach((n) => { if (n.nodeType === 1) targets.add(n); });
    }
    targets.forEach(scan);
    observer.takeRecords(); // 위에서 바꾼 것 때문에 다시 불리지 않게 버린다
    if (tipFor && !document.body.contains(tipFor)) hideTip();
  }

  function start() {
    scan(document.body);
    observer = new MutationObserver(onMutations);
    observer.observe(document.body, { childList: true, subtree: true, characterData: true });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();

  window.iconifyButtons = scan;
})();
