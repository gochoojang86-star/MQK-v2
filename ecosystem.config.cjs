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
      cron_restart: "45 8 * * 1-5",  // KST 08:45 (PM2는 호스트 로컬 시간 기준 — 호스트는 Asia/Seoul)
      autorestart: false,
    },
    {
      name: "mqk-v3-scan",
      script: "/mnt/c/Users/gocho/MQK-v2/.venv/bin/python",
      args: "/mnt/c/Users/gocho/MQK-v2/run_schedule_v3.py",
      cwd: "/mnt/c/Users/gocho/MQK-v2",
      env: { MQK_PHASE: "scan" },
      // KST 09:10/11:10/14:10 (원래 의도는 09:10/11:00/14:00이었으나, 분 단위까지
      // 정확히 맞추려면 별도 항목이 필요. 단순화를 위해 09:10/11:10/14:10으로 통일)
      cron_restart: "10 9,11,14 * * 1-5",
      autorestart: false,
    },
    {
      name: "mqk-v3-intraday",
      script: "/mnt/c/Users/gocho/MQK-v2/.venv/bin/python",
      args: "/mnt/c/Users/gocho/MQK-v2/run_schedule_v3.py",
      cwd: "/mnt/c/Users/gocho/MQK-v2",
      env: { MQK_PHASE: "intraday" },
      cron_restart: "*/5 9-14 * * 1-5",  // KST 09:00~14:55, 5분 간격
      autorestart: false,
    },
    {
      name: "mqk-v3-close",
      script: "/mnt/c/Users/gocho/MQK-v2/.venv/bin/python",
      args: "/mnt/c/Users/gocho/MQK-v2/run_schedule_v3.py",
      cwd: "/mnt/c/Users/gocho/MQK-v2",
      env: { MQK_PHASE: "close" },
      cron_restart: "30 15 * * 1-5",  // KST 15:30
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
