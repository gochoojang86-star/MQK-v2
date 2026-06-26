"""Kiwoom REST + WebSocket API client.

MQK v3에서는 테마/대장주 선별, 조건검색, 수급 랭킹, 호가 분석 보강용으로 사용한다.
주문/계좌 기능은 아직 포함하지 않는다.
"""
from __future__ import annotations

import ast
import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

logger = logging.getLogger("mqk_v3")


@dataclass
class KiwoomConfig:
    appkey: str = os.environ.get("KIWOOM_APP_KEY", "")
    secretkey: str = os.environ.get("KIWOOM_SECRET_KEY", "")
    base_url: str = os.environ.get("KIWOOM_BASE_URL", "https://api.kiwoom.com")
    ws_base_url: str = os.environ.get("KIWOOM_WS_URL", "wss://api.kiwoom.com:10000")


class KiwoomRateLimitError(RuntimeError):
    """키움 API 호출 제한/쿨다운 상태."""


class KiwoomApi:
    def __init__(self, config: KiwoomConfig | None = None, token_cache_path: Path | None = None):
        self._cfg = config or KiwoomConfig()
        self._token: str | None = None
        self._token_expires_at: float = 0.0
        self._last_call_at: dict[str, float] = {}
        self._cooldown_until: dict[str, float] = {}
        self._min_interval_seconds = float(os.environ.get("KIWOOM_MIN_INTERVAL_SECONDS", "0.35"))
        self._rate_limit_cooldown_seconds = float(os.environ.get("KIWOOM_RATE_LIMIT_COOLDOWN_SECONDS", "30"))
        self._token_cache_path = token_cache_path or (
            Path(__file__).parent.parent / "data" / "cache" / "kiwoom_token.json"
        )

    @property
    def available(self) -> bool:
        return bool(self._cfg.appkey and self._cfg.secretkey)

    def _token_headers(self, api_id: str) -> dict[str, str]:
        return {
            "Content-Type": "application/json;charset=UTF-8",
            "api-id": api_id,
        }

    def _api_headers(
        self,
        api_id: str,
        cont_yn: str = "N",
        next_key: str = "",
    ) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json;charset=UTF-8",
            "api-id": api_id,
            "authorization": f"Bearer {self._get_token()}",
        }
        if cont_yn:
            headers["cont-yn"] = cont_yn
        if next_key:
            headers["next-key"] = next_key
        return headers

    def _load_cached_token(self) -> str | None:
        try:
            data = json.loads(self._token_cache_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return None

        token = str(data.get("token") or "")
        expires_epoch = float(data.get("expires_epoch", 0))
        if token and time.time() < expires_epoch:
            self._token = token
            self._token_expires_at = expires_epoch
            return token
        return None

    def _save_cached_token(self, token: str, expires_dt: str) -> None:
        self._token_cache_path.parent.mkdir(parents=True, exist_ok=True)
        expires_epoch = _expires_dt_to_epoch(expires_dt)
        self._token_cache_path.write_text(
            json.dumps({"token": token, "expires_dt": expires_dt, "expires_epoch": expires_epoch}),
            encoding="utf-8",
        )
        self._token = token
        self._token_expires_at = expires_epoch

    def _get_token(self) -> str:
        if not self.available:
            raise RuntimeError("Kiwoom API credentials are not configured")
        if self._token and time.time() < self._token_expires_at:
            return self._token
        cached = self._load_cached_token()
        if cached:
            return cached

        resp = requests.post(
            f"{self._cfg.base_url}/oauth2/token",
            headers=self._token_headers("au10001"),
            json={
                "grant_type": "client_credentials",
                "appkey": self._cfg.appkey,
                "secretkey": self._cfg.secretkey,
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        token = str(data.get("token") or "")
        if not token:
            raise RuntimeError(f"Kiwoom token missing in response: {data}")
        self._save_cached_token(token, str(data.get("expires_dt") or ""))
        return token

    def _guard_rate_limit(self, api_id: str) -> None:
        cooldown_until = self._cooldown_until.get(api_id, 0.0)
        now = time.time()
        if now < cooldown_until:
            remaining = max(cooldown_until - now, 0.0)
            raise KiwoomRateLimitError(
                f"Kiwoom API cooldown active for {api_id} ({remaining:.1f}s remaining)"
            )

    def _respect_min_interval(self, api_id: str) -> None:
        last_call_at = self._last_call_at.get(api_id, 0.0)
        wait_seconds = self._min_interval_seconds - (time.time() - last_call_at)
        if wait_seconds > 0:
            time.sleep(wait_seconds)

    def _mark_call(self, api_id: str) -> None:
        self._last_call_at[api_id] = time.time()

    def _apply_rate_limit(self, api_id: str, reason: str) -> None:
        cooldown_until = time.time() + self._rate_limit_cooldown_seconds
        self._cooldown_until[api_id] = cooldown_until
        logger.warning(
            "[KiwoomApi] rate limit detected for %s — cooldown %.1fs (%s)",
            api_id,
            self._rate_limit_cooldown_seconds,
            reason,
        )

    def _looks_like_rate_limit(self, payload: Any) -> bool:
        if payload is None:
            return False
        try:
            serialized = json.dumps(payload, ensure_ascii=False).lower()
        except (TypeError, ValueError):
            serialized = str(payload).lower()
        markers = (
            "rate limit",
            "too many",
            "호출제한",
            "전송제한",
            "조회제한",
            "요청제한",
            "접근제한",
        )
        return any(marker in serialized for marker in markers)

    def _rest_request(self, api_id: str, path: str, payload: dict[str, Any], timeout: int = 10) -> dict[str, Any]:
        self._guard_rate_limit(api_id)
        self._respect_min_interval(api_id)
        resp = requests.post(
            f"{self._cfg.base_url}{path}",
            headers=self._api_headers(api_id),
            json=payload,
            timeout=timeout,
        )
        self._mark_call(api_id)
        if resp.status_code == 429:
            self._apply_rate_limit(api_id, "http_429")
            raise KiwoomRateLimitError(f"Kiwoom API rate limited: {api_id} (HTTP 429)")
        try:
            resp.raise_for_status()
        except requests.HTTPError as exc:
            body = None
            try:
                body = resp.json()
            except ValueError:
                body = resp.text
            if self._looks_like_rate_limit(body):
                self._apply_rate_limit(api_id, f"http_error:{resp.status_code}")
                raise KiwoomRateLimitError(f"Kiwoom API rate limited: {api_id}") from exc
            raise

        data = resp.json()
        if self._looks_like_rate_limit(data):
            self._apply_rate_limit(api_id, "payload_message")
            raise KiwoomRateLimitError(f"Kiwoom API rate limited: {api_id}")
        return data

    def _ws_request(self, api_id: str, payload: dict) -> dict[str, Any]:
        """키움 WebSocket 단발 요청-응답.

        프로토콜: 연결 → LOGIN(token만, Bearer 접두사 없이) → 실제 요청 → 응답 → 종료.
        서버 응답이 Python literal 포맷(단따옴표)일 수 있어 ast.literal_eval 폴백 처리.
        """
        import websocket as _ws  # websocket-client

        self._guard_rate_limit(api_id)
        self._respect_min_interval(api_id)
        token = self._get_token()
        headers = [
            f"api-id: {api_id}",
            f"authorization: Bearer {token}",
        ]
        conn = _ws.create_connection(
            f"{self._cfg.ws_base_url}/api/dostk/websocket",
            header=headers,
            timeout=15,
        )
        try:
            # WebSocket 인증: Bearer 없이 raw token만 전송
            conn.send(json.dumps({"trnm": "LOGIN", "token": token}, ensure_ascii=False))
            login_raw = conn.recv()
            login = self._parse_ws_response(login_raw)
            self._mark_call(api_id)
            if self._looks_like_rate_limit(login):
                self._apply_rate_limit(api_id, "ws_login_payload")
                raise KiwoomRateLimitError(f"Kiwoom WS rate limited during login: {api_id}")
            if login.get("return_code", -1) != 0:
                raise RuntimeError(f"키움 WS 로그인 실패: {login.get('return_msg')} (code={login.get('return_code')})")

            conn.send(json.dumps(payload, ensure_ascii=False))
            raw = conn.recv()
        finally:
            conn.close()

        parsed = self._parse_ws_response(raw)
        if self._looks_like_rate_limit(parsed):
            self._apply_rate_limit(api_id, "ws_payload")
            raise KiwoomRateLimitError(f"Kiwoom WS rate limited: {api_id}")
        return parsed

    def _parse_ws_response(self, raw: str) -> dict[str, Any]:
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return ast.literal_eval(raw)

    def search_list(self) -> dict[str, Any]:
        """ka10171 조건검색 목록조회. 영웅문4에 저장된 조건식 목록 반환."""
        return self._ws_request("ka10171", {"trnm": "CNSRLST"})

    def search_result(self, seq: str, stex_tp: str = "K") -> dict[str, Any]:
        """ka10172 조건검색 요청 일반. seq = 조건식 일련번호 (search_list로 확인).

        stex_tp: "K"=코스피, "Q"=코스닥 (KRX는 "K"로 통일).
        응답 data[*]["9001"] = 종목코드(A접두사 포함), "302" = 종목명, "12" = 등락율.
        """
        return self._ws_request("ka10172", {
            "trnm": "CNSRREQ",
            "seq": str(seq),
            "search_type": "0",
            "stex_tp": stex_tp,
            "cont_yn": "N",
            "next_key": "",
        })

    def sector_investor_flow(
        self,
        mrkt_tp: str = "0",
        amt_qty_tp: str = "0",
        stex_tp: str = "1",
    ) -> dict[str, Any]:
        """ka10051 업종별투자자순매수요청.

        mrkt_tp: "0"=전체, "1"=코스피, "2"=코스닥.
        amt_qty_tp: "0"=금액(억원), "1"=수량.
        orgn_netprps = 기관계 순매수, frgnr_netprps = 외국인 순매수.
        """
        return self._rest_request(
            "ka10051",
            "/api/dostk/sect",
            {
                "mrkt_tp": mrkt_tp,
                "amt_qty_tp": amt_qty_tp,
                "base_dt": "",
                "stex_tp": stex_tp,
            },
        )

    def bid_queue_surge(
        self,
        mrkt_tp: str = "001",
        trde_tp: str = "1",
        sort_tp: str = "1",
        tm_tp: str = "30",
        trde_qty_tp: str = "0",
        stk_cnd: str = "0",
        stex_tp: str = "1",
    ) -> dict[str, Any]:
        """ka10021 호가잔량급증요청.

        trde_tp: "1"=매수잔량급증, "2"=매도잔량급증.
        sort_tp: "1"=급증률순, "2"=급증수량순.
        tm_tp: 기준 시간(분) "30"|"60"|"120".
        sdnin_rt = 급증률(%), tot_buy_qty = 총매수잔량.
        """
        return self._rest_request(
            "ka10021",
            "/api/dostk/rkinfo",
            {
                "mrkt_tp": mrkt_tp,
                "trde_tp": trde_tp,
                "sort_tp": sort_tp,
                "tm_tp": tm_tp,
                "trde_qty_tp": trde_qty_tp,
                "stk_cnd": stk_cnd,
                "stex_tp": stex_tp,
            },
        )

    def theme_groups(
        self,
        qry_tp: str = "0",
        date_tp: str = "10",
        flu_pl_amt_tp: str = "1",
        stex_tp: str = "1",
        thema_nm: str = "",
        stk_cd: str = "",
    ) -> dict[str, Any]:
        """ka90001 테마그룹별요청."""
        return self._rest_request(
            "ka90001",
            "/api/dostk/thme",
            {
                "qry_tp": qry_tp,
                "stk_cd": stk_cd,
                "date_tp": date_tp,
                "thema_nm": thema_nm,
                "flu_pl_amt_tp": flu_pl_amt_tp,
                "stex_tp": stex_tp,
            },
        )

    def realtime_viewing_rank(self, qry_tp: str = "1") -> dict[str, Any]:
        """ka00198 실시간종목조회순위 (빅데이터 기반).

        qry_tp: "1"=1분, "2"=10분, "3"=1시간, "4"=당일누적, "5"=30초
        반환: item_inq_rank 리스트 (stk_cd, stk_nm, bigd_rank, rank_chg_sign, base_comp_chgr)
        """
        return self._rest_request("ka00198", "/api/dostk/stkinfo", {"qry_tp": qry_tp})

    def theme_components(
        self,
        thema_grp_cd: str,
        date_tp: str = "2",
        stex_tp: str = "1",
    ) -> dict[str, Any]:
        """ka90002 테마구성종목요청."""
        return self._rest_request(
            "ka90002",
            "/api/dostk/thme",
            {
                "date_tp": date_tp,
                "thema_grp_cd": thema_grp_cd,
                "stex_tp": stex_tp,
            },
        )

    def foreign_institution_top(
        self,
        mrkt_tp: str = "000",
        amt_qty_tp: str = "1",
        qry_dt_tp: str = "1",
        stex_tp: str = "1",
    ) -> dict[str, Any]:
        """ka90009 외국인기관매매상위요청. 외인/기관 순매수·순매도 상위 종목."""
        return self._rest_request(
            "ka90009",
            "/api/dostk/rkinfo",
            {
                "mrkt_tp": mrkt_tp,
                "amt_qty_tp": amt_qty_tp,
                "qry_dt_tp": qry_dt_tp,
                "stex_tp": stex_tp,
            },
        )

    def program_netbuy_top(
        self,
        trde_upper_tp: str = "2",
        amt_qty_tp: str = "1",
        mrkt_tp: str = "P00101",
        stex_tp: str = "1",
    ) -> dict[str, Any]:
        """ka90003 프로그램순매수상위50요청.

        trde_upper_tp: "1"=순매도상위, "2"=순매수상위.
        amt_qty_tp: "1"=금액, "2"=수량.
        mrkt_tp: "P00101"=코스피, "P10102"=코스닥.
        """
        return self._rest_request(
            "ka90003",
            "/api/dostk/stkinfo",
            {
                "trde_upper_tp": trde_upper_tp,
                "amt_qty_tp": amt_qty_tp,
                "mrkt_tp": mrkt_tp,
                "stex_tp": stex_tp,
            },
        )

    def foreign_continuous_rank(
        self,
        mrkt_tp: str = "000",
        trde_tp: str = "2",
        base_dt_tp: str = "1",
        stex_tp: str = "1",
    ) -> dict[str, Any]:
        """ka10035 외인연속순매매상위요청. trde_tp=2(순매수)."""
        return self._rest_request(
            "ka10035",
            "/api/dostk/rkinfo",
            {
                "mrkt_tp": mrkt_tp,
                "trde_tp": trde_tp,
                "base_dt_tp": base_dt_tp,
                "stex_tp": stex_tp,
            },
        )

    def volume_surge(self, mrkt_tp: str = "000") -> dict[str, Any]:
        """ka10023 거래량급증요청."""
        return self._rest_request("ka10023", "/api/dostk/rkinfo", {"mrkt_tp": mrkt_tp})

    def intraday_investor_rank(self, trde_tp: str) -> dict[str, Any]:
        """ka10065 장중투자자별매매상위요청. trde_tp: 1=기관, 2=외국인, 3=개인."""
        return self._rest_request("ka10065", "/api/dostk/rkinfo", {"trde_tp": trde_tp})

    def intraday_candles(self, ticker: str, tic_scope: str = "1") -> dict[str, Any]:
        """ka10080 주식분봉차트조회요청 - 당일 분봉 데이터 조회.

        Args:
            ticker: 종목코드 (예: "005930")
            tic_scope: 틱범위 (1=1분, 3=3분, 5=5분, 10=10분, 15=15분, 30=30분, 45=45분, 60=60분)

        Returns:
            {"stk_min_pole_chart_qry": [{"cntr_tm": "...", "cur_prc": ..., "trde_qty": ...}, ...]}
        """
        return self._rest_request(
            "ka10080",
            "/api/dostk/chart",
            {
                "stk_cd": ticker,
                "tic_scope": tic_scope,
                "upd_stkpc_tp": "1",
            },
        )


def _expires_dt_to_epoch(value: str) -> float:
    if not value or len(value) != 14 or not value.isdigit():
        return time.time() + 3600
    try:
        from datetime import datetime
        dt = datetime.strptime(value, "%Y%m%d%H%M%S")
        return dt.timestamp() - 60
    except ValueError:
        return time.time() + 3600
