const fmt = (n) => n.toLocaleString("ko-KR") + "원";

const tierClass = {
  entry: "tier-entry",
  standard: "tier-standard",
  pro: "tier-pro",
  expert: "tier-expert",
  "expert-5080": "tier-expert",
  "expert-5090": "tier-expert-5090",
  premium: "tier-expert",
  iworks: "tier-standard",
  recommend: "tier-pro",
  "game-s": "tier-standard",
  "game-p": "tier-expert",
  price: "tier-standard",
};

let currentStore = "compuzone"; // "compuzone" | "apple"
let quoteData = null;
let activeTab = 0;
let currentCat = "ai";
let includeMonitor = false;
let includeKeyboard = false; // 컴퓨존 기본: 무선키보드 제외
let includeRam32 = false; // AI PC 기본: 컴퓨존과 동일(메모리 ×1), 켜면 32GB(×2)
let includeSetup = false; // 기본: 초기 세팅비 제외 (모니터/키보드와 동일)
let showUnitPrices = true; // 기본: 품목별 단가·금액 표시
const CONSUMER_MARGIN_PERCENT = 30;
let quoteMarginPercent = CONSUMER_MARGIN_PERCENT; // 컴퓨존 기본: 총액의 30% 소비자견적
let hasLoadedOnce = false;
let quoteInfo = null; // 견적처/공급자 정보 (서버 저장)
let currentTrId = null; // 현재 작업내역서 ID

// 게임으로 PC 찾기
let gamePcMeta = null;
let selectedGames = []; // [{id, title}]
let selectedResolution = "";
let gameWizardStep = 1;
let lastGameQuery = null; // {games, titles, resolution}

// 금액대로 찾기
let lastPriceQuery = null; // {min_price, max_price, type} 원 단위
let priceTargetType = "assembled"; // 금액대로 찾기 대상: "assembled" | "notebook"
let resultView = "card"; // "card" | "list"

// CTO 관련 상태
let activeCtoTierKey = null;
let activeCtoIndex = null;
let currentCtoOptions = [];
let baseQuoteForCto = null;
let ctoLive = null; // 공식몰 구성하기 API 응답 {options, total, base_total, selected, parts}. 없으면 비교 옵션 방식

function isAppleQuote(q) {
  return currentStore === "apple" || (q && String(q.price_source || "").startsWith("apple"));
}

function hasLiveCto(q) {
  if (q?.cto_live) return true;
  const groups = q?.cto_groups;
  return Array.isArray(groups) && groups.some((g) => (g.options || g.choices || []).length >= 2);
}

function isMacCategory(cat) {
  return ["apple_macbook", "apple_imac", "apple_macmini", "apple_macstudio", "apple_all"].includes(cat);
}

function ceilToManwon(n) {
  const v = Number(n) || 0;
  if (v === 0) return 0;
  const unit = 10000;
  if (v > 0) return Math.ceil(v / unit) * unit;
  return -Math.ceil(Math.abs(v) / unit) * unit;
}

function shouldCeilManwon() {
  if (currentStore === "apple") return false;
  return currentStore === "compuzone" && quoteMarginPercent > 0;
}

function displayAmount(n, q) {
  const v = Number(n) || 0;
  if (currentStore === "apple" || isAppleQuote(q)) return v;
  return shouldCeilManwon() ? ceilToManwon(v) : v;
}

function activeMarginPercent() {
  return currentStore === "compuzone" ? quoteMarginPercent : 0;
}

function markedUpAmount(n, percent) {
  const v = Number(n) || 0;
  return percent > 0 ? Math.round(v * (100 + percent) / 100) : v;
}

function displayTotal(q) {
  if (isAppleQuote(q)) return Number(q.total) || 0;
  const base = markedUpAmount(q.total, activeMarginPercent());
  return shouldCeilManwon() ? ceilToManwon(base) : base;
}

/**
 * 표시용 품목 목록. 마진을 합계뿐 아니라 품목 단가에도 배분하고,
 * 만원 단위 올림으로 생긴 잔액은 번들할인 행이 흡수해 품목합 = 합계를 맞춘다.
 * Apple 정품 견적은 올림/반올림 없이 공홈 단가 그대로 표시한다.
 */
function displayParts(q) {
  if (isAppleQuote(q)) {
    return q.parts.map((p) => {
      const unit = Number(p.unit_price) || 0;
      return { ...p, unit_price: unit, amount: p.qty > 0 ? unit * p.qty : 0 };
    });
  }
  const pct = activeMarginPercent();
  const rows = q.parts.map((p) => {
    const unit = displayAmount(markedUpAmount(p.unit_price, pct));
    return { ...p, unit_price: unit, amount: p.qty > 0 ? unit * p.qty : 0 };
  });

  const diff = displayTotal(q) - rows.reduce((sum, r) => sum + r.amount, 0);
  if (diff !== 0) {
    for (let i = rows.length - 1; i >= 0; i--) {
      if (rows[i].category !== "할인") continue;
      rows[i].amount += diff;
      rows[i].unit_price = rows[i].amount;
      break;
    }
  }
  return rows;
}

function marginKindLabel(m) {
  if (m === CONSUMER_MARGIN_PERCENT) return "소비자견적";
  if (m === 0) return "원가견적";
  return `마진 ${m}% 견적`;
}

function badgeClass(tierKey) {
  const key = String(tierKey);
  if (key.startsWith("macbook") || key.startsWith("imac") ||
      key.startsWith("macmini") || key.startsWith("macstudio") ||
      key.startsWith("ipad") || key.startsWith("iphone") ||
      key.startsWith("apple")) {
    return "tier-apple";
  }
  if (key.startsWith("game-s")) return tierClass["game-s"];
  if (key.startsWith("game-p")) return tierClass["game-p"];
  if (key.startsWith("price")) return tierClass.price;
  return tierClass[key] || tierClass[key.split("-")[0]] || "";
}

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

const LOADING_TEXT_COMPUZONE = "컴퓨존에서 실시간 견적 수집 중…";
const LOADING_TEXT_APPLE = "애플 공식홈페이지에서 검색 중…";

function setLoadingMessage(text) {
  const el = $("#loading")?.querySelector("p");
  if (el) el.textContent = text;
}

function loadingMessageFor(cat) {
  const key = String(cat || "");
  if (currentStore === "apple" || key.startsWith("apple_") || key === "apple") {
    return LOADING_TEXT_APPLE;
  }
  return LOADING_TEXT_COMPUZONE;
}

function updateEmptyStateCopy() {
  const p = $("#emptyState p");
  if (!p) return;
  if (currentStore === "apple") {
    p.innerHTML =
      "상단 <strong>MacBook · iMac · Mac mini · Mac Studio · iPad & iPhone</strong> 버튼을 눌러<br />애플 공식홈페이지에서 현재 판매 제품을 불러오세요.";
  } else {
    p.innerHTML =
      "상단 <strong>AI PC · 프리미엄PC · 아이웍스PC · 추천조립PC · 게임으로PC찾기 · 금액대로 찾기</strong> 버튼을 눌러<br />컴퓨존 실시간 견적을 불러오세요.";
  }
}

function escapeHtml(str) {
  if (str == null) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function show(el) {
  if (!el) return;
  el.classList.remove("hidden");
  el.setAttribute("aria-hidden", "false");
}
function hide(el) {
  if (!el) return;
  el.classList.add("hidden");
  el.setAttribute("aria-hidden", "true");
}

// 화면 가운데 처리 중 팝업 (Excel 생성·CTO 옵션·시세 검색·링크 준비·업데이트 대기 등)
let busyDepth = 0;
function showBusy(text) {
  busyDepth += 1;
  const t = $("#busyText");
  if (t) t.textContent = text;
  show($("#busyBox"));
}
function setBusyText(text) {
  const t = $("#busyText");
  if (t) t.textContent = text;
}
function hideBusy() {
  busyDepth = Math.max(0, busyDepth - 1);
  if (!busyDepth) hide($("#busyBox"));
}

/** 서버에서 파일을 받아 저장. 생성하는 동안 처리 상태 창을 보여 준다. */
async function downloadWithBusy(url, options = {}, fallbackName = "download.xlsx", label = "Excel 파일 생성 중…") {
  showBusy(label);
  try {
    const res = await fetch(url, options);
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `파일 생성 실패 (${res.status})`);
    }
    const cd = res.headers.get("Content-Disposition") || "";
    const star = cd.match(/filename\*=UTF-8''([^;]+)/i);
    const plain = cd.match(/filename="([^"]+)"/i);
    const name = star ? decodeURIComponent(star[1]) : plain ? plain[1] : fallbackName;
    if (res.headers.get("X-Archive-Id")) loadQuoteArchive();
    const blob = await res.blob();
    const href = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = href;
    a.download = name;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(href), 1000);
    return true;
  } catch (e) {
    alert("다운로드 오류: " + e.message);
    return false;
  } finally {
    hideBusy();
  }
}

function openCoupangSearch(keyword, event) {
  if (event) {
    event.preventDefault();
    event.stopPropagation();
  }
  const q = String(keyword || "").trim();
  if (!q) return;

  const fallback = `https://www.coupang.com/np/search?component=&q=${encodeURIComponent(q)}&channel=user`;
  window.open(fallback, "_blank", "noopener");
}

let usedMarketLinks = { keyword: "", bunjang_url: "", joongna_url: "" };

function looksAppleModel(model) {
  const text = String(model || "");
  const lower = text.toLowerCase();
  return lower.startsWith("apple") || /맥북|아이맥|맥미니|맥 미니|맥스튜디오|맥 스튜디오|아이패드|아이폰|애플/.test(text);
}

function usedSearchKeyword(model, cpu, gpu) {
  if (looksAppleModel(model)) return String(model || "").trim();
  /* 조립PC는 CPU + 그래픽카드만 사용 (모델명 폴백 없음) */
  const parts = [cpu, gpu].map(s => String(s || "").trim()).filter(Boolean);
  return parts.join(" ");
}

function usedSearchButtonAttrs(q) {
  const apple = isAppleQuote(q);
  const cpu = apple ? "" : encodeURIComponent(quotePartName(q, "CPU") || "");
  const vga = apple ? "" : encodeURIComponent(quotePartName(q, "그래픽카드") || "");
  return `data-keyword="${encodeURIComponent(q.model || "")}" data-cpu="${cpu}" data-vga="${vga}"`;
}

function fallbackUsedLinks(model, cpu, gpu) {
  const keyword = usedSearchKeyword(model, cpu, gpu);
  if (!keyword) return { keyword: "", bunjang_url: "", joongna_url: "" };
  return {
    keyword,
    bunjang_url: `https://m.bunjang.co.kr/search/products?order=score&q=${encodeURIComponent(keyword)}`,
    joongna_url: `https://web.joongna.com/search/${encodeURIComponent(keyword)}`,
  };
}

async function openUsedMarketDirect(source, market, event) {
  if (event) {
    event.preventDefault();
    event.stopPropagation();
  }
  let model = "";
  let cpu = "";
  let gpu = "";
  if (source && source.dataset) {
    model = decodeURIComponent(source.dataset.keyword || "");
    cpu = decodeURIComponent(source.dataset.cpu || "");
    gpu = decodeURIComponent(source.dataset.vga || "");
  } else {
    model = String(source || "").trim();
  }
  if (!model && !cpu && !gpu) return;
  let links = fallbackUsedLinks(model, cpu, gpu);
  showBusy("중고 시세 검색어 준비 중…");
  try {
    const params = new URLSearchParams();
    if (model) params.set("q", model);
    if (cpu) params.set("cpu", cpu);
    if (gpu) params.set("gpu", gpu);
    const res = await fetch(`/api/used/links?${params.toString()}`);
    const data = await res.json().catch(() => ({}));
    if (res.ok && data.bunjang_url) {
      links = {
        keyword: data.keyword || links.keyword,
        bunjang_url: data.bunjang_url,
        joongna_url: data.joongna_url || links.joongna_url,
      };
    }
  } catch (_) {
    /* 키워드 API 실패 시 CPU/그래픽카드로 만든 검색어를 그대로 쓴다 */
  } finally {
    hideBusy();
  }
  const url = market === "joongna" ? links.joongna_url : links.bunjang_url;
  if (url) window.open(url, "_blank", "noopener,noreferrer");
}

window.openUsedMarketDirect = openUsedMarketDirect;

// ---------- 다나와 비슷한 PC 찾기 (CPU · 그래픽카드 · RAM · SSD 기준) · 쿠팡 검색 ----------
// 완제품 PC 에는 모니터·키보드·세팅비가 없으므로 본체 금액끼리 비교한다.
const SIMILAR_NON_BODY = ["모니터", "키보드마우스", "초기 세팅비", "기술지원"];
const SIMILAR_STORES = {
  danawa: { label: "다나와", icon: "🔍", api: "/api/danawa/similar", unit: "다나와 최저가" },
};
const danawaModal = $("#danawaModal");

function quoteBodyPrice(q) {
  if (!q.parts?.length) return displayTotal(q); // 노트북 견적은 품목 없이 합계만 있다
  return displayParts(q)
    .filter((p) => !SIMILAR_NON_BODY.includes(p.category))
    .reduce((sum, p) => sum + (Number(p.amount) || 0), 0);
}

function similarChip(label, state) {
  const mark = state === "exact" ? "✓" : (state === "near" ? "≈" : "✗");
  return `<span class="dn-chip dn-${state || "none"}">${label} ${mark}</span>`;
}

function fmtCapacity(gb) {
  if (!gb) return "-";
  return gb >= 1024 && gb % 1024 === 0 ? `${gb / 1024}TB` : `${gb}GB`;
}

function renderSimilarModal(store, q, data) {
  const conf = SIMILAR_STORES[store];
  const base = data.base || {};
  const basePrice = Number(base.price) || 0;
  $("#danawaTitle").textContent = `${conf.icon} ${conf.label} 비슷한 PC (CPU · 그래픽카드 · RAM · SSD 기준)`;
  $("#danawaBase").innerHTML = `
    <div><strong>컴퓨존 기준</strong> · ${escapeHtml(q.model || "")}</div>
    <div class="dn-base-spec">
      CPU <b>${escapeHtml(base.cpu || "-")}</b> · 그래픽 <b>${escapeHtml(base.gpu || "-")}</b> ·
      RAM <b>${fmtCapacity(base.ram_gb)}</b> · SSD <b>${fmtCapacity(base.ssd_gb)}</b> ·
      ${base.notebook ? "노트북 금액" : "본체 금액"} <b>${basePrice ? fmt(basePrice) : "-"}</b>
      ${base.notebook ? "" : '<span class="dn-note">(모니터·키보드·세팅비 제외)</span>'}
    </div>
    <div class="dn-base-query">${conf.label} 검색어: ${escapeHtml(data.query || "")}
      <a href="${escapeHtml(data.search_url || "#")}" target="_blank" rel="noopener">${conf.label}에서 더 보기 ↗</a></div>`;

  const items = data.items || [];
  if (!items.length) {
    $("#danawaList").innerHTML = `<p class="dn-empty">${conf.label}에서 비슷한 PC를 찾지 못했습니다. 위 링크로 직접 검색해 보세요.</p>`;
  } else {
    $("#danawaList").innerHTML = `
      <table class="dn-table">
        <thead><tr><th>#</th><th></th><th>제품 / 사양</th><th>일치</th><th>${conf.unit}</th><th>컴퓨존 대비</th></tr></thead>
        <tbody>${items.map((it, idx) => {
          const price = Number(it.price) || 0;
          const diff = price && basePrice ? price - basePrice : null;
          const diffText = diff === null ? "-" : `${diff > 0 ? "+" : ""}${diff.toLocaleString("ko-KR")}원`;
          const diffClass = diff === null ? "" : (diff > 0 ? "dn-up" : "dn-down");
          return `
          <tr>
            <td class="dn-rank">${idx + 1}</td>
            <td class="dn-thumb">${it.image ? `<img src="${escapeHtml(it.image)}" alt="" loading="lazy" referrerpolicy="no-referrer" />` : ""}</td>
            <td class="dn-name">
              <a href="${escapeHtml(it.url)}" target="_blank" rel="noopener">${escapeHtml(it.name)}</a>
              <div class="dn-spec">${escapeHtml(it.spec || "")}</div>
            </td>
            <td class="dn-match">${similarChip("CPU", it.match?.cpu)}${similarChip("GPU", it.match?.gpu)}${similarChip("RAM", it.match?.ram)}${similarChip("SSD", it.match?.ssd)}</td>
            <td class="dn-price">${price ? fmt(price) : "가격없음"}</td>
            <td class="dn-diff ${diffClass}">${diffText}</td>
          </tr>`;
        }).join("")}</tbody>
      </table>`;
  }
  show(danawaModal);
}

async function openSimilarCompare(store, index, event) {
  if (event) {
    event.preventDefault();
    event.stopPropagation();
  }
  const conf = SIMILAR_STORES[store];
  const q = quoteData?.quotes?.[index];
  if (!conf || !q) return;
  const params = quoteSpecParams(q);
  params.set("price", String(quoteBodyPrice(q)));
  showBusy(`${conf.label}에서 사양이 비슷한 PC 찾는 중…`);
  try {
    const res = await fetch(`${conf.api}?${params.toString()}`);
    const data = await res.json().catch(() => ({}));
    const detail = data.detail;
    if (!res.ok) throw new Error((detail && (detail.message || detail)) || `${conf.label} 검색에 실패했습니다.`);
    renderSimilarModal(store, q, data);
  } catch (e) {
    alert(e.message || `${conf.label} 검색에 실패했습니다.`);
  } finally {
    hideBusy();
  }
}

/** 견적의 CPU · 그래픽카드 · RAM · SSD 품명(노트북은 모델명)을 비교 · 검색 API 파라미터로 */
function quoteSpecParams(q) {
  const part = (cat) => (q.parts || []).find((p) => p.category === cat);
  const mem = part("메모리");
  const ssd = part("SSD");
  return new URLSearchParams({
    cpu: quotePartName(q, "CPU") || "",
    gpu: quotePartName(q, "그래픽카드") || "",
    ram: mem?.name || "",
    ram_qty: String(mem?.qty || 1),
    ssd: ssd?.name || "",
    ssd_qty: String(ssd?.qty || 1),
    model: q.model || "",
  });
}

/** 🛒 쿠팡: CPU · 그래픽카드 검색어로 쿠팡 일반 검색 페이지를 연다(파트너스 API 는 쓰지 않는다). */
async function openCoupangSpecSearch(index, event) {
  if (event) {
    event.preventDefault();
    event.stopPropagation();
  }
  const q = quoteData?.quotes?.[index];
  if (!q) return;
  let url = "";
  showBusy("쿠팡 검색어 준비 중…");
  try {
    const res = await fetch(`/api/coupang/link?${quoteSpecParams(q).toString()}`);
    const data = await res.json().catch(() => ({}));
    if (res.ok) url = data.search_url || "";
  } catch (_) {
    /* 검색어를 못 만들면 모델명으로 검색한다 */
  } finally {
    hideBusy();
  }
  if (url) window.open(url, "_blank", "noopener");
  else openCoupangSearch(q.model);
}

window.openSimilarCompare = openSimilarCompare;
window.openCoupangSpecSearch = openCoupangSpecSearch;
$("#danawaClose")?.addEventListener("click", () => hide(danawaModal));
danawaModal?.addEventListener("click", (e) => {
  if (e.target === danawaModal) hide(danawaModal);
});

function renderSummaryCards(quotes) {
  const container = $("#summaryCards");
  if (!container) return;

  container.innerHTML = quotes.map((q, i) => summaryCardHtml(q, i)).join("");
  bindSummaryCardEvents(container);
}

function isAppleLiveSource(src) {
  const s = String(src || "");
  return s.startsWith("apple_official") || s === "apple_cto";
}

function summaryCardHtml(q, i) {
  const apple = isAppleQuote(q);
  const coupangKeyword = encodeURIComponent(q.model);
  const liveApple = isAppleLiveSource(q.price_source);
  const sourceClass = q.price_source === "live" || liveApple ? "source-live" : "source-fallback";
  const sourceText = q.price_source === "live"
    ? "● 실시간 수집"
    : (liveApple
      ? (q.price_source === "apple_official_snapshot"
        ? "● Apple 공식몰 스냅샷"
        : (q.price_source === "apple_cto" ? "● Apple 공식몰 + CTO" : "● Apple 공식몰 현재 판매가"))
      : "● 기본가/캐시");
  return `
    <div class="summary-card ${i === activeTab ? "active" : ""}" data-index="${i}">
      <span class="tier-badge ${badgeClass(q.tier_key)}">${q.tab_label || q.tier}</span>
      <h3>${escapeHtml(q.model)}</h3>
      <p class="model">${escapeHtml(q.description)}</p>
      <div class="price-row">
        <div class="price">${fmt(displayTotal(q))}</div>
        ${q.url ? `
        <div class="price-row-btns">
          ${apple ? "" : `
          <a href="https://www.compuzone.co.kr/" target="_blank" rel="noopener" class="btn btn-sm btn-compuzone" title="컴퓨존 메인 페이지를 엽니다" onclick="event.stopPropagation()">
            ⚡ 컴퓨존
          </a>`}
          <a href="/api/image?redirect=1&url=${encodeURIComponent(q.url)}" target="_blank" rel="noopener" class="btn btn-sm btn-image" title="원본 페이지의 대표 이미지를 새 창으로 봅니다" onclick="event.stopPropagation()">
            🖼️ 이미지
          </a>
        </div>` : ""}
      </div>
      <p class="source ${sourceClass}">
        ${sourceText}
      </p>
      <div class="card-actions">
        ${apple ? `
        <a href="${q.url}" target="_blank" rel="noopener" class="btn btn-sm btn-buy" onclick="event.stopPropagation()">
          🛍️ 구입하기 ↗
        </a>
        ${hasLiveCto(q) ? `
        <button type="button" class="btn btn-sm btn-cto btn-open-cto" data-index="${i}" data-tierkey="${q.tier_key}" onclick="event.stopPropagation()">
          ⚙️ 사양 CTO 수정
        </button>` : ""}` : `
        <a href="${q.url}" target="_blank" rel="noopener" class="btn btn-sm btn-buy" onclick="event.stopPropagation()">
          🛍️ 상품상세 ↗
        </a>`}
        ${apple ? "" : `
        <button type="button" class="btn btn-sm btn-danawa" title="컴퓨존 사양(CPU · 그래픽카드 · RAM · SSD)과 가장 비슷한 다나와 완제품 PC를 찾습니다" onclick="openSimilarCompare('danawa', ${i}, event)">
          🔍 다나와
        </button>`}
        ${apple ? `
        <button type="button" class="btn btn-sm btn-coupang btn-coupang-search" data-keyword="${coupangKeyword}" onclick="openCoupangSearch(decodeURIComponent(this.dataset.keyword), event)">
          🛒 쿠팡검색 ↗
        </button>` : `
        <button type="button" class="btn btn-sm btn-coupang" title="CPU · 그래픽카드 검색어로 쿠팡 검색 페이지를 엽니다" onclick="openCoupangSpecSearch(${i}, event)">
          🛒 쿠팡 ↗
        </button>`}
        <button type="button" class="btn btn-sm btn-bunjang" ${usedSearchButtonAttrs(q)} onclick="openUsedMarketDirect(this, 'bunjang', event)">
          ⚡ 번개장터
        </button>
        <button type="button" class="btn btn-sm btn-joongna" ${usedSearchButtonAttrs(q)} onclick="openUsedMarketDirect(this, 'joongna', event)">
          🟢 중고나라
        </button>
        <button type="button" class="btn btn-sm btn-excel-one" data-index="${i}" title="폼1~8 썸네일을 보고 하나를 골라 이 항목만 그 서식으로 받습니다">
          📥 엑셀 견적서
        </button>
      </div>
    </div>`;
}

function bindSummaryCardEvents(container) {
  if (!container) return;
  container.querySelectorAll(".summary-card").forEach((card) => {
    card.addEventListener("click", () => {
      activeTab = parseInt(card.dataset.index, 10);
      if (!quoteData?.quotes) return;
      renderTabs(quoteData.quotes);
      renderDetail(quoteData.quotes);
      applyResultView();
    });
  });

  container.querySelectorAll(".btn-open-cto").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      const idx = parseInt(btn.dataset.index, 10);
      const tierKey = btn.dataset.tierkey;
      openCtoModal(idx, tierKey);
    });
  });

  // 📥 엑셀 견적서 = 서식 미리보기 창(폼1~8 중 하나를 골라 받기)
  container.querySelectorAll(".btn-excel-one").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      openFormGallery(parseInt(btn.dataset.index, 10));
    });
  });
}

function renderEstimateForm(q) {
  const apple = isAppleQuote(q);
  const shownTotal = displayTotal(q);

  const rows = displayParts(q)
    .map((p) => {
      const isDiscount = p.category === "할인" || p.amount < 0 || p.unit_price < 0;
      const isSetup = p.category === "초기 세팅비";
      let unitPriceStr;
      let amountStr;
      // 애플스토어는 세부 단가를 사양으로만 표기한다. 초기 세팅비는 예외.
      if (!showUnitPrices || (apple && !isSetup)) {
        unitPriceStr = "-";
        amountStr = "-";
      } else {
        unitPriceStr = p.qty > 0 && (p.unit_price > 0 || isDiscount) ? fmt(p.unit_price) : "";
        amountStr = p.qty > 0 && (p.amount > 0 || isDiscount) ? fmt(p.amount) : "별도청구";
      }
      return `
    <tr${isDiscount ? ' class="discount-row"' : ""}>
      <td class="col-no">${p.no}</td>
      <td class="col-cat">${p.category}</td>
      <td class="col-name">${p.name}</td>
      <td class="col-qty">${p.qty > 0 ? p.qty : "1"}</td>
      <td class="col-price">${unitPriceStr}</td>
      <td class="col-amount">${amountStr}</td>
    </tr>`;
    })
    .join("");

  const storeHeaderLabel = apple ? "APPLE STORE ESTIMATE (사양 명세서)" : "COMPUZONE PC ESTIMATE";
  const storeFooterNotice = apple
    ? "※ 애플스토어 견적 표시 방식: 세부 품목별 단가는 사양으로 표시되며, 제품 구매가가 합계로 표기됩니다."
    : (quoteMarginPercent > 0
      ? `※ ${marginKindLabel(quoteMarginPercent)} · 컴퓨존 판매가 기준 품목 단가와 합계에 ${quoteMarginPercent}% 마진 적용`
      : "※ 컴퓨존 실시간 판매가 기준 · 아래 링크는 컴퓨존 견적 공유 URL입니다.");
  const unitPriceNotice = showUnitPrices
    ? ""
    : '<p class="estimate-disclaimer">※ 품목별 단가·금액은 표시하지 않고 합계금액만 안내합니다.</p>';
  return `
  <div class="estimate-form">
    <div class="estimate-header">
      <h2>견&nbsp;&nbsp;적&nbsp;&nbsp;서</h2>
      <p>${storeHeaderLabel}</p>
    </div>
    <div class="estimate-meta">
      <div class="meta-row"><span class="meta-label">견적일</span><span class="meta-value">${quoteInfo?.quote_date || q.quote_date}</span></div>
      <div class="meta-row"><span class="meta-label">견적구분</span><span class="meta-value">${q.tier}</span></div>
      <div class="meta-row"><span class="meta-label">모델명</span><span class="meta-value">${q.model}</span></div>
      <div class="meta-row"><span class="meta-label">상품번호</span><span class="meta-value">${q.product_no}</span></div>
      ${quoteInfo?.customer_name || quoteInfo?.customer_manager ? `
      <div class="meta-row"><span class="meta-label">견적처</span><span class="meta-value">${[quoteInfo.customer_name, quoteInfo.customer_manager].filter(Boolean).join(" · ")}</span></div>` : ""}
      ${quoteInfo?.supplier_name ? `
      <div class="meta-row"><span class="meta-label">공급자</span><span class="meta-value">${[quoteInfo.supplier_name, quoteInfo.supplier_manager, quoteInfo.supplier_phone].filter(Boolean).join(" · ")}</span></div>` : ""}
      ${quoteInfo?.supplier_email ? `
      <div class="meta-row"><span class="meta-label">이메일</span><span class="meta-value">${quoteInfo.supplier_email}</span></div>` : ""}
    </div>
    <div class="estimate-table-wrap">
      <table class="estimate-table">
        <thead>
          <tr>
            <th style="width: 50px;">No</th>
            <th style="width: 100px;">구분</th>
            <th>품명 및 규격 명세</th>
            <th style="width: 60px;">수량</th>
            <th style="width: 120px;">단가</th>
            <th style="width: 130px;">금액</th>
          </tr>
        </thead>
        <tbody>
          ${rows}
        </tbody>
        <tfoot>
          <tr>
            <td colspan="5" class="total-label">합계 (VAT 포함)</td>
            <td class="total-price">${fmt(shownTotal)}</td>
          </tr>
        </tfoot>
      </table>
    </div>
    <div class="estimate-footer">
      <p class="estimate-disclaimer">※ 윈도우, 오피스 별도 · 기술지원비 별도청구</p>
      ${unitPriceNotice}
      <p>${storeFooterNotice}</p>
      <p><a href="${q.url}" target="_blank" rel="noopener">${q.url}</a></p>
    </div>
    ${q.url ? `
    <div class="estimate-image" data-src-url="${escapeHtml(q.url)}">
      <span class="estimate-image-status">🖼️ 대표 이미지 불러오는 중…</span>
    </div>` : ""}
  </div>`;
}

