# V3.2 依赖与许可

所有版本来自独立 Research32 环境的实际安装元数据，并锁定在 requirements-lock.txt。原环境未升级。

| 依赖 | 实际版本 | 许可声明 |
|---|---|---|
| exchange_calendars | 4.13.2 | Apache-2.0 |
| numpy | 2.5.4 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 |
| pandas | 3.1.0 | BSD-3-Clause |
| pyluach | 2.3.0 | License :: OSI Approved :: MIT License |
| toolz | 1.2.0 | BSD-3-Clause |
| tzdata | 2026.5 | Apache-2.0 |
| korean_lunar_calendar | 0.4.0 | MIT |
| requests | 2.34.2 | Apache-2.0 |
| python-dateutil | 3.0.0 | Apache-2.0 OR BSD-3-Clause |
| six | 1.17.0 | MIT |
| certifi | 2026.10.23 | MPL-2.0 |
| charset-normalizer | 3.5.2 | MIT |
| idna | 3.20 | BSD-3-Clause |
| urllib3 | 2.8.0 | MIT |

python-dateutil 的 Dual License 为上游双许可声明；具体条款以包内 LICENSE 文件为准。numpy 的组合表达式包含其分发的第三方许可，不简化为单一许可证。certifi 为 MPL-2.0，未修改其证书包。

exchange_calendars 负责交易日历，核心HTTP适配使用Python标准库并保留系统代理及TLS验证。requests保留用于诊断旧请求链的代理解析。无券商或AI SDK。

浏览器回归复用现有 Playwright 1.62.1（Apache-2.0）和已安装Edge；没有生产npm依赖。

元数据用于许可盘点，不替代上游完整条款；公开行情接口的使用/再分发条件与代码许可分开，真实行情仅本机留存，未打包进Git。
