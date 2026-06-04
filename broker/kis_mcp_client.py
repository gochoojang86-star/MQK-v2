"""
KIS MCP Client - KIS MCP 서버 HTTP 클라이언트
OrderManager의 대안 실행 경로.
KIS MCP 서버(/home/gochoojang/kis-mcp-source)가 SSE 모드로 실행되어야 사용 가능.
서버 미실행 시 available=False, 기존 kis_api.py로 폴백.
"""
from __future__ import annotations

import os
from typing import Any

import requests


class KISMCPClient:
    """KIS MCP SSE 서버와 통신하는 경량 클라이언트."""

    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = (
            base_url or os.environ.get("KIS_MCP_URL", "http://localhost:8080")
        ).rstrip("/")

    @property
    def available(self) -> bool:
        try:
            resp = requests.get(f"{self.base_url}/health", timeout=2)
            return resp.status_code == 200
        except Exception:
            return False

    def call_tool(self, category: str, method: str, params: dict[str, Any]) -> dict:
        """MCP JSON-RPC 도구 호출.

        Args:
            category: 도구 카테고리 (e.g. "domestic_stock")
            method:   API 메서드명 (e.g. "inquire_price")
            params:   KIS API 파라미터

        Returns:
            KIS API 응답 dict

        Raises:
            RuntimeError: MCP 서버 오류 응답 시
        """
        payload = {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": f"{category}__{method}",
                "arguments": params,
            },
            "id": 1,
        }
        resp = requests.post(f"{self.base_url}/mcp", json=payload, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if "error" in data:
            raise RuntimeError(f"KIS MCP 오류: {data['error']}")
        return data.get("result", {})

    def buy_market(self, ticker: str, quantity: int, account_no: str) -> dict:
        parts = account_no.split("-")
        return self.call_tool("domestic_stock", "order_cash", {
            "CANO": parts[0],
            "ACNT_PRDT_CD": parts[1] if len(parts) > 1 else "01",
            "PDNO": ticker,
            "ORD_DVSN": "01",
            "ORD_QTY": str(quantity),
            "ORD_UNPR": "0",
        })

    def sell_market(self, ticker: str, quantity: int, account_no: str) -> dict:
        parts = account_no.split("-")
        return self.call_tool("domestic_stock", "order_cash", {
            "CANO": parts[0],
            "ACNT_PRDT_CD": parts[1] if len(parts) > 1 else "01",
            "PDNO": ticker,
            "ORD_DVSN": "01",
            "ORD_QTY": str(quantity),
            "ORD_UNPR": "0",
            "SLL_BUY_DVSN_CD": "02",
        })
