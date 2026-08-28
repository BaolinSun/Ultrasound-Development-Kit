# 交接摘要

## 1. 项目背景、技术栈

项目是一个 Python 超声桌面成像工作站，用于实时显示 B-mode 超声图像，并调节显示参数。底层数据来自 FPGA / Zynq 系统，PC 端负责接收波束合成后的数据、做极坐标到笛卡尔坐标转换、B-mode 映射、Graymap/TGC 调节以及 GUI 显示。

技术栈：

- Python
- PySide6：桌面 GUI
- NumPy：图像/曲线/参数向量化处理
- OpenCV：旧显示路径和部分图像处理兼容
- multiprocessing：采集进程、图像处理进程
- QThread：GUI 帧接收线程
- QProcess：调用 PS 参数配置工具
- USB / UART / WiFi UDP：用于数据传输或参数下发
- Conda 环境：`cubdl`

当前工程目录已从：

```text
D:\MyProjects\py_prj\py_test
```

改名为：

```text
D:\MyProjects\py_prj\ultrasound_dev_kit
```

## 2. 已经完成的工作、关键决策

### GUI 工作站化

已经从原始 `cv2.imshow` 显示方式，逐步演进为 PySide6 桌面工作站界面。

主要文件：

```text
D:\MyProjects\py_prj\ultrasound_dev_kit\ultrasound_desktop_app.py
D:\MyProjects\py_prj\ultrasound_dev_kit\ultrasound_gui.py
D:\MyProjects\py_prj\ultrasound_dev_kit\ultrasound_pipeline_adapter.py
```

GUI 已包含：

- 实时超声图像显示区
- FPS / 状态显示
- Acquisition 控制区
- START / STOP toggle
- FREEZE / RESUME
- B-mode 参数显示
- Dynamic / Brightness / Contrast Gain / Noise Floor 滑块
- Graymap Curve 曲线调节
- TGC Curve 曲线调节
- PS UART Config 面板
- PS USB Config 面板
- 自定义标题栏、图标、深色医学设备风格 UI

### Graymap 与 B-mode 参数

已实现 MATLAB graymap 风格迁移：

- `Dynamic`
- `Brightness`
- `Contrast Gain`
- `Noise Floor`
- Graymap LUT 曲线拖拽
- Reset 功能

图像处理路径为：

```text
rfdata -> TGC -> scan conversion -> B-mode mapping -> graymap LUT -> GUI
```

### TGC 功能

已实现可视化 TGC 曲线：

- 右侧 TGC Curve 面板
- Enable TGC
- Reset / Save / Load
- 控制点拖拽
- JSON 保存/加载
- TGC UI 使用 dB 曲线
- 范围：`-24 dB ~ +24 dB`
- 内部换算为线性 gain：

```python
gain = 10 ** (tgc_db / 20.0)
```

TGC 对 FPGA 输出的 Q2.14 log10 数据处理路径已调整为：

```text
rfdata Q2.14 -> float log10 -> log10 域加 log10(gain) -> scan conversion
```

关键决策：

- `apply_tgc_gain_log10_q214()` 输出正常 `float32 log10`，不再转回 Q2.14。
- `apply_bmode_mapping()` 输入正常 log10 float，不再除以 `2**14`。

### PS 参数配置

已集成两个配置面板：

- `PS UART Config`
- `PS USB Config`

当前工具路径按新工程本地 tools 目录：

```text
D:\MyProjects\py_prj\ultrasound_dev_kit\tools
```

相关工具：

```text
D:\MyProjects\py_prj\ultrasound_dev_kit\tools\ultrasound_config_uart.py
D:\MyProjects\py_prj\ultrasound_dev_kit\tools\ultrasound_config_usb.py
D:\MyProjects\py_prj\ultrasound_dev_kit\tools\ultrasound_config_gui.py
```

已实现逻辑：

- `Configure PS via USB` 前，如果实时采集占用 USB，会先停止采集链路。
- USB 配置结束后，自动恢复配置前的采集/冻结状态。
- 配置期间同步 START/FREEZE 按钮状态，避免 USB 资源冲突。

### WiFi UDP 数据流

曾将 GUI 后台采集从 USB 切换到新增 WiFi UDP 管线：

