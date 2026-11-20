/* 🗒️ 메모장 창 (templates/memo.html, 서버 app/memo_book.py · /api/memos)
 * 메인창의 [🗒️ 메모장] 이 계산기처럼 별도 --app 창(app/calc_window.py open_memo_window)으로 연다. 창 크기는 마우스로 조절.
 * 왼쪽 목록(검색 · 📌 고정 먼저) | 오른쪽 작성. 그림은 Ctrl+V 붙여넣기 · 끌어놓기 · 파일 선택으로 붙이고
 * 💾 저장 때 메모 한 건을 통째로 보낸다(새 그림은 data URL, 이미 저장된 그림은 file 이름).
 */
"use strict";

const $ = (sel) => document.querySelector(sel);
const MEMO_MAX_IMAGE_BYTES = 15 * 1024 * 1024; // app/memo_book.py 의 MAX_IMAGE_BYTES 와 같게

let memoItems = [];
let memoCur = null; // 지금 연 저장본(새 메모면 null)
let memoImgs = []; // [{file, name}] 저장된 그림 | [{data, name}] 아직 저장 안 한 그림
let memoDirty = false;
let memoMaxImages = 30;

function escapeHtml(str) {
  return String(str ?? "")
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
async function postJsonRaw(url, body) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  const data = await res.json().catch(() => ({}));
  return { status: res.status, ok: res.ok, data };
}
// 처리 중 표시(머리줄 작은 글자)
function setBusy(text) {
  const b = $("#memoBusy");
  if (!b) return;
  b.textContent = text ? `⏳ ${text}` : "";
  b.classList.toggle("hidden", !text);
  document.body.classList.toggle("is-busy", !!text);
}

function memoFirstLine(text) {
  return (String(text || "").split(/\r?\n/).find((l) => l.trim()) || "").trim();
}
function memoLabel(m) {
  return (m.title || "").trim() || memoFirstLine(m.body).slice(0, 60) || (m.images?.length ? "🖼️ 그림 메모" : "(빈 메모)");
}
function memoImgSrc(img) {
  return img.data || `/api/memos/images/${encodeURIComponent(img.file)}`;
}
function setMemoDirty(v) {
  memoDirty = v;
  const st = $("#memoState");
  if (st) {
    st.textContent = v ? "● 저장 안 됨" : memoCur ? `저장됨 · ${memoCur.updated_at || ""}` : "새 메모";
    st.classList.toggle("is-dirty", v);
  }
}

async function loadMemos() {
  try {
    const res = await fetch("/api/memos", { cache: "no-store" });
    const data = await res.json();
    memoItems = data.memos || [];
    memoMaxImages = data.max_images || memoMaxImages;
  } catch (_) {
    memoItems = [];
  }
  renderMemoList();
}

function renderMemoList() {
  const ul = $("#memoList");
  if (!ul) return;
  const q = ($("#memoSearch")?.value || "").trim().toLowerCase();
  const rows = memoItems.filter((m) => !q || `${m.title || ""}\n${m.body || ""}`.toLowerCase().includes(q));
  const cnt = $("#memoCount");
  if (cnt) cnt.textContent = memoItems.length ? (q ? `${rows.length} / ${memoItems.length}건` : `${memoItems.length}건`) : "";
  if (!rows.length) {
    ul.innerHTML = `<li class="memo-empty">${memoItems.length ? "검색 결과가 없습니다." : "저장된 메모가 없습니다. 오른쪽에 쓰고 💾 저장하세요."}</li>`;
    return;
  }
  ul.innerHTML = rows.map((m) => {
    const lines = String(m.body || "").split(/\r?\n/).filter((l) => l.trim());
    const preview = (m.title ? lines[0] || "" : lines[1] || "").trim().slice(0, 80);
    const n = m.images?.length || 0;
    return `<li class="memo-item${memoCur?.id === m.id ? " is-active" : ""}" data-id="${escapeHtml(m.id)}">
      <div class="memo-item-top">
        <button type="button" class="memo-item-pin${m.pinned ? " on" : ""}" data-pin="${escapeHtml(m.id)}" title="${m.pinned ? "고정 풀기" : "맨 위에 고정"}">📌</button>
        <span class="memo-item-title">${escapeHtml(memoLabel(m))}</span>
        ${n ? `<span class="memo-item-img" title="그림 ${n}장">🖼️${n}</span>` : ""}
      </div>
      <div class="memo-item-sub"><span>${escapeHtml(preview)}</span><time>${escapeHtml((m.updated_at || "").slice(0, 16))}</time></div>
    </li>`;
  }).join("");
}