// 원본 페이지 URL → 대표 이미지 URL ("" = 없음). 같은 상품을 다시 열 때 서버에 묻지 않는다.
const detailImageCache = new Map();

/** 견적서 하단에 원본 페이지 대표 이미지를 표시한다. 견적을 열 때만 불러온다. */
async function loadDetailImage(container) {
  const box = container?.querySelector(".estimate-image");
  if (!box) return;
  const srcUrl = box.dataset.srcUrl;
  let image = detailImageCache.get(srcUrl);
  if (image === undefined) {
    try {
      const res = await fetch(`/api/image?url=${encodeURIComponent(srcUrl)}`);
      image = res.ok ? (await res.json()).image || "" : "";
    } catch (_) {
      image = "";
    }
    detailImageCache.set(srcUrl, image);
  }
  if (!box.isConnected || box.dataset.srcUrl !== srcUrl) return; // 그사이 다른 견적으로 바뀜
  if (!image) {
    box.innerHTML = '<span class="estimate-image-status">대표 이미지를 찾지 못했습니다.</span>';
    return;
  }
  const source = srcUrl.includes("apple.com") ? "Apple 공식몰 (제품군 대표 사진)" : "컴퓨존 상품 사진";
  box.innerHTML = `
    <a href="${escapeHtml(image)}" target="_blank" rel="noopener" title="클릭하면 원본 크기로 엽니다">
      <img src="${escapeHtml(image)}" alt="대표 이미지" loading="lazy" />
    </a>
    <span class="estimate-image-caption">대표 이미지 · ${source} · 클릭하면 크게 보기</span>`;
  box.querySelector("img")?.addEventListener("error", () => {
    box.innerHTML = '<span class="estimate-image-status">대표 이미지를 불러오지 못했습니다.</span>';
  });
}

function renderTabs(quotes) {
  const container = $("#tabBar");
  if (!container) return;

  container.innerHTML = quotes
    .map(
      (q, i) => `
    <button class="tab-btn ${i === activeTab ? "active" : ""}" data-index="${i}">
      ${q.tab_label || q.tier} (${fmt(displayTotal(q))})
    </button>`
    )
    .join("");

  container.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      activeTab = parseInt(btn.dataset.index, 10);
      renderTabs(quotes);
      renderDetail(quotes);
      applyResultView();
    });
  });
}

function renderDetail(quotes) {
  const q = quotes[activeTab];
  if (!q) return;

  const detailArea = $("#quotePanels");
  if (!detailArea) return;

  detailArea.innerHTML = renderEstimateForm(q);
  loadDetailImage(detailArea);
}

function excelQueryParams(index) {
  const params = new URLSearchParams({
    cat: currentCat === "game-pc" ? "game" : currentCat,
    monitor: includeMonitor ? "1" : "0",
    keyboard: includeKeyboard ? "1" : "0",
    ram32: includeRam32 ? "1" : "0",
    setup: includeSetup ? "1" : "0",
    unitprice: showUnitPrices ? "1" : "0",
    margin: String(currentStore === "apple" ? 0 : quoteMarginPercent),
    t: String(Date.now()),
  });
  if (currentCat === "game-pc" && lastGameQuery) {
    params.set("games", lastGameQuery.games.join("|"));
    params.set("titles", lastGameQuery.titles.join("|"));
    params.set("resolution", lastGameQuery.resolution);
  }
  if (currentCat === "price" && lastPriceQuery) {
    params.set("min_price", String(lastPriceQuery.min_price));
    params.set("max_price", String(lastPriceQuery.max_price));
    params.set("price_type", lastPriceQuery.type || "assembled");
  }
  params.set("index", String(index));
  const cto = ctoUpgradesForExcel(index);
  if (cto) params.set("cto", cto);
  return params.toString();
}

/** 화면에서 적용한 Apple CTO 추가 품목을 {tier_key: [{category, name, amount}]} JSON 으로 만든다. */
function ctoUpgradesForExcel(index) {
  const quotes = quoteData?.quotes || [];
  const picked = [quotes[index]];
  const map = {};
  picked.forEach((q) => {
    const added = q?._ctoAdded || [];
    if (!q?._ctoOriginal || !q.tier_key || !added.length) return;
    map[q.tier_key] = added.map((p) => ({ category: p.category, name: p.name, amount: p.amount }));
  });
  return Object.keys(map).length ? JSON.stringify(map) : "";
}

// ---------- Excel 견적서 서식 (폼1~8) ----------
const EXCEL_FORM_LABELS = { 1: "표준", 2: "인보이스", 3: "보고서", 4: "명세서", 5: "리테일", 6: "조립PC", 7: "데스크탑", 8: "클래식" };

// ---------- 서식 미리보기 갤러리 (카드 한 건: 폼1~8 썸네일 → 하나 골라 그 서식만 받기) ----------
// 창을 열면 폼1~8 썸네일(thumb=1&forms=N)을 동시에 요청한다. 서버는 상주 Excel 여러 개로 나눠 PDF → PNG 를 만들고,
// 다 된 것부터 칸이 채워진다. 한 장을 누르면 같은 PDF(pdf=1, 서버 캐시)를 크게 보여 준다.
// Excel 이 없으면 static/img/forms/formN.png 샘플로 대신한다.
const FORM_GALLERY_DESC = {
  1: "B2B 표준 견적서 · 공급자/수급자 직인란",
  2: "영문 모던 INVOICE 스타일",
  3: "결재·보고용 총액 강조 요약형",
  4: "부품 기술 명세서형",
  5: "매장 즉시 발송용 영수증 스타일",
  6: "부품별 A/S 기간 · 소비자 유의사항",
  7: "제품 SPEC 표 · 고객 발주확인란",
  8: "관공서/기업 납품 세로형 전통 서식",
};
let formGalleryIndex = 0;
let formPdfUrls = {}; // 폼 번호 → 실제 견적서 PDF blob URL (갤러리를 다시 열면 비운다)
let formDetailSeq = 0;
let formGallerySession = 0;
let formThumbUrls = {}; // 폼 번호 → 실제 견적서 썸네일 blob URL
let formThumbsDone = 0;
let formThumbsError = "";

function resetFormPdfUrls() {
  [...Object.values(formPdfUrls), ...Object.values(formThumbUrls)].forEach((u) => URL.revokeObjectURL(u));
  formPdfUrls = {};
  formThumbUrls = {};
}

function openFormGallery(index) {
  const modal = $("#formGalleryModal");
  const grid = $("#formGalleryGrid");
  if (!modal || !grid) return;
  const quotes = quoteData?.quotes || [];
  const q = quotes[index];
  if (!q) {
    alert("먼저 견적을 불러오세요.");
    return;
  }
  formGalleryIndex = index;
  resetFormPdfUrls();
  const title = $("#formGalleryTitle");
  if (title) title.textContent = `🖼️ 견적서 서식 미리보기 — ${q.tab_label || q.tier || ""} ${q.model || ""}`.trim();
  grid.innerHTML = Object.keys(EXCEL_FORM_LABELS).map((k) => {
    const n = Number(k);
    return `
      <div class="form-gallery-item" data-form="${n}" role="button" tabindex="0" title="폼${n} ${EXCEL_FORM_LABELS[n]} 크게 보기">
        <div class="form-gallery-thumb fg-thumb-wait">⏳ 실제 견적서<br>만드는 중…</div>
        <div class="form-gallery-name">폼${n} (${EXCEL_FORM_LABELS[n]})</div>
        <div class="form-gallery-desc">${FORM_GALLERY_DESC[n] || ""}</div>
        <button type="button" class="btn btn-sm btn-primary">🔍 크게 보기</button>
      </div>`;
  }).join("");
  grid.querySelectorAll(".form-gallery-item").forEach((item) => {
    const pick = () => showFormDetail(Number(item.dataset.form));
    item.addEventListener("click", pick);
    item.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        pick();
      }
    });
  });
  showFormGrid();
  show(modal);
  loadFormThumbs();
}

/** 이 카드의 폼1~8 실제 견적서 썸네일을 동시에 요청해 다 된 칸부터 바꾼다. 실패한 칸은 디자인 샘플 그림을 보여 준다. */
function loadFormThumbs() {
  const session = ++formGallerySession;
  const started = Date.now();
  formThumbsDone = 0;
  formThumbsError = "";
  updateFormGridHint(0);
  const forms = Object.keys(EXCEL_FORM_LABELS).map(Number);
  const tick = setInterval(() => {
    if (session !== formGallerySession || formThumbsDone >= forms.length) return clearInterval(tick);
    updateFormGridHint(Math.round((Date.now() - started) / 1000));
  }, 1000);
  forms.forEach(async (n) => {
    let src = `/static/img/forms/form${n}.png`;
    let label = "디자인 샘플";
    try {
      const res = await fetch(`/api/download/excel?${excelQueryParams(formGalleryIndex)}&forms=${n}&thumb=1`);
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `썸네일 생성 실패 (${res.status})`);
      }
      const blob = await res.blob();
      if (session !== formGallerySession) return;
      src = formThumbUrls[n] = URL.createObjectURL(blob);
      label = "실제 견적서";
    } catch (e) {
      if (session !== formGallerySession) return;
      formThumbsError = e.message;
    }
    setFormGridImage(n, src, label);
    formThumbsDone += 1;
    updateFormGridHint(Math.round((Date.now() - started) / 1000));
  });
}

function setFormGridImage(n, src, label) {
  const item = $("#formGalleryGrid")?.querySelector(`.form-gallery-item[data-form="${n}"]`);
  if (!item) return;
  const img = document.createElement("img");
  img.className = "form-gallery-thumb";
  img.src = src;
  img.alt = `폼${n} ${EXCEL_FORM_LABELS[n]} ${label}`;
  item.querySelector(".form-gallery-thumb")?.replaceWith(img);
}

function updateFormGridHint(elapsed = 0) {
  const hint = $("#formGalleryHint");
  if (!hint || $("#formGalleryGrid")?.classList.contains("hidden")) return;
  const total = Object.keys(EXCEL_FORM_LABELS).length;
  if (formThumbsDone < total) {
    hint.textContent = `⏳ 이 카드의 실제 견적서 만드는 중… ${formThumbsDone}/${total}장 · ${elapsed}초 (Excel → PDF). 먼저 서식을 눌러도 됩니다.`;
  } else if (formThumbsError) {
    hint.textContent = `⚠️ 일부 서식은 실제 견적서를 만들지 못해 디자인 샘플 그림을 보여 줍니다(${formThumbsError}). 📥 다운로드 · ✉️ 이메일은 그대로 쓸 수 있습니다.`;
  } else {
    hint.textContent = "이 카드의 실제 견적서입니다(현재 마진 · 견적정보 · 옵션 반영). 서식을 누르면 크게 보여 주고, 거기서 📥 다운로드(Excel) 또는 ✉️ 이메일(Excel · PDF 첨부)을 고릅니다.";
  }
}

/** 8장 목록으로 돌아간다. */
function showFormGrid() {
  show($("#formGalleryGrid"));
  hide($("#formGalleryDetail"));
  updateFormGridHint();
}

/** 고른 서식으로 만든 이 카드의 실제 견적서(PDF)를 8장 보이던 자리에 크게 보여 주고, 다운로드 · 이메일 버튼을 단다. */
function showFormDetail(n) {
  const detail = $("#formGalleryDetail");
  if (!detail || !EXCEL_FORM_LABELS[n]) return;
  const customerEmail = (quoteInfo?.customer_email || "").trim();
  const hint = $("#formGalleryHint");
  if (hint) hint.textContent = "이 카드의 실제 견적서입니다(현재 마진 · 견적정보 · 옵션 반영). 폼 번호를 누르면 다른 서식으로 바꿔 봅니다.";
  const tabs = Object.keys(EXCEL_FORM_LABELS).map((k) =>
    `<button type="button" class="btn btn-sm ${Number(k) === n ? "btn-primary" : "btn-outline"} fg-tab" data-form="${k}" title="폼${k} (${EXCEL_FORM_LABELS[k]})">폼${k}</button>`).join("");
  detail.innerHTML = `
    <div class="fg-detail-bar">
      <button type="button" class="btn btn-sm btn-outline fg-back">⬅ 서식 목록</button>
      <strong>폼${n} (${EXCEL_FORM_LABELS[n]})</strong>
      <span class="fg-tabs">${tabs}</span>
      <div class="fg-detail-actions">
        <button type="button" class="btn btn-sm btn-primary fg-download" title="이 서식으로 이 카드의 견적서를 Excel 파일로 받습니다">📥 다운로드 (Excel)</button>
        <button type="button" class="btn btn-sm btn-outline fg-email" title="견적서(Excel · PDF 선택)를 첨부한 메일 작성 창을 엽니다 — 받는 사람: ${escapeHtml(customerEmail || "견적처 이메일 미입력(직접 입력)")}">✉️ 이메일</button>
      </div>
    </div>
    <div class="fg-detail-view"><div class="fg-detail-msg">⏳ 실제 견적서를 만드는 중… (Excel → PDF)</div></div>`;
  detail.querySelector(".fg-back").addEventListener("click", showFormGrid);
  detail.querySelectorAll(".fg-tab").forEach((b) => b.addEventListener("click", () => showFormDetail(Number(b.dataset.form))));
  detail.querySelector(".fg-download").addEventListener("click", () => downloadFormFromGallery(n));
  detail.querySelector(".fg-email").addEventListener("click", () => emailFormFromGallery(n));
  hide($("#formGalleryGrid"));
  show(detail);
  loadFormPdf(n, detail.querySelector(".fg-detail-view"));
}

/** 실제 견적서 PDF 를 받아 iframe(브라우저 PDF 보기)으로 띄운다. 실패하면 샘플 그림과 이유를 보여 준다. */
async function loadFormPdf(n, view) {
  const seq = ++formDetailSeq;
  let url = formPdfUrls[n];
  if (!url) {
    showBusy(`폼${n} ${EXCEL_FORM_LABELS[n]} 실제 견적서 만드는 중…`);
    try {
      const res = await fetch(galleryFileUrl(n, { pdf: true }));
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `미리보기 실패 (${res.status})`);
      }
      url = URL.createObjectURL(await res.blob());
      formPdfUrls[n] = url;
    } catch (e) {
      if (seq === formDetailSeq) {
        view.innerHTML = `
          <div class="fg-detail-msg fg-detail-err">⚠️ 실제 견적서 미리보기를 만들지 못했습니다: ${escapeHtml(e.message)}<br>
          아래는 디자인 샘플입니다. 📥 다운로드 · ✉️ 이메일은 그대로 쓸 수 있습니다.</div>
          <img src="/static/img/forms/form${n}_large.png" alt="폼${n} ${EXCEL_FORM_LABELS[n]} 샘플" />`;
      }
      return;
    } finally {
      hideBusy();
    }
  }
  if (seq !== formDetailSeq) return; // 그 사이 다른 서식을 눌렀거나 창을 닫았다
  view.innerHTML = `<iframe class="fg-pdf" src="${url}#view=FitH" title="폼${n} ${EXCEL_FORM_LABELS[n]} 실제 견적서"></iframe>`;
}

function closeFormGallery() {
  formDetailSeq += 1;
  formGallerySession += 1;
  hide($("#formGalleryModal"));
}

/** 미리보기(pdf)는 보관함에 넣지 않는다. 다운로드 · 이메일만 archive=1. */
function galleryFileUrl(n, { pdf = false } = {}) {
  const base = `/api/download/excel?${excelQueryParams(formGalleryIndex)}&forms=${n}`;
  return pdf ? `${base}&pdf=1` : `${base}&archive=1`;
}

async function downloadFormFromGallery(n) {
  if (!EXCEL_FORM_LABELS[n]) return;
  closeFormGallery();
  await downloadWithBusy(galleryFileUrl(n), {}, `견적서_폼${n}.xlsx`, `폼${n} ${EXCEL_FORM_LABELS[n]} 견적서 생성 중…`);
}

// ---------- ✉️ 견적서 메일 발송 창 (Gmail SMTP, app/mail_send.py) ----------
// [✉️ 이메일] → 서버가 Excel 을 만들어 초안으로 보관하고 받는 사람(견적처) · 참조(공급자) · 제목 · 본문을 채워 준다.
// 이 창에서 고치고 첨부 형식(Excel · PDF)을 고른 뒤 [보내기] 를 누르면 서버가 보내는 Gmail 계정으로 보낸다.
const mailModal = $("#mailModal");
let mailDraftId = "";
let mailAccount = { user: "", configured: false };

async function emailFormFromGallery(n) {
  if (!EXCEL_FORM_LABELS[n]) return;
  closeFormGallery();
  showBusy(`폼${n} ${EXCEL_FORM_LABELS[n]} 견적서를 첨부한 메일 준비 중…`);
  let data;
  try {
    const res = await fetch(`${galleryFileUrl(n)}&email=1`);
    data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `메일 준비 실패 (${res.status})`);
  } catch (e) {
    alert("이메일 오류: " + e.message);
    return;
  } finally {
    hideBusy();
  }
  openMailDraft(data);
}

/** 서버가 만든 메일 초안(email=1 응답)으로 발송 창을 채워 연다. 카드 견적 · 직접 만든 견적이 함께 쓴다. */
function openMailDraft(data) {
  if (data.archive_id) loadQuoteArchive();
  mailDraftId = data.draft_id;
  $("#mailTo").value = data.to || "";
  $("#mailCc").value = data.cc || "";
  $("#mailSubject").value = data.subject || "";
  $("#mailBody").value = data.body || "";
  $("#mailAttachXlsxName").textContent = `${data.filename} (${qaFmtSize(data.size)})`;
  $("#mailAttachPdfName").textContent = data.pdf_available
    ? data.pdf_filename
    : "이 PC 에 Excel 이 없어 PDF 를 만들 수 없습니다";
  $("#mailAttachXlsx").checked = true;
  $("#mailAttachPdf").checked = !!data.pdf_available;
  $("#mailAttachPdf").disabled = !data.pdf_available;
  syncMailAccountUI(data.account || mailAccount);
  show(mailModal);
  (data.to ? $("#mailSubject") : $("#mailTo"))?.focus();
}

function syncMailAccountUI(account, editing = false) {
  mailAccount = account || { user: "", configured: false };
  const form = !mailAccount.configured || editing;
  $("#mailFrom").textContent = mailAccount.user || "-";
  $("#mailAccountView").classList.toggle("hidden", form);
  $("#mailAccountForm").classList.toggle("hidden", !form);
  if (form) {
    $("#mailUser").value = mailAccount.user || (quoteInfo?.supplier_email || "").trim();
    $("#mailPass").value = "";
  }
}

async function saveMailAccount() {
  const user = $("#mailUser").value.trim();
  const password = $("#mailPass").value;
  showBusy("Gmail 로그인 확인 중…");
  try {
    const res = await postJsonRaw("/api/mail/account", { user, password });
    if (!res.ok) throw new Error(res.data?.detail || "계정을 저장하지 못했습니다.");
    syncMailAccountUI(res.data);
  } catch (e) {
    alert("보내는 계정 오류: " + e.message);
  } finally {
    hideBusy();
  }
}

async function sendQuoteMail() {
  if (!mailAccount.configured) {
    alert("먼저 위의 보내는 Gmail 주소와 앱 비밀번호를 저장하세요.");
    $("#mailUser")?.focus();
    return;
  }
  const to = $("#mailTo").value.trim();
  if (!to) {
    alert("받는 사람 이메일을 입력하세요.");
    $("#mailTo").focus();
    return;
  }
  const attach = [["xlsx", "#mailAttachXlsx"], ["pdf", "#mailAttachPdf"]]
    .filter(([, sel]) => $(sel).checked && !$(sel).disabled)
    .map(([kind]) => kind);
  if (!attach.length) {
    alert("첨부할 형식(Excel · PDF)을 하나 이상 고르세요.");
    return;
  }
  showBusy(attach.includes("pdf") ? `PDF 만들고 ${to} 에게 견적서 메일 보내는 중…` : `${to} 에게 견적서 메일 보내는 중…`);
  let res;
  try {
    res = await postJsonRaw("/api/mail/send", {
      draft_id: mailDraftId,
      to,
      cc: $("#mailCc").value.trim(),
      subject: $("#mailSubject").value,
      body: $("#mailBody").value,
      attach,
    });
  } catch (_) {
    res = { ok: false, status: 0, data: { detail: "서버에 연결할 수 없습니다." } };
  } finally {
    hideBusy();
  }
  if (res.ok) {
    hide(mailModal);
    const attached = (res.data.attach || []).map((k) => (k === "pdf" ? "PDF" : "Excel")).join(" + ");
    alert(`✔ 견적서 메일을 보냈습니다.\n\n받는 사람: ${res.data.to.join(", ")}${res.data.cc.length ? `\n참조: ${res.data.cc.join(", ")}` : ""}\n첨부: ${attached}\n보낸 계정: ${res.data.from}`);
    return;
  }
  if (res.status === 410) hide(mailModal); // 초안 만료: 창을 닫고 다시 시작하게 한다
  if (res.status === 428) syncMailAccountUI({ user: mailAccount.user, configured: false });
  alert("메일 발송 오류: " + (res.data?.detail || `실패 (${res.status})`));
}

$("#mailAccountEdit")?.addEventListener("click", () => syncMailAccountUI(mailAccount, true));
$("#mailAccountSave")?.addEventListener("click", saveMailAccount);
$("#mailSend")?.addEventListener("click", sendQuoteMail);
$("#mailCancel")?.addEventListener("click", () => hide(mailModal));
$("#mailClose")?.addEventListener("click", () => hide(mailModal));
// ❓ 앱 비밀번호 가져오는 법 (메일 발송 창 위에 뜨는 안내 팝업)
const appPwHelpModal = $("#appPwHelpModal");
$("#appPwHelpOpen")?.addEventListener("click", () => show(appPwHelpModal));
$("#appPwHelpClose")?.addEventListener("click", () => hide(appPwHelpModal));
appPwHelpModal?.addEventListener("click", (e) => {
  if (e.target === appPwHelpModal) hide(appPwHelpModal);
});

$("#formGalleryClose")?.addEventListener("click", closeFormGallery);
$("#formGalleryModal")?.addEventListener("click", (e) => {
  if (e.target === $("#formGalleryModal")) closeFormGallery();
});

// ---------- 🗂️ 견적서 관리 (설정관리 탭) ----------
// 두 저장소를 한 목록으로 보여 준다(최신순 · 15개씩 페이지).
//   📝 작성본  : 새 견적 만들기로 직접 작성해 저장한 견적(manual_quotes.json) — 다시 열어 고칠 수 있다
//   📥 보관 파일: 카드 · 작성 창에서 Excel 다운로드 · 이메일로 만든 xlsx 사본(quote_archive/)
// 작성본에서 만든 보관 파일은 manual_id 로 원본 작성본과 이어진다(📝 원본 버튼).
let qaEntries = [];
let qaArchiveStats = { count: 0, size: 0, dir: "" };
let mqList = []; // 작성본 목록 (아래 📝 새 견적 만들기 코드와 함께 쓴다)
const HUB_PAGE_SIZE = 15;
let hubPage = 1;
let hubKind = "all"; // all | manual | archive

function qaFormsLabel(e) {
  const forms = Array.isArray(e.forms) ? e.forms : [];
  const formText = forms.length === 1
    ? `폼${forms[0]} ${EXCEL_FORM_LABELS[forms[0]] || ""}`.trim()
    : (forms.length ? forms.map((n) => `폼${n}`).join("·") : "전체");
  const marginText = e.margin === null || e.margin === undefined ? "" : (e.margin === 0 ? " · 원가" : ` · ${e.margin}%`);
  return formText + marginText;
}

function qaFmtSize(bytes) {
  const kb = Math.max(1, Math.round((bytes || 0) / 1024));
  return kb >= 1024 ? `${(kb / 1024).toFixed(1)}MB` : `${kb}KB`;
}

/** 보관 파일의 출처 배지: 직접 작성 / Apple / 컴퓨존 */
function hubSourceBadge(e) {
  if (e.kind === "manual") return `<span class="hub-src hub-src-manual">📝 직접작성</span>`;
  if (String(e.category || "").startsWith("apple")) return `<span class="hub-src hub-src-apple">🍎 Apple</span>`;
  return `<span class="hub-src hub-src-cz">💻 컴퓨존</span>`;
}

/** 보관 파일을 만든 작성본. manual_id 가 있으면 그것으로, 예전 보관본(manual_id 없음)은 제목 · 거래처 · 금액이 같은 작성본으로 찾는다. */
function hubOriginOf(e) {
  if (e.kind !== "manual") return null;
  if (e.manual_id) return mqList.find((m) => m.id === e.manual_id) || null;
  return mqList.find((m) => (m.title || "컴퓨터 견적") === e.model && (m.customer || "") === (e.customer || "") && m.total === e.total) || null;
}

/** 작성본 · 보관 파일을 같은 모양의 행으로 합친다(날짜 문자열 YYYY-MM-DD HH:MM:SS 내림차순). */
function hubAllRows() {
  const manual = mqList.map((r) => ({
    kind: "manual",
    id: r.id,
    date: r.updated_at || r.created_at || "",
    customer: r.customer || "",
    title: r.title || "(제목 없음)",
    form: `폼${r.form} ${EXCEL_FORM_LABELS[r.form] || ""}`.trim(),
    total: r.total || 0,
    files: qaEntries.filter((e) => hubOriginOf(e)?.id === r.id).length,
    search: [r.title, r.customer, r.customer_info?.manager, r.customer_info?.phone],
  }));
  const archive = qaEntries.map((e) => ({
    kind: "archive",
    id: e.id,
    date: e.saved_at || "",
    customer: e.customer || "",
    title: (e.kind !== "manual" && e.tab_label ? `${e.tab_label} · ` : "") + (e.model || ""),
    form: qaFormsLabel(e),
    total: e.total || 0,
    entry: e,
    search: [e.customer, e.model, e.filename, e.note, e.tab_label],
  }));
  return [...manual, ...archive].sort((a, b) => String(b.date).localeCompare(String(a.date)));
}

