"""
Peer group 해석 공통 모듈.

감사 관점에서 동종업종 비교를 일관되게 수행하기 위한 함수들을 모은다.

핵심 규칙:
- Adaptive ladder: KSIC 3자리 → n<5면 2자리로 fallback
- Sector mutual exclusion: 금융(64~66) / 지주(6420) / 부동산(68) / 일반
- Size bucket opt-in: 자산총계 log10 기준 ±decade
"""
from __future__ import annotations

import math
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

from sqlalchemy import create_engine, inspect, text

from kreports.db.engine import engine
from kreports.semantic.metrics import METRICS, metric_definition


_PEER_PROFILES = frozenset({
    "investor",
    "audit_fee",
    "audit_risk",
    "accounting_policy",
    "kam_procedure",
})
_MAX_RETURNED_EXCLUSIONS = 50
_SIZE_OUTLIER_DECADES = 2.0


def _require_tuple(name: str, value: object) -> tuple:
    if not isinstance(value, tuple):
        raise TypeError(f"{name} must be a tuple")
    return value


def _validate_pairs(
    name: str,
    value: object,
    *,
    numeric_values: bool = False,
) -> None:
    pairs = _require_tuple(name, value)
    keys: set[str] = set()
    for pair in pairs:
        if not isinstance(pair, tuple) or len(pair) != 2:
            raise TypeError(f"{name} entries must be two-item tuples")
        key, item_value = pair
        if not isinstance(key, str) or not key:
            raise TypeError(f"{name} keys must be non-empty strings")
        if key in keys:
            raise ValueError(f"{name} keys must be unique")
        keys.add(key)
        if numeric_values and item_value is not None:
            if (
                not isinstance(item_value, (int, float))
                or isinstance(item_value, bool)
                or not math.isfinite(float(item_value))
            ):
                raise TypeError(f"{name} values must be finite numbers or None")


def _validate_string_tuple(name: str, value: object) -> None:
    items = _require_tuple(name, value)
    if any(not isinstance(item, str) or not item for item in items):
        raise TypeError(f"{name} entries must be non-empty strings")


class PeerDatabaseUnavailable(RuntimeError):
    """The runtime database cannot be inspected without risking a write."""


@dataclass(frozen=True)
class PeerMember:
    corp_code: str
    corp_name: str
    induty_code: str
    fs_div: str
    score: float
    reason_codes: tuple[str, ...]
    score_components: tuple[tuple[str, float | None], ...]
    metric_values: tuple[tuple[str, float | None], ...]
    metric_bases: tuple[tuple[str, str], ...] = ()
    limitations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name, value in (
            ("corp_code", self.corp_code),
            ("corp_name", self.corp_name),
            ("induty_code", self.induty_code),
        ):
            if not isinstance(value, str) or not value:
                raise TypeError(f"{name} must be a non-empty string")
        if self.fs_div not in {"CFS", "OFS"}:
            raise ValueError("fs_div must be CFS or OFS")
        if (
            not isinstance(self.score, (int, float))
            or isinstance(self.score, bool)
            or not math.isfinite(float(self.score))
        ):
            raise TypeError("score must be a finite number")
        _validate_string_tuple("reason_codes", self.reason_codes)
        _validate_pairs(
            "score_components",
            self.score_components,
            numeric_values=True,
        )
        _validate_pairs("metric_values", self.metric_values, numeric_values=True)
        _validate_pairs("metric_bases", self.metric_bases)
        if any(
            not isinstance(value, str) or not value
            for _key, value in self.metric_bases
        ):
            raise TypeError("metric_bases values must be non-empty strings")
        _validate_string_tuple("limitations", self.limitations)


@dataclass(frozen=True)
class PeerExclusion:
    corp_code: str
    corp_name: str
    reason_code: str
    secondary_reason_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name, value in (
            ("corp_code", self.corp_code),
            ("corp_name", self.corp_name),
            ("reason_code", self.reason_code),
        ):
            if not isinstance(value, str) or not value:
                raise TypeError(f"{name} must be a non-empty string")
        _validate_string_tuple(
            "secondary_reason_codes",
            self.secondary_reason_codes,
        )


@dataclass(frozen=True)
class PeerCohort:
    subject_corp_code: str
    subject_name: str
    requested_year: int
    profile: str
    fs_div: str | None
    members: tuple[PeerMember, ...]
    exclusions: tuple[PeerExclusion, ...]
    exclusion_counts: tuple[tuple[str, int], ...]
    total_candidates: int
    eligible_count: int
    subject_metrics: tuple[tuple[str, float | None], ...] = ()
    subject_metric_bases: tuple[tuple[str, str], ...] = ()
    score_policy: tuple[tuple[str, str], ...] = ()
    limitations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.subject_corp_code, str) or not self.subject_corp_code:
            raise TypeError("subject_corp_code must be a non-empty string")
        if not isinstance(self.subject_name, str) or not self.subject_name:
            raise TypeError("subject_name must be a non-empty string")
        if not isinstance(self.requested_year, int) or isinstance(
            self.requested_year, bool
        ):
            raise TypeError("requested_year must be an integer")
        if self.profile not in _PEER_PROFILES:
            raise ValueError(f"unsupported peer profile: {self.profile}")
        if not 1900 <= self.requested_year <= 9999:
            raise ValueError("requested_year must be a four-digit year")
        if self.fs_div not in {None, "CFS", "OFS"}:
            raise ValueError("fs_div must be CFS, OFS, or None")
        members = _require_tuple("members", self.members)
        exclusions = _require_tuple("exclusions", self.exclusions)
        if any(not isinstance(member, PeerMember) for member in members):
            raise TypeError("members entries must be PeerMember")
        if any(
            not isinstance(exclusion, PeerExclusion)
            for exclusion in exclusions
        ):
            raise TypeError("exclusions entries must be PeerExclusion")
        _validate_pairs("exclusion_counts", self.exclusion_counts)
        if any(
            not isinstance(count, int)
            or isinstance(count, bool)
            or count < 0
            for _reason, count in self.exclusion_counts
        ):
            raise TypeError("exclusion_counts values must be non-negative integers")
        for name, value in (
            ("total_candidates", self.total_candidates),
            ("eligible_count", self.eligible_count),
        ):
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or value < 0
            ):
                raise TypeError(f"{name} must be a non-negative integer")
        if self.eligible_count > self.total_candidates:
            raise ValueError("eligible_count cannot exceed total_candidates")
        if len(self.members) > self.eligible_count:
            raise ValueError("members cannot exceed eligible_count")
        if len(self.exclusions) > _MAX_RETURNED_EXCLUSIONS:
            raise ValueError(
                f"exclusions cannot exceed {_MAX_RETURNED_EXCLUSIONS}"
            )
        _validate_pairs(
            "subject_metrics",
            self.subject_metrics,
            numeric_values=True,
        )
        _validate_pairs("subject_metric_bases", self.subject_metric_bases)
        _validate_pairs("score_policy", self.score_policy)
        _validate_string_tuple("limitations", self.limitations)

    @property
    def denominator_metadata(
        self,
    ) -> tuple[tuple[str, int | bool], ...]:
        counts = dict(self.exclusion_counts)
        subject_excluded = counts.get("subject", 0)
        outside_limit = counts.get("outside_limit", 0)
        return (
            ("company_universe", self.total_candidates),
            ("subject_excluded", subject_excluded),
            (
                "candidate_peers",
                max(0, self.total_candidates - subject_excluded),
            ),
            ("common_eligible", self.eligible_count),
            ("selected", len(self.members)),
            ("outside_limit", outside_limit),
            ("outside_limit_is_presentation_exclusion", True),
        )


