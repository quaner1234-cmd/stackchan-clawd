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
| `pc_audio.py` | Windows WASAPI 系统声音回环与连续 USB 音箱传输 |
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
pip install -r requirements-audio.txt  # 可选：Windows 电脑音箱与播放验证
```

`--preview-ui` 需要运行依赖，但不需要串口设备或云端 API Key。

## 电脑音箱、拿话筒与报时

可选“自动电脑音箱”开关：首次默认关闭，之后只保存本机开关。启用后，检测和播放共用一条Windows系统回环。最近1.5秒内累计约600ms有声、当前仍有声且小克空闲时自动开启，允许视频说话的短暂停顿；连续安静10秒后回到原语音待机。只保留最新60ms/2880B声音和最多75个有声帧时间戳，不调用云服务或保存音频，检测不打开电脑麦克风。单次250ms短提示音忽略，接连的提示音可能触发；开头检测/USB切换时仍可能丢失，过轻声音不保证接管。

聊天、拍照、舞蹈或提醒期间不自动抢占；整点报时仍可中断/恢复。点屏幕、长按或手动停止后，同一段声音不会立刻重新触发，需先安静约1秒，再有新的声音。关闭自动开关停止自动流并释放检测；手动开启的音箱不因静音10秒而退出。Windows端点缺失、变化或采集异常会显示原因并以1–5秒退避重试，恢复后继续检测，无需反复关开开关。开关位于本机`pc-audio-settings.json`，不应提交版本库。自动功能只改电脑端，不依赖虚拟声卡、不改默认输出、不需要再刷固件。

自动切换硬件检查为`check-auto-speaker.py`，会播放本地测试音；请先关闭正常窗口并暂停其他电脑音源。`test_auto_audio.py`默认不访问硬件；显式设置`CLAWD_AUDIO_INTEGRATION=1`才运行其中的真实回环检查。当前软件检查166项，默认165项通过、1项硬件检查跳过。

2026-10-21复核：旧规则把200ms以上停顿视作重新开始，真实Windows HDMI输出中400ms声音/300ms安静交替6秒，能捕获有声却完全没有自动接管。累计规则修复后，同一声音在正常家长窗口自动传到机器人，设备711帧、无坏包/断流；现有6.4秒本地语音WAV也自动接管并恢复KWS。`check-auto-audio-reassessment.py`可观察正常运行窗口，用普通Windows WAV播放复核；加`--intermittent`复现断续声，不占串口、不保存音频或调用云端。约1.2秒为本机回环触发检测时间，不是声学端到端延迟。真人播放器、实际听感和长时间耐久仍需验证。公开仓库不包含本机回退备份；可用Git恢复旧源码。

连接支持 `pc_stream=pcm1` 的固件后，在“动作与音量”页开启电脑音箱。程序捕获当前 Windows 默认播放端点的系统声音，通过 USB 连续交给小克；不打开电脑麦克风，不保存音频，不调用模型。需要 Windows 有在线的播放设备；仅使用 HDMI 音频时，关闭或拔掉显示器可能使音频端点消失。更换默认端点后需重新开启音箱。

`pc_scene=singer1` 固件在音箱模式显示拿着话筒的小克，嘴巴随真实播放音量开合；退出音箱后回到原场景。单声道、24kHz/16bit，不承诺高保真或严格视频同步。音箱期间暂停本地语音唤醒；点屏幕停止音箱后进入聊天，长按仍清空对话。

整点报时和“测试声音/试播报时”会先用校验 STOP/STREAM_DONE 释放 USB，再播已有本地语音；正常结束后自动恢复电脑声音。电脑播放器本身继续走当前进度，不回放报时期间的内容。点屏幕、停止、关闭音箱、USB/捕获错误会撤销自动恢复；起床闹钟仍优先停止音箱，之后不自动重开。夜间静音、报时开关与时段去重保留；喝水提醒仍等空闲。

软件检查：在本目录执行 `python -m unittest discover -p "test_*.py"`。发布前146项通过。硬件检查另用 `check-pc-speaker.py`、`check-singer-scene.py`、`check-speaker-hourly.py`，默认占用 COM5，须先关正式窗口并阅读脚本。测试音会从机器人播放。整点验证只注入临时测试时间，不改系统时间和生产作息；公开仓库不包含私人回退备份，故旧版本对照可跳过，实际抢占/报时/恢复检查仍执行。2026-10-20开发机实机检查通过，真人听感、真实整点守候和长期运行验收仍待确认。

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