function hubRowHtml(r) {
  const id = escapeHtml(r.id);
  const date = escapeHtml(String(r.date).slice(0, 16));
  if (r.kind === "manual") {
    const tip = `작성본 · 수정: ${r.date}${r.files ? `\n이 작성본에서 만든 보관 파일 ${r.files}건` : ""}`;
    return `
    <tr class="hub-row-manual" title="${escapeHtml(tip)}">
      <td>${date}</td>
      <td><span class="hub-kind hub-kind-manual">📝 작성본</span></td>
      <td class="qa-ellip">${escapeHtml(r.customer || "-")}</td>
      <td class="qa-ellip">${escapeHtml(r.title)}${r.files ? ` <span class="hub-files" title="이 작성본에서 만든 보관 파일 수">📥${r.files}</span>` : ""}</td>
      <td class="qa-ellip">${escapeHtml(r.form)}</td>
      <td class="qa-num">${r.total ? fmt(r.total) : "-"}</td>
      <td>
        <div class="tr-list-actions">
          <button type="button" class="btn btn-xs btn-primary" data-act="edit" data-kind="manual" data-id="${id}" title="작성 창에서 열어 고치거나 미리보기 · 이메일로 보냅니다">✏️ 열기</button>
          <button type="button" class="btn btn-xs btn-outline" data-act="excel" data-kind="manual" data-id="${id}" title="저장된 서식으로 Excel 을 받고 보관 파일로도 남깁니다">📥 Excel</button>
          <button type="button" class="btn btn-xs btn-shutdown" data-act="del" data-kind="manual" data-id="${id}" title="작성본 삭제 (보관 파일은 남습니다)">✕ 삭제</button>
        </div>
      </td>
    </tr>`;
  }
  const e = r.entry;
  const src = hubOriginOf(e);
  const tip = [
    e.saved_at && `저장: ${e.saved_at}`,
    `서식: ${qaFormsLabel(e)}`,
    e.filename && `파일: ${e.filename} (${qaFmtSize(e.size)})`,
    e.note && `메모: ${e.note}`,
    e.manual_id && !src && "※ 원본 작성본이 삭제됨",
    e.missing && "※ 보관 파일 없음",
  ].filter(Boolean).join("\n");
  const dis = e.missing ? "disabled" : "";
  const origin = e.manual_id || src
    ? `<button type="button" class="btn btn-xs btn-outline" data-act="origin" data-kind="archive" data-id="${id}" ${src ? "" : "disabled"} title="${src ? "이 파일을 만든 작성본을 작성 창에서 엽니다" : "원본 작성본이 삭제되었습니다"}">📝 원본</button>`
    : "";
  return `
    <tr class="${e.missing ? "is-missing" : ""}" title="${escapeHtml(tip)}">
      <td>${date}</td>
      <td><span class="hub-kind hub-kind-archive">📥 보관</span>${hubSourceBadge(e)}</td>
      <td class="qa-ellip">${escapeHtml(r.customer || "-")}</td>
      <td class="qa-ellip">${escapeHtml(r.title)}</td>
      <td class="qa-ellip">${escapeHtml(r.form)}</td>
      <td class="qa-num">${r.total ? fmt(r.total) : "-"}</td>
      <td>
        <div class="tr-list-actions">
          <button type="button" class="btn btn-xs btn-primary" data-act="dl" data-kind="archive" data-id="${id}" ${dis} title="보관된 파일을 다시 내려받습니다">📥 받기</button>
          ${origin}
          <button type="button" class="btn btn-xs btn-outline" data-act="dir" data-kind="archive" data-id="${id}" ${dis} title="서버 PC 탐색기에서 이 파일을 선택해 보여 줍니다">📂</button>
          <button type="button" class="btn btn-xs btn-shutdown" data-act="del" data-kind="archive" data-id="${id}" title="보관 파일 삭제">✕ 삭제</button>
        </div>
      </td>
    </tr>`;
}

/** ◀ 1 2 … N ▶ (페이지가 많으면 현재 앞뒤 2쪽 + 처음/끝만) */
function renderHubPager(pages) {
  const box = $("#qaPager");
  if (!box) return;
  if (pages <= 1) {
    box.innerHTML = "";
    return;
  }
  const nums = [];
  for (let p = 1; p <= pages; p += 1) {
    if (p === 1 || p === pages || Math.abs(p - hubPage) <= 2) nums.push(p);
    else if (nums[nums.length - 1] !== "…") nums.push("…");
  }
  box.innerHTML = [
    `<button type="button" class="btn btn-xs btn-outline" data-page="${hubPage - 1}" ${hubPage <= 1 ? "disabled" : ""}>◀</button>`,
    ...nums.map((p) => (p === "…"
      ? `<span class="hub-pager-gap">…</span>`
      : `<button type="button" class="btn btn-xs ${p === hubPage ? "btn-primary" : "btn-outline"}" data-page="${p}">${p}</button>`)),
    `<button type="button" class="btn btn-xs btn-outline" data-page="${hubPage + 1}" ${hubPage >= pages ? "disabled" : ""}>▶</button>`,
  ].join("");
}

function renderQuoteHub() {
  const all = hubAllRows();
  const nManual = mqList.length;
  const nArchive = qaEntries.length;
  const badge = $("#quoteArchiveCount");
  if (badge) {
    badge.textContent = String(nManual + nArchive);
    badge.classList.toggle("hidden", !(nManual + nArchive));
  }
  const counts = { all: nManual + nArchive, manual: nManual, archive: nArchive };
  document.querySelectorAll("[data-hub-count]").forEach((el) => { el.textContent = counts[el.dataset.hubCount] ?? 0; });
  document.querySelectorAll(".hub-filter-btn").forEach((b) => {
    const on = b.dataset.kind === hubKind;
    b.classList.toggle("btn-primary", on);
    b.classList.toggle("btn-outline", !on);
    b.setAttribute("aria-pressed", on ? "true" : "false");
  });
  const tbody = $("#qaTbody");
  if (!tbody) return;
  const q = ($("#qaSearch")?.value || "").trim().toLowerCase();
  const rows = all.filter((r) => (hubKind === "all" || r.kind === hubKind)
    && (!q || r.search.some((v) => String(v || "").toLowerCase().includes(q))));
  const pages = Math.max(1, Math.ceil(rows.length / HUB_PAGE_SIZE));
  hubPage = Math.min(Math.max(1, hubPage), pages);
  const stats = $("#qaStats");
  if (stats) {
    const shown = rows.length !== counts[hubKind] ? `${rows.length} / ${counts[hubKind]}건` : `${rows.length}건`;
    stats.textContent = `${shown} · ${hubPage}/${pages}쪽 · 보관 파일 ${qaFmtSize(qaArchiveStats.size)}`;
    stats.title = `보관 폴더: ${qaArchiveStats.dir || ""}`;
  }
  renderHubPager(pages);
  if (!rows.length) {
    const empty = counts[hubKind]
      ? "검색 결과가 없습니다."
      : "견적서가 없습니다. ➕ 새 견적 만들기로 작성하거나, 카드의 📥 엑셀 견적서를 내려받으면 여기에 쌓입니다.";
    tbody.innerHTML = `<tr><td colspan="7" style="text-align:center;color:var(--muted);padding:14px;">${empty}</td></tr>`;
    return;
  }
  tbody.innerHTML = rows.slice((hubPage - 1) * HUB_PAGE_SIZE, hubPage * HUB_PAGE_SIZE).map(hubRowHtml).join("");
}

// 예전 이름으로 부르는 곳(작성 창 · 다운로드 후 갱신)이 그대로 동작하도록 남겨 둔다
const renderQuoteArchive = renderQuoteHub;

async function loadQuoteArchive() {
  const before = qaEntries.length;
  try {
    const res = await fetch("/api/quotes/archive", { cache: "no-store" });
    const data = await res.json();
    qaEntries = data.entries || [];
    qaArchiveStats = { count: data.count || 0, size: data.size || 0, dir: data.dir || "" };
  } catch (_) {
    qaEntries = [];
  }
  if (qaEntries.length > before && before) hubPage = 1; // 새 보관 파일은 맨 앞(1쪽)에 들어간다
  renderQuoteHub();
}

async function qaDownload(id) {
  const e = qaEntries.find((x) => x.id === id);
  await downloadWithBusy(`/api/quotes/archive/${encodeURIComponent(id)}/download`, {}, e?.filename || "견적서.xlsx", "보관된 견적서 내려받는 중…");
}

async function qaDelete(id) {
  const e = qaEntries.find((x) => x.id === id);
  const label = e ? `${e.customer || "-"} · ${e.model || ""}${e.total ? ` · ${fmt(e.total)}` : ""}` : id;
  if (!confirm(`보관함에서 삭제할까요?\n${label}\n(보관 파일도 함께 지워집니다)`)) return;
  try {
    const res = await fetch(`/api/quotes/archive/${encodeURIComponent(id)}`, { method: "DELETE" });
    if (!res.ok) throw new Error("삭제에 실패했습니다.");
  } catch (err) {
    alert(err.message);
  }
  loadQuoteArchive();
}

async function qaOpenDir(id = "") {
  let res;
  try {
    res = await postJsonRaw("/api/quotes/archive/open-dir", id ? { id } : {});
  } catch (_) {
    alert("서버에 연결할 수 없습니다.");
    return;
  }
  if (!res.ok) alert(res.data?.detail?.message || res.data?.detail || "폴더를 열지 못했습니다.");
}

/** 백업 폴더(작업내역서와 공용)에 ZIP 백업. 폴더가 없거나 사라졌으면 선택 창을 띄운 뒤 다시 시도한다. */
async function backupZipToFolder({ url, btnSel, busyText, doneLabel }) {
  const btn = $(btnSel);
  if (btn) btn.disabled = true;
  try {
    if (!trBackupDir) {
      const dir = await chooseTrBackupDir();
      if (!dir) return;
    }
    showBusy(busyText);
    let res;
    try {
      res = await postJsonRaw(url, {});
    } finally {
      hideBusy();
    }
    if (res.status === 428) {
      trBackupDir = "";
      syncTrBackupDirUI();
      const dir = await chooseTrBackupDir();
      if (!dir) return;
      showBusy(busyText);
      try {
        res = await postJsonRaw(url, {});
      } finally {
        hideBusy();
      }
    }
    if (!res.ok || !res.data.ok) {
      const d = res.data?.detail;
      throw new Error((d && (d.message || d)) || "백업에 실패했습니다.");
    }
    const openIt = confirm(
      `✔ 백업 완료 — ${doneLabel(res.data)}\n\n파일: ${res.data.path}\n크기: ${qaFmtSize(res.data.size)}\n\n백업 폴더를 열까요? (서버 PC 화면의 탐색기에 열립니다)`
    );
    if (openIt) await openTrBackupDir(res.data.path);
  } catch (e) {
    alert("백업 오류: " + e.message);
  } finally {
    if (btn) btn.disabled = false;
  }
}

function qaBackup() {
  return backupZipToFolder({
    url: "/api/quotes/archive/backup",
    btnSel: "#qaBtnBackup",
    busyText: "보관함을 ZIP 으로 묶는 중…",
    doneLabel: (d) => `견적서 ${d.count}건`,
  });
}

function refreshQuoteHub() {
  return Promise.all([loadMqList(), loadQuoteArchive()]);
}

$("#qaSearch")?.addEventListener("input", () => {
  hubPage = 1;
  renderQuoteHub();
});
$("#qaBtnRefresh")?.addEventListener("click", refreshQuoteHub);
$("#qaBtnOpenDir")?.addEventListener("click", () => qaOpenDir());
$("#qaBtnBackup")?.addEventListener("click", qaBackup);
document.querySelectorAll(".hub-filter-btn").forEach((b) => b.addEventListener("click", () => {
  hubKind = b.dataset.kind || "all";
  hubPage = 1;
  renderQuoteHub();
}));
$("#qaPager")?.addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-page]");
  if (!btn || btn.disabled) return;
  hubPage = Number(btn.dataset.page) || 1;
  renderQuoteHub();
  $("#qaTbody")?.closest(".tr-list-table-wrap")?.scrollTo({ top: 0 });
});
$("#mqBtnToggleForms")?.addEventListener("click", (e) => {
  const strip = $("#mqFormStrip");
  if (!strip) return;
  renderMqFormGrid();
  const open = strip.classList.contains("hidden");
  strip.classList.toggle("hidden", !open);
  e.currentTarget.setAttribute("aria-expanded", open ? "true" : "false");
  e.currentTarget.textContent = open ? "▲ 서식 접기" : "➕ 새 견적 만들기";
});
$("#qaTbody")?.addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-act]");
  if (!btn || btn.disabled) return;
  const { act, kind, id } = btn.dataset;
  if (kind === "manual") {
    const rec = mqList.find((r) => r.id === id);
    if (act === "edit" && rec) openMqEditor(rec);
    else if (act === "excel" && rec) downloadMqExcel(rec);
    else if (act === "del") deleteMq(id);
    return;
  }
  if (act === "dl") qaDownload(id);
  else if (act === "dir") qaOpenDir(id);
  else if (act === "del") qaDelete(id);
  else if (act === "origin") {
    const entry = qaEntries.find((x) => x.id === id);
    const rec = entry && hubOriginOf(entry);
    if (rec) openMqEditor(rec);
  }
});
// 시작 시 견적서 관리 탭의 건수 배지 (작성본은 아래 새 견적 만들기 코드 끝에서 읽는다)
loadQuoteArchive();

/** 모니터·무선키보드 공통 표시: "{이름} (포함됨|제외됨)" */
function extraStatusText(included) {
  return included ? "포함됨" : "제외됨";
}

function syncExtraToggleUI({ included, labelSel, btnSel, name, text, title }) {
  const status = extraStatusText(included);
  const label = $(labelSel);
  const btn = $(btnSel);
  if (label) label.textContent = text || `${name} (${status})`;
  if (btn) {
    btn.classList.toggle("extra-off", !included);
    btn.classList.toggle("extra-on", included);
    btn.setAttribute("aria-pressed", included ? "true" : "false");
    btn.title = title || `${name} ${status} — 클릭하여 전환`;
  }
}

function updateRam32Visibility() {
  const btn = $("#btnRam32Toggle");
  if (currentStore === "compuzone" && currentCat === "ai") {
    show(btn);
  } else {
    hide(btn);
  }
}

function updateExtraButtonsVisibility() {
  const apple = currentStore === "apple";
  const monitorBtn = $("#btnMonitorToggle");
  const keyboardBtn = $("#btnKeyboardToggle");
  const setupBtn = $("#btnSetupToggle");
  const unitPriceBtn = $("#btnUnitPriceToggle");
  const marginBtn = $("#btnMarginCycle");
  if (apple) {
    hide(monitorBtn);
    hide(keyboardBtn);
    hide($("#btnRam32Toggle"));
    if (isMacCategory(currentCat)) show(setupBtn);
    else hide(setupBtn);
    // Apple 견적은 세부 단가를 사양으로만 표기하므로 단가 토글·마진이 의미 없다.
    hide(unitPriceBtn);
    hide(marginBtn);
  } else if (currentCat === "price" && priceTargetType === "notebook") {
    // 노트북 검색에는 모니터·키보드·세팅비·메모리 옵션이 적용되지 않는다.
    hide(monitorBtn);
    hide(keyboardBtn);
    hide(setupBtn);
    hide($("#btnRam32Toggle"));
    show(unitPriceBtn);
    show(marginBtn);
  } else {
    show(monitorBtn);
    show(keyboardBtn);
    show(setupBtn);
    show(unitPriceBtn);
    show(marginBtn);
    updateRam32Visibility();
  }
}

function updateExtrasUI() {
  // 모니터 버튼 = 무선키보드 버튼과 동일한 로직·표시
  syncExtraToggleUI({
    included: includeMonitor,
    labelSel: "#monitorToggleLabel",
    btnSel: "#btnMonitorToggle",
    name: "모니터27",
  });
  syncExtraToggleUI({
    included: includeKeyboard,
    labelSel: "#keyboardToggleLabel",
    btnSel: "#btnKeyboardToggle",
    name: "무선키보드",
  });
  syncExtraToggleUI({
    included: includeRam32,
    labelSel: "#ram32ToggleLabel",
    btnSel: "#btnRam32Toggle",
    name: "메모리32GB로 추가",
  });
  syncExtraToggleUI({
    included: includeSetup,
    labelSel: "#setupToggleLabel",
    btnSel: "#btnSetupToggle",
    name: "초기 세팅비",
  });
  syncExtraToggleUI({
    included: showUnitPrices,
    labelSel: "#unitPriceToggleLabel",
    btnSel: "#btnUnitPriceToggle",
    name: "단가",
    text: showUnitPrices ? "단가표시" : "단가미표시",
    title: showUnitPrices
      ? "품목별 단가·금액을 표시 중입니다 — 클릭하면 숨깁니다"
      : "품목별 단가·금액을 숨기고 합계만 보여 줍니다 — 클릭하면 표시합니다",
  });
  updateExtraButtonsVisibility();
  syncMarginUI();
}

function toggleExtraAndReload(kind) {
  if (kind === "monitor") includeMonitor = !includeMonitor;
  else if (kind === "keyboard") includeKeyboard = !includeKeyboard;
  else if (kind === "ram32") includeRam32 = !includeRam32;
  else if (kind === "setup") includeSetup = !includeSetup;
  else return;

  updateExtrasUI();
  if (!hasLoadedOnce) return;
  if (currentCat === "game-pc" && lastGameQuery) {
    submitGamePcRecommend();
  } else if (currentCat === "price" && lastPriceQuery) {
    submitPriceSearch({ keepView: true });
  } else {
    // Apple 세팅비 토글처럼 같은 목록을 다시 불러올 때 적용해 둔 CTO 구성을 유지한다.
    fetchQuotes(currentCat, false, { ctoCarry: snapshotCto(quoteData?.quotes) });
  }
}

$("#btnMonitorToggle")?.addEventListener("click", () => toggleExtraAndReload("monitor"));
$("#btnKeyboardToggle")?.addEventListener("click", () => toggleExtraAndReload("keyboard"));
$("#btnRam32Toggle")?.addEventListener("click", () => {
  if (currentCat !== "ai") return;
  toggleExtraAndReload("ram32");
});
$("#btnSetupToggle")?.addEventListener("click", () => toggleExtraAndReload("setup"));

// 단가 표시 여부는 화면 표기만 바꾸므로 견적을 다시 불러오지 않는다.
$("#btnUnitPriceToggle")?.addEventListener("click", () => {
  showUnitPrices = !showUnitPrices;
  updateExtrasUI();
  if (quoteData?.quotes) renderDetail(quoteData.quotes);
});

// 클릭할 때마다 이 순서대로 순환한다.
const MARGIN_CYCLE = [CONSUMER_MARGIN_PERCENT, 25, 20, 15, 10, 0];

/** 버튼 면에 들어가는 짧은 표기 */
function marginButtonLabel(m) {
  return m === CONSUMER_MARGIN_PERCENT ? "C" : String(m);
}

/** 툴팁에서 뜻을 풀어 쓰는 표기 */
function marginFullLabel(m) {
  if (m === CONSUMER_MARGIN_PERCENT) return `C (소비자견적 ${m}%)`;
  if (m === 0) return "0 (원가견적)";
  return `${m} (마진 ${m}%)`;
}

function syncMarginUI() {
  const label = $("#marginCycleLabel");
  const btn = $("#btnMarginCycle");
  if (label) label.textContent = marginButtonLabel(quoteMarginPercent);
  if (!btn) return;
  const next = MARGIN_CYCLE[(MARGIN_CYCLE.indexOf(quoteMarginPercent) + 1) % MARGIN_CYCLE.length];
  btn.classList.toggle("margin-cost", quoteMarginPercent === 0);
  btn.title =
    `현재 ${marginFullLabel(quoteMarginPercent)} — 클릭하면 ${marginFullLabel(next)}(으)로 바뀝니다\n` +
    MARGIN_CYCLE.map(marginButtonLabel).join(" → ") + " 순으로 순환";
}

function setQuoteMargin(percent) {
  const next = parseInt(percent, 10);
  if (!MARGIN_CYCLE.includes(next)) return;
  quoteMarginPercent = next;
  syncMarginUI();
  if (quoteData?.quotes) {
    renderTabs(quoteData.quotes);
    renderDetail(quoteData.quotes);
    applyResultView();
  }
  updateExtrasUI();
}

function cycleQuoteMargin() {
  const at = MARGIN_CYCLE.indexOf(quoteMarginPercent);
  setQuoteMargin(MARGIN_CYCLE[(at + 1) % MARGIN_CYCLE.length]);
}

$("#btnMarginCycle")?.addEventListener("click", (e) => {
  e.preventDefault();
  cycleQuoteMargin();
});

updateExtrasUI();

// ---------- 견적처/공급자 정보 관리 (설정관리 → 견적정보 · 거래처 탭) ----------
const qiFields = {
  customer_name: "#qiCustomerName",
  customer_manager: "#qiCustomerManager",
  customer_address: "#qiCustomerAddress",
  customer_phone: "#qiCustomerPhone",
  customer_email: "#qiCustomerEmail",
  quote_date: "#qiQuoteDate",
  supplier_name: "#qiSupplierName",
  supplier_business_number: "#qiSupplierBizNo",
  supplier_manager: "#qiSupplierManager",
  supplier_address: "#qiSupplierAddress",
  supplier_phone: "#qiSupplierPhone",
  supplier_email: "#qiSupplierEmail",
  monitor_price: "#qiMonitorPrice",
  keyboard_price: "#qiKeyboardPrice",
  setup_price: "#qiSetupPrice",
  bank_name: "#qiBankName",
  bank_account: "#qiBankAccount",
  bank_holder: "#qiBankHolder",
};

// 사용자가 앱 실행 후 입력 및 저장했는지 상태 관리
let isQuoteInfoSaved = false;
let isTaskReportSaved = false;

// 가짜 데이터 판별용 기준 리스트 (상호, 대표/담당자, 주소, 연락처, 이메일)
const DUMMY_VALUES = {
  names: ["대한민국주식회사", "0000", "0000 ", "하나시스템"],
  managers: ["홍길동대표", "홍길동", "애플아저씨", "김대건 대표", "김진수 실장"],
  addresses: ["서울시", "인천광역시 부평구 부개로 71-12 제2동 제1층 152호, 153호", "인천 동구 방축로 37길 30, 35동 131호"],
  phones: ["010-1234-5678", "01012345678", "010-4226-9692", "010-0000-0000", "010 - 4226 - 9692"],
  emails: ["email1234@gmail.com", "hello@example.com", "kimdaekun@gmail.com"],
};

function isDummyValue(val, dummyList) {
  if (!val || !String(val).trim()) return true;
  const clean = String(val).replace(/[\s-]/g, "").toLowerCase();
  return dummyList.some((d) => clean === String(d).replace(/[\s-]/g, "").toLowerCase());
}

// 견적처 또는 공급자가 진짜 데이터로 변경·저장되었는지 검사
function isQuoteInfoDummy(info) {
  // 사용자가 앱 실행 후 정보를 입력하고 저장을 완료했다면 깜박임 즉시 중지!
  if (isQuoteInfoSaved) return false;
  if (!info) return true;

  // 상호명이 기본 가짜 데이터(대한민국주식회사 등)이거나 비어있으면 미갱신으로 간주
  const custDummy = isDummyValue(info.customer_name, DUMMY_VALUES.names);
  const suppDummy = isDummyValue(info.supplier_name, DUMMY_VALUES.names);

  return custDummy || suppDummy;
}

// 작업내역서의 업체정보가 저장되었거나 실제 데이터인지 검사
function isTaskReportCompanyDummy(name, addr, phone) {
  // 작업내역서가 저장되었거나, 견적정보가 입력·저장되었으면 깜박임 즉시 중지!
  if (isTaskReportSaved || isQuoteInfoSaved) return false;

  // 상호명이 기본 가짜 데이터이거나 비어있으면 미갱신으로 간주
  return isDummyValue(name, DUMMY_VALUES.names);
}

// 깜박임(Blink Warning) 상태 통합 갱신 함수
function updateBlinkWarnings() {
  // 1. 메인창 "거래처" 표시 · "설정관리" 버튼 깜박임 처리
  const qiDummy = isQuoteInfoDummy(quoteInfo);
  const btnQi = $("#btnCurrentCustomer");
  const txtQi = $("#quoteInfoBtnText");
  const bannerQi = $("#qiWarningNotice");

  if (btnQi) btnQi.classList.toggle("blink-warning", qiDummy);
  if (txtQi) {
    txtQi.classList.toggle("blink-warning-text", qiDummy);
    txtQi.textContent = `거래처: ${(quoteInfo?.customer_name || "").trim() || "미지정"}`;
  }
  if (bannerQi) bannerQi.classList.toggle("hidden", !qiDummy);
  $("#btnSettings")?.classList.toggle("blink-warning", qiDummy);
  $("#settingsBtnText")?.classList.toggle("blink-warning-text", qiDummy);

  // 2. 작업내역서 업체정보 영역 깜박임 처리
  const trName = $("#trCompanyName")?.value || quoteInfo?.supplier_name;
  const trAddr = $("#trCompanyAddress")?.value || quoteInfo?.supplier_address;
  const trPhone = $("#trCompanyPhone")?.value || quoteInfo?.supplier_phone;
  const trDummy = isTaskReportCompanyDummy(trName, trAddr, trPhone);

  const brandBox = $("#trBrandInfoBox");
  const bannerTr = $("#trCompanyWarningNotice");

  if (brandBox) brandBox.classList.toggle("blink-warning", trDummy);
  if (bannerTr) bannerTr.classList.toggle("hidden", !trDummy);

  // 작업내역서 모달 내부 입력칸 각각의 깜박임 효과 (저장되면 모두 해제)
  const inpName = $("#trCompanyName");
  const inpAddr = $("#trCompanyAddress");
  const inpPhone = $("#trCompanyPhone");
  if (inpName) inpName.classList.toggle("blink-warning-input", trDummy && isDummyValue(trName, DUMMY_VALUES.names));
  if (inpAddr) inpAddr.classList.toggle("blink-warning-input", trDummy && isDummyValue(trAddr, DUMMY_VALUES.addresses));
  if (inpPhone) inpPhone.classList.toggle("blink-warning-input", trDummy && isDummyValue(trPhone, DUMMY_VALUES.phones));
}

// 견적정보 -> 작업내역서 업체정보 동기화
function syncQuoteInfoToTaskReport(force = false) {
  if (!quoteInfo) return;
  // 신규 작성 중이거나 강제 동기화(저장/초기화 시)인 경우 업체정보 반영
  if (!currentTrId || force) {
    if ($("#trCompanyName")) $("#trCompanyName").value = quoteInfo.supplier_name || "대한민국주식회사";
    if ($("#trCompanyAddress")) $("#trCompanyAddress").value = quoteInfo.supplier_address || "서울시";
    if ($("#trCompanyPhone")) $("#trCompanyPhone").value = quoteInfo.supplier_phone || "010-1234-5678";
  }
  updateBlinkWarnings();
}

