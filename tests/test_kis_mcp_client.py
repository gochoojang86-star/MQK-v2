from unittest.mock import patch, MagicMock
from broker.kis_mcp_client import KISMCPClient


def test_client_init():
    client = KISMCPClient(base_url="http://localhost:8080")
    assert client.base_url == "http://localhost:8080"


def test_client_strips_trailing_slash():
    client = KISMCPClient(base_url="http://localhost:8080/")
    assert client.base_url == "http://localhost:8080"


def test_call_tool_returns_result():
    client = KISMCPClient(base_url="http://localhost:8080")
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"result": {"rt_cd": "0", "output": []}}
    mock_resp.raise_for_status = MagicMock()
    with patch("requests.post", return_value=mock_resp):
        result = client.call_tool(
            "domestic_stock", "inquire_price",
            {"FID_INPUT_ISCD": "005930"}
        )
    assert result["rt_cd"] == "0"


def test_available_false_when_no_server():
    client = KISMCPClient(base_url="http://localhost:19999")
    assert client.available is False


def test_call_tool_raises_on_error():
    client = KISMCPClient(base_url="http://localhost:8080")
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"error": {"code": -32600, "message": "Invalid Request"}}
    mock_resp.raise_for_status = MagicMock()
    with patch("requests.post", return_value=mock_resp):
        try:
            client.call_tool("domestic_stock", "bad_method", {})
            assert False, "RuntimeError expected"
        except RuntimeError as e:
            assert "KIS MCP 오류" in str(e)
