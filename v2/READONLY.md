# V2 real_readonly 基础版：已运行的虚构接口测试

本版在原四页外壳、CSS、导航和卡片布局上增加独立只读入口。**真实模式协议已运行，但数据提供者被锁定为代码内虚构夹具**；不是已接通真实持仓或iPhone。原公开演示入口保持不动。

## Windows最简单测试

1. 下载原PR #5分支新ZIP，解压到新目录，打开`v2`。
2. 双击 **`只读模式虚构测试.cmd`**，选择“正常历史快照”，点击“启动虚构测试”。在文件选择窗口选你已有的 **PR #3源码目录或其中的p1目录**，不要选择`GooseResearchP1`私人数据目录。
3. 浏览器自动打开`http://127.0.0.1:9876/readonly/`，输入电脑控制窗口显示的8位一次性配对码，即可操作四页、账户筛选、持仓/策略/报告详情及返回。
4. 点击电脑窗口“撤销会话 / 更新配对码”：旧会话立即在服务端失效，前台页面最迟下次15秒检查时清空数据。重新输入新码可继续测试。网页“退出并撤销会话”立即清空本页并请求撤销。
5. 点“停止分享”后，可选择“缺少行情”或“数据过期”，重新启动测试；新页面有对应中文提示。关闭控制窗口同样停止服务和会话。

**无需安装Tailscale、Node.js或Python包。** 脚本优先使用已安装的K2工具Python，否则用已有`py -3.12`；控制窗口使用官方Windows Python自带Tkinter。不改V1环境，不自动下载安装。

### P1源码版本

PR #3依赖固定到提交 **`f6c117fab964f2e627d1a64a651bab2827ff0308`**：

