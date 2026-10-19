# StackChan MCP 桥接（Windows 本机）

让 StackChan 的语音 AI 用上这台 Windows 电脑上的本地工具。当前只开放一个安全的计算器工具。

> 桥接不启动完全不影响设备；启动它就多一个工具，停止就恢复原样。

## 工作原理

```
StackChan（语音） → 云端 AI →（接入点 WebSocket）→ 本机桥接 mcp_pipe.py
                                                          └→ calculator.py（计算器，本机求值）
```

接入点地址形如 `wss://api.xiaozhi.me/mcp/?token=…`，由手机 App 的 MCP 页面生成，相当于「授权某台电脑作为该智能体工具服务端」的凭证。

本项目 = 一个桥接进程 + 一个只做数学计算的安全工具（无文件、无网络、无系统命令）。电脑睡眠、断网或关窗口，只影响「计算器工具是否可用」，不影响设备普通聊天。

## 快速开始

**推荐：双击 `start-bridge.cmd`。**

首次运行会自动创建 `.env` 并打开记事本，把 `MCP_ENDPOINT=` 后面整体替换为从 App 复制的完整接入点地址，保存并关闭记事本，脚本会自动继续启动。

之后每次启动直接双击 `start-bridge.cmd`；停止可以按 `Ctrl+C`、关窗口，或双击 `stop-bridge.cmd`。

也可以分步操作：

| 命令 | 作用 |
| --- | --- |
| `edit-env.cmd` | 用记事本编辑 `.env` |
| `doctor.cmd` | 自检配置（`check` 默认 / `connect` 真连测试 / `ask` 私密验证地址） |
| `setup.cmd` | 重建 Python 环境（换机或环境损坏时） |

日志在 `logs\bridge.log`（自动滚动，保留 3 份）。

## 验收步骤

1. 启动窗口出现 `Successfully connected to WebSocket server`
2. 手机上**重新进入** App 的 MCP 页面（App 无实时刷新，重进即刷新），「Access Point Status」显示工具名 `calculator`
3. 对设备说：「Hi StackChan，帮我算一下 123 乘 456 等于多少？」→ 设备应说出 **56088**
4. 日志里应同时能找到：

   ```
   [calculator] Successfully connected to WebSocket server
   [calculator] Tool call: calculator
   [calculator] Calculating formula: 123 * 456, result: 56088
   [calculator] Tool result: {"...success":true,"result":56088 ...}
   ```

5. 停止桥接后，对设备说「你好」→ 普通聊天正常，只是计算器暂时不可用

更多测试句：

| 对设备说 | 期望 |
| --- | --- |
| 计算 (88 + 12) 除以 4 | 25 |
| 2 的 10 次方是多少 | 1024 |
| 10 的阶乘是多少 | 3628800 |
| 从 1 到 10 里随机挑一个数字 | 1~10 的随机整数 |

## 故障排查

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| 启动提示「未找到 .env / 仍是占位符」 | 配置未完成 | 运行 `edit-env.cmd` |
| 日志反复 `HTTP 401` | 令牌无效或过期 | App 重新复制 → `edit-env` → 重启桥接 |
| 日志 `HTTP 400` | 地址不完整 | 从 App 完整重新复制（含 `?token=` 全部字符） |
| 握手超时 | 网络不可达/太慢 | 先 `doctor.cmd connect` 测试；换网络或稍后重试 |
| App 一直 Offline | 桥接没连上，或页面没刷新 | ①看桥接窗口是否 connected ②重新进入 MCP 页面 ③唤醒设备再试 |
| 能聊天但日志无 `Tool call` | 模型没用工具 | ①明确说「用计算器算…」 ②唤醒开始新对话再试 ③确认 App 里能看到 calculator 工具 |
| 想同时开两个桥接窗口 | — | 不要。同一接入点多连接可能互相挤掉（云端有并发限制） |
| 日志出现 `gbk`/乱码 | 少见编码问题 | 已统一编码；如出现请保留该日志段落反馈 |

**已知问题**：Windows 个别网络环境存在 websockets 库握手兼容性报告（mcp-calculator #42、#32）。若持续握手超时，先用 `doctor.cmd connect` 判断是否纯网络问题；当前锁定 websockets 17.2。

## 令牌与安全

接入点地址中的 `token` 相当于「允许某台电脑作为该智能体的工具服务端」的长期凭证。它不是账号密码，但**不要外传、不要截图分享**。

