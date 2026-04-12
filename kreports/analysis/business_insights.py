"""
kreports.analysis.business_insights — 사업보고서 기반 자동 인사이트.

사업보고서 텍스트 + 재무 데이터에서 rule-based 인사이트를 추출한다.
LLM 없이 정규식 + 데이터 조합으로 감사인/투자자 핵심 포인트 제공.
"""
from __future__ import annotations

import re
from typing import Optional


# ---------------------------------------------------------------------------
# R&D 비율 추출 (연구개발활동 섹션 테이블 파싱)
# ---------------------------------------------------------------------------

def extract_rd_ratio(sections: dict) -> Optional[dict]:
    """
    연구개발활동 섹션에서 R&D 비용/매출 비율 추출.

    Returns:
        {"ratios": [{"period": str, "ratio_pct": float, "amount_m": int, "revenue_m": int}],
         "trend": "increasing"/"decreasing"/"stable"}
        또는 None
    """
    rd_section = sections.get("rd_activities") or sections.get("key_contracts")
    if not rd_section:
        return None

    body = rd_section.get("body_text", "")
    if not body:
        return None

    # 공백 정규화 (테이블 변환 후 다중 공백/줄바꿈 정리)
    body_clean = re.sub(r'\s+', ' ', body)

    # "연구개발비 / 매출액 비율" 패턴 (X.X%)
    ratio_matches = re.findall(
        r'연구개발비\s*/\s*매출액\s*비율[^%]*?(\d+\.?\d*)\s*%',
        body_clean,
    )
    # 대안: "X.X%" 패턴을 비율 행 근처에서 찾기
    if not ratio_matches:
        ratio_matches = re.findall(
            r'매출액\s*비율.*?(\d+\.?\d*)\s*%',
            body_clean,
        )

    # "연구개발비용 합계" 행의 금액
    amount_matches = re.findall(
        r'연구개발비용\s*합계.*?([\d,]{4,})',
        body_clean,
    )

    # "매출액" 행의 금액 (연구개발 섹션 내)
    revenue_matches = re.findall(
        r'매출액\s+([\d,]{4,})',
        body_clean,
    )

    if not ratio_matches:
        return None

    ratios = []
    for i, r_str in enumerate(ratio_matches):
        entry = {"ratio_pct": float(r_str)}
        if i < len(amount_matches):
            entry["amount_m"] = int(amount_matches[i].replace(",", ""))
        if i < len(revenue_matches):
            entry["revenue_m"] = int(revenue_matches[i].replace(",", ""))
        ratios.append(entry)

    # 추세 판단
    if len(ratios) >= 2:
        first = ratios[0]["ratio_pct"]
        last = ratios[-1]["ratio_pct"]
        if first > last + 1:
            trend = "decreasing"
        elif last > first + 1:
            trend = "increasing"
        else:
            trend = "stable"
    else:
        trend = "unknown"

    return {"ratios": ratios, "trend": trend}


# ---------------------------------------------------------------------------
# 위험 유형 분포 (위험관리 섹션 키워드 빈도)
# ---------------------------------------------------------------------------

_RISK_CATEGORIES = {
    "환율": ["환율", "외환", "외화", "환위험", "환변동"],
    "이자율": ["이자율", "금리", "이자비용", "변동금리"],
    "유동성": ["유동성", "자금조달", "차입금", "만기"],
    "신용": ["신용위험", "대손", "채권회수", "거래상대방"],
    "가격": ["가격위험", "원자재", "원재료", "시장가격"],
    "규제": ["규제", "법규", "컴플라이언스", "인허가"],
    "파생상품": ["파생", "스왑", "옵션", "헤지"],
}