```text
D:\MyProjects\py_prj\ultrasound_dev_kit\wifi_udp_graymap_bmode_pipeline.py
```

当时实现要点：

- adapter 导入 WiFi 管线
- 非 demo 模式启动 `wifi_udp_reader`
- 默认监听：
  - `192.168.3.23:50001`
- 保留 GUI、TGC、Graymap、PS 参数下发逻辑不变

重要分支信息：

- WiFi 管线相关代码位于当前 git 仓库的 `wifi_udp_debug` 分支。
- WiFi/PSRAM snapshot 相关调试代码位于 `debug/psram-snapshot` 分支。
- 当前工作区如果没有 `wifi_udp_graymap_bmode_pipeline.py`，不应直接判断为文件丢失，应先检查上述分支。

后续在当前工作区检查时发现 `ultrasound_pipeline_adapter.py` 仍导入：

```python
import usb_graymap_bmode_pipeline
```

因此当前检出分支的实际运行路径可能仍是 USB graymap 管线；若要恢复 WiFi 后台，应从 `wifi_udp_debug` 或 `debug/psram-snapshot` 分支同步相关文件和 adapter 改动。

### 4 倍下采样适配

针对 PL 端深度方向 4 倍下采样，曾制定并执行过适配方案：

- `DEFAULT_RF_DEPTH: 6144 -> 1536`
- 新增：
  - `DEFAULT_DEPTH_DOWNSAMPLE = 4`
- `range_step_m` 乘以 4
- `DDR_RING_BYTES` 改为公式计算：

```python
DDR_RING_BYTES = DEFAULT_CHANNEL_COUNT * WORDS_PER_LINE * 2
```

新帧长度：

```text
64 * (1536 + 8) * 2 = 197632 bytes
```

但当前新目录实际代码里仍看到：

```python
DEFAULT_RF_DEPTH = 6144
```

说明当前检出分支可能尚未包含下采样适配。4 倍下采样适配代码位于当前 git 仓库的 `debug/downsampling` 分支，后续需要从该分支同步或 cherry-pick。

## 3. 遇到的问题、踩过的坑

### 工程目录改名导致路径混乱

原路径：

```text
D:\MyProjects\py_prj\py_test
```

后来用户改名为：

```text
D:\MyProjects\py_prj\ultrasound_dev_kit
```

一开始工具上下文仍指向旧路径，导致部分检查失败。后续确认旧路径不可用，新路径有效。

### Plan Mode / Default Mode 混乱

有几次用户明确要求执行计划，但系统处于 Plan Mode，导致无法修改文件，只能输出计划。这造成用户体验混乱。

尤其是“GUI 超声显示深度可调方案”：

- 已经给出详细方案。
- 用户要求执行。
- 但由于当时会话处于 Plan Mode，未能真正修改代码。
- 当前仍是待执行任务。

### GUI 当前显示深度固定

当前实际显示深度来自：

```python
usb_graymap_bmode_pipeline.get_default_imaging_params()
```

计算公式：

```python
depth_mm = 0.5 * c / fs * (DEFAULT_RF_DEPTH - 1) * 1000
```

当前参数：

```text
c = 1540.0 m/s
fs = 25 MHz
DEFAULT_RF_DEPTH = 6144
```

所以当前 GUI 显示深度约：

```text
189.2 mm
```

问题是这个深度目前不可调。

### USB 资源冲突

实时采集时 `usb_reader` 会占用 USB，导致 `Configure PS via USB` 打开 USB 失败。已加入配置前停止采集、配置后恢复的方案。

### 最大化/全屏/任务栏问题

曾遇到：

- Win11 最大化遮挡任务栏
- 双屏最大化跑到两个屏幕中间
- QSizeGrip 问题
- 应用图标任务栏不稳定显示

做过多轮 UI 修复，包括自定义标题栏、frameless 窗口、全屏按钮、最大化区域调整等。

### TGC 数据域理解问题

最初 TGC 是直接对 log 压缩数据做乘法，后来明确 FPGA 输出是 Q2.14 log10 数据，因此改为：

```text
Q2.14 log10 -> float log10 -> 加 log10(gain)
```

这是正确的线性域乘法等价实现，避免逐点反 log / 再 log 的额外开销。

## 4. 待解决任务、下一步目标