@dataclass(frozen=True)
class PeerMetricComparison:
    metric_key: str
    subject_value: float | None
    peer_values: tuple[float, ...]
    n: int
    unavailable_count: int
    percentile: float | None
    decile: int | None
    unit: str
    basis: str | None
    confidence: str
    limitations: tuple[str, ...] = ()
    ties: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.metric_key, str) or not self.metric_key:
            raise TypeError("metric_key must be a non-empty string")
        _require_tuple("peer_values", self.peer_values)
        if any(
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(float(value))
            for value in self.peer_values
        ):
            raise TypeError("peer_values entries must be finite numbers")
        for name, value in (
            ("n", self.n),
            ("unavailable_count", self.unavailable_count),
            ("ties", self.ties),
        ):
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or value < 0
            ):
                raise TypeError(f"{name} must be a non-negative integer")
        if self.n != len(self.peer_values):
            raise ValueError("n must equal the number of peer_values")
        if tuple(sorted(self.peer_values)) != self.peer_values:
            raise ValueError("peer_values must be sorted")
        if self.subject_value is not None and (
            not isinstance(self.subject_value, (int, float))
            or isinstance(self.subject_value, bool)
            or not math.isfinite(float(self.subject_value))
        ):
            raise TypeError("subject_value must be a finite number or None")
        if self.percentile is not None and not 0 <= self.percentile <= 100:
            raise ValueError("percentile must be between 0 and 100")
        if self.decile is not None and not 1 <= self.decile <= 10:
            raise ValueError("decile must be between 1 and 10")
        if not isinstance(self.unit, str) or not self.unit:
            raise TypeError("unit must be a non-empty string")
        if self.basis is not None and (
            not isinstance(self.basis, str) or not self.basis
        ):
            raise TypeError("basis must be a non-empty string or None")
        if not isinstance(self.confidence, str) or not self.confidence:
            raise TypeError("confidence must be a non-empty string")
        _validate_string_tuple("limitations", self.limitations)

    @property
    def distribution_values(self) -> tuple[float, ...]:
        """Stable sorted peer values used for the reported rank."""
        return self.peer_values


class SectorGroup(str, Enum):
    FINANCIAL = "financial"      # KSIC 64, 65, 66
    HOLDING = "holding"          # KSIC 64201(일반지주회사). 64202+ 미정의 코드는 FINANCIAL로 처리.
    REAL_ESTATE = "real_estate"  # KSIC 68
    GENERAL = "general"          # 그 외 제조·서비스·도소매 등
    UNKNOWN = "unknown"          # induty_code 없음/이상


def classify_sector(induty_code: Optional[str]) -> SectorGroup:
    """KSIC induty_code를 감사 비교 단위의 sector group으로 분류.

    분류 규칙:
    - 지주회사 우선 매칭: KSIC `64201` (일반지주회사) 또는 4자리 입력 `6420`
    - 금융: 2자리가 64, 65, 66
    - 부동산: 2자리가 68
    - 그 외: GENERAL
    - induty_code 없음/비숫자: UNKNOWN
    """
    if not induty_code or not isinstance(induty_code, str):
        return SectorGroup.UNKNOWN
    code = induty_code.strip()
    if not code or not code[:2].isdigit():
        return SectorGroup.UNKNOWN
    # 지주 명시 매칭: KSIC 64201(일반지주회사)만 HOLDING.
    # "6420" 4자리는 카테고리 명목값으로 HOLDING. 64202+ 미정의 642xx는 FINANCIAL.
    if code == "6420" or code.startswith("64201"):
        return SectorGroup.HOLDING
    p2 = code[:2]
    if p2 in {"64", "65", "66"}:
        return SectorGroup.FINANCIAL
    if p2 == "68":
        return SectorGroup.REAL_ESTATE
    return SectorGroup.GENERAL


# 임계값 — 통계 신뢰도
_N_HIGH = 20
_N_MEDIUM = 10
_N_LOW = 5


def resolve_fs_div_for_company(
    corp_code: str,
    year: int | None,
    fs_strategy: str = "auto",
    *,
    read_engine=None,
) -> str:
    strategy = (fs_strategy or "auto").upper()
    if strategy in {"CFS", "OFS"}:
        return strategy
    if strategy != "AUTO":
        return "CFS"
    active_engine = read_engine or engine
    with active_engine.connect() as conn:
        if year is None:
            row = conn.execute(
                text("SELECT MAX(year) FROM financials WHERE corp_code=:cc AND quarter=4"),
                {"cc": corp_code},
            ).first()
            year = row[0] if row and row[0] else None
        if year is None:
            return "CFS"
        cfs = conn.execute(
            text("SELECT 1 FROM financials WHERE corp_code=:cc AND year=:y AND quarter=4 AND fs_div='CFS' LIMIT 1"),
            {"cc": corp_code, "y": year},
        ).first()
        if cfs:
            return "CFS"
        ofs = conn.execute(
            text("SELECT 1 FROM financials WHERE corp_code=:cc AND year=:y AND quarter=4 AND fs_div='OFS' LIMIT 1"),
            {"cc": corp_code, "y": year},
        ).first()
        return "OFS" if ofs else "CFS"


