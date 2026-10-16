# FableTech · GeekezBrowser 调研与借鉴分析

> 调研人：Step-5-Preview（主 Agent）｜ 2026-10-16
> 对象：[EchoHS/GeekezBrowser](https://github.com/echohs/GeekezBrowser)（用户提供线索）
> 结论先行：**思路可借鉴，代码不可用**（其许可禁止商用）；FableTech 技术路线更优，有 5 项功能值得纳入 v1.1

---

## 1. 仓库概况

| 项 | 值 |
|---|---|
| Stars / Forks | 1,131 / 234 |
| 技术栈 | JavaScript（Electron + Puppeteer） |
| 代理内核 | Xray-core |
| 创建 / 最近推送 | 2025-11-24 / 2026-10-14（活跃） |
| 许可证 | **PolyForm Noncommercial 1.0.0**（源码可见但**非 OSI 开源，商用需单独授权**） |
| 官网 | browser.geekez.net |
| 定位 | 跨境电商多账号管理的指纹浏览器（TikTok/Amazon/FB/Shopee） |

## 2. 核心特性

**指纹隔离（JS/参数级）**
- CPU 核数（4/8/12/16）与设备内存（4/8/16GB）随机化
- 时区/地理：自动匹配代理出口 IP，或手动 50+ 城市
- 语言：60+ 语言（浏览器 + HTTP 头 + Intl API）
- WebRTC 物理屏蔽（`disable_non_proxied_udp`）
- UA / WebGL 修改（官方自述「bypassing detection is not guaranteed」）
- Canvas 采用**噪声**方案（其自述「Canvas noise effectively prevents device association」）

**网络引擎（Xray-core）**
- 协议全家桶：VMess / VLESS / Trojan / Shadowsocks（含 SS-2022）/ SOCKS5 / HTTP
- 传输：REALITY / XHTTP / gRPC / mKCP / WebSocket / H2
- **预代理链**：`本地 → 预代理 → 档案代理 → 目标` 隐藏真实 IP
- IPv4/IPv6 双栈智能路由

**工作流**
- Chrome 扩展支持（可按环境启用，如 MetaMask）
- 标签系统、**动态水印**（档案名浮于页面顶部防误操作）
- 多实例隔离端口
- 远程调试端口（Puppeteer/DevTools，默认关闭）

**检测自述**：Browserscan / Pixelscan / Cloudflare 通过；平台兼容性给出 TikTok/FB/Shopee/Amazon（买家+卖家）安全评级，前提是专用 IP。

## 3. 与 FableTech 技术路线对比

| 维度 | GeekezBrowser | FableTech | 判断 |
|---|---|---|---|
| 指纹路线 | JS 注入 + 启动参数（Canvas 噪声、UA 修改） | **Camoufox C++ 内核级注入** | FableTech 更深层：JS 路线会被现代检测识破，官方自己也承认「不保证绕过」 |
| 一致性 | 随机化为主（每环境随机） | 档案种子持久化（跨会话恒定） | 各有取舍：随机适合注册，恒定适合养号；**FableTech 可两者兼顾**（新建档案=新种子） |
| 代理内核 | Xray-core | mihomo | 平手——mihomo 同样支持 VMess/VLESS/Trojan/SS/REALITY，且已内置 hy2/anytls |
| 时区/语言 | 60+ 语言、50+ 城市 | geoip 自动 + 12 国 locale 映射 | **需扩展**（见 §4） |
| GUI | Electron | CustomTkinter | Electron 更美观；CustomTkinter 零运行时依赖、包体小 |
| 许可 | PolyForm Noncommercial（**商用受限**） | MIT（自研部分） | **FableTech 团队自用无任何障碍** |
| 自动化 | Puppeteer 远程调试口（默认关） | Recipe 框架 + 人工介入点 + 调度器 | FableTech 更贴注册/养号场景 |

**关键认知**：Geekez 证明了这个赛道「Electron + 代理内核 + 档案管理」形态的市场需求（1.1k star/半年），但它的指纹路线（JS 级）弱于 Camoufox（C++ 级）。FableTech 的组合（Camoufox + mihomo + Recipe 自动化）在检测对抗上更有优势。

## 4. 值得借鉴的功能（v1.1 排期）

| # | 功能 | 来源 | 实现路径 | 优先级 |
|---|---|---|---|---|
| 1 | **语言库扩展 60+** | Geekez | `suggest_locale` 映射表从 12 国扩展到 60+ 语言/地区，批量导入代理时按出口国家推荐更细的 locale（en-GB/de-DE/fr-FR/ja-JP…） | P1 |
| 2 | **动态水印** | Geekez | 档案启动时经 camoufox 注入脚本在页面顶部叠加半透明档案名（防多窗口误操作）——camoufox 支持 addons/init 脚本注入 | P1 |
| 3 | **WebRTC 硬屏蔽开关** | Geekez | camoufox `launch_options(block_webrtc=...)` 档案级开关（默认开，极端场景可关） | P1 |
| 4 | **mihomo 协议扩展** | Geekez(Xray) | relay.py 配置生成从 hy2/anytls 扩展到 VMess/VLESS/Trojan/SS（mihomo 原生支持，配置生成是同构工作）——覆盖机场订阅用户 | P1 |
| 5 | **预代理链** | Geekez | mihomo `dialer-proxy`：档案代理前加一跳（隐藏真实 IP 的高级场景） | P2 |
| 6 | 平台兼容矩阵 | Geekez | 使用手册补充各平台安全评级（同 §5） | P1 |

> 注：PolyForm Noncommercial 许可下**不复制任何代码**，以上均为独立实现的思路借鉴。

## 5. 平台风控经验（双方共识，写入团队 SOP）

| 平台 | Geekez 评级 | FableTech 建议 |
|---|---|---|
| TikTok | ✅ 安全（Canvas 噪声+专用 IP 防关联） | 一档案一专用住宅 IP；新号 7 天低频 |
| Facebook | ✅ 安全（去 WebDriver 特征，避免高频自动化） | 禁高频自动化；养号节奏人工化 |
| Shopee | ✅ 稳定 | 一账号一环境 |
| Amazon 买家 | ✅ 隔离足够 | 同上 |
| Amazon 卖家 | ✅（TLS 指纹安全，专用 IP+固定环境） | 卖家主账号：固定指纹+固定 IP+养号 30 天 |
| Outlook | —（未覆盖） | 见使用手册 §6：半自动 + 住宅 IP + 7 天养号 |

## 6. 结论

1. **不采用其代码**（商用许可限制），**采纳其产品洞察**：水印、语言库、代理链、平台矩阵都是真实运营痛点。
2. **FableTech 保持技术优势**：C++ 级指纹注入 + 种子持久化 + 半自动注册配方，检测对抗强于 JS 路线。
3. v1.1 按 §4 排期落地，优先 ①②③④（工作量小、运营价值大）。

---

*本调研不影响 v1.0.1 交付；v1.0.1 = v1.0.0 全部内容 + GUI 崩溃修复 + hy2/anytls 支持 + 打包修复。*
