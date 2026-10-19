# Windows A 股自动模拟交易系统 V1.1

**只有模拟交易，没有实盘功能。无需、也不应提供任何券商密码或交易凭证。**
支持长江电力 `600900.SH`；红利50 ETF 未确定，默认禁用，绝不预填猜测代码。


## 已有 V1：Windows 升级与 Bark 配置

本版本保留原有模拟账户、风控、交易规则与任务名，运行时间仍为**周一至周五北京时间14:50**。Bark 通知在交易事务完成后发送，失败不回滚账户、不重新下单、不改变交易退出码。无需编写 Python。

1. **等本 PR 合并后**，在原安装目录打开 PowerShell。先暂停任务并确认没有运行中的程序，再备份整个数据目录：

   ```powershell
   Disable-ScheduledTask -TaskName 'QuantTrading-Paper-V1'
   New-Item -ItemType Directory -Force .\backups | Out-Null
   Copy-Item .\data ('.\backups\data-' + (Get-Date -Format 'yyyyMMdd-HHmmss')) -Recurse
   git switch main
   git pull --ff-only
   powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install.ps1
   ```

   ZIP 安装用户：下载合并后的 main，覆盖**原目录中的代码文件**，不要删除或覆盖 `data`、`config.json`、`bark.local.json`，然后运行上面的安装命令。不要换目录或初始化新账户。安装失败时保持任务停用，修复后再继续。

2. iPhone 安装并打开 Bark、允许通知。在项目目录创建本机配置：

   ```powershell
   Copy-Item .\bark.example.json .\bark.local.json
   notepad .\bark.local.json
   ```

   只在第一次配置时复制，已有文件直接编辑。`server` 填 Bark 的 HTTPS **服务器根地址**（官方服务为 `https://api.day.app`，也支持自建 HTTPS 域名）；`device_key` 填 Bark App 显示的设备密钥。若 App 给出 `https://服务器/设备密钥/…`，将服务器根地址和密钥分开填写，不要把完整推送链接填入 server。保持 JSON 双引号和逗号有效。

   `bark.local.json` 已被 Git 忽略，程序不会输出其内容。不要截图分享密钥，不要使用 `git add -f` 提交它。配置必须可被运行计划任务的当前 Windows 用户读取；不要放进交易参数 `config.json`。

3. **只测试通知，不读取行情、不打开数据库、不产生模拟交易**：

   ```powershell
   .\.venv\Scripts\python.exe -m quant_paper.cli notify-test
   ```

   终端显示 `Bark 通知：成功`，并在 iPhone 收到测试消息后继续。`未配置` / `失败` 的退出码为1；检查 JSON、密钥、iPhone 通知权限、网络及服务器可用性。服务端接受成功不保证 iPhone 必然展示通知。

