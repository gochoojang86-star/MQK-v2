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
    {
      name: "mqk-kis-mcp",
      script: "/home/gochoojang/kis-mcp-source/MCP/Kis Trading MCP/.venv/bin/python",
      args: "server.py",
      cwd: "/home/gochoojang/kis-mcp-source/MCP/Kis Trading MCP",
      env: { ENV: "mqk" },
      autorestart: true,
      restart_delay: 3000,
    },
    {
      name: "mqk-telegram-news",
      script: "/mnt/c/Users/gocho/MQK-v2/.venv/bin/python",
      args: "-m broker.telegram_news",
      cwd: "/mnt/c/Users/gocho/MQK-v2",
      autorestart: true,
      restart_delay: 5000,
    },
  ],
};
