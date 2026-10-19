# Clawd / 小克 — StackChan 家庭语音陪伴

把一只陶土橙的桌面机器人（[M5Stack StackChan / CoreS3](https://www.m5stack.com/)，ESP32-S3，320×240 彩屏）改造成孩子的语音陪伴设备：本地语音唤醒、连续对话、拍照问答、动作表情、整点报时、喝水提醒、工作日叫醒，以及一个给家长用的桌面控制面板。

电脑上跑一个 Python 桥接程序，通过 USB 与机器人对话；机器人自己负责待机动画、表情、动作和灯效。

```
孩子说话 ──► 机器人（USB 音频上行）
                │
                ▼
        电脑端 bridge.py  ──► 云端大模型 ──► 语音合成
                │                            │
                └──── 动作/表情指令 ◄─────────┘
                          │
                          ▼
                    机器人（舵机/屏幕/灯）
```

## 仓库结构

| 路径 | 内容 |
| --- | --- |
| `clawd-face-lab/index.html` | 小克形象的浏览器原型。直接双击打开即可，无需安装依赖或启动服务，页面本身不发起任何网络请求 |
| `clawd-face-lab/chat/` | 电脑端主程序。`bridge.py` 是 USB 桥接与云端编排入口，`panel_design.py` 是家长面板的界面层，其余为唤醒、相机、日程、天气、动作控制等模块与单元测试 |
| `clawd-face-lab/firmware/hal/` | 设备端新增的 10 个自研源文件（USB 音频通道、相机、动作、待机场景、手动控制、名画素材等） |
| `clawd-face-lab/tools/` | 离线渲染检查、图标生成、名画素材构建等开发工具 |
| `clawd-face-lab/artwork-previews/` | 小克 × 三幅名画的 AI 衍生预览图（原作均为公有领域） |
| `mcp-bridge/` | 独立的 MCP 桥接程序，让设备端语音能调用电脑上的本地工具（当前只开放一个安全的计算器） |

## 快速开始

### 看一眼形象

用浏览器打开 `clawd-face-lab/index.html`。在文件地址后附加参数可固定状态，便于逐帧检查：

```
index.html#f=happy&s=speaking&t=90&theme=cream&bare=1&z=1
```

`f` 情绪、`s` 状态、`t` 毫秒时间、`theme` 主题、`bare=1` 只留画布。

### 跑家长面板（不接设备）

```powershell
python clawd-face-lab/chat/bridge.py --preview-ui
```

`--preview-ui` 不打开串口、不连接设备，只渲染界面，可以安全地查看和截图。

### 连真机

需要 ESP-IDF 工具链、串口（Windows 上通常是 `COM5`）和云端模型服务的 API Key。

```powershell
# 1. 准备环境变量（不要把 Key 写进文件）
$env:DASHSCOPE_API_KEY = "sk-..."

# 2. 启动桥接
python clawd-face-lab/chat/bridge.py --port COM5
```

固件构建与刷写脚本在 `clawd-face-lab/device/`（本仓库未包含上游 SDK checkout 与编译产物，需按官方文档自行准备）。

## 已实现的能力

**对话与感知**
- 连续语音对话，回答后自动继续聆听；可长按屏幕清空上下文
- 本地语音唤醒「你好小克」，用 sherpa-onnx 离线模型，不联网、不保存录音
- 拍照并用一句话回答「看到了什么」；照片默认只存在电脑上，可关闭
- 天气查询走中央气象台公开接口，不需要 Key

**表现与交互**
- 表情：平静、开心、生气、难过、疑惑、困倦；说话时口型开合；自然眨眼与呼吸
- 待机轮播：敲键盘、爱心、喝水、小睡，以及三幅名画
- 舵机动作：抬头、左右转、跳舞；`sent` 量化的方向与音量控制带实际编码器反馈

**日程**
- 整点报时（夜间静音）、喝水提醒（9:00–20:00）、工作日 6:40 叫醒（按国务院公布的调休安排）
- 23:00 后进入夜间睡觉画面

**家长面板**
- 四个页签：聊天陪伴 / 动作与音量 / 作息提醒 / 家长任务
- 顶部「停止」始终可用；连接状态用状态点表示；内容可滚动

## 隐私设计

- 待机语音唤醒在本地完成，音频不落盘、不上云
- 云对话有 20 轮 / 30 分钟上限，到期后只保留本地功能
- 天气查询只发送一个固定的公开站点号，不发送位置
- 设备侧不写分区表和 NVS，只更新已有的 APP 分区
- 家长任务（Codex 计划）在电脑后台执行，结果只保存到本机，不念给孩子

## 第三方材料

本仓库包含或衍生自以下第三方工作，详见 [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md)：

- `mcp-bridge/mcp_pipe.py`、`calculator.py` 衍生自 [78/mcp-calculator](https://github.com/78/mcp-calculator) v0.2.0（MIT）
- 目标固件是 M5Stack 的（MIT，Copyright 2026 M5Stack Technology CO LTD）；`firmware/hal/` 下是本项目新增的文件
- 小克形象参考 [Clawd by Claude Code](https://www.stickermule.com/claudecode/item/19156131)
- `artwork-previews/` 是三幅公有领域名画的 AI 衍生图

## 许可

自研代码以 MIT License 发布（见 `LICENSE`）。

## 免责声明

这是一个个人项目，与 M5Stack、Anthropic 或任何云端模型服务商没有关联。刷写固件会覆盖设备上的应用分区，请先确认你了解风险，并保留官方恢复方式。
