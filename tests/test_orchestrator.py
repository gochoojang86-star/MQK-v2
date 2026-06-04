from pathlib import Path

from agents.regime_agent import MarketStatus, Regime, RegimeJudgment
from agents.review_agent import TradeReview
from agents.self_improvement_agent import ChangeType, ImprovementProposal
from agents.theme_agent import ThemeAnalysis, ThemeItem
from codes.market_data import IndexStatus, MarketSnapshot
from codes.order_manager import OrderManager
from codes.stop_take_profit import PositionStatus
from orchestrator import MQKOrchestrator


def make_orchestrator(tmp_path: Path) -> MQKOrchestrator:
    orchestrator = MQKOrchestrator.__new__(MQKOrchestrator)
    orchestrator._today = "2026-06-03"
    orchestrator._log_dir = tmp_path
    orchestrator._sector_performance = {}
    orchestrator._last_regime = None
    orchestrator._last_theme = None
    orchestrator._current_theme = ""
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


class FakeBalanceApi:
    def get_balance(self):
        return {
            "output1": [
                {
                    "pdno": "005930",
                    "prdt_name": "삼성전자",
                    "hldg_qty": "10",
                    "pchs_avg_pric": "70000",
                    "prpr": "72000",
                    "evlu_amt": "720000",
                },
                {
                    "pdno": "000660",
                    "prdt_name": "SK하이닉스",
                    "hldg_qty": "5",
                    "pchs_avg_pric": "150000",
                    "prpr": "160000",
                    "evlu_amt": "800000",
                },
            ],
            "output2": [
                {
                    "tot_evlu_amt": "10000000",
                    "thdt_evlu_pfls_amt": "-50000",
                }
            ],
        }


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


def test_build_portfolio_state_from_kis_balance(tmp_path):
    orchestrator = make_orchestrator(tmp_path)
    orchestrator._kis_api = FakeBalanceApi()

    state = orchestrator.build_portfolio_state({
        "005930": "반도체",
        "000660": "반도체",
    })

    assert state.total_capital == 10_000_000
    assert state.daily_pnl == -50_000
    assert len(state.open_positions) == 2
    assert state.open_positions[0]["ticker"] == "005930"
    assert state.open_positions[0]["quantity"] == 10
    assert state.theme_exposure["반도체"] == 15.2


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

    # Mock journal to return trades
    class FakeJournal:
        def get_closed_trades(self, days=1):
            return [
                {
                    "ticker": "005930",
                    "entry_price": 70000.0,
                    "exit_price": 71000.0,
                    "quantity": 1,
                }
            ]

    # Mock improvement manager
    class FakeImprovementManager:
        def save(self, proposal):
            return 1

    orchestrator._journal = FakeJournal()
    orchestrator._improvement_mgr = FakeImprovementManager()

    orchestrator.run_close_review()

    assert "005930: WIN +1.00%" in orchestrator._si_agent.journal_summary
    assert "[개선 제안]" in caplog.text
    assert "Tighten filter" in caplog.text


def test_process_position_exit_executes_sell_on_stop_loss(tmp_path):
    orchestrator = make_orchestrator(tmp_path)
    orchestrator._order_manager = OrderManager(kis_api=None, dry_run=True, log_dir=tmp_path)
    orchestrator._stp_manager = __import__(
        "codes.stop_take_profit", fromlist=["StopTakeProfitManager"]
    ).StopTakeProfitManager()
    position = PositionStatus(
        ticker="005930",
        entry_price=50000,
        stop_loss_price=47000,
        quantity=10,
        atr=1000,
        highest_price=50000,
    )

    result = orchestrator.process_position_exit(position, "삼성전자", current_price=46900)

    assert result["action"] == "SELL_EXECUTED"
    assert result["signal"] == "STOP_LOSS"
    assert result["quantity"] == 10
    assert (tmp_path / "exit_signals.jsonl").exists()
