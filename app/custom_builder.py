# -*- coding: utf-8 -*-
"""
405.kr 스타일 용도별 맞춤 PC 자동 견적 엔진 (컴퓨존 추천 부품 실시간 연동)
- 컴퓨존 온라인 견적 추천 부품(BigDivNo=4, PreOrder=recommand) 실시간 수집·스펙 파싱
- 용도(사무/게임/영상·3D/AI) · 해상도/목표 프레임 · 작업 프로그램 기반 최소 요구사양(floors) 역산
- 소켓 · 메모리 규격 · 전원부 · 정격파워(피크+여유율) · 쿨러 방열량 · 케이스 호환 자동 매칭
- 한 번의 요청으로 [최저가 견적 · 추천가 견적 · 최고가 견적] 3종 단가 견적 동시 생성
"""
from __future__ import annotations

import html as htmlmod
import json
import math
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from pathlib import Path
from typing import Any

from app.config import (
    BASE_PRODUCT_URL,
    CATEGORY_ORDER,
    COMPUZONE_ASSEMBLY_PART,
    COMPUZONE_PART_DIVS,
    ONLINE_LIST_URL,
    ONLINE_MAIN_URL,
)
from app.quote_engine import (
    Quote,
    QuotePart,
    TodayQuotesResult,
    USER_AGENT,
    _active_extras,
    _clean_part_name,
    _sort_key,
)

BASE_DIR = Path(__file__).resolve().parent.parent
from app.data_paths import PARTS_CACHE_FILE as CACHE_FILE  # data/cache/compuzone_parts_cache.json
PART_CATALOG_TTL_SEC = 15 * 60

_catalog_lock = threading.Lock()
_catalog_mem: dict[str, Any] = {"at": 0.0, "parts": {}, "synced_at": ""}


# ---------------------------------------------------------------------------
# 1. 용도 프리셋 · 게임 벤치 모델 · 작업 프로그램 정의 (405.kr 로직 기반)
# ---------------------------------------------------------------------------

USAGE_PRESETS: list[dict[str, Any]] = [
    {
        "id": "office",
        "label": "📄 사무 · 인강 · 매장용",
        "short": "사무/인강",
        "desc": "문서·엑셀·웹서핑·유튜브·원격업무 (내장그래픽 중심 실속 구성)",
        "need_gpu": False,
        "default_res": "FHD",
        "default_fps": 60,
        "floors": {
            "min": {"cores": 4, "gi": 0, "vram": 0, "ram": 16, "ssd": 500, "cpu_floor": 110},
            "rec": {"cores": 6, "gi": 0, "vram": 0, "ram": 16, "ssd": 1000, "cpu_floor": 145},
            "max": {"cores": 8, "gi": 0, "vram": 0, "ram": 32, "ssd": 1000, "cpu_floor": 180},
        },
        "method_title": "사무·인강용 구성 공식",
        "method_rules": [
            "외장 그래픽카드를 제외하고 내장그래픽(iGPU) 탑재 CPU로 전력·소음·비용을 절감합니다.",
            "다중 브라우저 창·엑셀 대용량 시트를 위해 최소 16GB 메모리를 기본 탑재합니다.",
            "체감 속도를 좌우하는 M.2 NVMe SSD(추천가 이상 TLC/DRAM 탑재)와 정격 500~600W 파워를 매칭합니다.",
        ],
    },
    {
        "id": "game_fhd",
        "label": "🎮 FHD 게이밍 (1080p)",
        "short": "FHD 게이밍",
        "desc": "롤·발로란트·배그·오버워치2·로스트아크·FC온라인 144Hz 쾌적 구동",
        "need_gpu": True,
        "default_res": "FHD",
        "default_fps": 144,
        "default_games": ["pubg", "lol", "valorant"],
        "floors": {
            "min": {"cores": 6, "gi": 58, "vram": 6, "ram": 16, "ssd": 500, "cpu_floor": 165},
            "rec": {"cores": 6, "gi": 110, "vram": 8, "ram": 32, "ssd": 1000, "cpu_floor": 195},
            "max": {"cores": 8, "gi": 135, "vram": 12, "ram": 32, "ssd": 1000, "cpu_floor": 265},
        },
        "method_title": "FHD 게이밍 구성 공식",
        "method_rules": [
            "FHD(1920×1080) 144Hz 방어를 위해 예산의 중심을 메인스트림 그래픽카드(RTX 3050/5060/5060 Ti 급)에 배분합니다.",
            "온라인 게임의 하위 1% 프레임 방어를 위해 L3 캐시 효율이 높은 6~8코어 게이밍 CPU(최고가 X3D)를 매칭합니다.",
            "과지출 방지 게이트: 그래픽카드 체급 대비 과도한 다코어 작업용 CPU 투입을 막고 GPU 체급을 우선 올립니다.",
        ],
    },
    {
        "id": "game_qhd",
        "label": "🔥 QHD 고주사율 게이밍 (1440p)",
        "short": "QHD 게이밍",
        "desc": "배그·디아블로4·몬헌 와일즈·스팀 AAA 게임 QHD 144fps+ 고옵션",
        "need_gpu": True,
        "default_res": "QHD",
        "default_fps": 144,
        "default_games": ["pubg", "diablo4", "mhwilds"],
        "floors": {
            "min": {"cores": 6, "gi": 110, "vram": 8, "ram": 32, "ssd": 1000, "cpu_floor": 195},
            "rec": {"cores": 8, "gi": 140, "vram": 12, "ram": 32, "ssd": 1000, "cpu_floor": 255},
            "max": {"cores": 8, "gi": 205, "vram": 16, "ram": 32, "ssd": 2000, "cpu_floor": 295},
        },
        "method_title": "QHD 고주사율 게이밍 구성 공식",
        "method_rules": [
            "QHD 해상도는 FHD 대비 픽셀 수가 1.78배 많아 VRAM 12~16GB 이상의 퍼포먼스급 GPU(RTX 5060 Ti/5070/RX 9070 XT/5070 Ti)가 핵심입니다.",
            "고주사율 병목 해소를 위해 3D V-Cache(7800X3D/9800X3D) 및 DDR5 32GB 메모리를 적극 배치합니다.",
            "순간 피크 전력을 견디도록 ATX 3.1 규격 정격 750~850W 파워와 타워형/360mm 수랭 쿨러를 자동 지정합니다.",
        ],
    },
    {
        "id": "game_4k",
        "label": "🚀 4K UHD · 하이엔드 게이밍",
        "short": "4K 하이엔드",
        "desc": "4K 고해상도 AAA 패키지 · 레이트레이싱 · DLSS/FG 풀옵션",
        "need_gpu": True,
        "default_res": "UHD",
        "default_fps": 120,
        "default_games": ["cyberpunk", "wukong", "mhwilds"],
        "floors": {
            "min": {"cores": 8, "gi": 170, "vram": 12, "ram": 32, "ssd": 1000, "cpu_floor": 240},
            "rec": {"cores": 8, "gi": 210, "vram": 16, "ram": 32, "ssd": 2000, "cpu_floor": 275},
            "max": {"cores": 8, "gi": 300, "vram": 16, "ram": 64, "ssd": 2000, "cpu_floor": 305},
        },
        "method_title": "4K UHD 하이엔드 게이밍 구성 공식",
        "method_rules": [
            "4K(3840×2160) 고해상도 텍스처 처리를 위해 VRAM 16GB+ 하이엔드 GPU(RTX 5070 Ti / RX 9070 XT / RTX 5090 급)를 최우선 배치합니다.",
            "최상위 게이밍 CPU(7800X3D / 9800X3D / 9950X3D)와 고페이즈 전원부 방열판 메인보드(B850/X870E)를 결합합니다.",
            "12V-2×6(ATX 3.1) 네이티브 850~1000W 골드 풀모듈러 파워와 360mm 수랭 쿨러·전면 메쉬 케이스로 발열을 제어합니다.",
        ],
    },
    {
        "id": "creator",
        "label": "🎬 영상편집 · 그래픽 · 3D 작업",
        "short": "영상/3D/디자인",
        "desc": "프리미어 프로·애프터이펙트·포토샵·블렌더·오토캐드·솔리드웍스",
        "need_gpu": True,
        "prefer_nvidia": True,
        "default_res": "QHD",
        "default_fps": 60,
        "default_apps": ["premiere_4k", "photoshop", "aftereffects"],
        "floors": {
            "min": {"cores": 6, "gi": 80, "vram": 8, "ram": 32, "ssd": 1000, "cpu_floor": 180},
            "rec": {"cores": 8, "gi": 130, "vram": 12, "ram": 32, "ssd": 1000, "cpu_floor": 220},
            "max": {"cores": 12, "gi": 210, "vram": 16, "ram": 64, "ssd": 2000, "cpu_floor": 260},
        },
        "method_title": "영상편집·3D 크리에이터 구성 공식",
        "method_rules": [
            "타임라인 프리뷰·렌더링 단축을 위해 다코어 CPU(8~16코어)와 대용량 메모리(32~64GB)를 우선 확보합니다.",
            "H.264/HEVC 하드웨어 가속(NVENC) 및 CUDA 렌더링 호환성이 뛰어난 NVIDIA 지포스 RTX 그래픽카드를 우선 선정합니다.",
            "대용량 미디어 캐시 쓰기 속도 저하를 막기 위해 DRAM 캐시가 탑재된 1군 TLC NVMe SSD를 배치합니다.",
        ],
    },
    {
        "id": "ai_work",
        "label": "🤖 AI · LLM · 딥러닝 워크스테이션",
        "short": "AI/딥러닝/개발",
        "desc": "로컬 LLM 구동·스테이블 디퓨전·파이토치 학습·대용량 VRAM 연산",
        "need_gpu": True,
        "prefer_nvidia": True,
        "default_res": "QHD",
        "default_fps": 60,
        "default_apps": ["local_llm", "coding_docker"],
        "floors": {
            "min": {"cores": 6, "gi": 110, "vram": 12, "ram": 32, "ssd": 1000, "cpu_floor": 185},
            "rec": {"cores": 8, "gi": 140, "vram": 16, "ram": 32, "ssd": 2000, "cpu_floor": 225},
            "max": {"cores": 16, "gi": 215, "vram": 16, "ram": 64, "ssd": 4000, "cpu_floor": 270},
        },
        "method_title": "AI·LLM 워크스테이션 구성 공식",
        "method_rules": [
            "AI 모델 가중치 적재를 위해 CUDA 가속이 가능한 NVIDIA RTX GPU 중 VRAM 용량(12GB → 16GB → 32GB)이 큰 모델을 최우선 선정합니다.",
            "모델 로딩 및 데이터 전처리를 위해 DDR5 32~64GB 메모리와 고속 Gen4/Gen5 NVMe SSD(1~4TB)를 매칭합니다.",
            "장시간 풀로드 연산 시 스로틀링과 전원 불안정을 막기 위해 80PLUS 골드 ATX 3.1 파워와 고성능 쿨러를 강제합니다.",
        ],
    },
]

USAGE_PRESET_MAP: dict[str, dict[str, Any]] = {p["id"]: p for p in USAGE_PRESETS}

# 해상도 메타데이터 (405.kr resInfo 대응)
# 용도별로 하단 세부 선택에 보일 것: 게임(+ 해상도 · 주사율) 칸을 보일지, 어떤 작업 프로그램을 보일지.
# 화면은 이 목록만 보여 주고, 견적 계산도 여기 없는 게임 · 프로그램은 뺀다(숨겨진 선택이 결과에 섞이지 않게).
USAGE_DETAIL: dict[str, dict[str, Any]] = {
    "office": {"games": False, "apps": ["office_doc", "web_multi"]},
    "game_fhd": {"games": True, "apps": ["streaming"]},
    "game_qhd": {"games": True, "apps": ["streaming"]},
    "game_4k": {"games": True, "apps": ["streaming"]},
    "creator": {"games": False, "apps": ["photoshop", "premiere_fhd", "premiere_4k", "aftereffects", "blender", "cad_3d", "streaming"]},
    "ai_work": {"games": False, "apps": ["local_llm", "coding_docker"]},
}


def usage_detail(usage_id: str) -> dict[str, Any]:
    """용도의 세부 선택 범위. 모르는 용도면 모두 보인다."""
    return USAGE_DETAIL.get(usage_id) or {"games": True, "apps": [a["id"] for a in CUSTOM_APPS]}


RESOLUTION_INFO: dict[str, dict[str, Any]] = {
    "FHD": {"id": "FHD", "label": "FHD (1920×1080)", "ko": "FHD", "mp": 2_073_600, "idx": 0, "vram_min": 6},
    "QHD": {"id": "QHD", "label": "QHD (2560×1440)", "ko": "QHD", "mp": 3_686_400, "idx": 1, "vram_min": 8},
    "UHD": {"id": "UHD", "label": "4K UHD (3840×2160)", "ko": "4K", "mp": 8_294_400, "idx": 2, "vram_min": 12},
}