### 最高优先级：执行 GUI 显示深度可调

需要真正修改 `ultrasound_gui.py`，实现：

- `full_depth_mm`
- `display_depth_mm`
- `min_display_depth_mm`
- 底部 QuickControls 增加 Depth 滑块
- `UltrasoundImageWidget.paintEvent()` 按比例裁剪显示图像
- overlay 深度刻度同步 `display_depth_mm`
- 右上角信息框 Depth 同步
- focus marker clamp 到当前显示深度
- Reset 恢复完整显示深度

推荐实现方式：

```python
crop_ratio = display_depth_mm / full_depth_mm
source_height = int(pixmap.height() * crop_ratio)
source_rect = QRect(0, 0, pixmap.width(), source_height)
painter.drawPixmap(image_rect, self._pixmap, source_rect)
```

### 确认当前使用的是 USB 还是 WiFi 管线

当前新目录中检查到：

```python
import usb_graymap_bmode_pipeline
```

但此前用户希望切换到 WiFi UDP 管线。需要确认：

- 当前是否应继续使用 USB？
- 是否需要从 `wifi_udp_debug` 或 `debug/psram-snapshot` 分支同步 `wifi_udp_graymap_bmode_pipeline.py` 和 adapter 改动？
- 是否继续使用 4 倍下采样后的 1536 点逻辑？

### 同步 4 倍下采样适配

如果当前 PL 端已经固定 4 倍下采样，则当前工程还需要确认并同步：

- 从 `debug/downsampling` 分支同步或 cherry-pick 下采样适配。
- `DEFAULT_RF_DEPTH = 1536`
- `DEFAULT_DEPTH_DOWNSAMPLE = 4`
- `DDR_RING_BYTES = 197632`
- TGC 曲线 `num_samples = 1536`
- 坐标转换 `range_step_m *= 4`

### 检查当前目录变更丢失

由于目录从 `py_test` 改名到 `ultrasound_dev_kit`，需要重新 diff/检查：

- WiFi 管线是否已从 `wifi_udp_debug` / `debug/psram-snapshot` 同步到当前分支
- adapter 是否仍使用旧 USB 管线
- TGC 是否还是 6144
- 4 倍下采样改动是否已从 `debug/downsampling` 同步到当前分支
- USB 配置自动释放采集逻辑是否仍存在

## 5. 重要相关文件路径

当前工程目录：

```text
D:\MyProjects\py_prj\ultrasound_dev_kit
```

主入口：

```text
D:\MyProjects\py_prj\ultrasound_dev_kit\ultrasound_desktop_app.py
```

GUI 主窗口：

```text
D:\MyProjects\py_prj\ultrasound_dev_kit\ultrasound_gui.py
```

GUI 与后台管线适配器：

```text
D:\MyProjects\py_prj\ultrasound_dev_kit\ultrasound_pipeline_adapter.py
```

当前实际存在的 USB graymap 管线：

```text
D:\MyProjects\py_prj\ultrasound_dev_kit\usb_graymap_bmode_pipeline.py
```

此前曾新增/使用过的 WiFi UDP 管线：

```text
D:\MyProjects\py_prj\ultrasound_dev_kit\wifi_udp_graymap_bmode_pipeline.py
```

注意：该文件和相关 adapter 改动应优先从 `wifi_udp_debug` 或 `debug/psram-snapshot` 分支查找。

相关 git 分支：

```text
wifi_udp_debug
debug/psram-snapshot
debug/downsampling
```

PS 参数配置工具目录：

```text
D:\MyProjects\py_prj\ultrasound_dev_kit\tools
```

相关工具：

```text
D:\MyProjects\py_prj\ultrasound_dev_kit\tools\ultrasound_config_uart.py
D:\MyProjects\py_prj\ultrasound_dev_kit\tools\ultrasound_config_usb.py
D:\MyProjects\py_prj\ultrasound_dev_kit\tools\ultrasound_config_gui.py
```

配置 JSON：

```text
D:\MyProjects\py_prj\ultrasound_dev_kit\tools\configs\afe5832_default.json
```

图标资源：

```text
D:\MyProjects\py_prj\ultrasound_dev_kit\assets
```

旧工程路径，现已不应继续使用：

```text
D:\MyProjects\py_prj\py_test
```
