"""
MQK-v2 전역 설정 - 단일 기준 파일
모든 리스크 파라미터는 이 파일에서만 변경 가능
"""
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

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
    allow_averaging_down: bool = False
    require_telegram_approval: bool = True


@dataclass(frozen=True)
class ScannerConfig:
    universe_size: int = 5000             # 전체 종목수
    candidate_count: int = 30            # Scanner 통과 종목수
    final_candidates: int = 5            # LLM 평가 최종 종목수
    min_trading_value_krw: int = 5_000_000_000  # 최소 거래대금 50억


@dataclass(frozen=True)
class LLMConfig:
    # ── OpenAI 모델 배치 (현재 사용) ─────────────────────────────────────────
    # REASONING: 핵심 투자 판단 (PortfolioManager, SelfImprovement)
    model_reasoning: str = "o4-mini"
    # STANDARD: 복합 해석 (Regime, Theme, Review)
    model_standard: str = "gpt-4o"
    # FAST: 단순 분류 패턴 (News, Disclosure)
    model_fast: str = "gpt-4o-mini"

    # ── Anthropic 단일 모델 (원복용 주석) ────────────────────────────────────
    # model: str = "claude-opus-4-8"

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


RISK       = RiskConfig()
SCANNER    = ScannerConfig()
LLM_CONFIG = LLMConfig()
LOG_CONFIG = LogConfig()