def extract_risk_distribution(sections: dict) -> Optional[dict]:
    """
    위험관리 섹션에서 위험 유형별 키워드 빈도 분석.

    Returns:
        {"categories": [{"name", "count", "pct"}],
         "top_risk": str,
         "total_mentions": int}
    """
    risk_section = sections.get("risk_management")
    if not risk_section:
        return None

    body = risk_section.get("body_text", "")
    if len(body) < 100:
        return None

    counts = {}
    total = 0
    for cat, keywords in _RISK_CATEGORIES.items():
        cnt = sum(body.count(kw) for kw in keywords)
        counts[cat] = cnt
        total += cnt

    if total == 0:
        return None

    categories = sorted(
        [
            {"name": cat, "count": cnt, "pct": round(100 * cnt / total, 1)}
            for cat, cnt in counts.items()
            if cnt > 0
        ],
        key=lambda x: x["count"],
        reverse=True,
    )

    return {
        "categories": categories,
        "top_risk": categories[0]["name"] if categories else None,
        "total_mentions": total,
    }


# ---------------------------------------------------------------------------
# 핵심 리스크 하이라이트 (첫 언급 위험 = 경영진 최우선 관심사)
# ---------------------------------------------------------------------------

def extract_top_risk_highlight(sections: dict) -> Optional[str]:
    """위험관리 섹션의 첫 200자에서 핵심 리스크 문장 추출."""
    risk_section = sections.get("risk_management")
    if not risk_section:
        return None

    body = risk_section.get("body_text", "")
    # 제목 행 건너뛰기
    lines = [l.strip() for l in body.split("\n") if len(l.strip()) > 30]
    if not lines:
        return None

    # 첫 실질 문장 (숫자 시작이 아닌 것)
    for line in lines[:5]:
        if not re.match(r'^\d+\.', line) and len(line) > 50:
            return line[:200] + ("..." if len(line) > 200 else "")

    return lines[0][:200] if lines else None


# ---------------------------------------------------------------------------
# 주요계약 분석
# ---------------------------------------------------------------------------

def extract_key_contracts_summary(sections: dict) -> Optional[dict]:
    """주요계약 섹션에서 계약 건수 + 상대방 추출."""
    contracts_section = sections.get("key_contracts")
    if not contracts_section:
        return None

    body = contracts_section.get("body_text", "")
    if len(body) < 50:
        return None

    # "계약 상대방" 또는 "계약상대방" 뒤의 회사명
    parties = re.findall(r'(?:계약\s*상대방|거래상대방)\s*[:\s]*([A-Za-z가-힣\s]+?)(?:\s{2,}|\n|$)', body)
    # "계약 유형" 추출
    types = re.findall(r'(?:계약\s*유형|계약유형)\s*[:\s]*([^\n]+)', body)

    return {
        "party_count": len(set(parties)),
        "parties": list(set(p.strip() for p in parties if p.strip()))[:5],
        "contract_types": list(set(t.strip() for t in types if t.strip()))[:5],
    }


# ---------------------------------------------------------------------------
# 업종별 핵심 키워드 (KSIC 2자리 prefix → 키워드 + 라벨)
# ---------------------------------------------------------------------------

