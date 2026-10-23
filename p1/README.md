# P1 安装与验收（中文）

此目录为独立只读研究台。**不要在宿舍电脑现有 V1.1 目录升级、安装或运行原 scripts。** 审核阶段只在另一测试目录检出本 Draft PR；不合并、不部署。

## 515450 日线备用源（2026-10-23更新）

用户Windows实机已通过完整采集/原API/新鲜度核验。本版启用**新浪515450原始日线观察备用源**：默认仍先东方财富，主源通过时不调用新浪；失败才回退。每批均重新校验完整响应、原始历史、原API及新鲜度，不固定沿用验收当天的日期或条数。

**价格单位、成交量单位、服务端复权口径尚未独立确认**。新浪数据不进入估值、费用、量价信号或投资建议；保留源站原值及unknown口径，不乘除100。此次不是对所有用途完全放行。证据和逐项权限见[验收与接入记录](../docs/P1-SINA-ACCEPTANCE.md)。

已安装K2工具的用户直接看[Windows最短步骤](SINA-VERIFICATION.md)，无需重装依赖或Git。标准库环境仍能使用P1；只有实际调用新浪或运行真实解码集成测试才需要已有的独立工具环境。下一阶段复用CSV和本地报告，衔接范围见[持仓与V2任务](../docs/P1-HOLDINGS-NEXT.md)，当前不连接手机真实数据或云端。

## Windows Python 3.12

使用单独代码副本，例如 `C:\GooseP1Code`，不要放进 V1.1 目录。Python 3.12 已安装时，无需 pip 安装任何包：

```powershell
cd C:\GooseP1Code\p1
py -3.12 -m unittest discover -s tests -v
```

选择当前用户专用、本地非云同步数据目录。下面使用 LocalAppData；先确认没有被其他备份/同步工具上传。目录内将包含真实数据和离线报告，不可提交或分享。

```powershell
$P1Data = Join-Path $env:LOCALAPPDATA 'GooseResearchP1'
py -3.12 -m goose_research --data-dir "$P1Data" init --confirm-local-storage
py -3.12 -m goose_research --data-dir "$P1Data" template
notepad (Join-Path $P1Data 'holdings-template.csv')
```

模板中的 DEMO、100股等**全是虚构数据**。复制模板为 `holdings.csv` 后再填写；重复运行 template 不覆盖已有模板。也可用 Excel 打开，另存为 **CSV UTF-8（逗号分隔）**，不要改变表头顺序。

| 列 | 填写要求 |
|---|---|
| account | 匿名代号，例如 HB_A、YH_A；1–32位字母数字下划线或短横线，勿填真实账号 |
| broker | 券商名称；不关联任何登录 |
| as_of | 盘后/时点持仓的日期 YYYY-MM-DD；同批次全账户一致，不填未来 |
| coverage | 未录全填 partial；全部境内股票/ETF和现金录全才填 complete |
| cash | 账户现金，元；未知留空；complete 必填；同账户各行相同 |
| symbol | 例如600900.SH、515450.SH；账户只有现金时可空，其余持仓字段全空 |
| kind | stock 或 etf；本程序不权威核验证券身份，需自行核对 |
| quantity | 总股/份数，正整数（允许现有零股，不要求100倍数） |
| available | 券商显示的该日期可卖股/份数，非负整数，不大于总数 |
| broker_cost | 券商显示每股/份成本，元；未知留空，可负；不要自行重建真实收益 |

部分录入不代表整个账户配置。不支持基金、债券、负债等其他资产时请填 partial。券商显示成本受分红/做T影响；无完整流水无法计算真实累计收益。

```powershell
py -3.12 -m goose_research --data-dir "$P1Data" import-csv (Join-Path $P1Data 'holdings.csv')
```

记录返回的快照完整路径。无效输入退出1且不新增快照；成功退出0，每次导入都新增文件，旧快照不变。程序不输出持仓内容。

## 手动行情与离线报告

只在主动执行 collect 时联网，无需API密钥；不上传持仓、券商或账户，URL只带证券代码和日期。日线默认请求3年、分钟实际范围可能很短或不可用。服务使用条款需遵守，不转售数据。

```powershell
py -3.12 -m goose_research --data-dir "$P1Data" collect 600900.SH
& (Join-Path $env:LOCALAPPDATA 'GooseP1SinaTools-K2-1.19.1\Scripts\python.exe') -m goose_research --data-dir "$P1Data" collect 515450.SH
py -3.12 -m goose_research --data-dir "$P1Data" collect 600900.SH --interval minute
```

