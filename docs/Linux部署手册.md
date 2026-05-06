# Linux 部署手册

## 1. 部署目标

这一版推荐的 Linux 单机部署方式是：

- `MySQL`
- `Python 3.10+`
- `systemd`
- `zj-agent admin` 轻量后台
- 每周定时任务：抓取 -> AI 提取 -> 入池 -> 飞书汇总推送

推荐拆成两个 systemd 单元：

- `zj-agent-web.service`
  - 持续运行后台页
- `zj-agent-weekly.timer`
  - 每周触发一次完整任务

## 2. 目录约定

下面以 `/opt/zj-agent` 为例：

```bash
/opt/zj-agent
├── .env
├── .venv
├── configs
├── deploy
├── docs
├── sql
└── src
```

## 3. 系统依赖

Ubuntu / Debian 示例：

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip mysql-client
```

## 4. 安装项目

```bash
sudo mkdir -p /opt/zj-agent
sudo chown -R $USER:$USER /opt/zj-agent
cd /opt/zj-agent
```

把代码放进去后执行：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 5. 配置 `.env`

至少需要配置这些项：

```bash
MYSQL_DSN=mysql+pymysql://user:password@127.0.0.1:3306/scraper?charset=utf8mb4

LLM_PROVIDER=openai
LLM_API_KEY=...
LLM_BASE_URL=...
LLM_MODEL=...

FEISHU_WEBHOOK_URL=...
FEISHU_SIGN_SECRET=

ADMIN_USERNAME=admin
ADMIN_PASSWORD=change-me
APP_SECRET_KEY=replace-with-a-long-random-string
APP_LOG_DIR=/var/log/zj-agent

WEEKLY_LOOKBACK_DAYS=7
WEEKLY_NOTIFY_LIMIT=10
EXTRACT_BATCH_SIZE=50
POOL_BATCH_SIZE=50
```

## 6. 初始化数据库和来源

```bash
export PYTHONPATH=src
.venv/bin/python -m zj_agent.cli init-db
.venv/bin/python -m zj_agent.cli seed-sources
```

## 7. 先手工跑一轮

上线前建议手工跑一遍完整链路：

```bash
export PYTHONPATH=src
.venv/bin/python -m zj_agent.cli run weekly-job
```

如果只想验证后台：

```bash
export PYTHONPATH=src
.venv/bin/python -m zj_agent.cli serve --host 0.0.0.0 --port 8080
```

浏览器访问：

```text
http://<server-ip>:8080
```

## 8. 安装 systemd

把示例文件复制到系统目录：

```bash
sudo cp deploy/systemd/zj-agent-web.service /etc/systemd/system/
sudo cp deploy/systemd/zj-agent-weekly.service /etc/systemd/system/
sudo cp deploy/systemd/zj-agent-weekly.timer /etc/systemd/system/
```

按实际环境修改：

- `WorkingDirectory`
- `ExecStart`
- `User`
- `Group`

重载并启动：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now zj-agent-web.service
sudo systemctl enable --now zj-agent-weekly.timer
```

## 9. 查看运行状态

```bash
sudo systemctl status zj-agent-web.service
sudo systemctl status zj-agent-weekly.timer
sudo journalctl -u zj-agent-web.service -f
sudo journalctl -u zj-agent-weekly.service -n 200
```

## 10. 后台页能做什么

第一版后台页提供：

- 仪表盘
- 来源管理
- 线索池查看
- 任务中心
- 手动执行本周任务
- 单来源试跑
- 失败重试
- 重发飞书周汇总
- 来源启停

## 11. 周任务实际做什么

`run weekly-job` 会顺序执行：

1. `crawl weekly --all`
2. 提取未处理原文
3. 对失败提取再重试一轮
4. 同步到 `clue_pool`
5. 飞书发送最近 7 天、带发布时间、未推送的周汇总

## 12. 为什么用 systemd 而不是 cron

主要原因：

- 有统一日志
- 可以看服务状态
- 定时任务失败后更容易排查
- `Persistent=true` 可以补跑错过的时间点

如果只是测试环境，cron 也能跑，但正式推荐还是 `systemd + timer`。