def confidence_band(n: int) -> str:
    """peer 수에 따른 통계 신뢰도 라벨."""
    if n >= _N_HIGH:
        return "high"
    if n >= _N_MEDIUM:
        return "medium"
    if n >= _N_LOW:
        return "low"
    return "insufficient"


@dataclass(frozen=True)
class PeerResolution:
    """resolve_peers의 응답 컨테이너."""
    peer_corp_codes: list[str]
    matched_prefix_len: int
    sector_group: SectorGroup
    n_peers: int
    excluded_categories: list[str] = field(
        default_factory=lambda: ["financial", "holding", "real_estate"]
    )
    size_bucket_applied: Optional[float] = None
    resolved_year: Optional[int] = None  # peer 풀이 산정된 실제 Q4 연도 (subject 기준)
    note: str = ""

    @property
    def confidence(self) -> str:
        return confidence_band(self.n_peers)


def resolve_peers(
    corp_code: str,
    prefix_len_start: int = 3,
    min_n: int = 5,
    exclude_other_sectors: bool = True,
    size_bucket_decade: Optional[float] = None,
    fs_div: str = "CFS",
    year: Optional[int] = None,
    read_engine=None,
) -> PeerResolution:
    """동종업종 비교를 위한 peer corp_code 목록을 해석한다.

    Adaptive ladder: prefix_len_start(기본 3자리)로 매칭 → peer 수가 min_n 미만이면
    2자리로 fallback. subject 본인은 항상 제외. exclude_other_sectors=True인 경우
    subject와 다른 sector group(금융/지주/부동산/일반)은 제외한다.

    size_bucket_decade가 주어지면 subject의 total_assets 대비 log10 거리가 해당 값
    이하인 peer만 남긴다 (예: 1.0 → ±1 decade).

    year=None이면 subject가 보유한 가장 최근 Q4/fs_div 연도를 사용한다.
    """
    active_engine = read_engine or engine
    with active_engine.connect() as conn:
        subject_row = conn.execute(
            text("SELECT induty_code FROM companies WHERE corp_code = :cc"),
            {"cc": corp_code},
        ).first()

        if subject_row is None or not subject_row[0]:
            return PeerResolution(
                peer_corp_codes=[],
                matched_prefix_len=prefix_len_start,
                sector_group=SectorGroup.UNKNOWN,
                n_peers=0,
                note="subject corp_code 미등록 또는 induty_code 없음",
            )

        subject_induty = subject_row[0]
        subject_sector = classify_sector(subject_induty)

        # year 1회 해석 (ladder 양쪽 rung에서 동일하게 사용)
        resolved_year = year
        if resolved_year is None:
            year_row = conn.execute(
                text(
                    "SELECT MAX(year) FROM financials "
                    "WHERE quarter=4 AND fs_div=:fs AND corp_code=:cc"
                ),
                {"fs": fs_div, "cc": corp_code},
            ).first()
            resolved_year = year_row[0] if year_row and year_row[0] else None

        peers: list[str] = []
        matched_plen = prefix_len_start
        for plen in (prefix_len_start, 2):
            peers = _query_peers(
                conn=conn,
                subject_induty=subject_induty,
                subject_corp_code=corp_code,
                subject_sector=subject_sector,
                prefix_len=plen,
                exclude_other_sectors=exclude_other_sectors,
                size_bucket_decade=size_bucket_decade,
                fs_div=fs_div,
                year=resolved_year,
            )
            matched_plen = plen
            if len(peers) >= min_n:
                break

    excluded = (
        [s.value for s in SectorGroup
         if s != subject_sector and s != SectorGroup.UNKNOWN]
        if exclude_other_sectors else []
    )

    return PeerResolution(
        peer_corp_codes=peers,
        matched_prefix_len=matched_plen,
        sector_group=subject_sector,
        n_peers=len(peers),
        excluded_categories=excluded,
        size_bucket_applied=size_bucket_decade,
        resolved_year=resolved_year,
        note=_build_note(
            matched_plen,
            len(peers),
            size_bucket_decade,
            subject_sector=subject_sector,
            prefix_len_start=prefix_len_start,
        ),
    )


def _query_peers(
    *,
    conn,
    subject_induty: str,
    subject_corp_code: str,
    subject_sector: SectorGroup,
    prefix_len: int,
    exclude_other_sectors: bool,
    size_bucket_decade: Optional[float],
    fs_div: str,
    year: Optional[int],
) -> list[str]:
    prefix = subject_induty[:prefix_len]

    if year is None:
        return []

    subject_assets = None
    if size_bucket_decade is not None:
        ta_row = conn.execute(
            text(
                "SELECT total_assets FROM financials "
                "WHERE corp_code=:cc AND year=:y AND quarter=4 AND fs_div=:fs"
            ),
            {"cc": subject_corp_code, "y": year, "fs": fs_div},
        ).first()
        subject_assets = ta_row[0] if ta_row and ta_row[0] else None

    rows = conn.execute(
        text(
            "SELECT DISTINCT c.corp_code, c.induty_code, f.total_assets "
            "FROM companies c "
            "JOIN financials f ON f.corp_code = c.corp_code "
            "WHERE substr(c.induty_code,1,:plen) = :prefix "
            "  AND c.corp_code != :subject_cc "
            "  AND f.year = :year AND f.quarter = 4 AND f.fs_div = :fs"
        ),
        {
            "plen": prefix_len,
            "prefix": prefix,
            "subject_cc": subject_corp_code,
            "year": year,
            "fs": fs_div,
        },
    ).all()

    out: list[str] = []
    for cc, induty, ta in rows:
        if exclude_other_sectors:
            if classify_sector(induty) != subject_sector:
                continue
        if size_bucket_decade is not None:
            if not (subject_assets and subject_assets > 0 and ta and ta > 0):
                continue  # 음수/0 자산은 size_bucket 비교 불가
            if abs(math.log10(ta) - math.log10(subject_assets)) > size_bucket_decade:
                continue
        out.append(cc)
    return out


