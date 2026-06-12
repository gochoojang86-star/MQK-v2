from broker.telegram import ApprovalRequest, TelegramApproval


def make_request():
    return ApprovalRequest(
        ticker="005930",
        name="삼성전자",
        decision="BUY",
        entry_price=70000.0,
        stop_loss_price=67000.0,
        quantity=10,
        risk_pct=0.3,
        confidence=80,
        reason="테스트",
        counter_argument="반론",
    )


class FakeTelegramApproval(TelegramApproval):
    def __init__(self, updates):
        super().__init__(bot_token="token", chat_id="1234")
        self._updates = list(updates)
        self.sent = []

    def _send_message(self, text: str, reply_markup=None, chat_id=None, parse_mode=None) -> None:
        self.sent.append({"text": text, "reply_markup": reply_markup, "chat_id": chat_id})

    def _get_updates(self) -> list:
        if not self._updates:
            return []
        return self._updates.pop(0)

    def _answer_callback(self, callback_query_id: str) -> None:
        return None


class FakeNotifyTelegramApproval(TelegramApproval):
    def __init__(self, notify_chat_ids):
        super().__init__(bot_token="token", chat_id="primary", notify_chat_ids=notify_chat_ids)
        self.sent = []

    def _send_message(self, text: str, reply_markup=None, chat_id=None, parse_mode=None) -> None:
        self.sent.append({"text": text, "chat_id": chat_id, "reply_markup": reply_markup})


def test_approves_matching_callback_button(monkeypatch):
    monkeypatch.setattr("broker.telegram.uuid.uuid4", lambda: "abcd1234-0000-0000-0000-000000000000")
    times = iter([0, 0, 0, 2])
    monkeypatch.setattr("broker.telegram.time.time", lambda: next(times))
    monkeypatch.setattr("broker.telegram.time.sleep", lambda _: None)
    approval = FakeTelegramApproval([
        [],
        [
            {
                "update_id": 1,
                "callback_query": {
                    "id": "callback-1",
                    "message": {"chat": {"id": 1234}},
                    "data": "approve:wrong-id",
                },
            }
        ],
        [
            {
                "update_id": 2,
                "callback_query": {
                    "id": "callback-2",
                    "message": {"chat": {"id": 1234}},
                    "data": "approve:abcd1234-0000-0000-0000-000000000000",
                },
            }
        ],
    ])

    result = approval.request_approval(make_request(), timeout_sec=1)

    assert result.approved is True
    assert result.request_id == "abcd1234-0000-0000-0000-000000000000"
    keyboard = approval.sent[0]["reply_markup"]["inline_keyboard"][0]
    assert keyboard[0]["callback_data"] == "approve:abcd1234-0000-0000-0000-000000000000"
    assert keyboard[1]["callback_data"] == "reject:abcd1234-0000-0000-0000-000000000000"


def test_rejects_approval_from_unexpected_chat(monkeypatch):
    monkeypatch.setattr("broker.telegram.uuid.uuid4", lambda: "abcd1234-0000-0000-0000-000000000000")
    times = iter([0, 0, 0, 2])
    monkeypatch.setattr("broker.telegram.time.time", lambda: next(times))
    monkeypatch.setattr("broker.telegram.time.sleep", lambda _: None)
    approval = FakeTelegramApproval([
        [],
        [
            {
                "update_id": 1,
                "callback_query": {
                    "id": "callback-1",
                    "message": {"chat": {"id": 9999}},
                    "data": "approve:abcd1234-0000-0000-0000-000000000000",
                },
            }
        ],
    ])

    result = approval.request_approval(make_request(), timeout_sec=1)

    assert result.approved is False


def test_rejects_matching_callback_button(monkeypatch):
    monkeypatch.setattr("broker.telegram.uuid.uuid4", lambda: "abcd1234-0000-0000-0000-000000000000")
    times = iter([0, 0, 0, 2])
    monkeypatch.setattr("broker.telegram.time.time", lambda: next(times))
    monkeypatch.setattr("broker.telegram.time.sleep", lambda _: None)
    approval = FakeTelegramApproval([
        [],
        [
            {
                "update_id": 1,
                "callback_query": {
                    "id": "callback-1",
                    "message": {"chat": {"id": 1234}},
                    "data": "reject:abcd1234-0000-0000-0000-000000000000",
                },
            }
        ],
    ])

    result = approval.request_approval(make_request(), timeout_sec=1)

    assert result.approved is False
    assert result.responder_note == "사용자 거부"


def test_rejects_immediately_when_telegram_is_not_configured():
    approval = TelegramApproval(bot_token="", chat_id="")

    result = approval.request_approval(make_request(), timeout_sec=300)

    assert result.approved is False
    assert "미설정" in result.responder_note


def test_notify_sends_to_each_configured_notify_chat_once():
    approval = FakeNotifyTelegramApproval("primary, secondary, primary")

    approval.notify("hello")

    assert [item["chat_id"] for item in approval.sent] == ["primary", "secondary"]


def test_notify_falls_back_to_approval_chat_id_when_notify_chat_ids_empty():
    approval = FakeNotifyTelegramApproval("")

    approval.notify("hello")

    assert [item["chat_id"] for item in approval.sent] == ["primary"]
