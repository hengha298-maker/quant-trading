# Windows 新手指南：核验 515450.SH 的新浪日线候选源

这份指南只用于独立测试公开行情，不需要券商账号、持仓文件、Bark 密钥或其他凭证。原有 V1.1 定时任务、SQLite 数据库、Bark 配置和已安装环境保持不动。P1 的一般用法见 [README.md](README.md)。

## 先了解当前状态

**新浪候选源尚未完成真实网络验收，当前严格未启用。** 本轮开发环境访问 `finance.sina.com.cn` 遇到代理 `403 Forbidden`；用户授权联网和执行环境的网络权限，并不等于出口代理的目标域名白名单已经生效。这次失败不能说明你的电脑也一定失败，也不能写成已经成功取得新浪行情。

代码保留 `SINA_DAILY_VERIFIED = False`。因此目前的 `--source auto` 仍先尝试东方财富；主源失败后，新浪会记作 `skipped / SourceNotVerified`，不会自动请求它。指定 `--source sina` 也不能绕过门禁。**请勿自行编辑 Python 文件开启门禁。** 独立探测成功也不会自动启用数据源，应把脱敏结果交给维护者复核。

已经实现离线测试覆盖的回退逻辑：主源成功时不用备用；主源断开、数据非法或过旧时，整批弃用后才尝试符合门禁的备用；两源都失败时返回 `unavailable` 和空数据，不保留部分成功行情。离线测试使用明确的虚构输入，不证明新浪现在可访问。

本次候选源**仅限 `515450.SH` 日线**，不扩展到 `600900.SH` 或分钟行情。单个采集批次只使用一个来源，不拼接两个来源的数据。

## 一、准备独立代码目录，不需要 Git

