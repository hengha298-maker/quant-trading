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