function qiSetStatus(msg, ok = true) {
  const el = $("#quoteInfoStatus");
  if (!el) return;
  el.textContent = msg;
  el.style.color = ok ? "var(--accent, #4caf50)" : "#e53935";
  if (msg) setTimeout(() => { el.textContent = ""; }, 3000);
}

function qiFillForm(info) {
  for (const [key, sel] of Object.entries(qiFields)) {
    const input = $(sel);
    if (input) input.value = info?.[key] ?? "";
  }
  updateBlinkWarnings();
}

function qiReloadQuotesIfNeeded() {
  if (!hasLoadedOnce) return;
  if (currentCat === "game-pc" && lastGameQuery) {
    submitGamePcRecommend();
  } else if (currentCat === "price" && lastPriceQuery) {
    submitPriceSearch({ keepView: true });
  } else {
    fetchQuotes(currentCat);
  }
}

async function loadQuoteInfo() {
  try {
    const res = await fetch("/api/quote-info");
    if (res.ok) {
      quoteInfo = await res.json();
      // 기존에 저장된 데이터가 기본 더미 데이터가 아니라면 저장된 상태로 간주
      if (
        quoteInfo &&
        !isDummyValue(quoteInfo.supplier_name, DUMMY_VALUES.names) &&
        !isDummyValue(quoteInfo.customer_name, DUMMY_VALUES.names)
      ) {
        isQuoteInfoSaved = true;
      }
      syncQuoteInfoToTaskReport();
      updateBlinkWarnings();
    }
  } catch (e) {
    console.warn("quote-info 로드 실패:", e);
  }
}

function qiRerender() {
  if (quoteData && quoteData.quotes) {
    renderDetail(quoteData.quotes);
  }
}

$("#btnCurrentCustomer")?.addEventListener("click", () => openSettings("info"));
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  // 메일 발송 창 위의 앱 비밀번호 안내만 닫는다(아래 발송 창의 입력은 그대로)
  const pwHelp = $("#appPwHelpModal");
  if (pwHelp && !pwHelp.classList.contains("hidden")) {
    hide(pwHelp);
    return;
  }
  // 새 견적 작성 창: 위에 메일 발송 창이 떠 있으면 그대로 두고, 아니면 이 창만 닫는다(아래 설정관리는 그대로)
  const mqOpen = mqModal && !mqModal.classList.contains("hidden");
  if (mqOpen) {
    if (mailModal.classList.contains("hidden")) closeMqEditor();
    return;
  }
  if (ctoModal && !ctoModal.classList.contains("hidden")) closeCtoModal();
  if (trModal && !trModal.classList.contains("hidden")) closeTrModal();
  else if (settingsModal && !settingsModal.classList.contains("hidden")) closeSettings();
  if (netModal && !netModal.classList.contains("hidden")) hide(netModal);
  if (danawaModal && !danawaModal.classList.contains("hidden")) hide(danawaModal);
  const galleryModal = $("#formGalleryModal");
  if (galleryModal && !galleryModal.classList.contains("hidden")) closeFormGallery();
});

$("#quoteInfoSave")?.addEventListener("click", async () => {
  const payload = {};
  for (const [key, sel] of Object.entries(qiFields)) {
    payload[key] = $(sel)?.value.trim() || "";
  }
  try {
    const res = await fetch("/api/quote-info", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) throw new Error(`저장 실패 (${res.status})`);
    quoteInfo = await res.json();
    isQuoteInfoSaved = true;
    syncQuoteInfoToTaskReport(true);
    updateBlinkWarnings();
    const registered = await custUpsertFromForm({ quiet: true });
    qiSetStatus(`✔ 저장되었습니다. 모든 견적서 및 작업내역서에 적용됩니다.${registered ? " (거래처 목록에도 등록)" : ""}`);
    qiRerender();
    qiReloadQuotesIfNeeded();
  } catch (e) {
    qiSetStatus(e.message, false);
  }
});

$("#quoteInfoReset")?.addEventListener("click", async () => {
  if (!confirm("저장된 견적처/공급자 정보를 초기화할까요?")) return;
  try {
    const res = await fetch("/api/quote-info", { method: "DELETE" });
    if (!res.ok) throw new Error("초기화 실패");
    quoteInfo = await res.json();
    isQuoteInfoSaved = false;
    qiFillForm(quoteInfo);
    syncQuoteInfoToTaskReport(true);
    updateBlinkWarnings();
    qiSetStatus("✔ 초기화되었습니다.");
    qiRerender();
    qiReloadQuotesIfNeeded();
  } catch (e) {
    qiSetStatus(e.message, false);
  }
});



// ---------- 거래처 목록 (customers.json) — 설정관리 → 견적정보 · 거래처 탭 ----------
// 행을 누르면 견적처 칸에 불러오고(저장 전), [✔ 적용]은 불러온 뒤 바로 견적정보로 저장한다.
// 상호명이 같으면 같은 거래처로 보고 갱신한다(이름을 바꿔 저장하면 새 거래처로 등록, 옛 항목은 그대로).
let custList = [];
let custSelectedName = "";
const CUST_FORM = {
  name: "#qiCustomerName",
  manager: "#qiCustomerManager",
  address: "#qiCustomerAddress",
  phone: "#qiCustomerPhone",
  email: "#qiCustomerEmail",
};

async function loadCustomers() {
  try {
    const res = await fetch("/api/customers", { cache: "no-store" });
    const data = await res.json();
    custList = data.customers || [];
  } catch (_) {
    custList = [];
  }
  renderCustomers();
}

function renderCustomers() {
  const tbody = $("#custTbody");
  if (!tbody) return;
  const q = ($("#custSearch")?.value || "").trim().toLowerCase();
  const rows = custList.filter((c) => !q || ["name", "manager", "phone", "address", "email"].some((k) => String(c[k] || "").toLowerCase().includes(q)));
  const count = $("#custCount");
  if (count) count.textContent = q ? `${rows.length} / ${custList.length}건` : `${custList.length}건`;
  if (!rows.length) {
    tbody.innerHTML = `<tr><td colspan="4" style="text-align:center;color:var(--muted);padding:12px;">${custList.length ? "검색 결과가 없습니다." : "등록된 거래처가 없습니다. 견적처를 입력하고 💾 저장하면 등록됩니다."}</td></tr>`;
    return;
  }
  const current = (custSelectedName || quoteInfo?.customer_name || "").trim();
  tbody.innerHTML = rows.map((c) => `
    <tr class="cust-row${c.name === current ? " is-selected" : ""}" data-id="${escapeHtml(c.id)}" title="${escapeHtml([c.address, c.email].filter(Boolean).join(" · "))}">
      <td><strong>${escapeHtml(c.name || "")}</strong></td>
      <td>${escapeHtml(c.manager || "")}</td>
      <td>${escapeHtml(c.phone || "")}</td>
      <td>
        <div class="qa-actions">
          <button type="button" class="btn btn-sm btn-primary cust-apply" data-id="${escapeHtml(c.id)}" title="불러온 뒤 바로 저장해 견적서에 적용합니다">✔ 적용</button>
          <button type="button" class="btn btn-sm btn-outline cust-del" data-id="${escapeHtml(c.id)}" title="거래처 목록에서 삭제">✕</button>
        </div>
      </td>
    </tr>`).join("");
}

function custFillForm(c) {
  if (!c) return;
  for (const [key, sel] of Object.entries(CUST_FORM)) {
    const input = $(sel);
    if (input) input.value = c[key] || "";
  }
  custSelectedName = c.name || "";
  $("#qiCustomerName")?.dispatchEvent(new Event("input"));
  renderCustomers();
}

/** 견적처 칸의 내용을 거래처 목록에 저장(상호명 기준 생성/갱신). 저장했으면 true. */
async function custUpsertFromForm({ quiet = false } = {}) {
  const data = {};
  for (const [key, sel] of Object.entries(CUST_FORM)) data[key] = $(sel)?.value.trim() || "";
  if (isDummyValue(data.name, DUMMY_VALUES.names)) {
    if (!quiet) qiSetStatus("거래처 상호명을 실제 이름으로 입력해 주세요.", false);
    return false;
  }
  try {
    const res = await fetch("/api/customers", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
    if (!res.ok) throw new Error("거래처 저장에 실패했습니다.");
    custSelectedName = data.name;
    await loadCustomers();
    if (!quiet) qiSetStatus(`📒 '${data.name}' 거래처 목록에 저장했습니다. (견적서에는 💾 저장을 눌러야 적용됩니다)`);
    return true;
  } catch (e) {
    if (!quiet) qiSetStatus(e.message, false);
    return false;
  }
}

async function custDelete(id) {
  const c = custList.find((x) => x.id === id);
  if (!confirm(`거래처 '${c?.name || id}'을(를) 목록에서 삭제할까요?\n(현재 견적정보와 보관된 견적서는 그대로 남습니다)`)) return;
  try {
    const res = await fetch(`/api/customers/${encodeURIComponent(id)}`, { method: "DELETE" });
    if (!res.ok) throw new Error("삭제에 실패했습니다.");
  } catch (e) {
    alert(e.message);
  }
  loadCustomers();
}

$("#custSearch")?.addEventListener("input", renderCustomers);
$("#custBtnBackup")?.addEventListener("click", () => backupZipToFolder({
  url: "/api/customers/backup",
  btnSel: "#custBtnBackup",
  busyText: "거래처 목록 · 견적정보를 ZIP 으로 묶는 중…",
  doneLabel: (d) => `거래처 ${d.count}곳 + 견적정보`,
}));
$("#custBtnBackupOpen")?.addEventListener("click", () => openTrBackupDir());
$("#custBtnNew")?.addEventListener("click", () => {
  custFillForm({ name: "", manager: "", address: "", phone: "", email: "" });
  qiSetStatus("새 거래처를 입력한 뒤 💾 저장 또는 📒 거래처 목록에만 저장을 누르세요.");
  $("#qiCustomerName")?.focus();
});
$("#custBtnSaveOnly")?.addEventListener("click", () => custUpsertFromForm());
$("#custTbody")?.addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-id]");
  if (btn) {
    const id = btn.dataset.id;
    if (btn.classList.contains("cust-del")) custDelete(id);
    else if (btn.classList.contains("cust-apply")) {
      custFillForm(custList.find((x) => x.id === id));
      $("#quoteInfoSave")?.click();
    }
    return;
  }
  const row = e.target.closest("tr.cust-row");
  if (!row) return;
  const c = custList.find((x) => x.id === row.dataset.id);
  custFillForm(c);
  qiSetStatus(`'${c?.name || ""}' 불러옴 — 💾 저장을 누르면 견적서에 적용됩니다.`);
});

// ---------- ⚙️ 설정관리 (견적정보 · 거래처 / 서식폼 / 견적보관함 / 작업내역서) ----------
const settingsModal = $("#settingsModal");
let settingsTab = "info";

function switchSettingsTab(tab) {
  if (!settingsModal) return;
  settingsTab = tab;
  settingsModal.querySelectorAll(".settings-tab").forEach((btn) => {
    const on = btn.dataset.tab === tab;
    btn.classList.toggle("active", on);
    btn.classList.toggle("btn-primary", on);
    btn.classList.toggle("btn-outline", !on);
    btn.setAttribute("aria-selected", on ? "true" : "false");
  });
  settingsModal.querySelectorAll(".settings-panel").forEach((panel) => {
    panel.classList.toggle("hidden", panel.dataset.panel !== tab);
  });
  if (tab === "info") loadCustomers();
  else if (tab === "archive") {
    refreshQuoteHub();
    loadTrBackupDir();
  } else if (tab === "taskreport") {
    loadTrReportsList();
    loadTrBackupDir();
  }
}

/** 설정관리 창을 연다. 견적정보 칸은 열 때마다 저장된 값으로 다시 채운다(저장하지 않은 수정은 버림). */
function openSettings(tab = settingsTab) {
  if (!settingsModal) return;
  custSelectedName = "";
  qiFillForm(quoteInfo);
  show(settingsModal);
  switchSettingsTab(tab);
  updateBlinkWarnings();
}

function closeSettings() {
  hide(settingsModal);
}

$("#btnSettings")?.addEventListener("click", () => openSettings());
$("#settingsClose")?.addEventListener("click", closeSettings);
settingsModal?.addEventListener("click", (e) => {
  if (e.target === settingsModal) closeSettings();
});
settingsModal?.querySelectorAll(".settings-tab").forEach((btn) => {
  btn.addEventListener("click", () => switchSettingsTab(btn.dataset.tab));
});


// ---------- 네트워크 접속 안내 모달 ----------
let netModal = null;

function initNetworkModal() {
  netModal = $("#networkModal");
  const btnNetShare = $("#btnNetworkShare");
  const netClose = $("#networkModalClose");
  const netOk = $("#networkModalOk");
  const btnCopyNet = $("#btnCopyNetUrl");
  const btnCopyLocal = $("#btnCopyLocalUrl");
  const netInput = $("#netIpInput");
  const localInput = $("#localIpInput");

  function openNetModal() {
    if (netModal) show(netModal);
  }
  function closeNetModal() {
    if (netModal) hide(netModal);
  }

  btnNetShare?.addEventListener("click", openNetModal);
  netClose?.addEventListener("click", closeNetModal);
  netOk?.addEventListener("click", closeNetModal);
  netModal?.addEventListener("click", (e) => {
    if (e.target === netModal) closeNetModal();
  });

  async function copyToClipboard(inputEl, btnEl) {
    if (!inputEl) return;
    const text = inputEl.value;
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(text);
      } else {
        inputEl.select();
        document.execCommand("copy");
      }
      if (btnEl) {
        const orig = btnEl.textContent;
        btnEl.textContent = "✔ 복사됨!";
        setTimeout(() => { btnEl.textContent = orig; }, 2000);
      }
    } catch {
      inputEl.select();
      document.execCommand("copy");
      if (btnEl) {
        const orig = btnEl.textContent;
        btnEl.textContent = "✔ 복사됨!";
        setTimeout(() => { btnEl.textContent = orig; }, 2000);
      }
    }
  }

  btnCopyNet?.addEventListener("click", () => copyToClipboard(netInput, btnCopyNet));
  btnCopyLocal?.addEventListener("click", () => copyToClipboard(localInput, btnCopyLocal));
}

// ---------- CTO 커스텀 모달 ----------
const ctoModal = $("#ctoModal");

async function openCtoModal(idx, tierKey) {
  if (!ctoModal) return;
  activeCtoIndex = idx;
  activeCtoTierKey = tierKey;

  const baseQ = quoteData?.quotes?.[idx];
  if (!baseQ) return;
  const original = baseQ._ctoOriginal || baseQ;
  baseQuoteForCto = JSON.parse(JSON.stringify(original));
  delete baseQuoteForCto._ctoOriginal;

  const infoEl = $("#ctoModelInfo");
  if (infoEl) {
    infoEl.innerHTML = `
      <h4 style="margin:0 0 4px 0; font-size:1.05rem; color:var(--text); font-weight:700;">${baseQ.model}</h4>
      <p style="margin:0; font-size:1rem; color:var(--muted);">${baseQ.description}</p>
    `;
  }

  showBusy("CTO 사양 옵션 불러오는 중…");
  try {
    ctoLive = null;
    // 이미 적용한 구성이 있으면 그 선택으로 다시 연다.
    const sel = baseQ._ctoSel ? `?sel=${encodeURIComponent(JSON.stringify(baseQ._ctoSel))}` : "";
    const res = await fetch(`/api/apple/cto/${encodeURIComponent(tierKey)}${sel}`);
    const data = await res.json();
    if (data.live) {
      renderLiveCto(data);
      show(ctoModal);
      return;
    }
    currentCtoOptions = data.options || [];
    if (!currentCtoOptions.length && Array.isArray(baseQ.cto_groups)) {
      currentCtoOptions = baseQ.cto_groups;
    }

    const body = $("#ctoFormContainer");
    if (!body) return;

    if (!currentCtoOptions.length) {
      body.innerHTML = '<p class="empty-state">선택 가능한 CTO 옵션이 없습니다.</p>';
      $("#ctoTotalPrice").textContent = fmt(baseQ.total);
      show(ctoModal);
      return;
    }

    body.innerHTML = currentCtoOptions
      .map((grp) => {
        const opts = grp.options || grp.choices || [];
        return `
      <div class="cto-group" style="margin-bottom: 20px;">
        <label style="display: block; font-weight: 700; margin-bottom: 8px; color: var(--accent);">${grp.name}</label>
        <select id="cto_select_${grp.id}" class="cto-select" data-group-id="${grp.id}">
          ${opts
            .map((opt, optIdx) => {
              const delta = opt.delta !== undefined ? opt.delta : (opt.delta_price || 0);
              return `<option value="${optIdx}" data-price="${delta}" data-name="${opt.name_override || ""}">${opt.label}</option>`;
            })
            .join("")}
        </select>
      </div>`;
      })
      .join("");

    currentCtoOptions.forEach((grp) => {
      $(`#cto_select_${grp.id}`)?.addEventListener("change", updateCtoTotalPrice);
    });

    updateCtoTotalPrice();
    show(ctoModal);
  } catch (e) {
    alert("CTO 옵션 정보를 가져오는데 실패했습니다: " + e.message);
  } finally {
    hideBusy();
  }
}

/** 구성하기 API 응답으로 선택 상자를 그린다. 값을 바꾸면 전체 선택으로 다시 물어 정확한 총액을 받는다. */
function renderLiveCto(data) {
  ctoLive = data;
  currentCtoOptions = data.options || [];
  const body = $("#ctoFormContainer");
  if (!body) return;
  body.innerHTML = currentCtoOptions
    .map(
      (grp) => `
      <div class="cto-group" style="margin-bottom: 20px;">
        <label style="display: block; font-weight: 700; margin-bottom: 8px; color: var(--accent);">${grp.name}</label>
        <select id="cto_select_${grp.id}" class="cto-select" data-group-id="${grp.id}">
          ${grp.options
            .map((opt) => `<option value="${opt.value}"${opt.selected ? " selected" : ""}>${opt.label}</option>`)
            .join("")}
        </select>
      </div>`
    )
    .join("");
  if (data.notice) {
    // 고른 옵션을 이 모델에서 쓸 수 없어 이전 구성을 유지한 경우 안내
    body.insertAdjacentHTML(
      "afterbegin",
      `<p class="cto-notice" style="margin:0 0 10px;padding:6px 8px;border:1px solid rgba(251,191,36,.4);background:rgba(251,191,36,.1);color:#fbbf24;border-radius:4px;">⚠️ ${escapeHtml(data.notice)}</p>`
    );
  }
  currentCtoOptions.forEach((grp) => {
    $(`#cto_select_${grp.id}`)?.addEventListener("change", () => refreshLiveCto(null, grp.id));
  });
  showLiveCtoTotal();
}

function showLiveCtoTotal() {
  if (!ctoLive || !baseQuoteForCto) return;
  // 카드 합계에 초기 세팅비 등 Apple 본체 외 금액이 있으면 그대로 더한다.
  const extra = Math.max(0, (baseQuoteForCto.total || 0) - (ctoLive.base_total || 0));
  $("#ctoTotalPrice").textContent = fmt((ctoLive.total || 0) + extra);
}

async function refreshLiveCto(selections, changed = "") {
  if (!ctoLive) return;
  const sel = selections || {};
  if (!selections) {
    currentCtoOptions.forEach((grp) => {
      const el = $(`#cto_select_${grp.id}`);
      if (el) sel[grp.id] = el.value;
    });
  }
  $("#ctoTotalPrice").textContent = "계산 중…";
  $$(".cto-select").forEach((el) => (el.disabled = true));
  try {
    const res = await fetch(
      `/api/apple/cto/${encodeURIComponent(activeCtoTierKey)}?sel=${encodeURIComponent(JSON.stringify(sel))}&changed=${encodeURIComponent(changed)}`
    );
    const data = await res.json();
    if (!data.live) throw new Error("구성 가격을 받지 못했습니다.");
    renderLiveCto(data);
  } catch (e) {
    alert("CTO 가격 계산 실패: " + e.message);
    renderLiveCto(ctoLive);
  }
}

function updateCtoTotalPrice() {
  if (ctoLive) return showLiveCtoTotal();
  if (!baseQuoteForCto) return;
  let totalDelta = 0;

  currentCtoOptions.forEach((grp) => {
    const sel = $(`#cto_select_${grp.id}`);
    if (sel) {
      const selectedOption = sel.options[sel.selectedIndex];
      if (selectedOption) {
        const delta = parseInt(selectedOption.dataset.price, 10) || 0;
        totalDelta += delta;
      }
    }
  });

  const finalTotal = baseQuoteForCto.total + totalDelta;
  $("#ctoTotalPrice").textContent = fmt(finalTotal);
}

function applyCtoChanges() {
  if (activeCtoIndex === null || !quoteData?.quotes?.[activeCtoIndex]) return;

  const targetQ = quoteData.quotes[activeCtoIndex];
  let totalDelta = 0;
  const addedParts = [];

  if (ctoLive) {
    // 구성하기 API 가 계산한 총액과 추가 품목 행을 그대로 쓴다.
    totalDelta = (ctoLive.total || 0) - (ctoLive.base_total || 0);
    (ctoLive.parts || []).forEach((p) =>
      addedParts.push({ category: p.category, name: p.name, qty: 1, unit_price: p.amount, amount: p.amount })
    );
    targetQ._ctoSel = addedParts.length ? { ...ctoLive.selected } : null;
  }

  if (!ctoLive) currentCtoOptions.forEach((grp) => {
    const sel = $(`#cto_select_${grp.id}`);
    if (sel) {
      const opts = grp.options || grp.choices || [];
      const selectedIdx = parseInt(sel.value, 10);
      const choice = opts[selectedIdx];
      if (choice) {
        const delta = choice.delta !== undefined ? choice.delta : (choice.delta_price || 0);
        if (delta) {
          totalDelta += delta;
          addedParts.push({
            category: grp.name,
            name: choice.name_override || `[CTO 업그레이드] ${choice.label}`,
            qty: 1,
            unit_price: delta,
            amount: delta,
          });
        }
      }
    }
  });

  if (!targetQ._ctoOriginal) {
    targetQ._ctoOriginal = JSON.parse(JSON.stringify(baseQuoteForCto));
  }
  // 화면·Excel 이 같은 규칙으로 합치도록 원래 추가 품목도 따로 기억한다.
  targetQ._ctoAdded = addedParts.map((p) => ({ category: p.category, name: p.name, amount: p.amount }));
  targetQ.parts = mergeCtoParts(baseQuoteForCto.parts, targetQ._ctoAdded);
  targetQ.total = baseQuoteForCto.total + totalDelta;
  targetQ.price_source =
    totalDelta || addedParts.length ? "apple_cto" : (baseQuoteForCto.price_source || "apple_official");

  closeCtoModal();
  renderTabs(quoteData.quotes);
  renderSummaryCards(quoteData.quotes);
  renderDetail(quoteData.quotes);
}

// CTO 항목 → 같은 사양을 나타내는 기본 사양 행 (서버 main.py CTO_BASE_ROW 와 같게 유지)
const CTO_BASE_ROW = { "통합 메모리": "메모리", "저장장치": "SSD", "프로세서": "CPU/SoC" };

/** 기본 사양 행이 있는 CTO 항목은 그 행을 업그레이드 사양으로 바꾸고 금액을 더한다. 나머지는 아래에 붙인다. */
function mergeCtoParts(baseParts, added) {
  const parts = JSON.parse(JSON.stringify(baseParts || []));
  (added || []).forEach((a) => {
    const row = parts.find((p) => p.category === CTO_BASE_ROW[a.category]);
    if (row) {
      row.name = a.name;
      row.amount = (row.amount || 0) + (a.amount || 0);
      row.unit_price = row.amount;
    } else {
      parts.push({ category: a.category, name: a.name, qty: 1, unit_price: a.amount, amount: a.amount });
    }
  });
  parts.forEach((p, i) => (p.no = i + 1));
  return parts;
}

/** 적용된 CTO(추가 부품·추가 금액)를 tier_key 별로 기억해 둔다. 옵션 토글로 다시 불러올 때 쓴다. */
function snapshotCto(quotes) {
  const carry = {};
  (quotes || []).forEach((q) => {
    const orig = q._ctoOriginal;
    if (!orig || !q.tier_key) return;
    const delta = (q.total || 0) - (orig.total || 0);
    const added = q._ctoAdded || [];
    if (!delta && !added.length) return;
    carry[q.tier_key] = {
      delta,
      parts: JSON.parse(JSON.stringify(added)),
      priceSource: q.price_source,
      sel: q._ctoSel || null,
    };
  });
  return carry;
}

/** 새로 불러온 견적에 기억해 둔 CTO 를 다시 얹는다. */
function reapplyCto(quotes, carry) {
  (quotes || []).forEach((q) => {
    const c = carry[q.tier_key];
    if (!c) return;
    q._ctoOriginal = JSON.parse(JSON.stringify(q));
    q._ctoAdded = c.parts;
    q.parts = mergeCtoParts(q.parts, c.parts);
    q.total = (q.total || 0) + c.delta;
    q.price_source = c.priceSource || "apple_cto";
    if (c.sel) q._ctoSel = c.sel;
  });
}

function closeCtoModal() {
  if (!ctoModal) return;
  hide(ctoModal);
}

$("#ctoClose")?.addEventListener("click", closeCtoModal);
$("#ctoCancelBtn")?.addEventListener("click", closeCtoModal);
$("#ctoResetBtn")?.addEventListener("click", () => {
  if (ctoLive) return refreshLiveCto({}); // 빈 선택 = 공식몰 기본 구성
  currentCtoOptions.forEach((grp) => {
    const sel = $(`#cto_select_${grp.id}`);
    if (sel) sel.value = "0";
  });
  updateCtoTotalPrice();
});
$("#ctoApplyBtn")?.addEventListener("click", applyCtoChanges);
ctoModal?.addEventListener("click", (e) => {
  if (e.target === ctoModal) closeCtoModal();
});

// Store switcher handler
function switchStore(store) {
  currentStore = store;
  const btnCompuzone = $("#storeBtnCompuzone");
  const btnApple = $("#storeBtnApple");
  const groupCompuzone = $("#compuzoneCatGroup");
  const groupApple = $("#appleCatGroup");
  hidePriceResultChrome();
  updateEmptyStateCopy();
  // 자동으로 여는 첫 카테고리 버튼을 선택 상태로 표시한다.
  const firstCat = store === "compuzone" ? "ai" : "apple_macbook";
  $$(".cat-btn, .game-cat-btn, .price-cat-btn, .apple-cat-btn").forEach((b) =>
    b.classList.toggle("active", b.dataset.cat === firstCat)
  );
  if (store === "compuzone") {
    btnCompuzone?.classList.add("active");
    btnApple?.classList.remove("active");
    show(groupCompuzone);
    hide(groupApple);
    updateExtrasUI();
    fetchQuotes(firstCat);
  } else {
    btnApple?.classList.add("active");
    btnCompuzone?.classList.remove("active");
    show(groupApple);
    hide(groupCompuzone);
    updateExtrasUI();
    fetchQuotes(firstCat);
  }
}

$("#storeBtnCompuzone")?.addEventListener("click", () => switchStore("compuzone"));
$("#storeBtnApple")?.addEventListener("click", () => switchStore("apple"));

$$(".cat-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    const cat = btn.dataset.cat;
    if (!cat || cat === "game-pc" || cat === "price") return;
    $$(".cat-btn").forEach((b) => b.classList.remove("active"));
    $$(".game-cat-btn").forEach((b) => b.classList.remove("active"));
    $$(".price-cat-btn").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    fetchQuotes(cat);
  });
});