[下载该提交的源码ZIP](https://github.com/hengha298-maker/quant-trading/archive/f6c117fab964f2e627d1a64a651bab2827ff0308.zip)

若已有验收版源码，无需再下载。加载器核验`__init__.py`、`validation.py`、`usage.py`、`report_model.py`四个核心文件的SHA256，允许Windows CRLF，锁定这些文件与该提交一致；不依赖Git元数据、不声称已校验整个ZIP所有文件。版本不符则停止，不降级为演示/弱校验。

原校验与`build_view_model`、`market_usage`直接执行于这些已核验的P1源码，不复制进PR #5；不用已安装的同名包，不执行旧`__pycache__`。加载源码时不导入P1行情采集、数据库、Bark、存储或报告演示入口。没有个人数据目录输入。

命令行备用入口（在新ZIP的v2目录执行）：

```powershell
py -3.12 readonly_server.py --p1-code "替换为PR3源码目录" --scenario normal
```

可选`--scenario missing`、`--scenario stale`，端口冲突时可用`--port 9877`。关闭窗口或Ctrl+C停止。没有`--lan`、`--real`、`--data-dir`、`--snapshot`、代理信任或云端参数。

## 已完成的功能

- `GET /api/v1/view`：配对会话认证后返回最小化P1投影；未授权GET/HEAD均401。只提供固定的虚构数据，接口不接受路径、快照、行情或代码作为请求参数。
- 首页：总资产资格未开放、已知现金及未知覆盖、重点标的的受限历史观察、三个来源时间。
- 持仓：分账户筛选、数量、快照可卖量、成本、详情与T+1说明；未知成本/现金保留未知，真实零保留零。
- 策略：长期与日内独立，均未验证、不可操作，没有价格/数量/资金建议。
- 报告：仅当前数据批次摘要和详情，不填充旧演示报告或收益记录。
- 状态处理：未授权、会话失效/撤销、网络中断、服务关闭、错误协议/模式、缺失行情、过期历史数据、旧SW控制器，均有中文状态。失败不保留旧数据作为当前状态，不回填演示。
- 独立入口`/readonly/`不加载`demo-data.js`、`model.js`、`app.js`、manifest或SW。原演示仍通过原`preview.py`运行。

## 数据协议 v1

顶层明确`api_version=1`、`mode=real_readonly`、`test_data=true`、`p1_revision`和`fixture-`批次标识；另含：

| 字段 | 语义 |
|---|---|
| snapshot_as_of / snapshot_at | P1持仓日期；日内时刻始终null，不补造 |
| generated_at | 本次电脑发布批次生成时间；刷新不改写 |
| accounts | 重新编号的匿名显示ID、券商、完整性、现金及持仓事实；不泄露原账户代号 |
| quotes | 名称/代码、P1状态、来源说明、行情日期、采集时间、历史间隔、数值、单位及允许用途 |
| known_cash / unknown_cash_accounts | 直接采用P1投影的现金合计及未知覆盖，不在JS算资产 |
| permissions / total_assets | valuation、profit_loss、advice固定false，总资产固定null |
| strategies / reports | 两套不可用研究状态及一份当前批次摘要，不是虚构操作建议 |

金额是十进制字符串，未知是null，数量为整数。前端只格式化，不调用原演示`portfolio()`。515450未核验行情的`close_unit=null`、`historical_cost_reference=false`，只标“原始历史收盘数值 · 单位未核验”，不当成人民币参考价、估值、盈亏或交易建议。主源通过历史费用资格也不扩大估值权限，本页不计算费用。

原始行、volume历史、URL/响应哈希审计、私人路径、原账户ID、CSV、Bark和密钥不进入响应。未来真实模式将需要新的明确数据提供者及协议门禁，不能把当前test_data改成false直接通过。

“超过7自然日”只是界面历史提示阈值，不是交易日历、实时标准、质量认证或策略有效期。

## 认证与安全边界

- 固定绑定127.0.0.1，前端也仅接受本机HTTP测试地址；不能用同网脚本、反向代理或公网参数开放此入口。
- 一次性8位随机配对码，5分钟有效、最多5次失败；会话使用随机256位令牌，15分钟固定有效期，刷新不延长。认证状态只在内存，重启即失效。
- Cookie为HttpOnly、SameSite=Strict；退出、电脑撤销、服务关闭均使旧令牌失效。**当前Cookie没有Secure，因为这是本机HTTP虚构测试；不是生产认证配置。**
- 配对/退出使用两个限定POST会话端点，不写P1；数据接口拒绝POST/PUT/PATCH/DELETE等写入。严格Host、Origin、请求大小/重复字段、Cookie和白名单路径检查，无CORS通配或代理头信任。
- 所有响应no-store，前端fetch no-store；不使用localStorage、sessionStorage、IndexedDB、Cache Storage或SW保存数据/会话。检测到旧SW控制器即拒绝加载；可停止测试换新端口，不自动删除用户其他缓存。
- 页面隐藏、离开和BFCache返回会清空或重新校验；异步请求有取消与版本检查，旧响应不能在退出后回填。服务端撤销在下一次前台15秒检查生效于页面；Safari后台定时器不作实时承诺，返回前台会重新校验。
- 不记录请求内容、路径、Cookie、配对码或账户日志；配对码只显示在本机控制窗口/命令行供本人使用。服务响应不返回令牌正文。无后台任务、落盘会话或自动开机启动。

## 尚未完成，禁止用于真实持仓

**可信HTTPS、Secure Cookie、手机设备授权、Tailscale/私有网络接入、真实P1文件选择/读取及iPhone Safari实测均未完成。** 本机HTTP依然是明文，即使有认证也不适合真实数据；localhost Cookie也不是不同本机端口间的安全隔离。

因此当前入口只有代码内虚构提供者，`REAL_DATA_ENABLED`改true也会拒绝启动；没有可选的真实数据输入。正式接入必须先实现可信加密连接和设备限制，再增加调用P1原`read_private`的私有文件提供者、重验缓存与会话；不能只换夹具、关校验或开放0.0.0.0。不会要求用户本轮安装VPN或配置云端。

## 测试与截图

开发者可设`GOOSE_P1_CODE`为上述源码目录运行：

```powershell
$env:GOOSE_P1_CODE = '替换为PR3源码目录'
py -3.12 -m unittest discover -s tests -p test_readonly.py -v
```

19项Python认证/HTTP/原P1复用测试（其中1项Windows原生控制窗口）；缺少依赖源码时13项集成测试会明确跳过，不能当通过。新增专用Windows/Linux CI独立检出固定P1提交，确保实际执行。另有7项新JS协议测试和原11项模型、5项SW回归（Node仅开发测试使用）。

已有Playwright/Chromium执行13组浏览器检查：四页、持仓详情/返回、缺行情、过期、断网、服务关闭、服务端撤销自动清屏、退出、错误模式注入、旧SW拒收；验证没有演示脚本请求、无存储条目/JS可读会话Cookie、无页面异常。截图全部虚构：

[首页](docs/readonly-screenshots/home.png) · [持仓](docs/readonly-screenshots/holdings.png) · [策略](docs/readonly-screenshots/strategies.png) · [报告](docs/readonly-screenshots/reports.png) · [缺行情](docs/readonly-screenshots/missing.png) · [过期](docs/readonly-screenshots/stale.png)

最终CI证据见原Draft PR #5。测试与截图不代替Windows/iPhone真实数据验收，不构成生产安全认证。本轮没有修改PR #3、V1.1、Bark、计划任务、真实文件或券商环境，不合并、不部署。
