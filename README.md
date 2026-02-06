## 股票实时量化工具（最小可运行版）

这是一个**实时行情推送 + 策略信号 + 纸上交易**的量化工具骨架：

- **后端**: FastAPI + WebSocket（实时推送价格/指标/信号/持仓）
- **数据源**: 默认内置 `SimulatedProvider`（离线模拟实时数据，保证你开箱可跑）；也预留了 HTTP 拉取型 Provider 接口（可接入 TuShare/AlphaVantage/Polygon 等）
- **策略**: 均线交叉（MA Crossover）、RSI（示例）
- **前端**: 一个轻量静态网页仪表盘（Chart.js + WebSocket）

### 快速开始

在项目根目录执行：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

如果你本机 pip 版本较旧，建议先升级一次：

```bash
python -m pip install --upgrade pip
```

或直接：

```bash
bash scripts/run_dev.sh
```

如果你需要**稳定不断流**（避免 WebSocket 出现 `1012 service restart`），用：

```bash
bash scripts/run.sh
```

打开：

- Dashboard: `http://127.0.0.1:8000/`
- WebSocket: `ws://127.0.0.1:8000/ws`

### 配置

默认读取 `config/config.yaml`。你可以修改：

- `symbols`: 订阅标的（例如 `AAPL`, `MSFT`，或你自定义的股票代码）
- `interval_seconds`: 推送间隔
- `strategy`: 使用的策略与参数

切换策略示例（在 `config/config.yaml`）：

- `strategy.type: ma_crossover`
- `strategy.type: rsi`

### 接入真实 A 股实时行情（东方财富，免 token）

项目内置 `EastmoneyProvider`，无需 token。示例配置在 `config/config_ashare_eastmoney.yaml`。

启动时用环境变量指定配置文件：

```bash
export QUANT_CONFIG="/Users/kl/Desktop/lianghua/config/config_ashare_eastmoney.yaml"
uvicorn app.main:app --reload --port 8000
```

支持的股票代码格式：

- `600519.SH` / `000001.SZ`
- `sh600519` / `sz000001`
- `600519` / `000001`（自动推断：6xxxxx -> SH，其它 -> SZ）

注意：

- 东方财富接口属于**非官方**，可能会限流/变更；建议把 `interval_seconds` 设为 >= 1s。
- 如果你网络需要代理，可在 `provider.eastmoney.proxy` 填如 `http://127.0.0.1:7890`。

### 使用 efinance 拉取全 A 股实时行情（每 60 秒过滤刷新）

脚本：`scripts/efinance_a_share_realtime.py`

功能：

- 拉取全 A 股实时行情
- 输出字段：股票代码、名称、最新价、涨跌幅、成交额
- 过滤条件：**成交额 > 5 亿** 且 **涨幅 3% ~ 5%**
- 默认每 **60 秒**刷新一次

运行：

```bash
source .venv/bin/activate
pip install -r requirements.txt
python scripts/efinance_a_share_realtime.py
```

只跑一次（不循环）：

```bash
python scripts/efinance_a_share_realtime.py --once
```

### 使用 AkShare 监控「有色金属」板块（板块平均跌幅触发弹窗）

脚本：`scripts/akshare_nonferrous_monitor.py`

逻辑：

- 拉取行业板块「有色金属」的成分股列表
- 获取成分股实时涨跌幅，计算板块**平均涨跌幅**
- 若 **板块平均涨跌幅 <= -3%** 且 **铜陵有色(000630)跌幅小于 2%**（即涨跌幅 > -2%）
  则弹窗提醒：**“防范补跌风险”**

运行（默认每 20 秒刷新一次，可改成 60 秒）：

```bash
source .venv/bin/activate
pip install -r requirements.txt
python scripts/akshare_nonferrous_monitor.py --refresh 60
```

### 使用 Baostock 做T监控（分钟K轮询 + 彩色报警）

脚本：`scripts/baostock_t0_monitor.py`

逻辑：

- Base=9:25 开盘价（若 Baostock 无 9:25 分钟K，降级为当日第一根分钟K的开盘价）
- 红色买入报警：现价跌破 Base 的 1.5% 且当前分钟成交量较前 5 分钟萎缩
- 买入后：
  - 绿色卖出报警：涨幅达 1.2% 或时间到达 14:50
  - 蓝色熔断报警：亏损达 2%

运行（默认每 10 秒轮询一次）：

```bash
source .venv/bin/activate
pip install -r requirements.txt
python scripts/baostock_t0_monitor.py --symbol 000630
```

### 运行逻辑（概要）

1. Provider 产生或拉取行情 tick（价格、时间戳）
2. Engine 持续更新指标（MA/RSI）
3. Strategy 生成信号（BUY/SELL/HOLD）
4. PaperBroker 执行模拟下单，更新持仓与资金
5. WebSocket 广播给 Dashboard

---

如果你要接入**真实A股实时数据**（TuShare、聚宽、米筐等）或美股（IEX/Polygon/Alpaca），告诉我你想用哪个数据源、是否已有 token，我可以把 Provider 直接补齐到可用状态。

