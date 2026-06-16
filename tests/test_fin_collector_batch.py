from datetime import datetime

from kreports.db.models import Company, Financial, FinancialFact


def test_dart_limit_response_detection():
    from kreports.collector.fin_collector import _is_dart_auth_response, _is_dart_limit_response

    assert _is_dart_limit_response({"status": "020", "message": "사용한도를 초과하였습니다."})
    assert _is_dart_limit_response({"status": "020", "message": "daily limit exceeded"})
    assert not _is_dart_limit_response({"status": "013", "message": "조회된 데이터가 없습니다."})
    assert not _is_dart_limit_response({"status": "000", "message": "정상"})

    assert _is_dart_auth_response({"status": "010", "message": "등록되지 않은 인증키입니다."})
    assert _is_dart_auth_response({"status": "010", "message": "invalid api key"})
    assert not _is_dart_auth_response({"status": "020", "message": "사용한도를 초과하였습니다."})


def test_collect_all_companies_defaults_to_listed_markets(temp_engine, monkeypatch):
    from kreports.collector import fin_collector
    from kreports.db.engine import get_session

    with get_session() as session:
        session.add_all(
            [
                Company(corp_code="00000001", stock_code="000001", corp_name="코스피", market="KOSPI"),
                Company(corp_code="00000002", stock_code="000002", corp_name="코스닥", market="KOSDAQ"),
                Company(corp_code="00000003", stock_code="000003", corp_name="코넥스", market="KONEX"),
                Company(corp_code="00000004", stock_code="000004", corp_name="과거법인", market=None),
            ]
        )

    called = []

    def fake_collect(stock_code, year_from, year_to, *, force=False):
        assert force is False
        called.append(stock_code)
        return {"success": 1, "no_data": 0, "error": 0, "skipped": 0}

    monkeypatch.setattr(fin_collector, "collect_financial_range", fake_collect)

    result = fin_collector.collect_all_companies(2023, 2023)

    assert result == {"success": 3, "no_data": 0, "error": 0, "skipped": 0}
    assert called == ["000001", "000002", "000003"]


def test_collect_all_companies_filters_market(temp_engine, monkeypatch):
    from kreports.collector import fin_collector
    from kreports.db.engine import get_session

    with get_session() as session:
        session.add_all(
            [
                Company(corp_code="00000001", stock_code="000001", corp_name="코스피", market="KOSPI"),
                Company(corp_code="00000002", stock_code="000002", corp_name="코스닥", market="KOSDAQ"),
            ]
        )

    called = []

    def fake_collect(stock_code, year_from, year_to, *, force=False):
        assert force is False
        called.append(stock_code)
        return {"success": 1, "no_data": 0, "error": 0, "skipped": 0}

    monkeypatch.setattr(fin_collector, "collect_financial_range", fake_collect)

    result = fin_collector.collect_all_companies(2023, 2023, market="kosdaq")

    assert result == {"success": 1, "no_data": 0, "error": 0, "skipped": 0}
    assert called == ["000002"]


def test_collect_all_companies_stops_on_dart_limit(temp_engine, monkeypatch):
    import pytest
    from kreports.collector import fin_collector
    from kreports.db.engine import get_session

    with get_session() as session:
        session.add_all(
            [
                Company(corp_code="00000001", stock_code="000001", corp_name="첫회사", market="KOSDAQ"),
                Company(corp_code="00000002", stock_code="000002", corp_name="둘째회사", market="KOSDAQ"),
            ]
        )

    called = []

    def fake_collect(stock_code, year_from, year_to, *, force=False):
        assert force is False
        called.append(stock_code)
        raise fin_collector.DartApiLimitExceeded("사용한도를 초과하였습니다.")

    monkeypatch.setattr(fin_collector, "collect_financial_range", fake_collect)

    with pytest.raises(fin_collector.DartApiLimitExceeded):
        fin_collector.collect_all_companies(2023, 2023, market="KOSDAQ")

    assert called == ["000002"]


def test_collect_all_companies_stops_on_dart_auth_error(temp_engine, monkeypatch):
    import pytest
    from kreports.collector import fin_collector
    from kreports.db.engine import get_session

    with get_session() as session:
        session.add_all(
            [
                Company(corp_code="00000001", stock_code="000001", corp_name="첫회사", market="KOSDAQ"),
                Company(corp_code="00000002", stock_code="000002", corp_name="둘째회사", market="KOSDAQ"),
            ]
        )

    called = []

    def fake_collect(stock_code, year_from, year_to, *, force=False):
        assert force is False
        called.append(stock_code)
        raise fin_collector.DartApiAuthError("등록되지 않은 인증키입니다.")

    monkeypatch.setattr(fin_collector, "collect_financial_range", fake_collect)

    with pytest.raises(fin_collector.DartApiAuthError):
        fin_collector.collect_all_companies(2023, 2023, market="KOSDAQ")

    assert called == ["000002"]


