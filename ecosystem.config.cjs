// ecosystem.config.cjs
module.exports = {
  apps: [
    {
      name: "mqk-holiday-check",
      script: "/mnt/c/Users/gocho/MQK-v2/.venv/bin/python",
      args: "/mnt/c/Users/gocho/MQK-v2/run_schedule.py",
      cwd: "/mnt/c/Users/gocho/MQK-v2",
      env: { MQK_PHASE: "holiday_check" },
      cron_restart: "30 0 * * *",   // KST 00:30
      autorestart: false,
    },
    // ── v2 트레이딩 앱 4개 — 2026-06-12 v3 전환으로 비활성화 (사용자 결정) ──
    // 롤백 시 아래 주석을 해제하고 v3 앱들을 중지하세요.
    // {
    //   name: "mqk-premarket",
    //   script: "/mnt/c/Users/gocho/MQK-v2/.venv/bin/python",
    //   args: "/mnt/c/Users/gocho/MQK-v2/run_schedule.py",
    //   cwd: "/mnt/c/Users/gocho/MQK-v2",
    //   env: { MQK_PHASE: "premarket" },
    //   cron_restart: "0 8 * * 1-5",
    //   autorestart: false,
    // },
    // {
    //   name: "mqk-scan",
    //   script: "/mnt/c/Users/gocho/MQK-v2/.venv/bin/python",
    //   args: "/mnt/c/Users/gocho/MQK-v2/run_schedule.py",
    //   cwd: "/mnt/c/Users/gocho/MQK-v2",
    //   env: { MQK_PHASE: "scan" },
    //   cron_restart: "30 8 * * 1-5",
    //   autorestart: false,
    // },
    // {
    //   name: "mqk-intraday",
    //   script: "/mnt/c/Users/gocho/MQK-v2/.venv/bin/python",
    //   args: "/mnt/c/Users/gocho/MQK-v2/run_schedule.py",
    //   cwd: "/mnt/c/Users/gocho/MQK-v2",
    //   env: { MQK_PHASE: "intraday" },
    //   cron_restart: "*/5 9-15 * * 1-5",  // 평일 09:00~15:55 매 5분
    //   autorestart: false,
    // },
    // {
    //   name: "mqk-close",
    //   script: "/mnt/c/Users/gocho/MQK-v2/.venv/bin/python",
    //   args: "/mnt/c/Users/gocho/MQK-v2/run_schedule.py",
    //   cwd: "/mnt/c/Users/gocho/MQK-v2",
    //   env: { MQK_PHASE: "close" },
    //   cron_restart: "30 15 * * 1-5",
    //   autorestart: false,
    // },
    // ⚠️ 경고: 아래 v3 앱들과 위의 v2 트레이딩 앱(mqk-premarket/scan/intraday/close)을
    // 동시에 실행하면 같은 계좌에서 두 개의 자율 트레이더가 동시에 주문합니다.
    // v3로 전환 시 반드시 v2 4개 앱을 `pm2 stop`/`pm2 delete` 하거나 cron_restart를
    // 주석 처리한 후 v3를 시작하세요. (운영 전환은 수동 결정 사항)
    {
      name: "mqk-v3-premarket-early",
      script: "/mnt/c/Users/gocho/MQK-v2/.venv/bin/python",
      args: "/mnt/c/Users/gocho/MQK-v2/run_schedule_v3.py",
      cwd: "/mnt/c/Users/gocho/MQK-v2",
      env: { MQK_PHASE: "premarket_sejuk" },
      // KST 08:45 — 장전 상한가 세력 검증 + 진입 후보 watchlist 선주입.
      cron_restart: "45 8 * * 1-5",
      autorestart: false,
    },
    {
      name: "mqk-v3-premarket",
      script: "/mnt/c/Users/gocho/MQK-v2/.venv/bin/python",
      args: "/mnt/c/Users/gocho/MQK-v2/run_schedule_v3.py",
      cwd: "/mnt/c/Users/gocho/MQK-v2",
      env: { MQK_PHASE: "premarket" },
      // KST 09:03 / 11:03 / 13:03 — 장중 첫번째 레짐 평가 포함 3회.
      // :03인 이유: intraday */10 틱(:00)과 flock 충돌을 피하기 위함.
      cron_restart: "3 9,11,13 * * 1-5",
      autorestart: false,
    },
    {
      name: "mqk-v3-scan",
      script: "/mnt/c/Users/gocho/MQK-v2/.venv/bin/python",
      args: "/mnt/c/Users/gocho/MQK-v2/run_schedule_v3.py",
      cwd: "/mnt/c/Users/gocho/MQK-v2",
      env: { MQK_PHASE: "scan" },
      // KST 09:17/11:17/13:17 — 각 레짐 평가(09:03/11:03/13:03) 직후 14분 내 스캔.
      // 13:17 추가: 13:03 레짐 재평가와 바로 연결 (기존 74분 갭 해소).
      // :17인 이유: intraday */5 틱(:15, :20)과 flock 충돌을 피하기 위함.
      cron_restart: "17 9,11,13 * * 1-5",
      autorestart: false,
    },
    {
      name: "mqk-v3-scan-eod",
      script: "/mnt/c/Users/gocho/MQK-v2/.venv/bin/python",
      args: "/mnt/c/Users/gocho/MQK-v2/run_schedule_v3.py",
      cwd: "/mnt/c/Users/gocho/MQK-v2",
      env: { MQK_PHASE: "scan" },
      // KST 15:00 — 장마감 전 마지막 watchlist 갱신 (기존 14:17에서 이동).
      // close(15:18) 직전 최신 watchlist 확보 목적.
      cron_restart: "0 15 * * 1-5",
      autorestart: false,
    },
    {
      name: "mqk-v3-intraday",
      script: "/mnt/c/Users/gocho/MQK-v2/.venv/bin/python",
      args: "/mnt/c/Users/gocho/MQK-v2/run_schedule_v3.py",
      cwd: "/mnt/c/Users/gocho/MQK-v2",
      env: { MQK_PHASE: "intraday" },
      // KST 09:00~14:50, 10분 간격 (LLM 비용 절감 — Tier2 드리프트 감시 주기도 10분).
      // 09:03 레짐 생성 전 틱(09:00)이나 premarket 실패일에는 당일-레짐 가드가 스킵.
      // watchlist 0 + 보유 0 + STABLE이면 코드 게이트가 LLM 호출 없이 스킵.
      cron_restart: "*/10 9-14 * * 1-5",
      autorestart: false,
    },
    {
      name: "mqk-v3-premarket-close",
      script: "/mnt/c/Users/gocho/MQK-v2/.venv/bin/python",
      args: "/mnt/c/Users/gocho/MQK-v2/run_schedule_v3.py",
      cwd: "/mnt/c/Users/gocho/MQK-v2",
      env: { MQK_PHASE: "premarket_close" },
      // KST 15:10 — 마감 직전 레짐 재평가. 직전 13:03 레짐과 비교해 마감 국면 판단.
      // close(15:18)보다 먼저 실행해 최신 레짐을 close context에 반영.
      cron_restart: "10 15 * * 1-5",
      autorestart: false,
    },
    {
      name: "mqk-v3-late-intraday",
      script: "/mnt/c/Users/gocho/MQK-v2/.venv/bin/python",
      args: "/mnt/c/Users/gocho/MQK-v2/run_schedule_v3.py",
      cwd: "/mnt/c/Users/gocho/MQK-v2",
      env: { MQK_PHASE: "late_intraday" },
      // KST 15:08/15:13 — 폭락일(지수 -3%↓ 또는 RED) 전용 과매도 낙주 종가 부근 진입.
      // 평상시에는 코드 게이트가 LLM 호출 없이 즉시 스킵한다 (비용 0).
      // close(15:18)와의 flock 충돌을 피해 앞당김.
      cron_restart: "8,13 15 * * 1-5",
      autorestart: false,
    },
    {
      name: "mqk-v3-psearch-watcher",
      script: "/mnt/c/Users/gocho/MQK-v2/.venv/bin/python",
      args: "/mnt/c/Users/gocho/MQK-v2/run_psearch_watcher.py",
      cwd: "/mnt/c/Users/gocho/MQK-v2",
      // KST 09:01 기동 → 내부 루프(90초 폴링)로 15:06까지 조건검색 편입 감시 (유사 웹훅).
      // 신규 편입: 알림 + watchlist 병합 + intraday LLM 즉시 트리거 (낙주는 알림만).
      cron_restart: "1 9 * * 1-5",
      autorestart: false,
    },
    {
      name: "mqk-v3-close",
      script: "/mnt/c/Users/gocho/MQK-v2/.venv/bin/python",
      args: "/mnt/c/Users/gocho/MQK-v2/run_schedule_v3.py",
      cwd: "/mnt/c/Users/gocho/MQK-v2",
      env: { MQK_PHASE: "close" },
      // KST 15:18 — 정규장 내 청산 판단(일반 주문, 동시호가 직전 = 사실상 종가 청산).
      // 모의투자가 장후 시간외(06) 주문을 미지원하므로 정규장 내로 당김. 복기는 market_close(17:00)가 수행.
      cron_restart: "18 15 * * 1-5",
      autorestart: false,
    },
    {
      name: "mqk-v3-market-close",
      script: "/mnt/c/Users/gocho/MQK-v2/.venv/bin/python",
      args: "/mnt/c/Users/gocho/MQK-v2/run_schedule_v3.py",
      cwd: "/mnt/c/Users/gocho/MQK-v2",
      env: { MQK_PHASE: "market_close" },
      cron_restart: "0 17 * * 1-5",  // KST 17:00
      autorestart: false,
    },
    // MCP 비활성화
    // {
    //   name: "mqk-kis-mcp",
    //   script: "server.py",
    //   cwd: "/home/gochoojang/kis-mcp-source/MCP/Kis Trading MCP",
    //   interpreter: "/home/gochoojang/kis-mcp-source/MCP/Kis Trading MCP/.venv/bin/python",
    //   env: { ENV: "mqk" },
    //   autorestart: true,
    //   restart_delay: 3000,
    // },
    {
      name: "mqk-telegram-news",
      script: "/mnt/c/Users/gocho/MQK-v2/.venv/bin/python",
      args: "-m broker.telegram_news",
      cwd: "/mnt/c/Users/gocho/MQK-v2",
      autorestart: true,
      restart_delay: 3000,
    },
    // ── MQK v4 (국장 세력주 스나이퍼) ─────────────────────────────────────────
    {
      name: "mqk-v4-premarket-sejuk",
      script: "/mnt/c/Users/gocho/MQK-v2/.venv/bin/python",
      args: "/mnt/c/Users/gocho/MQK-v2/run_schedule_v3.py",
      cwd: "/mnt/c/Users/gocho/MQK-v2",
      env: { MQK_PHASE: "premarket_sejuk" },
      // KST 08:45 — 장전 상한가 세력 검증
      cron_restart: "45 8 * * 1-5",
      autorestart: false,
    },
    {
      name: "mqk-v4-premarket",
      script: "/mnt/c/Users/gocho/MQK-v2/.venv/bin/python",
      args: "/mnt/c/Users/gocho/MQK-v2/run_schedule_v3.py",
      cwd: "/mnt/c/Users/gocho/MQK-v2",
      env: { MQK_PHASE: "premarket" },
      // KST 09:03/11:03/13:03 — 레짐 판단 (v3와 동일)
      cron_restart: "3 9,11,13 * * 1-5",
      autorestart: false,
    },
    {
      name: "mqk-v4-scan",
      script: "/mnt/c/Users/gocho/MQK-v2/.venv/bin/python",
      args: "/mnt/c/Users/gocho/MQK-v2/run_schedule_v3.py",
      cwd: "/mnt/c/Users/gocho/MQK-v2",
      env: { MQK_PHASE: "scan" },
      // KST 09:17/11:17/13:17
      cron_restart: "17 9,11,13 * * 1-5",
      autorestart: false,
    },
    {
      name: "mqk-v4-scan-eod",
      script: "/mnt/c/Users/gocho/MQK-v2/.venv/bin/python",
      args: "/mnt/c/Users/gocho/MQK-v2/run_schedule_v3.py",
      cwd: "/mnt/c/Users/gocho/MQK-v2",
      env: { MQK_PHASE: "scan" },
      // KST 15:00 — 마감 전 마지막 스캔
      cron_restart: "0 15 * * 1-5",
      autorestart: false,
    },
    {
      name: "mqk-v4-intraday",
      script: "/mnt/c/Users/gocho/MQK-v2/.venv/bin/python",
      args: "/mnt/c/Users/gocho/MQK-v2/run_schedule_v3.py",
      cwd: "/mnt/c/Users/gocho/MQK-v2",
      env: { MQK_PHASE: "intraday" },
      // KST 09:20~14:50, 10분 간격
      cron_restart: "*/10 9-14 * * 1-5",
      autorestart: false,
    },
    {
      name: "mqk-v4-close",
      script: "/mnt/c/Users/gocho/MQK-v2/.venv/bin/python",
      args: "/mnt/c/Users/gocho/MQK-v2/run_schedule_v3.py",
      cwd: "/mnt/c/Users/gocho/MQK-v2",
      env: { MQK_PHASE: "close" },
      // KST 15:18
      cron_restart: "18 15 * * 1-5",
      autorestart: false,
    },
    {
      name: "mqk-v4-market-close",
      script: "/mnt/c/Users/gocho/MQK-v2/.venv/bin/python",
      args: "/mnt/c/Users/gocho/MQK-v2/run_schedule_v3.py",
      cwd: "/mnt/c/Users/gocho/MQK-v2",
      env: { MQK_PHASE: "market_close" },
      // KST 17:00
      cron_restart: "0 17 * * 1-5",
      autorestart: false,
    },
  ],
};