每次返回独立行情JSON路径。**collect退出0只代表采集记录已保存，不代表行情获取成功**：检查报告中的 status、error_type、实际范围；失败为 unavailable。网络不可用也可生成报告。

日线新增 `--source auto|eastmoney|sina`，默认 `auto`；新浪只适用 `515450.SH` 原始日线观察，受独立采集开关和用途限制控制。日线默认要求北京时间16点前的上一工作日、16点后的当日（周末退回工作日），不接受未结束的当日日线。此规则不是交易所日历，节假日可能保守拒绝；仅在独立核对最后完整交易日后使用 `--expected-last-date YYYY-MM-DD`。新采集数据未达到要求截至日时整批不可用，不沿用早期成功记录。单批不跨来源拼接；未知复权的备用数据即使通过结构校验也不进入费用参考计算。

```powershell
py -3.12 -m goose_research --data-dir "$P1Data" report '替换为快照完整路径' --market '替换为600900日线记录完整路径' --market '替换为515450日线记录完整路径'
```

可省略全部 --market，验收缺数据状态；多份用重复 --market 参数，单标的只选一份日线。用浏览器打开返回的HTML，断网仍可查看。报告引用历史收盘作费用参考，显示是否与快照同日；不代表当前市场、当日可卖数量或可执行交易。分钟记录可附加展示审计信息，但不生成分钟策略。

## 验收清单

1. 自动测试通过；模板存在时重新生成应失败且保留原文件。
2. 用虚构数据只填两个标的，未知成本/现金保留未知；报告不显示全账户配置比例、累计收益或交易信号。
3. 将数量改负、成本改NaN、重复同账户证券或可卖大于总数，确认导入失败且旧快照数量与内容未变。
4. 报告区分事实/假设/结论/不可用，华宝与银河的具体费率均显示示例；保守费用不标实际费用。
5. 不传行情生成报告，明确缺数据；网络失败记录 unavailable，无假行情。联网验收须核对实际返回范围和交易日缺口，不能仅凭HTTP成功认定3年完整。
6. 尝试仓库内路径、OneDrive路径初始化，应拒绝。检查P1未打开V1数据库、未访问Bark、未改变V1文件和计划任务。

## 防误提交、备份与回滚

在仓库根目录，每次提交前执行：

```powershell
py -3.12 p1/check_private.py
py -3.12 p1/check_private.py --all
git diff --cached --stat
git diff --cached
```

有 Git 时脚本检查暂存区或全部跟踪文件；ZIP 无 `.git` 或未安装 Git 时，改为明确标记的文件系统全量扫描，此时不需要执行上面的两条 `git diff`。两种模式都拒绝CSV、JSON快照、HTML、数据库、Excel和备份；原V1两个配置示例例外。ZIP 全量扫描不按 `.gitignore` 排除工具或虚拟环境，不能把私人数据、工具环境放进源码。CI运行全部检查。勿用 git add -f，勿把真实数据贴到源码、测试、PR、截图或聊天。检查不保证识别所有秘密或改名数据，人工审阅仍必要。

停止P1进程后，把整个 `$P1Data` 复制到**另一个非同步、受访问控制的本地磁盘目录**（包含 .local-only、输入、snapshots、market、reports）；不要备份至仓库或云盘。可用 `Get-FileHash -Algorithm SHA256 文件路径` 对比原件与备份。不需要停止或修改V1任务，因为P1不访问V1。

恢复时使用新的空本地目录，复制整个备份，不与现存快照目录混合覆盖，重新选择快照与行情路径。误录时重新导入正确CSV形成新快照，再指定新路径生成报告；旧快照保留追溯。代码回滚可切回原提交/删除独立代码副本，私有目录保留；不执行数据库迁移，也不触碰V1环境。备份同样包含敏感信息，建议使用系统磁盘加密。

设计与限制见 [修订设计](../docs/P1-DESIGN.md)，实测证据见 [测试记录](../docs/P1-VALIDATION.md)。

## PR #3 审查修复补充

本地JSON与直接报告入口现在统一校验，不支持手工放入旧版本、未来/乱序/非法OHLC或结构不一致数据。无效本地输入会拒绝整份报告，不生成部分分析。Windows只接受确认的本地固定磁盘：UNC、映射网络驱动器、设备路径、未知驱动器和无法严格解析的链接会拒绝；普通本地中文路径可用。过大数值或超过12位小数会被拒绝，请核对单位和原始数据，不要盲目截断。

报告新增账户现金及股票/ETF、总数量、真实录入可卖数量、成本与快照日期。v1快照没有日内时刻，会明确标注，不代表当前可卖量。详见[本轮修复与测试证据](../docs/P1-REVIEW-FIXES.md)。
