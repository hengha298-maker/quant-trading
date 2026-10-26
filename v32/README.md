# 养鹅计划 V3.2：独立量化研究 MVP

从 V3.1 `0cc04df` 建独立工作区与功能分支。V3.1 基线、原 V1 账户、冻结 V2/P1 和真实研究数据不参与写入。这里只运行独立假设资金的历史研究，没有实盘交易或任务注册。研究流程不发送 Bark；另有严格要求明确授权的单次人工送达测试入口。

## 本机直接使用

1. 双击根目录 `Start-V3.2.cmd`，打开独立的 `127.0.0.1:9394`。手机安装服务由控制窗口管理，只在前台使用私网 HTTPS `10001`。已验收 V3.1 继续使用 `9393/10000`，两个来源的浏览器数据相互独立，不会自动同步或覆盖。
2. 双击 `Replay-600900-2025.cmd`，重放本机已下载的 2025 公开历史数据并发布本工作区的研究 JSON。原数据和每次结果都保留在忽略的 `v32/work`，不会写到旧数据目录。
3. 决策页点“载入本机研究报告”，核对来源与日期，再确认保存。保存后可断网关闭重开查看，并完整加密备份。加载只由点击触发，不自动导入；离线时可手选已下载文件。

Windows 研究 Python：`D:\LifeOS\Wealth\Goose\Runtime\Research32\Scripts\python.exe`。新建虚拟环境未修改系统 Python 或其他环境。版本锁见 `requirements-lock.txt`；许可记录见 `DEPENDENCIES.md`。

## 可重复 CLI

以下命令在独立工作区执行；`$py` 仅为 PowerShell 进程变量。

```powershell
$py = '..\Runtime\Research32\Scripts\python.exe'
& $py -I -B v32/run.py fetch --symbol 600900.SH --source tencent --start 2025-01-01 --end 2025-12-31
& $py -I -B v32/run.py backtest --dataset v32/work/600900-tencent-2025.json --publish
& $py -I -B v32/run.py compare --left v32/work/600900-eastmoney-2025.json --right v32/work/600900-tencent-2025.json
& $py -I -B v32/run.py pre1445 --dataset v32/work/600900-tencent-2025.json --test
& $py -I -B v32/run.py backtest --dataset v32/fixtures/synthetic-600900-2025.json
```

`fetch` 输出新目录中的 `dataset.json`，不会覆盖既有数据；上面的固定数据文件名属于本轮实际下载留存。所有命令可用 `--out v32/work/一个全新目录` 明确输出位置，已有目录和工作区外位置被拒绝。质量阻断时输出诊断和观察报告，进程返回非零，不捏造零收益图。

输出包括数据指纹、质量报告、完整回测曲线/指令/模拟成交、旧版兼容报告 `goose-report-v31.json` 和新版四策略报告 `goose-report-v32.json`。旧版报告不含未知扩展字段，已用原 V3.1 验证器在内存接受，不修改原应用。逐笔 ETF 分以下成交价保留在回测 JSON，不强行舍入为手机整数分成交记录。

`--publish` 仅把生成的研究报告原子替换到本 V3.2 静态白名单地址。它不发布账户、流水、持仓或备份，不接收手机上传，不涉及云部署。报告资源不加入 service worker 缓存；用户确认导入后才进入本机数据库并支持离线。

## 行情证据与未完成事项

本轮实际从东方财富与腾讯各取得 600900、515450 的 2025 年 243 个 XSHG 日线。两接口逐日 OHLC/统一成交量数值一致，原 JSON、请求结果和 SHA256 留在忽略目录。**这只是两家接口的数值对照，不等于独立交易所原始行情授权或计价单位认证。** 不擅自提升 `crossValidated`。

数据明确记录来源 URL、币种、元/股或份的价格口径、不复权、交易日、抓取时间和字段。成交量由来源的手换为股/份，转换在适配器明确完成。来源没有日线发布时间时保留 `publishedAt:null`，不拿抓取时间冒充发布时刻；次交易日开盘可用仅为保守研究假设，尚未取得 point-in-time 发布证明。

515450 增加独立异常诊断：0.001 元步长、≥20% 日变化、10/100 倍及倒数附近的疑似缩放。真实样本未发现这些异常，仍因单位证据不完整保持 **blocked**，不计算其真实收益。无自动除以 10/100 的“修复”。自定义标的须显式类型，未经身份与单位审阅先阻断。

2026-10-26 原 V1 分钟请求在代理链出现 `ProxyError`。本轮历史日线请求成功不证明原分钟链路已修复；未关闭代理、证书或新鲜度校验，也未重跑原模拟策略。

日历依赖 `exchange_calendars` XSHG，独立审阅边界仍为 **2026-12-31**。2027 请求明确拒绝，不退化为周一到周五。跨年须先核验交易所休市公告、更新独立日历及回归测试，原 V1 不在本轮升级。

