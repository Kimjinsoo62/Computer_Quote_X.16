# -*- coding: utf-8 -*-
"""🗒️ 메모장 — 글 + 이미지(붙여넣기 · 끌어놓기 · 파일 선택)를 메모 한 건으로 저장 · 관리한다.

    data/memos/memos.json      메모 목록 [{id, title, body, pinned, images: [{file, name}], created_at, updated_at}]
    data/memos/images/          이미지 파일 {메모 id}_{순번 6자리 난수}.{확장자}

화면은 저장할 때 메모 한 건을 통째로 보낸다. images 의 각 항목은 이미 저장된 그림이면 {"file": 이름},
새 그림이면 {"data": "data:image/png;base64,...", "name": 원래 이름}. 목록에서 빠진 옛 그림 파일은 지운다.
"""
from __future__ import annotations

import base64
import binascii
import json
import re
import secrets
import threading
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from app.data_paths import MEMO_IMAGE_DIR, MEMOS_FILE

MAX_IMAGES = 30  # 메모 한 건의 그림 수
MAX_IMAGE_BYTES = 15 * 1024 * 1024  # 그림 한 장
MAX_TITLE = 200
MAX_BODY = 100_000
IMAGE_TYPES = {"image/png": "png", "image/jpeg": "jpg", "image/gif": "gif", "image/webp": "webp", "image/bmp": "bmp"}
MEDIA_TYPES = {ext: mime for mime, ext in IMAGE_TYPES.items()}
_FILE_RE = re.compile(r"^[0-9A-Za-z_-]+\.(png|jpg|gif|webp|bmp)$")
_lock = threading.Lock()
NL, BS = chr(10), chr(92)  # 백업 README 글자(줄바꿈 · 역슬래시)