4. 更新同名任务的启动参数并恢复自动运行（**仍是14:50，没有改变触发时间**）：

   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\schedule.ps1
   .\.venv\Scripts\python.exe -m quant_paper.cli status
   ```

   `status` 只查看账户，不推送。确认原有持仓与现金仍在。不要为了测试通知执行 `run`，交易时段执行它会正常触发策略。

### V1.1 Bark 历史保存修复：安全升级

本修复默认发送 `isArchive=1`，新通知会请求保存到 Bark App 历史；此前未归档的消息不会补回。旧配置无需修改。可在本机 `bark.local.json` 增加 `"is_archive": false` 关闭，或设为 `true` 开启；必须使用 JSON 布尔值，不能写字符串或数字。环境变量 `BARK_IS_ARCHIVE=1` / `0` 优先覆盖此字段。若服务器和密钥均来自环境变量，仍不读取私密文件，此时使用该环境变量控制历史，省略则开启。

仅升级本修复时，**等待审核合并 main 后**，在非运行时段手动停用 `QuantTrading-Paper-V1`，确认程序已结束，再备份 `data`（包含 SQLite 及可能存在的附属文件）、`config.json` 和 `bark.local.json` 到本机私密位置。记录原提交，工作区有改动时先处理，勿用强制覆盖命令。

```powershell
git rev-parse HEAD
git status --short
git switch main
git pull --ff-only
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m quant_paper.cli notify-test
.\.venv\Scripts\python.exe -m quant_paper.cli status
```

确认测试通过、iPhone 收到测试通知且 Bark App 历史可见、原账户现金和持仓正确后，手动启用原任务。本修复没有依赖或数据库迁移，不需要重新安装、运行 `schedule.ps1` 或重建14:50任务。ZIP 用户仅覆盖本 PR 涉及的代码、测试及文档，保留本机配置和数据。不要为验证通知执行 `run`。失败时保持任务停用，恢复原代码版本并排查；不要重置或用旧数据库覆盖已有成交。不要上传备份、密钥或完整推送地址。

### 推送规则与环境变量

- 计划任务使用 `run --notify always`：每次完成都发中文结果，含日期、状态、当次模拟成交方向/代码/数量/价格/手续费及账户现金；观望、风控拒单、当天重复委托和休市也会报告。重复运行不会把历史成交冒充本次成交。
- 手动 `run` 默认 `--notify auto`：交易时段的结果照常推送；非交易日/非交易时段的正常跳过不推送。异常仍尽可能通知。`--notify never` 可关闭该次所有通知。
- 无配置则禁用推送，保留 V1 行为。配置错误或 Bark 网络/API 错误只记固定的“失败”，不记录地址、密钥、请求正文或服务端原始错误；通知超时为连接3秒、读取5秒，不重试、不跟随重定向。行情或交易异常仍退出1，不因通知成功而掩盖错误。
- 若多个标的中一笔已经提交、后续发生异常，错误报告会保留本次已完成的委托信息；已有成交不会回滚。异常详情见本机 `logs/paper.log`。
- 也可使用本机环境变量 `BARK_SERVER`、`BARK_DEVICE_KEY`（分别覆盖同名文件字段）；两项均设置时无需配置文件。请使用 Windows 的“编辑账户的环境变量”界面填写，避免把密钥留在 PowerShell 历史中。计划任务进程必须能继承这些变量，修改后可能需要重新登录 Windows，因此优先推荐本机 JSON 文件。`.env` 文件不自动加载。
- 高级用户可用 `--bark-config` 指定另一本机文件；相对路径按当前工作目录解析，计划任务工作目录固定为仓库目录。请保证任何自定义密钥文件同样不提交 Git。

## Windows 首次安装

1. 安装 **Python 3.12 64 位**（[官网](https://www.python.org/downloads/windows/)），保留 Python Launcher；下载此仓库代码并解压到固定目录。
2. 在仓库目录右键打开 PowerShell，粘贴以下命令。脚本安装依赖、生成配置并自动运行测试，无需编写 Python：

   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install.ps1
   powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run.ps1
   ```

3. 如需自动运行：Windows 时区设为 **(UTC+08:00) 北京**，执行：

   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\schedule.ps1
   ```

每天北京时间 14:50（周一至周五）运行；程序另外检查上交所交易日，节假日跳过。任务以当前用户普通权限执行，无需密码；电脑必须开机、联网、用户已登录，不能处于睡眠状态。错过运行不补做历史成交。任务名 `QuantTrading-Paper-V1`。移动代码目录后需重新配置任务；重复配置会更新同名任务。

停用自动任务：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\schedule.ps1 -Remove
```

这里的 Bypass 只对本次 PowerShell 进程有效，不修改系统执行策略。企业电脑若禁止脚本，应遵守管理员设置。

## 运行与查看

单次运行也可用 `.\.venv\Scripts\python.exe -m quant_paper.cli run`。
查看账户与委托：

```powershell
.\.venv\Scripts\python.exe -m quant_paper.cli status
```

- `data/paper.sqlite3`：现金（分）、仓位（股/份）、委托、虚拟成交、事件日志。`status` 中金额除以100得到元。
- `logs/paper.log`：轮转运行日志，保留交易异常详情及脱敏后的通知状态。失败退出码1；正常运行或跳过退出码0。
- `FILLED` 表示**虚拟成交**；成交标签固定 `PAPER_SYNTHETIC_MINUTE_PRICE`，不是券商或交易所成交。
- `DUPLICATE` 当天已经委托；`INSUFFICIENT_CASH` 资金不足；`POSITION_LIMIT` 超仓位；`T_PLUS_ONE` 当天买入不可卖。
- 非交易日/非交易时段显示 `SKIP_...`，正常且不会下单。
- `events` 表记录信号、参数、行情源时间及价格和错误，可用 SQLite 查看工具打开；无需修改数据库。

备份前停止计划任务、关闭运行程序，复制整个 `data` 目录（包括可能存在的 `-wal`、`-shm` 文件）。恢复时放回原目录。不要运行中复制单独的 SQLite 主文件。修改初始资金不会覆盖已有账户；需要全新实验时先停任务备份，再改 `--database` 指定另一个文件，不能删除旧账户后冒充连续运行。

## 配置（只需编辑 JSON，无需写 Python）

安装自动复制 `config.example.json` 为 `config.json`，再次安装不覆盖。普通记事本即可编辑，保存有效 JSON；不支持注释。