function renderMemoImages() {
  const box = $("#memoImages");
  if (!box) return;
  box.innerHTML = memoImgs.map((img, i) => `
    <figure class="memo-thumb${img.data ? " is-new" : ""}" data-idx="${i}" title="${escapeHtml(img.name || "")} — 클릭하면 크게 봅니다">
      <img src="${escapeHtml(memoImgSrc(img))}" alt="" loading="lazy" />
      <button type="button" class="memo-thumb-x" data-del="${i}" title="이 그림 빼기">✕</button>
      <figcaption>${escapeHtml(img.name || "")}</figcaption>
    </figure>`).join("");
  $("#memoImgHint")?.classList.toggle("hidden", memoImgs.length > 0);
}

function fillMemoEditor(m) {
  memoCur = m || null;
  $("#memoTitle").value = m?.title || "";
  $("#memoBody").value = m?.body || "";
  $("#memoPinned").checked = !!m?.pinned;
  memoImgs = (m?.images || []).map((img) => ({ file: img.file, name: img.name || "" }));
  $("#memoBtnDelete").disabled = !m;
  renderMemoImages();
  setMemoDirty(false);
  renderMemoList();
}

function memoConfirmDiscard() {
  return !memoDirty || confirm("저장하지 않은 내용이 있습니다. 버리고 넘어갈까요?");
}

function readFileAsDataUrl(file) {
  return new Promise((resolve, reject) => {
    const fr = new FileReader();
    fr.onload = () => resolve(fr.result);
    fr.onerror = () => reject(fr.error);
    fr.readAsDataURL(file);
  });
}

async function addMemoImageFiles(files) {
  const list = Array.from(files || []).filter((f) => /^image\/(png|jpeg|gif|webp|bmp)$/i.test(f.type));
  if (!list.length) return;
  const room = memoMaxImages - memoImgs.length;
  if (room <= 0) {
    alert(`그림은 메모 한 건에 ${memoMaxImages}장까지 넣을 수 있습니다.`);
    return;
  }
  if (list.length > room) alert(`그림은 ${memoMaxImages}장까지라 앞의 ${room}장만 붙입니다.`);
  for (const f of list.slice(0, room)) {
    if (f.size > MEMO_MAX_IMAGE_BYTES) {
      alert(`'${f.name}'은(는) 15MB 를 넘어 붙이지 않습니다.`);
      continue;
    }
    try {
      const stamp = new Date().toTimeString().slice(0, 8).replace(/:/g, "");
      const ext = (f.type.split("/")[1] || "png").replace("jpeg", "jpg");
      const name = f.name && f.name !== "image.png" ? f.name : `붙여넣기_${stamp}.${ext}`;
      memoImgs.push({ data: await readFileAsDataUrl(f), name });
    } catch (_) {
      alert(`'${f.name}'을(를) 읽지 못했습니다.`);
    }
  }
  renderMemoImages();
  setMemoDirty(true);
}

async function saveMemo() {
  const body = {
    id: memoCur?.id || "",
    title: $("#memoTitle").value.trim(),
    body: $("#memoBody").value,
    pinned: $("#memoPinned").checked,
    images: memoImgs.map((img) => (img.file ? { file: img.file, name: img.name } : { data: img.data, name: img.name })),
  };
  if (!body.title && !body.body.trim() && !body.images.length) {
    alert("제목 · 내용 · 그림 중 하나는 있어야 저장합니다.");
    return false;
  }
  setBusy(body.images.some((i) => i.data) ? "메모 · 그림 저장 중…" : "메모 저장 중…");
  try {
    const res = await postJsonRaw("/api/memos", body);
    if (!res.ok) throw new Error(res.data?.detail || `저장 실패 (${res.status})`);
    await loadMemos();
    fillMemoEditor(memoItems.find((x) => x.id === res.data.memo.id) || res.data.memo);
    return true;
  } catch (e) {
    alert("메모 저장 오류: " + e.message);
    return false;
  } finally {
    setBusy("");
  }
}

async function deleteMemo() {
  if (!memoCur) return;
  if (!confirm(`'${memoLabel(memoCur)}' 메모를 지울까요? 붙인 그림도 함께 지워지며 되돌릴 수 없습니다.`)) return;
  try {
    const res = await fetch(`/api/memos/${encodeURIComponent(memoCur.id)}`, { method: "DELETE" });
    if (!res.ok && res.status !== 404) throw new Error(`삭제 실패 (${res.status})`);
  } catch (e) {
    alert(e.message);
    return;
  }
  memoDirty = false;
  await loadMemos();
  fillMemoEditor(null);
}

async function backupMemos() {
  if (memoDirty && !confirm("저장하지 않은 메모는 백업에 들어가지 않습니다. 그대로 백업할까요?")) return;
  setBusy("메모장 백업 중…");
  try {
    const r = await postJsonRaw("/api/memos/backup", {});
    if (!r.ok) throw new Error(r.data?.detail?.message || r.data?.detail || `백업 실패 (${r.status})`);
    alert(`✔ 백업했습니다 (메모 ${r.data.count}건 · 그림 ${r.data.files}개)\n${r.data.path}`);
  } catch (e) {
    alert("백업 오류: " + e.message);
  } finally {
    setBusy("");
  }
}

