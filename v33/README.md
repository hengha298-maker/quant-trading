# V3.3 当日行情可靠性验证

独立工作区与分支基于 V3.2 `3d6dae1`。只新增 `v33/`、一个人工检查入口和专用 CI；不启动、发布或修改手机应用。V3.1/V3.2 手机存储、备份、服务入口及原量化任务保持隔离。没有通知发送或交易入口。

## 实际运行

Windows 双击根目录 `Check-V3.3-Health.cmd`，或在本工作区运行：

```powershell
$py = '..\Runtime\Research32\Scripts\python.exe'
& $py -I -B v33/run.py check
# 可显式选择来源；单源不能通过双源人工窗口检查
& $py -I -B v33/run.py check --sources eastmoney-minute
# 纯离线、完全虚构工程样本，绝不会认证盘中可用性
& $py -I -B v33/run.py replay --input v33/fixtures/fictional-observations.json
```

复用已锁定的 Research32 Python **只读运行**，不安装、升级或修改环境。依赖版本/许可仍见 `v32/requirements-lock.txt` 与 `v32/DEPENDENCIES.md`；新增模块仅用标准库及现有 XSHG 日历。本模块适用 `../v32/LICENSE` 的 MIT 许可。

每次仅查询显式选择的两个公开接口各一次，无重试、无隐式切源。输出到本工作区 `v33/work/时间-随机串/`，含原始公开响应、SHA256、来源元数据、`health.json`、兼容手机手动导入的 `goose-report-v31.json`、中文 `report.md` 和文件清单 `run.json`。真实响应和本机审计全部 Git 忽略。

`--out` 只能是 `v33/work` 下全新子目录，拒绝已有目录、链接/重解析点和旧工作区。不覆盖其他研究输出或用户文件。离线输入不联网；即使伪装新鲜，也不能成为 live 检查。CLI 没有时钟覆盖、Bark、计划任务或实盘参数。

退出0仅表示本次真实14:35—14:45窗口的两个来源检查候选通过，**不是盘中持续可靠性、人工验收或交易授权**。盘外、重放、数据失败或未满足候选条件返回2；诊断仍正常落盘。完整命令耗时记录在 run.json，8秒是单次网络操作超时参数，并非端到端SLA。

健康快照的检查时刻与最终文件完成分开记录；窗口验收还须检查 `run.json` 的 `manualWindowCheckComplete` 和 `endedAt`，不能仅看较早的健康快照。

## 字段与失败关闭

仅审阅 `600900.SH`，硬性核对代码、SH市场及长江电力名称。金额用 Decimal 文本，价格为未复权元/股，0.01元步长；量由手明确换算为股，不缩放价格。零价、无有效成交量、OHLC错误、疑似超过20%变化、标的/单位/复权不符均阻断。20%是异常研究阈值，不是涨跌停规则或除权修复。

- 东方财富 `trends2/get`：与 V1 一分钟接口同类公开端点，保留5日原始响应，读取最后一分钟收盘与分钟标签。历史分钟的开盘字段实际存在0占位，记为缺失并明确不用于报价/回测；最新非09:30分钟缺开盘则阻断，绝不把缺失0当成可交易价格。最新分钟量是该分钟量。
- 腾讯 `qt.gtimg.cn`：GBK固定框架和字段数校验；读取第3价格、第30供应商报价更新时间、第6/36累计量并对照、第33/34高低。没有官方版本化字段合同，必须人工核对；格式变化即失败。累计量不能与东方财富单分钟量直接比较。
- **两个来源都没有已认证最后一笔成交时刻**。`confirmedLastTradeAt:null`，分钟标签和供应商更新时间不冒充成交时刻。盘外供应商更新可能晚于15:00，因此不能把最新字符串当作新鲜成交证明。

