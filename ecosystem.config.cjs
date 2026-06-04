// ecosystem.config.cjs
module.exports = {
  apps: [
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
      name: "mqk-close",
      script: "/mnt/c/Users/gocho/MQK-v2/.venv/bin/python",
      args: "/mnt/c/Users/gocho/MQK-v2/run_schedule.py",
      cwd: "/mnt/c/Users/gocho/MQK-v2",
      env: { MQK_PHASE: "close" },
      cron_restart: "30 15 * * 1-5",
      autorestart: false,
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
