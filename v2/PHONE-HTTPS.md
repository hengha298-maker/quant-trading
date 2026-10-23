# iPhone 安全连接：第二阶段，仅虚构数据

本轮交付可运行的 **Tailscale私网HTTPS + 指定设备授权 + 配对会话**。四页、P1源码校验和515450观察限制沿用第一阶段；没有读取真实持仓。`real_readonly`是接口模式，**`test_data: true`仍强制开启**，不代表真实账户已接通。

## 第一次：先只完成安装和连接

1. Windows安装[官方Tailscale](https://tailscale.com/download/windows)，iPhone在App Store安装Tailscale；两端登录**同一个自己的账号**。已有安装无需重装；Windows要求正式版1.90.9或更新版本，推荐当前正式版。
2. 在iPhone允许添加VPN配置，打开Tailscale并连接；电脑也保持已连接。使用自己的设备，本版不支持分享节点、标签设备或组织账号。
3. 在[管理后台DNS页面](https://login.tailscale.com/admin/dns)确认开启MagicDNS及HTTPS Certificates。只开启HTTPS证书，**不启用Funnel**。然后回到电脑。

不需要路由器端口映射、防火墙放行Python、同一个Wi-Fi、Tailscale API密钥或券商密码。Tailscale登录和VPN安装由你在官方应用完成，本程序不会自动登录、申请密钥或更改账号/ACL。

## 第二次：启动虚构测试

1. 将最新PR #5 ZIP解压到**新的独立文件夹**。在`v2`双击 **`iPhone安全连接虚构测试.cmd`**；保留已有P1源码目录，不覆盖P1安装。
2. 点“刷新设备”，选择自己的在线iPhone。点“启动虚构HTTPS”，在确认框核对设备名及Tailscale IP，然后选择第一阶段用过的 **P1源码目录或其中的p1目录**，不是私人数据目录。
3. 点“复制手机地址”，将这个**不含凭据的地址**发送给自己的iPhone。手机用Safari打开`https://电脑名.私网名.ts.net:8443/readonly/`，输入电脑窗口8位配对码。

启动脚本优先复用`%LOCALAPPDATA%\GooseP1SinaTools-K2-1.19.1\Scripts\python.exe`，否则使用已有`py -3.12`。不运行pip，不需要Git、Node、新Python环境或重新安装K2依赖。P1仍锁定`f6c117fab964f2e627d1a64a651bab2827ff0308`四个核心文件及SHA256，支持ZIP/CRLF。

首次只检查：首页标明“仅虚构测试”，四页可切换；515450仍不能估值、算盈亏或给交易建议。Safari有证书错误时停止验收，**不要忽略证书警告或改用HTTP**。后续可用Safari“分享→添加到主屏幕”，它只是入口，没有离线数据功能；独立窗口可能需要重新配对。

## 第三次：验证撤销并停止

1. 电脑点“撤销会话 / 新配对码”。手机前台约15秒内应显示未授权，并清空原持仓和现金；慢请求最长另有8秒超时。也可点手机首页“重新读取当前批次”立即检查。
2. 可重新配对一次，再在电脑点“停止分享”。控制窗口应确认本机接口及Serve关闭；手机再次读取应清空数据并显示断开。**最后关闭控制窗口**，无需卸载Tailscale。
3. 若有另一台**未选定**的同私网设备，可打开同一地址：应被403拒绝，不能配对或获取数据。只有一台iPhone时，此项已有虚构设备自动化测试，仍不计作你的实机验收。

切后台/离页即清空DOM，回前台重新检查。停止分享不能撤回截图或手工保存的内容；操作系统预览图不属于应用可控制的缓存。为了观察清屏，请把Safari保持前台。

## 已实现的安全边界

- **私网HTTPS限定8443**：Tailscale负责证书和加密；Python只监听`127.0.0.1`随机端口。没有LAN/公网绑定、Funnel命令、端口映射、云端或开机任务。
- 仅本次电脑手动确认的**同账号在线iOS设备**可访问，授权只在内存。每个请求检查Serve覆盖的单值X-Forwarded头及用户头，并通过本机`tailscale whois --json`重新核对稳定设备ID、用户ID、IP、授权状态和密钥有效期。不能只凭IP或同账号放行；未选设备连页面/配对入口也拒绝。
- 每轮随机代理路径只配置给Serve，不进入手机URL。固定Host/Origin及路由白名单，拒绝HTTP降级、重复/拼接代理头、Funnel标记、直接后端路径及跨站请求。信任边界包含Windows账号、操作系统及本机Tailscale服务，不能抵御已控制电脑的本地恶意程序。
- **双层授权**：指定设备之后还需8位一次性配对码（5分钟、最多5次失败）。会话256位随机，绑定稳定设备ID，15分钟固定到期，不因刷新延长；Cookie为`__Host-`、Secure、HttpOnly、SameSite=Strict、Path=/、无Domain。
- 所有响应与fetch均no-store。手机入口不加载demo-data.js/演示计算、不注册SW、不把数据或凭据写入localStorage/sessionStorage/IndexedDB/CacheStorage。401/403/断线/8秒超时清屏；旧异步响应不能回填。原公开演示入口及其缓存独立保留。
- **Serve前台受控**：不用`--bg`。启动前若已有Serve或Funnel配置直接拒绝，不覆盖、不reset。启动后核对唯一前台HTTPS代理配置；每2秒检查进程、配置与授权设备，异常即撤销会话、清空数据并停止。
- 关闭窗口/停止按钮先关数据和会话，再结束本次Serve；Windows Job Object还会在控制进程异常退出时终止子进程。关后再次查配置确认；若不能确认，会明确提示，**不声称HTTPS已关闭**。此时退出Windows Tailscale确认断开，再处理残留；程序不会擅自删除他人的Serve配置。
- 不打印原始CLI输出、身份清单、Cookie、请求正文或报告；设备信息和配对码仅显示在电脑控制窗口。Tailscale自身仍维护正常设备/连接元数据和证书，本程序不能承诺第三方零日志。

Tailscale需要联网访问登录/协调及证书服务；连接可能经端到端加密中继，宿舍网络限制可能使连接失败，程序不会绕过限制。公开证书透明日志可能记录电脑的`*.ts.net`主机名，**不含本程序持仓数据**；电脑名不要含账户号码。个人套餐限制以[官方定价](https://tailscale.com/pricing)为准；本程序不创建付费资源或订阅。

## 自动化测试和未验收部分

- `tests/test_phone.py`：21项，仅虚构CLI/设备 + 原P1夹具；代理头伪造、错设备/盗Cookie、IP重分配、过期、跨站、投影、前台配置、失败清理、运行中配置改变等。Windows另实际执行原生控制窗口及Job关闭子进程测试。
- `tests/phone-browser.cjs`：11组；仅本机临时自签名TLS代理替身，检查Secure/HttpOnly Cookie、四页、空存储、过期/撤销清屏及403清屏。**测试上下文忽略临时测试证书错误；正式启动器没有忽略证书选项。替身不是Tailscale隧道，更不代表iPhone验收。**
- 原19项接口/P1复用测试、23项JS协议/演示回归、原预览和13组浏览器检查继续保留。Windows/Linux专用CI固定检出P1源码，不把依赖缺失的跳过记作通过。

开发者在`v2`目录、已有环境运行（普通用户无需执行）：

```powershell
$env:GOOSE_P1_CODE = "P1源码目录"
py -3.12 -m unittest discover -s tests -p test_phone.py -v
```

**仍未完成：你的Windows/iPhone实际Serve证书与连接验收、实际第二设备拒绝验收、真实P1文件提供者及真实数据隐私验收。** 当前云环境没有私人VPN；本轮没有登录任何tailnet、启动真实Serve或读取真实持仓，不把TLS替身结果说成真实通道验收。

下一阶段只有你确认本轮虚构HTTPS验收通过后，才单独接入P1受限的`read_private`真实文件提供者，继续白名单投影及515450单位/复权限制。不能改一个开关替换成本机真实数据；`REAL_DATA_ENABLED=True`仍拒绝启动。

## 技术依据

已核对[官方Serve文档](https://tailscale.com/docs/features/tailscale-serve)、[CLI/停止命令](https://tailscale.com/docs/reference/tailscale-cli/serve)及[HTTPS证书说明](https://tailscale.com/kb/1199/tailscale-certificates)。依据的[代理源码v1.90.9](https://github.com/tailscale/tailscale/blob/v1.90.9/ipn/ipnlocal/serve.go)会覆盖Forwarded头、清除伪造用户头并标记Funnel；[前台配置](https://github.com/tailscale/tailscale/blob/v1.90.9/ipn/serve.go)与[whois CLI](https://github.com/tailscale/tailscale/blob/v1.90.9/cmd/tailscale/cli/whois.go)也逐项核对。运行时结构不符合预期即拒绝，不猜测兼容或降级。

PR #3与PR #5保持Draft，不合并、不部署公网，不改V1.1、Bark、计划任务、私人文件或券商环境。
