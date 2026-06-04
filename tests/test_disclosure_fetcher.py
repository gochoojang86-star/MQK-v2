import io
import zipfile

from codes.disclosure_fetcher import DARTFetcher, DisclosureItem


def make_zip_response(text: str):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("document.xml", text)

    class FakeResponse:
        content = buf.getvalue()

        def raise_for_status(self):
            return None

    return FakeResponse()


def test_get_document_text_extracts_zipped_xml(monkeypatch):
    fetcher = DARTFetcher()
    fetcher._api_key = "dart-key"
    monkeypatch.setattr(
        "codes.disclosure_fetcher.requests.get",
        lambda *args, **kwargs: make_zip_response("<doc>공급계약 금액 100억원</doc>"),
    )

    text = fetcher.get_document_text("202606040001")

    assert "공급계약 금액 100억원" in text


def test_get_document_text_uses_in_memory_cache(monkeypatch):
    fetcher = DARTFetcher()
    fetcher._api_key = "dart-key"
    calls = []

    def fake_get(*args, **kwargs):
        calls.append(kwargs)
        return make_zip_response("<doc>공급계약 금액 100억원</doc>")

    monkeypatch.setattr("codes.disclosure_fetcher.requests.get", fake_get)

    assert "공급계약" in fetcher.get_document_text("202606040001")
    assert "공급계약" in fetcher.get_document_text("202606040001")

    assert len(calls) == 1
    assert calls[0]["timeout"] == 5


def test_enrich_content_sets_disclosure_content(monkeypatch):
    fetcher = DARTFetcher()
    monkeypatch.setattr(fetcher, "get_document_text", lambda rcept_no: "본문 내용")
    item = DisclosureItem(
        ticker="005930",
        corp_name="삼성전자",
        title="단일판매 공급계약",
        date="20260604",
        rcept_no="202606040001",
        pblntf_ty="B",
    )

    enriched = fetcher.enrich_content(item)

    assert enriched.to_dict()["content"] == "본문 내용"
