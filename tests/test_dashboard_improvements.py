"""
tests/test_dashboard_improvements.py

web/improvements_service.py (개선 보고서 read-only 조회) 검증.
- 목록/정렬/frontmatter 파싱/kind 분류
- 경로 가드(순회/하위경로/비-md/심볼릭 링크 탈출)
- 상세 조회 + 내용 truncation
- 디렉토리 부재 시 graceful 빈 목록

프로덕션 시크릿/DB/브로커 API 의존 없음.
"""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from web import improvements_service as svc  # noqa: E402


WEEKLY_MD = """---
analysis_period: 2026-06-26 ~ 2026-07-03
mode: weekly
sample_size: 10 (매도 건수)
generated_at: 2026-07-03 18:06 KST
---

# 주간 개선 제안서 (2026-W27)

> 결론 요약: 표본 부족으로 판단 유보.

본문 내용.
"""

MONTHLY_MD = """---
analysis_period: 2026-07
mode: monthly
sample_size: 42
generated_at: 2026-07-03 09:18 KST
---

# 월간 개선 제안서 (2026-07)

월간 요약 문단입니다.
"""

CLOSING_BET_MD = """# 종가베팅 현재 성과 분석 리포트

- 생성 UTC: 2026-07-03
- DB: data/closing_bet.db

종가베팅 성과 요약.
"""

FOCUS_MD = """# 집중 분석 — 트레일링

트레일링 폭 관련 분석.
"""

PLAIN_MD = """이것은 제목 헤딩이 없는 일반 보고서입니다.
"""


@pytest.fixture
def reports_dir(tmp_path, monkeypatch):
    d = tmp_path / "improvements"
    d.mkdir()
    monkeypatch.setattr(svc, "REPORTS_DIR", d)
    return d


def _write(d: Path, name: str, content: str, mtime: float | None = None):
    p = d / name
    p.write_text(content, encoding="utf-8")
    if mtime is not None:
        os.utime(p, (mtime, mtime))
    return p


def test_list_includes_reports_sorted_newest_first(reports_dir):
    _write(reports_dir, "2026-W27-weekly.md", WEEKLY_MD, mtime=1000)
    _write(reports_dir, "2026-07-monthly.md", MONTHLY_MD, mtime=3000)
    _write(reports_dir, "20260703_closing_bet_review.md", CLOSING_BET_MD, mtime=2000)

    reports = svc.list_improvements()
    assert len(reports) == 3
    names = [r["filename"] for r in reports]
    # mtime 3000 > 2000 > 1000
    assert names == [
        "2026-07-monthly.md",
        "20260703_closing_bet_review.md",
        "2026-W27-weekly.md",
    ]
    # 정렬용 내부 키는 응답에서 제거
    assert all("_mtime" not in r for r in reports)


def test_frontmatter_parsed(reports_dir):
    _write(reports_dir, "2026-W27-weekly.md", WEEKLY_MD)
    reports = svc.list_improvements()
    r = reports[0]
    assert r["analysis_period"] == "2026-06-26 ~ 2026-07-03"
    assert r["mode"] == "weekly"
    assert r["sample_size"] == "10 (매도 건수)"
    assert r["generated_at"] == "2026-07-03 18:06 KST"
    assert r["title"] == "주간 개선 제안서 (2026-W27)"
    assert r["summary"]  # 요약 발췌 존재
    assert r["size_bytes"] > 0
    assert r["modified_at"]


def test_kind_classification(reports_dir):
    _write(reports_dir, "2026-W27-weekly.md", WEEKLY_MD)
    _write(reports_dir, "2026-07-monthly.md", MONTHLY_MD)
    _write(reports_dir, "20260703_closing_bet_review.md", CLOSING_BET_MD)
    _write(reports_dir, "2026-06-16-focus-trailing.md", FOCUS_MD)
    _write(reports_dir, "some_random_note.md", PLAIN_MD)

    by_name = {r["filename"]: r["kind"] for r in svc.list_improvements()}
    assert by_name["2026-W27-weekly.md"] == "weekly"
    assert by_name["2026-07-monthly.md"] == "monthly"
    assert by_name["20260703_closing_bet_review.md"] == "closing_bet"
    assert by_name["2026-06-16-focus-trailing.md"] == "focus"
    assert by_name["some_random_note.md"] == "other"


def test_detail_reads_valid_file(reports_dir):
    _write(reports_dir, "2026-07-monthly.md", MONTHLY_MD)
    d = svc.get_improvement("2026-07-monthly.md")
    assert d is not None
    assert d["filename"] == "2026-07-monthly.md"
    assert d["kind"] == "monthly"
    assert "월간 개선 제안서" in d["content"]
    assert d["content_truncated"] is False
    # 상세는 목록 메타 + content 포함
    assert d["analysis_period"] == "2026-07"


