# Windows指南：515450.SH原始日线备用源

用户已于2026-10-23完成Windows采集链路实测。本版开放**原始日线观察**回退；价格单位、成交量单位及服务端复权口径仍未独立确认，不能用于估值、费用、量价信号或投资建议。证据与权限见[接入说明](../docs/P1-SINA-ACCEPTANCE.md)。

## 已安装K2工具：最短升级步骤

从[原Draft PR #3分支下载新ZIP](https://github.com/hengha298-maker/quant-trading/archive/refs/heads/codex/goose-v2-p1-readonly.zip)，解压到**新的独立目录**；打开其中`p1`文件夹，在资源管理器地址栏输入`powershell`。不要覆盖V1.1或私人数据目录。以下复用已有环境，不需要Git、激活环境、管理员权限或重新安装依赖。

```powershell
$P1SinaPython = Join-Path $env:LOCALAPPDATA 'GooseP1SinaTools-K2-1.19.1\Scripts\python.exe'
& $P1SinaPython -m unittest discover -s tests -p test_sina_integration.py -v
if ($LASTEXITCODE -ne 0) { throw '集成测试未通过，请停止并保留结果。' }
& $P1SinaPython -m goose_research.diagnose_sina
```

- 集成专项应为**7项通过，无跳过**，全用虚构输入，不联网；包括真实K2解码器、原API和实际子进程。
- 诊断会联网，成功应为`observed`、`worker_matches_parent=true`、`failed_stage/check=null`。日期按执行时点检查，不要求永远停在2026-10-23。
- 现在`source_gate_enabled=true`仅指原始观察采集开关；`source_usage=raw_observation_only`，价格/成交量单位、复权、估值、量价及建议字段仍false。
- 诊断只输出脱敏JSON，不输出价格、完整响应、私人路径、持仓或异常原文；不读取持仓、不保存行情、不改变权限。可以回传这份JSON。
- 失败保留`unavailable`，不改日期或安全校验来通过。解释器不存在时先停下，不要改用V1.1环境安装。

只需一条脱敏诊断命令时：

```powershell
& (Join-Path $env:LOCALAPPDATA 'GooseP1SinaTools-K2-1.19.1\Scripts\python.exe') -m goose_research.diagnose_sina
```

完整P1回归可运行：

```powershell
& $P1SinaPython -m unittest discover -s tests -v
```

Windows已安装且依赖版本匹配时应145项通过。未装可选工具的标准库环境会跳过真实解码/集成项，跳过不能当通过。ZIP无Git场景及中文路径/cp1252控制台有自动回归。可选依赖版本沿用原`requirements-sina.txt`，本次没有新增或升级依赖。

## 实际保存一次主源优先采集

确认下面LocalAppData目录为本地固定磁盘、没有被任何同步软件上传，再运行（可复用旧目录；不读持仓、不覆盖快照）：

```powershell
$P1Data = Join-Path $env:LOCALAPPDATA 'GooseResearchP1'
& $P1SinaPython -m goose_research --data-dir "$P1Data" init --confirm-local-storage
if ($LASTEXITCODE -ne 0) { throw '本地目录确认失败，请停止。' }
& $P1SinaPython -m goose_research --data-dir "$P1Data" collect 515450.SH
```

`init`只创建/确认本地目录标记，不导入、清空或重写持仓。`collect`默认auto：东方财富通过校验就结束；失败才请求新浪。每批新浪都重新校验完整历史与原API；只用同一来源，不拼接两个来源，不拿旧快照补新行情。

命令返回**新记录路径**；collect退出0仅表示记录已保存，不代表成功取得行情。在本地记事本打开该JSON，查看`status`、`source`、`provider_attempts`、`freshness`和`provenance`：

- 主源正常：仅有`eastmoney / observed`，不调用新浪。
- 主源失败、备用通过：`eastmoney / unavailable`后为`sina / observed`；`usage=raw_observation_only`，`api_rows_match=true`，单位和复权核验仍false。成交量保留原值，不乘除100。
- 两源失败：`unavailable`、`rows=[]`；没有成功条数、范围、来源审计或半批行情，不产生建议。

只用主源可在collect命令后加`--source eastmoney`；单独核验新浪可加`--source sina`，仍不能绕过校验或用途限制。仅支持515450.SH日线，不扩展600900或分钟行情。不要分享含私人目录的路径或本地持仓报告；反馈问题优先只发脱敏诊断。

## 日期、安全与回滚

默认按北京时间16点前的上一工作日、16点后的当日检查，周末退回工作日，拒绝未结束的当日日线。它**不是交易所日历**，节假日可能保守拒绝；只有独立核实最后完整交易日后才能用`--expected-last-date YYYY-MM-DD`，不能为了通过而改成早期日期。条数和响应字节数随时间变化，728条、17915字节不是永久成功常数。

HTTPS默认校验、禁止重定向、响应上限8MB、下载15秒/子进程45秒、固定本地解码器调用5秒/128MiB等限制保留。失败先看`failed_stage/check`与`not_reached`；未执行阶段不能算通过。旧第126行、K2/KL及完整尾注的历史解释分别见[阶段诊断](../docs/P1-SINA-DIAGNOSTICS.md)、[K2修复](../docs/P1-SINA-K2.md)、[尾注修复](../docs/P1-SINA-TRAILER.md)，其当时“尚未实机验收”的状态已由本次受限接入替代。

停止P1只需关闭窗口。回滚可使用`--source eastmoney`，或重新使用上一份独立P1源码；保留私人数据目录及全部历史快照，不删除、不合并覆盖、不迁移数据库。V1.1、Bark、任务、V2手机原型、已装依赖均不修改，不接入券商交易。

下一阶段CSV与本地资产报告的衔接见[开发任务与验收](../docs/P1-HOLDINGS-NEXT.md)，常规持仓导入用法见[README](README.md)。当前仍没有手机真实数据服务或云同步。