## 回测口径

- 买入持有、V1 前收盘对 MA20 的连续比较、MA20 交叉对照、定期投入使用共同区间、初始资金和外部注资日程。统一预留 21 条完整日线。
- V1 为前一完整交易日收盘高于/低于 20 日均线时连续 BUY/SELL，相等 HOLD，**不是交叉**。本研究只复现信号，统一次日开盘撮合；不冒充原 V1 的 14:50 分钟执行或原账户收益。
- Decimal 价格和整数分现金、100 股整手、T+1、开盘仓位上限、佣金最低额、卖股印花税与滑点。费用是配置假设，不保证覆盖全部规费及历史费率变化。
- 定投预算不足整手时跨期积累；实际买入支出含佣金、滑点不双扣。所有策略获得相同外部现金流，不用增资冒充收益。
- 指令只用前序已发布数据。当日完整成交量只作事后零成交可行性过滤，不是开盘时可观察的预测输入；未建立涨跌停、撮合队列或真实流动性模型。
- 总收益为时间加权价格路径收益 TWR：先计旧仓位隔夜至开盘的收益，再计开盘注资后至收盘的收益并链乘；新钱不分摊注资前的隔夜涨跌。年化按 252 交易日；不是相对累计本金 ROI。回撤和波动按每日收盘 TWR 净值，未衡量盘中极值。不可表示的指标或缺少三段区间时阻断手机收益导出，不用零补齐。
- 固定日期 60/20/20 训练/验证/测试分区，不搜索最优参数；各段沿用模拟仓位，不重置本金。手机对比为完整共同区间，分段指标在完整回测 JSON。
- **未复权且未建模分红、拆股和全部企业行为，不是完整含息总回报，也不是已经证实的策略有效性。** 股息型标的尤其要注意这一限制。信号只作为带日期、依据、风险的历史研究标签。

## 14:45 流程

报告生成仅 `pre1445 --test`，不发送通知。单次人工 Bark 测试是独立入口，必须另获明确授权，不能在研究流程或计划任务中隐式调用。它分别记录数据健康、观察信号、历史回测和显式请求的 V1 只读状态。`--v1-db` 使用 SQLite `mode=ro&immutable=1` 与 `query_only`，前后检查主库哈希及 WAL/SHM/journal，不读取持仓或余额，不导入 `Store`。

生成时间使用真实时钟，超过截止时间不能声明提前生成。盘外、非交易日、来源过期和无当日分钟行情都会明确标记；观察、不操作。测试的 Bark JSON 只是本机预览，`sent:false`、`phoneArrivalVerified:false`。原 14:50 结果带自己的真实日期，不冒充 14:45 新结果。

手动入口格式：`python -I -B v32/run.py manual-bark --health <本机pre1445-test.json> --config <本机Bark配置.json> --confirm-one-test`。缺少确认标志会拒绝；配置只读、密钥不进入命令行或日志；固定摘要仅含标的/数据日期/诊断码，不含持仓或金额。服务接受不等于手机已显示。**本轮未执行此发送命令，测试均mock传输。**

只有后续取得用户对具体手动 Bark 发送的明确授权，并完成盘中可靠性、总耗时和手机到达测试后，才具备提出独立自动任务的前提。本轮不注册或修改任务、不发通知、不读改 Bark 密钥。

## 自动验证

```powershell
& $py -B -m unittest discover -s v32/tests -v
& $py -B -m unittest discover -s v3/tests -p test_server.py -v
node --test v3/tests/*.test.mjs
# 安装的Playwright 1.62.1和Edge，仅隔离虚构浏览器数据
$env:V3_BASE_URL = 'http://127.0.0.1:9394'
node v3/tests/browser.cjs
node v3/tests/browser-v31.cjs
node v32/tests/browser.cjs
```

核心测试无网络、无真实账户。真实数据不放进 Git；仓库内离线重放 fixture 为明确虚构的完整价格路径。Edge 回归不能替代 iPhone 实机验收。新模块 MIT 许可见 `LICENSE`，不重新许可旧仓库。

## 原始参考

- [AKShare 日线接口与口径](https://akshare.akfamily.xyz/data/stock/stock.html)
- [AKShare 基金接口原文](https://github.com/akfamily/akshare/blob/main/docs/data/fund/fund_public.md)
- [AKShare 数据风险说明](https://github.com/akfamily/akshare/blob/main/docs/data_tips.md)
- [上交所交易机制与时间](https://english.sse.com.cn/start/trading/mechanism/)
- [上交所披露 515450 基金身份](https://www.sse.com.cn/disclosure/fund/announcement/c/new/2023-02-08/512330_20230208_9XYC.pdf)

开源代码许可不等于行情再分发授权。本版不打包真实源数据、密钥或个人资料进入 Git；外部接口可用性、服务条款与限频需持续核查，失效时停止对应研究。