$("#btnGamePc")?.addEventListener("click", () => {
  $$(".cat-btn").forEach((b) => b.classList.remove("active"));
  $$(".game-cat-btn").forEach((b) => b.classList.add("active"));
  $$(".price-cat-btn").forEach((b) => b.classList.remove("active"));
  openGamePcModal();
});

$("#btnPriceSearch")?.addEventListener("click", () => {
  $$(".cat-btn").forEach((b) => b.classList.remove("active"));
  $$(".game-cat-btn").forEach((b) => b.classList.remove("active"));
  $$(".price-cat-btn").forEach((b) => b.classList.remove("active"));
  $("#btnPriceSearch")?.classList.add("active");
  activatePriceSearch("assembled");
});

$("#btnNotebookPriceSearch")?.addEventListener("click", () => {
  $$(".cat-btn").forEach((b) => b.classList.remove("active"));
  $$(".game-cat-btn").forEach((b) => b.classList.remove("active"));
  $$(".price-cat-btn").forEach((b) => b.classList.remove("active"));
  $("#btnNotebookPriceSearch")?.classList.add("active");
  activatePriceSearch("notebook");
});

$$(".apple-cat-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    $$(".apple-cat-btn").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    fetchQuotes(btn.dataset.cat);
  });
});

const btnAppleRefresh = $("#btnAppleRefresh");
if (btnAppleRefresh) {
  btnAppleRefresh.addEventListener("click", () => {
    const targetCat = currentCat && currentCat.startsWith("apple_") ? currentCat : "apple_all";
    fetchQuotes(targetCat, true);
  });
}

function extrasQuery() {
  return `monitor=${includeMonitor ? 1 : 0}&keyboard=${includeKeyboard ? 1 : 0}&ram32=${includeRam32 ? 1 : 0}&setup=${includeSetup ? 1 : 0}`;
}

function hideResultChrome() {
  hide($("#resultViewBar"));
  hide($("#resultListWrap"));
}

function hidePriceSearchBar() {
  hide($("#priceSearchBar"));
}

function hidePriceResultChrome() {
  hidePriceSearchBar();
  hideResultChrome();
}

/** 서버가 보낸 Apple 공식몰 수집 경고(개정 의심·접속 실패·새 제품군)를 결과 위에 보여 준다. */
function renderCatalogWarnings(warnings) {
  const box = $("#catalogWarn");
  const list = $("#catalogWarnList");
  if (!box || !list) return;
  const items = Array.isArray(warnings) ? warnings : [];
  list.innerHTML = items.map((w) => `<li>${escapeHtml(w)}</li>`).join("");
  if (items.length) show(box);
  else hide(box);
}
$("#catalogWarnClose")?.addEventListener("click", () => hide($("#catalogWarn")));

function applyQuotePayload(data) {
  renderCatalogWarnings(data?.warnings);
  quoteData = data;
  hasLoadedOnce = true;
  activeTab = 0;
  renderTabs(quoteData.quotes);
  renderDetail(quoteData.quotes);
  hide($("#loading"));
  applyResultView();
  updateExtrasUI();
}

async function fetchQuotes(cat = "ai", forceRefresh = false, opts = {}) {
  if (cat === "game-pc" || cat === "game") {
    openGamePcModal();
    return;
  }
  if (cat === "price" || cat === "price-search") {
    activatePriceSearch(priceTargetType);
    return;
  }

  currentCat = cat;
  updateExtrasUI();
  hidePriceSearchBar();
  hideResultChrome();
  const loading = $("#loading");
  const errorBox = $("#error");
  const emptyState = $("#emptyState");
  const summaryCards = $("#summaryCards");
  const quoteTabs = $("#quoteTabs");

  show(loading);
  setLoadingMessage(loadingMessageFor(cat));
  hide(errorBox);
  hide(emptyState);
  hide(summaryCards);
  hide(quoteTabs);

  try {
    let endpoint = `/api/category/${cat}`;
    if (cat === "ai") {
      endpoint = `/api/today`;
    } else if (cat.startsWith("apple_") || cat === "apple") {
      const appleCat = cat.replace("apple_", "");
      endpoint = `/api/apple/${appleCat}`;
    }

    let qStr = extrasQuery();
    if (forceRefresh) {
      qStr += (qStr ? "&" : "") + "refresh=1";
    }

    const res = await fetch(`${endpoint}?${qStr}`);
    if (!res.ok) throw new Error(`HTTP error ${res.status}`);
    quoteData = await res.json();

    // 구버전/우회 호출로 game-pc category 응답이 온 경우 위자드 실행
    if (quoteData.needs_game_wizard) {
      hide(loading);
      openGamePcModal();
      return;
    }
    if (quoteData.needs_price_wizard) {
      hide(loading);
      activatePriceSearch(priceTargetType);
      return;
    }

    if (opts.ctoCarry) reapplyCto(quoteData.quotes, opts.ctoCarry);
    applyQuotePayload(quoteData);
  } catch (e) {
    hide(loading);
    hide(summaryCards);
    hide(quoteTabs);
    hideResultChrome();
    show(emptyState);
    if (errorBox) {
      errorBox.textContent = `견적 수집 실패: ${e.message}. 다시 시도해주세요.`;
      show(errorBox);
    }
  }
}

// ---------- 게임으로 PC 찾기 ----------
const gamePcModal = $("#gamePcModal");

function gameSetStatus(msg, ok = true) {
  const el = $("#gamePcStatus");
  if (!el) return;
  el.textContent = msg || "";
  el.style.color = ok ? "var(--accent, #4caf50)" : "#e53935";
}

function renderGameSelectedChips() {
  const box = $("#gameSelectedChips");
  if (!box) return;
  if (!selectedGames.length) {
    box.innerHTML = '<span class="game-chip-empty">선택된 게임 없음</span>';
    return;
  }
  box.innerHTML = selectedGames
    .map(
      (g) => `
    <span class="game-chip">
      ${g.title}
      <button type="button" class="game-chip-x" data-id="${g.id}" aria-label="제거">×</button>
    </span>`
    )
    .join("");
  box.querySelectorAll(".game-chip-x").forEach((btn) => {
    btn.addEventListener("click", () => toggleGame(btn.dataset.id));
  });
}

function renderGameGrid() {
  const grid = $("#gameGrid");
  if (!grid || !gamePcMeta) return;
  grid.innerHTML = gamePcMeta.games
    .map((g) => {
      const on = selectedGames.some((s) => s.id === g.id);
      return `
      <button type="button" class="game-card ${on ? "on" : ""}" data-id="${g.id}" title="${g.title}">
        <img src="${g.image}" alt="${g.title}" loading="lazy" />
        <span>${g.title}</span>
      </button>`;
    })
    .join("");
  grid.querySelectorAll(".game-card").forEach((btn) => {
    btn.addEventListener("click", () => toggleGame(btn.dataset.id));
  });
}

function renderResolutionGrid() {
  const grid = $("#resolutionGrid");
  if (!grid || !gamePcMeta) return;
  grid.innerHTML = gamePcMeta.resolutions
    .map((r) => {
      const on = selectedResolution === r.id;
      return `
      <button type="button" class="resolution-card ${on ? "on" : ""}" data-id="${r.id}">
        <strong>${r.id}</strong>
        <span>${r.label}</span>
      </button>`;
    })
    .join("");
  grid.querySelectorAll(".resolution-card").forEach((btn) => {
    btn.addEventListener("click", () => {
      selectedResolution = btn.dataset.id;
      renderResolutionGrid();
      gameSetStatus("");
    });
  });
}

function toggleGame(id) {
  const meta = gamePcMeta?.games?.find((g) => g.id === id);
  if (!meta) return;
  const idx = selectedGames.findIndex((g) => g.id === id);
  if (idx >= 0) {
    selectedGames.splice(idx, 1);
  } else {
    if (selectedGames.length >= (gamePcMeta.max_games || 4)) {
      gameSetStatus("게임은 최대 4개까지 선택 가능합니다.", false);
      return;
    }
    selectedGames.push({ id: meta.id, title: meta.title });
  }
  gameSetStatus("");
  renderGameGrid();
  renderGameSelectedChips();
}

function setGameWizardStep(step) {
  gameWizardStep = step;
  $$(".game-step-tab").forEach((tab) => {
    tab.classList.toggle("active", tab.dataset.step === String(step));
  });
  const step1 = $("#gameStep1");
  const step2 = $("#gameStep2");
  const prev = $("#gamePcPrev");
  const next = $("#gamePcNext");
  const submit = $("#gamePcSubmit");
  if (step === 1) {
    show(step1);
    hide(step2);
    hide(prev);
    show(next);
    hide(submit);
  } else {
    hide(step1);
    show(step2);
    show(prev);
    hide(next);
    show(submit);
  }
}

async function ensureGamePcMeta() {
  if (gamePcMeta) return gamePcMeta;
  gameSetStatus("게임 목록을 불러오는 중…");
  const res = await fetch("/api/game-pc/meta");
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `게임 목록 로드 실패 (${res.status})`);
  }
  gamePcMeta = await res.json();
  gameSetStatus("");
  return gamePcMeta;
}

async function openGamePcModal() {
  if (!gamePcModal) return;
  try {
    await ensureGamePcMeta();
    renderGameGrid();
    renderGameSelectedChips();
    renderResolutionGrid();
    setGameWizardStep(selectedGames.length && selectedResolution ? 2 : 1);
    show(gamePcModal);
  } catch (e) {
    alert(e.message);
  }
}

function closeGamePcModal() {
  hide(gamePcModal);
}

async function submitGamePcRecommend() {
  if (!selectedGames.length) {
    gameSetStatus("게임을 1개 이상 선택해주세요.", false);
    setGameWizardStep(1);
    return;
  }
  if (!selectedResolution) {
    gameSetStatus("해상도를 선택해주세요.", false);
    setGameWizardStep(2);
    return;
  }

  closeGamePcModal();
  currentCat = "game-pc";
  updateExtrasUI();
  lastGameQuery = {
    games: selectedGames.map((g) => g.id),
    titles: selectedGames.map((g) => g.title),
    resolution: selectedResolution,
  };

  const loading = $("#loading");
  const errorBox = $("#error");
  const emptyState = $("#emptyState");
  const summaryCards = $("#summaryCards");
  const quoteTabs = $("#quoteTabs");
  const loadingText = loading?.querySelector("p");

  show(loading);
  if (loadingText) loadingText.textContent = "게임에 맞는 PC 견적을 수집 중…";
  hide(errorBox);
  hide(emptyState);
  hide(summaryCards);
  hide(quoteTabs);
  hidePriceSearchBar();
  hideResultChrome();

  try {
    const res = await fetch("/api/game-pc/recommend", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        games: lastGameQuery.games,
        titles: lastGameQuery.titles,
        resolution: lastGameQuery.resolution,
        monitor: includeMonitor ? 1 : 0,
        keyboard: includeKeyboard ? 1 : 0,
        setup: includeSetup ? 1 : 0,
      }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `HTTP error ${res.status}`);
    applyQuotePayload(data);
  } catch (e) {
    hide(loading);
    hide(summaryCards);
    hide(quoteTabs);
    show(emptyState);
    if (errorBox) {
      errorBox.textContent = `게임PC 추천 실패: ${e.message}`;
      show(errorBox);
    }
  } finally {
    setLoadingMessage(LOADING_TEXT_COMPUZONE);
  }
}

$("#gamePcClose")?.addEventListener("click", closeGamePcModal);
$("#gamePcCancel")?.addEventListener("click", closeGamePcModal);
gamePcModal?.addEventListener("click", (e) => {
  if (e.target === gamePcModal) closeGamePcModal();
});
$("#gamePcPrev")?.addEventListener("click", () => setGameWizardStep(1));
$("#gamePcNext")?.addEventListener("click", () => {
  if (!selectedGames.length) {
    gameSetStatus("게임을 1개 이상 선택해주세요.", false);
    return;
  }
  gameSetStatus("");
  setGameWizardStep(2);
});
$("#gamePcSubmit")?.addEventListener("click", submitGamePcRecommend);
$$(".game-step-tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    const step = parseInt(tab.dataset.step, 10);
    if (step === 2 && !selectedGames.length) {
      gameSetStatus("게임을 1개 이상 선택해주세요.", false);
      return;
    }
    setGameWizardStep(step);
  });
});

// ---------- 금액대로 찾기 · 카드/리스트 보기 ----------
function parseManwonInput(raw) {
  const n = Number(String(raw || "").replace(/[^\d]/g, ""));
  return Number.isFinite(n) ? n : 0;
}

function manwonToWon(man) {
  return parseManwonInput(man) * 10000;
}

function wonToManwon(won) {
  return Math.round((Number(won) || 0) / 10000);
}

function updateManwonPreview() {
  [
    ["#priceSearchMin", "#priceSearchMinWon"],
    ["#priceSearchMax", "#priceSearchMaxWon"],
  ].forEach(([inputSel, previewSel]) => {
    const preview = $(previewSel);
    if (!preview) return;
    const man = parseManwonInput($(inputSel)?.value);
    preview.textContent = man ? `= ${(man * 10000).toLocaleString("ko-KR")}원` : "";
  });
}

function fillManwonInputs() {
  if (!lastPriceQuery) {
    updateManwonPreview();
    return;
  }
  const minEl = $("#priceSearchMin");
  const maxEl = $("#priceSearchMax");
  if (minEl) minEl.value = String(wonToManwon(lastPriceQuery.min_price) || "");
  if (maxEl) maxEl.value = String(wonToManwon(lastPriceQuery.max_price) || "");
  updateManwonPreview();
}

function priceSetStatus(msg, ok = true) {
  const el = $("#priceSearchStatus");
  if (!el) return;
  el.textContent = msg || "";
  el.style.color = ok ? "var(--accent, #4caf50)" : "#e53935";
}

function quotePartName(q, category) {
  const row = (q.parts || []).find((p) => p.category === category);
  return row ? row.name : "";
}

function isPriceSearchResult(data = quoteData) {
  return data?.event_title === "금액대로 찾기";
}

function priceSearchMetaText() {
  if (!lastPriceQuery) return "";
  const matched = Number(quoteData?.matched_count || quoteData?.quotes?.length || 0);
  const shown = quoteData?.quotes?.length || 0;
  const lo = wonToManwon(lastPriceQuery.min_price);
  const hi = wonToManwon(lastPriceQuery.max_price);
  if (matched > shown) {
    return `${lo}만 원 ~ ${hi}만 원 · 총 ${matched.toLocaleString("ko-KR")}건 중 금액 낮은 순 ${shown}건`;
  }
  return `${lo}만 원 ~ ${hi}만 원 · ${shown}건`;
}

function resultMetaText() {
  const shown = quoteData?.quotes?.length || 0;
  if (currentCat === "price") return priceSearchMetaText();
  if (currentCat === "game-pc" && lastGameQuery) {
    return `${lastGameQuery.titles.join(", ")} · ${lastGameQuery.resolution} · ${shown}건`;
  }
  const title = quoteData?.event_title || quoteData?.series || "";
  return title ? `${title} · ${shown}건` : `${shown}건`;
}

function renderResultListBody(quotes) {
  const tbody = $("#resultListBody");
  if (!tbody) return;
  tbody.innerHTML = quotes
    .map((q, i) => {
      const cpu = quotePartName(q, "CPU") || "-";
      const vga = quotePartName(q, "그래픽카드") || "-";
      return `
      <tr class="${i === activeTab ? "active" : ""}" data-index="${i}">
        <td class="col-rank">${escapeHtml(q.tab_label || q.tier || `${i + 1}`)}</td>
        <td class="col-model">${escapeHtml(q.model)}</td>
        <td class="col-spec">${escapeHtml(cpu)}</td>
        <td class="col-spec">${escapeHtml(vga)}</td>
        <td class="col-price">${fmt(displayTotal(q))}</td>
      </tr>`;
    })
    .join("");
  tbody.querySelectorAll("tr").forEach((row) => {
    row.addEventListener("click", () => {
      activeTab = parseInt(row.dataset.index, 10);
      if (!quoteData?.quotes) return;
      renderTabs(quoteData.quotes);
      renderDetail(quoteData.quotes);
      applyResultView();
    });
  });
}

function setResultView(mode) {
  resultView = mode === "list" ? "list" : "card";
  applyResultView();
}

function applyResultView() {
  const quotes = quoteData?.quotes;
  const bar = $("#resultViewBar");
  const meta = $("#resultViewMeta");
  const toggle = $("#btnResultViewToggle");
  const cards = $("#summaryCards");
  const list = $("#resultListWrap");

  if (!quotes?.length) {
    hide(bar);
    hide(list);
    hide(cards);
    return;
  }

  if (currentStore !== "compuzone") {
    hide(bar);
    hide(list);
    renderSummaryCards(quotes);
    show(cards);
    show($("#quoteTabs"));
    return;
  }

  const isList = resultView === "list";
  if (meta) meta.textContent = resultMetaText();
  if (toggle) toggle.textContent = isList ? "카드로보기" : "리스트로보기";
  show(bar);

  if (isList) {
    hide(cards);
    show(list);
    renderResultListBody(quotes);
  } else {
    show(cards);
    hide(list);
    renderSummaryCards(quotes);
  }
  show($("#quoteTabs"));
}

const PRICE_SCOPE_TEXT = {
  assembled: "추천조립 · 아이웍스 · 프리미엄 전체를 판매가 기준으로 검색합니다.",
  notebook: "컴퓨존 노트북 전체를 판매가 기준으로 검색합니다.",
};

function activatePriceSearch(targetType = "assembled") {
  priceTargetType = targetType;
  currentCat = "price";
  const scope = $("#priceSearchScope");
  if (scope) scope.textContent = PRICE_SCOPE_TEXT[targetType] || PRICE_SCOPE_TEXT.assembled;
  updateExtrasUI();

  hide($("#error"));
  hide($("#loading"));
  hide($("#emptyState"));
  show($("#priceSearchBar"));
  fillManwonInputs();
  $("#priceSearchMin")?.focus();

  if (isPriceSearchResult() && quoteData?.quotes?.length) {
    applyResultView();
    priceSetStatus("");
  } else {
    hide($("#summaryCards"));
    hide($("#quoteTabs"));
    hideResultChrome();
    priceSetStatus("");
  }
  $("#quote")?.scrollIntoView({ behavior: "smooth", block: "start" });
}

async function submitPriceSearch(opts = {}) {
  const keepView = Boolean(opts.keepView);
  const minMan = parseManwonInput($("#priceSearchMin")?.value);
  const maxMan = parseManwonInput($("#priceSearchMax")?.value);
  if (!minMan && !maxMan) {
    priceSetStatus("최저가와 최고가를 만원 단위로 입력해주세요. 예: 50 ~ 70", false);
    return;
  }
  if (!maxMan) {
    priceSetStatus("최고가를 만원 단위로 입력해주세요.", false);
    return;
  }
  if (maxMan < minMan) {
    priceSetStatus("최고가는 최저가보다 크거나 같아야 합니다.", false);
    return;
  }

  const minPrice = manwonToWon(minMan);
  const maxPrice = manwonToWon(maxMan);
  lastPriceQuery = { min_price: minPrice, max_price: maxPrice, type: priceTargetType };
  currentCat = "price";
  if (!keepView) resultView = "card";
  updateExtrasUI();
  $$(".cat-btn").forEach((b) => b.classList.remove("active"));
  $$(".game-cat-btn").forEach((b) => b.classList.remove("active"));
  $$(".price-cat-btn").forEach((b) => b.classList.toggle("active", b.dataset.type === priceTargetType));
  show($("#priceSearchBar"));
  updateManwonPreview();

  const loading = $("#loading");
  const errorBox = $("#error");
  const emptyState = $("#emptyState");
  const summaryCards = $("#summaryCards");
  const quoteTabs = $("#quoteTabs");
  const loadingText = loading?.querySelector("p");
  const submitBtn = $("#priceSearchSubmit");

  priceSetStatus(
    priceTargetType === "notebook"
      ? "노트북을 검색하는 중… 컴퓨존 노트북 목록을 확인합니다."
      : "조립PC를 검색하는 중… 추천·아이웍스·프리미엄 목록을 확인합니다."
  );
  if (submitBtn) submitBtn.disabled = true;
  show(loading);
  if (loadingText) {
    if (priceTargetType === "notebook") loadingText.textContent = "금액대에 맞는 노트북을 검색하는 중…";
    else loadingText.textContent = "금액대에 맞는 조립PC를 검색하는 중…";
  }
  hide(errorBox);
  hide(emptyState);
  hide(summaryCards);
  hide(quoteTabs);
  hideResultChrome();

  try {
    const res = await fetch("/api/price-search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        min_price: minPrice,
        max_price: maxPrice,
        target_type: priceTargetType,
        monitor: includeMonitor ? 1 : 0,
        keyboard: includeKeyboard ? 1 : 0,
        setup: includeSetup ? 1 : 0,
      }),
    });
    const data = await res.json().catch(() => ({}));
    const detail = Array.isArray(data.detail)
      ? data.detail.map((x) => x.msg || x).join(" ")
      : data.detail;
    if (!res.ok) throw new Error(detail || `HTTP error ${res.status}`);
    applyQuotePayload(data);
    const shown = data.quotes?.length || 0;
    const matched = Number(data.matched_count || shown);
    priceSetStatus(
      matched > shown
        ? `총 ${matched.toLocaleString("ko-KR")}건 중 상위 ${shown}건을 표시합니다.`
        : `${shown}건을 찾았습니다.`
    );
  } catch (e) {
    hide(loading);
    hide(summaryCards);
    hide(quoteTabs);
    hideResultChrome();
    show($("#priceSearchBar"));
    priceSetStatus(e.message, false);
    if (errorBox) {
      errorBox.textContent = `금액대 검색 실패: ${e.message}`;
      show(errorBox);
    }
  } finally {
    if (submitBtn) submitBtn.disabled = false;
    setLoadingMessage(LOADING_TEXT_COMPUZONE);
  }
}

$("#priceSearchSubmit")?.addEventListener("click", () => submitPriceSearch());
$("#btnResultViewToggle")?.addEventListener("click", () => {
  setResultView(resultView === "list" ? "card" : "list");
});
["#priceSearchMin", "#priceSearchMax"].forEach((sel) => {
  const el = $(sel);
  el?.addEventListener("input", updateManwonPreview);
  el?.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      updateManwonPreview();
      submitPriceSearch();
    }
  });
});

document.addEventListener("DOMContentLoaded", () => {
  // [종료] 후 새로고침된 페이지: 견적을 불러오지 않고 서버 종료 · 창 닫기만 한다
  if (new URLSearchParams(window.location.search).get("shutdown") === "1") {
    finishShutdown();
    return;
  }
  initNetworkModal();
  updateExtrasUI();
  loadQuoteInfo();
  loadTrReportsList();
  fetchQuotes("ai");
});

const logicSection = document.getElementById("logic");
const navLogic = document.getElementById("navLogic");
const navQuote = document.getElementById("navQuote");

function setLogicOpen(open) {
  if (open) {
    show(logicSection);
    logicSection.setAttribute("aria-hidden", "false");
    navLogic?.classList.add("is-active");
  } else {
    hide(logicSection);
    logicSection.setAttribute("aria-hidden", "true");
    navLogic?.classList.remove("is-active");
  }
}

navLogic?.addEventListener("click", (e) => {
  e.preventDefault();
  const willOpen = logicSection.classList.contains("hidden");
  setLogicOpen(willOpen);
  if (willOpen) {
    navQuote?.classList.remove("is-active");
    logicSection.scrollIntoView({ behavior: "smooth", block: "start" });
  }
});

navQuote?.addEventListener("click", (e) => {
  e.preventDefault();
  setLogicOpen(false);
  navQuote.classList.add("is-active");
  document.getElementById("quote").scrollIntoView({ behavior: "smooth", block: "start" });
});

if (window.location.hash === "#logic") {
  setLogicOpen(true);
}

// ---------- 앱 종료 (백엔드 · 포트 · 캐시 · 브라우저) ----------
async function clearClientCaches() {
  try {
    sessionStorage.clear();
  } catch (_) {}
  try {
    localStorage.clear();
  } catch (_) {}
  try {
    if (typeof caches !== "undefined") {
      const keys = await caches.keys();
      await Promise.all(keys.map((k) => caches.delete(k)));
    }
  } catch (_) {}
}

function showShutdownDonePage() {
  document.body.innerHTML = `
    <div style="min-height:100vh;display:flex;align-items:center;justify-content:center;background:#070b14;color:#e8edf5;font-family:'Noto Sans KR',sans-serif;padding:24px;text-align:center;">
      <div>
        <h1 style="font-size:1.6rem;margin-bottom:12px;">서버가 종료되었습니다</h1>
        <p style="color:#8b9cb3;margin-bottom:8px;">백엔드 종료 · 포트 정리 · 캐시 삭제를 완료했습니다.</p>
        <p style="color:#8b9cb3;">이 창이 자동으로 닫히지 않으면 직접 닫아 주세요.</p>
      </div>
    </div>`;
}

// 확인 팝업 없이 바로 종료
async function shutdownApp() {
  closeCalcWindow();

  const buttons = [$("#btnShutdown"), $("#btnShutdownNav")].filter(Boolean);
  buttons.forEach((b) => {
    b.disabled = true;
    b.textContent = "종료 중…";
  });

  await clearClientCaches();

  // 브라우저 새로고침 → 새로 뜬 페이지가 finishShutdown() 으로 서버 종료 후 창을 닫는다
  window.location.replace("/?shutdown=1");
}

async function finishShutdown() {
  // 주소를 "/" 로 되돌려 둔다 (탭 복원 시 다시 종료되지 않도록)
  history.replaceState(null, "", "/");
  showShutdownDonePage();

  try {
    await fetch("/api/shutdown", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      keepalive: true,
    });
  } catch (_) {
    // 서버가 바로 내려가도 정상
  }

  // 앱 전용 창이면 닫히고, 일반 탭이면 안내 페이지가 남는다
  setTimeout(() => {
    try {
      window.open("", "_self");
      window.close();
    } catch (_) {}
    showShutdownDonePage();
  }, 400);
}

$("#btnShutdown")?.addEventListener("click", shutdownApp);
$("#btnShutdownNav")?.addEventListener("click", shutdownApp);

// ==========================================================================
// 🔄 업데이트 (Update)
// ==========================================================================
// 1) GitHub 확인 → 2) 업데이트 유무 팝업 · 승인 → 3) git pull → 4) 재시작 안내 팝업
async function updateApp() {
  let info;
  showBusy("GitHub에서 업데이트 확인 중…");
  try {
    const res = await fetch("/api/update/check", { cache: "no-store" });
    info = await res.json();
  } catch (err) {
    info = { ok: false, message: "서버에 연결할 수 없습니다." };
  } finally {
    hideBusy();
  }
  if (!info.ok) {
    alert("업데이트 확인 실패\n\n" + info.message);
    return;
  }
  if (!info.behind) {
    alert("현재 최신 버전입니다.\n받을 업데이트가 없습니다.");
    return;
  }

  const more = info.behind > info.commits.length ? `\n… 외 ${info.behind - info.commits.length}건` : "";
  const dirtyNote = info.dirty ? "\n\n※ 이 PC에서 수정한 파일이 있어 업데이트가 실패할 수 있습니다." : "";
  if (!confirm(`새 업데이트가 ${info.behind}건 있습니다.\n\n${info.commits.join("\n")}${more}${dirtyNote}\n\n지금 업데이트할까요?`)) {
    return;
  }

  let result;
  showBusy("업데이트 다운로드 중 (git pull)…");
  try {
    const res = await fetch("/api/update/apply", { method: "POST" });
    result = await res.json();
  } catch (err) {
    result = { ok: false, message: "서버에 연결할 수 없습니다." };
  } finally {
    hideBusy();
  }
  if (!result.ok) {
    alert("업데이트 실패\n\n" + result.message);
    return;
  }

  const depsNote = result.deps_changed ? "\n필요한 구성요소(의존성)도 재시작할 때 자동으로 설치됩니다." : "";
  if (!confirm(`업데이트가 완료되었습니다. (변경 파일 ${result.changed_files}개)\n변경 내용을 적용하려면 프로그램을 재시작해야 합니다.${depsNote}\n\n지금 재시작할까요?`)) {
    alert("다음에 프로그램을 재시작하면 업데이트가 적용됩니다.");
    return;
  }
  try {
    await fetch("/api/update/restart", { method: "POST" });
  } catch (_) {}
  reloadWhenServerBack();
}

