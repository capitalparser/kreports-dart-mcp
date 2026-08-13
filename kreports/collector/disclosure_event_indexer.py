"""Index investor/auditor-relevant event disclosures from DART titles."""
from __future__ import annotations

from datetime import datetime, time

from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from kreports.db.engine import get_session, init_db
from kreports.db.models import Disclosure, DisclosureEvent


EVENT_RULES: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("fraud", ("횡령", "배임"), "high"),
    ("litigation", ("소송", "중재", "분쟁"), "warning"),
    ("control_change", ("최대주주 변경", "경영권 변경", "주식등의대량보유상황보고서"), "warning"),
    ("capital_raise", ("유상증자", "전환사채", "신주인수권", "교환사채"), "monitor"),
    ("major_contract", ("단일판매", "공급계약", "판매ㆍ공급계약"), "monitor"),
    ("asset_deal", ("유형자산 양수", "유형자산 양도", "타법인 주식", "주식 취득", "영업양수", "영업양도"), "monitor"),
    ("audit_related", ("감사보고서", "감사의견", "내부회계", "감사인"), "monitor"),
)


def classify_disclosure_event(report_nm: str) -> dict | None:
    title = report_nm or ""
    for event_type, keywords, severity in EVENT_RULES:
        if any(keyword in title for keyword in keywords):
            return {"event_type": event_type, "severity_hint": severity}
    return None


def rebuild_disclosure_events(
    *,
    year: int | None = None,
    market: str | None = None,
    limit: int | None = None,
) -> dict:
    """Rebuild disclosure event rows from cached disclosure titles."""
    init_db()
    with get_session() as session:
        query = session.query(Disclosure)
        if year is not None:
            query = query.filter(Disclosure.disc_date >= f"{int(year)}-01-01", Disclosure.disc_date <= f"{int(year)}-12-31")
        if market:
            from kreports.db.models import Company

            query = query.join(Company, Company.corp_code == Disclosure.corp_code).filter(Company.market == market)
        query = query.order_by(Disclosure.disc_date.desc(), Disclosure.rcept_no.desc())
        if limit:
            query = query.limit(int(limit))
        rows = query.all()
        indexed = 0
        for row in rows:
            classified = classify_disclosure_event(row.report_nm)
            if not classified:
                continue
            event_date = datetime.combine(row.disc_date, time.min)
            values = {
                "rcept_no": row.rcept_no,
                "corp_code": row.corp_code,
                "event_date": event_date,
                "event_type": classified["event_type"],
                "event_title": row.report_nm,
                "severity_hint": classified["severity_hint"],
                "source_report_nm": row.report_nm,
                "fetched_at": datetime.utcnow(),
            }
            stmt = sqlite_insert(DisclosureEvent).values(values)
            stmt = stmt.on_conflict_do_update(
                index_elements=["rcept_no", "event_type"],
                set_={
                    "event_date": stmt.excluded.event_date,
                    "event_title": stmt.excluded.event_title,
                    "severity_hint": stmt.excluded.severity_hint,
                    "source_report_nm": stmt.excluded.source_report_nm,
                    "fetched_at": stmt.excluded.fetched_at,
                },
            )
            session.execute(stmt)
            indexed += 1
    return {"scanned": len(rows), "indexed": indexed, "year": year, "market": market}
