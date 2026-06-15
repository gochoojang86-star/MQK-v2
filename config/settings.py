"""
MQK-v2 전역 설정 - 단일 기준 파일
모든 리스크 파라미터는 이 파일에서만 변경 가능
"""
from dataclasses import dataclass
from enum import Enum
import os
from pathlib import Path

from .runtime_overrides import load_runtime_overrides

BASE_DIR = Path(__file__).parent.parent


class ModelTier(str, Enum):
    REASONING = "reasoning"  # o4-mini        — 핵심 판단, 추론 필요
    STANDARD  = "standard"   # gpt-4o         — 복합 해석, 중간 복잡도
    FAST      = "fast"       # gpt-4o-mini    — 단순 분류, 반복 패턴


@dataclass(frozen=True)
class RiskConfig:
    risk_per_trade_pct: float = 0.5        # 종목당 최대 손실 0.5%
    max_daily_loss_pct: float = 2.0        # 일일 최대 손실 2%
    max_positions: int = 5                 # 최대 보유종목수
    max_theme_exposure_pct: float = 40.0   # 테마 집중도 최대 40%
    max_single_position_pct: float = 20.0  # 단일 종목 최대 20%
    stop_loss_method: str = "ATR"
    atr_multiplier: float = 1.5
    max_stop_loss_pct: float = 10.0          # 손절폭은 유연하되 진입가 대비 최대 10%
    allow_averaging_down: bool = False
    require_telegram_approval: bool = True


@dataclass(frozen=True)
class RegimeSafetyBounds:
    """RegimeAgent가 선언한 risk_guidance 값의 코드 강제 한계.

    LLM이 risk_guidance에 어떤 값을 선언해도 이 범위를 벗어나면
    clamp_risk_guidance()가 강제로 잘라낸다. v2 RiskConfig가 천장.
    """
    min_buy_confidence_threshold: float = 65.0
    max_buy_confidence_threshold: float = 95.0
    min_risk_per_trade_pct: float = 0.10
    max_risk_per_trade_pct: float = 0.50   # RiskConfig.risk_per_trade_pct(0.5)가 천장
    min_positions: int = 1
    max_positions: int = 5                  # RiskConfig.max_positions(5)가 천장
    min_trading_value_krw: int = 5_000_000_000
    min_cooldown_minutes: int = 15
    max_cooldown_minutes: int = 240
    default_cooldown_minutes: int = 60
    min_daily_triggers: int = 1
    max_daily_triggers: int = 5
    default_daily_triggers: int = 3


@dataclass(frozen=True)
class ScannerConfig:
    universe_size: int = 5000             # 전체 종목수
    candidate_count: int = 30            # Scanner 통과 종목수
    final_candidates: int = 5            # LLM 평가 최종 종목수
    min_trading_value_krw: int = 5_000_000_000  # 최소 거래대금 50억


@dataclass(frozen=True)
class ReversalConfig:
    enabled: bool = True
    rsi_threshold: float = 30.0
    min_disparity20_pct: float = -8.0
    min_disparity60_pct: float = -12.0
    max_positions: int = 2
    risk_per_trade_pct: float = 0.25
    take_profit_pct: float = 4.0
    max_holding_days: int = 4


@dataclass(frozen=True)
class LLMConfig:
    # ── OpenAI 모델 배치 (Hermes/Codex OAuth 인증) ───────────────────────────
    # REASONING: 핵심 투자 판단 (PortfolioManager, SelfImprovement)
    model_reasoning: str = "gpt-5.4"
    # STANDARD: 반복 호출되는 주력 운영 경로 (TradingAgent SCAN/INTRADAY)
    model_standard: str = "gpt-5.4-mini"
    # FAST: 단순 분류/복기/보조 분석
    model_fast: str = "gpt-5.4-mini"

    max_tokens: int = 2048
    # o-series는 temperature 미지원 — LLMClient에서 모델별 자동 처리
    temperature: float = 0.1

    # 비용 제어: Scanner 통과 후 30종목 이하에만 LLM 호출
    max_llm_calls_per_day: int = 100

    def model_for(self, tier: ModelTier) -> str:
        return {
            ModelTier.REASONING: self.model_reasoning,
            ModelTier.STANDARD:  self.model_standard,
            ModelTier.FAST:      self.model_fast,
        }[tier]


@dataclass(frozen=True)
class LogConfig:
    base_dir: Path = BASE_DIR / "logs" / "debug"
    journal_filename: str = "journal.md"


@dataclass(frozen=True)
class ExecutionConfig:
    order_dry_run: bool = os.environ.get("ORDER_DRY_RUN", "false").lower() in {
        "1", "true", "yes", "y", "on"
    }


_RUNTIME_OVERRIDES = load_runtime_overrides()
RISK       = RiskConfig(**_RUNTIME_OVERRIDES.get("RISK", {}))
REGIME_SAFETY_BOUNDS = RegimeSafetyBounds()
SCANNER    = ScannerConfig(**_RUNTIME_OVERRIDES.get("SCANNER", {}))
REVERSAL   = ReversalConfig(**_RUNTIME_OVERRIDES.get("REVERSAL", {}))
LLM_CONFIG = LLMConfig(**_RUNTIME_OVERRIDES.get("LLM_CONFIG", {}))
LOG_CONFIG = LogConfig()
EXECUTION  = ExecutionConfig()
