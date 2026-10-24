# V2 真实持仓只读验证

## Tailscale 1.104.2 监督修复

基于 98d5425 增加固定诊断及元数据误停修复，见[复验说明](连接自动停止复验.md)。本地完整 pytest：**153通过，5项Windows原生专项在Linux跳过**。新增监督测试12项：Linux11通过/1项原生窗口重试与诊断复制专项跳过；覆盖15种监督失败分支及撤销清理，另核对未知异常不进入诊断、完整身份/IP集合不变。真实模式浏览器14组继续通过，0演示请求、0页面异常。原P1源码及真实文件校验保持不变。

专用Windows/Linux CI同时执行监督专项；没有把源码契约/虚构CLI或TLS替身结果当作用户Windows Tailscale/iPhone实机验收。

以下为前一轮真实接入基线结果，继续保留。

基线为 PR #5 `d3ad1bf`，P1 依赖为 PR #3 `f6c117f`。没有访问用户真实持仓或 tailnet。

## 本次本地结果

- 完整 pytest：142 通过，4 项 Windows 原生专项在 Linux 跳过。包含原 V1.1/Bark 回归和全部 P1 源码依赖测试。
- 新增 test_private.py：18 项，Linux 17 通过/1 项 Windows 原生真实启动窗口跳过；专用 CI 在 Windows 运行该项。临时虚构 JSON 验证选定读取、原文件哈希不变、未知与零、原 P1 函数复用、OHLC/未来日期/JSON重复键及NaN拒绝、缺失和歧义行情、路径/大小限制、无演示导入、真实接口设备绑定及停止清理。
- 原 test_readonly.py、test_phone.py、test_preview.py 全部保留；错码/重放/短会话/盗Cookie/错设备/HTTP降级/Funnel/配置改变/停止等测试同样覆盖新增真实文件模式。
- JS 模型、SW 和只读协议共25项通过，包含新增真实/虚构批次互斥和权限不可提升检查；没有修改原演示计算或缓存逻辑。
- 临时JSON真实模式 TLS 浏览器：14组通过；原虚构 TLS 浏览器：13组通过；原本机只读浏览器：13组通过。真实模式验证精确数量/成本、四页、来源及单位限制、空浏览器存储、Secure/HttpOnly Cookie、8秒超时及迟到响应、401/403/撤销/断网/服务关闭清屏。全部0演示脚本请求、0页面异常。
- diff与提交范围检查通过；只包含 v2 和独立 V2 CI，没有持仓JSON、数据目录、报告或密钥。原 P1 和原 V1.1/Bark 文件未修改。

浏览器 TLS 仅为本机临时自签名替身：忽略证书错误只存在于隔离测试浏览器，正式启动器没有此选项。未启动真实 Tailscale Serve、未登录私人网络、未进行实际 iPhone Safari 验收。没有将跳过算作通过。

## 可复现检查

开发者提供固定 P1 **源码**目录：

```powershell
$env:GOOSE_P1_CODE = "PR3源码目录"
py -3.12 -m unittest discover -s v2/tests -v
node v2/tests/model.test.cjs
node v2/tests/sw.test.cjs
node v2/tests/readonly-model.test.cjs
```

已有 Playwright/Chromium 的开发环境还可运行：

```powershell
$env:GOOSE_TEST_PRIVATE_FILES = "1"
node v2/tests/phone-browser.cjs
```

该变量只选择**测试脚本**内临时虚构文件，不是正式启动器的真实数据开关。普通用户不需要安装Node/Playwright或重跑以上测试。

Windows/Linux GitHub Actions 专用 V2 工作流固定检出 P1，执行原接口测试、手机传输测试、新增文件适配测试及 JS 回归。主项目回归工作流保留。