目前没有发现用户可自助的「一键轮换/撤销」入口。如果你怀疑某个旧地址已泄露：

- 用 `doctor.cmd ask` **私密验证**某个旧地址是否仍有效（粘贴的地址不回显、不写盘、不出现在日志里）
- 如需强制失效，建议联系官方支持处理

风险范围（据架构推断）：持有该地址者可把自己伪装成你智能体的工具服务端，可能影响工具调用的返回内容；不能读取你的账号、聊天记录，也不能直接控制设备。

**本项目的防护措施**：

- 日志自动脱敏：写日志前 `token=xxx` 一律替换为 `token=***`
- 桥接**不向子进程**传递 `MCP_ENDPOINT`（计算器进程接触不到令牌）
- `.gitignore` 已排除 `.env`
- 计算器为 **AST 白名单**实现：只允许数字、四则运算、math/random 只读函数，禁止 import、属性链、字符串、下标、推导式、lambda、列表重复运算等一切其他语法，并带计算量上限（防「指数炸弹」、超大阶乘、列表内存放大）
- 只开放这一个工具，不提供文件/网络/命令能力

## 文件清单

| 文件 | 作用 |
| --- | --- |
| `mcp_pipe.py` | 桥接主程序 |
| `calculator.py` | 安全计算器（MCP 工具，AST 白名单） |
| `mcp_config.json` | 子进程配置（venv python + 计算器 + UTF-8 环境变量） |
| `requirements.txt` | 依赖锁定清单 |
| `.env` / `.env.example` | 私有接入点配置 / 模板 |
| `edit-env.cmd` | 配置向导 |
| `start-bridge.cmd` | 一键启动（首次含配置向导；含占位符/格式/重复启动三重守卫） |
| `stop-bridge.cmd` / `stop-bridge.ps1` | 停止（含进程树清理） |
| `doctor.cmd` / `doctor.py` | 自检工具 |
| `setup.cmd` | 重建 Python 环境 |
| `tests/` | 离线自测（计算器用例 + 全链路模拟云端） |

## 测试

需要先跑一次 `setup.cmd` 建好虚拟环境（测试会调用 `.venv\Scripts\python.exe`）：

```powershell
setup.cmd
tests\run_all.cmd
```

三个测试都完全在本地进行，不连接真实云端：

- `test_calculator_stdio.py` — 计算器的 MCP stdio 接口
- `test_pipe_mock_cloud.py` — 用本地模拟云端跑完整桥接链路
- `test_security_probe.py` — 对抗性攻击探测（沙箱逃逸、资源耗尽）

## 对上游的修改

基于 [github.com/78/mcp-calculator](https://github.com/78/mcp-calculator) 的 `mcp_pipe.py` v0.2.0（原版在 `vendor\mcp_pipe.orig.py`，MIT License）。本副本的修改：

1. **日志双通道**：控制台 INFO + `logs\bridge.log` 文件 DEBUG（滚动 2MB×3 份）
2. **令牌脱敏**：日志过滤器把 `token=xxx` 替换为 `token=***`
3. **留证**：`Tool call:` / `Tool result:` 两条 INFO 行 + 子进程 stderr 进日志
4. **中文提示**：401/400/超时等常见错误附可操作的中文处理建议
5. **配置定位**：`.env`、`mcp_config.json` 固定从脚本所在目录读取
6. **隔离**：子进程环境剔除 `MCP_ENDPOINT`
7. **Windows 编码**：子进程管道强制 UTF-8，主程序保持系统默认编码

## 维护者注意（编码规约）

- 本目录所有 `.cmd` 文件为 **GBK/CP936 编码**（中文 Windows 批处理惯例）——修改后请以相同编码保存，**不要另存为 UTF-8**（UTF-8+chcp 在此类脚本上会随机解析错位）。
- `.py` 文件保持 **UTF-8**；新增可打印的字符串请确保 GBK 可编码（避免 `✓` 等字符：当输出被重定向时会以 cp936 编码而报错）。
- 修改 `mcp_pipe.py` 时保留头部「修改点」注释并同步更新本节。

## 回退与卸载

- **停止桥接** = 立即回到接入前状态；设备、App、云端配置零改动
- **完全卸载**：删除整个 `mcp-bridge` 文件夹即可（无注册表、无系统服务、无开机项）
- **重建环境**：双击 `setup.cmd`（依赖版本已锁定）