def _build_note(
    matched_plen: int,
    n: int,
    size_bucket: Optional[float],
    *,
    subject_sector: SectorGroup = SectorGroup.UNKNOWN,
    prefix_len_start: int = 3,
) -> str:
    # n_peers는 의도적으로 노출하지 않음. 외부 호출자(api.py)가 NULL 필터 후 n을
    # 별도로 갖고 있어 두 개의 count가 동시에 보이면 혼란을 준다. 여기는 resolution
    # 속성(sector·prefix·fallback·size_bucket·warning)만 기록한다.
    parts = [
        f"sector={subject_sector.value}",
        f"KSIC prefix_len={matched_plen} 매칭",
    ]
    if prefix_len_start > matched_plen:
        parts.append(f"⚠ p{prefix_len_start}→p{matched_plen} fallback")
    if size_bucket is not None:
        parts.append(f"size_bucket=±{size_bucket} decade")
    if n < _N_LOW:
        parts.append("⚠ peer 수가 부족합니다 (n<5 → P25/P75 신뢰도 낮음)")
    return " · ".join(parts)


@contextmanager
def peer_read_engine():
    """Yield a schema-checked read engine without creating SQLite files/sidecars."""
    source_engine = engine
    if source_engine.dialect.name == "sqlite":
        database = source_engine.url.database
        if database not in {None, "", ":memory:"}:
            database_path = Path(str(database)).expanduser().resolve()
            if not database_path.is_file():
                raise PeerDatabaseUnavailable("runtime_db_unavailable")
            wal_path = Path(f"{database_path}-wal")
            if wal_path.exists() and wal_path.stat().st_size > 0:
                raise PeerDatabaseUnavailable(
                    "runtime_db_unavailable:uncheckpointed_wal"
                )
            readonly_engine = create_engine(
                f"sqlite:///file:{database_path.as_posix()}?mode=ro&immutable=1&uri=true",
                connect_args={"check_same_thread": False},
            )
            try:
                _validate_peer_schema(readonly_engine)
                yield readonly_engine
            finally:
                readonly_engine.dispose()
            return
    _validate_peer_schema(source_engine)
    yield source_engine


def _validate_peer_schema(read_engine) -> None:
    try:
        inspector = inspect(read_engine)
        tables = set(inspector.get_table_names())
    except Exception as exc:
        raise PeerDatabaseUnavailable(
            f"runtime_db_unavailable:{type(exc).__name__}"
        ) from exc
    missing = sorted({"companies", "financials"} - tables)
    if missing:
        raise PeerDatabaseUnavailable(f"missing_schema:{','.join(missing)}")
    required_columns = {
        "companies": {"corp_code", "corp_name", "stock_code", "market", "induty_code"},
        "financials": {
            "corp_code",
            "year",
            "quarter",
            "fs_div",
            "revenue",
            "operating_profit",
            "net_income",
            "total_assets",
            "total_debt",
            "total_equity",
        },
    }
    for table_name, required in required_columns.items():
        columns = {
            str(column["name"])
            for column in inspector.get_columns(table_name)
        }
        missing_columns = sorted(required - columns)
        if missing_columns:
            raise PeerDatabaseUnavailable(
                f"missing_columns:{table_name}:{','.join(missing_columns)}"
            )


def _table_columns(read_engine, table_name: str) -> set[str]:
    inspector = inspect(read_engine)
    if table_name not in set(inspector.get_table_names()):
        return set()
    return {str(column["name"]) for column in inspector.get_columns(table_name)}


def _profile_table_available(
    read_engine,
    table_name: str,
    required_columns: set[str],
) -> bool:
    columns = _table_columns(read_engine, table_name)
    return bool(columns) and required_columns <= columns


def _resolve_company_row(conn, company: str):
    exact_rows = conn.execute(
        text(
            """
            SELECT corp_code, corp_name, stock_code, market, induty_code
            FROM companies
            WHERE corp_code=:company
               OR stock_code=:company
               OR corp_name=:company
            ORDER BY
                CASE
                    WHEN corp_code=:company THEN 0
                    WHEN stock_code=:company THEN 1
                    ELSE 2
                END,
                corp_code
            LIMIT 6
            """
        ),
        {"company": company},
    ).mappings().all()
    if exact_rows:
        first = exact_rows[0]
        if first["corp_code"] == company or first["stock_code"] == company:
            return first
        if len(exact_rows) == 1:
            return first
        _raise_ambiguous_company(company, exact_rows)

    escaped = (
        str(company)
        .replace("!", "!!")
        .replace("%", "!%")
        .replace("_", "!_")
    )
    substring_rows = conn.execute(
        text(
            """
            SELECT corp_code, corp_name, stock_code, market, induty_code
            FROM companies
            WHERE corp_name LIKE :company_like ESCAPE '!'
            ORDER BY corp_code
            LIMIT 6
            """
        ),
        {"company_like": f"%{escaped}%"},
    ).mappings().all()
    if len(substring_rows) == 1:
        return substring_rows[0]
    if len(substring_rows) > 1:
        _raise_ambiguous_company(company, substring_rows)
    return None


def _raise_ambiguous_company(company: str, rows) -> None:
    identities = ", ".join(
        f"{row['corp_code']}:{row['corp_name']}"
        for row in rows[:5]
    )
    suffix = ", ..." if len(rows) > 5 else ""
    raise ValueError(
        f"ambiguous company '{company}': {identities}{suffix}"
    )


def _financial_metrics(row) -> dict[str, float | None]:
    if row is None:
        return {}

    def number(key: str) -> float | None:
        value = row.get(key)
        return float(value) if value is not None else None

    revenue = number("revenue")
    operating_profit = number("operating_profit")
    net_income = number("net_income")
    assets = number("total_assets")
    debt = number("total_debt")
    equity = number("total_equity")
    operating_cf = number("operating_cf")

    def safe_ratio(numerator: float | None, denominator: float | None) -> float | None:
        if numerator is None or denominator is None or denominator <= 0:
            return None
        return numerator / denominator

    return {
        "revenue": revenue,
        "operating_profit": operating_profit,
        "profit_loss": net_income,
        "assets": assets,
        "liabilities": debt,
        "equity": equity,
        "operating_cash_flow": operating_cf,
        "operating_margin": safe_ratio(operating_profit, revenue),
        "net_margin": safe_ratio(net_income, revenue),
        "debt_ratio": safe_ratio(debt, equity),
        "roe": safe_ratio(net_income, equity),
        "roa": safe_ratio(net_income, assets),
    }


