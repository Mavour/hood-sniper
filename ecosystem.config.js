module.exports = {
  apps: [
    {
      name: "hoodsniper",
      script: "bot.py",
      args: "run",
      interpreter: "./venv/bin/python",
      cwd: __dirname,
      autorestart: true,
      max_restarts: 100,
      restart_delay: 5000,
      max_memory_restart: "300M",
      out_file: "./logs/out.log",
      error_file: "./logs/err.log",
      time: true,
    },
  ],
};