def test_traversal_rejected(reports_dir):
    with pytest.raises(ValueError):
        svc.get_improvement("../secret.md")
    with pytest.raises(ValueError):
        svc.get_improvement("../../etc/passwd.md")


def test_subdir_filename_rejected(reports_dir):
    sub = reports_dir / "sub"
    sub.mkdir()
    _write(sub, "nested.md", MONTHLY_MD)
    with pytest.raises(ValueError):
        svc.get_improvement("sub/nested.md")


def test_absolute_path_rejected(reports_dir):
    with pytest.raises(ValueError):
        svc.get_improvement("/etc/passwd.md")


def test_backslash_rejected(reports_dir):
    with pytest.raises(ValueError):
        svc.get_improvement("..\\secret.md")


def test_non_md_rejected(reports_dir):
    _write(reports_dir, "notes.txt", "secret")
    with pytest.raises(ValueError):
        svc.get_improvement("notes.txt")
    with pytest.raises(ValueError):
        svc.get_improvement("config.py")


def test_missing_file_returns_none(reports_dir):
    assert svc.get_improvement("does_not_exist.md") is None


def test_non_md_not_listed(reports_dir):
    _write(reports_dir, "2026-07-monthly.md", MONTHLY_MD)
    _write(reports_dir, "notes.txt", "secret")
    names = [r["filename"] for r in svc.list_improvements()]
    assert "notes.txt" not in names
    assert "2026-07-monthly.md" in names


@pytest.mark.skipif(
    not hasattr(os, "symlink"), reason="symlink 미지원 플랫폼"
)
def test_symlink_escaping_root_rejected(reports_dir, tmp_path):
    outside = tmp_path / "outside_secret.md"
    outside.write_text("TOP SECRET", encoding="utf-8")
    link = reports_dir / "link.md"
    try:
        os.symlink(outside, link)
    except (OSError, NotImplementedError):
        pytest.skip("심볼릭 링크 생성 불가")
    # 루트 밖으로 resolve 되므로 거부(None)
    assert svc.get_improvement("link.md") is None
    # 목록 조회에서도 심볼릭 링크 탈출 파일은 노출되지 않음
    _write(reports_dir, "real.md", MONTHLY_MD)
    names = [r["filename"] for r in svc.list_improvements()]
    assert "link.md" not in names
    assert "real.md" in names


def test_missing_directory_returns_empty(tmp_path, monkeypatch):
    ghost = tmp_path / "does_not_exist"
    monkeypatch.setattr(svc, "REPORTS_DIR", ghost)
    # 500 없이 빈 목록
    assert svc.list_improvements() == []
    assert svc.get_improvement("whatever.md") is None


def test_content_truncation(reports_dir, monkeypatch):
    monkeypatch.setattr(svc, "MAX_CONTENT_CHARS", 100)
    big = "# 큰 보고서\n\n" + ("가" * 500)
    _write(reports_dir, "big_report.md", big)
    d = svc.get_improvement("big_report.md")
    assert d is not None
    assert d["content_truncated"] is True
    assert len(d["content"]) == 100


def test_plain_file_title_falls_back_to_filename(reports_dir):
    _write(reports_dir, "plain_note.md", PLAIN_MD)
    d = svc.get_improvement("plain_note.md")
    assert d is not None
    # heading 없으면 확장자 제거된 파일명
    assert d["title"] == "plain_note"



def test_api_list_and_detail_routes(reports_dir):
    """FastAPI route shape: list/detail return stable JSON behind auth."""
    _write(reports_dir, "2026-W27-weekly.md", WEEKLY_MD)

    from fastapi.testclient import TestClient
    from web.app import create_app
    from web.auth import COOKIE_NAME, create_token

    client = TestClient(create_app())
    client.cookies.set(COOKIE_NAME, create_token())

    list_resp = client.get("/api/v1/improvements")
    assert list_resp.status_code == 200
    payload = list_resp.json()
    assert payload["count"] == 1
    assert payload["reports"][0]["filename"] == "2026-W27-weekly.md"

    detail_resp = client.get("/api/v1/improvements/2026-W27-weekly.md")
    assert detail_resp.status_code == 200
    detail = detail_resp.json()
    assert detail["filename"] == "2026-W27-weekly.md"
    assert "content" in detail
    assert detail["content_truncated"] is False


def test_api_invalid_and_missing_routes(reports_dir):
    """Invalid filename -> 400/404, valid-but-missing markdown -> 404, not 500."""
    from fastapi.testclient import TestClient
    from web.app import create_app
    from web.auth import COOKIE_NAME, create_token

    client = TestClient(create_app())
    client.cookies.set(COOKIE_NAME, create_token())

    bad = client.get("/api/v1/improvements/..%2Fsecret.md")
    assert bad.status_code in (400, 404)  # router may reject encoded slash before handler

    missing = client.get("/api/v1/improvements/missing.md")
    assert missing.status_code == 404
