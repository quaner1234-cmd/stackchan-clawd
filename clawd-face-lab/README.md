# Clawd 动态脸实验室 · 小克

小克（Clawd）的形象原型、电脑端桥接程序与设备端自研固件源文件。

## 目录

| 路径 | 内容 |
| --- | --- |
| `index.html` | 形象原型。浏览器直接打开即可，不发起网络请求 |
| `chat/` | 电脑端主程序与单元测试。入口是 `chat/bridge.py` |
| `firmware/hal/` | 设备端新增的自研源文件（10 个） |
| `tools/` | 离线渲染检查、图标生成、名画素材构建 |
| `artwork-previews/` | 小克 × 三幅名画的 AI 衍生预览图 |

完整的项目说明、隐私设计和第三方材料说明见仓库根目录的 [README](../README.md)。

## 看形象

```powershell
# 浏览器打开 clawd-face-lab/index.html
# 固定状态便于逐帧检查：
#   index.html#f=happy&s=speaking&t=90&theme=cream&bare=1&z=1
```

## 只看界面（不接设备）

```powershell
python chat/bridge.py --preview-ui
```

不打开串口、不连接机器人，只渲染家长面板。

## 单元测试

```powershell
cd chat
python -m pytest test_bridge.py test_weather.py test_camera.py test_daily_routines.py test_device_controls.py test_idle_scenes.py test_parent_tasks.py test_wake.py -q
```

需要设备或云端的检查脚本（`check-*.py`）不在单元测试范围内：它们会占用真实串口或调用云服务，请先读脚本头部再运行，且不要与正式窗口同时占用同一个串口。

## 设备端固件

`firmware/hal/` 里的文件是本项目在 M5Stack 官方固件基础上**新增**的文件。它们不能脱离上游固件树单独编译——需要按 M5Stack 官方文档准备 ESP-IDF 工具链与固件源码树，再把这几个文件放到对应的 `firmware/main/hal/` 位置。

上游固件为 M5Stack 所有，MIT License，Copyright 2026 M5Stack Technology CO LTD。本目录的文件不包含上游代码。

本仓库不包含上游 SDK checkout、编译产物和设备镜像。

## 天气模块的地点配置

`chat/weather.py` 顶部的 `STATION` / `LOCATION` / `PAGE` 三项决定查询哪个气象站，默认配置指向一个山东的区县。换城市改这三项即可，不需要改其他代码。
