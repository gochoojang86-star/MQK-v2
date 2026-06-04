from pathlib import Path

from agents.regime_agent import MarketStatus, Regime, RegimeJudgment
from agents.portfolio_manager import Decision, PortfolioDecision
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
    orchestrator._kis_api = None
    orchestrator._atr_cache = {}
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
                    theme_stage="초입",
                    entry_verdict="진입가능",
                    laggard_stocks=["WeakSemi"],
                    junk_warning=False,
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


class FakeHoldPM:
    def decide(self, ticker, context):
        return PortfolioDecision(
            ticker=ticker,
            decision=Decision.HOLD,
            confidence=75,
            reason="leader still strong",
            counter_argument="could reverse",
        )


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


class FakeFlowHistoryApi:
    def get_investor_flow_history(self, ticker, days=3):
        return [
            {"date": "20260601", "ticker": ticker, "foreign_net": 1, "institution_net": 2, "program_net": 3, "trading_value": 10},
            {"date": "20260602", "ticker": ticker, "foreign_net": 4, "institution_net": 5, "program_net": 6, "trading_value": 20},
            {"date": "20260603", "ticker": ticker, "foreign_net": 7, "institution_net": 8, "program_net": 9, "trading_value": 30},
        ]


class FakeSeedApi:
    def get_theme_seed_tickers(self, limit=60):
        return ["005930", "000660"]


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
    assert candidates[0]["trading_value"] == 70_000_000_000
    assert candidates[0]["is_theme_leader"] is True
    assert orchestrator._candidate_context["005930"]["score"] == candidates[0]["score"]
    assert (tmp_path / "candidate_scores.jsonl").exists()


def test_get_scan_seed_tickers_prefers_kis_ranking_api(tmp_path):
    orchestrator = make_orchestrator(tmp_path)
    orchestrator._kis_api = FakeSeedApi()

    class ShouldNotUseUniverse:
        def get_universe(self):
            raise AssertionError("ranking seed should be preferred")

    orchestrator._market_data = ShouldNotUseUniverse()

    assert orchestrator._get_scan_seed_tickers() == ["005930", "000660"]


def test_build_reaction_context_uses_snapshot_and_average_trading_value(tmp_path):
    orchestrator = make_orchestrator(tmp_path)
    snapshot = MarketSnapshot(
        ticker="005930",
        name="삼성전자",
        current_price=70000,
        change_pct=2.5,
        volume=100,
        trading_value=30_000_000_000,
        foreign_net=0,
        institution_net=0,
    )
    bars = [
        __import__("codes.market_data", fromlist=["OHLCVBar"]).OHLCVBar(
            date=f"202606{i:02d}",
            open=1,
            high=1,
            low=1,
            close=1,
            volume=1,
            trading_value=10_000_000_000,
        )
        for i in range(1, 6)
    ]

    reaction = orchestrator._build_reaction_context(snapshot, bars)

    assert reaction["price_reaction_pct"] == 2.5
    assert reaction["current_trading_value"] == 30_000_000_000
    assert reaction["trading_value_ratio_20d"] == 3.0


def test_get_flow_records_prefers_three_day_history(tmp_path):
    orchestrator = make_orchestrator(tmp_path)
    orchestrator._kis_api = FakeFlowHistoryApi()
    snapshot = MarketSnapshot(
        ticker="005930",
        name="Samsung",
        current_price=70000,
        change_pct=1,
        volume=1,
        trading_value=100,
        foreign_net=100,
        institution_net=100,
        program_net=100,
    )

    records = orchestrator._get_flow_records("005930", snapshot)

    assert len(records) == 3
    assert records[-1].foreign_net == 7
    assert records[-1].program_net == 9


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


def test_profit_target_can_be_extended_with_protected_stop(tmp_path):
    orchestrator = make_orchestrator(tmp_path)
    orchestrator._stp_manager = __import__(
        "codes.stop_take_profit", fromlist=["StopTakeProfitManager"]
    ).StopTakeProfitManager()
    orchestrator._pm_agent = FakeHoldPM()

    class FakeJournal:
        def __init__(self):
            self.update = None

        def update_position_management(self, **kwargs):
            self.update = kwargs

    orchestrator._journal = FakeJournal()
    position = PositionStatus(
        ticker="005930",
        entry_price=50000,
        stop_loss_price=47000,
        quantity=10,
        atr=1000,
        highest_price=55000,
    )

    result = orchestrator.process_position_exit(position, "삼성전자", current_price=55000)

    assert result["action"] == "HOLD_EXTENDED"
    assert result["signal"] == "TARGET_1"
    assert result["protected_stop"] == 50000
    assert orchestrator._journal.update["stop_loss_price"] == 50000
    assert orchestrator._journal.update["target1_hit"] is True


def test_hold_exit_check_syncs_highest_price_and_trailing_state(tmp_path):
    orchestrator = make_orchestrator(tmp_path)
    orchestrator._stp_manager = __import__(
        "codes.stop_take_profit", fromlist=["StopTakeProfitManager"]
    ).StopTakeProfitManager()

    class FakeJournal:
        def __init__(self):
            self.update = None

        def update_position_management(self, **kwargs):
            self.update = kwargs

    orchestrator._journal = FakeJournal()
    position = PositionStatus(
        ticker="005930",
        entry_price=50000,
        stop_loss_price=47000,
        quantity=10,
        atr=1000,
        highest_price=50000,
    )

    result = orchestrator.process_position_exit(position, "삼성전자", current_price=53000)

    assert result["action"] == "HOLD"
    assert orchestrator._journal.update["highest_price"] == 53000
    assert orchestrator._journal.update["trailing_active"] is False


def test_run_position_exit_check_reads_open_positions(tmp_path):
    orchestrator = make_orchestrator(tmp_path)
    orchestrator._order_manager = OrderManager(kis_api=None, dry_run=True, log_dir=tmp_path)
    orchestrator._stp_manager = __import__(
        "codes.stop_take_profit", fromlist=["StopTakeProfitManager"]
    ).StopTakeProfitManager()

    class FakeJournal:
        def get_open_positions(self):
            return [
                {
                    "ticker": "005930",
                    "name": "삼성전자",
                    "entry_price": 50000,
                    "stop_loss_price": 47000,
                    "quantity": 10,
                }
            ]

    class ExitMarketData:
        def get_snapshot(self, ticker):
            return MarketSnapshot(
                ticker=ticker,
                name="삼성전자",
                current_price=46900,
                change_pct=-2,
                volume=1,
                trading_value=1,
                foreign_net=0,
                institution_net=0,
            )

        def get_ohlcv(self, ticker):
            return []

    orchestrator._journal = FakeJournal()
    orchestrator._market_data = ExitMarketData()
    orchestrator._technical = __import__(
        "codes.technical", fromlist=["TechnicalAnalysis"]
    ).TechnicalAnalysis()

    results = orchestrator.run_position_exit_check()

    assert results[0]["action"] == "SELL_EXECUTED"
    assert results[0]["signal"] == "STOP_LOSS"
