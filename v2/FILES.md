# 新增文件与依赖清单

本轮所有提交文件均位于 `v2/`。没有修改 V1.1、PR #3、根目录依赖或 Windows 环境。

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
