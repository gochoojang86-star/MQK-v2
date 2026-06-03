from pathlib import Path

from agents.regime_agent import MarketStatus, Regime, RegimeJudgment
from agents.review_agent import TradeReview
from agents.self_improvement_agent import ChangeType, ImprovementProposal
from agents.theme_agent import ThemeAnalysis, ThemeItem
from codes.market_data import IndexStatus, MarketSnapshot
from orchestrator import MQKOrchestrator


def make_orchestrator(tmp_path: Path) -> MQKOrchestrator:
    orchestrator = MQKOrchestrator.__new__(MQKOrchestrator)
    orchestrator._today = "2026-06-03"
    orchestrator._log_dir = tmp_path
    return orchestrator


class FakeMarketData:
    def get_index_status(self):
        return IndexStatus(
            kospi=2800.0,
            kosdaq=900.0,
            kospi_change_pct=0.7,
            kosdaq_change_pct=1.2,
        )

    def get_universe(self):
        return ["005930"]

    def get_snapshot(self, ticker):
        return MarketSnapshot(
            ticker=ticker,
            name="Samsung",
            current_price=70000.0,
            change_pct=3.0,
            volume=1_000_000,
            trading_value=70_000_000_000,
            foreign_net=1_000_000_000,
            institution_net=1_000_000_000,
        )

    def get_ohlcv(self, ticker):
        return []


class FakeRegimeAgent:
    def judge(self, market_context):
        return RegimeJudgment(
            status=MarketStatus.GREEN,
            regime=Regime.THEME_MARKET,
            confidence=80,
            reason="market is supportive",
            risk_notes=["watch volatility"],
        )


class FakeThemeAgent:
    def analyze(self, market_context):
        return ThemeAnalysis(
            top_themes=[
                ThemeItem(
                    theme="semiconductor",
                    strength=90,
                    leader_candidates=["Samsung"],
                    reason="strong flow",
                    risk="crowded",
                )
            ]
        )


class FakeTechnical:
    def analyze(self, ticker, bars):
        raise AssertionError("empty bars should skip technical analysis")


class FakeFlow:
    def analyze(self, ticker, records):
        return None


class FakeNewsFetcher:
    def search(self, query, display=5):
        return []

    def get_news(self, ticker="000000", limit=20):
        return []


class FakeReviewAgent:
    def analyze(self, trade):
        return TradeReview(
            ticker=trade["ticker"],
            trade_date="2026-06-03",
            result="WIN",
            pnl=1000.0,
            pnl_pct=1.0,
            markdown="good execution\nlesson: follow volume",
        )


class FakeSelfImprovementAgent:
    def __init__(self):
        self.journal_summary = None

    def suggest(self, trades, journal_summary):
        self.journal_summary = journal_summary
        return [
            ImprovementProposal(
                title="Tighten filter",
                hypothesis="avoid weak volume",
                change_type=ChangeType.FILTER,
                expected_effect="fewer weak entries",
                risk="may miss reversals",
                requires_backtest=True,
            )
        ]


def test_run_premarket_serializes_regime_status_and_risk_notes(tmp_path):
    orchestrator = make_orchestrator(tmp_path)
    orchestrator._market_data = FakeMarketData()
    orchestrator._regime_agent = FakeRegimeAgent()
    orchestrator._kis_news = FakeNewsFetcher()
    orchestrator._naver_news = FakeNewsFetcher()

    status = orchestrator.run_premarket()

    assert status["status"] == "GREEN"
    assert status["risk_notes"] == ["watch volatility"]
    assert (tmp_path / "market_status.json").exists()


def test_run_scan_uses_best_theme_for_theme_match(tmp_path):
    orchestrator = make_orchestrator(tmp_path)
    orchestrator._market_data = FakeMarketData()
    orchestrator._technical = FakeTechnical()
    orchestrator._flow = FakeFlow()
    orchestrator._scanner = __import__("codes.scanner", fromlist=["Scanner"]).Scanner()
    orchestrator._theme_agent = FakeThemeAgent()
    orchestrator._naver_news = FakeNewsFetcher()

    candidates = orchestrator.run_scan({"status": "GREEN"})

    assert len(candidates) == 1
    assert candidates[0]["theme"] == "semiconductor"
    assert "theme_match" in candidates[0]
    assert (tmp_path / "candidate_scores.jsonl").exists()


def test_run_close_review_summarizes_markdown_and_logs_expected_effect(tmp_path, caplog):
    caplog.set_level("INFO", logger="mqk_v2")
    orchestrator = make_orchestrator(tmp_path)
    orchestrator._review_agent = FakeReviewAgent()
    orchestrator._si_agent = FakeSelfImprovementAgent()

    orchestrator.run_close_review([
        {
            "ticker": "005930",
            "entry_price": 70000.0,
            "exit_price": 71000.0,
            "quantity": 1,
        }
    ])

    assert "lesson: follow volume" in orchestrator._si_agent.journal_summary
    assert "fewer weak entries" in caplog.text