def _audit_fee_metrics(row, columns: set[str]) -> tuple[dict[str, float | None], dict[str, str]]:
    if row is None:
        return {}, {}

    def value(name: str) -> float | None:
        raw = row.get(name) if name in columns else None
        return float(raw) if raw is not None else None

    actual_fee = value("actual_fee_m")
    actual_hours = value("actual_hours")
    contract_fee = value("contract_fee_m")
    contract_hours = value("contract_hours")
    legacy_fee = value("audit_fee_m")
    legacy_hours = value("audit_hours")
    if all(
        value is None
        for value in (
            actual_fee,
            actual_hours,
            contract_fee,
            contract_hours,
            legacy_fee,
            legacy_hours,
        )
    ):
        return {}, {}
    if actual_fee is not None:
        fee, fee_basis = actual_fee, "actual"
    elif contract_fee is not None:
        fee, fee_basis = contract_fee, "contract"
    else:
        fee, fee_basis = legacy_fee, "legacy_inferred"
    if actual_hours is not None:
        hours, hours_basis = actual_hours, "actual"
    elif contract_hours is not None:
        hours, hours_basis = contract_hours, "contract"
    else:
        hours, hours_basis = legacy_hours, "legacy_inferred"
    metrics = {
        "audit_fee": fee * 1_000_000 if fee is not None else None,
        "audit_hours": hours,
        "audit_fee_actual": actual_fee * 1_000_000 if actual_fee is not None else None,
        "audit_hours_actual": actual_hours,
        "audit_fee_contract": (
            contract_fee * 1_000_000 if contract_fee is not None else None
        ),
        "audit_hours_contract": contract_hours,
        "nas_ratio": (
            value("nas_ratio")
            if fee_basis in {"actual", "legacy_inferred"}
            else None
        ),
    }
    exact_bases = {
        "audit_fee": fee_basis,
        "audit_hours": hours_basis,
        "audit_fee_actual": "actual",
        "audit_hours_actual": "actual",
        "audit_fee_contract": "contract",
        "audit_hours_contract": "contract",
        "nas_ratio": fee_basis,
    }
    bases = {
        key: exact_bases[key]
        for key, metric_value in metrics.items()
        if metric_value is not None
    }
    return metrics, bases


def _similarity(left: float | None, right: float | None) -> float | None:
    if left is None or right is None or left <= 0 or right <= 0:
        return None
    distance = abs(math.log10(left) - math.log10(right))
    return round(max(0.0, 1.0 - distance / _SIZE_OUTLIER_DECADES), 6)


def _industry_specificity(subject_code: str, candidate_code: str) -> float:
    for prefix_len, score in ((5, 1.0), (4, 0.9), (3, 0.8), (2, 0.6)):
        if subject_code[:prefix_len] == candidate_code[:prefix_len]:
            return score
    return 0.2


def _score_weights(profile: str) -> dict[str, float]:
    if profile == "investor":
        return {
            "industry_specificity": 0.40,
            "asset_similarity": 0.35,
            "revenue_similarity": 0.25,
            "profile_evidence": 0.0,
        }
    return {
        "industry_specificity": 0.30,
        "asset_similarity": 0.20,
        "revenue_similarity": 0.15,
        "profile_evidence": 0.35,
    }


def _weighted_score(
    components: dict[str, float | None],
    weights: dict[str, float],
) -> float:
    available = [
        (components[key], weight)
        for key, weight in weights.items()
        if components.get(key) is not None and weight > 0
    ]
    denominator = sum(weight for _value, weight in available)
    if denominator <= 0:
        return 0.0
    return round(
        sum(float(value) * weight for value, weight in available) / denominator,
        6,
    )