// 업데이트 후 재시작된 서버가 응답하면 이 창을 새로고침 (START.bat은 새 창을 열지 않음)
function reloadWhenServerBack() {
  const startedAt = Date.now();
  let wentDown = false;
  showBusy("재시작 중… 서버 응답 대기");
  const timer = setInterval(async () => {
    let up = false;
    try {
      const r = await fetch("/api/server-info", { cache: "no-store" });
      up = r.ok;
    } catch (_) {
      up = false;
    }
    if (!up) wentDown = true;
    const elapsed = Date.now() - startedAt;
    const sec = Math.round(elapsed / 1000);
    setBusyText(wentDown ? `재시작 중… 구성요소 확인 후 서버 대기 (${sec}초)` : `재시작 준비 중… (${sec}초)`);
    // 기존 서버가 내려간 뒤 다시 올라왔거나, 3분이 지나면 새로고침
    if ((wentDown && up) || elapsed > 180000) {
      clearInterval(timer);
      window.location.reload();
    }
  }, 1500);
}

$("#btnUpdateApp")?.addEventListener("click", updateApp);

// ==========================================================================
// 📋 컴퓨터 작업내역서 (Task Report) 프론트엔드 컨트롤러
// ==========================================================================
let trReportsList = [];

const trModal = $("#taskReportModal");
const trTabListBtn = $("#trTabListBtn");

// 서명 캔버스 초기화
const signCanvasCust = $("#trSignCanvasCust");
const signCanvasTech = $("#trSignCanvasTech");

function initSignCanvas(canvas) {
  if (!canvas || canvas._signInit) return; // 창을 열 때마다 불리므로 이벤트가 겹쳐 달리지 않게
  canvas._signInit = true;
  const ctx = canvas.getContext("2d");
  ctx.strokeStyle = "#0f172a";
  ctx.lineWidth = 2;
  ctx.lineCap = "round";
  ctx.lineJoin = "round";

  let isDrawing = false;
  let lastX = 0, lastY = 0;

  /** 캔버스 내부 픽셀 크기를 화면에 보이는 크기에 맞춘다. 안 맞으면 서명이 칸의 위쪽 일부에만 그려진다.
   *  크기를 바꾸면 캔버스가 지워지므로 그려 둔 서명은 옮겨 그린다. */
  function syncSize() {
    const rect = canvas.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    const w = Math.round(rect.width * dpr), h = Math.round(rect.height * dpr);
    if (!w || !h || (canvas.width === w && canvas.height === h)) return;
    const old = document.createElement("canvas");
    old.width = canvas.width; old.height = canvas.height;
    old.getContext("2d").drawImage(canvas, 0, 0);
    canvas.width = w; canvas.height = h;
    ctx.drawImage(old, 0, 0, w, h);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.strokeStyle = "#0f172a";
    ctx.lineWidth = 2;
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
  }

  function getPos(e) {
    const rect = canvas.getBoundingClientRect();
    const clientX = e.touches ? e.touches[0].clientX : e.clientX;
    const clientY = e.touches ? e.touches[0].clientY : e.clientY;
    return [clientX - rect.left, clientY - rect.top];
  }

  canvas._syncSize = syncSize;
  canvas._hasInk = false;

  function startDraw(e) {
    syncSize();
    isDrawing = true;
    canvas._hasInk = true;
    [lastX, lastY] = getPos(e);
    if (e.type === "touchstart") e.preventDefault();
  }

  function draw(e) {
    if (!isDrawing) return;
    const [x, y] = getPos(e);
    ctx.beginPath();
    ctx.moveTo(lastX, lastY);
    ctx.lineTo(x, y);
    ctx.stroke();
    [lastX, lastY] = [x, y];
    if (e.type === "touchmove") e.preventDefault();
  }

  function stopDraw() {
    isDrawing = false;
  }

  canvas.addEventListener("mousedown", startDraw);
  canvas.addEventListener("mousemove", draw);
  canvas.addEventListener("mouseup", stopDraw);
  canvas.addEventListener("mouseleave", stopDraw);

  canvas.addEventListener("touchstart", startDraw, { passive: false });
  canvas.addEventListener("touchmove", draw, { passive: false });
  canvas.addEventListener("touchend", stopDraw);
}

function clearSignCanvas(type) {
  const canvas = type === "cust" ? signCanvasCust : signCanvasTech;
  if (canvas) {
    const ctx = canvas.getContext("2d");
    ctx.save();
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.restore();
    canvas._hasInk = false;
  }
}

/** 그린 서명을 PNG data URL 로 돌려준다. 아무것도 안 그렸으면 "". */
function getSignImage(canvas) {
  return canvas && canvas._hasInk ? canvas.toDataURL("image/png") : "";
}

/** 저장해 둔 서명 그림을 캔버스에 다시 그린다(창이 숨어 있어도 다음 그리기 때 크기를 맞추며 옮겨 그려진다). */
function loadSignImage(canvas, dataUrl) {
  if (!canvas || !dataUrl) return;
  const img = new Image();
  img.onload = () => {
    canvas._syncSize?.();
    const ctx = canvas.getContext("2d");
    ctx.save();
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
    ctx.restore();
    canvas._hasInk = true;
  };
  img.src = dataUrl;
}

// 작업 행 렌더링 & 계산 (기본 5행, 삭제 ✕ 는 맨 끝 조작 열)
const TR_MIN_TASK_ROWS = 5;

function renderTrTaskRows(tasks = []) {
  const tbody = $("#trTasksTbody");
  if (!tbody) return;
  tbody.innerHTML = "";

  const items = tasks.map((t) => ({ ...t }));
  while (items.length < TR_MIN_TASK_ROWS) {
    items.push({ no: items.length + 1, description: "", parts: "", cost: 0 });
  }

  items.forEach((task, idx) => {
    const tr = document.createElement("tr");
    tr.className = "tr-task-row";
    tr.innerHTML = `
      <td style="text-align:center;"><span class="tr-row-no">${idx + 1}</span></td>
      <td><input type="text" class="tr-inp tr-task-desc" value="${escapeHtml(task.description || '')}" placeholder="작업 내용" /></td>
      <td><input type="text" class="tr-inp tr-task-parts" value="${escapeHtml(task.parts || '')}" placeholder="부품 / 자재" /></td>
      <td class="tr-col-cost"><input type="text" inputmode="numeric" class="tr-inp tr-task-cost" style="text-align:right;" value="${task.cost ? Number(task.cost).toLocaleString("ko-KR") : ''}" placeholder="0" oninput="formatTrCostInput(this)" /></td>
      <td class="tr-col-act no-print"><button type="button" class="tr-task-del-btn" title="이 행 삭제" onclick="deleteTrTaskRow(${idx})"${items.length <= 1 ? " disabled" : ""}>✕</button></td>
    `;
    tbody.appendChild(tr);
  });

  calcTrTotal();
}

function addTrTaskRow() {
  const currentTasks = getTrTasksFromForm({ keepEmpty: true });
  currentTasks.push({ no: currentTasks.length + 1, description: "", parts: "", cost: 0 });
  renderTrTaskRows(currentTasks);
}

window.deleteTrTaskRow = function(idx) {
  const currentTasks = getTrTasksFromForm({ keepEmpty: true });
  if (currentTasks.length <= 1) return;
  currentTasks.splice(idx, 1);
  // 5행 아래로 줄어들면 renderTrTaskRows 가 빈 행을 채워 넣는다
  renderTrTaskRows(currentTasks);
};

/** 화면의 작업 행. 기본은 끝의 빈 행을 떼어 낸다(저장·Excel 에 빈 번호 행이 늘어나지 않게). keepEmpty 는 화면 편집용. */
function getTrTasksFromForm({ keepEmpty = false } = {}) {
  const rows = document.querySelectorAll("#trTasksTbody tr");
  const tasks = [];
  rows.forEach((tr) => {
    const desc = tr.querySelector(".tr-task-desc")?.value.trim() || "";
    const parts = tr.querySelector(".tr-task-parts")?.value.trim() || "";
    const cost = parseInt((tr.querySelector(".tr-task-cost")?.value || "").replace(/[^\d]/g, ""), 10) || 0;
    tasks.push({ no: 0, description: desc, parts: parts, cost: cost });
  });
  if (!keepEmpty) {
    while (tasks.length && !tasks[tasks.length - 1].description && !tasks[tasks.length - 1].parts && !tasks[tasks.length - 1].cost) {
      tasks.pop();
    }
  }
  tasks.forEach((t, i) => { t.no = i + 1; });
  return tasks;
}

/** 보증 기간: 1 · 3개월 중 고르거나 ( N 개월) 에 직접 적는다. 저장 값은 예전처럼 "N개월" 글자. */
const TR_WARRANTY_PRESETS = ["1개월", "3개월"];

function getTrWarranty() {
  const picked = document.querySelector('input[name="trWarranty"]:checked')?.value || "3개월";
  if (picked !== "custom") return picked;
  const v = ($("#trWarrantyCustom")?.value || "").trim();
  if (!v) return "3개월";
  return /^\d+$/.test(v) ? `${v}개월` : v; // "1년" 처럼 숫자가 아니면 적은 그대로
}

function setTrWarranty(value) {
  const v = String(value || "3개월").trim();
  const custom = $("#trWarrantyCustom");
  const isPreset = TR_WARRANTY_PRESETS.includes(v);
  const radio = document.querySelector(`input[name="trWarranty"][value="${isPreset ? v : "custom"}"]`);
  if (radio) radio.checked = true;
  if (custom) custom.value = isPreset ? "" : (v.match(/^(\d+)\s*개월$/)?.[1] ?? v);
}

/** 증상 / 요청 사항: 1행으로 시작해 글이 줄을 넘으면 행이 늘어난다. */
function autoGrowTextarea(el) {
  if (!el) return;
  el.style.height = "auto";
  if (!el.scrollHeight) return; // 창이 숨어 있으면 잴 수 없다 — 창을 연 뒤 다시 맞춘다
  el.style.height = `${el.scrollHeight + (el.offsetHeight - el.clientHeight)}px`;
}
$("#trSymptoms")?.addEventListener("input", (e) => autoGrowTextarea(e.target));

// ( N 개월) 칸에 적으면 그 항목이 저절로 선택된다
$("#trWarrantyCustom")?.addEventListener("input", () => {
  const r = document.querySelector('input[name="trWarranty"][value="custom"]');
  if (r) r.checked = true;
});

/** 비용 칸: 입력하는 동안 숫자만 남기고 천 단위 콤마를 넣는다(커서는 뒤에서부터 같은 숫자 위치에 둔다). */
window.formatTrCostInput = function(inp) {
  const pos = inp.selectionStart ?? inp.value.length;
  const digitsAfter = inp.value.slice(pos).replace(/[^\d]/g, "").length;
  const digits = inp.value.replace(/[^\d]/g, "").replace(/^0+(?=\d)/, "").slice(0, 10);
  inp.value = digits ? Number(digits).toLocaleString("ko-KR") : "";
  let caret = inp.value.length, seen = 0;
  while (caret > 0 && seen < digitsAfter) {
    if (/\d/.test(inp.value[caret - 1])) seen += 1;
    caret -= 1;
  }
  inp.setSelectionRange(caret, caret);
  calcTrTotal();
};

function calcTrTotal() {
  const tasks = getTrTasksFromForm();
  const total = tasks.reduce((sum, t) => sum + (t.cost || 0), 0);
  const display = $("#trTotalCostDisplay");
  if (display) display.textContent = total.toLocaleString("ko-KR");
  return total;
}

// 폼 데이터 수집 / 채우기
function getTrFormData() {
  const companyName = $("#trCompanyName")?.value.trim() || "0000 ";
  const companyAddress = $("#trCompanyAddress")?.value.trim() || "";
  const companyPhone = $("#trCompanyPhone")?.value.trim() || "";
  const businessNumber = $("#trBusinessNumber")?.value.trim() || "";
  const docNo = $("#trDocNo")?.value.trim() || "";
  const receiptDate = $("#trReceiptDate")?.value || localTodayStr();

  const customerName = $("#trCustName")?.value.trim() || "";
  const customerPhone = $("#trCustPhone")?.value.trim() || "";
  const customerAddress = $("#trCustAddress")?.value.trim() || "";
  const customerEmail = $("#trCustEmail")?.value.trim() || "";
  const contactMethod = document.querySelector('input[name="trContactMethod"]:checked')?.value || "전화";

  const deviceType = document.querySelector('input[name="trDeviceType"]:checked')?.value || "데스크탑";
  const deviceModel = $("#trDeviceModel")?.value.trim() || "";
  const serialNumber = $("#trSerialNumber")?.value.trim() || "";
  const purchaseDate = $("#trPurchaseDate")?.value || "";

  const accessories = [];
  document.querySelectorAll(".tr-acc-check:checked").forEach((chk) => accessories.push(chk.value));
  const accessoriesCustom = $("#trAccCustom")?.value.trim() || "";

  const symptoms = $("#trSymptoms")?.value.trim() || "";
  const tasks = getTrTasksFromForm();
  const totalCost = calcTrTotal();

  const status = document.querySelector('input[name="trStatus"]:checked')?.value || "완료";
  const expectedDate = $("#trExpectedDate")?.value || receiptDate;
  const technician = $("#trTechnician")?.value.trim() || "김대건";
  const warrantyPeriod = getTrWarranty();

  const customerSign = $("#trSignTextCust")?.value.trim() || "";
  const technicianSign = $("#trSignTextTech")?.value.trim() || "";

  return {
    id: currentTrId,
    company_name: companyName,
    company_address: companyAddress,
    company_phone: companyPhone,
    business_number: businessNumber,
    doc_no: docNo,
    receipt_date: receiptDate,
    customer_name: customerName,
    customer_phone: customerPhone,
    customer_address: customerAddress,
    customer_email: customerEmail,
    contact_method: contactMethod,
    device_type: deviceType,
    device_model: deviceModel,
    serial_number: serialNumber,
    purchase_date: purchaseDate,
    accessories: accessories,
    accessories_custom: accessoriesCustom,
    symptoms: symptoms,
    tasks: tasks,
    total_cost: totalCost,
    status: status,
    expected_date: expectedDate,
    technician: technician,
    warranty_period: warrantyPeriod,
    customer_sign: customerSign,
    technician_sign: technicianSign,
    customer_sign_img: getSignImage(signCanvasCust),
    technician_sign_img: getSignImage(signCanvasTech),
  };
}

// 컴퓨터(시스템) 기준 오늘 날짜 YYYY-MM-DD — toISOString()은 UTC라 한국 시간 오전 9시 전에는 전날로 나온다
function localTodayStr() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}
let trFormDay = null; // 작업내역서 폼을 채운 날짜(날짜가 바뀌었는지 판단용)

// 프로그램을 켜 둔 채 날짜가 바뀌면 시스템 날짜로 맞춘다
function syncTrDateWithSystem() {
  const today = localTodayStr();
  const quoteDateEl = $("#quoteDate");
  if (quoteDateEl && quoteDateEl.textContent !== today) quoteDateEl.textContent = today;
  if (!trFormDay || trFormDay === today) return;
  const oldDay = trFormDay;
  if (currentTrId) {
    // 저장된 내역서는 목록에 남아 있으므로, 날짜가 바뀌면 새 내역서로 시작한다(모달이 열려 있으면 작업 방해 않음)
    if (!trModal || trModal.classList.contains("hidden")) resetTrForm();
    return;
  }
  // 작성 중인 새 내역서: 입력 내용은 두고, 이전 날짜 그대로인 칸만 오늘로 바꾼다
  if ($("#trReceiptDate")?.value === oldDay) $("#trReceiptDate").value = today;
  if ($("#trExpectedDate")?.value === oldDay) $("#trExpectedDate").value = today;
  const docNoEl = $("#trDocNo");
  const oldPrefix = `TR-${oldDay.replace(/-/g, "")}-`;
  if (docNoEl?.value.startsWith(oldPrefix)) {
    docNoEl.value = `TR-${today.replace(/-/g, "")}-${docNoEl.value.slice(oldPrefix.length)}`;
  }
  trFormDay = today;
}
setInterval(syncTrDateWithSystem, 60 * 1000);
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) syncTrDateWithSystem();
});

// 견적 정보의 입금 계좌를 작업내역서에 표시(계좌번호가 없으면 숨김) — Excel 견적서와 같은 문구
function renderTrBankLine() {
  const el = $("#trBankLine");
  if (!el) return;
  const account = (quoteInfo?.bank_account || "").trim();
  const bank = (quoteInfo?.bank_name || "").trim();
  const holder = (quoteInfo?.bank_holder || "").trim();
  el.textContent = account
    ? `■ 입금 계좌 : ${bank ? bank + " " : ""}${account}${holder ? `  (예금주 : ${holder})` : ""}`
    : "";
  el.classList.toggle("hidden", !account);
}

function fillTrForm(data) {
  currentTrId = data?.id || null;
  trFormDay = localTodayStr();
  const badge = $("#trCurrentStatusBadge");
  if (badge) {
    badge.textContent = currentTrId ? `작업내역서 #${data.doc_no || currentTrId}` : "신규 작성중";
  }

  // 작업내역서의 업체정보: 견적정보의 공급자 정보를 우선 기입
  if ($("#trCompanyName")) $("#trCompanyName").value = data?.company_name || quoteInfo?.supplier_name || "대한민국주식회사";
  if ($("#trCompanyAddress")) $("#trCompanyAddress").value = data?.company_address || quoteInfo?.supplier_address || "서울시";
  if ($("#trCompanyPhone")) $("#trCompanyPhone").value = data?.company_phone || quoteInfo?.supplier_phone || "010-1234-5678";
  if ($("#trBusinessNumber")) $("#trBusinessNumber").value = data?.business_number || quoteInfo?.supplier_business_number || "";

  const todayStr = trFormDay;
  if ($("#trDocNo")) $("#trDocNo").value = data?.doc_no || `TR-${todayStr.replace(/-/g, "")}-${Math.floor(Math.random()*900+100)}`;
  if ($("#trReceiptDate")) $("#trReceiptDate").value = data?.receipt_date || todayStr;

  if ($("#trCustName")) $("#trCustName").value = data?.customer_name || (!currentTrId && quoteInfo?.customer_name ? quoteInfo.customer_name : "");
  if ($("#trCustPhone")) $("#trCustPhone").value = data?.customer_phone || (!currentTrId && quoteInfo?.customer_phone ? quoteInfo.customer_phone : "");
  if ($("#trCustAddress")) $("#trCustAddress").value = data?.customer_address || (!currentTrId && quoteInfo?.customer_address ? quoteInfo.customer_address : "");
  if ($("#trCustEmail")) $("#trCustEmail").value = data?.customer_email || (!currentTrId && quoteInfo?.customer_email ? quoteInfo.customer_email : "");

  // 라디오 선택
  const cm = data?.contact_method || "전화";
  const cmRadio = document.querySelector(`input[name="trContactMethod"][value="${cm}"]`);
  if (cmRadio) cmRadio.checked = true;

  const dt = data?.device_type || "데스크탑";
  const dtRadio = document.querySelector(`input[name="trDeviceType"][value="${dt}"]`);
  if (dtRadio) dtRadio.checked = true;

  if ($("#trDeviceModel")) $("#trDeviceModel").value = data?.device_model || "";
  if ($("#trSerialNumber")) $("#trSerialNumber").value = data?.serial_number || "";
  if ($("#trPurchaseDate")) $("#trPurchaseDate").value = data?.purchase_date || "";

  // 체크박스
  const acc = data?.accessories || [];
  document.querySelectorAll(".tr-acc-check").forEach((chk) => {
    chk.checked = acc.includes(chk.value);
  });
  if ($("#trAccCustom")) $("#trAccCustom").value = data?.accessories_custom || "";

  if ($("#trSymptoms")) $("#trSymptoms").value = data?.symptoms || "";
  autoGrowTextarea($("#trSymptoms"));

  // 작업 내역 행
  renderTrTaskRows(data?.tasks || []);

  const st = data?.status || "완료";
  const stRadio = document.querySelector(`input[name="trStatus"][value="${st}"]`);
  if (stRadio) stRadio.checked = true;

  if ($("#trExpectedDate")) $("#trExpectedDate").value = data?.expected_date || todayStr;
  if ($("#trTechnician")) $("#trTechnician").value = data?.technician || "김대건";
  setTrWarranty(data?.warranty_period || "3개월");

  if ($("#trSignTextCust")) $("#trSignTextCust").value = data?.customer_sign || "";
  if ($("#trSignTextTech")) $("#trSignTextTech").value = data?.technician_sign || "";

  clearSignCanvas("cust");
  clearSignCanvas("tech");
  loadSignImage(signCanvasCust, data?.customer_sign_img);
  loadSignImage(signCanvasTech, data?.technician_sign_img);
  updateBlinkWarnings();
}

function resetTrForm() {
  fillTrForm(null);
}

// 작업내역서 모달 열기/닫기
function openTrModal() {
  if (!trModal) return;
  initSignCanvas(signCanvasCust);
  initSignCanvas(signCanvasTech);
  syncTrDateWithSystem();
  renderTrBankLine();
  loadTrBackupDir();
  if (!currentTrId) {
    resetTrForm();
  }
  loadTrReportsList();
  show(trModal);
  autoGrowTextarea($("#trSymptoms"));
  updateBlinkWarnings();
}

function closeTrModal() {
  if (!trModal) return;
  hideTrAfterPrintNotice();
  hide(trModal);
}

// API 서버 통신
async function loadTrReportsList() {
  try {
    const res = await fetch("/api/task-reports");
    if (!res.ok) throw new Error("목록 조회 실패");
    const data = await res.json();
    trReportsList = data.reports || [];
    if (trReportsList.length > 0) {
      const hasRealReport = trReportsList.some((r) => !isDummyValue(r.company_name, DUMMY_VALUES.names));
      if (hasRealReport) {
        isTaskReportSaved = true;
      }
    } else {
      isTaskReportSaved = false;
    }
    renderTrListTable(filteredTrReports());
    const countEl = $("#trListCount");
    if (countEl) countEl.textContent = trReportsList.length;
    const statsEl = $("#trListStats");
    if (statsEl) statsEl.textContent = `${trReportsList.length}건`;
    updateBlinkWarnings();
  } catch (e) {
    console.warn("작업내역서 목록 로드 오류:", e);
  }
}

