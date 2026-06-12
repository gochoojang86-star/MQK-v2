// ecosystem.config.cjs
module.exports = {
  apps: [
    {
      name: "mqk-holiday-check",
      script: "/mnt/c/Users/gocho/MQK-v2/.venv/bin/python",
      args: "/mnt/c/Users/gocho/MQK-v2/run_schedule.py",
      cwd: "/mnt/c/Users/gocho/MQK-v2",
      env: { MQK_PHASE: "holiday_check" },
      cron_restart: "30 15 * * *",  // UTC 15:30 = KST 00:30
      autorestart: false,
    },
    {
      name: "mqk-premarket",
      script: "/mnt/c/Users/gocho/MQK-v2/.venv/bin/python",
      args: "/mnt/c/Users/gocho/MQK-v2/run_schedule.py",
      cwd: "/mnt/c/Users/gocho/MQK-v2",
      env: { MQK_PHASE: "premarket" },
      cron_restart: "0 8 * * 1-5",
      autorestart: false,
    },
    {
      name: "mqk-scan",
      script: "/mnt/c/Users/gocho/MQK-v2/.venv/bin/python",
      args: "/mnt/c/Users/gocho/MQK-v2/run_schedule.py",
      cwd: "/mnt/c/Users/gocho/MQK-v2",
      env: { MQK_PHASE: "scan" },
      cron_restart: "30 8 * * 1-5",
      autorestart: false,
    },
    {
      name: "mqk-intraday",
      script: "/mnt/c/Users/gocho/MQK-v2/.venv/bin/python",
      args: "/mnt/c/Users/gocho/MQK-v2/run_schedule.py",
      cwd: "/mnt/c/Users/gocho/MQK-v2",
      env: { MQK_PHASE: "intraday" },
      cron_restart: "*/5 9-15 * * 1-5",  // 평일 09:00~15:55 매 5분
      autorestart: false,
    },
    {
      name: "mqk-close",
      script: "/mnt/c/Users/gocho/MQK-v2/.venv/bin/python",
      args: "/mnt/c/Users/gocho/MQK-v2/run_schedule.py",
      cwd: "/mnt/c/Users/gocho/MQK-v2",
      env: { MQK_PHASE: "close" },
      cron_restart: "30 15 * * 1-5",
      autorestart: false,
    },
    // ⚠️ 경고: 아래 v3 앱들과 위의 v2 트레이딩 앱(mqk-premarket/scan/intraday/close)을
    // 동시에 실행하면 같은 계좌에서 두 개의 자율 트레이더가 동시에 주문합니다.
    // v3로 전환 시 반드시 v2 4개 앱을 `pm2 stop`/`pm2 delete` 하거나 cron_restart를
    // 주석 처리한 후 v3를 시작하세요. (운영 전환은 수동 결정 사항)
    {
      name: "mqk-v3-premarket",
      script: "/mnt/c/Users/gocho/MQK-v2/.venv/bin/python",
      args: "/mnt/c/Users/gocho/MQK-v2/run_schedule_v3.py",
      cwd: "/mnt/c/Users/gocho/MQK-v2",
      env: { MQK_PHASE: "premarket" },
      // KST 09:03 — 장 시작 후 시가/초반 흐름을 보고 레짐 판단 (장전 실행 불필요).
      // 09:05가 아닌 09:03인 이유: intraday */5 틱(09:05)과 flock 충돌을 피하기 위함.
      cron_restart: "3 9 * * 1-5",
      autorestart: false,
    },
    {
      name: "mqk-v3-scan",
      script: "/mnt/c/Users/gocho/MQK-v2/.venv/bin/python",
      args: "/mnt/c/Users/gocho/MQK-v2/run_schedule_v3.py",
      cwd: "/mnt/c/Users/gocho/MQK-v2",
      env: { MQK_PHASE: "scan" },
      // KST 09:17/11:17/14:17 — 09:03 레짐 판단 후 첫 스캔.
      // :17인 이유: intraday */5 틱(:15, :20)과 flock 충돌을 피하기 위함.
      cron_restart: "17 9,11,14 * * 1-5",
      autorestart: false,
    },
    {
      name: "mqk-v3-intraday",
      script: "/mnt/c/Users/gocho/MQK-v2/.venv/bin/python",
      args: "/mnt/c/Users/gocho/MQK-v2/run_schedule_v3.py",
      cwd: "/mnt/c/Users/gocho/MQK-v2",
      env: { MQK_PHASE: "intraday" },
      // KST 09:00~14:55, 5분 간격. 09:03 레짐 생성 전 틱(09:00)이나 premarket 실패일에는
      // run_intraday_v3의 당일-레짐 가드가 NO_TRADE로 안전하게 스킵한다.
      cron_restart: "*/5 9-14 * * 1-5",
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
    {
      name: "mqk-kis-mcp",
      script: "server.py",
      cwd: "/home/gochoojang/kis-mcp-source/MCP/Kis Trading MCP",
      interpreter: "/home/gochoojang/kis-mcp-source/MCP/Kis Trading MCP/.venv/bin/python",
      env: { ENV: "mqk" },
      autorestart: true,
      restart_delay: 3000,
    },
    {
      name: "mqk-telegram-news",
      script: "/mnt/c/Users/gocho/MQK-v2/.venv/bin/python",
      args: "-m broker.telegram_news",
      cwd: "/mnt/c/Users/gocho/MQK-v2",
      cron_restart: "0 21 * * *",  // UTC 21:00 = KST 06:00 매일 재시작 (메모리 초기화)
      autorestart: false,           // 21:00 KST 운영 종료 후 재시작 안 함
    },
  ],
};
