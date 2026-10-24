# iPhone 私网 HTTPS：真实持仓只读

Windows 双击 **`iPhone真实持仓只读.cmd`**，明确选择 P1 源码、snapshots 持仓 JSON 和对应 market JSON，授权指定 iPhone 后分享。完整新手步骤、下载地址、按钮及排障见[真实持仓手机使用说明](真实持仓手机使用说明.md)。无需先重复虚构演练；旧 `iPhone安全连接虚构测试.cmd` 仍单独保留。

## 数据入口

- 固定执行 PR #3 `f6c117fab964f2e627d1a64a651bab2827ff0308` 的 `validation.py`、`usage.py`、`report_model.py`、`storage.py` 和包入口，逐个验证 SHA256；兼容 ZIP/CRLF，不执行未验证 pyc。
- 复用原 `read_private`、`validate_snapshot`、`validate_market`、`build_view_model(demo=False)` 和 `market_usage`。只读取明确选择的文件，不扫描用户目录、不挑最新行情、不写回、不采集新行情、不拼接批次。
- P1 原 `.local-only`、本地非同步存储及非 Git 路径限制保留。持仓须来自 snapshots，所选行情须来自同一根目录的 market。最多 16 份行情、总输入 32 MB、单份沿用 P1 8 MB 上限。
- 只发送白名单投影：临时账户序号、券商显示名、数量、可卖数量、成本、现金和受限行情状态。原始账户内部 ID、本地路径、URL、原始行、来源审计不传给手机。
- 真数据使用 `test_data:false` / `private-` 批次，虚构入口使用 `test_data:true` / `fixture-`。服务器明确标记页面模式；浏览器拒绝跨模式批次。真实启动路径不导入虚构提供者，也不加载演示脚本或计算。
- 未知现金/成本保持未知。515450 新浪数据只允许原始观察，单位和复权未核验时不标人民币、不进入估值/费用/盈亏/建议。全部估值与建议权限保持 false。

## 传输和认证

- Python 只监听 `127.0.0.1` 随机端口；Tailscale Serve 在私网 HTTPS 8443 终止 TLS。仅本机 Serve→Python 使用回环代理，真实数据不会以 HTTP 发到手机、局域网或公网。
- 仅本次手动确认的同账号在线 iOS 设备可访问，授权只在内存。每个请求都核对单值代理头、HTTPS、Host/Origin、Tailscale 用户头，再由本机 `whois` 复核稳定设备 ID、用户 ID、IP、机器授权和密钥有效期。未选设备连静态页面和配对入口也拒绝。
- 随机后端代理路径不进入手机 URL；拒绝直接后端请求、重复/拼接头、跨站来源、HTTP 降级和 Funnel 标记。信任 Windows 账号、OS 和本机 Tailscale 服务，不承诺抵御已控制电脑的恶意本地程序。
- 一次性 8 位码有效 5 分钟、最多失败 5 次；256 位会话绑定设备、15 分钟固定过期。Cookie 为 `__Host-`、Secure、HttpOnly、SameSite=Strict、Path=/、无 Domain。真实与虚构 Cookie 名分开。
- Serve 仅前台运行，不用 `--bg`、Funnel 或公网绑定。发现原有 Serve/Funnel 配置即拒绝，不覆盖、不 reset。运行中每 2 秒检查前台进程、唯一 HTTPS 配置和设备状态，异常则撤销并停止。
- “撤销授权”“停止分享”及关窗先废除会话、清空内存数据引用，再关闭本机接口及本次 Serve。Windows Job Object 还负责控制进程异常退出时结束子进程。结束后核对配置，无法确认则明确提示退出 Windows Tailscale；不删除其他应用的配置。

## 手机隐私和断线

所有响应和 fetch 均 `no-store`。不写 localStorage/sessionStorage/IndexedDB/CacheStorage，不注册 Service Worker。页面若被旧 SW 控制会在读取数据前拒绝。Cookie 只用于短期认证，不储存持仓。

401/403、读取失败、8 秒超时立即清空持仓状态、DOM 和弹窗；迟到响应不能回填。网络离线事件、隐藏和离页也清屏，返回前台重新验会话。前台每 15 秒检查；电脑撤销或停止后下一轮检查清屏，慢请求最多再等 8 秒。手机退出若不能确认服务器撤销会明确提示，不声称已撤销。

本版没有持仓离线浏览、云端同步、手机修改、自动交易或 Bark 接入。截图及系统任务预览不是应用能撤回的缓存。Tailscale 自身有正常设备/连接元数据，HTTPS 证书透明日志可能公开电脑的 ts.net 主机名，不包含持仓；电脑名不要含账户号码。

## 运行前提及尚待本机验收

用户本人完成官方安装、两端同账号登录、iPhone VPN 允许、后台 MagicDNS/HTTPS Certificates 启用和私人文件选择。正常 Serve 不需向 Safari 导入证书；有证书警告时停止，不忽略错误、不改 HTTP。程序不自动登录，不改 ACL、防火墙、路由器或任务，不申请 API 密钥。

云端只使用临时虚构文件、CLI/设备替身和本机 TLS 替身执行测试。Windows/iPhone 实际 Tailscale、证书及真实 JSON 显示需用户实机验收，不能把替身结果称为实际隧道或 Safari 验收。

[验证记录](VALIDATION.md) · [官方 Serve 文档](https://tailscale.com/docs/features/tailscale-serve) · [HTTPS 证书说明](https://tailscale.com/kb/1199/tailscale-certificates)

PR #3 和 PR #5 保持 Draft；不合并、不部署公网，不改 V1.1、Bark、定时任务、P1 原文件或券商。