| 参数 | 默认 | 含义 |
|---|---:|---|
| mode | paper | 唯一允许值；live 等值报错 |
| initial_cash | 100000 | 新数据库初始模拟现金，元 |
| etf_symbol | null | 待确认后填写完整代码，格式 `六位数字.SH` / `.SZ`；只支持境内股票类ETF |
| lookback | 20 | 上一交易日及之前20个交易日的收盘均线 |
| order_lots | 1 | 每次买入手数，每手100股/份 |
| max_position | 0.30 | 每个标的买入后市值不得超过扣手续费后账户权益30% |
| commission_rate | 0.0003 | 买卖佣金率 |
| min_commission | 5 | 单笔最低佣金，元；ETF也默认沿用，按需调整 |
| stock_sell_tax | 0.0005 | 仅股票卖出印花税示例 |
| stock_transfer_rate | 0.00001 | 仅股票双向过户费示例 |
| max_quote_age_seconds | 120 | 源行情最大年龄，允许1至300秒 |

费率是可配置模拟假设，不能作为任何券商收费承诺。ETF不收本程序的股票印花税和过户费。含ETF时最多两个标的，各30%上限意味着默认总买入仓位上限约60%；市场价格变动后可以超限，系统只限制新买入，不自动强制降仓。请勿在有持仓时移除原来的ETF配置，否则系统会阻止交易直到恢复配置。ETF市场后缀与产品身份需自行确认，本程序不验证基金名称或投资范围。

## 信号、成交和安全边界

1. 使用 `exchange-calendars` 的 `XSHG` 日历，以 `Asia/Shanghai` 处理时间。未知日期或超出日历范围直接报错，绝不只凭周一到周五推断交易日。当前锁定版日历覆盖至 **2026-12-31**，跨年前需要更新依赖并重新测试。
2. AKShare 获取东方财富不复权日线和当日1分钟行情。策略仅使用**上一交易日及之前**收盘：上一日收盘高于均线买入100股/份；低于均线卖出全部持仓；相等观望。不是交叉策略，因此连续高于均线时每天可能继续买入，直到仓位/资金限制阻止。仅作透明工程示例，没有盈利保证。
3. 只在北京时间09:32–11:29:59、13:02–14:59:59接受当日新鲜行情。时间戳来自行情源，禁止使用抓取时间替代。价格非正/NaN、成交量非正、历史交易日缺失、过期/未来行情、数据源异常均记录错误并停止。先检查全部标的数据，再开始下单；下单前按实际耗时重新检查行情年龄和交易时段。没有缓存回退，没有补跑历史日期或回测成交入口。
4. 成交模型为**运行时按最近分钟价格立即生成虚拟成交**。日线只产生信号，绝不用于当日成交价。分钟数据可能延迟或尚未完结；模型不保证该价可实际成交，不模拟订单簿、排队、滑点、部分成交、涨跌停封单和交易所撮合。停牌等通常由无新鲜正成交量行情拦截，但没有独立停牌/涨跌停状态验证。
5. 使用整数分结算和四舍五入手续费；只买整手100股/份，不允许融资、负现金、做空。股票和ETF均保守采用T+1，包含较早仓位时只要当天增持也整仓锁定当日卖出；不支持T+0 ETF、零股、分红送股、拆合股或复权处理。
6. 每标的每天最多一笔委托（含拒单），数据库唯一约束和写事务保护重复/并发运行；拒单后当日不自动再试。没有挂单队列，委托终态只有 FILLED / REJECTED。现金、仓位、订单、成交原子保存；重启不会重置账户。
7. 无券商SDK、凭证入口、实盘开关。`mode` 不是paper即拒绝启动。

## 依赖与开发验证

优先采用 AKShare（MIT）、exchange-calendars（Apache-2.0）、SQLite/Python标准库、Requests（Apache-2.0）、pytest（MIT）和 tzdata（Apache-2.0，内含时区数据授权声明）。完整版本在 `requirements.lock`；上游及传递依赖许可证见 [THIRD_PARTY.md](THIRD_PARTY.md)。行情数据使用权与软件许可证不同，请遵守数据服务条款，不批量转售行情。

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pip check
```

自动测试使用固定数据，不依赖真实行情、不生成真实委托；覆盖资金不足、缺失/过期行情、非交易日、重复与并发委托、恢复、T+1、费用、仓位、故障回滚和数据适配器。GitHub Actions 配置了 Windows/Linux Python 3.12 测试。详见 [TEST_RESULTS.md](TEST_RESULTS.md)。

已知限制：用户已在 Windows 完成 V1 自动模拟买入；V1.1 的真实 Bark iPhone 到达测试仍需在本机配置后通过 `notify-test` 验证。自动测试使用模拟 Bark 响应，不需要或读取真实设备密钥。数据服务可能变更字段、延迟、限流或停服，失败时系统会拒绝下单，请查看日志，不要关闭过期保护。需要先在自己的Windows电脑安装并观察运行日志，再长期自动运行。
