# 新增文件与依赖清单

应用文件位于 `v2/`，另有独立V2 CI工作流。没有修改 V1.1、PR #3、根目录依赖或 Windows 环境。

- 页面运行：原生 HTML / CSS / JavaScript，无第三方运行依赖和构建步骤。
- 本地预览：已有 Python 3.12+ 标准库；两个 `.cmd` 仅选择已有 Python 并启动预览。
- 单元测试：Node.js 18+ 内置测试模块；Python 标准库 unittest。
- 可选浏览器验收：开发环境的 Playwright，本轮版本 1.62.1；不属于软件运行依赖。
- 图标由开发时自绘生成，交付为静态文件；使用时不需要 Pillow。
- 一次性开发排版使用 Prettier 3.6.2，缓存仅在临时 work/，不是运行依赖或启动要求。
- 原回归依赖只安装在临时隔离的 `work/validation-venv`，不会打包进软件。

用途：`index.html` / `styles.css` / `app.js` 为交互界面；`demo-data.js` 为冻结的虚构夹具；`model.js` 为展示及未来历史映射；`preview.py` 为白名单只读服务器；manifest / SW / icons 为 PWA；tests 为必要验证；docs 为实际截图与浏览器结果。`.gitattributes` 只规范本目录启动脚本的换行与图片处理。

完整文件清单：

- `.gitattributes`
- `DATA-CONTRACT.md`
- `FILES.md`
- `README.md`
- `VALIDATION.md`
- `app.js`
- `assets/192.png`
- `assets/512.png`
- `assets/apple-touch-icon.png`
- `assets/icon.svg`
- `demo-data.js`
- `docs/browser-results.json`
- `docs/screenshots/desktop-holdings-viewport.png`
- `docs/screenshots/desktop-home.png`
- `docs/screenshots/desktop-reports-viewport.png`
- `docs/screenshots/desktop-strategies-viewport.png`
- `docs/screenshots/iphone-holdings-viewport.png`
- `docs/screenshots/iphone-home-viewport.png`
- `docs/screenshots/iphone-report-detail.png`
- `docs/screenshots/iphone-reports-viewport.png`
- `docs/screenshots/iphone-strategies-viewport.png`
- `index.html`
- `manifest.webmanifest`
- `model.js`
- `preview.py`
- `styles.css`
- `sw.js`
- `tests/browser.cjs`
- `tests/model.test.cjs`
- `tests/sw.test.cjs`
- `tests/test_preview.py`
- `启动预览.cmd`
- `手机同网预览.cmd`


## real_readonly基础版新增文件

- `p1_dependency.py`：固定PR #3四个原始核心文件指纹，直接执行核验源码。
- `readonly_fixture.py`：唯一数据提供者，代码内虚构输入与最小化投影。
- `readonly_server.py`：仅本机认证测试接口、配对/会话/撤销/关闭，无真实文件入口。
- `readonly.html`、`readonly.js`、`readonly-model.js`、`readonly.css`：独立模式入口；复用原外壳、样式和四页结构，不加载演示脚本。
- `readonly_launcher.py`、`只读模式虚构测试.cmd`：Windows源码选择、启动/撤销/停止控制窗口。
- `tests/test_readonly.py`、`tests/readonly-model.test.cjs`、`tests/readonly-browser.cjs`：安全、协议和浏览器功能验证。
- `READONLY.md`及`docs/readonly-screenshots/`六张截图：中文操作说明及明确标注的虚构界面。
- `../.github/workflows/v2-readonly.yml`：独立Windows/Linux测试，检出固定P1源码。未修改V1依赖锁或既有CI工作流。

没有新增运行包；Python标准库+独立P1核心源码。Node/Playwright仅开发验证，普通用户不安装。

## iPhone安全连接第二阶段

- `tailscale_transport.py`：本机Tailscale CLI适配、设备身份复核、前台Serve监督及关闭。
- `phone_launcher.py`、`iPhone安全连接虚构测试.cmd`：中文设备选择/授权/启动/撤销/停止窗口。
- `PHONE-HTTPS.md`：分批安装、连接与停止；实际完成和未验收范围。
- `tests/test_phone.py`：虚构设备边界、P1夹具HTTP集成、前台生命周期，Windows原生窗口/Job测试。
- `tests/phone_fixture_host.py`、`tests/phone-browser.cjs`：开发专用临时TLS代理替身和浏览器测试，不能作为真实Serve启动器。
- 复用并小幅修改`readonly_server.py`认证设备绑定/Secure Cookie、`readonly.js`HTTPS和403/超时处理、`readonly.html`连接说明，以及既有文档/V2专用CI。

新增外部运行前提仅为**用户自行安装登录的官方Tailscale**；没有新增pip/npm运行依赖。原演示JS、样式、P1源码、V1工作流及依赖锁保持原样。
