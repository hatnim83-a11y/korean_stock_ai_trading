"""
web/improvements_service.py - 개선 보고서(read-only) 조회 서비스

docs/improvements/ 아래의 마크다운 개선 제안서를 대시보드에서 읽기 전용으로
노출하기 위한 헬퍼 모음. 트레이딩/주문 로직과 무관하며 파일 시스템 읽기만 수행한다.

보안(경로 가드):
- docs/improvements 바로 아래의 `.md` 파일만 허용
- 경로 순회('..'), 슬래시/백슬래시, 절대경로, 하위 디렉토리, 심볼릭 링크 탈출 거부
- 각 파일은 읽기 직전 재-resolve 하여 루트 내부에 있는지 재확인
"""

import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import KST as _KST  # noqa: E402  (서버 UTC → KST 표시 통일)

# 루트 = 저장소 루트의 docs/improvements
# (테스트에서 monkeypatch 로 교체 가능하도록 모듈 전역으로 노출)
REPORTS_DIR: Path = Path(__file__).parent.parent / "docs" / "improvements"

# 내용 최대 길이 (약 80k chars)
MAX_CONTENT_CHARS = 80_000

# 요약 발췌 최대 길이
_SUMMARY_MAX_CHARS = 240


def _reports_dir() -> Path:
    """현재 보고서 디렉토리(테스트 monkeypatch 반영)."""
    return Path(REPORTS_DIR)


def _classify_kind(filename: str, mode: str = "") -> str:
    """파일명/mode 기반 보고서 종류 분류."""
    name = (filename or "").lower()
    if "closing_bet" in name or "closing-bet" in name:
        return "closing_bet"
    if "focus" in name:
        return "focus"
    if "weekly" in name or re.search(r"-w\d", name):
        return "weekly"
    if "monthly" in name:
        return "monthly"
    m = (mode or "").strip().lower()
    if m in ("weekly", "monthly", "focus", "closing_bet"):
        return m
    return "other"


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """선두 `---` YAML-lite frontmatter 파싱 → (meta dict, 본문 나머지)."""
    meta: dict = {}
    body = text
    if text.startswith("---"):
        # 첫 줄이 정확히 '---' 인 경우에만 frontmatter 로 취급
        lines = text.splitlines()
        if lines and lines[0].strip() == "---":
            end_idx = None
            for i in range(1, len(lines)):
                if lines[i].strip() == "---":
                    end_idx = i
                    break
            if end_idx is not None:
                for raw in lines[1:end_idx]:
                    if ":" not in raw:
                        continue
                    key, _, val = raw.partition(":")
                    key = key.strip().lower()
                    if key:
                        meta[key] = val.strip()
                body = "\n".join(lines[end_idx + 1:])
    return meta, body


def _extract_title(body: str, filename: str) -> str:
    """본문 첫 마크다운 heading 을 title 로. 없으면 파일명(확장자 제거)."""
    for line in body.splitlines():
        s = line.strip()
        if s.startswith("#"):
            return s.lstrip("#").strip() or filename[:-3]
    return filename[:-3] if filename.endswith(".md") else filename


def _extract_summary(body: str) -> str:
    """제목/빈줄을 건너뛴 첫 의미 있는 문단을 요약 발췌로."""
    for line in body.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("#"):
            continue
        # 인용/불릿 마커 제거
        s = re.sub(r"^[>\-\*\s]+", "", s).strip()
        if not s:
            continue
        if len(s) > _SUMMARY_MAX_CHARS:
            s = s[:_SUMMARY_MAX_CHARS].rstrip() + "…"
        return s
    return ""


def _validate_filename(filename: str) -> bool:
    """파일명 자체(경로 미포함) 유효성 검사."""
    if not filename or not isinstance(filename, str):
        return False
    if filename != filename.strip():
        return False
    if "\x00" in filename:
        return False
    if "/" in filename or "\\" in filename:
        return False
    # 경로 컴포넌트가 정확히 파일명과 일치해야 함 (순회/하위경로 차단)
    if os.path.basename(filename) != filename:
        return False
    if filename in (".", ".."):
        return False
    if not filename.endswith(".md"):
        return False
    return True


def _resolve_report(filename: str) -> Optional[Path]:
    """유효한 경우 루트 내부의 실제 파일 Path 를 반환, 아니면 None.

    심볼릭 링크 탈출 방어를 위해 읽기 직전 재-resolve 하여
    resolved 부모가 resolved 루트와 정확히 일치하는지 확인한다.
    """
    if not _validate_filename(filename):
        return None
    root = _reports_dir()
    try:
        root_resolved = root.resolve()
        candidate = (root_resolved / filename).resolve()
    except (OSError, RuntimeError, ValueError):
        return None
    # 심볼릭 링크로 루트를 벗어난 경우 부모가 달라짐 → 거부
    if candidate.parent != root_resolved:
        return None
    if not candidate.is_file():
        return None
    return candidate


def _build_meta(path: Path) -> dict:
    """단일 파일의 리스트용 메타데이터 dict 생성 (본문 미포함)."""
    filename = path.name
    try:
        stat = path.stat()
        size_bytes = stat.st_size
        modified_at = datetime.fromtimestamp(stat.st_mtime, _KST).isoformat()
        mtime = stat.st_mtime
    except OSError:
        size_bytes = 0
        modified_at = ""
        mtime = 0.0

    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""

    fm, body = _parse_frontmatter(text)
    kind = _classify_kind(filename, fm.get("mode", ""))
    return {
        "filename": filename,
        "title": _extract_title(body, filename),
        "kind": kind,
        "size_bytes": size_bytes,
        "modified_at": modified_at,
        "_mtime": mtime,  # 정렬용 (응답에서 제거)
        "analysis_period": fm.get("analysis_period", ""),
        "mode": fm.get("mode", ""),
        "sample_size": fm.get("sample_size", ""),
        "generated_at": fm.get("generated_at", ""),
        "summary": _extract_summary(body),
    }


def list_improvements() -> list[dict]:
    """개선 보고서 목록(최신 수정순). 디렉토리 부재 시 빈 리스트."""
    root = _reports_dir()
    try:
        if not root.is_dir():
            return []
        entries = list(root.iterdir())
    except OSError:
        return []

    reports = []
    for entry in entries:
        try:
            if not entry.name.endswith(".md"):
                continue
            # 상세 조회와 동일한 경로 가드 재사용 → 심볼릭 링크 루트 탈출 배제
            safe = _resolve_report(entry.name)
            if safe is None:
                continue
            reports.append(_build_meta(safe))
        except OSError:
            continue

    reports.sort(key=lambda r: r.get("_mtime", 0.0), reverse=True)
    for r in reports:
        r.pop("_mtime", None)
    return reports


def get_improvement(filename: str) -> Optional[dict]:
    """단일 보고서 상세(메타 + 본문). 유효하지 않으면 ValueError, 없으면 None."""
    if not _validate_filename(filename):
        raise ValueError("invalid filename")
    path = _resolve_report(filename)
    if path is None:
        return None

    meta = _build_meta(path)
    meta.pop("_mtime", None)
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    truncated = len(text) > MAX_CONTENT_CHARS
    if truncated:
        text = text[:MAX_CONTENT_CHARS]
    meta["content"] = text
    meta["content_truncated"] = truncated
    return meta
