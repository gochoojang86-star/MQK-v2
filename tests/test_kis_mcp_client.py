import json
from unittest.mock import patch, MagicMock
from broker.kis_mcp_client import KISMCPClient


def _make_json_resp(body: dict, session_id: str = "") -> MagicMock:
    """Content-Type: application/json 응답 모의객체."""
    m = MagicMock()
    m.headers = {"Content-Type": "application/json", "Mcp-Session-Id": session_id}
    m.json.return_value = body
    m.raise_for_status = MagicMock()
    return m


def _make_sse_resp(body: dict, session_id: str = "") -> MagicMock:
    """Content-Type: text/event-stream 응답 모의객체."""
    m = MagicMock()
    m.headers = {"Content-Type": "text/event-stream", "Mcp-Session-Id": session_id}
    m.text = f"event: message\ndata: {json.dumps(body)}\n\n"
    m.raise_for_status = MagicMock()
    return m


def _tool_envelope(kis_dict: dict) -> dict:
    """KIS 응답 dict를 FastMCP content envelope 구조로 감싼다."""
    tool_result = {"ok": True, "data": {"success": True, "data": json.dumps(kis_dict)}}
    return {"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": json.dumps(tool_result)}], "isError": False}}


def test_client_init():
    client = KISMCPClient(base_url="http://localhost:8080")
    assert client.base_url == "http://localhost:8080"


def test_client_strips_trailing_slash():
    client = KISMCPClient(base_url="http://localhost:8080/")
    assert client.base_url == "http://localhost:8080"


def test_call_tool_returns_kis_dict():
    """call_tool이 파싱된 KIS 응답 dict(rt_cd 등)를 반환한다."""
    client = KISMCPClient(base_url="http://localhost:8080")
    init_resp = _make_json_resp({"jsonrpc": "2.0", "id": 0, "result": {}}, session_id="sess-1")
    tool_resp = _make_json_resp(_tool_envelope({"rt_cd": "0", "output": {"ODNO": "0000123"}}))
    with patch("requests.post", side_effect=[init_resp, tool_resp]):
        result = client.call_tool("domestic_stock", "inquire_price", {"FID_INPUT_ISCD": "005930"})
    assert result["rt_cd"] == "0"
    assert result["output"]["ODNO"] == "0000123"


def test_call_tool_sse_response():
    """SSE 응답에서도 KIS dict를 올바르게 추출한다."""
    client = KISMCPClient(base_url="http://localhost:8080")
    init_resp = _make_json_resp({"jsonrpc": "2.0", "id": 0, "result": {}}, session_id="sess-2")
    tool_resp = _make_sse_resp(_tool_envelope({"rt_cd": "0", "msg1": "정상처리"}))
    with patch("requests.post", side_effect=[init_resp, tool_resp]):
        result = client.call_tool("domestic_stock", "order_cash", {"PDNO": "005930"})
    assert result["rt_cd"] == "0"


def test_call_tool_raises_on_jsonrpc_error():
    """JSON-RPC 에러 응답 시 RuntimeError를 발생시킨다."""
    client = KISMCPClient(base_url="http://localhost:8080")
    init_resp = _make_json_resp({"jsonrpc": "2.0", "id": 0, "result": {}}, session_id="sess-3")
    err_resp = _make_json_resp({"jsonrpc": "2.0", "id": 1, "error": {"code": -32600, "message": "Invalid Request"}})
    with patch("requests.post", side_effect=[init_resp, err_resp]):
        try:
            client.call_tool("domestic_stock", "bad_method", {})
            assert False, "RuntimeError expected"
        except RuntimeError as e:
            assert "JSON-RPC 오류" in str(e)


def test_call_tool_raises_on_tool_error():
    """MCP 도구가 ok=False를 반환하면 RuntimeError를 발생시킨다."""
    client = KISMCPClient(base_url="http://localhost:8080")
    init_resp = _make_json_resp({"jsonrpc": "2.0", "id": 0, "result": {}}, session_id="sess-4")
    body = {"ok": False, "error": "지원하지 않는 API 타입: bad_method"}
    envelope = {"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": json.dumps(body)}], "isError": True}}
    tool_resp = _make_json_resp(envelope)
    with patch("requests.post", side_effect=[init_resp, tool_resp]):
        try:
            client.call_tool("domestic_stock", "bad_method", {})
            assert False, "RuntimeError expected"
        except RuntimeError as e:
            assert "MCP 도구 오류" in str(e)


def test_call_tool_raises_on_api_exec_failure():
    """KIS 서브프로세스 실패(token 만료 등) 시 RuntimeError를 발생시킨다."""
    client = KISMCPClient(base_url="http://localhost:8080")
    init_resp = _make_json_resp({"jsonrpc": "2.0", "id": 0, "result": {}}, session_id="sess-5")
    tool_result = {"ok": True, "data": {"success": False, "error": "Get Authentification token fail!"}}
    envelope = {"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": json.dumps(tool_result)}], "isError": False}}
    tool_resp = _make_json_resp(envelope)
    with patch("requests.post", side_effect=[init_resp, tool_resp]):
        try:
            client.call_tool("domestic_stock", "order_cash", {})
            assert False, "RuntimeError expected"
        except RuntimeError as e:
            assert "실행 실패" in str(e)


def test_available_false_when_no_server():
    client = KISMCPClient(base_url="http://localhost:19999")
    assert client.available is False


def test_to_order_result_success():
    """_to_order_result: rt_cd=0이면 success=True, order_no 추출."""
    client = KISMCPClient(base_url="http://localhost:8080")
    raw = {"rt_cd": "0", "msg1": "정상처리 되었습니다", "output": {"ODNO": "0000456"}}
    result = client._to_order_result(raw, "005930", 1, 75000, "BUY")
    assert result.success is True
    assert result.order_no == "0000456"
    assert result.error_msg == ""


def test_to_order_result_failure():
    """_to_order_result: rt_cd!=0이면 success=False, error_msg=msg1."""
    client = KISMCPClient(base_url="http://localhost:8080")
    raw = {"rt_cd": "1", "msg1": "잔고 부족", "output": {}}
    result = client._to_order_result(raw, "005930", 1, 75000, "BUY")
    assert result.success is False
    assert result.error_msg == "잔고 부족"