# 405.kr 방식 게임별 실측 환산 계수 (giPerFps: [FHD, QHD, 4K], cpuLoad: CPU 부하계수, low: 하위 1% 비율)
CUSTOM_GAMES: list[dict[str, Any]] = [
    {"id": "lol", "label": "리그 오브 레전드", "giPerFps": [0.16, 0.24, 0.42], "cpuLoad": 0.65, "low": [0.78, 0.78, 0.76], "storGB": 30, "fg": False, "mfg": False, "cap": 240},
    {"id": "valorant", "label": "발로란트", "giPerFps": [0.18, 0.28, 0.50], "cpuLoad": 0.70, "low": [0.76, 0.76, 0.75], "storGB": 45, "fg": False, "mfg": False, "cap": 360},
    {"id": "fc", "label": "FC 온라인", "giPerFps": [0.22, 0.35, 0.65], "cpuLoad": 0.75, "low": [0.75, 0.74, 0.72], "storGB": 35, "fg": False, "mfg": False, "cap": 240},
    {"id": "overwatch2", "label": "오버워치 2", "giPerFps": [0.38, 0.58, 1.05], "cpuLoad": 0.85, "low": [0.76, 0.75, 0.74], "storGB": 55, "fg": True, "mfg": False, "cap": 300},
    {"id": "lostark", "label": "로스트아크", "giPerFps": [0.52, 0.78, 1.38], "cpuLoad": 1.15, "low": [0.68, 0.67, 0.65], "storGB": 90, "fg": False, "mfg": False, "cap": 240},
    {"id": "pubg", "label": "배틀그라운드", "giPerFps": [0.62, 0.92, 1.65], "cpuLoad": 1.20, "low": [0.68, 0.67, 0.65], "storGB": 55, "fg": False, "mfg": False, "cap": 300},
    {"id": "diablo4", "label": "디아블로 4", "giPerFps": [0.68, 1.02, 1.85], "cpuLoad": 0.95, "low": [0.74, 0.73, 0.71], "storGB": 95, "fg": True, "mfg": True, "cap": 240},
    {"id": "poe2", "label": "패스 오브 엑자일 2", "giPerFps": [0.78, 1.15, 2.05], "cpuLoad": 1.22, "low": [0.66, 0.65, 0.64], "storGB": 100, "fg": True, "mfg": False, "cap": 240},
    {"id": "palworld", "label": "팰월드", "giPerFps": [0.82, 1.22, 2.15], "cpuLoad": 1.05, "low": [0.70, 0.69, 0.67], "storGB": 45, "fg": True, "mfg": False, "cap": 240},
    {"id": "eldenring", "label": "엘든 링", "giPerFps": [0.85, 1.25, 2.20], "cpuLoad": 1.00, "low": [0.74, 0.72, 0.70], "storGB": 65, "fg": False, "mfg": False, "cap": 60},
    {"id": "inzoi", "label": "인조이 (inZOI)", "giPerFps": [1.05, 1.55, 2.75], "cpuLoad": 1.25, "low": [0.68, 0.67, 0.65], "storGB": 75, "fg": True, "mfg": True, "cap": 180},
    {"id": "wukong", "label": "검은신화: 오공", "giPerFps": [1.18, 1.75, 3.10], "cpuLoad": 1.05, "low": [0.72, 0.70, 0.68], "storGB": 130, "fg": True, "mfg": True, "cap": 180},
    {"id": "cyberpunk", "label": "사이버펑크 2077", "giPerFps": [1.22, 1.82, 3.20], "cpuLoad": 1.15, "low": [0.73, 0.71, 0.69], "storGB": 85, "fg": True, "mfg": True, "cap": 240},
    {"id": "mhwilds", "label": "몬스터헌터: 와일즈", "giPerFps": [1.28, 1.90, 3.35], "cpuLoad": 1.28, "low": [0.67, 0.66, 0.64], "storGB": 140, "fg": True, "mfg": True, "cap": 180},
]
GAME_MAP: dict[str, dict[str, Any]] = {g["id"]: g for g in CUSTOM_GAMES}

# 작업용 프로그램 정의 (405.kr proApps 대응)
CUSTOM_APPS: list[dict[str, Any]] = [
    {"id": "office_doc", "label": "한글 · MS오피스 · 문서", "cores": 4, "ram": 16, "gi": 0, "vram": 0, "ssd": 500, "nvenc": False},
    {"id": "web_multi", "label": "크롬 다중창(20탭+) · HTS", "cores": 6, "ram": 16, "gi": 0, "vram": 0, "ssd": 500, "nvenc": False},
    {"id": "photoshop", "label": "포토샵 · 일러스트 · 라이트룸", "cores": 6, "ram": 32, "gi": 55, "vram": 6, "ssd": 1000, "nvenc": False},
    {"id": "premiere_fhd", "label": "프리미어 · 캡컷 (FHD 편집)", "cores": 6, "ram": 32, "gi": 80, "vram": 8, "ssd": 1000, "nvenc": True},
    {"id": "premiere_4k", "label": "프리미어 · 다빈치 (4K 편집)", "cores": 8, "ram": 32, "gi": 130, "vram": 12, "ssd": 2000, "nvenc": True},
    {"id": "aftereffects", "label": "애프터이펙트 · 모션그래픽", "cores": 8, "ram": 64, "gi": 130, "vram": 12, "ssd": 2000, "nvenc": True},
    {"id": "blender", "label": "블렌더 · C4D · 3D 렌더링", "cores": 8, "ram": 32, "gi": 165, "vram": 12, "ssd": 2000, "nvenc": True},
    {"id": "cad_3d", "label": "오토캐드 · 솔리드웍스 · 3D CAD", "cores": 8, "ram": 32, "gi": 110, "vram": 8, "ssd": 1000, "nvenc": False},
    {"id": "coding_docker", "label": "코딩 · 도커 · 가상머신", "cores": 8, "ram": 32, "gi": 0, "vram": 0, "ssd": 1000, "nvenc": False},
    {"id": "streaming", "label": "원컴 방송 · OBS 스트리밍", "cores": 8, "ram": 32, "gi": 125, "vram": 8, "ssd": 1000, "nvenc": True},
    {"id": "local_llm", "label": "로컬 LLM · 스테이블 디퓨전", "cores": 8, "ram": 32, "gi": 140, "vram": 16, "ssd": 2000, "nvenc": True},
]
APP_MAP: dict[str, dict[str, Any]] = {a["id"]: a for a in CUSTOM_APPS}


# ---------------------------------------------------------------------------
# 2. 컴퓨존 추천 부품 수집 및 정규식 스펙 파서
# ---------------------------------------------------------------------------

def _clean_online_name(raw_name: str) -> str:
    name = htmlmod.unescape(raw_name or "").strip()
    name = re.sub(r"\[\d{5,8}\]\s*$", "", name)
    name = re.sub(r"▶.*?◀", "", name)
    name = re.sub(r"★.*?★", "", name)
    return _clean_part_name(name)


def _detect_color(text: str) -> str:
    if re.search(r"화이트|white|실버|silver|\bice\b|백색", text, re.I):
        return "white"
    return "black"


def _parse_cpu_meta(name: str, spec: str, price: int) -> dict[str, Any] | None:
    full = f"{name} {spec}"
    if re.search(r"스레드리퍼|threadripper|제온|xeon|epyc|서버", full, re.I):
        return None
    vendor = "amd" if re.search(r"\[AMD\]|라이젠|ryzen", full, re.I) else "intel"
    # 소켓 판별
    if re.search(r"AM5|라파엘|그래니트|피닉스|7\d00|8\d00|9\d00", full, re.I):
        socket = "AM5"
        mem_type = "DDR5"
    elif re.search(r"AM4|버미어|세잔|5\d00", full, re.I):
        socket = "AM4"
        mem_type = "DDR4"
    elif re.search(r"1851|애로우레이크|Ultra\s*[579]", full, re.I):
        socket = "LGA1851"
        mem_type = "DDR5"
    else:
        socket = "LGA1700"
        mem_type = "DDR4_DDR5"

    # 코어 수 추출
    cores = 6
    m_pe = re.search(r"P(\d+)\+E(\d+)코어", full)
    m_c = re.search(r"(\d+)\s*코어", full)
    if m_pe:
        cores = int(m_pe.group(1)) + int(m_pe.group(2))
    elif m_c:
        cores = int(m_c.group(1))

    # TDP & 실소비(PPT) 추출
    tdp_w = 65
    m_tdp = re.search(r"TDP\s*[:：]?\s*(\d+)\s*w|(\d+)\s*w\s*TDP", full, re.I)
    if m_tdp:
        tdp_w = int(m_tdp.group(1) or m_tdp.group(2))
    elif re.search(r"9950|14900|285K", name, re.I):
        tdp_w = 170
    elif re.search(r"7800X3D|9800X3D|9900X|14700|270K|265K", name, re.I):
        tdp_w = 120
    elif re.search(r"9700X|9600X|7600X|14600|250K|245K", name, re.I):
        tdp_w = 105
    ppt_w = int(round(tdp_w * 1.35)) if tdp_w >= 105 else 88

    x3d = bool(re.search(r"X3D", name, re.I))
    apu = bool(re.search(r"5500GT|5600G|5700G|8500G|8600G|8700G|세잔", full, re.I))
    # 내장그래픽 유무: F 시리즈(7500F, 14400F, 8400F 등)와 버미어(5600, 5600X3D)는 내장 없음
    no_igpu_model = bool(re.search(r"\b\d{4,5}F\b|5600\b|5600X\b|5600X3D|5700X\b|5700X3D|5800X", name, re.I))
    if "내장그래픽 무" in full or "내장그래픽: 무" in full or no_igpu_model:
        igpu = False
    elif "내장그래픽 유" in full or apu or socket in ("AM5", "LGA1851") or not no_igpu_model:
        igpu = True
    else:
        igpu = False

    has_cooler = ("쿨러미포함" not in full) and not x3d and tdp_w <= 65

    # 405.kr 스타일 게임 성능 지수(cpg) 매핑
    if re.search(r"9800X3D|9950X3D", name, re.I):
        cpg = 315
    elif re.search(r"7800X3D", name, re.I):
        cpg = 278
    elif re.search(r"285K|14900|9900X|9700X", name, re.I):
        cpg = 240
    elif re.search(r"270K|265K|14700|9600X|7700", name, re.I):
        cpg = 225
    elif re.search(r"250K|245K|14600|5600X3D|7600|7500F", name, re.I):
        cpg = 205
    elif re.search(r"225|14400|5600", name, re.I):
        cpg = 182
    elif re.search(r"5500GT|14100", name, re.I):
        cpg = 152
    else:
        cpg = 175

    parallel_import = "병행" in name or "벌크" in name
    return {
        "vendor": vendor,
        "socket": socket,
        "mem_type": mem_type,
        "cores": cores,
        "tdp_w": tdp_w,
        "ppt_w": ppt_w,
        "igpu": igpu,
        "x3d": x3d,
        "apu": apu,
        "has_cooler": has_cooler,
        "cpg": cpg,
        "bulk": parallel_import,
    }


def _parse_mb_meta(name: str, spec: str, price: int) -> dict[str, Any] | None:
    full = f"{name} {spec}"
    # 그래픽카드 번들 상품 제외 (예: B850M + RX 9070)
    if "+" in name and re.search(r"RTX|RX\s*\d|라데온|지포스", name, re.I):
        return None
    if re.search(r"TRX50|WRX90|서버|sTRX", full, re.I):
        return None

    # 칩셋 및 소켓 판별
    chipset = "B650"
    chipset_rank = 2
    socket = "AM5"
    for cs, sock, rnk in (
        ("X870E", "AM5", 4),
        ("X870", "AM5", 4),
        ("X670E", "AM5", 4),
        ("X670", "AM5", 4),
        ("B850", "AM5", 3),
        ("B650E", "AM5", 3),
        ("B650", "AM5", 2),
        ("A620", "AM5", 1),
        ("B550", "AM4", 2),
        ("A520", "AM4", 1),
        ("Z890", "LGA1851", 4),
        ("B860", "LGA1851", 3),
        ("H810", "LGA1851", 1),
        ("Z790", "LGA1700", 4),
        ("B760", "LGA1700", 2),
        ("H610", "LGA1700", 1),
    ):
        if re.search(rf"\b{cs}", full, re.I):
            chipset = cs
            socket = sock
            chipset_rank = rnk
            break

    if socket == "AM4":
        mem_type = "DDR4"
    elif socket in ("AM5", "LGA1851"):
        mem_type = "DDR5"
    else:
        mem_type = "DDR4" if re.search(r"DDR4|\bD4\b", full, re.I) else "DDR5"

    form_factor = "M-ATX" if re.search(r"M-ATX|Micro-ATX|\b\w+M[-\s]", full, re.I) else "ATX"
    m_vrm = re.search(r"(\d+)\+\d+(?:\+\d+)?\s*페이즈|(\d+)\s*페이즈", full)
    if m_vrm:
        vrm_phases = int(m_vrm.group(1) or m_vrm.group(2))
    else:
        vrm_phases = {1: 6, 2: 8, 3: 12, 4: 16}.get(chipset_rank, 8)

    wifi = bool(re.search(r"WIFI|Wi-Fi|와이파이", full, re.I))
    heatsink = chipset_rank >= 2 or bool(re.search(r"TUF|AORUS|박격포|토마호크|PRO-A|GAMING|FORCE", name, re.I))
    return {
        "socket": socket,
        "chipset": chipset,
        "chipset_rank": chipset_rank,
        "mem_type": mem_type,
        "form_factor": form_factor,
        "vrm_phases": vrm_phases,
        "wifi": wifi,
        "heatsink": heatsink,
        "color": _detect_color(name),
    }


def _parse_ram_meta(name: str, spec: str, price: int) -> dict[str, Any] | None:
    full = f"{name} {spec}"
    if re.search(r"ECC|REG|서버용|노트북용|SO-DIMM|23년도 이전", full, re.I):
        return None
    mem_type = "DDR4" if "DDR4" in full.upper() else "DDR5"
    gb = 16
    m_gb = re.search(r"\[(\d+)\s*GB", name, re.I) or re.search(r"\b(\d+)\s*GB\b", full, re.I)
    if m_gb:
        gb = int(m_gb.group(1))
    if gb not in (8, 16, 24, 32, 48, 64):
        return None

    kit = 2 if re.search(r"\*\s*2|2개|패키지", full) and ("GB*2" in name or "2개" in name) else 1
    clock = 5600 if mem_type == "DDR5" else 3200
    m_clk = re.search(r"(\d{4})\s*MHz|\((\d{4})\)", full)
    if m_clk:
        clock = int(m_clk.group(1) or m_clk.group(2))

    amd_only = "인텔 15세대 에로우레이크 호환 불가" in full or "AMD 전용" in full
    tier1 = bool(re.search(r"삼성전자|SK\s*hynix|하이닉스|마이크론|Crucial|에센코어|KLEVV|CORSAIR|Team\s*Group|ADATA", name, re.I))
    heatsink = "방열판 무" not in spec or bool(re.search(r"CRAS|BOLT|FIT|VENGEANCE|EXPERT|RGB", name, re.I))
    return {
        "mem_type": mem_type,
        "gb": gb,
        "kit": kit,
        "clock": clock,
        "amd_only": amd_only,
        "tier1": tier1,
        "heatsink": heatsink,
        "color": _detect_color(name),
    }