function renderTrListTable(reports) {
  const tbody = $("#trListTbody");
  if (!tbody) return;
  tbody.innerHTML = "";

  if (reports.length === 0) {
    tbody.innerHTML = `<tr><td colspan="8" style="text-align:center; padding:32px; color:var(--muted);">${trReportsList.length ? "검색 결과가 없습니다." : "저장된 작업 내역서가 없습니다."}</td></tr>`;
    return;
  }

  reports.forEach((rep) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${escapeHtml(rep.receipt_date || "-")}</td>
      <td><strong>${escapeHtml(rep.doc_no || rep.id)}</strong></td>
      <td>${escapeHtml(rep.customer_name || "-")}</td>
      <td>${escapeHtml(rep.customer_phone || "-")}</td>
      <td>${escapeHtml(rep.device_type || "")} ${escapeHtml(rep.device_model || "")}</td>
      <td><span class="badge-status">${escapeHtml(rep.status || "접수")}</span></td>
      <td style="font-weight:700; color:#fbbf24;">${(rep.total_cost || 0).toLocaleString("ko-KR")} 원</td>
      <td>
        <div class="tr-list-actions">
          <button type="button" class="btn btn-xs btn-primary" onclick="loadTrReportItem('${rep.id}')">📂 열기</button>
          <button type="button" class="btn btn-xs btn-outline" onclick="downloadTrExcelById('${rep.id}')">📥 Excel</button>
          <button type="button" class="btn btn-xs btn-shutdown" onclick="deleteTrReportItem('${rep.id}')">✕ 삭제</button>
        </div>
      </td>
    `;
    tbody.appendChild(tr);
  });
}

window.loadTrReportItem = async function(id) {
  try {
    const res = await fetch(`/api/task-reports/${id}`);
    if (!res.ok) throw new Error("내역 조회 실패");
    const data = await res.json();
    fillTrForm(data.report);
    openTrModal(); // 설정관리 위에 작업내역서 창이 뜬다. 닫으면 목록으로 돌아온다.
  } catch (e) {
    alert(e.message);
  }
};

window.deleteTrReportItem = async function(id) {
  if (!confirm("이 작업내역서를 삭제하시겠습니까?")) return;
  try {
    const res = await fetch(`/api/task-reports/${id}`, { method: "DELETE" });
    if (!res.ok) throw new Error("삭제 실패");
    if (currentTrId === id) resetTrForm();
    loadTrReportsList();
  } catch (e) {
    alert(e.message);
  }
};

window.downloadTrExcelById = function(id) {
  downloadWithBusy(`/api/task-reports/${id}/download/excel`, {}, "작업내역서.xlsx", "작업내역서 Excel 생성 중…");
};

// 저장
async function saveTrForm() {
  const data = getTrFormData();
  if (!data.customer_name) {
    alert("고객명을 입력해 주세요.");
    $("#trCustName")?.focus();
    return;
  }

  const isUpdate = Boolean(currentTrId);
  const url = isUpdate ? `/api/task-reports/${currentTrId}` : "/api/task-reports";
  const method = isUpdate ? "PUT" : "POST";

  try {
    const btn = $("#trBtnSave");
    if (btn) { btn.disabled = true; btn.textContent = "저장 중…"; }

    const res = await fetch(url, {
      method: method,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });

    if (!res.ok) throw new Error("저장에 실패했습니다.");
    const result = await res.json();
    currentTrId = result.report.id;
    isTaskReportSaved = true;
    fillTrForm(result.report);
    loadTrReportsList();
    updateBlinkWarnings();
    alert("✔ 작업내역서가 성공적으로 저장되었습니다.");
  } catch (e) {
    alert(e.message);
  } finally {
    const btn = $("#trBtnSave");
    if (btn) { btn.disabled = false; btn.textContent = "💾 저장하기"; }
  }
}

// ---------- 백업: 지정 폴더에 Excel + JSON 으로 모아 두기 ----------
let trBackupDir = "";

async function postJsonRaw(url, body) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  const data = await res.json().catch(() => ({}));
  return { status: res.status, ok: res.ok, data };
}

function syncTrBackupDirUI() {
  const btn = $("#trBtnBackupDir");
  if (btn) {
    btn.title = trBackupDir
      ? `백업 폴더: ${trBackupDir}\n눌러서 바꿉니다 (폴더 선택 창은 서버 PC 화면에 뜹니다)`
      : "백업 폴더를 지정합니다 (폴더 선택 창은 서버 PC 화면에 뜹니다)";
  }
  const backupBtn = $("#trBtnBackup");
  if (backupBtn) {
    backupBtn.title = trBackupDir
      ? `저장된 작업내역서 전체를 ZIP 한 파일로 백업합니다\n백업 폴더: ${trBackupDir}`
      : "저장된 작업내역서 전체를 ZIP 한 파일로 백업합니다. 폴더가 없으면 먼저 고릅니다";
  }
  const openBtn = $("#trBtnBackupOpen");
  if (openBtn) {
    openBtn.title = trBackupDir
      ? `백업 폴더를 서버 PC 화면의 탐색기로 엽니다\n${trBackupDir}`
      : "백업 폴더가 아직 없습니다. 💽 백업 또는 📁 로 먼저 지정하세요";
    openBtn.disabled = !trBackupDir;
  }
}

/** 백업 폴더(또는 ZIP 파일 선택 상태)를 서버 PC 탐색기로 연다. */
async function openTrBackupDir(path = "") {
  let res;
  try {
    res = await postJsonRaw("/api/task-reports/backup/open-dir", path ? { path } : {});
  } catch (_) {
    alert("서버에 연결할 수 없습니다.");
    return;
  }
  if (!res.ok) {
    const d = res.data?.detail;
    alert((d && (d.message || d)) || "폴더를 열지 못했습니다.");
  }
}

async function loadTrBackupDir() {
  try {
    const res = await fetch("/api/task-reports/backup/settings", { cache: "no-store" });
    const data = await res.json();
    trBackupDir = data.dir || "";
  } catch (_) {
    trBackupDir = "";
  }
  syncTrBackupDirUI();
}

/** 폴더 선택 창(서버 PC 화면) → 취소하면 경로 직접 입력. 정해진 폴더를 돌려주고, 없으면 "". */
async function chooseTrBackupDir() {
  const btn = $("#trBtnBackupDir");
  if (btn) btn.disabled = true;
  showBusy("백업 폴더 선택 창이 서버 PC 화면에 열렸습니다. 폴더를 고르세요…");
  let res;
  try {
    res = await postJsonRaw("/api/task-reports/backup/choose-dir", {});
  } catch (_) {
    res = { ok: false, data: { message: "서버에 연결할 수 없습니다." } };
  } finally {
    hideBusy();
    if (btn) btn.disabled = false;
  }
  if (res.ok && res.data.ok && res.data.dir) {
    trBackupDir = res.data.dir;
    syncTrBackupDirUI();
    return trBackupDir;
  }
  const typed = prompt(
    "폴더 선택 창에서 폴더를 고르지 않았습니다.\n백업 폴더 경로를 직접 입력하세요 (서버 PC 기준 경로, 없으면 새로 만듭니다).",
    res.data?.dir || trBackupDir || ""
  );
  if (!typed || !typed.trim()) return "";
  let typedRes;
  try {
    typedRes = await postJsonRaw("/api/task-reports/backup/choose-dir", { dir: typed.trim() });
  } catch (_) {
    alert("서버에 연결할 수 없습니다.");
    return "";
  }
  if (!typedRes.ok || !typedRes.data.ok) {
    alert(typedRes.data?.detail || typedRes.data?.message || "폴더를 지정하지 못했습니다.");
    return "";
  }
  trBackupDir = typedRes.data.dir;
  syncTrBackupDirUI();
  return trBackupDir;
}

async function backupTrReport() {
  const btn = $("#trBtnBackup");
  if (btn) btn.disabled = true;
  try {
    if (!trBackupDir) {
      const dir = await chooseTrBackupDir();
      if (!dir) return;
    }
    showBusy("저장된 작업내역서를 ZIP 으로 묶는 중…");
    let res;
    try {
      res = await postJsonRaw("/api/task-reports/backup", {});
    } finally {
      hideBusy();
    }
    if (res.status === 428) {
      // 폴더가 아직 없거나 사라졌다 → 다시 고르고 한 번 더
      trBackupDir = "";
      syncTrBackupDirUI();
      const dir = await chooseTrBackupDir();
      if (!dir) return;
      showBusy("저장된 작업내역서를 ZIP 으로 묶는 중…");
      try {
        res = await postJsonRaw("/api/task-reports/backup", {});
      } finally {
        hideBusy();
      }
    }
    if (!res.ok || !res.data.ok) {
      const d = res.data?.detail;
      throw new Error((d && (d.message || d)) || "백업에 실패했습니다.");
    }
    const kb = Math.max(1, Math.round((res.data.size || 0) / 1024));
    const openIt = confirm(
      `✔ 백업 완료 — 저장된 내역서 ${res.data.count}건\n\n파일: ${res.data.path}\n크기: ${kb}KB\n\n백업 폴더를 열까요? (서버 PC 화면의 탐색기에 열립니다)`
    );
    if (openIt) await openTrBackupDir(res.data.path);
  } catch (e) {
    alert("백업 오류: " + e.message);
  } finally {
    if (btn) btn.disabled = false;
  }
}

// 인쇄 / PDF 출력
function printTrPaper() {
  // 인쇄 창(브라우저 것)은 버튼을 넣을 수 없으므로, 닫힌 뒤 앱 쪽에 [닫기] 안내를 띄운다
  window.addEventListener("afterprint", showTrAfterPrintNotice, { once: true });
  window.print();
}

const TR_AFTER_PRINT_SEC = 10;
let trAfterPrintTimer = null;

/** 인쇄 창이 닫히면 잠깐 띄우는 안내. 앱 창의 ✕ 대신 여기 [✕ 닫기]로 작업내역서만 닫게 한다. */
function showTrAfterPrintNotice() {
  hideTrAfterPrintNotice();
  const box = document.createElement("div");
  box.id = "trAfterPrintNotice";
  box.className = "tr-after-print no-print";
  box.setAttribute("role", "alertdialog");
  box.innerHTML = `
    <div class="tr-after-print-msg">🖨️ 인쇄 창이 닫혔습니다.<br><small>작업내역서를 닫으려면 앱 창의 ✕ 가 아니라 아래 [✕ 닫기]를 누르세요.</small></div>
    <div class="tr-after-print-btns">
      <button type="button" class="btn-modal-close tr-after-print-close">✕ 닫기</button>
      <button type="button" class="btn btn-outline btn-sm tr-after-print-stay">계속 작업 <span class="tr-after-print-sec">${TR_AFTER_PRINT_SEC}</span></button>
    </div>`;
  box.querySelector(".tr-after-print-close").addEventListener("click", () => { hideTrAfterPrintNotice(); closeTrModal(); });
  box.querySelector(".tr-after-print-stay").addEventListener("click", hideTrAfterPrintNotice);
  document.body.appendChild(box);
  box.querySelector(".tr-after-print-close").focus();
  let left = TR_AFTER_PRINT_SEC;
  trAfterPrintTimer = setInterval(() => {
    left -= 1;
    const sec = box.querySelector(".tr-after-print-sec");
    if (sec) sec.textContent = left;
    if (left <= 0) hideTrAfterPrintNotice(); // 아무것도 안 누르면 창은 그대로 두고 안내만 사라진다
  }, 1000);
}

function hideTrAfterPrintNotice() {
  if (trAfterPrintTimer) clearInterval(trAfterPrintTimer);
  trAfterPrintTimer = null;
  document.getElementById("trAfterPrintNotice")?.remove();
}

// 실시간 엑셀 다운로드
async function downloadTrExcelDirect() {
  const data = getTrFormData();
  const custName = data.customer_name || "고객";
  const docNo = data.doc_no || "01";
  await downloadWithBusy(
    "/api/task-reports/download/excel",
    { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) },
    `작업내역서_${custName}_${docNo}.xlsx`,
    "작업내역서 Excel 생성 중…"
  );
}

// 이벤트 바인딩
$("#trModalClose")?.addEventListener("click", closeTrModal);
$("#trBtnClose")?.addEventListener("click", closeTrModal);
trModal?.addEventListener("click", (e) => {
  if (e.target === trModal) closeTrModal();
});

trTabListBtn?.addEventListener("click", () => {
  closeTrModal();
  openSettings("taskreport");
});
$("#setTrBtnNew")?.addEventListener("click", () => {
  resetTrForm();
  openTrModal();
});
$("#setTrBtnBackup")?.addEventListener("click", backupTrReport);
$("#setTrBtnBackupOpen")?.addEventListener("click", () => openTrBackupDir());

$("#trBtnNew")?.addEventListener("click", () => {
  if (confirm("새 작업내역서를 작성하시겠습니까? (현재 작성 내용 초기화)")) {
    resetTrForm();
  }
});
$("#trBtnAddTaskRow")?.addEventListener("click", addTrTaskRow);
$("#trBtnSave")?.addEventListener("click", saveTrForm);
$("#trBtnBackup")?.addEventListener("click", backupTrReport);
$("#trBtnBackupOpen")?.addEventListener("click", () => openTrBackupDir());
$("#trBtnBackupDir")?.addEventListener("click", async () => {
  const dir = await chooseTrBackupDir();
  if (dir) alert(`백업 폴더: ${dir}`);
});
$("#trBtnPrint")?.addEventListener("click", printTrPaper);
$("#trBtnExcel")?.addEventListener("click", downloadTrExcelDirect);
$("#trBtnRefreshList")?.addEventListener("click", loadTrReportsList);

// 작업내역서 업체 정보 변경 시 실시간 깜박임 갱신
["#trCompanyName", "#trCompanyAddress", "#trCompanyPhone"].forEach((sel) => {
  $(sel)?.addEventListener("input", () => {
    updateBlinkWarnings();
  });
});

// 견적 정보 입력 필드 변경 시 실시간 경고 배너 갱신
Object.values(qiFields).forEach((sel) => {
  $(sel)?.addEventListener("input", () => {
    const currentValues = {};
    for (const [key, fieldSel] of Object.entries(qiFields)) {
      currentValues[key] = $(fieldSel)?.value.trim() || "";
    }
    const isStillDummy = isQuoteInfoDummy(currentValues);
    $("#qiWarningNotice")?.classList.toggle("hidden", !isStillDummy);
  });
});

// 검색 필터
// 검색칸에 남은 검색어로 목록을 거른다 (새로고침 후에도 필터 유지)
function filteredTrReports() {
  const q = ($("#trSearchInput")?.value || "").toLowerCase().trim();
  if (!q) return trReportsList;
  return trReportsList.filter((rep) => {
    return (
      (rep.customer_name || "").toLowerCase().includes(q) ||
      (rep.customer_phone || "").toLowerCase().includes(q) ||
      (rep.doc_no || "").toLowerCase().includes(q) ||
      (rep.device_model || "").toLowerCase().includes(q)
    );
  });
}
$("#trSearchInput")?.addEventListener("input", () => renderTrListTable(filteredTrReports()));
// 설정관리 목록 검색 버튼: 🔍 검색 / Enter 는 필터를 다시 적용, ✕ 는 검색어를 지우고 전체 목록으로
function runSettingsSearch(inputId, clear = false) {
  const inp = document.getElementById(inputId);
  if (!inp) return;
  if (clear) inp.value = "";
  inp.dispatchEvent(new Event("input"));
  inp.closest(".settings-panel")?.querySelector(".tr-list-table-wrap")?.scrollTo({ top: 0 });
  inp.focus();
}
document.querySelectorAll(".set-search-go").forEach((b) => b.addEventListener("click", () => runSettingsSearch(b.dataset.for)));
document.querySelectorAll(".set-search-clear").forEach((b) => b.addEventListener("click", () => runSettingsSearch(b.dataset.for, true)));
["custSearch", "qaSearch", "trSearchInput"].forEach((id) => {
  document.getElementById(id)?.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); runSettingsSearch(id); }
    else if (e.key === "Escape" && e.target.value) { e.preventDefault(); e.stopPropagation(); runSettingsSearch(id, true); }
  });
});
// ---------- 📝 새 견적 만들기 (직접 작성 견적, app/manual_quote.py) ----------
// 설정관리 [📝 새 견적 만들기] 탭에서 폼1~8 중 하나를 고르면 빈 견적 작성 창이 열린다.
// 단가는 부가세 포함 금액 그대로 쓴다(마진 · 만원 올림 없음). Excel · PDF · 메일은 카드 견적과 같은 서버 코드를 쓴다.
const mqModal = $("#manualQuoteModal");
const MQ_MAX_ROWS = 20; // 서버 manual_quote.MAX_ITEMS 와 같게
// 종이에 처음 보이는 행 수. 실제 견적서(manual_quote.MIN_ITEM_ROWS = 20행)와 같은 칸 수로 보여 준다.
const MQ_MIN_ROWS = MQ_MAX_ROWS;
let mqCurrentId = "";
// mqList(작성본 목록)는 위 🗂️ 견적서 관리 코드에 선언돼 있다
let mqPreviewUrl = "";
let mqPreviewSeq = 0;

function mqDigits(v) {
  return String(v ?? "").replace(/[^\d]/g, "");
}

function mqFmt(n) {
  return (Number(n) || 0).toLocaleString("ko-KR");
}

/** 🗂️ 견적서 관리 탭 [➕ 새 견적 만들기]로 펼치는 서식 8개 고르기 칸 */
function renderMqFormGrid() {
  const grid = $("#mqFormGrid");
  if (!grid || grid.childElementCount) return;
  grid.innerHTML = Object.entries(EXCEL_FORM_LABELS).map(([n, label]) => `
    <button type="button" class="mq-form-card" data-form="${n}" title="폼${n} (${label}) 서식으로 빈 견적을 만듭니다">
      <img src="/static/img/forms/form${n}.png" alt="폼${n} ${label} 서식" loading="lazy" />
      <span>폼${n} · ${label}</span>
    </button>`).join("");
  grid.querySelectorAll(".mq-form-card").forEach((b) =>
    b.addEventListener("click", () => openMqEditor(null, Number(b.dataset.form))));
}

/** 작성본 목록을 읽어 🗂️ 견적서 관리 목록에 합쳐 그린다. */
async function loadMqList() {
  const before = mqList.length;
  try {
    const res = await fetch("/api/manual-quotes", { cache: "no-store" });
    const data = await res.json();
    mqList = data.quotes || [];
  } catch (_) {
    mqList = [];
  }
  if (mqList.length > before && before) hubPage = 1; // 새로 저장한 작성본은 맨 앞(1쪽)에 들어간다
  renderQuoteHub();
}

async function deleteMq(id) {
  const rec = mqList.find((r) => r.id === id);
  if (!confirm(`작성본을 삭제할까요?\n${rec?.title || "(제목 없음)"}\n(이 작성본으로 만든 📥 보관 파일은 그대로 남습니다)`)) return;
  const res = await fetch(`/api/manual-quotes/${encodeURIComponent(id)}`, { method: "DELETE" }).catch(() => null);
  if (!res || !res.ok) {
    alert("삭제하지 못했습니다.");
    return;
  }
  if (mqCurrentId === id) mqCurrentId = "";
  loadMqList();
}

// ----- 작성 창: 고른 서식(폼1~8) 모양의 '종이' 위에서 바로 입력 -----
// 서식마다 제목 · 색 · 정보칸 · 열 머리글을 흉내 낸다(Excel 과 픽셀 단위로 같지는 않다 → 👁️ 미리보기가 실제 모양).
// 품목 행은 서식과 상관없이 같은 입력 클래스(.mq-cat/.mq-name/.mq-qty/.mq-price)를 쓴다.
// 견적처(customer_info)는 견적마다 따로 저장되고 Excel · PDF · 메일에 그대로 들어간다(견적정보 파일은 건드리지 않음).
let mqMeta = { title: "", quote_date: "", customer: { name: "", manager: "", phone: "", email: "", address: "" } };

// 열 키: no 번호 · cat 구분 · name 품명 · catname [구분] 품명 한 칸 · qty 수량 · unit 단가 · unitaux 단가(입력용, 서식엔 없음)
//        amount 금액(자동) · as 무상A/S(자동) · remark 비고(자동)
const MQ_PAPER = {
  1: { cols: ["no", "cat", "name", "qty", "unit", "amount"], heads: ["No", "구분", "품명", "수량", "단가", "금액"], th: "background:#1F4E79;color:#fff" },
  2: { cols: ["no", "cat", "name", "qty", "unit", "amount"], heads: ["No", "ITEM", "DESCRIPTION", "QTY", "RATE", "AMOUNT"], th: "background:#2C3E50;color:#fff", flat: true },
  3: { cols: ["catname", "qty", "unit", "amount"], heads: ["Description", "Qty", "Unit Price", "Amount"], th: "background:#D35400;color:#fff", flat: true, line: "#F0C9A8" },
  4: { cols: ["no", "cat", "name", "qty", "unit", "amount"], heads: ["No", "Component", "Specification", "Q'ty", "Unit", "Total"], th: "background:#27AE60;color:#fff", flat: true },
  5: { cols: ["catname", "qty", "unitaux", "amount"], heads: ["Item Description", "Qty", "단가 ✎", "Amount"], th: "background:#fff;color:#000;border-bottom:3px solid #34495E;text-align:left", flat: true },
  6: { cols: ["cat", "name", "qty", "unit", "amount", "as", "remark"], heads: ["부품 구분", "제조사 / 제품명 (상세 스펙)", "수량", "단가 (원)", "금액 (원)", "무상A/S", "비고"], th: "background:#F2F2F2;color:#000" },
  7: { cols: ["no", "cat", "name", "qty", "unitaux", "amount", "remark"], heads: ["No", "구 분", "부품 및 상세 사양", "수량", "단가 ✎", "금액", "비고"], th: "background:#D9E1F2;color:#000" },
  8: { cols: ["no", "cat", "name", "qty", "unit", "amount"], heads: ["NO", "품 목", "규격 및 상세 사양", "수 량", "단 가", "금 액"], th: "background:#1F4E79;color:#fff" },
};
const MQ_COL_WIDTH = { no: "6%", cat: "15%", qty: "7%", unit: "12%", unitaux: "12%", amount: "13%", as: "8%", remark: "11%" };
const MQ_VALID_NOTE = "▶ 견적유효기간 : 견적일로부터 24시간";

// excel_export._as_warranty_period / _spec_remark 와 같은 규칙(폼6 무상A/S, 폼6·7 비고 자동 표시용)
function mqWarranty(cat) {
  const c = (cat || "").toUpperCase();
  if (c.includes("CPU") || c.includes("메인보드") || c.includes("M.B")) return "3년";
  if (c.includes("그래픽") || c.includes("VGA") || c.includes("VIDEO")) return "3년";
  if (c.includes("SSD")) return "2~5년";
  if (c.includes("HDD")) return "2년";
  if (c.includes("파워") || c.includes("POWER")) return "5~7년";
  if (c.includes("쿨러") || c.includes("케이스") || c.includes("메모리") || c.includes("RAM")) return "1년";
  if (["조립", "공임", "기술", "소프트웨어"].some((k) => c.includes(k))) return "-";
  return "1년";
}

function mqRemark(cat, name) {
  const c = (cat || "").toUpperCase();
  const n = (name || "").toUpperCase();
  if (c.includes("RAM") || c.includes("메모리")) {
    if (n.includes("32")) return "총 32GB";
    if (n.includes("16")) return "총 16GB";
    if (n.includes("64")) return "총 64GB";
    return "";
  }
  if (c.includes("조립") || c.includes("공임")) return "초기불량 케어";
  if (c.includes("소프트웨어") || c.includes("윈도우") || n.includes("DSP")) return "정품 라이선스";
  return "";
}

function mqGet(path) {
  return path.split(".").reduce((o, k) => (o == null ? "" : o[k]), mqMeta) ?? "";
}

function mqSet(path, value) {
  const keys = path.split(".");
  const last = keys.pop();
  const obj = keys.reduce((o, k) => o[k], mqMeta);
  obj[last] = value;
}

/** 견적처 · 제목 · 날짜 입력칸(data-mq) 값을 mqMeta 와 맞춘다. except 는 지금 입력 중인 칸. */
function mqSyncBound(except = null) {
  document.querySelectorAll("#mqEditPane [data-mq]").forEach((el) => {
    if (el === except) return;
    const v = mqGet(el.dataset.mq);
    if (el.value !== v) el.value = v;
  });
  document.querySelectorAll("#mqEditPane [data-mq-text]").forEach((el) => {
    el.textContent = mqGet(el.dataset.mqText) || el.dataset.empty || "";
  });
}

function mqCustomerFromInfo() {
  const qi = quoteInfo || {};
  return {
    name: (qi.customer_name || "").trim(),
    manager: (qi.customer_manager || "").trim(),
    phone: (qi.customer_phone || "").trim(),
    email: (qi.customer_email || "").trim(),
    address: (qi.customer_address || "").trim(),
  };
}

/** 📇 거래처 불러오기 목록. 견적처 상호와 같은 거래처가 있으면 그 항목을 선택해 둔다. */
function renderMqCustPick() {
  const sel = $("#mqCustPick");
  if (!sel) return;
  const cur = (mqMeta.customer.name || "").trim();
  sel.innerHTML = `<option value="">${custList.length ? `— 저장된 거래처 ${custList.length}곳 —` : "— 저장된 거래처 없음 —"}</option>` +
    custList.map((c) => `<option value="${escapeHtml(c.id)}"${c.name === cur ? " selected" : ""}>${escapeHtml(c.name || "")}${c.manager ? ` (${escapeHtml(c.manager)})` : ""}</option>`).join("");
}

// ----- 종이(견적서 모양) 그리기 -----
function mqIn(path, ph = "", cls = "") {
  const type = path === "quote_date" ? "date" : "text";
  return `<input type="${type}" class="mqp-in ${cls}" data-mq="${path}" value="${escapeHtml(mqGet(path))}" placeholder="${escapeHtml(ph)}" />`;
}

function mqSup(key, fallback = "") {
  return `<span class="mqp-sup">${escapeHtml((quoteInfo?.[key] || "").trim() || fallback)}</span>`;
}

function mqTot(prefix = "", suffix = "") {
  return `<span data-mq-calc="total" data-prefix="${prefix}" data-suffix="${suffix}">${prefix}0${suffix}</span>`;
}

const MQ_CONTACT = () => `${mqIn("customer.phone", "연락처", "w-phone")} / ${mqIn("customer.email", "이메일", "w-email")}`;

function mqPaperTop(n) {
  const valid = `<div class="mqp-valid">${MQ_VALID_NOTE}</div>`;
  switch (n) {
    case 1:
      return `<div class="mqp-title" style="color:#1F4E79">견 &nbsp; 적 &nbsp; 서</div>
        <table class="mqp-grid mqp-info"><colgroup><col style="width:11%"><col style="width:39%"><col style="width:8%"><col style="width:11%"><col></colgroup>
          <tr><th>견 적 처</th><td>${mqIn("customer.name", "거래처 상호", "b")}</td><td class="mqp-gap"></td><th>공 급 자</th><td>${mqSup("supplier_name")}</td></tr>
          <tr><th>담 당 자</th><td>${mqIn("customer.manager", "담당자")}</td><td class="mqp-gap"></td><th>담 당 자</th><td>${mqSup("supplier_manager")}</td></tr>
          <tr><th>연 락 처</th><td>${MQ_CONTACT()}</td><td class="mqp-gap"></td><th>연 락 처</th><td>${mqSup("supplier_phone")}</td></tr>
          <tr><th>견 적 일</th><td>${mqIn("quote_date")}</td><td class="mqp-gap"></td><th>이 메 일</th><td>${mqSup("supplier_email")}</td></tr>
          <tr class="mqp-sum"><td colspan="2" class="c b">합계금액 (부가세포함)</td><td colspan="3" class="r big">${mqTot()}</td></tr>
        </table>${valid}`;
    case 2:
      return `<div class="mqp-flex"><div class="mqp-inv">INVOICE</div>
          <div class="mqp-right"><div class="mqp-supname">${mqSup("supplier_name")}</div>
            <div>Tel: ${mqSup("supplier_phone")} / 담당: ${mqSup("supplier_manager")}</div>
            <div>E-mail: ${mqSup("supplier_email")}</div>
            <div>DATE: ${mqIn("quote_date")}</div></div></div>
        <div class="b">BILL TO:</div>
        <div class="mqp-indent">${mqIn("customer.name", "거래처 상호", "b big w-name")}</div>
        <div class="mqp-indent">Attn: ${mqIn("customer.manager", "담당자", "w-mgr")} / Tel: ${mqIn("customer.phone", "연락처", "w-phone")} / E-mail: ${mqIn("customer.email", "이메일", "w-email")}</div>${valid}`;
    case 3:
      return `<div class="mqp-title l" style="color:#E67E22">PROJECT QUOTATION</div>
        <div class="mqp-box" style="border-color:#E67E22;background:#FDF2E9"><div class="mqp-box-l">
            <div class="b">To: ${mqIn("customer.name", "거래처 상호", "b w-name")} (${mqIn("customer.manager", "담당자", "w-mgr")} / ${mqIn("customer.phone", "연락처", "w-phone")} / ${mqIn("customer.email", "이메일", "w-email")})</div>
            <div>From: ${mqSup("supplier_name")} (${mqSup("supplier_manager")} / ${mqSup("supplier_phone")})</div>
            <div>E-mail: ${mqSup("supplier_email")}</div></div>
          <div class="mqp-box-r"><div class="mqp-muted">TOTAL ESTIMATE</div><div class="mqp-hero" style="color:#E67E22">${mqTot("₩ ")}</div></div></div>
        <div class="mqp-muted r">견적일 ${mqIn("quote_date")}</div>${valid}`;
    case 4:
      return `<div class="mqp-title" style="color:#27AE60">기술 명세 및 견적서 (Technical Proposal)</div>
        <div class="r">Date: ${mqIn("quote_date")} &nbsp;|&nbsp; Ref: 0</div>
        <table class="mqp-grid mqp-greenbox"><colgroup><col style="width:66%"><col></colgroup>
          <tr><td class="b">수신 (To) : ${mqIn("customer.name", "거래처 상호", "b w-name")} - ${mqIn("customer.manager", "담당자", "w-mgr")} 귀하 (${mqIn("customer.phone", "연락처", "w-phone")} / ${mqIn("customer.email", "이메일", "w-email")})</td><td class="c mqp-muted b">총 견적금액</td></tr>
          <tr><td>발신 (From): ${mqSup("supplier_name")} - ${mqSup("supplier_manager")} (${mqSup("supplier_phone")} / ${mqSup("supplier_email")})</td><td class="c mqp-hero" style="color:#27AE60">${mqTot("", " 원")}</td></tr>
        </table>${valid}`;
    case 5:
      return `<div class="mqp-title" style="color:#34495E">견 적 (ESTIMATE)</div>
        <div class="mqp-flex"><div class="b">To: ${mqIn("customer.name", "거래처 상호", "b w-name")} (${mqIn("customer.manager", "담당자", "w-mgr")} / ${mqIn("customer.phone", "연락처", "w-phone")} / ${mqIn("customer.email", "이메일", "w-email")})</div>
          <div>Date: ${mqIn("quote_date")}</div></div>${valid}`;
    case 6:
      return `<div class="mqp-banner" style="background:#1F4E79">조 립 &nbsp; 컴 퓨 터 &nbsp; 견 적 서</div>
        <div class="mqp-sec">1. 견적 개요 및 판매자 정보</div>
        <table class="mqp-grid mqp-info"><colgroup><col style="width:13%"><col style="width:41%"><col style="width:11%"><col></colgroup>
          <tr><th>견적 일자</th><td>${mqIn("quote_date")}</td><th>상 호 명</th><td>${mqSup("supplier_name")}</td></tr>
          <tr><th>고 객 명</th><td>${mqIn("customer.name", "거래처 상호", "b")}</td><th>대 표 자</th><td>${mqSup("supplier_manager")}</td></tr>
          <tr><th>담 당 자</th><td>${mqIn("customer.manager", "담당자")}</td><th>연 락 처</th><td>${mqSup("supplier_phone")}</td></tr>
          <tr><th>연 락 처</th><td>${MQ_CONTACT()}</td><th>이 메 일</th><td>${mqSup("supplier_email")}</td></tr>
          <tr><th>용 &nbsp; &nbsp; 도</th><td class="mqp-muted">□ 게이밍 &nbsp; □ 사무/인터넷 &nbsp; □ 영상편집</td><th>매장주소</th><td>${mqSup("supplier_address")}</td></tr>
        </table>
        <div class="mqp-sec">2. 조립 부품 세부 견적 내역</div>`;
    case 7:
      return `<div class="mqp-title" style="color:#1F4E79">견 &nbsp; &nbsp; 적 &nbsp; &nbsp; 서</div>
        <div class="mqp-sub">Estimate &nbsp;| &nbsp;Philippians 4:13 &nbsp;I can do all this through him who gives me strength.</div>
        <div class="mqp-flex top"><div class="mqp-lines">
            <div class="b">견적일 : ${mqIn("quote_date")}</div>
            <div class="b big">수 &nbsp;신 : ${mqIn("customer.name", "거래처 상호", "b big w-name")} (${mqIn("customer.manager", "담당자", "w-mgr")})</div>
            <div>연락처 : ${mqIn("customer.phone", "연락처", "w-phone")}</div>
            <div>이메일 : ${mqIn("customer.email", "이메일", "w-email")}</div>
            <div>아래와 같이 견적하여 드립니다.</div></div>
          <table class="mqp-grid mqp-info mqp-sup7"><colgroup><col style="width:28%"><col></colgroup>
            <tr><th>상 &nbsp; 호</th><td>${mqSup("supplier_name")} <small>(대표: ${mqSup("supplier_manager")})</small></td></tr>
            <tr><th>등록번호</th><td>${mqSup("supplier_business_number")}</td></tr>
            <tr><th>연 락 처</th><td>${mqSup("supplier_phone")}</td></tr>
            <tr><th>이 메 일</th><td>${mqSup("supplier_email")}</td></tr>
            <tr><th>주 &nbsp; 소</th><td>${mqSup("supplier_address")}</td></tr></table></div>
        <table class="mqp-grid mqp-items"><colgroup><col style="width:6%"><col style="width:17%"><col><col style="width:7%"><col style="width:13%"><col style="width:15%"></colgroup>
          <thead><tr>${["No", "구분/품명", "상세 모델명", "수량", "공급가(원)", "합계(VAT포함)"].map((h) => `<th style="background:#61D1C6;color:#fff">${h}</th>`).join("")}</tr></thead>
          <tbody><tr><td class="c">1</td><td class="c b">데스크탑 컴퓨터</td><td>${mqIn("title", "견적 제목 (모델명)")}</td><td class="c">1</td><td class="r" data-mq-calc="supply">0</td><td class="r b">${mqTot()}</td></tr>
            <tr class="mqp-sum"><td colspan="4" class="c b">합 &nbsp; 계 (VAT 포함)</td><td colspan="2" class="r b big">${mqTot("₩ ")}</td></tr></tbody></table>
        <div class="mqp-note b">■ 납품/보증: 발주 후 3~7일 이내 납품 | 모든 신품 하드웨어 1년 무상 보증 (당사 구매 제품 무상 점검 지원)</div>
        <div class="mqp-banner sm" style="background:#2E75B6">【 제 품 &nbsp; 상 세 &nbsp; S P E C 】</div>`;
    case 8:
      return `<div class="mqp-title" style="color:#1F4E79">견 &nbsp; &nbsp; 적 &nbsp; &nbsp; 서</div>
        <table class="mqp-grid mqp-info"><colgroup><col style="width:8%"><col style="width:42%"><col style="width:6%"><col style="width:11%"><col></colgroup>
          <tr><th>상 호</th><td>${mqIn("customer.name", "거래처 상호", "b")}</td><th rowspan="5" class="mqp-vert">공<br>급<br>자</th><th>등록번호</th><td>${mqSup("supplier_business_number")}</td></tr>
          <tr><th>담 당</th><td>${mqIn("customer.manager", "담당자", "w-mgr")} 귀하</td><th>상 &nbsp; 호</th><td>${mqSup("supplier_name")} <small>(대표: ${mqSup("supplier_manager")})</small></td></tr>
          <tr><th>연락처</th><td>${MQ_CONTACT()}</td><th>사 업 장</th><td>${mqSup("supplier_address")}</td></tr>
          <tr><th>내 용</th><td>${mqIn("title", "견적 제목 (비우면 '컴퓨터 견적')")}</td><th>연 락 처</th><td>${mqSup("supplier_phone")}</td></tr>
          <tr><th>일 자</th><td>${mqIn("quote_date")}</td><th>이 메 일</th><td>${mqSup("supplier_email")}</td></tr>
          <tr class="mqp-sum"><td colspan="2" class="c b" style="background:#E7E6E6">합계금액 (VAT 포함)</td><td colspan="3" class="r b big" style="color:#1F4E79">${mqTot("₩ ")}</td></tr>
        </table>`;
    default:
      return "";
  }
}

function mqPaperBottom(n, ncol) {
  const sumRow = (label, labelSpan, prefix = "") =>
    `<tr class="mqp-sum"><td colspan="${labelSpan}" class="c b">${label}</td><td colspan="${ncol - labelSpan}" class="r b big">${mqTot(prefix)}</td><td class="mqp-x"></td></tr>`;
  const right = (label, value, cls = "") =>
    `<tr class="mqp-flat-sum ${cls}"><td colspan="${ncol - 1}" class="r b">${label}</td><td class="r b">${value}</td><td class="mqp-x"></td></tr>`;
  const bank = quoteInfo?.bank_account ? `<div class="mqp-note mqp-muted">■ 입금 계좌 : ${escapeHtml([quoteInfo.bank_name, quoteInfo.bank_account].filter(Boolean).join(" "))}${quoteInfo.bank_holder ? ` (예금주 : ${escapeHtml(quoteInfo.bank_holder)})` : ""}</div>` : "";
  switch (n) {
    case 1:
      return { foot: sumRow("합계 (VAT 포함)", ncol - 1), after: `<div class="mqp-note b" style="color:#000080">※ 윈도우, 오피스 별도 · 기술지원비 별도청구</div>${bank}` };
    case 2:
      return { foot: right("SUBTOTAL", mqTot()) + right("TAX (VAT)", "Included") + right("TOTAL DUE", mqTot(), "big"), after: `${bank}<div class="mqp-note c" style="font-style:italic">Thank you for your business!</div>` };
    case 3:
    case 4:
      return { foot: "", after: `<div class="mqp-note mqp-muted">※ 윈도우, 오피스 별도 · 기술지원비 별도청구</div>${bank}` };
    case 5:
      return { foot: right("TOTAL", mqTot(), "big navy"), after: `${bank}<div class="mqp-note c">${mqSup("supplier_name")} / 담당: ${mqSup("supplier_manager")} / Tel: ${mqSup("supplier_phone")} / E-mail: ${mqSup("supplier_email")}</div>` };
    case 6:
      return { foot: sumRow("합 계 금 액 (VAT 포함)", 4, "₩ "), after: `<div class="mqp-sec">3. 유의사항 및 안내사항</div>
        <div class="mqp-note mqp-muted">1) 본 견적서는 발행일로부터 3일간 유효하며, 부품 시장 시세 변동에 따라 단가가 변경될 수 있습니다.<br>2) 모든 부품은 100% 정품/새제품만 사용하며, 조립 완료 후 남은 정품 박스 및 구성품은 모두 함께 제공해 드립니다.<br>3) 무상 A/S 기간은 부품 제조사/수입사 기준이며, 매장 자체 초기불량 보증 기간은 구매일로부터 14일입니다.<br>4) 결제 방식: 현금영수증 및 세금계산서 발행 가능 (계좌입금 / 카드결제 동일)</div>${bank}` };
    case 7:
      return { foot: "", after: `<div class="mqp-note b">※ 제품 발주시 아래 발주확인 란 작성 후 회신 바랍니다.</div>
        <div class="mqp-flex"><span>발주 일자 : &nbsp; &nbsp; 년 &nbsp; 월 &nbsp; 일</span><span>상호/성명 : <span data-mq-text="customer.name"></span></span><span>서명/직인 : &nbsp; &nbsp; &nbsp; (인)</span></div>${bank}
        <div class="mqp-note c mqp-muted">영업담당: ${mqSup("supplier_manager")} / Tel: ${mqSup("supplier_phone")} / E-mail: ${mqSup("supplier_email")}</div>` };
    case 8:
      return { foot: sumRow("TOTAL (부가세 포함)", ncol - 1, "₩ "), after: `<div class="mqp-note b">■ 첨부 및 안내사항</div><div class="mqp-note">1. 납품일자 : 기간 협의 &nbsp; | &nbsp; 2. 유효기간 : 견적일로부터 7일<br>3. 결제조건 : 선결제 (세금계산서 발행)</div>${bank}` };
    default:
      return { foot: "", after: "" };
  }
}

/** 고른 서식 모양으로 종이 전체를 다시 그린다. items 는 지금까지 입력한 품목(빈 행 포함 가능). */
function renderMqPaper(items = []) {
  const paper = $("#mqPaper");
  if (!paper) return;
  const n = Number($("#mqForm")?.value) || 1;
  const cfg = MQ_PAPER[n];
  const ncol = cfg.cols.length;
  const { foot, after } = mqPaperBottom(n, ncol);
  paper.className = `mq-paper mqp-f${n}${cfg.flat ? " mqp-flat" : ""}`;
  paper.style.setProperty("--mqp-line", cfg.line || "#dddddd");
  paper.innerHTML = `${mqPaperTop(n)}
    <table class="mqp-grid mqp-items mqp-rows"><colgroup>${cfg.cols.map((c) => `<col${MQ_COL_WIDTH[c] ? ` style="width:${MQ_COL_WIDTH[c]}"` : ""}>`).join("")}<col style="width:26px"></colgroup>
      <thead><tr>${cfg.heads.map((h, i) => `<th style="${cfg.th}"${cfg.cols[i] === "unitaux" ? ' class="mqp-aux-h" title="이 서식에는 단가 열이 없습니다. 금액(수량×단가) 계산용 입력칸입니다"' : ""}>${h}</th>`).join("")}<th class="mqp-x"></th></tr></thead>
      <tbody id="mqItemsTbody"></tbody>
      ${foot ? `<tfoot>${foot}</tfoot>` : ""}
    </table>
    <div class="mqp-addrow"><button type="button" id="mqBtnAddRow" class="btn btn-xs btn-outline" title="품목 행을 하나 더 추가합니다 (최대 ${MQ_MAX_ROWS}행)">＋ 행추가</button></div>
    ${after}`;
  $("#mqBtnAddRow").addEventListener("click", () => {
    const cur = getMqItems({ keepEmpty: true });
    if (cur.length >= MQ_MAX_ROWS) return;
    cur.push({ category: "", name: "", qty: 1, unit_price: 0 });
    renderMqRows(cur);
  });
  renderMqRows(items);
  mqSyncBound();
}

function renderMqRows(items = []) {
  const tbody = $("#mqItemsTbody");
  if (!tbody) return;
  const cfg = MQ_PAPER[Number($("#mqForm")?.value) || 1];
  const rows = items.map((it) => ({ ...it }));
  while (rows.length < MQ_MIN_ROWS) rows.push({ category: "", name: "", qty: 1, unit_price: 0 });
  const cell = (key, it, idx) => {
    const filled = it.name || it.unit_price;
    const cat = `<input type="text" class="mqp-in mq-cat" value="${escapeHtml(it.category || "")}" placeholder="구분" />`;
    const name = `<input type="text" class="mqp-in mq-name" value="${escapeHtml(it.name || "")}" placeholder="품명 / 상세 모델명" />`;
    const price = `<input type="text" inputmode="numeric" class="mqp-in mq-price r" value="${it.unit_price ? mqFmt(it.unit_price) : ""}" placeholder="0" />`;
    switch (key) {
      case "no": return `<td class="c">${idx + 1}</td>`;
      case "cat": return `<td class="mqp-cat">${cat}</td>`;
      case "name": return `<td>${name}</td>`;
      case "catname": return `<td><div class="mqp-catname">[${cat}] ${name}</div></td>`;
      case "qty": return `<td><input type="text" inputmode="numeric" class="mqp-in mq-qty c" value="${filled ? (it.qty || 1) : ""}" placeholder="1" /></td>`;
      case "unit": return `<td>${price}</td>`;
      case "unitaux": return `<td class="mqp-aux">${price}</td>`;
      case "amount": return `<td class="r mq-amount"></td>`;
      case "as": return `<td class="c mqp-auto mq-as"></td>`;
      case "remark": return `<td class="c mqp-auto mq-remark"></td>`;
      default: return "<td></td>";
    }
  };
  tbody.innerHTML = rows.slice(0, MQ_MAX_ROWS).map((it, idx) =>
    `<tr class="mq-row">${cfg.cols.map((k) => cell(k, it, idx)).join("")}<td class="mqp-x"><button type="button" class="mqp-del" data-idx="${idx}" title="이 행 삭제">✕</button></td></tr>`).join("");
  const addBtn = $("#mqBtnAddRow");
  if (addBtn) addBtn.disabled = rows.length >= MQ_MAX_ROWS;
  calcMqTotal();
}

function getMqItems({ keepEmpty = false } = {}) {
  const items = [...document.querySelectorAll("#mqItemsTbody .mq-row")].map((tr) => ({
    category: tr.querySelector(".mq-cat").value.trim(),
    name: tr.querySelector(".mq-name").value.trim(),
    qty: Math.max(1, Number(mqDigits(tr.querySelector(".mq-qty").value)) || 1),
    unit_price: Number(mqDigits(tr.querySelector(".mq-price").value)) || 0,
  }));
  return keepEmpty ? items : items.filter((it) => it.name);
}

function calcMqTotal() {
  let total = 0;
  document.querySelectorAll("#mqItemsTbody .mq-row").forEach((tr) => {
    const qty = Math.max(1, Number(mqDigits(tr.querySelector(".mq-qty").value)) || 1);
    const price = Number(mqDigits(tr.querySelector(".mq-price").value)) || 0;
    const cat = tr.querySelector(".mq-cat").value.trim();
    const named = tr.querySelector(".mq-name").value.trim();
    const amount = named || price ? qty * price : 0;
    tr.querySelector(".mq-amount").textContent = named || price ? mqFmt(amount) : "";
    const as = tr.querySelector(".mq-as");
    if (as) as.textContent = named ? mqWarranty(cat) : "";
    const remark = tr.querySelector(".mq-remark");
    if (remark) remark.textContent = named ? mqRemark(cat, named) : "";
    if (named) total += amount;
  });
  document.querySelectorAll("#mqPaper [data-mq-calc='total']").forEach((el) => {
    el.textContent = `${el.dataset.prefix || ""}${mqFmt(total)}${el.dataset.suffix || ""}`;
  });
  document.querySelectorAll("#mqPaper [data-mq-calc='supply']").forEach((el) => {
    el.textContent = mqFmt(Math.round(total / 1.1)); // excel_export 폼7 공급가 = round(합계 / 1.1)
  });
  return total;
}

function getMqPayloadQuote() {
  return {
    id: mqCurrentId,
    form: Number($("#mqForm").value) || 1,
    title: (mqMeta.title || "").trim(),
    quote_date: mqMeta.quote_date,
    customer_info: { ...mqMeta.customer },
    items: getMqItems(),
  };
}

function syncMqHeader() {
  const n = Number($("#mqForm")?.value) || 1;
  $("#mqTitle").textContent = `📝 새 견적 만들기 — 폼${n} (${EXCEL_FORM_LABELS[n]})`;
  const badge = $("#mqStatusBadge");
  if (badge) badge.textContent = mqCurrentId ? "저장된 견적 수정 중" : "새 견적";
}

/** rec 이 있으면 저장된 견적을 열고, 없으면 form 서식으로 빈 견적을 연다. 견적처 기본값은 견적정보에 지정된 거래처. */
async function openMqEditor(rec, form = 1) {
  const sel = $("#mqForm");
  if (sel && !sel.childElementCount) {
    sel.innerHTML = Object.entries(EXCEL_FORM_LABELS).map(([n, l]) => `<option value="${n}">폼${n} · ${l}</option>`).join("");
  }
  mqCurrentId = rec?.id || "";
  sel.value = String(rec?.form || form || 1);
  const saved = rec?.customer_info;
  mqMeta = {
    title: rec?.title || "",
    quote_date: rec?.quote_date || new Date().toLocaleDateString("sv-SE"), // YYYY-MM-DD (로컬 날짜)
    // 예전 저장본(견적처 칸 없음)은 견적정보의 견적처로 연다
    customer: saved && Object.values(saved).some((v) => String(v || "").trim()) ? { ...mqCustomerFromInfo(), ...saved } : mqCustomerFromInfo(),
  };
  $("#mqUnitPrice").checked = showUnitPrices;
  renderMqPaper(rec?.items || []);
  showMqEdit();
  syncMqHeader();
  renderMqCustPick();
  show(mqModal);
  $("#mqQuoteTitle").focus();
  if (!custList.length) {
    await loadCustomers();
    renderMqCustPick();
  }
}

function mqApplyCustomer(c) {
  mqMeta.customer = {
    name: (c?.name || "").trim(),
    manager: (c?.manager || "").trim(),
    phone: (c?.phone || "").trim(),
    email: (c?.email || "").trim(),
    address: (c?.address || "").trim(),
  };
  mqSyncBound();
  renderMqCustPick();
}

async function saveMqCustomer() {
  const data = { ...mqMeta.customer };
  if (!data.name) {
    alert("견적처 상호를 먼저 입력하세요.");
    return;
  }
  try {
    const res = await postJsonRaw("/api/customers", data);
    if (!res.ok) throw new Error(res.data?.detail || "거래처 저장에 실패했습니다.");
  } catch (e) {
    alert("거래처 저장 오류: " + e.message);
    return;
  }
  await loadCustomers();
  renderMqCustPick();
  alert(`📒 '${data.name}' 거래처를 목록에 저장했습니다.`);
}

// 종이 · 상단 칸 입력을 한 곳에서 받는다(다시 그려도 리스너가 남도록 위임)
$("#mqEditPane")?.addEventListener("input", (e) => {
  const el = e.target;
  if (el.dataset?.mq) {
    mqSet(el.dataset.mq, el.value);
    mqSyncBound(el);
    if (el.dataset.mq === "customer.name") renderMqCustPick();
    return;
  }
  if (el.classList.contains("mq-price")) {
    const d = mqDigits(el.value).slice(0, 12);
    el.value = d ? mqFmt(Number(d)) : "";
  } else if (el.classList.contains("mq-qty")) {
    el.value = mqDigits(el.value).slice(0, 4);
  }
  if (el.closest("#mqItemsTbody")) calcMqTotal();
});
$("#mqEditPane")?.addEventListener("click", (e) => {
  const del = e.target.closest(".mqp-del");
  if (!del) return;
  const cur = getMqItems({ keepEmpty: true });
  cur.splice(Number(del.dataset.idx), 1);
  renderMqRows(cur);
});
$("#mqCustPick")?.addEventListener("change", (e) => {
  const c = custList.find((x) => x.id === e.target.value);
  if (c) mqApplyCustomer(c);
});
$("#mqCustReset")?.addEventListener("click", () => mqApplyCustomer(mqCustomerFromInfo()));
$("#mqCustSave")?.addEventListener("click", saveMqCustomer);

function closeMqEditor() {
  mqPreviewSeq += 1;
  hide(mqModal);
}

async function saveMq() {
  const quote = getMqPayloadQuote();
  if (!quote.items.length) {
    alert("품목을 한 줄 이상 입력하세요(품명이 빈 줄은 저장되지 않습니다).");
    return;
  }
  showBusy("견적 저장 중…");
  try {
    const res = await postJsonRaw("/api/manual-quotes", quote);
    if (!res.ok) throw new Error(res.data?.detail || `저장 실패 (${res.status})`);
    mqCurrentId = res.data.quote.id;
    syncMqHeader();
    loadMqList();
  } catch (e) {
    alert("저장 오류: " + e.message);
    return;
  } finally {
    hideBusy();
  }
  alert("✔ 견적을 저장했습니다. ⚙️ 설정관리 → 🗂️ 견적서 관리 목록(📝 작성본)에서 다시 열 수 있습니다.");
}

function mqExcelBody(quote, extra = {}) {
  return JSON.stringify({ quote, unitprice: $("#mqUnitPrice")?.checked === false ? 0 : 1, ...extra });
}

async function downloadMqExcel(rec) {
  const quote = rec || getMqPayloadQuote();
  if (!quote.items?.length) {
    alert("품목을 한 줄 이상 입력하세요.");
    return;
  }
  const n = quote.form || 1;
  await downloadWithBusy("/api/manual-quotes/excel", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: mqExcelBody(quote, { archive: 1 }),
  }, `견적서_폼${n}.xlsx`, `폼${n} ${EXCEL_FORM_LABELS[n]} 견적서 생성 중…`);
}

async function emailMq() {
  const quote = getMqPayloadQuote();
  if (!quote.items.length) {
    alert("품목을 한 줄 이상 입력하세요.");
    return;
  }
  showBusy("견적서를 첨부한 메일 준비 중…");
  let data;
  try {
    const res = await fetch("/api/manual-quotes/excel", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: mqExcelBody(quote, { email: 1, archive: 1 }),
    });
    data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `메일 준비 실패 (${res.status})`);
  } catch (e) {
    alert("이메일 오류: " + e.message);
    return;
  } finally {
    hideBusy();
  }
  openMailDraft(data);
}

function showMqEdit() {
  mqPreviewSeq += 1;
  show($("#mqEditPane"));
  hide($("#mqPreviewPane"));
}

/** 고른 서식으로 만든 견적서 PDF 를 이 창 안에 띄운다(새 창을 열지 않는다). */
async function previewMq() {
  const quote = getMqPayloadQuote();
  if (!quote.items.length) {
    alert("품목을 한 줄 이상 입력하세요.");
    return;
  }
  const seq = ++mqPreviewSeq;
  const n = quote.form;
  $("#mqPreviewLabel").textContent = `폼${n} (${EXCEL_FORM_LABELS[n]}) 미리보기 — 인쇄는 미리보기 안의 🖨️ 버튼으로 합니다`;
  const view = $("#mqPreviewView");
  view.innerHTML = `<div class="fg-detail-msg">⏳ 견적서를 만드는 중… (Excel → PDF)</div>`;
  hide($("#mqEditPane"));
  show($("#mqPreviewPane"));
  showBusy(`폼${n} ${EXCEL_FORM_LABELS[n]} 견적서 미리보기 만드는 중…`);
  try {
    const res = await fetch("/api/manual-quotes/excel", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: mqExcelBody(quote, { pdf: 1 }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `미리보기 실패 (${res.status})`);
    }
    const blob = await res.blob();
    if (seq !== mqPreviewSeq) return; // 그 사이 편집으로 돌아갔거나 창을 닫았다
    if (mqPreviewUrl) URL.revokeObjectURL(mqPreviewUrl);
    mqPreviewUrl = URL.createObjectURL(blob);
    view.innerHTML = `<iframe class="fg-pdf" src="${mqPreviewUrl}#view=FitH" title="견적서 미리보기"></iframe>`;
  } catch (e) {
    if (seq === mqPreviewSeq) {
      view.innerHTML = `<div class="fg-detail-msg fg-detail-err">⚠️ 미리보기를 만들지 못했습니다: ${escapeHtml(e.message)}<br>📥 Excel 다운로드 · ✉️ 이메일은 그대로 쓸 수 있습니다.</div>`;
    }
  } finally {
    hideBusy();
  }
}

// 서식을 바꾸면 입력한 품목 · 견적처를 그대로 둔 채 종이 모양만 바꾼다
$("#mqForm")?.addEventListener("change", () => {
  const items = getMqItems({ keepEmpty: true });
  syncMqHeader();
  showMqEdit();
  renderMqPaper(items);
});
$("#mqBtnNew")?.addEventListener("click", () => {
  if (confirm("지금 서식으로 빈 견적을 새로 쓸까요? (저장하지 않은 내용은 지워집니다)")) openMqEditor(null, Number($("#mqForm").value));
});
$("#mqBtnSave")?.addEventListener("click", saveMq);
$("#mqBtnPreview")?.addEventListener("click", previewMq);
$("#mqPreviewBack")?.addEventListener("click", showMqEdit);
$("#mqBtnExcel")?.addEventListener("click", () => downloadMqExcel());
$("#mqBtnEmail")?.addEventListener("click", emailMq);
$("#mqClose")?.addEventListener("click", closeMqEditor);
$("#mqBtnClose")?.addEventListener("click", closeMqEditor);
mqModal?.addEventListener("click", (e) => {
  if (e.target === mqModal) closeMqEditor();
});
loadMqList(); // 🗂️ 견적서 관리 탭의 건수 표시

// ---------- 독립 창 계산기 ----------
const CALC_POPUP_NAME = "quoteCalcPopup";
const CALC_POPUP_W = 280;
const CALC_POPUP_H = 540;
let calcWin = null;

function calcPopupPos() {
  const availLeft = window.screen.availLeft || 0;
  const availTop = window.screen.availTop || 0;
  const availW = window.screen.availWidth || window.screen.width;
  const availH = window.screen.availHeight || window.screen.height;
  let left = Math.round((window.screenX || 0) + (window.outerWidth || 0) + 8);
  let top = Math.round((window.screenY || 0) + 72);
  if (left + CALC_POPUP_W > availLeft + availW) left = availLeft + availW - CALC_POPUP_W - 8;
  if (left < availLeft) left = availLeft + 8;
  if (top + CALC_POPUP_H > availTop + availH) top = availTop + availH - CALC_POPUP_H - 8;
  if (top < availTop) top = availTop + 8;
  return { left, top, width: CALC_POPUP_W, height: CALC_POPUP_H };
}

function closeCalcWindow() {
  try {
    if (calcWin && !calcWin.closed) calcWin.close();
  } catch (_) {}
  calcWin = null;
}

function placeCalcWindow(win, pos) {
  if (!win || win.closed) return;
  try {
    win.resizeTo(pos.width, pos.height);
    win.moveTo(pos.left, pos.top);
  } catch (_) {}
}

async function openCalcWindow() {
  const pos = calcPopupPos();
  try {
    const res = await fetch("/api/calc/open", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(pos),
    });
    if (res.ok) return;
  } catch (_) {}
  if (calcWin && !calcWin.closed) {
    try { calcWin.focus(); } catch (_) {}
    return;
  }
  const features = [
    "popup=true",
    "width=" + pos.width,
    "height=" + pos.height,
    "left=" + pos.left,
    "top=" + pos.top,
    "resizable=yes",
    "scrollbars=no",
  ].join(",");
  const win = window.open("/calc", CALC_POPUP_NAME, features);
  if (!win || win === window) {
    alert("계산기 창이 차단되었습니다. 브라우저에서 이 사이트의 팝업을 허용해 주세요.");
    return;
  }
  calcWin = win;
  placeCalcWindow(win, pos);
  try { calcWin.focus(); } catch (_) {}
}

$("#btnOpenCalc")?.addEventListener("click", openCalcWindow);
