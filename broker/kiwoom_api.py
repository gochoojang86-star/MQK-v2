"""Kiwoom REST API client - theme/group lookup focused.

MQK v3에서는 SCAN 단계의 테마 확산/대장주 선별 보강용으로 사용한다.
주문/계좌 기능은 아직 포함하지 않는다.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests


@dataclass
class KiwoomConfig:
    appkey: str = os.environ.get("KIWOOM_APP_KEY", "")
    secretkey: str = os.environ.get("KIWOOM_SECRET_KEY", "")
    base_url: str = os.environ.get("KIWOOM_BASE_URL", "https://api.kiwoom.com")


class KiwoomApi:
    def __init__(self, config: KiwoomConfig | None = None, token_cache_path: Path | None = None):
        self._cfg = config or KiwoomConfig()
        self._token: str | None = None
        self._token_expires_at: float = 0.0
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
        resp = requests.post(
            f"{self._cfg.base_url}/api/dostk/thme",
            headers=self._api_headers("ka90001"),
            json={
                "qry_tp": qry_tp,
                "stk_cd": stk_cd,
                "date_tp": date_tp,
                "thema_nm": thema_nm,
                "flu_pl_amt_tp": flu_pl_amt_tp,
                "stex_tp": stex_tp,
            },
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()

    def theme_components(
        self,
        thema_grp_cd: str,
        date_tp: str = "2",
        stex_tp: str = "1",
    ) -> dict[str, Any]:
        """ka90002 테마구성종목요청."""
        resp = requests.post(
            f"{self._cfg.base_url}/api/dostk/thme",
            headers=self._api_headers("ka90002"),
            json={
                "date_tp": date_tp,
                "thema_grp_cd": thema_grp_cd,
                "stex_tp": stex_tp,
            },
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()


def _expires_dt_to_epoch(value: str) -> float:
    if not value or len(value) != 14 or not value.isdigit():
        return time.time() + 3600
    try:
        from datetime import datetime
        dt = datetime.strptime(value, "%Y%m%d%H%M%S")
        return dt.timestamp() - 60
    except ValueError:
        return time.time() + 3600
