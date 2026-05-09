"""
test_dart_mcp.py — MCP 서버 stdio 프로토콜 및 도구 핸들러 검증.

세 레이어 테스트:
  1. Unit: tools.call_tool(name, args) 직접 호출
  2. Integration: dart_mcp 모듈 import, ALL_TOOLS/HANDLERS 정합성
  3. E2E: stdio 서버 subprocess 기동 → tools/list → tools/call (삼성전자)
"""
from __future__ import annotations

import asyncio
import json
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

from kreports.mcp.tools import ALL_TOOLS, HANDLERS, call_tool


PROJECT_ROOT = Path(__file__).parent.parent


# ---------------------------------------------------------------------------
# 1. Unit: tools.call_tool
# ---------------------------------------------------------------------------

EXPECTED_TOOL_COUNT = 10  # search, get_financial_snapshot, score_going_concern,
                         # detect_restatement, get_accounting_policy,
                         # get_audit_history, get_subsidiary_auditors, compare_to_industry,
                         # get_business_overview, get_investor_signals


class TestToolRegistryConsistency:
    def test_tools_registered(self):
        assert len(ALL_TOOLS) == EXPECTED_TOOL_COUNT

    def test_all_tools_have_handlers(self):
        tool_names = {t.name for t in ALL_TOOLS}
        handler_names = set(HANDLERS.keys())
        assert tool_names == handler_names

    def test_all_tools_have_description(self):
        for tool in ALL_TOOLS:
            assert tool.description
            assert len(tool.description) > 20

    def test_all_tools_have_input_schema(self):
        for tool in ALL_TOOLS:
            assert tool.inputSchema
            assert tool.inputSchema.get("type") == "object"
            assert "properties" in tool.inputSchema


class TestCallToolErrors:
    def test_unknown_tool_returns_error(self):
        result = json.loads(call_tool("nonexistent", {}))
        assert "error" in result
        assert "Unknown tool" in result["error"]
        assert "available" in result
        assert len(result["available"]) == EXPECTED_TOOL_COUNT

    def test_unknown_company_returns_error(self):
        result = json.loads(call_tool(
            "score_going_concern",
            {"company": "절대로존재하지않는기업명XYZ"},
        ))
        assert "error" in result
        assert "찾을 수 없습니다" in result["error"]

    def test_invalid_limit_returns_user_error(self):
        result = json.loads(call_tool(
            "search_company",
            {"query": "삼성전자", "limit": 0},
        ))
        assert "error" in result
        assert "limit" in result["error"]

    def test_investor_signals_unknown_company_returns_error(self):
        result = json.loads(call_tool(
            "get_investor_signals",
            {"company": "절대로존재하지않는기업명XYZ"},
        ))
        assert "error" in result
        assert "찾을 수 없습니다" in result["error"]


