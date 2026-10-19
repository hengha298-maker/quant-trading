# V1.1 Bark 历史保存修复验证

验证日期：北京时间 2026-10-19；Linux、Python 3.12，使用 requirements.lock 锁定依赖。

- 通知专项 `python -m pytest -q tests/test_notify.py`：42 passed。
- 全量 V1.1 回归 `python -m pytest -q`：79 passed，保留原68项并新增11项参数化用例。
- `python -m pip check`、`git diff --check`：通过。
- 覆盖默认归档、JSON 开关、严格布尔值校验、环境变量覆盖及无文件模式；保留中文标题与正文、成交价格/数量/手续费/现金/异常提醒测试；通知超时后成交不变、同日再次运行不重复委托、每次只发送一次。
- 所有请求使用虚构测试密钥和模拟响应，没有访问真实 Bark 服务。iPhone 收到通知和 App 历史可见需由用户升级后运行 `notify-test` 验证；旧通知不能补回。
- 仅修改通知配置/参数、测试、配置示例与文档；没有修改交易策略、SQLite、CLI、Windows 脚本或14:50任务。
- 本地未在 Windows 主机实测；远程 Windows/Linux 结果以独立修复 PR 的 Checks 为准。

以下为此前版本的历史验证记录。

# V1.1 Bark 验证记录

验证日期：北京时间 2026-10-16；Linux、Python 3.12、依赖沿用 requirements.lock。

- `python -m pytest -q`：**68 passed**（原 V1 37项全部保留）。
- `python -m pip check`、`git diff --check`：通过。
- `python -m pip install --no-deps -e .`：安装成功，版本0.1.1。
- 新增31项用例：Bark JSON/环境变量优先级、配置校验和脱敏、中文POST正文、连接/读取超时参数、禁止重定向、HTTP/API/JSON/连接失败；买入/卖出/手续费/现金报告；观望、拒单、重复委托、非交易时段与非交易日的通知策略；行情/配置/数据库/日志目录异常通知；通知失败或格式化失败不影响成交与重复保护；部分标的失败时报告已提交成交；独立通知测试不读取行情、不创建账户、不改动现有账户。
- 所有 Bark 请求使用模拟响应，未使用真实设备密钥、未发送真实手机推送。真实 iPhone 展示需由 Windows 本机 `notify-test` 验证。
- GitHub Actions 继续在 `ubuntu-latest` 和 `windows-latest` Python 3.12 上运行完整测试，并新增依赖一致性检查；远程结果以本 PR 的 Checks 为准。
- 计划任务仅新增 `--notify always` 启动参数，14:50触发时间、任务名、用户权限和并发策略不变。没有连接券商或修改数据库结构。

以下保留 V1 开发时的历史记录（不代表 V1.1 当前 CI 状态）。

# V1 验证记录

验证日期：北京时间 2026-10-16。环境：Linux、CPython 3.12；AKShare 1.19.1、exchange-calendars 4.13.2、pytest 8.4.2。运行时依赖版本见 requirements.lock。

- `python -m pytest -q`：**37 passed**。
- `python -m pip check`：**No broken requirements found**。
- `python -m pip install --no-deps -e .`：安装成功。
- CLI `status`：新账户100000元、无持仓/委托/成交。
- CLI `run`：开发时北京时间已不在交易时段，正确返回 `SKIP_OUTSIDE_SESSION`，账户无变化。
- `git diff --check`：通过。

测试包含：资金不足（计入手续费）、单标的仓位限制、重复订单、两个连接并发运行只成交一次、持久化恢复且不重置资金、缺失历史行情、历史窗口不足、过期/未来/错代码/NaN/无成交量行情、数据源异常、所有标的先校验、耗时后重新检查行情年龄、北京时间转换、非交易日/非交易时段、真实XSHG日历国庆休市、日历越界拒绝、股票T+1、买卖费用、成交写入异常导致整笔事务回滚、股票与ETF适配器字段、CLI账户恢复和live模式拒绝。

真实行情网络端到端测试：**未执行**，当前网络策略未开放东方财富域名；不使用伪造行情宣称联网成功。适配器接口签名已与安装的AKShare版本核对，并以固定DataFrame测试字段转换。

Windows：**尚未在Windows主机实测**。已提供PowerShell安装/运行/定时任务脚本，以及Windows/Linux Python 3.12的GitHub Actions工作流；未宣称远程CI已通过。Windows用户执行安装脚本时会在本机重新运行同一套测试。

这些测试验证软件规则，不证明策略收益，也不证明实际市场可以按虚拟价格成交。
