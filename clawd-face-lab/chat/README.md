# 电脑端桥接程序

小克的电脑端主程序。设备通过 USB 与它对话，它把音频送到云端模型，再把回复、动作和表情发回设备。

入口是 `bridge.py`，界面层在 `panel_design.py`，其余是按功能拆分的模块。

## 运行

```powershell
# 只渲染界面，不开串口、不连设备
python bridge.py --preview-ui

# 连接真实设备
$env:DASHSCOPE_API_KEY = "sk-..."
python bridge.py --port COM5
```

不带 `--port` 时会自动枚举符合已知 VID/PID 的设备；找到多台时会拒绝连接并提示用 `--port` 指定。

支持的参数：

| 参数 | 说明 |
| --- | --- |
| `--preview-ui` | 只渲染界面，不连接设备 |
| `--port` | 串口名，如 `COM5` |
| `--provider` | `dashscope`（默认）或 `deepseek` |
| `--model` | 模型名，默认 `qwen3.8-flash` |

## 模块

| 文件 | 作用 |
| --- | --- |
| `bridge.py` | USB 桥接与云端编排、家长面板的全部控件 |
| `panel_design.py` | 家长面板的配色、样式和布局助手 |
| `providers.py` | 云端模型与语音合成的统一入口，凭据只从环境变量读 |
| `camera.py` | 相机取图与图像识别 |
| `wake.py` | 本地语音唤醒（sherpa-onnx 离线模型） |
| `device_controls.py` | 方向与音量指令的编码（限幅 + CRC） |
| `idle_scenes.py` | 待机场景与轮播 |
| `daily_routines.py` | 整点报时、喝水提醒、夜间睡觉 |
| `workday_alarm.py` | 工作日叫醒 |
| `parent_tasks.py` | 家长计划任务的调度 |
| `weather.py` | 天气查询（中央气象台公开接口） |
| `calendars/` | 年度节假日与调休表 |

## 依赖

```powershell
pip install -r requirements.txt        # 串口 + HTTP
pip install -r requirements-wake.txt   # 本地语音唤醒（sherpa-onnx + numpy）
```

只用标准库也能跑 `--preview-ui` 界面预览。

## 环境变量

| 变量 | 用途 |
| --- | --- |
| `DASHSCOPE_API_KEY` | 默认的语音识别、对话与语音合成服务 |
| `DEEPSEEK_API_KEY` | 仅当 `--provider deepseek` 时需要 |

程序只读环境变量名，不会把 Key 写进任何文件或日志。

## 设置文件

程序会在 `chat/` 下写一些本机状态，用于记住开关状态：

`wake-settings.json`、`daily-settings.json`、`alarm-settings.json`、`drink-settings.json`、`home.json`

这些文件属于运行状态，不纳入版本控制（见仓库根目录的 `.gitignore`）。删除它们等同于重置本机设置。

`calendars/china-workdays.json` 是年度节假日与调休表，和运行状态不同，是仓库的一部分。更新方法见 `calendars/README.md`。

## 录音与隐私

- 待机语音唤醒在本机完成，音频不落盘、不上云
- 云对话有 20 轮 / 30 分钟上限；到期后只保留本地功能
- 拍照识别会把图片发送到云端模型，界面里有一项开关可以关闭"在电脑留一份照片"
- `connection.jsonl` 与 `usage.jsonl` 只记录时间、串口、连接阶段、耗时和用量，不含音频或对话内容

## 测试

单元测试不需要设备或云端：

```powershell
python -m pytest test_bridge.py test_weather.py test_camera.py test_daily_routines.py \
    test_device_controls.py test_idle_scenes.py test_parent_tasks.py test_wake.py -q
```

`check-*.py` 是需要真实设备或网络的检查脚本，会占用串口或调用云服务。请先读脚本头部再运行，且不要与正式窗口同时占用同一个串口。

## 家长面板

四个页签：聊天陪伴 / 动作与音量 / 作息提醒 / 家长任务。

顶部「停止」始终可用，连接状态用状态点表示，内容可滚动。界面用 Tkinter 实现，只依赖标准库。

## 已知限制

- 物理方向与手感需要在设备旁实测，程序里的角度换算是有限幅的近似，不能当作精密定位
- 长按屏幕、真实触屏中止这些交互没有真人验证过，只在模拟事件路径上测过
- 天气模块只查一个固定气象站
- 童声效果和长时间运行的稳定性未单独验收