class TestCallToolRealData:
    """삼성전자 데이터가 DB에 있는 경우의 실제 호출."""

    def _samsung_cc(self) -> str | None:
        import kreports
        return kreports.resolve_corp_code("005930")

    def test_search_company_samsung(self):
        result = json.loads(call_tool("search_company", {"query": "삼성전자"}))
        assert "count" in result
        assert "results" in result
        if result["count"] == 0:
            pytest.skip("삼성전자 DB 미등록")
        assert any("삼성전자" in r["corp_name"] for r in result["results"])
        assert result["_meta"]["source"] == "local_kreports_db"
        assert result["_meta"]["tool"] == "search_company"

    def test_get_financial_snapshot_by_stock_code(self):
        samsung = self._samsung_cc()
        if samsung is None:
            pytest.skip("삼성전자 DB 미등록")
        result = json.loads(call_tool(
            "get_financial_snapshot",
            {"company": "005930", "years": 2},
        ))
        assert result["corp_code"] == samsung
        assert result["unit"] == "억원"
        assert result["row_count"] >= 1
        assert result["_meta"]["company"]["corp_code"] == samsung
        assert "financial" in result["_meta"]["data_freshness"]

    def test_get_financial_snapshot_by_name(self):
        samsung = self._samsung_cc()
        if samsung is None:
            pytest.skip("삼성전자 DB 미등록")
        result = json.loads(call_tool(
            "get_financial_snapshot",
            {"company": "삼성전자", "years": 2},
        ))
        assert result["corp_code"] == samsung

    def test_ambiguous_company_name_returns_candidates(self):
        hits = json.loads(call_tool("search_company", {"query": "삼성", "limit": 2}))
        if hits["count"] < 2:
            pytest.skip("ambiguous 삼성 candidates unavailable")
        result = json.loads(call_tool(
            "score_going_concern",
            {"company": "삼성"},
        ))
        assert "error" in result
        assert "모호" in result["error"]
        assert "후보" in result["error"]

    def test_score_going_concern(self):
        samsung = self._samsung_cc()
        if samsung is None:
            pytest.skip("삼성전자 DB 미등록")
        result = json.loads(call_tool("score_going_concern", {"company": samsung}))
        assert result["has_data"] is True
        assert result["grade"] == "안정"
        assert 0 <= result["score"] <= 100

    def test_get_audit_history(self):
        samsung = self._samsung_cc()
        if samsung is None:
            pytest.skip("삼성전자 DB 미등록")
        result = json.loads(call_tool("get_audit_history", {"company": samsung}))
        assert result["count"] >= 1

    def test_get_investor_signals(self):
        samsung = self._samsung_cc()
        if samsung is None:
            pytest.skip("삼성전자 DB 미등록")
        result = json.loads(call_tool(
            "get_investor_signals",
            {"company": samsung, "years": 3, "window_days": 365},
        ))
        assert result["corp_code"] == samsung
        assert "quality_snapshot" in result
        assert "accounting_risk" in result
        assert "event_counts" in result
        assert result["_meta"]["tool"] == "get_investor_signals"

    def test_subsidiary_auditors_default_slim_100(self):
        """기본값: slim=True, limit=100."""
        samsung = self._samsung_cc()
        if samsung is None:
            pytest.skip("삼성전자 DB 미등록")
        result = json.loads(call_tool(
            "get_subsidiary_auditors", {"company": samsung}
        ))
        if result.get("total", 0) == 0:
            pytest.skip("종속회사 데이터 없음")
        # 기본 limit은 100
        assert result["count"] <= 100
        # truncated 플래그
        if result["total"] > 100:
            assert result["truncated"] is True
        # slim 모드: 핵심 필드만
        expected_keys = {
            "name", "relation", "ownership_pct", "listed_yn",
            "corp_code", "stock_code", "market", "auditor",
        }
        if result["subsidiaries"]:
            actual_keys = set(result["subsidiaries"][0].keys())
            assert actual_keys == expected_keys, f"slim 모드 필드 불일치: {actual_keys}"

    def test_subsidiary_auditors_only_with_auditor(self):
        samsung = self._samsung_cc()
        if samsung is None:
            pytest.skip("삼성전자 DB 미등록")
        result = json.loads(call_tool(
            "get_subsidiary_auditors",
            {"company": samsung, "only_with_auditor": True, "limit": 500},
        ))
        if "error" in result and "DART_API_KEY" in result["error"]:
            pytest.skip("종속회사 데이터 수집에 DART_API_KEY 필요")
        for sub in result["subsidiaries"]:
            assert sub["auditor"] is not None
            assert sub["auditor"] != {}

    def test_subsidiary_auditors_json_serializable(self):
        samsung = self._samsung_cc()
        if samsung is None:
            pytest.skip("삼성전자 DB 미등록")
        result = call_tool(
            "get_subsidiary_auditors",
            {"company": samsung, "limit": 10},
        )
        # call_tool은 이미 JSON 문자열
        parsed = json.loads(result)
        # 재직렬화 — 내부 dict가 완전 JSON-safe한지
        json.dumps(parsed)