def test_collect_financial_range_skips_existing_full_fact_quarters(temp_engine, monkeypatch):
    from kreports.collector import fin_collector
    from kreports.db.engine import get_session

    with get_session() as session:
        session.add(
            Company(corp_code="00000001", stock_code="000001", corp_name="기존회사", market="KOSDAQ")
        )
        session.add(
            FinancialFact(
                corp_code="00000001",
                bsns_year=2024,
                reprt_code="11011",
                fs_div="CFS",
                sj_div="BS",
                account_id="ifrs-full_Assets",
                account_nm="자산총계",
                thstrm_amount=1,
                fetched_at=datetime.utcnow(),
            )
        )

    called = []

    def fake_collect(stock_code, year, quarter):
        called.append((stock_code, year, quarter))
        return "success"

    monkeypatch.setattr(fin_collector, "collect_financial", fake_collect)

    result = fin_collector.collect_financial_range("000001", 2024, 2024)

    assert result == {"success": 3, "no_data": 0, "error": 0, "skipped": 1}
    assert called == [
        ("000001", 2024, 1),
        ("000001", 2024, 2),
        ("000001", 2024, 3),
    ]


def test_collect_financial_range_skips_existing_summary_fallback_quarters(temp_engine, monkeypatch):
    from kreports.collector import fin_collector
    from kreports.db.engine import get_session

    with get_session() as session:
        session.add(
            Company(corp_code="00000001", stock_code="000001", corp_name="기존회사", market="KOSDAQ")
        )
        session.add(
            Financial(
                corp_code="00000001",
                year=2024,
                quarter=4,
                fs_div="OFS",
                revenue=1,
                source="acnt",
                fetched_at=datetime.utcnow(),
            )
        )

    called = []

    def fake_collect(stock_code, year, quarter):
        called.append((stock_code, year, quarter))
        return "success"

    monkeypatch.setattr(fin_collector, "collect_financial", fake_collect)

    result = fin_collector.collect_financial_range("000001", 2024, 2024)

    assert result == {"success": 3, "no_data": 0, "error": 0, "skipped": 1}
    assert called == [
        ("000001", 2024, 1),
        ("000001", 2024, 2),
        ("000001", 2024, 3),
    ]


def test_collect_financial_range_skips_prior_no_data_quarters(temp_engine, monkeypatch):
    from kreports.collector import fin_collector
    from kreports.db.engine import get_session
    from kreports.db.models import FetchLog

    with get_session() as session:
        session.add(
            Company(corp_code="00000001", stock_code="000001", corp_name="기존회사", market="KOSDAQ")
        )
        session.add(
            FetchLog(
                task_type="financial",
                corp_code="00000001",
                year=2024,
                quarter=4,
                status="no_data",
                fetched_at=datetime.utcnow(),
            )
        )

    called = []

    def fake_collect(stock_code, year, quarter):
        called.append((stock_code, year, quarter))
        return "success"

    monkeypatch.setattr(fin_collector, "collect_financial", fake_collect)

    result = fin_collector.collect_financial_range("000001", 2024, 2024)

    assert result == {"success": 3, "no_data": 0, "error": 0, "skipped": 1}
    assert called == [
        ("000001", 2024, 1),
        ("000001", 2024, 2),
        ("000001", 2024, 3),
    ]


def test_collect_financial_range_force_refetches_existing_quarters(temp_engine, monkeypatch):
    from kreports.collector import fin_collector
    from kreports.db.engine import get_session

    with get_session() as session:
        session.add(
            Company(corp_code="00000001", stock_code="000001", corp_name="기존회사", market="KOSDAQ")
        )
        session.add(
            FinancialFact(
                corp_code="00000001",
                bsns_year=2024,
                reprt_code="11011",
                fs_div="CFS",
                sj_div="BS",
                account_id="ifrs-full_Assets",
                account_nm="자산총계",
                thstrm_amount=1,
                fetched_at=datetime.utcnow(),
            )
        )

    called = []

    def fake_collect(stock_code, year, quarter):
        called.append((stock_code, year, quarter))
        return "success"

    monkeypatch.setattr(fin_collector, "collect_financial", fake_collect)

    result = fin_collector.collect_financial_range("000001", 2024, 2024, force=True)

    assert result == {"success": 4, "no_data": 0, "error": 0, "skipped": 0}
    assert called == [
        ("000001", 2024, 1),
        ("000001", 2024, 2),
        ("000001", 2024, 3),
        ("000001", 2024, 4),
    ]
