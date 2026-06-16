"""
KIS MCP Client - KIS MCP 서버 HTTP 클라이언트
OrderManager의 대안 실행 경로.
KIS MCP 서버(/home/gochoojang/kis-mcp-source)가 SSE 모드로 실행되어야 사용 가능.
서버 미실행 시 available=False, 기존 kis_api.py로 폴백.

활성화: .env에 KIS_USE_MCP=true 추가.
"""
from __future__ import annotations

import os
import socket
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

import requests

from broker.kis_api import OrderResult


class KISMCPClient:
    """KIS MCP SSE 서버와 통신하는 경량 클라이언트."""

    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = (
            base_url or os.environ.get("KIS_MCP_URL", "http://localhost:8080")
        ).rstrip("/")

    @property
    def available(self) -> bool:
        """소켓 연결로 서버 가동 여부 확인 (/health 엔드포인트 불필요)."""
        parsed = urlparse(self.base_url)
        host = parsed.hostname or "localhost"
        port = parsed.port or 80
        try:
            with socket.create_connection((host, port), timeout=2):
                return True
        except (OSError, ConnectionRefusedError):
            return False

    _MCP_HEADERS = {"Accept": "application/json, text/event-stream"}

    def _parse_mcp_response(self, resp: requests.Response, target_id: int = 1) -> dict:
        """streamable-http 응답을 파싱한다 (JSON 또는 SSE data: 라인).

        SSE에는 여러 data: 라인이 있을 수 있으므로 id==target_id인 라인을 찾는다.
        """
        import json as _json
        if "text/event-stream" in resp.headers.get("Content-Type", ""):
            last: dict | None = None
            for line in resp.text.splitlines():
                if not line.startswith("data:"):
                    continue
                try:
                    obj = _json.loads(line[5:].strip())
                except ValueError:
                    continue
                if obj.get("id") == target_id:
                    return obj
                last = obj
            if last is not None:
                return last
            raise RuntimeError("MCP SSE 응답에서 data 라인을 찾을 수 없음")
        return resp.json()

    def _init_session(self) -> str:
        """streamable-http 세션을 초기화하고 Mcp-Session-Id를 반환한다."""
        payload = {
            "jsonrpc": "2.0",
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "mqk-v3", "version": "1.0"},
            },
            "id": 0,
        }
        resp = requests.post(
            f"{self.base_url}/mcp", json=payload, headers=self._MCP_HEADERS, timeout=5
        )
        resp.raise_for_status()
        session_id = resp.headers.get("Mcp-Session-Id", "")
        if not session_id:
            raise RuntimeError("MCP 서버가 세션 ID를 반환하지 않았습니다")
        return session_id

    def call_tool(self, category: str, method: str, params: dict[str, Any]) -> dict:
        """MCP JSON-RPC 도구 호출 (streamable-http 세션 자동 관리).

        Args:
            category: 도구 이름 (e.g. "domestic_stock")
            method:   api_type 값 (e.g. "order_cash")
            params:   KIS API 파라미터

        Returns:
            파싱된 KIS API 응답 dict (rt_cd, msg1, output 등)

        Raises:
            RuntimeError: MCP 서버 오류 또는 KIS API 실패 시
        """
        import json as _json

        session_id = self._init_session()
        payload = {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": category,
                "arguments": {"api_type": method, "params": params},
            },
            "id": 1,
        }
        headers = {**self._MCP_HEADERS, "Mcp-Session-Id": session_id}
        resp = requests.post(
            f"{self.base_url}/mcp", json=payload, headers=headers, timeout=30
        )
        resp.raise_for_status()
        envelope = self._parse_mcp_response(resp, target_id=1)
        if "error" in envelope:
            raise RuntimeError(f"KIS MCP JSON-RPC 오류: {envelope['error']}")

        # FastMCP wraps the tool return in result.content[0].text (JSON string)
        content_text = envelope.get("result", {}).get("content", [{}])[0].get("text", "{}")
        tool_result: dict = _json.loads(content_text) if isinstance(content_text, str) else content_text

        if not tool_result.get("ok"):
            raise RuntimeError(f"KIS MCP 도구 오류: {tool_result.get('error', tool_result)}")

        # tool_result["data"] is the ApiExecutor result dict; its "data" key is the KIS JSON string
        api_exec = tool_result.get("data", {})
        if not api_exec.get("success"):
            raise RuntimeError(f"KIS API 실행 실패: {api_exec.get('error', api_exec)}")

        raw_output = api_exec.get("data", "{}")
        try:
            return _json.loads(raw_output) if isinstance(raw_output, str) else raw_output
        except (ValueError, TypeError) as exc:
            raise RuntimeError(f"KIS API 응답 파싱 실패: {raw_output!r}") from exc

    def _to_order_result(self, raw: dict, ticker: str, quantity: int, price: float, side: str) -> OrderResult:
        """MCP 응답 dict → OrderResult (OrderManager 호환)"""
        success = raw.get("rt_cd") == "0"
        return OrderResult(
            success=success,
            order_no=raw.get("output", {}).get("ODNO", ""),
            ticker=ticker,
            quantity=quantity,
            price=price,
            side=side,
            timestamp=datetime.now().isoformat(),
            error_msg="" if success else raw.get("msg1", "MCP 주문 실패"),
        )

    def buy_market(self, ticker: str, quantity: int, account_no: str | None = None) -> OrderResult:
        acct = account_no or os.environ.get("KIS_REAL_ACCOUNT", "")
        parts = acct.split("-")
        raw = self.call_tool("domestic_stock", "order_cash", {
            "CANO": parts[0],
            "ACNT_PRDT_CD": parts[1] if len(parts) > 1 else "01",
            "PDNO": ticker,
            "ORD_DVSN": "01",
            "ORD_QTY": str(quantity),
            "ORD_UNPR": "0",
        })
        return self._to_order_result(raw, ticker, quantity, 0, "BUY")

    def buy_limit(self, ticker: str, quantity: int, price: float, account_no: str | None = None) -> OrderResult:
        acct = account_no or os.environ.get("KIS_REAL_ACCOUNT", "")
        parts = acct.split("-")
        raw = self.call_tool("domestic_stock", "order_cash", {
            "CANO": parts[0],
            "ACNT_PRDT_CD": parts[1] if len(parts) > 1 else "01",
            "PDNO": ticker,
            "ORD_DVSN": "00",
            "ORD_QTY": str(quantity),
            "ORD_UNPR": str(int(price)),
        })
        return self._to_order_result(raw, ticker, quantity, price, "BUY")

    def sell_market(self, ticker: str, quantity: int, account_no: str | None = None) -> OrderResult:
        acct = account_no or os.environ.get("KIS_REAL_ACCOUNT", "")
        parts = acct.split("-")
        raw = self.call_tool("domestic_stock", "order_cash", {
            "CANO": parts[0],
            "ACNT_PRDT_CD": parts[1] if len(parts) > 1 else "01",
            "PDNO": ticker,
            "ORD_DVSN": "01",
            "ORD_QTY": str(quantity),
            "ORD_UNPR": "0",
            "SLL_BUY_DVSN_CD": "02",
        })
        return self._to_order_result(raw, ticker, quantity, 0, "SELL")

    def sell_limit(self, ticker: str, quantity: int, price: float, account_no: str | None = None) -> OrderResult:
        acct = account_no or os.environ.get("KIS_REAL_ACCOUNT", "")
        parts = acct.split("-")
        raw = self.call_tool("domestic_stock", "order_cash", {
            "CANO": parts[0],
            "ACNT_PRDT_CD": parts[1] if len(parts) > 1 else "01",
            "PDNO": ticker,
            "ORD_DVSN": "00",
            "ORD_QTY": str(quantity),
            "ORD_UNPR": str(int(price)),
            "SLL_BUY_DVSN_CD": "02",
        })
        return self._to_order_result(raw, ticker, quantity, price, "SELL")