function showMemoViewer(img) {
  const v = $("#memoViewer");
  if (!v || !img) return;
  $("#memoViewerImg").src = memoImgSrc(img);
  $("#memoViewerName").textContent = img.name || "";
  show(v);
}
function hideMemoViewer() {
  const v = $("#memoViewer");
  if (v && !v.classList.contains("hidden")) {
    hide(v);
    $("#memoViewerImg").removeAttribute("src");
    return true;
  }
  return false;
}

function closeMemoWindow() {
  if (!memoConfirmDiscard()) return;
  memoDirty = false; // beforeunload 경고를 다시 띄우지 않게
  try {
    window.close();
  } catch (_) {}
}

$("#memoClose")?.addEventListener("click", closeMemoWindow);
$("#memoBtnBackup")?.addEventListener("click", backupMemos);
$("#memoBtnNew")?.addEventListener("click", () => {
  if (!memoConfirmDiscard()) return;
  fillMemoEditor(null);
  $("#memoTitle")?.focus();
});
$("#memoBtnSave")?.addEventListener("click", saveMemo);
$("#memoBtnDelete")?.addEventListener("click", deleteMemo);
$("#memoBtnAddImg")?.addEventListener("click", () => $("#memoFile")?.click());
$("#memoFile")?.addEventListener("change", async (e) => {
  await addMemoImageFiles(e.target.files);
  e.target.value = "";
});
$("#memoSearch")?.addEventListener("input", renderMemoList);
["#memoTitle", "#memoBody"].forEach((sel) => $(sel)?.addEventListener("input", () => setMemoDirty(true)));
$("#memoPinned")?.addEventListener("change", () => setMemoDirty(true));

$("#memoList")?.addEventListener("click", async (e) => {
  const pin = e.target.closest("[data-pin]");
  if (pin) {
    e.stopPropagation();
    const m = memoItems.find((x) => x.id === pin.dataset.pin);
    if (!m) return;
    const res = await postJsonRaw(`/api/memos/${encodeURIComponent(m.id)}/pin`, { pinned: !m.pinned });
    if (!res.ok) {
      alert(res.data?.detail || "고정을 바꾸지 못했습니다.");
      return;
    }
    await loadMemos();
    if (memoCur?.id === m.id) {
      memoCur = memoItems.find((x) => x.id === m.id) || memoCur;
      if (!memoDirty) $("#memoPinned").checked = !!memoCur.pinned;
    }
    return;
  }
  const li = e.target.closest(".memo-item");
  if (!li || li.dataset.id === memoCur?.id) return;
  if (!memoConfirmDiscard()) return;
  fillMemoEditor(memoItems.find((x) => x.id === li.dataset.id) || null);
});

$("#memoImages")?.addEventListener("click", (e) => {
  const del = e.target.closest("[data-del]");
  if (del) {
    memoImgs.splice(Number(del.dataset.del), 1);
    renderMemoImages();
    setMemoDirty(true);
    return;
  }
  const fig = e.target.closest(".memo-thumb");
  if (fig) showMemoViewer(memoImgs[Number(fig.dataset.idx)]);
});
$("#memoViewer")?.addEventListener("click", hideMemoViewer);

// 그림 붙여넣기(Ctrl+V) — 클립보드에 그림이 있을 때만 가로챈다(글 붙여넣기는 그대로)
document.addEventListener("paste", (e) => {
  const files = Array.from(e.clipboardData?.items || [])
    .filter((it) => it.kind === "file" && it.type.startsWith("image/"))
    .map((it) => it.getAsFile())
    .filter(Boolean);
  if (!files.length) return;
  e.preventDefault();
  addMemoImageFiles(files);
});
// 끌어놓기 — 창 어디에 놓아도 붙는다
document.addEventListener("dragover", (e) => {
  if (!Array.from(e.dataTransfer?.types || []).includes("Files")) return;
  e.preventDefault();
  $("#memoDrop")?.classList.add("is-over");
});
document.addEventListener("dragleave", (e) => {
  if (!e.relatedTarget) $("#memoDrop")?.classList.remove("is-over");
});
document.addEventListener("drop", (e) => {
  e.preventDefault(); // 그림을 놓쳐도 브라우저가 그 파일로 넘어가지 않게
  $("#memoDrop")?.classList.remove("is-over");
  if (e.dataTransfer?.files?.length) addMemoImageFiles(e.dataTransfer.files);
});
// Esc: 그림 크게 보기만 닫는다(창은 ✕ 닫기로). Ctrl+S: 저장.
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    if (hideMemoViewer()) e.preventDefault();
  } else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") {
    e.preventDefault();
    saveMemo();
  }
});
// 저장 안 한 채 창을 닫으려 하면 브라우저가 한 번 묻는다
window.addEventListener("beforeunload", (e) => {
  if (!memoDirty) return;
  e.preventDefault();
  e.returnValue = "";
});

(async () => {
  await loadMemos();
  fillMemoEditor(null);
  $("#memoBody")?.focus();
})();