def _profile_evidence(
    conn,
    read_engine,
    corp_code: str,
    year: int,
    fs_div: str,
    profile: str,
) -> tuple[bool, dict[str, float | None], dict[str, str], tuple[str, ...]]:
    if profile == "investor":
        return True, {}, {}, ()
    if profile == "audit_fee":
        columns = _table_columns(read_engine, "audit_fees")
        if not {"corp_code", "bsns_year"} <= columns:
            return False, {}, {}, ("audit_fee_schema_unavailable",)
        selected = [
            name
            for name in (
                "audit_fee_m",
                "audit_hours",
                "actual_fee_m",
                "actual_hours",
                "contract_fee_m",
                "contract_hours",
                "nas_ratio",
            )
            if name in columns
        ]
        if not selected:
            return False, {}, {}, ("audit_fee_columns_unavailable",)
        row = conn.execute(
            text(
                f"SELECT {', '.join(selected)} FROM audit_fees "
                "WHERE corp_code=:cc AND bsns_year=:year LIMIT 1"
            ),
            {"cc": corp_code, "year": year},
        ).mappings().first()
        metrics, bases = _audit_fee_metrics(row, columns)
        return bool(metrics), metrics, bases, ()
    if profile == "audit_risk":
        evidence_specs = (
            (
                "auditors",
                {"corp_code", "bsns_year", "fs_div"},
                "SELECT 1 FROM auditors "
                "WHERE corp_code=:cc AND bsns_year=:year AND fs_div=:fs LIMIT 1",
            ),
            (
                "report_sections",
                {"corp_code", "bsns_year", "source_type"},
                "SELECT 1 FROM report_sections "
                "WHERE corp_code=:cc AND bsns_year=:year "
                "AND source_type='audit_report' LIMIT 1",
            ),
            (
                "audit_matter_items",
                {"corp_code", "bsns_year"},
                "SELECT 1 FROM audit_matter_items "
                "WHERE corp_code=:cc AND bsns_year=:year LIMIT 1",
            ),
            (
                "kam_items",
                {"id", "corp_code", "bsns_year"},
                "SELECT 1 FROM kam_items "
                "WHERE corp_code=:cc AND bsns_year=:year LIMIT 1",
            ),
        )
        evidence_queries: list[str] = []
        limitations: list[str] = []
        valid_tables: set[str] = set()
        for table_name, required_columns, query in evidence_specs:
            if _profile_table_available(
                read_engine,
                table_name,
                required_columns,
            ):
                evidence_queries.append(query)
                valid_tables.add(table_name)
            else:
                limitations.append(f"profile_schema_unavailable:{table_name}")
        exists = any(
            conn.execute(
                text(query),
                {"cc": corp_code, "year": year, "fs": fs_div},
            ).first()
            is not None
            for query in evidence_queries
        )
        if "kam_items" not in valid_tables or conn.execute(
            text(
                "SELECT 1 FROM kam_items "
                "WHERE corp_code=:cc AND bsns_year=:year LIMIT 1"
            ),
            {"cc": corp_code, "year": year},
        ).first() is None:
            limitations.append("kam_evidence_unavailable")
        return exists, {}, {}, tuple(dict.fromkeys(limitations))
    if profile == "accounting_policy":
        if not _profile_table_available(
            read_engine,
            "accounting_policy_items",
            {"corp_code", "bsns_year", "fs_div", "item_key"},
        ):
            return False, {}, {}, (
                "profile_schema_unavailable:accounting_policy_items",
            )
        count = conn.execute(
            text(
                "SELECT COUNT(DISTINCT item_key) FROM accounting_policy_items "
                "WHERE corp_code=:cc AND bsns_year=:year AND fs_div=:fs"
            ),
            {"cc": corp_code, "year": year, "fs": fs_div},
        ).scalar_one()
        return count > 0, {}, {}, ()
    missing_profile_tables = [
        table_name
        for table_name, required_columns in (
            (
                "kam_items",
                {"id", "corp_code", "bsns_year", "normalized_topic"},
            ),
            (
                "audit_procedure_items",
                {
                    "corp_code",
                    "bsns_year",
                    "kam_item_id",
                    "procedure_type",
                },
            ),
        )
        if not _profile_table_available(
            read_engine,
            table_name,
            required_columns,
        )
    ]
    if missing_profile_tables:
        return False, {}, {}, tuple(
            f"profile_schema_unavailable:{table_name}"
            for table_name in missing_profile_tables
        )
    count = conn.execute(
        text(
            "SELECT COUNT(*) FROM audit_procedure_items p "
            "JOIN kam_items k ON k.id=p.kam_item_id "
            "AND k.corp_code=p.corp_code AND k.bsns_year=p.bsns_year "
            "WHERE p.corp_code=:cc AND p.bsns_year=:year "
            "AND p.kam_item_id IS NOT NULL"
        ),
        {"cc": corp_code, "year": year},
    ).scalar_one()
    return count > 0, {}, {}, ()


def _profile_overlap(
    conn,
    profile: str,
    subject_corp_code: str,
    candidate_corp_code: str,
    year: int,
    fs_div: str,
) -> float:
    if profile == "accounting_policy":
        table, field, extra = "accounting_policy_items", "item_key", "AND fs_div=:fs"
    elif profile == "kam_procedure":
        params = {
            "subject": subject_corp_code,
            "candidate": candidate_corp_code,
            "year": year,
        }

        def linked_keys(corp_param: str) -> set[str]:
            return {
                f"{row[0]}:{row[1] or 'unknown'}"
                for row in conn.execute(
                    text(
                        "SELECT DISTINCT p.procedure_type, k.normalized_topic "
                        "FROM audit_procedure_items p "
                        "JOIN kam_items k ON k.id=p.kam_item_id "
                        "AND k.corp_code=p.corp_code "
                        "AND k.bsns_year=p.bsns_year "
                        f"WHERE p.corp_code=:{corp_param} "
                        "AND p.bsns_year=:year AND p.kam_item_id IS NOT NULL"
                    ),
                    params,
                )
                if row[0]
            }

        subject_keys = linked_keys("subject")
        candidate_keys = linked_keys("candidate")
        union = subject_keys | candidate_keys
        return (
            round(len(subject_keys & candidate_keys) / len(union), 6)
            if union
            else 0.0
        )
    else:
        return 1.0
    params = {
        "subject": subject_corp_code,
        "candidate": candidate_corp_code,
        "year": year,
        "fs": fs_div,
    }
    subject_keys = {
        row[0]
        for row in conn.execute(
            text(
                f"SELECT DISTINCT {field} FROM {table} "
                f"WHERE corp_code=:subject AND bsns_year=:year {extra}"
            ),
            params,
        )
        if row[0]
    }
    candidate_keys = {
        row[0]
        for row in conn.execute(
            text(
                f"SELECT DISTINCT {field} FROM {table} "
                f"WHERE corp_code=:candidate AND bsns_year=:year {extra}"
            ),
            params,
        )
        if row[0]
    }
    union = subject_keys | candidate_keys
    return round(len(subject_keys & candidate_keys) / len(union), 6) if union else 0.0


