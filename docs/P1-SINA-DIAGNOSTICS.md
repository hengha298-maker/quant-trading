# P1 新浪阶段诊断与第126行版本核对

## 已核实的代码事实

本轮未根据HTTP200后的`SinaResponseError`猜测网络或再次改包装正则。逐个核对原PR已发布提交：

| 提交 | sina.py第126行 | 实际含义 |
|---|---|---|
| bdc5afe | `raise SinaResponseError()` | 第124行旧整段包装正则不匹配，第125行判断为空后抛错，尚未进入解码 |
| ddd0297 | `if length is not None and (not re.fullmatch(r'[0-9]+', length) or int(length) > MAX_BYTES):` | 校验Content-Length格式和上限，真正抛错在第127行 |

如果同一次调用确实执行ddd0297，且`length`是字符串`"17915"`、`MAX_BYTES=8000000`，这个条件必为False。实际字节数相等也不会触发后面的长度不一致判断。但单凭行号不能证明用户加载的代码版本，不能直接断言是旧目录、缓存或网络故障。新增诊断用于核对实际执行路径。

两版旧源码（UTF8、统一换行）SHA256：

- bdc5afe：`0e0d35b4d7875f90ec40c1abacdb108cbd48c6ae90e68f969ddaf0769aa1881c`
- ddd0297：`3b33b71b82df75b83098252aea358f9b72ea89ef3d962f9352d7ff74961e80ca`

## 一条Windows命令

重新下载原PR分支ZIP并解压到新的独立目录，在`p1`文件夹地址栏打开PowerShell；复用已安装工具，不需要Git或pip：

```powershell
& (Join-Path $env:LOCALAPPDATA 'GooseP1SinaTools-K2-1.19.1\Scripts\python.exe') -m goose_research.diagnose_sina
```

只输出脱敏JSON。正常行情摘要与诊断入口分开，诊断没有持仓目录参数，不读私人数据、不保存行情或报告、不改门禁。`status=observed`退出0，否则退出1；没有以诊断结果自动启用来源的路径。

## 如何读结果

先看`failed_stage`和`failed_check`，看同阶段`metrics`，而不是只看异常类或行号：

| 阶段 | 检查示例 | 含义 |
|---|---|---|
| input | dates | 本次截至日期参数校验 |
| http | open、identity | 请求打开、状态码及最终URL是否匹配（不输出实际URL） |
| length | header_format、header_parse、declared_limit、read、body_limit、exact_match | 头类型/ASCII数字、整数解析、声明上限、读取、实际大小及精确相等 |
| wrapper | utf8、assignment | UTF8和完整的证券赋值包装 |
| trailer | limit、boundary、content | 尾注长度、完整边界、嵌套/可打印字符 |
| encoding | header、wrapper_match | K2/KL压缩头类型/版本与包装一致 |
| decode | dependencies、engine、static_decoder、payload、rows | 运行库、本地固定解码器、价格/日期/量的原始序列检查 |
| api | compare | 与原AKShare API逐行核对 |
| freshness | last_date | 实际最后日期是否达到本次要求 |
| summary | validate | 原有成功摘要二次校验 |
| worker | launch、protocol、code_identity、timeout | 子进程启动、协议、版本不一致或未产生阶段信息的超时 |

`stages`中的passed才是通过；`not_reached`不是成功。HTTP/读取/解码超时会保留最后已刷新阶段，不把“未完成”当“通过”。异常消息、堆栈、私有路径和响应内容不进入诊断。

`code.revision`及`sina_implementation`为`sina-stages-v1`。同时输出实际源码SHA256、实际执行函数字节码指纹、下载函数起始行、Python版本、平台和实际`max_bytes`，不输出模块绝对路径。源码缺失时明确为null；字节码指纹保留实际加载代码的证据，不能仅凭磁盘文件声称执行版本相同。指纹移除编译文件名；父子进程必须匹配，否则不可用。不同Python版本的字节码指纹不必相同。

`known_sina_source`仅匹配上面两版历史源码；新版出现`unlisted`不是失败，要结合版本标识、源码指纹及`worker_matches_parent`判断。全程不依赖Git，因此ZIP使用同样有效。

## 隐私、校验和测试

诊断事件仅允许固定阶段/检查枚举、布尔值、有限计数、公开行情日期与代码指纹。完整响应、压缩串、注释正文、原始头值、价格、Bark密钥、环境变量、实际URL、账号及本机路径不在允许字段中。子进程第三方输出重定向到空设备，父进程严格校验事件协议；异常或未知字段失败关闭。进度仅写入有大小上限的临时协议流，关闭即清理，不生成持久日志。只要回传输出JSON即可，不需要额外截图或原始文件。

没有再次修改证券包装正则。长度校验拆成等价的明确步骤：保留严格ASCII数字/整数/上限/实际字节数检查，不strip头、不忽略长度不一致；缺失长度保留null，不变为零。尾注、编码、解码、API和新鲜度标准均不降低。

新增13项离线诊断测试，覆盖17915/17915、异常头/过大声明/截断读取、错误包装/尾注/编码、解码/API/新鲜度失败、子进程超时保留进度、协议注入/伪成功、父子版本冲突、敏感内容金丝雀，以及实际无Git ZIP副本/cp1252子进程。现有真实K2解码器样本也通过阶段钩子核验，仍是明确虚构的回归数据，非真实515450行情。

本地新浪相关44项通过，原PR分支完整pytest199通过、6项Windows专用跳过；Windows/Linux CI结果见原PR Checks。当前用户Windows真实失败阶段尚待此命令返回证据，不能以云环境状态代替用户本机结论。

本轮仅改P1及相关文档，不改V1.1、Bark、任务、V2、私人数据、原依赖锁或CI配置，不合并、不部署。`SINA_DAILY_VERIFIED=False`保持关闭。
