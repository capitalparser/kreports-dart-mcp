"""
감사인 판단 플래그 계산.
auditors 테이블의 is_auditor_changed / consecutive_years 를 갱신한다.
"""
import logging
from kreports.db.engine import get_session
from kreports.db.models import Auditor

logger = logging.getLogger(__name__)


def compute_auditor_flags(corp_code: str) -> int:
    """
    특정 기업의 감사인 이력 전체를 연도 순으로 정렬하여
    is_auditor_changed / consecutive_years 를 계산·갱신한다.

    Returns:
        갱신된 레코드 수
    """
    with get_session() as session:
        rows = (
            session.query(Auditor)
            .filter_by(corp_code=corp_code)
            .order_by(Auditor.fs_div, Auditor.bsns_year)
            .all()
        )
        if not rows:
            return 0

        # fs_div 별로 분리하여 처리
        by_div: dict[str, list[Auditor]] = {}
        for r in rows:
            by_div.setdefault(r.fs_div, []).append(r)

        updated = 0
        for div_rows in by_div.values():
            div_rows.sort(key=lambda r: r.bsns_year)
            for i, row in enumerate(div_rows):
                prev = div_rows[i - 1] if i > 0 else None
                if prev is None:
                    row.is_auditor_changed = None
                    row.consecutive_years = 1
                else:
                    changed = prev.auditor_nm != row.auditor_nm
                    row.is_auditor_changed = changed
                    row.consecutive_years = 1 if changed else (prev.consecutive_years or 1) + 1
                updated += 1

    return updated


def compute_all_auditor_flags() -> int:
    """전체 기업의 감사인 플래그를 일괄 계산한다."""
    with get_session() as session:
        corp_codes = [
            r[0] for r in
            session.query(Auditor.corp_code).distinct().all()
        ]

    total = 0
    for corp_code in corp_codes:
        total += compute_auditor_flags(corp_code)
    return total