def _parse_gpu_meta(name: str, spec: str, price: int) -> dict[str, Any] | None:
    full = f"{name} {spec}"
    if re.search(r"RTX\s*PRO|Quadro|Arc\s*Pro|A100|H100|서버", full, re.I):
        return None
    vendor = "amd" if re.search(r"라데온|Radeon|\bRX\s*\d", full, re.I) else "nvidia"
    vram = 8
    m_vram = re.search(r"(\d+)\s*GB", name, re.I) or re.search(r"(\d+)\s*GB", spec, re.I)
    if m_vram:
        vram = int(m_vram.group(1))

    # 칩셋 및 405.kr 스타일 성능지수(gi) · 소비전력(watts) · 권장파워(rec_psu) 매핑
    chipset = "GPU"
    gi = 100
    watts = 180
    rec_psu = 600
    mfg = False
    fg = True
    gpu_table = [
        (r"RTX\s*5090", "RTX 5090", 360, 575, 1000, True),
        (r"RTX\s*5080", "RTX 5080", 255, 360, 850, True),
        (r"RTX\s*5070\s*Ti", "RTX 5070 Ti", 215, 300, 750, True),
        (r"RX\s*9070\s*XT", "RX 9070 XT", 205, 304, 750, False),
        (r"RTX\s*5070\b", "RTX 5070", 175, 250, 650, True),
        (r"RX\s*9070\b", "RX 9070", 170, 220, 650, False),
        (r"RTX\s*5060\s*Ti", "RTX 5060 Ti", 142 if vram >= 16 else 130, 180, 600, True),
        (r"RX\s*9060\s*XT", "RX 9060 XT", 136 if vram >= 16 else 125, 180, 600, False),
        (r"RTX\s*5060\b", "RTX 5060", 112, 150, 550, True),
        (r"RX\s*9060\b", "RX 9060", 105, 150, 550, False),
        (r"RTX\s*4060\s*Ti", "RTX 4060 Ti", 118, 165, 550, False),
        (r"RTX\s*4060\b", "RTX 4060", 96, 115, 550, False),
        (r"RX\s*7600", "RX 7600", 88, 165, 550, False),
        (r"RTX\s*3060", "RTX 3060", 82, 170, 550, False),
        (r"RTX\s*3050", "RTX 3050", 58, 115, 500, False),
    ]
    for pat, label, g_val, w_val, psu_val, is_mfg in gpu_table:
        if re.search(pat, full, re.I):
            chipset = label
            gi = g_val
            watts = w_val
            rec_psu = psu_val
            mfg = is_mfg
            if label in ("RTX 3060", "RTX 3050"):
                fg = False
            break

    m_psu = re.search(r"정격파워\s*(\d+)\s*W", full, re.I)
    if m_psu:
        rec_psu = max(rec_psu, int(m_psu.group(1)))

    length_mm = 260
    m_len = re.search(r"가로\(길이\)\s*[:：]?\s*(\d+(?:\.\d+)?)\s*mm", full, re.I)
    if m_len:
        length_mm = int(float(m_len.group(1)))

    return {
        "vendor": vendor,
        "chipset": chipset,
        "vram": vram,
        "gi": gi,
        "watts": watts,
        "peak_w": int(round(watts * 1.25)),
        "rec_psu": rec_psu,
        "length_mm": length_mm,
        "fg": fg,
        "mfg": mfg,
        "color": _detect_color(name),
    }


def _parse_ssd_meta(name: str, spec: str, price: int) -> dict[str, Any] | None:
    full = f"{name} {spec}"
    if "M.2" not in full and "NVMe" not in full:
        return None
    gb = 1000
    m_tb = re.search(r"\[(\d+)\s*TB\]|\b(\d+)\s*TB\b", full, re.I)
    m_gb = re.search(r"\[(\d+)\s*GB\]|\b(\d+)\s*GB\b", full, re.I)
    if m_tb:
        gb = int(m_tb.group(1) or m_tb.group(2)) * 1000
    elif m_gb:
        gb = int(m_gb.group(1) or m_gb.group(2))

    nand = "QLC" if "QLC" in full.upper() else "TLC"
    dram = "DRAM 탑재" in full and "DRAM 미탑재" not in full
    gen = 5 if "PCIeGen5" in full or "Gen5" in full else (4 if "PCIeGen4" in full or "Gen4" in full else 3)
    tier1 = bool(re.search(r"삼성전자|SK\s*hynix|하이닉스|마이크론|Crucial|WD|샌디스크|키오시아|Solidigm", name, re.I))
    read_mb = 7000 if gen == 4 else (10000 if gen == 5 else 3500)
    m_rd = re.search(r"읽기\s*(\d+)\s*M", full)
    if m_rd:
        read_mb = int(m_rd.group(1))
    return {
        "gb": gb,
        "nand": nand,
        "dram": dram,
        "gen": gen,
        "tier1": tier1,
        "read_mb": read_mb,
    }


def _parse_cooler_meta(name: str, spec: str, price: int) -> dict[str, Any] | None:
    full = f"{name} {spec}"
    if "CPU" not in full and "수랭" not in full and "공랭" not in full:
        return None
    is_aio = "수랭" in full or bool(re.search(r"\b360\b|\b240\b|MasterLiquid|KRAKEN", name, re.I))
    if is_aio:
        kind = "aio360" if "240" not in name else "aio240"
        rank = 4 if kind == "aio360" else 3
        default_tdp = 300 if kind == "aio360" else 250
        label = "360mm 수랭" if kind == "aio360" else "240mm 수랭"
    elif "듀얼타워" in full or re.search(r"620|AG620|AK620|PA120|PS120|RC1900", name, re.I):
        kind = "dual_tower"
        rank = 3
        default_tdp = 260
        label = "듀얼타워 공랭"
    else:
        kind = "single_tower"
        rank = 2
        default_tdp = 220
        label = "싱글타워 공랭"

    tdp_w = default_tdp
    m_tdp = re.search(r"TDP\s*[:：]?\s*(\d+)\s*W", full, re.I)
    if m_tdp:
        tdp_w = int(m_tdp.group(1))

    return {
        "kind": kind,
        "kind_label": label,
        "rank": rank,
        "aio": is_aio,
        "tdp_w": tdp_w,
        "color": _detect_color(name),
    }


def _parse_case_meta(name: str, spec: str, price: int) -> dict[str, Any] | None:
    full = f"{name} {spec}"
    if "파워포함" in full:
        return None
    supports_atx = bool(re.search(r"표준-ATX|지원보드규격\s*[:：]?\s*(?:E-ATX\s*[,/]\s*)?ATX", full, re.I)) or "미들타워" in full or "빅타워" in full
    mesh = bool(re.search(r"메쉬|MESH", full, re.I))
    fans = 3
    m_f = re.search(r"(?:기본팬|쿨링팬)\s*[:：]?\s*총?\s*(\d+)\s*개", full)
    if m_f:
        fans = int(m_f.group(1))
    return {
        "supports_atx": supports_atx,
        "mesh": mesh,
        "fans": fans,
        "color": _detect_color(name),
    }


def _parse_psu_meta(name: str, spec: str, price: int) -> dict[str, Any] | None:
    full = f"{name} {spec}"
    watts = 600
    m_w = re.search(r"\[(\d{3,4})\s*W\]|정격출력\s*[:：]?\s*(\d{3,4})\s*W|\b(\d{3,4})\s*W\b", full, re.I)
    if m_w:
        watts = int(m_w.group(1) or m_w.group(2) or m_w.group(3))
    if watts < 400:
        return None

    if re.search(r"Platinum|플래티넘|Titanium|티타늄", full, re.I):
        eff_rank, eff_label = 4, "80PLUS 플래티넘"
    elif re.search(r"Gold|골드", full, re.I):
        eff_rank, eff_label = 3, "80PLUS 골드"
    elif re.search(r"Bronze|브론즈|Silver|실버", full, re.I):
        eff_rank, eff_label = 2, "80PLUS 브론즈/실버"
    else:
        eff_rank, eff_label = 1, "정격 Active PFC"

    atx3 = bool(re.search(r"ATX\s*3\.[01]|ATX3\.[01]", full, re.I))
    modular = bool(re.search(r"풀\s*모듈러|풀모듈러", full, re.I))
    bulk = "벌크" in full
    return {
        "watts": watts,
        "eff_rank": eff_rank,
        "eff_label": eff_label,
        "atx3": atx3,
        "modular": modular,
        "bulk": bulk,
        "color": _detect_color(name),
    }


_META_PARSERS = {
    "cpu": _parse_cpu_meta,
    "cooler": _parse_cooler_meta,
    "motherboard": _parse_mb_meta,
    "ram": _parse_ram_meta,
    "gpu": _parse_gpu_meta,
    "ssd": _parse_ssd_meta,
    "case": _parse_case_meta,
    "psu": _parse_psu_meta,
}


