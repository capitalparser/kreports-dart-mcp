"""
DART Financial Collector — 내부 REST API.

로컬 및 사내망 전용. 외부 공개 금지.

실행:
    uvicorn api.main:app --host 127.0.0.1 --port 8000 --reload

    # 스케줄러 포함 실행 (일별 증분 수집 활성화)
    DART_SCHEDULER=1 uvicorn api.main:app --host 127.0.0.1 --port 8000

Swagger UI:
    http://127.0.0.1:8000/docs
"""
import os
import sys
import logging
from contextlib import asynccontextmanager
from pathlib import Path

# dart_platform 패키지 경로 추가 (프로젝트 루트 기준)
sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routers import companies, financials, disclosures, auditors

logger = logging.getLogger(__name__)

_scheduler = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """서버 시작/종료 시 스케줄러 생명주기를 관리한다."""
    global _scheduler
    if os.getenv("DART_SCHEDULER", "").strip() == "1":
        from dart_platform.collector.scheduler import start_scheduler
        _scheduler = start_scheduler()
        logger.info("일별 증분 수집 스케줄러 활성화")
    yield
    if _scheduler is not None:
        _scheduler.shutdown()
        logger.info("스케줄러 종료")


app = FastAPI(
    title="DART Financial Collector API",
    description="감사팀 현장 지원 · 재무 위험 조기 경보 · 감사인 이력 추적",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# 로컬/사내망 전용 CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8501",   # Streamlit 기본 포트
        "http://127.0.0.1:8501",
        "http://localhost:3000",   # 향후 React 대시보드용
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["GET"],
    allow_headers=["*"],
)

app.include_router(companies.router, prefix="/api")
app.include_router(financials.router, prefix="/api")
app.include_router(disclosures.router, prefix="/api")
app.include_router(auditors.router, prefix="/api")


@app.get("/", tags=["health"])
def root():
    return {"status": "ok", "service": "DART Financial Collector API"}


@app.get("/health", tags=["health"])
def health():
    """DB 연결 상태 확인."""
    from dart_platform.db.engine import get_session
    from dart_platform.db.models import Company
    try:
        with get_session() as session:
            count = session.query(Company).count()
        return {"status": "ok", "companies": count}
    except Exception as e:
        return {"status": "error", "detail": str(e)}


@app.get("/api/scheduler/jobs", tags=["scheduler"])
def get_scheduler_jobs():
    """등록된 스케줄 작업 목록을 반환한다. DART_SCHEDULER=1 환경에서만 유효."""
    if _scheduler is None:
        return {"active": False, "jobs": []}
    from dart_platform.collector.scheduler import list_jobs
    return {"active": True, "jobs": list_jobs(_scheduler)}