来源时间、请求/接收时刻、HTTP Date/Age、单次耗时分别保存。来源须属于当天交易日和连续交易时段；来源年龄≤90秒、接收记录年龄≤30秒、无未来/倒序时钟、HTTP缓存年龄≤90秒、服务端Date时差≤300秒。14:57起集合竞价阶段不纳入本阶段连续行情验证。HTTP缺少Date不补造；仍依靠来源时间校验。

两个新鲜来源的时间差须≤60秒、相对价格差≤10bps（分母取较低价）。不同采样时间可能造成正常差异；超阈值需人工复查，不取平均、不选“更好看”的价格。数值一致也始终 `independentFeedVerified:false`，没有独立交易所原始源或再分发许可认证。

TLS和主机名验证始终开启，沿用操作系统代理/绕过配置，不写代理配置、不另行直连绕过。只读记录代理是否配置，不记录主机、凭证或原始异常。403、证书、超时、DNS、连接拒绝、远端断开各给固定码；不能单凭通用连接错误归因某个代理或源站。拒绝重定向和HTTP降级，响应限制2MiB，不执行上游JavaScript。

XSHG 日历继续显式止于 **2026-12-31**。2027或日历不可用时拒绝盘中认证且不发起新行情请求；不猜周一到周五。

## 研究报告与旧版兼容

本阶段一律观察、不操作，`decisionAllowed:false`、`manualAcceptanceVerified:false`、`intradayReliabilityVerified:false`。`fresh-observation` 只说明本次字段/时间/双接口阈值通过，不能称为已完成实际盘中验收。

可用 `check --history <手动准备的日线JSON>` 提供 V3.2 规范行情：必须是 validated 600900、严格截止上一交易日、末20条完整交易日。准确比较**上一完整交易日收盘与包含该日的20日MA**，不是均线交叉；不会以当日盘中报价代替前收盘。缺失、合成、旧日期或包含今日未完成日线时标明不可用。即便上下文有效，仍只输出比较依据与观察，不产生买卖指令。未独立认证发布时间及公司行为的限制继续保留。

输出沿用已有 `goose-investment-report` version1，不改手机数据库或备份结构。它没有虚构回测/模拟成交，也不自动写入旧服务或手机。报告日期沿用有效来源日期；完全缺失时明确是诊断生成日期。旧报价不能靠今天生成报告而“变新”。

保留原版同日、同来源、同策略的去重规则，因此同一天后续快照可能被旧版拒绝重复导入；本阶段三次盘中证据以Windows各自的独立目录为准，不为绕过去重而修改旧手机记录。是否设计报告修订是后续独立需求。

## 手工窗口与测试

见 [人工盘中验证流程](MANUAL-1435-1445.md)；不注册任何自动任务。样本和 mock 只证明工程行为，不证明真实盘中可用性：

```powershell
& $py -B -m unittest discover -s v33/tests -v
& $py -B -m unittest discover -s v32/tests -v
& $py -B -m unittest discover -s v3/tests -p test_server.py -v
node --test v3/tests/*.test.mjs
node --test v33/tests/*.test.mjs
```

## 一手依据与使用限制

- [AKShare 官方一分钟适配源码](https://github.com/akfamily/akshare/blob/main/akshare/stock_feature/stock_hist_em.py)：时间/收盘/量等字段与端点依据；开源库可调用不等于供应商SLA或数据再分发权。
- [腾讯实际公开响应端点](https://qt.gtimg.cn/q=sh600900)：本次只读观察的原始来源，非官方版本化API合同。字段语义和更新时间尚需盘中人工核验，不以其他项目口述作为独立认证。
- [AKShare 新浪接口维护源码](https://github.com/akfamily/akshare/blob/main/akshare/stock/stock_zh_a_sina.py)：公开接口存在频繁调用限制；本轮新浪单票HTTPS探测403后不绕过、不重试，不纳入可用备用源。
- [上交所交易时间](https://english.sse.com.cn/start/trading/schedule/)与[机制](https://english.sse.com.cn/start/trading/mechanism/)：区分连续交易及集合竞价；不替代对2027休市日历的独立审阅。
