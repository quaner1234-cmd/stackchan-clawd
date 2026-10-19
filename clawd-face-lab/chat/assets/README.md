# 语音素材

这里是用云端语音合成（TTS）预先生成的提示音，设备端直接播放本机 PCM，不在运行时调用云端。

| 文件 | 用途 |
| --- | --- |
| `drink-reminder.wav` | 喝水提醒：「小克也喝水啦，你也喝一小口吧。」 |
| `hourly/00.wav` … `hourly/23.wav` | 整点报时，每个整点一条（共 24 条） |

音频规格：24 kHz、单声道、16-bit PCM WAV。

## 重新生成

素材由 `prepare-drink-voice.py` 和 `prepare-hourly-voice.py` 生成，需要云端 TTS 的 API Key：

```powershell
$env:DASHSCOPE_API_KEY = "sk-..."
python prepare-drink-voice.py
python prepare-hourly-voice.py
```

脚本会写出 WAV，并在 `assets/` 下生成一份记录（模型名、时长、哈希、调用收据）。**收据文件不在本仓库里**——它含有云端请求 ID 和精确调用时间，属于本机运行痕迹，不适合公开。需要核对素材时，重新生成即可得到同样的记录。

## 关于这些素材

- 语音由 `qwen-audio-3.1-tts-flash` / `longanhuan_v3.1` 合成
- 内容为固定的提示语，不含任何个人信息
- 运行时播放本机文件，**不产生云端调用、不产生费用**
- 缺素材时对应功能会跳过并提示，不会自动付费重新生成

## 体积

25 个文件约 3.7 MB。如果不需要这些提示音，可以删除本目录——对应的功能会退化为"没有该条提示音"，其余功能不受影响。