def _fetch_online_page(mid: str, page: int) -> str:
    start = (page - 1) * 10
    data = urllib.parse.urlencode(
        {
            "actype": "list",
            "BigDivNo": "4",
            "MediumDivNo": mid,
            "DivNo": "",
            "PreOrder": "recommand",
            "StartNum": str(start),
            "PageNum": str(page),
            "param": "N|N",
            "SearchBottom": "",
            "SearchKeyword": "",
            "MaxCount": "20",
            "IsAroundComAssemble": "N",
        }
    ).encode()
    req = urllib.request.Request(
        ONLINE_LIST_URL,
        data=data,
        headers={
            "User-Agent": USER_AGENT,
            "Referer": ONLINE_MAIN_URL,
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = resp.read().decode("euc-kr", errors="replace")
    return htmlmod.unescape(raw)


def _parse_online_page_items(key: str, category_label: str, raw_html: str, start_rank: int) -> list[dict[str, Any]]:
    parser = _META_PARSERS[key]
    items: list[dict[str, Any]] = []
    pattern = re.compile(
        r"detail_show\(['\"](\d+)['\"]\).*?"
        r"(?:<img\s+src=['\"]([^'\"]+)['\"][^>]*>)?.*?"
        r"<td\s+class=\"name\">.*?<b>(.*?)</b>(.*?)</a>.*?"
        r"판매가격\s*<span[^>]*>([\d,]+)</span>",
        re.S,
    )
    for idx, m in enumerate(pattern.finditer(raw_html)):
        pno = int(m.group(1))
        img = (m.group(2) or "").strip()
        if img.startswith("//"):
            img = "https:" + img
        name = _clean_online_name(m.group(3))
        spec = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", m.group(4) or "")).strip(" /")
        price_str = re.sub(r"[^\d]", "", m.group(5) or "")
        price = int(price_str) if price_str else 0
        if price <= 0 or not name:
            continue
        meta = parser(name, spec, price)
        if meta is None:
            continue
        items.append(
            {
                "key": f"{key}_{pno}",
                "part_type": key,
                "category": category_label,
                "pno": pno,
                "name": name,
                "spec": spec,
                "price": price,
                "image": img,
                "url": BASE_PRODUCT_URL.format(pno=pno),
                "rec_rank": start_rank + idx,
                "meta": meta,
            }
        )
    return items


def fetch_compuzone_part_catalog(force_refresh: bool = False) -> dict[str, Any]:
    """컴퓨존 추천 부품 카탈로그를 병렬 수집하고 15분간 메모리 캐시한다."""
    now = time.time()
    with _catalog_lock:
        if not force_refresh and _catalog_mem["parts"] and (now - _catalog_mem["at"] < PART_CATALOG_TTL_SEC):
            return _catalog_mem

    tasks: list[tuple[str, str, str, int]] = []
    for key, info in COMPUZONE_PART_DIVS.items():
        for page in range(1, int(info.get("pages", 3)) + 1):
            tasks.append((key, info["mid"], info["category"], page))

    raw_pages: dict[tuple[str, int], str] = {}
    with ThreadPoolExecutor(max_workers=12) as pool:
        future_map = {
            pool.submit(_fetch_online_page, mid, page): (key, cat_label, page)
            for key, mid, cat_label, page in tasks
        }
        for fut, (key, _cat_label, page) in future_map.items():
            try:
                raw_pages[(key, page)] = fut.result()
            except (urllib.error.URLError, TimeoutError, OSError):
                raw_pages[(key, page)] = ""

    parts_by_key: dict[str, list[dict[str, Any]]] = {k: [] for k in COMPUZONE_PART_DIVS}
    seen_pnos: set[int] = set()
    for key, info in COMPUZONE_PART_DIVS.items():
        rank = 1
        for page in range(1, int(info.get("pages", 3)) + 1):
            html_chunk = raw_pages.get((key, page), "")
            if not html_chunk:
                continue
            parsed = _parse_online_page_items(key, info["category"], html_chunk, rank)
            for item in parsed:
                if item["pno"] in seen_pnos:
                    continue
                seen_pnos.add(item["pno"])
                parts_by_key[key].append(item)
                rank += 1

    # 필수 부품군이 모두 수집되었는지 점검, 실패 시 로컬 스냅샷 폴백
    required_keys = ("cpu", "motherboard", "ram", "gpu", "ssd", "case", "psu", "cooler")
    if all(len(parts_by_key.get(k, [])) > 0 for k in required_keys):
        synced_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        payload = {"at": now, "parts": parts_by_key, "synced_at": synced_at, "source": "live"}
        with _catalog_lock:
            _catalog_mem.update(payload)
        try:
            CACHE_FILE.write_text(
                json.dumps({"synced_at": synced_at, "parts": parts_by_key}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError:
            pass
        return payload

    if CACHE_FILE.exists():
        try:
            saved = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
            if saved.get("parts"):
                payload = {
                    "at": now,
                    "parts": saved["parts"],
                    "synced_at": saved.get("synced_at", ""),
                    "source": "cache",
                }
                with _catalog_lock:
                    _catalog_mem.update(payload)
                return payload
        except (OSError, ValueError):
            pass

    raise RuntimeError("컴퓨존 추천 부품 목록을 불러오지 못했습니다. 네트워크 연결을 확인해주세요.")


# ---------------------------------------------------------------------------
# 3. 405.kr 스타일 요구사양 역산(floors) 및 호환성·전력·쿨링 자동 매칭
# ---------------------------------------------------------------------------

def calculate_floors(
    usage: str = "game_fhd",
    games: list[str] | None = None,
    apps: list[str] | None = None,
    resolution: str = "",
    target_fps: int = 0,
    boost: bool = False,
) -> dict[str, Any]:
    """용도 프리셋 + 선택 게임(해상도·프레임·부스트) + 작업 프로그램으로 최저/추천/최고 하한을 계산한다."""
    preset = USAGE_PRESET_MAP.get(usage) or USAGE_PRESET_MAP["game_fhd"]
    res_key = (resolution or preset.get("default_res") or "FHD").upper()
    if res_key not in RESOLUTION_INFO:
        res_key = "FHD"
    res = RESOLUTION_INFO[res_key]
    fps = int(target_fps or preset.get("default_fps") or 144)

    detail = usage_detail(preset["id"])
    game_ids = [g for g in (games or []) if g in GAME_MAP] if detail["games"] else []
    app_ids = [a for a in (apps or []) if a in APP_MAP and a in detail["apps"]]

    # 프리셋 기본 요구치 복사
    base_min = dict(preset["floors"]["min"])
    base_rec = dict(preset["floors"]["rec"])
    base_max = dict(preset["floors"]["max"])

    need_gpu = bool(preset.get("need_gpu", True))
    prefer_nvidia = bool(preset.get("prefer_nvidia", False))
    why_notes: list[str] = []

    # 선택한 게임이 있으면 405.kr 실측 환산식 적용
    if game_ids:
        need_gpu = True
        max_game_gi = 0.0
        max_cpu_floor = 0.0
        heavy_game = False
        top_game_label = ""
        for gid in game_ids:
            g = GAME_MAP[gid]
            eff_fps = min(fps, g["cap"]) if g.get("cap") else fps
            if g.get("cpuLoad", 1.0) > 1.1:
                heavy_game = True
            # 업스케일+FG 부스트 배수 (공식 지원 게임만)
            boost_mult = 1.0
            if boost:
                boost_mult *= 1.35
                if g.get("fg"):
                    boost_mult *= 1.45
            per_fps = g["giPerFps"][res["idx"]]
            gi_need = (per_fps * eff_fps) / boost_mult
            if gi_need > max_game_gi:
                max_game_gi = gi_need
                top_game_label = g["label"]
            # 405.kr CPU 천장 하한 역산: fps = 113 * (cpg/100)^0.48 / cpuLoad
            c_floor = 100.0 * math.pow((eff_fps * g.get("cpuLoad", 1.0)) / 113.0, 1.0 / 0.48)
            if c_floor > max_cpu_floor:
                max_cpu_floor = c_floor

        c_floor_int = min(310, int(round(max_cpu_floor)))
        gi_int = int(round(max_game_gi))
        base_min["gi"] = max(base_min["gi"], int(round(gi_int * 0.85)))
        base_rec["gi"] = max(base_rec["gi"], gi_int)
        base_max["gi"] = max(base_max["gi"], int(round(gi_int * 1.35)))
        base_min["vram"] = max(base_min["vram"], res["vram_min"])
        base_rec["vram"] = max(base_rec["vram"], res["vram_min"])
        base_max["vram"] = max(base_max["vram"], max(res["vram_min"], 12 if res_key != "FHD" else 8))
        base_min["cores"] = max(base_min["cores"], 6)
        base_rec["cores"] = max(base_rec["cores"], 8 if (heavy_game and res_key != "FHD") else 6)
        base_max["cores"] = max(base_max["cores"], 8)
        base_min["cpu_floor"] = max(base_min["cpu_floor"], min(185 if res_key == "FHD" else 205, int(round(c_floor_int * 0.76))))
        base_rec["cpu_floor"] = max(base_rec["cpu_floor"], min(205 if res_key == "FHD" else 255, c_floor_int))
        base_max["cpu_floor"] = max(base_max["cpu_floor"], min(315, int(round(c_floor_int * 1.15))))
        if heavy_game or len(game_ids) >= 2:
            base_rec["ram"] = max(base_rec["ram"], 32)
            base_max["ram"] = max(base_max["ram"], 32)
        why_notes.append(f"{top_game_label} {res['ko']} {fps}fps 목표 기준 사양 역산")

    # 선택한 작업 프로그램이 있으면 작업 하한 반영
    if app_ids:
        for aid in app_ids:
            a = APP_MAP[aid]
            if a["gi"] > 0:
                need_gpu = True
            if a.get("nvenc"):
                prefer_nvidia = True
            base_min["cores"] = max(base_min["cores"], a["cores"])
            base_rec["cores"] = max(base_rec["cores"], a["cores"])
            base_max["cores"] = max(base_max["cores"], min(16, a["cores"] + 4))
            base_min["ram"] = max(base_min["ram"], a["ram"])
            base_rec["ram"] = max(base_rec["ram"], a["ram"])
            base_max["ram"] = max(base_max["ram"], min(64, a["ram"] * 2 if a["ram"] < 64 else 64))
            base_min["gi"] = max(base_min["gi"], int(round(a["gi"] * 0.85)))
            base_rec["gi"] = max(base_rec["gi"], a["gi"])
            base_max["gi"] = max(base_max["gi"], int(round(a["gi"] * 1.35)))
            base_min["vram"] = max(base_min["vram"], a["vram"])
            base_rec["vram"] = max(base_rec["vram"], a["vram"])
            base_max["vram"] = max(base_max["vram"], a["vram"])
            base_min["ssd"] = max(base_min["ssd"], a["ssd"])
            base_rec["ssd"] = max(base_rec["ssd"], a["ssd"])
            base_max["ssd"] = max(base_max["ssd"], min(4000, a["ssd"] * 2))
        app_names = ", ".join(APP_MAP[a]["label"].split("·")[0].strip() for a in app_ids[:3])
        why_notes.append(f"{app_names} 작업 권장 코어·메모리·VRAM 반영")

    return {
        "usage": preset["id"],
        "usage_label": preset["label"],
        "usage_short": preset["short"],
        "usage_desc": preset["desc"],
        "method_title": preset["method_title"],
        "method_rules": preset["method_rules"],
        "resolution": res_key,
        "target_fps": fps,
        "boost": boost,
        "need_gpu": need_gpu,
        "prefer_nvidia": prefer_nvidia,
        "games": game_ids or preset.get("default_games", []),
        "apps": app_ids or preset.get("default_apps", []),
        "why_notes": why_notes,
        "tiers": {
            "low": base_min,
            "mid": base_rec,
            "high": base_max,
        },
    }


def _color_match(item: dict[str, Any], want_color: str) -> bool:
    if not want_color or want_color == "auto":
        return True
    return item["meta"].get("color", "black") == want_color


def _pick_gpu_for_tier(
    gpus: list[dict[str, Any]],
    tier_req: dict[str, Any],
    tier_grade: str,
    gpu_vendor: str,
    prefer_nvidia: bool,
    want_color: str,
) -> dict[str, Any] | None:
    if not gpus:
        return None
    pool = [g for g in gpus if g["price"] <= 5_000_000] or list(gpus)
    if gpu_vendor in ("nvidia", "amd"):
        v_pool = [g for g in pool if g["meta"]["vendor"] == gpu_vendor]
        if v_pool:
            pool = v_pool
    elif prefer_nvidia:
        nv_pool = [g for g in pool if g["meta"]["vendor"] == "nvidia"]
        if nv_pool:
            pool = nv_pool

    need_gi = tier_req.get("gi", 60)
    need_vram = tier_req.get("vram", 6)

    qualified = [
        g for g in pool if g["meta"]["gi"] >= need_gi and g["meta"]["vram"] >= need_vram
    ]
    if not qualified:
        qualified = [g for g in pool if g["meta"]["gi"] >= need_gi]
    if not qualified:
        # 요구치가 가장 높은 카드보다 크면 최고 성능 그룹 선택
        max_gi = max(g["meta"]["gi"] for g in pool)
        qualified = [g for g in pool if g["meta"]["gi"] >= max_gi * 0.9]

    # 색상 선호 반영 (가격 +15% 이내)
    if want_color in ("white", "black"):
        colored = [g for g in qualified if _color_match(g, want_color)]
        if colored:
            min_q = min(g["price"] for g in qualified)
            affordable_colored = [g for g in colored if g["price"] <= min_q * 1.18]
            if affordable_colored:
                qualified = affordable_colored

    if tier_grade == "low":
        # 최저가: 요구 성능을 만족하는 가장 저렴한 모델
        return min(qualified, key=lambda g: (g["price"], g["rec_rank"]))
    if tier_grade == "mid":
        # 추천가: 요구 성능을 만족하는 주류 체급 중 컴퓨존 추천순 상위 + 가성비 균형
        min_gi_in_q = min(g["meta"]["gi"] for g in qualified)
        sweet_band = [g for g in qualified if g["meta"]["gi"] <= min_gi_in_q * 1.28]
        target_pool = sweet_band or qualified
        min_p = min(g["price"] for g in target_pool)
        top_rec = [g for g in target_pool if g["price"] <= min_p * 1.12]
        return min(top_rec or target_pool, key=lambda g: (g["rec_rank"], g["price"]))
    # 최고가: qualified 내에서 상위 성능 체급(단, 과도한 플래그십 1000만원대 RTX 5090은 4K/AI가 아니면 제한)
    cap_gi = 380 if need_gi >= 240 else min(240, int(need_gi * 1.55))
    upper_pool = [g for g in qualified if g["meta"]["gi"] <= cap_gi] or qualified
    max_gi = max(g["meta"]["gi"] for g in upper_pool)
    top_tier = [g for g in upper_pool if g["meta"]["gi"] >= max_gi * 0.95]
    return min(top_tier, key=lambda g: (-g["meta"]["gi"], g["rec_rank"], g["price"]))


def _pick_cpu_for_tier(
    cpus: list[dict[str, Any]],
    tier_req: dict[str, Any],
    tier_grade: str,
    need_igpu: bool,
    is_game_build: bool,
    cpu_vendor: str,
    gpu_item: dict[str, Any] | None,
) -> dict[str, Any]:
    pool = list(cpus)
    if cpu_vendor in ("amd", "intel"):
        v_pool = [c for c in pool if c["meta"]["vendor"] == cpu_vendor]
        if v_pool:
            pool = v_pool

    if need_igpu:
        ig_pool = [c for c in pool if c["meta"]["igpu"]]
        if ig_pool:
            pool = ig_pool
    else:
        # 405.kr 핵심 규칙: 외장 그래픽카드 구성에서는 L3 캐시가 반토막인 APU(5500GT 등) 배제
        non_apu = [c for c in pool if not c["meta"]["apu"]]
        if non_apu:
            pool = non_apu

    # 추천가·최고가는 최신 DDR5 플랫폼(AM5 / LGA1851 / LGA1700) 우선, 정품/멀티팩 선호(병행/벌크 후순위)
    if tier_grade in ("mid", "high") and not need_igpu:
        ddr5_pool = [c for c in pool if c["meta"]["socket"] in ("AM5", "LGA1851", "LGA1700") and c["meta"]["mem_type"] != "DDR4"]
        if ddr5_pool:
            pool = ddr5_pool
    if tier_grade in ("mid", "high"):
        non_bulk = [c for c in pool if not c["meta"]["bulk"]]
        if non_bulk:
            pool = non_bulk

    need_cores = tier_req.get("cores", 6)
    need_cpg = tier_req.get("cpu_floor", 160)

    # 405.kr CPU 과지출 게이트 (cpuGateOk): 그래픽카드 체급 대비 과도한 고가 CPU 방지
    gpu_gi = gpu_item["meta"]["gi"] if gpu_item else 0
    if is_game_build and gpu_gi > 0:
        if gpu_gi <= 90:
            gated_pool = [c for c in pool if c["price"] <= 280_000]
            if gated_pool:
                pool = gated_pool
        elif gpu_gi <= 118:
            # RTX 5060 / 3060 급에서는 40만원 초과 초고가 CPU 배제 (예산을 GPU에 쓰는 것이 이득)
            gated_pool = [c for c in pool if c["price"] <= 390_000]
            if gated_pool:
                pool = gated_pool
        elif gpu_gi <= 145 and tier_grade != "high":
            gated_pool = [c for c in pool if c["price"] <= 580_000]
            if gated_pool:
                pool = gated_pool

    core_ok = [c for c in pool if c["meta"]["cores"] >= need_cores] or pool
    cpg_ok = [c for c in core_ok if c["meta"]["cpg"] >= need_cpg]
    if not cpg_ok:
        max_cpg = max(c["meta"]["cpg"] for c in core_ok)
        cpg_ok = [c for c in core_ok if c["meta"]["cpg"] >= max_cpg * 0.9]

    if tier_grade == "low":
        return min(cpg_ok, key=lambda c: (c["price"], c["rec_rank"]))
    if tier_grade == "mid":
        # 추천가: 요구 조건 충족 중 추천순 상위 + 적정 가격대
        min_p = min(c["price"] for c in cpg_ok)
        mid_band = [c for c in cpg_ok if c["price"] <= min_p * 1.45]
        return min(mid_band or cpg_ok, key=lambda c: (c["rec_rank"], c["price"]))
    # 최고가: 사무용(내장그래픽)은 35만원 이하 실속 상급 CPU로 제한, 게임은 X3D 우선, 작업은 다코어 우선
    if need_igpu:
        ig_high = [c for c in cpg_ok if c["price"] <= 350_000] or cpg_ok
        return max(ig_high, key=lambda c: (c["meta"]["cores"], c["meta"]["cpg"], -c["rec_rank"]))
    if is_game_build:
        x3d_pool = [c for c in cpg_ok if c["meta"]["x3d"] and c["price"] <= 950_000]
        if x3d_pool:
            return max(x3d_pool, key=lambda c: (c["meta"]["cpg"], -c["rec_rank"]))
    reasonable_high = [c for c in cpg_ok if c["price"] <= 980_000] or cpg_ok
    return max(reasonable_high, key=lambda c: (c["meta"]["cpg"] if is_game_build else c["meta"]["cores"], -c["rec_rank"]))


def _pick_mb_for_cpu(
    mbs: list[dict[str, Any]],
    cpu_item: dict[str, Any],
    tier_grade: str,
    want_color: str,
) -> dict[str, Any]:
    socket = cpu_item["meta"]["socket"]
    cpu_ppt = cpu_item["meta"]["ppt_w"]
    cpu_price = cpu_item["price"]

    pool = [m for m in mbs if m["meta"]["socket"] == socket]
    if not pool:
        pool = list(mbs)

    # 405.kr 메인보드 전원부 & 칩셋 등급 페어링 규칙
    min_rank = 1
    if cpu_ppt >= 150 or cpu_price >= 500_000 or tier_grade == "high":
        min_rank = 3 if socket in ("AM5", "LGA1851") else 2
    elif cpu_ppt >= 110 or cpu_price >= 300_000 or tier_grade == "mid":
        min_rank = 2

    ranked = [m for m in pool if m["meta"]["chipset_rank"] >= min_rank]
    if not ranked:
        ranked = pool

    if tier_grade in ("mid", "high"):
        hs_pool = [m for m in ranked if m["meta"]["heatsink"]]
        if hs_pool:
            ranked = hs_pool

    if want_color == "white":
        w_pool = [m for m in ranked if m["meta"]["color"] == "white"]
        if w_pool:
            ranked = w_pool

    if tier_grade == "low":
        return min(ranked, key=lambda m: (m["price"], m["rec_rank"]))
    if tier_grade == "mid":
        # B650 / B850 / B760 / B860 중 가성비·추천순 상위
        mid_band = [m for m in ranked if m["price"] <= 280_000] or ranked
        return min(mid_band, key=lambda m: (m["price"], m["rec_rank"]))
    # 최고가: B850 상위(TUF/AORUS) 또는 X870E/Z890 (단, 50만원 이하 합리적 상급 보드 우선)
    high_band = [m for m in ranked if 200_000 <= m["price"] <= 480_000] or ranked
    return min(high_band, key=lambda m: (m["rec_rank"], -m["meta"]["chipset_rank"]))


def _pick_ram_for_mb(
    rams: list[dict[str, Any]],
    mb_item: dict[str, Any],
    cpu_item: dict[str, Any],
    target_gb: int,
    tier_grade: str,
    want_color: str,
) -> tuple[dict[str, Any], int]:
    mem_type = mb_item["meta"]["mem_type"]
    pool = [r for r in rams if r["meta"]["mem_type"] == mem_type]
    if cpu_item["meta"]["socket"] == "LGA1851":
        pool = [r for r in pool if not r["meta"]["amd_only"]] or pool
    if not pool:
        pool = list(rams)

    if tier_grade in ("mid", "high"):
        t1 = [r for r in pool if r["meta"]["tier1"]]
        if t1:
            pool = t1

    # 목표 용량 구성: 예) 32GB -> 16GB×2 또는 32GB×1(또는 32GB 패키지), 16GB -> 16GB×1 또는 8GB×2
    # 컴퓨존 단품 16GB × 2개 듀얼채널 구성이 가격·호환성에서 표준
    if target_gb >= 64:
        cands_32 = [r for r in pool if r["meta"]["gb"] == 32 and r["meta"]["kit"] == 1]
        cands_64 = [r for r in pool if r["meta"]["gb"] == 64]
        if cands_32:
            picked = min(cands_32, key=lambda r: (r["price"] if tier_grade == "low" else r["rec_rank"]))
            return picked, 2
        if cands_64:
            picked = min(cands_64, key=lambda r: r["price"])
            return picked, 1
    if target_gb >= 32:
        cands_16 = [r for r in pool if r["meta"]["gb"] == 16 and r["meta"]["kit"] == 1]
        cands_32kit = [r for r in pool if r["meta"]["gb"] == 32]
        if cands_16:
            picked = min(
                cands_16,
                key=lambda r: (r["price"], r["rec_rank"]) if tier_grade == "low" else (r["rec_rank"], r["price"]),
            )
            return picked, 2
        if cands_32kit:
            picked = min(cands_32kit, key=lambda r: r["price"])
            return picked, 1

    cands_16 = [r for r in pool if r["meta"]["gb"] == 16]
    if cands_16:
        picked = min(
            cands_16,
            key=lambda r: (r["price"], r["rec_rank"]) if tier_grade == "low" else (r["rec_rank"], r["price"]),
        )
        return picked, 1
    picked = min(pool, key=lambda r: r["price"])
    return picked, 1


def _pick_ssd(
    ssds: list[dict[str, Any]],
    target_gb: int,
    tier_grade: str,
) -> dict[str, Any]:
    pool = [s for s in ssds if s["meta"]["gb"] >= target_gb] or list(ssds)
    # 405.kr OS 디스크 등급 강제: 추천가·최고가 또는 1TB 이상에서는 TLC 우선, 추천/최고가는 DRAM 탑재 1군 우선
    if tier_grade in ("mid", "high"):
        tlc_pool = [s for s in pool if s["meta"]["nand"] == "TLC"]
        if tlc_pool:
            pool = tlc_pool
        t1_pool = [s for s in pool if s["meta"]["tier1"]]
        if t1_pool:
            pool = t1_pool
    if tier_grade == "high":
        dram_pool = [s for s in pool if s["meta"]["dram"]]
        if dram_pool:
            pool = dram_pool

    # 목표 용량과 동일한 용량대를 우선 선택
    exact_gb = [s for s in pool if s["meta"]["gb"] == target_gb]
    if exact_gb:
        pool = exact_gb

    if tier_grade == "low":
        return min(pool, key=lambda s: (s["price"], s["rec_rank"]))
    if tier_grade == "mid":
        return min(pool, key=lambda s: (s["price"], s["rec_rank"]))
    return min(pool, key=lambda s: (-int(s["meta"]["dram"]), s["rec_rank"], s["price"]))


def _pick_psu(
    psus: list[dict[str, Any]],
    cpu_item: dict[str, Any],
    gpu_item: dict[str, Any] | None,
    tier_grade: str,
    want_color: str,
) -> tuple[dict[str, Any], dict[str, int]]:
    cpu_w = cpu_item["meta"]["ppt_w"]
    gpu_w = gpu_item["meta"]["watts"] if gpu_item else 0
    gpu_pk = gpu_item["meta"]["peak_w"] if gpu_item else 0
    gpu_rec = gpu_item["meta"]["rec_psu"] if gpu_item else 450

    sustained_w = cpu_w + gpu_w + 100
    peak_w = cpu_w + gpu_pk + 100
    calc_w = int(math.ceil(peak_w * 1.30 / 50.0) * 50)
    need_w = max(calc_w, gpu_rec, 500)

    pool = [p for p in psus if p["meta"]["watts"] >= need_w] or list(psus)
    if tier_grade in ("mid", "high"):
        non_bulk = [p for p in pool if not p["meta"]["bulk"]]
        if non_bulk:
            pool = non_bulk

    # RTX 50 계열 고전력 또는 추천/최고가 구성은 ATX 3.1 우선
    need_atx3 = (gpu_item is not None and gpu_item["meta"]["watts"] >= 180) or tier_grade in ("mid", "high")
    if need_atx3:
        atx3_pool = [p for p in pool if p["meta"]["atx3"]]
        if atx3_pool:
            pool = atx3_pool

    if tier_grade == "high" or (cpu_w + gpu_w) >= 380:
        gold_pool = [p for p in pool if p["meta"]["eff_rank"] >= 3]
        if gold_pool:
            pool = gold_pool
    elif tier_grade == "mid":
        brz_pool = [p for p in pool if p["meta"]["eff_rank"] >= 2]
        if brz_pool:
            pool = brz_pool

    if want_color == "white":
        w_pool = [p for p in pool if p["meta"]["color"] == "white"]
        if w_pool:
            pool = w_pool

    if tier_grade == "low":
        picked = min(pool, key=lambda p: (p["price"], p["rec_rank"]))
    elif tier_grade == "mid":
        reasonable = [p for p in pool if p["meta"]["watts"] <= need_w + 150] or pool
        picked = min(reasonable, key=lambda p: (p["price"], p["rec_rank"]))
    else:
        reasonable_high = [p for p in pool if p["meta"]["watts"] <= need_w + 250] or pool
        mod_pool = [p for p in reasonable_high if p["meta"]["modular"]] or reasonable_high
        picked = min(mod_pool, key=lambda p: (p["rec_rank"], p["price"]))

    return picked, {"sustained_w": sustained_w, "peak_w": peak_w, "calc_w": calc_w, "rec_w": gpu_rec, "need_w": need_w}


def _pick_cooler(
    coolers: list[dict[str, Any]],
    cpu_item: dict[str, Any],
    tier_grade: str,
    want_color: str,
) -> dict[str, Any] | None:
    cpu_ppt = cpu_item["meta"]["ppt_w"]
    has_stock = cpu_item["meta"]["has_cooler"]

    # 최저가 견적에서 65W 이하 기본 쿨러 포함 CPU는 기본 쿨러 사용으로 비용 절감
    if tier_grade == "low" and has_stock and cpu_ppt <= 90:
        return None

    pool = [c for c in coolers if c["meta"]["tdp_w"] >= cpu_ppt] or list(coolers)
    if want_color == "white":
        w_pool = [c for c in pool if c["meta"]["color"] == "white"]
        if w_pool:
            pool = w_pool

    if cpu_ppt >= 180 or (tier_grade == "high" and cpu_ppt >= 140):
        # 고발열 CPU 또는 최고가 고성능 CPU: 360mm 수랭 또는 상급 듀얼타워
        high_cool = [c for c in pool if c["meta"]["rank"] >= 3] or pool
        if tier_grade == "high":
            aio_pool = [c for c in high_cool if c["meta"]["aio"]] or high_cool
            return min(aio_pool, key=lambda c: (c["rec_rank"], c["price"]))
        return min(high_cool, key=lambda c: (c["price"], c["rec_rank"]))

    if tier_grade == "low":
        air_pool = [c for c in pool if not c["meta"]["aio"]] or pool
        return min(air_pool, key=lambda c: (c["price"], c["rec_rank"]))
    if tier_grade == "mid":
        # 추천가: 싱글/듀얼타워 공랭 가성비 상위
        air_pool = [c for c in pool if not c["meta"]["aio"]] or pool
        return min(air_pool, key=lambda c: (c["price"], c["rec_rank"]))
    # 최고가: 듀얼타워 공랭 또는 수랭 쿨러
    dual_or_aio = [c for c in pool if c["meta"]["rank"] >= 3] or pool
    return min(dual_or_aio, key=lambda c: (c["rec_rank"], c["price"]))


def _pick_case(
    cases: list[dict[str, Any]],
    mb_item: dict[str, Any],
    gpu_item: dict[str, Any] | None,
    cooler_item: dict[str, Any] | None,
    tier_grade: str,
    want_color: str,
) -> dict[str, Any]:
    pool = list(cases)
    if mb_item["meta"]["form_factor"] == "ATX":
        atx_pool = [c for c in pool if c["meta"]["supports_atx"]]
        if atx_pool:
            pool = atx_pool

    hot_build = (gpu_item is not None and gpu_item["meta"]["watts"] >= 200) or (
        cooler_item is not None and cooler_item["meta"]["aio"]
    )
    if hot_build:
        mesh_pool = [c for c in pool if c["meta"]["mesh"]]
        if mesh_pool:
            pool = mesh_pool

    if want_color in ("white", "black"):
        c_pool = [c for c in pool if _color_match(c, want_color)]
        if c_pool:
            pool = c_pool

    if tier_grade == "low":
        return min(pool, key=lambda c: (c["price"], c["rec_rank"]))
    if tier_grade == "mid":
        return min(pool, key=lambda c: (c["rec_rank"], c["price"]))
    high_cases = [c for c in pool if c["price"] >= 55_000] or pool
    return min(high_cases, key=lambda c: (c["rec_rank"], -c["meta"]["fans"]))


# ---------------------------------------------------------------------------
# 4. 부품별 선정 사유(reasonOf) · 예상 프레임(fpsEst) · 조립 가이드 생성
# ---------------------------------------------------------------------------

def _build_part_reasons(
    cfg: dict[str, Any],
    psu_info: dict[str, int],
    floors_info: dict[str, Any],
    tier_grade: str,
) -> dict[str, str]:
    cpu = cfg["cpu"]
    mb = cfg["motherboard"]
    ram = cfg["ram"]
    ram_qty = cfg["ram_qty"]
    gpu = cfg.get("gpu")
    ssd = cfg["ssd"]
    cooler = cfg.get("cooler")
    case = cfg["case"]
    psu = cfg["psu"]

    reasons: dict[str, str] = {}
    cm = cpu["meta"]
    reasons["CPU"] = " · ".join(
        filter(
            None,
            [
                f"{cm['cores']}코어 / 소켓 {cm['socket']}",
                "3D V-Cache 게이밍 특화" if cm["x3d"] else ("내장그래픽 탑재(외장GPU 불필요)" if not gpu else f"게임지수 {cm['cpg']}"),
                f"실소비 약 {cm['ppt_w']}W 기준",
            ],
        )
    )

    if cooler:
        clm = cooler["meta"]
        reasons["쿨러/특수냉각"] = f"{clm['kind_label']} · 냉각 {clm['tdp_w']}W급 (CPU 발열 {cm['ppt_w']}W 완벽 제어)"

    mm = mb["meta"]
    reasons["메인보드"] = " · ".join(
        filter(
            None,
            [
                f"{mm['chipset']} 칩셋 ({mm['socket']} · {mm['form_factor']})",
                f"전원부 {mm['vrm_phases']}페이즈급",
                "전원부 방열판 탑재" if mm["heatsink"] else "실속형 구성",
                "Wi-Fi 내장" if mm["wifi"] else None,
            ],
        )
    )

    rm = ram["meta"]
    total_ram_gb = rm["gb"] * ram_qty
    reasons["메모리"] = " · ".join(
        filter(
            None,
            [
                f"{rm['mem_type']}-{rm['clock']} 총 {total_ram_gb}GB ({rm['gb']}GB×{ram_qty})",
                "듀얼채널 대역폭 구성" if ram_qty >= 2 else "단일 채널(추후 확장 용이)",
                "검증된 1군 메모리" if rm["tier1"] else None,
            ],
        )
    )

    if gpu:
        gm = gpu["meta"]
        reasons["그래픽카드"] = " · ".join(
            filter(
                None,
                [
                    f"{gm['chipset']} (VRAM {gm['vram']}GB · 성능지수 {gm['gi']})",
                    f"{floors_info['resolution']} {floors_info['target_fps']}fps 타깃 매칭",
                    "DLSS 4 MFG 지원" if gm["mfg"] else ("프레임 생성(FG) 지원" if gm["fg"] else None),
                ],
            )
        )

    sm = ssd["meta"]
    cap_str = f"{sm['gb'] // 1000}TB" if sm["gb"] >= 1000 else f"{sm['gb']}GB"
    reasons["SSD"] = " · ".join(
        filter(
            None,
            [
                f"PCIe Gen{sm['gen']} NVMe {cap_str} (읽기 {sm['read_mb']}MB/s)",
                f"{sm['nand']} 낸드",
                "DRAM 캐시 탑재(속도유지·수명 우수)" if sm["dram"] else "HMB 가성비 설계",
            ],
        )
    )

    csm = case["meta"]
    reasons["케이스"] = " · ".join(
        filter(
            None,
            [
                "전면 메쉬 고풍량 흡기 설계" if csm["mesh"] else "모던 디자인 케이스",
                f"기본 쿨링팬 {csm['fans']}개 장착",
                f"{mb['meta']['form_factor']} 보드 및 그래픽카드 장착 호환 검증",
            ],
        )
    )

    pm = psu["meta"]
    reasons["파워"] = " · ".join(
        filter(
            None,
            [
                f"부품 소비 {psu_info['sustained_w']}W · 피크 {psu_info['peak_w']}W · 여유 +30% → 권장 {psu_info['need_w']}W 이상",
                f"정격 {pm['watts']}W ({pm['eff_label']})",
                "ATX 3.1 (12V-2×6 차세대 규격)" if pm["atx3"] else None,
                "풀모듈러" if pm["modular"] else None,
            ],
        )
    )
    reasons["조립비"] = "컴퓨존 전문 엔지니어 하드웨어 조립 + 선정리 + 초기 불량 검수 포함"
    return reasons


def _estimate_game_fps(
    cfg: dict[str, Any],
    floors_info: dict[str, Any],
) -> list[dict[str, Any]]:
    gpu = cfg.get("gpu")
    if not gpu:
        return []
    cpu = cfg["cpu"]
    gi = gpu["meta"]["gi"]
    cpg = cpu["meta"]["cpg"]
    res_key = floors_info["resolution"]
    res = RESOLUTION_INFO.get(res_key, RESOLUTION_INFO["FHD"])
    boost = floors_info.get("boost", False)

    out: list[dict[str, Any]] = []
    for gid in floors_info.get("games", [])[:6]:
        g = GAME_MAP.get(gid)
        if not g:
            continue
        per = g["giPerFps"][res["idx"]]
        if per <= 0:
            continue
        u_mult = 1.35 if boost else 1.0
        f_mult = 1.45 if (boost and g.get("fg") and gpu["meta"]["fg"]) else 1.0
        gpu_fps = (gi / per) * u_mult * f_mult
        cpu_cap = 113.0 * math.pow(cpg / 100.0, 0.48) / (g.get("cpuLoad", 1.0)) * f_mult
        raw_fps = min(gpu_fps, cpu_cap)
        if g.get("cap"):
            raw_fps = min(raw_fps, g["cap"])
        est = max(30, int(round(raw_fps)))
        lo = int(round(est * 0.90))
        hi = int(round(est * 1.10))
        base_no_fg = min(gpu_fps / f_mult, cpu_cap / f_mult)
        if g.get("cap"):
            base_no_fg = min(base_no_fg, g["cap"])
        low_ratio = g["low"][res["idx"]]
        low_1pct = max(20, int(round(base_no_fg * low_ratio)))
        bottleneck = "엔진 상한" if (g.get("cap") and raw_fps >= g["cap"]) else ("GPU" if gpu_fps <= cpu_cap else "CPU")
        out.append(
            {
                "id": gid,
                "label": g["label"],
                "resolution": res["ko"],
                "fps": est,
                "range": f"{lo}~{hi} fps",
                "low_1pct": f"{low_1pct} fps",
                "bottleneck": bottleneck,
                "boosted": bool(u_mult > 1.0 or f_mult > 1.0),
            }
        )
    return out


def _build_assembly_notes(cfg: dict[str, Any], psu_info: dict[str, int]) -> list[str]:
    cpu = cfg["cpu"]
    mb = cfg["motherboard"]
    ram = cfg["ram"]
    gpu = cfg.get("gpu")
    cooler = cfg.get("cooler")
    psu = cfg["psu"]

    notes = [
        f"호환성 검증 완료: CPU 소켓({cpu['meta']['socket']}) ↔ 메인보드({mb['meta']['chipset']}) ↔ 메모리({ram['meta']['mem_type']}-{ram['meta']['clock']}) 규격이 정확히 일치합니다.",
    ]
    if gpu:
        notes.append(
            f"전력 밸런스 검증: 예상 실소비 {psu_info['sustained_w']}W (순간 피크 {psu_info['peak_w']}W) 대비 정격 {psu['meta']['watts']}W 파워로 여유 있게 설계되었습니다."
        )
        if gpu["meta"]["watts"] >= 220 and psu["meta"]["atx3"]:
            notes.append("ATX 3.1 네이티브 파워 적용: 고전력 그래픽카드 12V-2×6 전원 케이블을 변환 젠더 없이 직결하여 발열·접촉 불량을 예방합니다.")
    else:
        notes.append("내장그래픽 최적화 구성: 외장 그래픽카드 없이 메인보드 디스플레이 출력 포트로 듀얼 모니터까지 바로 연결할 수 있습니다.")

    if cooler and cooler["meta"]["aio"]:
        notes.append("수랭 쿨러 장착 안내: 상단 라디에이터 장착 시 메모리 방열판 간섭 여부를 가조립 단계에서 먼저 확인하세요.")
    if ram["meta"]["clock"] >= 5600 and ram["meta"]["mem_type"] == "DDR5":
        notes.append(f"메모리 클럭 안내: 장착 후 BIOS에서 EXPO/XMP 프로파일을 활성화하면 표기 클럭({ram['meta']['clock']}MHz)으로 작동합니다.")
    return notes


def _diff_from_base(base_cfg: dict[str, Any], target_cfg: dict[str, Any]) -> str:
    diffs: list[str] = []
    if base_cfg.get("gpu") and target_cfg.get("gpu"):
        g1 = base_cfg["gpu"]["meta"]["chipset"]
        g2 = target_cfg["gpu"]["meta"]["chipset"]
        if g1 != g2:
            diffs.append(f"그래픽카드 {g1} → {g2}")
        elif base_cfg["gpu"]["pno"] != target_cfg["gpu"]["pno"]:
            diffs.append(f"그래픽카드 상급 라인업({g2})")
    if base_cfg["cpu"]["pno"] != target_cfg["cpu"]["pno"]:
        c1 = base_cfg["cpu"]["name"].split("(")[0].replace("[AMD]", "").replace("[INTEL]", "").strip()
        c2 = target_cfg["cpu"]["name"].split("(")[0].replace("[AMD]", "").replace("[INTEL]", "").strip()
        diffs.append(f"CPU {c1} → {c2}")
    if base_cfg["motherboard"]["meta"]["chipset"] != target_cfg["motherboard"]["meta"]["chipset"]:
        diffs.append(f"메인보드 {base_cfg['motherboard']['meta']['chipset']} → {target_cfg['motherboard']['meta']['chipset']}")
    r1 = base_cfg["ram"]["meta"]["gb"] * base_cfg["ram_qty"]
    r2 = target_cfg["ram"]["meta"]["gb"] * target_cfg["ram_qty"]
    if r1 != r2:
        diffs.append(f"메모리 {r1}GB → {r2}GB")
    s1 = base_cfg["ssd"]["meta"]["gb"]
    s2 = target_cfg["ssd"]["meta"]["gb"]
    if s1 != s2:
        diffs.append(f"SSD {s1 // 1000 if s1 >= 1000 else s1}{'TB' if s1 >= 1000 else 'GB'} → {s2 // 1000 if s2 >= 1000 else s2}{'TB' if s2 >= 1000 else 'GB'}")
    elif not base_cfg["ssd"]["meta"]["dram"] and target_cfg["ssd"]["meta"]["dram"]:
        diffs.append("SSD DRAM 탑재 상급 모델")
    if base_cfg["psu"]["meta"]["watts"] != target_cfg["psu"]["meta"]["watts"]:
        diffs.append(f"파워 {base_cfg['psu']['meta']['watts']}W → {target_cfg['psu']['meta']['watts']}W")
    return ("최저가 대비 ↑ " + " · ".join(diffs)) if diffs else "컴퓨존 추천 상위 등급 부품 구성"


# ---------------------------------------------------------------------------
# 5. 최저가 · 추천가 · 최고가 3종 견적 동시 빌더
# ---------------------------------------------------------------------------

def _build_single_tier_cfg(
    parts_pool: dict[str, list[dict[str, Any]]],
    floors_info: dict[str, Any],
    tier_grade: str,
    cpu_vendor: str = "auto",
    gpu_vendor: str = "auto",
    ram_gb_override: int = 0,
    ssd_gb_override: int = 0,
    want_color: str = "auto",
    part_overrides: dict[str, int] | None = None,
) -> tuple[dict[str, Any], dict[str, int]]:
    tier_req = dict(floors_info["tiers"][tier_grade])
    need_gpu = floors_info["need_gpu"]
    is_game = floors_info["usage"].startswith("game_") or bool(floors_info.get("games"))
    prefer_nvidia = floors_info["prefer_nvidia"]
    overrides = part_overrides or {}

    def _find_by_pno(cat_key: str, pno: int) -> dict[str, Any] | None:
        if not pno:
            return None
        return next((x for x in parts_pool.get(cat_key, []) if x["pno"] == int(pno)), None)

    # 1) GPU 선정 (오버라이드 우선)
    gpu_item = _find_by_pno("gpu", overrides.get("그래픽카드", 0))
    if not gpu_item and need_gpu:
        gpu_item = _pick_gpu_for_tier(
            parts_pool["gpu"],
            tier_req,
            tier_grade,
            gpu_vendor,
            prefer_nvidia,
            want_color,
        )

    # 2) CPU 선정 (오버라이드 우선, 그래픽카드 유무에 따라 내장그래픽 필수 여부 반영)
    need_igpu = gpu_item is None
    cpu_item = _find_by_pno("cpu", overrides.get("CPU", 0))
    if not cpu_item:
        cpu_item = _pick_cpu_for_tier(
            parts_pool["cpu"],
            tier_req,
            tier_grade,
            need_igpu,
            is_game,
            cpu_vendor,
            gpu_item,
        )

    # 3) 메인보드 선정 (CPU 소켓에 자동 맞춤; 오버라이드가 있어도 소켓이 다르면 자동 재적합)
    mb_item = _find_by_pno("motherboard", overrides.get("메인보드", 0))
    if not mb_item or mb_item["meta"]["socket"] != cpu_item["meta"]["socket"]:
        mb_item = _pick_mb_for_cpu(parts_pool["motherboard"], cpu_item, tier_grade, want_color)

    # 4) 메모리 선정 (메인보드 DDR4/DDR5 규격에 자동 맞춤)
    target_ram_gb = int(ram_gb_override) if ram_gb_override > 0 else tier_req.get("ram", 16)
    ram_item = _find_by_pno("ram", overrides.get("메모리", 0))
    if ram_item and ram_item["meta"]["mem_type"] == mb_item["meta"]["mem_type"]:
        ram_qty = max(1, target_ram_gb // max(8, ram_item["meta"]["gb"]))
    else:
        ram_item, ram_qty = _pick_ram_for_mb(
            parts_pool["ram"], mb_item, cpu_item, target_ram_gb, tier_grade, want_color
        )

    # 5) SSD 선정
    target_ssd_gb = int(ssd_gb_override) if ssd_gb_override > 0 else tier_req.get("ssd", 1000)
    ssd_item = _find_by_pno("ssd", overrides.get("SSD", 0))
    if not ssd_item:
        ssd_item = _pick_ssd(parts_pool["ssd"], target_ssd_gb, tier_grade)

    # 6) 파워 & 쿨러 자동 지정
    psu_override = _find_by_pno("psu", overrides.get("파워", 0))
    psu_item, psu_info = _pick_psu(parts_pool["psu"], cpu_item, gpu_item, tier_grade, want_color)
    if psu_override and psu_override["meta"]["watts"] >= psu_info["need_w"]:
        psu_item = psu_override

    cooler_override = _find_by_pno("cooler", overrides.get("쿨러/특수냉각", 0))
    cooler_item = cooler_override or _pick_cooler(parts_pool["cooler"], cpu_item, tier_grade, want_color)

    # 7) 케이스 선정
    case_override = _find_by_pno("case", overrides.get("케이스", 0))
    case_item = case_override or _pick_case(
        parts_pool["case"], mb_item, gpu_item, cooler_item, tier_grade, want_color
    )

    cfg = {
        "cpu": cpu_item,
        "cooler": cooler_item,
        "motherboard": mb_item,
        "ram": ram_item,
        "ram_qty": ram_qty,
        "gpu": gpu_item,
        "ssd": ssd_item,
        "case": case_item,
        "psu": psu_item,
    }
    return cfg, psu_info


def _cfg_body_total(cfg: dict[str, Any]) -> int:
    total = (
        cfg["cpu"]["price"]
        + cfg["motherboard"]["price"]
        + cfg["ram"]["price"] * cfg["ram_qty"]
        + cfg["ssd"]["price"]
        + cfg["case"]["price"]
        + cfg["psu"]["price"]
        + COMPUZONE_ASSEMBLY_PART[2]
    )
    if cfg.get("cooler"):
        total += cfg["cooler"]["price"]
    if cfg.get("gpu"):
        total += cfg["gpu"]["price"]
    return total


def _upgrade_cfg_to_differentiate(
    cfg: dict[str, Any],
    parts_pool: dict[str, list[dict[str, Any]]],
    floors_info: dict[str, Any],
    tier_grade: str,
    min_total_exclusive: int,
    budget_limit: int,
    want_color: str,
) -> tuple[dict[str, Any], dict[str, int]]:
    """추천가/최고가가 이전 단계와 동일하거나 금액이 역전되지 않도록 한 단계 상위 부품으로 승급한다."""
    cur = dict(cfg)
    # 1순위: GPU 한 체급 위
    if cur.get("gpu"):
        cur_gi = cur["gpu"]["meta"]["gi"]
        cur_p = cur["gpu"]["price"]
        vendor = cur["gpu"]["meta"]["vendor"]
        ups = [
            g
            for g in parts_pool["gpu"]
            if g["meta"]["gi"] > cur_gi and g["price"] > cur_p and g["price"] <= cur_p + 850_000
        ]
        if not ups:
            ups = [
                g
                for g in parts_pool["gpu"]
                if g["meta"]["gi"] >= cur_gi and g["price"] > cur_p and g["meta"]["vendor"] == vendor
            ]
        if ups:
            cand_gpu = min(ups, key=lambda g: (g["price"], g["rec_rank"]))
            test_cfg = dict(cur, gpu=cand_gpu)
            if not budget_limit or _cfg_body_total(test_cfg) <= budget_limit * 1.08:
                cur = test_cfg

    # 2순위: CPU 한 체급 위 (같은 소켓 또는 AM5)
    if _cfg_body_total(cur) <= min_total_exclusive:
        cur_cpg = cur["cpu"]["meta"]["cpg"]
        cur_p = cur["cpu"]["price"]
        ups_cpu = [
            c
            for c in parts_pool["cpu"]
            if c["meta"]["cpg"] > cur_cpg
            and c["price"] > cur_p
            and c["price"] <= cur_p + 450_000
            and (not cur.get("gpu") and c["meta"]["igpu"] or cur.get("gpu") and not c["meta"]["apu"])
        ]
        if ups_cpu:
            cand_cpu = min(ups_cpu, key=lambda c: (c["price"], c["rec_rank"]))
            cand_mb = _pick_mb_for_cpu(parts_pool["motherboard"], cand_cpu, tier_grade, want_color)
            cand_ram, cand_qty = _pick_ram_for_mb(
                parts_pool["ram"],
                cand_mb,
                cand_cpu,
                cur["ram"]["meta"]["gb"] * cur["ram_qty"],
                tier_grade,
                want_color,
            )
            test_cfg = dict(cur, cpu=cand_cpu, motherboard=cand_mb, ram=cand_ram, ram_qty=cand_qty)
            if not budget_limit or _cfg_body_total(test_cfg) <= budget_limit * 1.08:
                cur = test_cfg

    # 3순위: SSD 용량/등급 상향
    if _cfg_body_total(cur) <= min_total_exclusive:
        cur_ssd_p = cur["ssd"]["price"]
        ups_ssd = [
            s
            for s in parts_pool["ssd"]
            if s["meta"]["gb"] >= cur["ssd"]["meta"]["gb"]
            and s["price"] > cur_ssd_p
            and s["meta"]["nand"] == "TLC"
        ]
        if ups_ssd:
            cur["ssd"] = min(ups_ssd, key=lambda s: s["price"])

    psu_item, psu_info = _pick_psu(parts_pool["psu"], cur["cpu"], cur.get("gpu"), tier_grade, want_color)
    cur["psu"] = psu_item
    if not cur.get("cooler") or cur["cpu"]["meta"]["ppt_w"] > cur["cooler"]["meta"]["tdp_w"]:
        cur["cooler"] = _pick_cooler(parts_pool["cooler"], cur["cpu"], tier_grade, want_color)
    return cur, psu_info


def _short_cpu_label(cpu_name: str) -> str:
    cleaned = re.sub(r"프로세서\s*", "", cpu_name)
    m = re.search(
        r"(9950X3D2|9950X3D|9800X3D|7800X3D|5600X3D|9900X|9700X|9600X|7500F|5600|5500GT|Ultra\s*[579]\s*\d{3}[A-Z]*(?:\s*PLUS)?|i[3579]-\d{4,5}[A-Z]*)",
        cleaned,
        re.I,
    )
    return m.group(1).strip() if m else cleaned[:18].strip()


def _cfg_to_quote(
    tier_title: str,
    tier_key: str,
    tab_label: str,
    badge_desc: str,
    cfg: dict[str, Any],
    psu_info: dict[str, int],
    floors_info: dict[str, Any],
    tier_grade: str,
    base_cfg: dict[str, Any] | None,
    include_monitor: bool,
    include_keyboard: bool,
    include_setup: bool,
    synced_at: str,
    source_kind: str,
) -> tuple[Quote, dict[str, Any]]:
    reasons = _build_part_reasons(cfg, psu_info, floors_info, tier_grade)
    raw_rows: list[tuple[str, str, int, int, int]] = []

    raw_rows.append(("CPU", cfg["cpu"]["name"], cfg["cpu"]["price"], 1, cfg["cpu"]["pno"]))
    if cfg.get("cooler"):
        raw_rows.append(("쿨러/특수냉각", cfg["cooler"]["name"], cfg["cooler"]["price"], 1, cfg["cooler"]["pno"]))
    raw_rows.append(("메인보드", cfg["motherboard"]["name"], cfg["motherboard"]["price"], 1, cfg["motherboard"]["pno"]))
    raw_rows.append(("메모리", cfg["ram"]["name"], cfg["ram"]["price"], cfg["ram_qty"], cfg["ram"]["pno"]))
    if cfg.get("gpu"):
        raw_rows.append(("그래픽카드", cfg["gpu"]["name"], cfg["gpu"]["price"], 1, cfg["gpu"]["pno"]))
    raw_rows.append(("SSD", cfg["ssd"]["name"], cfg["ssd"]["price"], 1, cfg["ssd"]["pno"]))
    raw_rows.append(("케이스", cfg["case"]["name"], cfg["case"]["price"], 1, cfg["case"]["pno"]))
    raw_rows.append(("파워", cfg["psu"]["name"], cfg["psu"]["price"], 1, cfg["psu"]["pno"]))
    asm_cat, asm_name, asm_price, asm_qty, asm_pno = COMPUZONE_ASSEMBLY_PART
    raw_rows.append((asm_cat, asm_name, asm_price, asm_qty, asm_pno))

    raw_rows.sort(key=lambda x: _sort_key(x[0]))
    extras = _active_extras(include_monitor, include_keyboard, include_setup)

    parts: list[QuotePart] = []
    part_details: list[dict[str, Any]] = []
    subtotal = 0

    for i, (cat, name, price, qty, pno) in enumerate(raw_rows, 1):
        amount = price * qty
        subtotal += amount
        parts.append(QuotePart(no=i, category=cat, name=name, qty=qty, unit_price=price, amount=amount))
        part_details.append(
            {
                "no": i,
                "category": cat,
                "name": name,
                "qty": qty,
                "unit_price": price,
                "amount": amount,
                "pno": pno,
                "url": BASE_PRODUCT_URL.format(pno=pno),
                "reason": reasons.get(cat, ""),
            }
        )

    start_no = len(parts) + 1
    for j, (cat, name, price, qty) in enumerate(extras, start_no):
        amount = price * qty
        subtotal += amount
        parts.append(QuotePart(no=j, category=cat, name=name, qty=qty, unit_price=price, amount=amount))

    cpu_short = _short_cpu_label(cfg["cpu"]["name"])
    gpu_short = cfg["gpu"]["meta"]["chipset"] if cfg.get("gpu") else "내장그래픽"
    ram_total = cfg["ram"]["meta"]["gb"] * cfg["ram_qty"]
    ssd_gb = cfg["ssd"]["meta"]["gb"]
    ssd_short = f"{ssd_gb // 1000}TB" if ssd_gb >= 1000 else f"{ssd_gb}GB"
    model_name = f"[{floors_info['usage_short']} {tab_label}] {cpu_short} · {gpu_short} · {ram_total}GB · {ssd_short}"

    diff_line = _diff_from_base(base_cfg, cfg) if base_cfg else "용도 최소 요구사양 충족 가성비 최적 조합"
    desc = f"{badge_desc} | {diff_line}"
    rep_pno = cfg["gpu"]["pno"] if cfg.get("gpu") else cfg["cpu"]["pno"]
    rep_url = BASE_PRODUCT_URL.format(pno=rep_pno)

    q = Quote(
        tier=tier_title,
        tier_key=tier_key,
        model=model_name,
        tab_label=tab_label,
        description=desc,
        product_no=rep_pno,
        url=rep_url,
        quote_date=date.today().isoformat(),
        parts=parts,
        parts_subtotal=subtotal,
        total=subtotal,
        price_source="live" if source_kind == "live" else "fallback",
        fetched_at=synced_at,
    )
    meta = {
        "tier_key": tier_key,
        "tab_label": tab_label,
        "tier_grade": tier_grade,
        "badge_desc": badge_desc,
        "diff_line": diff_line,
        "part_reasons": reasons,
        "part_details": part_details,
        "selected_pnos": {d["category"]: d["pno"] for d in part_details if d.get("pno")},
        "fps_estimates": _estimate_game_fps(cfg, floors_info),
        "assembly_notes": _build_assembly_notes(cfg, psu_info),
        "power_summary": psu_info,
    }
    return q, meta


def _build_three_tier_cfgs(
    parts_pool: dict[str, list[dict[str, Any]]],
    floors_info: dict[str, Any],
    tier_overrides: dict[str, dict[str, int]],
    cpu_vendor: str,
    gpu_vendor: str,
    ram_gb: int,
    ssd_gb: int,
    color: str,
    budget_won: int,
) -> dict[str, tuple[dict[str, Any], dict[str, int]]]:
    """최저가 → 추천가 → 최고가 순서로 구성한다. 교체 부품이 없는 등급은 앞 등급보다 비싸지도록 승급한다."""
    out: dict[str, tuple[dict[str, Any], dict[str, int]]] = {}
    prev_total = 0
    for tier_key, grade in (("custom-low", "low"), ("custom-mid", "mid"), ("custom-high", "high")):
        cfg, psu = _build_single_tier_cfg(
            parts_pool,
            floors_info,
            grade,
            cpu_vendor=cpu_vendor,
            gpu_vendor=gpu_vendor,
            ram_gb_override=ram_gb,
            ssd_gb_override=ssd_gb,
            want_color=color,
            part_overrides=tier_overrides.get(tier_key),
        )
        if grade != "low" and not tier_overrides.get(tier_key) and _cfg_body_total(cfg) <= prev_total:
            cfg, psu = _upgrade_cfg_to_differentiate(
                cfg, parts_pool, floors_info, grade, prev_total, budget_won, color
            )
        out[tier_key] = (cfg, psu)
        prev_total = _cfg_body_total(cfg)
    return out


SWAP_CATS: tuple[tuple[str, str], ...] = (
    ("CPU", "cpu"),
    ("쿨러/특수냉각", "cooler"),
    ("메인보드", "motherboard"),
    ("메모리", "ram"),
    ("그래픽카드", "gpu"),
    ("SSD", "ssd"),
    ("케이스", "case"),
    ("파워", "psu"),
)


def _part_compat(cat_key: str, item: dict[str, Any], cfg: dict[str, Any], psu_info: dict[str, int]) -> tuple[bool, str]:
    """현재 구성에 이 후보 부품을 넣었을 때의 호환 여부와 한 줄 사유.
    소켓 · 메모리 규격 · 파워 용량은 선택하면 서버가 자동으로 맞추므로 '자동 맞춤'으로 안내한다."""
    m = item["meta"]
    cpu_m = cfg["cpu"]["meta"]
    mb_m = cfg["motherboard"]["meta"]
    if cat_key == "cpu":
        if not cfg.get("gpu") and not m.get("igpu"):
            return False, "내장그래픽 없음 — 그래픽카드가 없는 구성이라 화면 출력 불가"
        if m["socket"] != mb_m["socket"]:
            return True, f"{m['socket']} 소켓 — 선택 시 호환 메인보드 · 메모리로 자동 맞춤"
        return True, f"{m['socket']} 소켓 — 현재 메인보드와 바로 호환"
    if cat_key == "motherboard":
        if m["socket"] != cpu_m["socket"]:
            return False, f"소켓 불일치 ({m['socket']} ≠ CPU {cpu_m['socket']})"
        if m["form_factor"] == "ATX" and not cfg["case"]["meta"].get("supports_atx"):
            return False, "ATX 보드 — 현재 케이스(M-ATX 전용)에 장착 불가"
        if m["mem_type"] != cfg["ram"]["meta"]["mem_type"]:
            return True, f"{m['mem_type']} 보드 — 선택 시 {m['mem_type']} 메모리로 자동 맞춤"
        return True, f"{m['chipset']} · {m['mem_type']} — 바로 호환"
    if cat_key == "ram":
        if m["mem_type"] != mb_m["mem_type"]:
            return False, f"메모리 규격 불일치 ({m['mem_type']} ≠ 메인보드 {mb_m['mem_type']})"
        if cpu_m["socket"] == "LGA1851" and m.get("amd_only"):
            return False, "AMD 전용 메모리 — 인텔 애로우레이크 보드 호환 불가"
        return True, f"{m['mem_type']}-{m['clock']} — 바로 호환"
    if cat_key == "cooler":
        if m["tdp_w"] < cpu_m["ppt_w"]:
            return False, f"냉각 용량 부족 (쿨러 {m['tdp_w']}W < CPU 발열 {cpu_m['ppt_w']}W)"
        return True, f"{m['kind_label']} — CPU 발열 {cpu_m['ppt_w']}W 대응"
    if cat_key == "gpu":
        if m["rec_psu"] > cfg["psu"]["meta"]["watts"]:
            return True, f"권장 파워 {m['rec_psu']}W — 선택 시 파워 자동 상향"
        return True, f"권장 파워 {m['rec_psu']}W — 현재 파워로 충분"
    if cat_key == "case":
        if mb_m["form_factor"] == "ATX" and not m.get("supports_atx"):
            return False, "M-ATX 전용 — 현재 ATX 메인보드 장착 불가"
        return True, "현재 메인보드 장착 가능"
    if cat_key == "psu":
        if m["watts"] < psu_info["need_w"]:
            return False, f"용량 부족 ({m['watts']}W < 권장 {psu_info['need_w']}W)"
        return True, f"권장 {psu_info['need_w']}W 충족 (정격 {m['watts']}W)"
    gb = int(m.get("gb") or 0)
    size = f"{gb // 1000}TB" if gb >= 1000 else f"{gb}GB"
    return True, f"{size} · Gen{m.get('gen', 4)} · {m.get('nand', '')}"


def _compat_issues(cfg: dict[str, Any], psu_info: dict[str, int]) -> list[str]:
    """직접 고른 쿨러 · 케이스처럼 서버가 자동으로 바꾸지 않는 부품 때문에 생긴 호환 문제."""
    cpu_m = cfg["cpu"]["meta"]
    mb_m = cfg["motherboard"]["meta"]
    cooler = cfg.get("cooler")
    issues: list[str] = []
    if not cfg.get("gpu") and not cpu_m.get("igpu"):
        issues.append("❌ 화면 출력 불가 — 그래픽카드가 없는데 내장그래픽이 없는 CPU입니다.")
    if cooler is None and not cpu_m.get("has_cooler"):
        issues.append("❌ 쿨러 누락 — 기본 쿨러가 없는 CPU라 CPU 쿨러를 따로 골라야 합니다.")
    if cooler is not None and cooler["meta"]["tdp_w"] < cpu_m["ppt_w"]:
        issues.append(
            f"⚠️ 쿨링 용량 부족 — CPU 발열 {cpu_m['ppt_w']}W 대비 쿨러 {cooler['meta']['tdp_w']}W라 쓰로틀링이 생길 수 있습니다."
        )
    if mb_m["form_factor"] == "ATX" and not cfg["case"]["meta"].get("supports_atx"):
        issues.append("❌ 케이스 크기 — ATX 메인보드는 M-ATX 전용 케이스에 들어가지 않습니다.")
    if cfg["psu"]["meta"]["watts"] < psu_info["need_w"]:
        issues.append(
            f"⚠️ 파워 용량 부족 — 권장 {psu_info['need_w']}W보다 정격 {cfg['psu']['meta']['watts']}W가 낮습니다."
        )
    return issues


def _swap_report(
    parts_pool: dict[str, list[dict[str, Any]]],
    cfg: dict[str, Any],
    psu_info: dict[str, int],
    base_cfg: dict[str, Any],
    overrides: dict[str, int],
) -> dict[str, Any]:
    """[부품 교체 및 재취합] 패널용: 원래 견적 대비 변경 부품 · 자동 보정 · 호환 경고 · 후보별 호환 여부."""
    changed: list[dict[str, Any]] = []
    auto_notes: list[str] = []
    for cat_ko, key in SWAP_CATS:
        cur = cfg.get(key)
        base = base_cfg.get(key)
        cur_pno = cur["pno"] if cur else 0
        base_pno = base["pno"] if base else 0
        if cur_pno != base_pno:
            changed.append({"category": cat_ko, "auto": cat_ko not in overrides})
        want = int(overrides.get(cat_ko) or 0)
        if want and want != cur_pno:
            got = cur["name"] if cur else "없음"
            auto_notes.append(f"{cat_ko}: 고른 부품이 현재 구성과 맞지 않아 {got}(으)로 자동 맞췄습니다.")
    for c in changed:
        if c["auto"]:
            auto_notes.append(f"{c['category']}: 다른 부품 교체에 맞춰 호환 부품으로 자동 연동했습니다.")

    part_compat: dict[str, dict[str, list[Any]]] = {}
    for cat_ko, key in SWAP_CATS:
        part_compat[cat_ko] = {
            str(p["pno"]): list(_part_compat(key, p, cfg, psu_info)) for p in parts_pool.get(key, [])
        }
    # 원래 견적(교체 전) 부품: 교체 창 왼쪽 '원래 견적' 칸에 나란히 보인다
    base_parts: list[dict[str, Any]] = []
    for cat_ko, key in SWAP_CATS:
        base = base_cfg.get(key)
        if not base:
            base_parts.append({"category": cat_ko, "name": "", "pno": 0, "unit_price": 0, "qty": 0})
            continue
        qty = int(base_cfg.get("ram_qty") or 1) if key == "ram" else 1
        base_parts.append({"category": cat_ko, "name": base["name"], "pno": base["pno"],
                           "unit_price": int(base["price"]), "qty": qty})
    return {
        "body_total": _cfg_body_total(cfg),
        "base_body_total": _cfg_body_total(base_cfg),
        "base_parts": base_parts,
        "changed_parts": changed,
        "auto_adjust_notes": auto_notes,
        "compat_issues": _compat_issues(cfg, psu_info),
        "part_compat": part_compat,
    }


def build_custom_quotes(
    usage: str = "game_fhd",
    games: list[str] | None = None,
    apps: list[str] | None = None,
    resolution: str = "",
    target_fps: int = 0,
    boost: bool = False,
    budget_manwon: int = 0,
    cpu_vendor: str = "auto",
    gpu_vendor: str = "auto",
    ram_gb: int = 0,
    ssd_gb: int = 0,
    color: str = "auto",
    overrides: dict[str, dict[str, int]] | None = None,
    include_monitor: bool = False,
    include_keyboard: bool = False,
    include_setup: bool = False,
    force_refresh: bool = False,
) -> tuple[TodayQuotesResult, dict[str, Any]]:
    """컴퓨존 추천 부품으로 [최저가 견적 · 추천가 견적 · 최고가 견적] 3종을 한 번에 산출한다."""
    catalog = fetch_compuzone_part_catalog(force_refresh=force_refresh)
    parts_pool = catalog["parts"]
    synced_at = catalog.get("synced_at") or datetime.now().isoformat(timespec="seconds")
    source_kind = catalog.get("source", "live")

    floors_info = calculate_floors(
        usage=usage,
        games=games,
        apps=apps,
        resolution=resolution,
        target_fps=target_fps,
        boost=boost,
    )
    budget_won = max(0, int(budget_manwon or 0) * 10_000)
    tier_overrides = overrides or {}
    build_kw = {
        "cpu_vendor": cpu_vendor,
        "gpu_vendor": gpu_vendor,
        "ram_gb": ram_gb,
        "ssd_gb": ssd_gb,
        "color": color,
        "budget_won": budget_won,
    }

    # 최저가(알뜰·가성비형) · 추천가(표준 밸런스형) · 최고가(고급·하이엔드형)
    built = _build_three_tier_cfgs(parts_pool, floors_info, tier_overrides, **build_kw)
    # 부품을 교체한 등급이 있으면 교체 전 원래 구성도 같은 조건으로 만들어 차액 · 변경 부품을 비교한다
    base_built = _build_three_tier_cfgs(parts_pool, floors_info, {}, **build_kw) if tier_overrides else built
    low_cfg, low_psu = built["custom-low"]
    mid_cfg, mid_psu = built["custom-mid"]
    high_cfg, high_psu = built["custom-high"]

    q_low, meta_low = _cfg_to_quote(
        tier_title="최저가 견적 (알뜰·가성비형)",
        tier_key="custom-low",
        tab_label="🟢 최저가 견적",
        badge_desc="목표 용도 최소 요구사양 충족 · 컴퓨존 추천 부품 가성비 조합",
        cfg=low_cfg,
        psu_info=low_psu,
        floors_info=floors_info,
        tier_grade="low",
        base_cfg=None,
        include_monitor=include_monitor,
        include_keyboard=include_keyboard,
        include_setup=include_setup,
        synced_at=synced_at,
        source_kind=source_kind,
    )
    q_mid, meta_mid = _cfg_to_quote(
        tier_title="추천가 견적 (표준 밸런스형)",
        tier_key="custom-mid",
        tab_label="🔵 추천가 견적",
        badge_desc="병목 없는 권장 사양 · 최신 DDR5 플랫폼 + 1군 SSD/파워 표준 조합",
        cfg=mid_cfg,
        psu_info=mid_psu,
        floors_info=floors_info,
        tier_grade="mid",
        base_cfg=low_cfg,
        include_monitor=include_monitor,
        include_keyboard=include_keyboard,
        include_setup=include_setup,
        synced_at=synced_at,
        source_kind=source_kind,
    )
    q_high, meta_high = _cfg_to_quote(
        tier_title="최고가 견적 (고급·하이엔드형)",
        tier_key="custom-high",
        tab_label="🟣 최고가 견적",
        badge_desc="상위 체급 GPU·CPU(X3D/다코어) · 골드 풀모듈러 파워 · 고성능 쿨링 하이엔드 조합",
        cfg=high_cfg,
        psu_info=high_psu,
        floors_info=floors_info,
        tier_grade="high",
        base_cfg=low_cfg,
        include_monitor=include_monitor,
        include_keyboard=include_keyboard,
        include_setup=include_setup,
        synced_at=synced_at,
        source_kind=source_kind,
    )

    for meta in (meta_low, meta_mid, meta_high):
        tk = meta["tier_key"]
        meta.update(
            _swap_report(
                parts_pool,
                built[tk][0],
                built[tk][1],
                base_built[tk][0],
                tier_overrides.get(tk) or {},
            )
        )

    quotes = [q_low, q_mid, q_high]
    result = TodayQuotesResult(
        quote_date=date.today().isoformat(),
        event_url=ONLINE_MAIN_URL,
        event_title=f"맞춤자동견적 ({floors_info['usage_short']})",
        series=f"컴퓨존 추천 부품 맞춤자동견적 — {floors_info['usage_label']}",
        quotes=quotes,
        logic_steps=[
            {"step": "1", "title": "용도·사양 역산", "desc": "게임 해상도·목표 프레임·작업 프로그램 기준으로 최소/권장/상위 사양 하한 계산"},
            {"step": "2", "title": "컴퓨존 추천 부품 수집", "desc": "컴퓨존 온라인 견적 추천순 부품을 실시간 수집하고 소켓·전력·크기 메타데이터 파싱"},
            {"step": "3", "title": "호환성·발열 자동 매칭", "desc": "CPU↔메인보드↔메모리 규격 일치 및 피크 전력+30% 여유 파워·쿨러·케이스 매칭"},
            {"step": "4", "title": "최저·추천·최고 3종 산출", "desc": "최저가(가성비)·추천가(표준 밸런스)·최고가(하이엔드) 부품별 단가 견적 동시 생성"},
        ],
        catalog_synced_at=synced_at,
        engine="custom_405_compuzone",
    )

    # 화면에서 드롭다운으로 부품 교체를 할 수 있도록 카테고리별 후보 목록 요약 제공
    part_candidates: dict[str, list[dict[str, Any]]] = {}
    for key, info in COMPUZONE_PART_DIVS.items():
        cat_name = info["category"]
        part_candidates[cat_name] = [
            {
                "pno": p["pno"],
                "name": p["name"],
                "price": p["price"],
                "spec": p["spec"][:90],
                "meta": p["meta"],
            }
            for p in parts_pool.get(key, [])
        ]

    extra_meta = {
        "custom_meta": {
            "usage": floors_info["usage"],
            "usage_label": floors_info["usage_label"],
            "usage_desc": floors_info["usage_desc"],
            "method_title": floors_info["method_title"],
            "method_rules": floors_info["method_rules"],
            "resolution": floors_info["resolution"],
            "target_fps": floors_info["target_fps"],
            "boost": floors_info["boost"],
            "why_notes": floors_info["why_notes"],
            "tiers": [meta_low, meta_mid, meta_high],
            "part_candidates": part_candidates,
            "synced_at": synced_at,
        }
    }
    return result, extra_meta


def get_custom_builder_options() -> dict[str, Any]:
    """프론트엔드 맞춤자동견적 위자드 초기화용 프리셋·게임·작업 프로그램·옵션 목록."""
    return {
        "presets": [
            {
                "id": p["id"],
                "label": p["label"],
                "short": p["short"],
                "desc": p["desc"],
                "need_gpu": p["need_gpu"],
                "default_res": p["default_res"],
                "default_fps": p["default_fps"],
                "default_games": p.get("default_games", []),
                "default_apps": p.get("default_apps", []),
                # 하단 세부 선택에 보일 범위(USAGE_DETAIL)
                "show_games": usage_detail(p["id"])["games"],
                "apps": usage_detail(p["id"])["apps"],
                "method_title": p["method_title"],
                "method_rules": p["method_rules"],
            }
            for p in USAGE_PRESETS
        ],
        "resolutions": list(RESOLUTION_INFO.values()),
        "fps_options": [60, 100, 120, 144, 165, 180, 240],
        "games": [
            {"id": g["id"], "label": g["label"], "fg": g["fg"], "mfg": g["mfg"], "cap": g["cap"]}
            for g in CUSTOM_GAMES
        ],
        "apps": [
            {"id": a["id"], "label": a["label"], "ram": a["ram"], "nvenc": a["nvenc"]}
            for a in CUSTOM_APPS
        ],
    }