def _load() -> list[dict[str, Any]]:
    if MEMOS_FILE.exists():
        try:
            data = json.loads(MEMOS_FILE.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return [m for m in data if isinstance(m, dict) and m.get("id")]
        except (json.JSONDecodeError, OSError):
            pass
    return []


def _save(items: list[dict[str, Any]]) -> None:
    MEMOS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = MEMOS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(MEMOS_FILE)


def list_memos() -> list[dict[str, Any]]:
    """📌 고정한 메모가 먼저, 그다음 최근 수정 순."""
    with _lock:
        items = _load()
    items.sort(key=lambda m: m.get("updated_at", ""), reverse=True)
    items.sort(key=lambda m: not m.get("pinned"))
    return items


def image_path(file: str) -> Path | None:
    """저장된 그림 파일 경로. 이름 규칙에 맞지 않거나 없으면 None(경로 탈출 방지)."""
    if not _FILE_RE.match(file or ""):
        return None
    p = MEMO_IMAGE_DIR / file
    return p if p.is_file() else None


def _decode_image(data_url: str) -> tuple[bytes, str]:
    m = re.match(r"^data:(image/[a-z0-9.+-]+);base64,(.*)$", data_url or "", re.S | re.I)
    if not m:
        raise ValueError("그림 형식을 알 수 없습니다(PNG · JPG · GIF · WEBP · BMP 만 됩니다).")
    ext = IMAGE_TYPES.get(m.group(1).lower())
    if not ext:
        raise ValueError(f"지원하지 않는 그림 형식입니다: {m.group(1)}")
    try:
        raw = base64.b64decode(m.group(2), validate=False)
    except (binascii.Error, ValueError) as e:
        raise ValueError("그림 데이터를 읽지 못했습니다.") from e
    if not raw:
        raise ValueError("빈 그림입니다.")
    if len(raw) > MAX_IMAGE_BYTES:
        raise ValueError(f"그림 한 장은 {MAX_IMAGE_BYTES // (1024 * 1024)}MB 이하만 됩니다.")
    return raw, ext


def save_memo(data: dict[str, Any]) -> dict[str, Any]:
    """id 가 있으면 수정, 없거나 못 찾으면 새로 만든다. 제목 · 내용 · 그림이 모두 비면 거절."""
    title = str(data.get("title") or "").strip()[:MAX_TITLE]
    body = str(data.get("body") or "").rstrip()[:MAX_BODY]
    pinned = bool(data.get("pinned"))
    images_in = data.get("images") if isinstance(data.get("images"), list) else []
    if len(images_in) > MAX_IMAGES:
        raise ValueError(f"그림은 메모 한 건에 {MAX_IMAGES}장까지 넣을 수 있습니다.")
    if not title and not body.strip() and not images_in:
        raise ValueError("제목 · 내용 · 그림 중 하나는 있어야 저장합니다.")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _lock:
        items = _load()
        memo_id = str(data.get("id") or "").strip()
        found = next((m for m in items if m["id"] == memo_id), None) if memo_id else None
        if found is None:
            memo_id = datetime.now().strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(2)
            found = {"id": memo_id, "created_at": now, "images": []}
            items.append(found)
        old_files = {img.get("file") for img in found.get("images", []) if isinstance(img, dict)}
        MEMO_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
        new_images: list[dict[str, str]] = []
        written: list[Path] = []
        try:
            for img in images_in:
                if not isinstance(img, dict):
                    continue
                name = str(img.get("name") or "").strip()[:120]
                if img.get("file"):
                    f = str(img["file"])
                    if f in old_files and image_path(f):
                        new_images.append({"file": f, "name": name})
                    continue
                raw, ext = _decode_image(str(img.get("data") or ""))
                f = f"{memo_id}_{secrets.token_hex(3)}.{ext}"
                (MEMO_IMAGE_DIR / f).write_bytes(raw)
                written.append(MEMO_IMAGE_DIR / f)
                new_images.append({"file": f, "name": name or f"그림.{ext}"})
        except ValueError:
            for p in written:  # 중간에 실패하면 이번에 쓴 그림만 지우고 메모는 그대로
                p.unlink(missing_ok=True)
            raise
        found.update(title=title, body=body, pinned=pinned, images=new_images, updated_at=now)
        _save(items)
        keep = {img["file"] for img in new_images}
        for f in old_files - keep:
            p = image_path(f or "")
            if p:
                p.unlink(missing_ok=True)
    return found


def set_pinned(memo_id: str, pinned: bool) -> dict[str, Any] | None:
    with _lock:
        items = _load()
        found = next((m for m in items if m["id"] == memo_id), None)
        if found is None:
            return None
        found["pinned"] = bool(pinned)
        _save(items)
    return found


def delete_memo(memo_id: str) -> bool:
    with _lock:
        items = _load()
        found = next((m for m in items if m["id"] == memo_id), None)
        if found is None:
            return False
        items.remove(found)
        _save(items)
    for img in found.get("images", []):
        p = image_path(str(img.get("file") or "")) if isinstance(img, dict) else None
        if p:
            p.unlink(missing_ok=True)
    return True


def backup_memos(folder: str) -> dict[str, Any]:
    """메모 목록(memos.json) + 붙인 그림을 folder 에 ZIP 한 파일(메모장_백업_날짜_시각.zip)로 저장한다."""
    target = Path(folder)
    if not target.is_dir():
        raise FileNotFoundError(f"백업 폴더를 찾을 수 없습니다: {folder}")
    with _lock:
        items = _load()
        stamp = datetime.now()
        stem = f"메모장_백업_{stamp.strftime('%Y%m%d_%H%M%S')}"
        zip_path = target / f"{stem}.zip"
        seq = 2
        while zip_path.exists():
            zip_path = target / f"{stem}_{seq}.zip"
            seq += 1
        files = 0
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("memos.json", json.dumps(items, ensure_ascii=False, indent=2))
            for m in items:
                for img in m.get("images", []):
                    p = image_path(str(img.get("file") or "")) if isinstance(img, dict) else None
                    if p:
                        zf.write(p, f"images/{p.name}")
                        files += 1
            zf.writestr(
                "README.txt",
                (
                    "컴퓨존 견적 자동화 - 🗒️ 메모장 백업"
                    f"{NL}백업 시각: {stamp.strftime('%Y-%m-%d %H:%M:%S')}"
                    f"{NL}메모: {len(items)}건 / 그림 파일: {files}개{NL}{NL}"
                    f"memos.json : 메모 목록(복구할 때 프로그램 폴더의 data{BS}memos{BS} 에 덮어쓰기){NL}"
                    f"images/    : 메모에 붙인 그림(복구할 때 data{BS}memos{BS}images{BS} 에 복사){NL}"
                ),
            )
    return {"path": str(zip_path), "count": len(items), "files": files}
