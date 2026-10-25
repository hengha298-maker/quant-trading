# 分享持续运行，但 iPhone Safari 打不开

已收到HTTPS `device_denied` 且Windows `check_whois_failed`：请先看[逐字段身份拒绝与一次自动预检](身份拒绝修复说明.md)。旧总码不能确定具体字段；新版无需手机重测即可在启动时给全部阻断码，另保留窗口外请求历史。

适用基线：PR #5 的 35c4440，Windows Tailscale 1.104.2。无需反复运行命令或改动私人数据。

## 已确认的修复

官方 1.104.2 的 [`tailcfg.Node.KeyExpiry`](https://github.com/tailscale/tailscale/blob/v1.104.2/tailcfg/tailcfg.go#L388) 使用 `json:",omitzero"`。禁用密钥到期时，[whois CLI](https://github.com/tailscale/tailscale/blob/v1.104.2/cmd/tailscale/cli/whois.go#L43) 会省略字段。35c4440 强制字段存在，会把这些合法设备误拒为 403。

新版只在**完整版本/状态检查已确认 CLI 为 1.104.2**时识别该省略形式；未知版本/旧版缺省仍拒绝，显式 null、错误类型、无效或过期时间仍拒绝。稳定设备ID、用户/登录、机器授权、地址、Expired、无标签及逐请求whois全部保留。没有用IP代替设备身份。

已核对官方 Serve 的路径拼接、Host保留、X-Forwarded头覆盖与源地址语义，未发现确定失配。正常whois拒绝会返回完整403；**这个确定的403缺陷不足以单独解释Safari“服务器停止响应”**，不把代码修复当作实机访问已成功。

## 现有实机证据的界限

- Windows到iPhone的Tailscale ping成功：说明有尾网连通性，不能证明iPhone到Windows的TCP8443方向可达。
- Windows访问自身TCP成功且HTTPS证书验证成功：证明Windows本机的8443/TLS可响应，不证明手机方向畅通。
- 本机403可能是正确拒绝未选Windows设备；不能据此断言手机也到达了应用。
- 域名文本一致不等于iPhone解析已成功；IP证书名称不匹配不能证明TCP不通。
- 持续“分享已启动”只表示监督检查通过，旧版并不知道手机HTTP请求是否进入后端。

## 最多两步实机复验

1. 停止旧窗口，解压PR #5最新版，启动真实只读入口并使用原有P1文件。升级后第一次需选择一次；同一窗口后续诊断/重试都保留选择，不重新导入数据。先直接用Safari打开新HTTPS地址；若已正常打开即可结束。
2. 若仍打不开，在Windows点 **“检查手机连接（同时刷新Safari）”**，然后在iPhone Safari刷新一次。等待自动结果后，点“复制诊断码”，只反馈这些固定代码即可。无需再运行ping、curl、Test-NetConnection或提供完整status JSON。

诊断不停止分享、不重新读取持仓；“停止分享/撤销授权”始终可用。首次安装、配对及停止分享的原步骤保持。

## 自动检查做什么

Windows自动复核指定设备whois，并解析电脑MagicDNS名称。只有解析结果都属于已核验的Self Tailscale地址时才连接本机8443；TLS始终保留域名SNI及正常证书验证。只发不含Cookie/配对码/正文的 **HEAD /readonly/**，不请求资产API、不读取响应正文、不使用系统HTTP代理或改动VPN/DNS设置。本机预期403只标为本机检查结果。

同一轮打开有限内存观察窗口。只有本次随机代理路径、loopback来源以及选定IP单值头满足时，才记录候选请求；之后仍需全部代理头和whois认证。Windows自检的来源不是选定iPhone，因此不会混成手机成功。直到稳定身份复核通过才标记身份成功。事件是固定码集合，不记录头值、IP、身份、URL、路径、Cookie、配对码、持仓或时间线；上一轮迟到请求不能写入新一轮结果。无新增公开诊断接口、文件或浏览器持久缓存。

| 固定码 | 能证明什么 |
| --- | --- |
| `check_local_https_ok` | Windows自身DNS、TCP、证书/TLS及应用403正常，**不是手机方向成功**。 |
| `check_local_dns_failed` / `check_local_dns_mismatch` | 本机解析失败或结果不属于Self，未向其他主机发送探测。 |
| `check_local_tcp_failed` / `check_local_tls_failed` | Windows自身对应层失败，无TLS降级。 |
| `check_whois_ok` / `check_whois_failed` | 电脑主动复核设备身份的结果，仍不证明手机请求到达。 |
| `phone_request_not_observed` | 本轮没有选定IP候选请求进入应用；尚不能区分手机DNS、TCP/TLS、VPN/代理或网络策略，不能假称其中某项已被证明。 |
| `phone_candidate_arrived` | 选定IP请求已进入后端；单凭IP还没有完成设备认证。 |
| `phone_proxy_headers_rejected` | 候选请求的代理头/安全入口不符合要求。 |
| `phone_whois_timeout` / `phone_whois_cli_failed` / `phone_whois_json_failed` | 候选请求已到应用，whois超时、命令或结构失败。 |
| `phone_identity_rejected` | 请求到达，稳定身份、权限或地址核验被拒绝。 |
| `phone_identity_verified` | 到达的请求已通过完整指定设备核验。 |
| `phone_host_route_rejected` / `phone_origin_rejected` | 完整身份后，Host/路径或Origin被拒绝。 |
| `phone_page_sent` | 应用已把配对页写给Serve，**不等于Safari已收到**；还需考虑返回路径、Serve至手机或手机系统VPN/代理。 |
| `phone_reply_failed` / `check_cancelled` | 应用回复失败，或本轮随停止分享取消。 |

如果结果仍指向手机之前的路径，Windows证据不能代替iPhone侧DNS/TCP/TLS观察，也不会伪装成环境问题已解决。保留所有HTTPS、设备身份、配对、会话、清屏及P1资格判断；不使用Funnel、公网、HTTP或放宽授权绕过。
