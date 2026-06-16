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

    def _parse_mcp_response(self, resp: requests.Response) -> dict:
        """streamable-http 응답을 파싱한다 (JSON 또는 SSE data: 라인)."""
        import json as _json
        if "text/event-stream" in resp.headers.get("Content-Type", ""):
            for line in resp.text.splitlines():
                if line.startswith("data:"):
                    return _json.loads(line[5:].strip())
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
            category: 도구 카테고리 (e.g. "domestic_stock")
            method:   API 메서드명 (e.g. "order_cash")
            params:   KIS API 파라미터

        Returns:
            KIS API 응답 dict

        Raises:
            RuntimeError: MCP 서버 오류 응답 시
        """
        session_id = self._init_session()
        payload = {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": f"{category}__{method}",
                "arguments": params,
            },
            "id": 1,
        }
        headers = {**self._MCP_HEADERS, "Mcp-Session-Id": session_id}
        resp = requests.post(
            f"{self.base_url}/mcp", json=payload, headers=headers, timeout=10
        )
        resp.raise_for_status()
        data = self._parse_mcp_response(resp)
        if "error" in data:
            raise RuntimeError(f"KIS MCP 오류: {data['error']}")
        return data.get("result", {})

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