1. 打开 [原 Draft PR #3 的分支](https://github.com/hengha298-maker/quant-trading/tree/codex/goose-v2-p1-readonly)，在本轮更新发布到该分支后选择 **Code → Download ZIP**。
2. 把 ZIP 解压到新的本地测试目录，例如 `C:\GooseP1SinaCode`。不要解压覆盖宿舍电脑现有 V1.1 目录，也不要放在 OneDrive 等同步目录中。
3. 找到解压后包含 `README.md`、`goose_research`、`tests` 和 `requirements-sina.txt` 的 **`p1` 文件夹**。
4. 在这个 `p1` 文件夹的资源管理器地址栏输入 `powershell`，按回车。下文命令都在这个窗口执行。

电脑已有 Python 3.12 时，先执行不联网、无第三方依赖的检查：

```powershell
py -3.12 --version
py -3.12 -m unittest discover -s tests -v
py -3.12 check_private.py --all
```

这些检查不需要 Git 或 `pip`，也不需要安装新浪工具。若没有 Python 3.12，请先停止，不要为了本次测试升级或替换 V1.1 已使用的环境。

ZIP 没有 `.git` 时，隐私检查会明确显示“文件系统全量扫描”，不是 Git 暂存区检查。它只排除源码根目录的 `.git` 元数据，**不会根据 `.gitignore` 跳过 `work`、工具目录或虚拟环境**。因此工具和私人数据都应放在源码之外；不要通过修改检查器或忽略规则处理误放的数据。检查只识别可疑文件名，不能保证识别改名或嵌入源码的秘密。

## 二、可选：安装独立的新浪探测工具

只有想实际联网核验新浪时才需要这一步。工具放在当前用户的 `LOCALAPPDATA` 下，与源码、V1.1 和私人数据目录分开。无需激活虚拟环境，也无需管理员权限。

仍在上面的 `p1` 文件夹中，复制执行：

```powershell
$P1SinaEnv = Join-Path $env:LOCALAPPDATA 'GooseP1SinaTools-1.19.1'
$P1SinaPython = Join-Path $P1SinaEnv 'Scripts\python.exe'
if (-not (Test-Path -LiteralPath $P1SinaPython -PathType Leaf)) {
    py -3.12 -m venv "$P1SinaEnv"
    if ($LASTEXITCODE -ne 0) { throw '独立工具环境创建失败，请停止，不要改装到 V1.1 环境。' }
}
& $P1SinaPython -m pip install -r .\requirements-sina.txt
if ($LASTEXITCODE -ne 0) { throw '独立工具依赖安装失败，请停止，不要在 V1.1 或源码目录中重装。' }
```

已有这个工具环境时会复用它，不删除目录。依赖文件固定本轮直接依赖 `akshare==1.19.1` 和 `py-mini-racer==0.6.0`，并会安装它们需要的间接依赖。所有安装都通过 `$P1SinaPython` 执行，不向 V1.1 的 Python 安装包。包下载需要访问软件包服务；包安装成功不代表新浪域名已经可访问。

如果重新打开 PowerShell，只需重新执行前两行变量赋值，再运行下面的探测命令；不需要每次重新安装。

## 三、运行独立探测，不读持仓、不保存采集记录

```powershell
& $P1SinaPython -m goose_research.probe_sina
$P1ProbeExit = $LASTEXITCODE
Write-Host "probe退出码：$P1ProbeExit"
```

对应的模块用法是 `python -m goose_research.probe_sina [--expected-last-date YYYY-MM-DD]`。这里的 `python` 必须使用上面独立工具环境的解释器。

探测不接受 `--data-dir`，不读取持仓、Bark 或 V1 数据，不在你的持仓／行情目录保存记录，不修改源门禁。它只输出公开行情的脱敏摘要。

探测会下载绑定到 `sh515450` 的真实响应，检查其完整包装和原始日期严格升序、无重复、OHLC 与数值，再与固定版本的原 `akshare.fund_etf_hist_sina` API 逐条比较。比较在隔离子进程中传递**同一个真实下载的压缩内容**，不会生成行情，也不会用虚构价格替代真实响应。先校验原始顺序，再核对 API 结果，避免 API 排序掩盖乱序。

子进程总时限为 45 秒；下载超时为 15 秒、响应上限为 8 MB；每次本地 JavaScript 解码调用限制为 5 秒和 128 MiB。不会执行源站返回的任意脚本，只向本地固定解码器传入经过包装检查的压缩内容。

### 退出码怎样看

| 结果 | 含义 | 下一步 |
| --- | --- | --- |
| `probe退出码：0` | 这一次响应绑定、数据结构、原 API 一致性及要求截至日期通过检查 | 仅回传脱敏摘要，等待维护者审核；门禁仍不变 |
| 非零，显示 `unavailable` | 本次未取得通过核验的结果，不能用作行情或研究输入 | 保留不可用状态，根据错误类别核对依赖、网络或日期，不修改门禁 |

常见类别：`SinaDependencyError` 表示依赖缺失或版本不符；`SinaResponseError` 表示下载、包装或响应检查未通过，也可能是代理拒绝；`SinaTimeoutError` 为超时；`SinaDecodeError` 为解码或数据结构不符合要求；`SinaApiMismatchError` 为与原 API 不一致；`SinaStaleDataError` 为未达到要求截至日。终端不会打印底层私密错误全文。

即使退出 0，**复权口径仍是 `unknown`，成交量保留源站原值**；AKShare 文档称成交量单位为“手”，本项目尚未核验，不擅自乘除 100。不能据此宣布数据三年完整、费用可用或策略有效。报告对复权口径未核验的备用源只展示行情事实，不把它送入费用参考计算。

### 截至日期如何选择

默认以北京时间判断：16:00 前要求上一工作日，16:00 后可要求当天，遇周末退回最近工作日。这样排除尚未结束的当日日线，**但这不是交易所日历**；节假日或其他缺口可能被保守拒绝。

仅当你已经独立核对了“最后一个已结束交易日”，才使用 `--expected-last-date`。不要填写未来日期、当天 16:00 前的日期，也不要为了让探测通过随便改成更早日期。下面命令会先让你输入确认过的日期：

```powershell
$P1ExpectedLastDate = Read-Host '输入已独立核实的最后完整交易日（YYYY-MM-DD）'
& $P1SinaPython -m goose_research.probe_sina --expected-last-date "$P1ExpectedLastDate"
$P1ProbeExit = $LASTEXITCODE
Write-Host "probe退出码：$P1ProbeExit"
```

填写日期不代表已经完成完整交易所日历核验；输出会保留“用户指定日期”的依据。代理 403 时不需要关闭代理、放宽 TLS 校验或绕过校园网络策略，先把不可用结果留给维护者处理。

## 四、可选：对照东方财富与 auto 的保存记录

这一步与探测不同：`collect` 会在已确认的本地目录下新增**公开行情采集记录**，成功或失败都会保存，不覆盖旧记录。它不读取或上传持仓。默认参数和新增源参数如下，均可在 `p1` 目录直接使用：

```powershell
$P1Data = Join-Path $env:LOCALAPPDATA 'GooseResearchP1'
if (-not (Test-Path -LiteralPath (Join-Path $P1Data '.local-only') -PathType Leaf)) {
    py -3.12 -m goose_research --data-dir "$P1Data" init --confirm-local-storage
    if ($LASTEXITCODE -ne 0) { throw '本地数据目录初始化失败，请先检查路径及非同步存储条件。' }
}
```

先自行确认这个目录位于本地固定磁盘、没有被任何同步软件上传。已有初始化目录时跳过 `init`；首次 `init` 只确认目录并创建 `.local-only` 标记，不导入、重导或改写持仓，也不生成示例持仓。无需运行 `template` 或 `import-csv`。

选择一种命令运行即可；它们输出新保存记录的完整路径：

```powershell
py -3.12 -m goose_research --data-dir "$P1Data" collect 515450.SH --interval daily --source eastmoney
py -3.12 -m goose_research --data-dir "$P1Data" collect 515450.SH --interval daily --source auto
```

若要比较两种模式并只查看公开字段，可复制以下整段。它只读取刚返回的行情 JSON，不遍历快照、不打开持仓、不输出私人目录路径：

```powershell
foreach ($P1Source in @('eastmoney', 'auto')) {
    $P1MarketPath = py -3.12 -m goose_research --data-dir "$P1Data" collect 515450.SH --interval daily --source $P1Source
    $P1CollectExit = $LASTEXITCODE
    Write-Host "source=$P1Source；collect退出码：$P1CollectExit"
    if ($P1CollectExit -ne 0) {
        Write-Host '记录未保存；请检查本地路径或参数，不代表取得行情。'
        continue
    }
    $P1MarketSummary = Get-Content -LiteralPath ([string]$P1MarketPath) -Raw -Encoding UTF8 | ConvertFrom-Json
    $P1MarketSummary | Select-Object symbol, interval, status, source, adjustment, fetched_at, requested_start, requested_end, count, observed_start, observed_end, error_type, provider_attempts, freshness | ConvertTo-Json -Depth 6
}
```

**`collect` 退出 0 只说明记录保存成功，不等于行情成功。** 应核对保存内容：

- `status: observed` 表示该批数据通过当前结构及日期要求，仍需看实际起止日期、来源和口径，不代表三年交易日齐全。
- `status: unavailable` 表示无可用数据；`rows` 为空，成功范围、条数及成功元数据不会保留。
- 当前 `auto` 在主源失败后会显示 `sina / skipped / SourceNotVerified`，这是未启用门禁的预期结果，不是已经回退成功。
- 只有未来经过真实验收并由维护者开启门禁的版本，才会尝试新浪；若两个来源都失败，保持 `unavailable`，不合并或补造数据。仅装好可选依赖或一次 `probe` 退出 0 都不会开启它。

`collect` 同样支持 `--expected-last-date`，日期条件与探测一致；分钟采集不接受这个日线日期参数。本指南不要求修改持仓或生成涉及持仓的报告。

## 五、只回传脱敏摘要

给维护者回传 `probe` 的文字摘要及退出码即可：是否通过／不可用、错误类别、证券、原始和选定范围、条数、截至日期、采集时间、公开来源 URL、响应 SHA256，以及复权／成交量限制。它们都是公开行情或核验元数据。

不要回传真实持仓、账户现金、券商账号密码、Bark 密钥、完整私人目录路径、原始持仓 JSON，或带这些信息的终端截图。不要把 `GooseResearchP1` 整个目录打包上传。若需要补充 `collect` 结果，使用上面只选择公开字段的摘要即可。

探测和采集都只是数据基础核验，不产生交易建议，不证明日内做T有稳定收益，也不接入任何真实券商交易接口。停止使用时关闭 PowerShell 即可；不需要改变 V1.1 的任务、数据库、配置或通知。