def build_peer_cohort(
    company: str,
    year: int,
    profile: str,
    limit: int,
) -> PeerCohort:
    """Build one deterministic, requested-year-bound explainable peer cohort."""
    if profile not in _PEER_PROFILES:
        raise ValueError(
            f"unsupported peer profile: {profile}; supported={sorted(_PEER_PROFILES)}"
        )
    if not isinstance(year, int) or year < 1900 or year > 9999:
        raise ValueError("year must be a four-digit integer")
    if not isinstance(limit, int) or limit < 1 or limit > 500:
        raise ValueError("limit must be between 1 and 500")

    with peer_read_engine() as read_engine, read_engine.connect() as conn:
        subject = _resolve_company_row(conn, company)
        if subject is None:
            raise ValueError(f"company not found: {company}")
        subject_code = str(subject["corp_code"])
        subject_industry = str(subject["induty_code"] or "")
        subject_sector = classify_sector(subject_industry)
        subject_financial_rows = conn.execute(
            text(
                "SELECT * FROM financials "
                "WHERE corp_code=:cc AND year=:year AND quarter=4 "
                "ORDER BY CASE fs_div WHEN 'CFS' THEN 0 WHEN 'OFS' THEN 1 ELSE 2 END"
            ),
            {"cc": subject_code, "year": year},
        ).mappings().all()
        subject_financial = subject_financial_rows[0] if subject_financial_rows else None
        fs_div = str(subject_financial["fs_div"]) if subject_financial else None
        subject_metrics = _financial_metrics(subject_financial)
        subject_bases: dict[str, str] = {
            key: fs_div
            for key, value in subject_metrics.items()
            if value is not None and fs_div is not None
        }
        limitations: list[str] = []
        if fs_div is None:
            limitations.append("subject_year_unavailable")
        subject_profile_available = False
        if fs_div is not None:
            (
                subject_profile_available,
                subject_profile_metrics,
                subject_profile_bases,
                subject_profile_limitations,
            ) = _profile_evidence(
                conn, read_engine, subject_code, year, fs_div, profile
            )
            subject_metrics.update(subject_profile_metrics)
            subject_bases.update(subject_profile_bases)
            limitations.extend(subject_profile_limitations)
            if profile == "audit_risk" and any(
                subject_metrics.get(key) is None
                for key in ("assets", "liabilities", "equity")
            ):
                subject_profile_available = False
                limitations.append("subject_required_metric_unavailable")
            if profile == "investor" and any(
                subject_metrics.get(key) is None
                for key in ("assets", "revenue")
            ):
                limitations.append("subject_required_metric_unavailable")
            if profile != "investor" and not subject_profile_available:
                limitations.append("subject_profile_evidence_unavailable")

        companies = conn.execute(
            text(
                "SELECT corp_code, corp_name, stock_code, market, induty_code "
                "FROM companies ORDER BY corp_code"
            )
        ).mappings().all()
        candidates: list[PeerMember] = []
        all_exclusions: list[PeerExclusion] = []
        exclusion_counts: dict[str, int] = {}

        def exclude(row, primary: str, *secondary: str) -> None:
            exclusion_counts[primary] = exclusion_counts.get(primary, 0) + 1
            all_exclusions.append(
                PeerExclusion(
                    corp_code=str(row["corp_code"]),
                    corp_name=str(row["corp_name"]),
                    reason_code=primary,
                    secondary_reason_codes=tuple(sorted(set(secondary)))[:5],
                )
            )

        for candidate in companies:
            candidate_code = str(candidate["corp_code"])
            if candidate_code == subject_code:
                exclude(candidate, "subject")
                continue
            if not candidate["stock_code"]:
                exclude(candidate, "unlisted")
                continue
            candidate_industry = str(candidate["induty_code"] or "")
            candidate_sector = classify_sector(candidate_industry)
            if candidate_sector == SectorGroup.UNKNOWN:
                exclude(candidate, "invalid_industry")
                continue
            if (
                subject_sector == SectorGroup.UNKNOWN
                or candidate_sector != subject_sector
            ):
                exclude(candidate, "sector_mismatch")
                continue
            exact_rows = conn.execute(
                text(
                    "SELECT * FROM financials "
                    "WHERE corp_code=:cc AND year=:year AND quarter=4"
                ),
                {"cc": candidate_code, "year": year},
            ).mappings().all()
            if not exact_rows:
                exclude(candidate, "year_unavailable")
                continue
            candidate_financial = next(
                (row for row in exact_rows if row["fs_div"] == fs_div),
                None,
            )
            if candidate_financial is None:
                exclude(candidate, "fs_basis_mismatch")
                continue
            candidate_metrics = _financial_metrics(candidate_financial)
            if profile == "investor" and (
                candidate_metrics.get("assets") is None
                or candidate_metrics.get("revenue") is None
            ):
                exclude(candidate, "missing_required_metric")
                continue
            if profile == "audit_risk" and any(
                candidate_metrics.get(key) is None
                for key in ("assets", "liabilities", "equity")
            ):
                exclude(candidate, "missing_required_metric")
                continue
            if profile != "investor" and not subject_profile_available:
                exclude(
                    candidate,
                    "missing_profile_evidence",
                    "subject_profile_evidence_unavailable",
                )
                continue
            subject_assets = subject_metrics.get("assets")
            candidate_assets = candidate_metrics.get("assets")
            if (
                subject_assets is not None
                and candidate_assets is not None
                and subject_assets > 0
                and candidate_assets > 0
                and abs(math.log10(subject_assets) - math.log10(candidate_assets))
                > _SIZE_OUTLIER_DECADES
            ):
                exclude(candidate, "size_outlier")
                continue
            (
                profile_available,
                profile_metrics,
                profile_bases,
                member_limitations,
            ) = _profile_evidence(
                conn,
                read_engine,
                candidate_code,
                year,
                str(fs_div),
                profile,
            )
            if profile != "investor" and not profile_available:
                exclude(candidate, "missing_profile_evidence")
                continue
            candidate_metrics.update(profile_metrics)
            candidate_bases: dict[str, str] = {
                key: str(fs_div)
                for key, value in candidate_metrics.items()
                if value is not None
            }
            candidate_bases.update(profile_bases)
            components = {
                "industry_specificity": _industry_specificity(
                    subject_industry, candidate_industry
                ),
                "asset_similarity": _similarity(
                    subject_metrics.get("assets"),
                    candidate_metrics.get("assets"),
                ),
                "revenue_similarity": _similarity(
                    subject_metrics.get("revenue"),
                    candidate_metrics.get("revenue"),
                ),
                "profile_evidence": _profile_overlap(
                    conn,
                    profile,
                    subject_code,
                    candidate_code,
                    year,
                    str(fs_div),
                ),
            }
            score = _weighted_score(components, _score_weights(profile))
            reason_codes = (
                "listed",
                "same_requested_year",
                f"fs_basis:{fs_div}",
                f"sector:{subject_sector.value}",
                f"industry_specificity:{components['industry_specificity']}",
                f"profile:{profile}",
            )
            candidates.append(
                PeerMember(
                    corp_code=candidate_code,
                    corp_name=str(candidate["corp_name"]),
                    induty_code=candidate_industry,
                    fs_div=str(fs_div),
                    score=score,
                    reason_codes=reason_codes,
                    score_components=tuple(sorted(components.items())),
                    metric_values=tuple(sorted(candidate_metrics.items())),
                    metric_bases=tuple(sorted(candidate_bases.items())),
                    limitations=member_limitations,
                )
            )
        ranked = sorted(candidates, key=lambda item: (-item.score, item.corp_code))
        members = tuple(ranked[:limit])
        for candidate in ranked[limit:]:
            exclusion_counts["outside_limit"] = (
                exclusion_counts.get("outside_limit", 0) + 1
            )
            all_exclusions.append(
                PeerExclusion(
                    corp_code=candidate.corp_code,
                    corp_name=candidate.corp_name,
                    reason_code="outside_limit",
                )
            )
        returned_exclusions = tuple(
            sorted(
                all_exclusions,
                key=lambda item: (
                    item.reason_code,
                    item.corp_code,
                    item.secondary_reason_codes,
                ),
            )[:_MAX_RETURNED_EXCLUSIONS]
        )
        weights = _score_weights(profile)
        score_policy = (
            ("direction", "higher_score_is_closer"),
            ("tie_breaker", "corp_code_ascending"),
            ("null_handling", "renormalize_available_weight;never_zero_impute"),
            ("size_outlier", f"log10_asset_distance>{_SIZE_OUTLIER_DECADES}"),
            *tuple((f"weight:{key}", str(value)) for key, value in weights.items()),
        )
        return PeerCohort(
            subject_corp_code=subject_code,
            subject_name=str(subject["corp_name"]),
            requested_year=year,
            profile=profile,
            fs_div=fs_div,
            members=members,
            exclusions=returned_exclusions,
            exclusion_counts=tuple(sorted(exclusion_counts.items())),
            total_candidates=len(companies),
            eligible_count=len(ranked),
            subject_metrics=tuple(sorted(subject_metrics.items())),
            subject_metric_bases=tuple(sorted(subject_bases.items())),
            score_policy=score_policy,
            limitations=tuple(dict.fromkeys(limitations)),
        )


