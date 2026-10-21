# 固件源码与集成

`hal/` 保留本项目新增文件；`stackchan.patch` 是完整补丁，同时包含官方主循环、音频、相机、动作和UI的集成改动。只复制 `hal/` 不足以启用这些功能。

上游：[M5Stack/StackChan](https://github.com/m5stack/StackChan)，固定提交 `1b5765599fba8aaad1811d9a79358ccc7051f5f3`。补丁以仓库根目录为基准，覆盖25个文件。请在新建、干净的上游工作目录中应用，不要重复覆盖已有自定义固件：

```powershell
git clone https://github.com/m5stack/StackChan.git
cd StackChan
git checkout 1b5765599fba8aaad1811d9a79358ccc7051f5f3
# 将下面路径替换为本仓库中的绝对路径
git apply --check <path-to-stackchan.patch>
git apply <path-to-stackchan.patch>
```

本机开发记录使用 ESP-IDF 5.5；依赖、板型、构建与刷写流程按该上游版本的说明准备。补丁不附带SDK、模型、已编译镜像、NVS或私人设备备份，不会自动刷写。刷写前自行保存当前设备完整备份、核对芯片与分区、确认恢复路径。

当前握手功能包含 `pc_stream=pcm1`、`pc_scene=singer1`、`controls=pose1` 和 `art_gallery=3`。电脑音箱报时抢占/恢复由电脑端负责，不需要为该修复再刷固件。

第三方版权见仓库根目录的 `THIRD-PARTY-NOTICES.md`。补丁已在开发机对应源码上完成反向应用检查；新电脑从干净源码完整构建仍需在自己的环境验证。