_INDUSTRY_KEYWORDS: dict[str, list[dict]] = {
    # 반도체/전자 (KSIC 26)
    "26": [
        {"keywords": ["HBM", "DRAM", "NAND", "DDR5", "DDR4"], "label": "메모리 제품군", "audience": "investor"},
        {"keywords": ["EUV", "공정", "나노", "미세화", "적층"], "label": "공정 기술", "audience": "investor"},
        {"keywords": ["AI", "데이터센터", "서버", "온디바이스"], "label": "AI/데이터센터 노출", "audience": "investor"},
        {"keywords": ["가동률", "CAPA", "생산능력", "증설", "팹"], "label": "생산능력/증설", "audience": "auditor"},
        {"keywords": ["재고", "재고자산", "재고평가", "순실현가능가치"], "label": "재고 리스크", "audience": "auditor"},
    ],
    # 금융 (KSIC 64-66)
    "64": [
        {"keywords": ["NIM", "순이자마진", "이자이익", "예대마진"], "label": "순이자마진", "audience": "investor"},
        {"keywords": ["NPL", "고정이하여신", "연체", "부실채권"], "label": "자산건전성", "audience": "auditor"},
        {"keywords": ["BIS", "자기자본비율", "보통주자본"], "label": "자본적정성", "audience": "auditor"},
        {"keywords": ["충당금", "대손", "손실충당금"], "label": "충당금 정책", "audience": "auditor"},
    ],
    "65": [  # 보험
        {"keywords": ["보험료", "위험률", "손해율", "IFRS 17"], "label": "보험 핵심 지표", "audience": "both"},
        {"keywords": ["지급여력", "RBC", "K-ICS"], "label": "지급여력비율", "audience": "auditor"},
    ],
    # 제조/자동차 (KSIC 29-30)
    "29": [
        {"keywords": ["수주", "수주잔고", "백로그", "수주산업"], "label": "수주 현황", "audience": "investor"},
        {"keywords": ["원재료", "원자재", "소재비", "원가율"], "label": "원가 구조", "audience": "auditor"},
    ],
    "30": [
        {"keywords": ["전기차", "EV", "하이브리드", "배터리", "전동화"], "label": "전동화 전환", "audience": "investor"},
        {"keywords": ["수주", "수주잔고", "글로벌 판매"], "label": "판매/수주", "audience": "investor"},
        {"keywords": ["리콜", "품질", "보증충당"], "label": "품질/리콜 리스크", "audience": "auditor"},
    ],
    # 바이오/제약 (KSIC 21)
    "21": [
        {"keywords": ["임상", "FDA", "MFDS", "식약처", "승인"], "label": "파이프라인/규제", "audience": "investor"},
        {"keywords": ["특허", "물질특허", "만료", "제네릭"], "label": "특허 리스크", "audience": "both"},
        {"keywords": ["CMO", "CDMO", "위탁생산", "바이오시밀러"], "label": "사업 모델", "audience": "investor"},
        {"keywords": ["연구개발", "R&D", "개발비 자본화"], "label": "R&D 회계처리", "audience": "auditor"},
    ],
    # IT서비스/소프트웨어 (KSIC 58, 62)
    "58": [
        {"keywords": ["구독", "SaaS", "ARR", "MRR", "이용자"], "label": "구독 모델", "audience": "investor"},
        {"keywords": ["콘텐츠", "라이선스", "저작권"], "label": "콘텐츠 자산", "audience": "auditor"},
    ],
    "62": [
        {"keywords": ["클라우드", "AWS", "Azure", "GCP"], "label": "클라우드 전환", "audience": "investor"},
        {"keywords": ["SI", "시스템통합", "유지보수", "구축"], "label": "SI 사업 구조", "audience": "investor"},
        {"keywords": ["인건비", "인력", "개발자", "이직"], "label": "인력 의존도", "audience": "auditor"},
    ],
    # 건설 (KSIC 41-42)
    "41": [
        {"keywords": ["수주잔고", "신규수주", "미착공"], "label": "수주 파이프라인", "audience": "investor"},
        {"keywords": ["PF", "프로젝트파이낸싱", "브릿지론", "분양률"], "label": "PF 노출", "audience": "auditor"},
        {"keywords": ["공사손실", "공사미수금", "진행률"], "label": "공사 리스크", "audience": "auditor"},
    ],
    # 에너지/유틸리티 (KSIC 35)
    "35": [
        {"keywords": ["발전", "태양광", "풍력", "신재생", "REC"], "label": "신재생 전환", "audience": "investor"},
        {"keywords": ["전력거래", "SMP", "용량요금"], "label": "전력 수익 구조", "audience": "investor"},
    ],
    # 유통/소매 (KSIC 47)
    "47": [
        {"keywords": ["온라인", "이커머스", "O2O", "배송"], "label": "온라인 전환", "audience": "investor"},
        {"keywords": ["점포", "매장", "출점", "폐점"], "label": "점포 전략", "audience": "investor"},
    ],
}