def compare_metric(
    cohort: PeerCohort,
    metric_key: str,
) -> PeerMetricComparison:
    """Compare a registered metric without crossing year, FS, or fee bases."""
    if metric_key not in METRICS:
        raise ValueError(f"unsupported metric key: {metric_key}")
    definition = metric_definition(metric_key)
    subject_metrics = dict(cohort.subject_metrics)
    subject_bases = dict(cohort.subject_metric_bases)
    subject_value = subject_metrics.get(metric_key)
    basis = subject_bases.get(metric_key)
    values: list[float] = []
    unavailable = 0
    for member in cohort.members:
        member_value = dict(member.metric_values).get(metric_key)
        member_basis = dict(member.metric_bases).get(metric_key)
        if (
            member_value is None
            or (basis is not None and member_basis != basis)
        ):
            unavailable += 1
            continue
        values.append(float(member_value))
    values.sort()
    n = len(values)
    limitations: list[str] = list(cohort.limitations)
    if cohort.eligible_count > len(cohort.members):
        limitations.append(
            f"cohort_truncated:{len(cohort.members)}/{cohort.eligible_count}"
        )
    if subject_value is None:
        limitations.append("subject_metric_unavailable")
    if unavailable:
        limitations.append(f"peer_metric_unavailable:{unavailable}")
    if n < 5:
        percentile = None
        confidence = "insufficient_n"
    elif subject_value is None:
        percentile = None
        confidence = "subject_unavailable"
    else:
        below = sum(1 for value in values if value < float(subject_value))
        equal = sum(1 for value in values if value == float(subject_value))
        percentile = round(100.0 * (below + 0.5 * equal) / n, 1)
        confidence = "sufficient_n"
    if n < 10 or percentile is None:
        decile = None
    else:
        decile = min(10, max(1, int(percentile // 10) + 1))
    ties = (
        sum(1 for value in values if value == float(subject_value))
        if subject_value is not None
        else 0
    )
    return PeerMetricComparison(
        metric_key=metric_key,
        subject_value=subject_value,
        peer_values=tuple(values),
        n=n,
        unavailable_count=unavailable,
        percentile=percentile,
        decile=decile,
        unit=definition.unit,
        basis=basis,
        confidence=confidence,
        limitations=tuple(dict.fromkeys(limitations)),
        ties=ties,
    )


def cohort_to_peer_group(cohort: PeerCohort) -> dict:
    """Adapt a typed cohort to the established select_peer_group response."""
    subject_metrics = dict(cohort.subject_metrics)
    peers: list[dict] = []
    for member in cohort.members:
        metrics = dict(member.metric_values)
        fee = metrics.get("audit_fee")
        peers.append({
            "corp_code": member.corp_code,
            "stock_code": None,
            "corp_name": member.corp_name,
            "market": None,
            "induty_code": member.induty_code,
            "total_assets": metrics.get("assets"),
            "revenue": metrics.get("revenue"),
            "audit_fee_m": fee / 1_000_000 if fee is not None else None,
            "audit_hours": metrics.get("audit_hours"),
            "nas_ratio": metrics.get("nas_ratio"),
            "include_reasons": list(member.reason_codes),
            "reason_components": dict(member.score_components),
        })
    return {
        "subject": {
            "corp_code": cohort.subject_corp_code,
            "stock_code": None,
            "corp_name": cohort.subject_name,
            "market": None,
            "induty_code": None,
        },
        "selection_policy": {
            "criteria": ["typed_peer_cohort"],
            "profile": cohort.profile,
            "fs_div_used": cohort.fs_div,
            "requested_year": cohort.requested_year,
            "resolved_year": cohort.requested_year if cohort.fs_div else None,
            "score_policy": dict(cohort.score_policy),
        },
        "peer_count": cohort.eligible_count,
        "returned_peer_count": len(cohort.members),
        "confidence": confidence_band(cohort.eligible_count),
        "peers": peers,
        "excluded_categories": [],
        "note": "typed explainable peer cohort compatibility adapter",
        "cohort_metadata": {
            "profile": cohort.profile,
            "requested_year": cohort.requested_year,
            "fs_div": cohort.fs_div,
            "total_candidates": cohort.total_candidates,
            "eligible_count": cohort.eligible_count,
            "selected_count": len(cohort.members),
            "exclusion_counts": dict(cohort.exclusion_counts),
            "denominator_metadata": dict(cohort.denominator_metadata),
            "limitations": list(cohort.limitations),
            "subject_metric_count": sum(
                value is not None for value in subject_metrics.values()
            ),
        },
    }
