"""
Telegram Approval - 매수 신호 텔레그램 알림 + 승인 시스템
require_telegram_approval=True 일 때 모든 매수는 여기를 통과한다.
"""
from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import requests
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"


@dataclass
class ApprovalRequest:
    ticker: str
    name: str
    decision: str           # BUY / SELL
    entry_price: float
    stop_loss_price: float
    quantity: int
    risk_pct: float
    confidence: int
    reason: str
    counter_argument: str


@dataclass
class ApprovalResult:
    approved: bool
    request_id: str
    responded_at: Optional[str] = None
    responder_note: str = ""


class TelegramApproval:
    """
    텔레그램 매수 승인 시스템.
    require_telegram_approval=True면 이 채널 승인 없이는 주문 불가.
    """

    def __init__(self, bot_token: str = BOT_TOKEN, chat_id: str = CHAT_ID):
        self._token = bot_token
        self._chat_id = chat_id
        self._last_update_id: int | None = None

    def request_approval(self, req: ApprovalRequest, timeout_sec: int = 300) -> ApprovalResult:
        """
        매수 신호를 텔레그램으로 전송하고 승인을 기다린다.
        timeout_sec 내에 응답 없으면 자동 거부.
        """
        # UUID 기반 고유 코드 — ticker/시간 기반 코드보다 예측 불가
        request_id = str(uuid.uuid4())
        approval_code = request_id[:8].upper()  # e.g. "A3F7C2D1"

        # 신규 요청 전 기존 누적 메시지 소진 (이전 응답 재사용 방지)
        self._flush_updates()

        msg_text = self._format_message(req, request_id)
        self._send_message(msg_text)

        # 승인 대기 (polling)
        start = time.time()
        while time.time() - start < timeout_sec:
            updates = self._get_updates()
            for update in updates:
                msg = update.get("message", {})
                # chat_id 검증: 등록된 채널의 메시지만 수락
                if not self._is_expected_chat(msg):
                    continue
                text = msg.get("text", "").strip().upper()
                # approval_code 정확 매칭 (부분 포함이 아닌 단어 단위)
                if approval_code not in text.split():
                    continue
                if "승인" in text or "YES" in text or text.endswith(" Y"):
                    return ApprovalResult(
                        approved=True,
                        request_id=request_id,
                        responded_at=datetime.now().isoformat(),
                    )
                if "거부" in text or "NO" in text or text.endswith(" N"):
                    return ApprovalResult(
                        approved=False,
                        request_id=request_id,
                        responded_at=datetime.now().isoformat(),
                        responder_note="사용자 거부",
                    )
            time.sleep(5)

        # 타임아웃 → 자동 거부
        self._send_message(f"⏰ [{req.ticker}] 타임아웃 - 자동 거부됨 (ID: {request_id[:8]})")
        return ApprovalResult(
            approved=False,
            request_id=request_id,
            responded_at=datetime.now().isoformat(),
            responder_note=f"타임아웃 ({timeout_sec}초)",
        )

    def notify(self, message: str) -> None:
        """일반 알림 전송 (승인 불필요)"""
        self._send_message(message)

    def _format_message(self, req: ApprovalRequest, request_id: str) -> str:
        risk_amount = (req.entry_price - req.stop_loss_price) * req.quantity
        return f"""🚨 **매수 승인 요청**

📌 종목: {req.name} ({req.ticker})
🎯 결정: {req.decision}
💰 진입가: {req.entry_price:,.0f}원
🛡 손절가: {req.stop_loss_price:,.0f}원
📦 수량: {req.quantity}주
⚠️ 리스크: {req.risk_pct:.3f}% ({risk_amount:,.0f}원)
📊 확신도: {req.confidence}%

✅ 근거:
{req.reason}

❌ 반론:
{req.counter_argument}

---
코드: {request_id[:8].upper()}
승인: "{request_id[:8].upper()} 승인" 또는 "{request_id[:8].upper()} Y"
거부: "{request_id[:8].upper()} 거부" 또는 "{request_id[:8].upper()} N"
"""

    def _send_message(self, text: str) -> None:
        if not self._token or not self._chat_id:
            return
        url = f"{TELEGRAM_API}/sendMessage"
        requests.post(url, json={
            "chat_id": self._chat_id,
            "text": text,
            "parse_mode": "Markdown",
        }, timeout=10)

    def _get_updates(self) -> list:
        if not self._token:
            return []
        url = f"{TELEGRAM_API}/getUpdates"
        params = {}
        if self._last_update_id is not None:
            params["offset"] = self._last_update_id + 1
        try:
            resp = requests.get(url, params=params, timeout=5)
            updates = resp.json().get("result", [])
            for update in updates:
                update_id = update.get("update_id")
                if isinstance(update_id, int):
                    self._last_update_id = max(self._last_update_id or update_id, update_id)
            return updates
        except Exception:
            return []

    def _flush_updates(self) -> None:
        """현재까지의 모든 누적 메시지를 소진해 offset을 최신으로 당긴다.
        승인 요청 전에 호출해 이전 메시지가 새 요청의 승인으로 오인되는 것을 방지.
        """
        self._get_updates()

    def _is_expected_chat(self, message: dict) -> bool:
        if not self._chat_id:
            return False
        chat = message.get("chat", {})
        return str(chat.get("id", "")) == str(self._chat_id)