def _extract_industry_insights(sections: dict, induty_code: str) -> list[dict]:
    """업종 특화 키워드 기반 인사이트 추출."""
    if not induty_code:
        return []

    prefix = induty_code[:2]
    templates = _INDUSTRY_KEYWORDS.get(prefix, [])
    if not templates:
        return []

    # 전체 섹션 텍스트 합산
    full_text = " ".join(
        s.get("body_text", "") for s in sections.values() if isinstance(s, dict)
    )
    if not full_text:
        return []

    full_text_clean = re.sub(r'\s+', ' ', full_text)
    insights = []

    for tmpl in templates:
        count = sum(full_text_clean.count(kw) for kw in tmpl["keywords"])
        if count == 0:
            continue

        # 첫 매칭 문맥 추출 (키워드 주변 80자)
        context = ""
        for kw in tmpl["keywords"]:
            pos = full_text_clean.find(kw)
            if pos >= 0:
                start = max(0, pos - 20)
                end = min(len(full_text_clean), pos + 60)
                context = full_text_clean[start:end].strip()
                break

        insights.append({
            "title": tmpl["label"],
            "value": f"{count}회 언급",
            "detail": context[:80] + "..." if len(context) > 80 else context,
            "audience": tmpl["audience"],
            "risk_level": "ok",
        })

    return insights


# ---------------------------------------------------------------------------
# 종합 인사이트 생성
# ---------------------------------------------------------------------------

def generate_business_insights(sections: dict, induty_code: str = "") -> list[dict]:
    """
    사업보고서 섹션에서 인사이트 카드 목록 생성.
    induty_code가 있으면 업종 특화 인사이트도 포함.

    Returns:
        [{"title", "value", "detail", "audience", "risk_level"}]
    """
    insights = []

    # 0. 업종 특화 인사이트 (최상단)
    if induty_code:
        industry_insights = _extract_industry_insights(sections, induty_code)
        insights.extend(industry_insights)

    # 1. R&D 비율
    rd = extract_rd_ratio(sections)
    if rd and rd["ratios"]:
        latest = rd["ratios"][0]
        ratio = latest["ratio_pct"]
        trend_label = {"increasing": "증가 추세", "decreasing": "감소 추세",
                       "stable": "안정", "unknown": "-"}.get(rd["trend"], "-")
        risk = "warn" if ratio > 15 else ("ok" if ratio > 3 else "bad")
        insights.append({
            "title": "R&D / 매출 비율",
            "value": f"{ratio}%",
            "detail": f"{len(rd['ratios'])}개년 추세: {trend_label}",
            "audience": "both",
            "risk_level": risk,
        })

    # 2. 위험 분포
    risk_dist = extract_risk_distribution(sections)
    if risk_dist and risk_dist["top_risk"]:
        top = risk_dist["top_risk"]
        insights.append({
            "title": "최대 위험 유형",
            "value": top,
            "detail": f"총 {risk_dist['total_mentions']}회 언급 중 {risk_dist['categories'][0]['pct']}%",
            "audience": "auditor",
            "risk_level": "warn" if top in ("유동성", "신용") else "ok",
        })

    # 3. 핵심 리스크 하이라이트
    highlight = extract_top_risk_highlight(sections)
    if highlight:
        # 핵심 키워드 추출 (첫 문장에서 위험 유형 식별)
        risk_keywords = ["외환", "환율", "이자율", "금리", "유동성", "신용", "가격",
                         "규제", "경쟁", "기술", "인력", "공급망", "지정학", "환경"]
        found_risks = [kw for kw in risk_keywords if kw in highlight[:100]]
        summary = ", ".join(found_risks[:3]) if found_risks else "복합 위험"
        insights.append({
            "title": "경영진 최우선 리스크",
            "value": summary,
            "detail": highlight,  # 전문 텍스트 — 대시보드에서 툴팁으로 표시
            "audience": "both",
            "risk_level": "ok",
        })

    # 4. 주요계약 요약
    contracts = extract_key_contracts_summary(sections)
    if contracts and contracts["party_count"] > 0:
        insights.append({
            "title": "주요 계약 상대방",
            "value": f"{contracts['party_count']}개사",
            "detail": ", ".join(contracts["parties"][:3]),
            "audience": "investor",
            "risk_level": "ok",
        })

    # 5. 사업 개요 길이 (정보 밀도 프록시)
    overview = sections.get("business_overview")
    if overview:
        length = overview.get("length", 0)
        if length < 200:
            insights.append({
                "title": "사업개요 정보 밀도",
                "value": "부족",
                "detail": f"{length}자 — 실질 정보가 적음. 사업내용 섹션 참조 필요.",
                "audience": "auditor",
                "risk_level": "warn",
            })

    return insights