# ---------------------------------------------------------------------------
# 3. E2E: stdio subprocess 기동
# ---------------------------------------------------------------------------

async def _e2e_stdio_tools_list() -> list[str]:
    """stdio 서버를 subprocess로 기동하고 tools/list 응답의 도구 이름 목록을 반환."""
    from mcp.client.stdio import stdio_client, StdioServerParameters
    from mcp import ClientSession

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "kreports.mcp"],
        cwd=str(PROJECT_ROOT),
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.list_tools()
            return [t.name for t in result.tools]


async def _e2e_stdio_call_tool(name: str, args: dict) -> str:
    """stdio 서버에 도구 호출을 보내고 결과 TextContent.text 반환."""
    from mcp.client.stdio import stdio_client, StdioServerParameters
    from mcp import ClientSession

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "kreports.mcp"],
        cwd=str(PROJECT_ROOT),
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(name, arguments=args)
            # result.content은 list[TextContent]
            return result.content[0].text


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_health(port: int, proc: subprocess.Popen, timeout: float = 20.0) -> None:
    deadline = time.time() + timeout
    url = f"http://127.0.0.1:{port}/healthz"
    last_error: Exception | None = None
    while time.time() < deadline:
        if proc.poll() is not None:
            stderr = proc.stderr.read() if proc.stderr else ""
            raise RuntimeError(f"HTTP MCP server exited early: {stderr}")
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return
        except Exception as exc:
            last_error = exc
        time.sleep(0.25)
    raise RuntimeError(f"HTTP MCP server did not become healthy: {last_error}")


async def _e2e_http_tools_list(port: int) -> list[str]:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    async with streamable_http_client(f"http://127.0.0.1:{port}/mcp") as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.list_tools()
            return [t.name for t in result.tools]


class TestStdioE2E:
    """subprocess로 실제 MCP 서버를 띄우고 JSON-RPC 왕복."""

    def test_stdio_tools_list_returns_all(self):
        names = asyncio.run(_e2e_stdio_tools_list())
        assert len(names) == EXPECTED_TOOL_COUNT
        assert "search_company" in names
        assert "score_going_concern" in names
        assert "compare_to_industry" in names

    def test_stdio_call_search_company(self):
        text = asyncio.run(
            _e2e_stdio_call_tool("search_company", {"query": "삼성전자", "limit": 2})
        )
        parsed = json.loads(text)
        assert "count" in parsed
        assert "results" in parsed

    def test_stdio_call_score_going_concern_samsung(self):
        import kreports
        if kreports.resolve_corp_code("005930") is None:
            pytest.skip("삼성전자 DB 미등록")
        text = asyncio.run(
            _e2e_stdio_call_tool("score_going_concern", {"company": "005930"})
        )
        parsed = json.loads(text)
        assert parsed["has_data"] is True
        assert parsed["grade"] in ("안정", "주의", "경고", "위험")


class TestStreamableHttpE2E:
    """Streamable HTTP transport for remote MCP clients."""

    def test_http_app_factory_routes(self):
        from kreports.mcp.http_server import create_app

        app = create_app(path="/mcp")
        route_paths = {getattr(route, "path", None) for route in app.routes}
        assert {"/", "/healthz", "/mcp"}.issubset(route_paths)

    def test_http_bearer_token_rejects_missing_auth(self):
        from starlette.testclient import TestClient

        from kreports.mcp.http_server import create_app

        app = create_app(path="/mcp", token="secret-token")
        with TestClient(app) as client:
            response = client.post("/mcp", json={})
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"

    def test_streamable_http_tools_list(self):
        pytest.importorskip("uvicorn")
        port = _free_port()
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "kreports.mcp.http_server",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--allow-unauthenticated",
                "--log-level",
                "warning",
            ],
            cwd=str(PROJECT_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            _wait_for_health(port, proc)
            names = asyncio.run(_e2e_http_tools_list(port))
            assert len(names) == EXPECTED_TOOL_COUNT
            assert "search_company" in names
            assert "get_business_overview" in names
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=10)
