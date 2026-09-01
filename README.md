# UltraVision Workstation

UltraVision Workstation 是一个面向 Zynq/USB 超声实验平台的实时 B-mode 桌面工作站。项目把 USB RF 数据采集、极坐标到笛卡尔扫描转换、灰阶映射、TGC 调节、PS 运行参数配置和 RF 数据分析工具放在同一个 Python/MATLAB 工程中，便于在桌面端完成实时成像、调试和离线分析。

UltraVision Workstation is a desktop ultrasound imaging workstation for a Zynq-based USB acquisition platform. It combines real-time B-mode display, USB RF streaming, scan conversion, graymap/TGC controls, PS-side runtime configuration, and offline RF analysis utilities in one Python/MATLAB project.

## Features

- PySide6 桌面 GUI：实时 B-mode 图像显示、冻结/继续、FPS/深度/探头参数叠加、灰阶曲线编辑和 TGC 曲线编辑。
- USB 成像 pipeline：从默认 USB 设备 `0x0424:0x4940` 读取 RF 数据，完成线数据解析、TGC、scan conversion 和 B-mode 灰阶映射。
- Demo 模式：无需硬件即可生成合成超声帧，用于 GUI 调试和展示。
- PS 配置工具：通过 UART 或 USB bulk endpoint 下发 AFE5832、TX7332、delay profile、demod coefficients、log table、hamming、filter 等 JSON 配置。
- RF 采集与频谱分析：支持从 DDR dump 中提取单条或 64 条 focus line，并生成 RF 波形、频谱和峰值矩阵。
- MATLAB 参数生成：用 MATLAB 脚本量化并生成 BRAM 默认 JSON 参数。

Key capabilities include a PySide6 real-time imaging GUI, a USB B-mode processing pipeline, a hardware-free demo mode, UART/USB runtime configuration tools, RF acquisition and spectrum analysis utilities, and MATLAB scripts for generating quantized BRAM configuration JSON files.

## Architecture

```text
USB device / Zynq PS
        |
        | bulk RF data / config frames
        v
usb_graymap_bmode_pipeline.py
        |
        | multiprocessing queues
        v
ultrasound_pipeline_adapter.py
        |
        | Qt signals
        v
ultrasound_gui.py / ultrasound_desktop_app.py
```

The desktop app starts the acquisition and processing workers, receives processed `uint8` B-mode frames in the Qt thread, and updates display controls such as dynamic range, brightness, contrast, graymap LUT, and TGC gain without blocking the UI.

## Repository Layout

```text
.
|-- ultrasound_desktop_app.py        # Main desktop GUI entry point
|-- ultrasound_gui.py                # PySide6 UI and imaging controls
|-- ultrasound_pipeline_adapter.py   # Qt bridge for the multiprocessing pipeline
|-- usb_graymap_bmode_pipeline.py    # USB RF reader and B-mode processing pipeline
|-- usb_beamformed_image_pipeline.bat
|-- tools/
|   |-- ultrasound_config_uart.py    # UART runtime configuration sender
|   |-- ultrasound_config_usb.py     # USB runtime configuration sender
|   |-- ultrasound_config_gui.py     # GUI for configuration preview/sending
|   |-- calc_dtgc_registers.py       # AFE5832 DTGC register calculator
|   |-- configs/                     # Default JSON parameter files
|   |   `-- c_array/                 # Generated Vitis-ready C array sources
|   `-- para_cal/                    # MATLAB BRAM parameter entry points
|       `-- scripts/                 # Shared MATLAB helpers and probe channel map
|-- docs/
|   `-- hisense_acquisition_protocol.md  # Calibration sweep protocol for the Hisense console
|-- image_autotune/                  # Image-feedback automatic parameter tuning
|   |-- hisense_loader.py            # Console export parsing and BC0 loading
|   |-- hisense_backend_sim.py       # Offline back-end display model and calibration
|   |-- hisense_display_response.py  # Display response recovery from a uniform-TGC sweep
|   |-- hisense_metrics.py           # Phantom image-quality metrics
|   |-- hisense_tgc_optimizer.py     # Closed-loop TGC parameter suggestion
|   `-- hisense_analyze.py           # Analysis figures and Markdown summary
`-- utils/
    |-- rfdata_acq.py                # DDR RF acquisition and CSV export
    |-- rfdata_analyzer.py           # RF waveform and spectrum analysis
    |-- rfdata_spectrum_peak.py      # 64x64 spectrum peak matrices
    `-- usb_reader.py / usb_debug.py # Lower-level USB debug utilities
```

大型采集数据、图片、缓存和本机输出文件不属于仓库核心源码。当前 Git 规则主要管理 Python、JSON、MATLAB 和 BAT 文件。

Large captured data, generated figures, cache files, and local experiment outputs are intentionally kept outside the core tracked source set.

## Environment

建议使用 Windows + Conda 或 venv。当前仓库没有 `requirements.txt`，可按需手动安装以下依赖：

```powershell
pip install PySide6 numpy scipy opencv-python pyusb pyserial matplotlib pandas h5py
```

Recommended environment is Windows with Conda or venv. This repository does not currently include a `requirements.txt`; install the dependencies above manually according to the tools you use.

硬件相关功能还需要：

- Zynq/PS 固件支持对应的 USB/UART 命令协议。
- USB 设备默认 VID/PID：`0x0424:0x4940`。
- USB bulk endpoint 默认值：OUT `0x01`，IN `0x81`。
- UART 默认端口：`COM6`，默认波特率：`460800`。
- Windows 上可能需要为 USB 设备安装合适的 libusb/WinUSB 驱动。

Hardware modes require matching Zynq firmware, the expected USB/UART command protocol, and a working USB driver on the host PC.

## Quick Start

无硬件演示 GUI：

```powershell
python ultrasound_desktop_app.py --demo
```

连接硬件后启动实时工作站：

```powershell
python ultrasound_desktop_app.py
```

也可以使用批处理启动脚本；该脚本会激活 `cubdl` Conda 环境后运行桌面程序：

```powershell
.\usb_beamformed_image_pipeline.bat
```

Run the app in demo mode first to verify the GUI and local Python environment. Use the normal hardware mode after the USB device and firmware are ready.

## Hardware Configuration

UART 配置 dry run，用于预览将要发送到 PS 的命令：

```powershell
python tools/ultrasound_config_uart.py --target all --dry-run
```

USB 配置 dry run：

```powershell
python tools/ultrasound_config_usb.py --target all --dry-run
```

发送单个配置目标示例：

```powershell
python tools/ultrasound_config_uart.py --port COM6 --target afe5832
python tools/ultrasound_config_usb.py --target delay_profile
```

控制 beamforming：

```powershell
python tools/ultrasound_config_usb.py --beam start
python tools/ultrasound_config_usb.py --beam stop
```

The configuration tools convert JSON files under `tools/configs/` into ASCII commands understood by the PS firmware. Use `--dry-run` before touching hardware so the generated command stream can be reviewed safely.

## RF Data Acquisition and Analysis

采集 DDR RF dump 并导出 64 条 focus line 到 `log/rfdata`：

```powershell
python utils/rfdata_acq.py --all-lines log/rfdata
```

导出单条 focus line、原始 dump，并显示波形：

```powershell
python utils/rfdata_acq.py --line 0 --csv log/rfdata_line0.csv --dump log/ddr_dump.txt --plot
```

分析某条 focus line 的某个通道频谱：

```powershell
python utils/rfdata_analysis.py --data-dir log/rfdata --line 0 --channel 0
```

生成 64 x 64 频谱峰值矩阵：

```powershell
python utils/rfdata_spectrum_peak.py --data-dir log/rfdata --out-dir log/rfdata_spectrum_peak --no-show
```

The RF analysis tools expect files named `rfdata_1.csv` through `rfdata_64.csv`, each with `4096` samples by `64` channels.

## Image Autotune (图像反馈的参数自动整定)

`image_autotune/` 实现「根据超声图像闭环自适应地反馈调整参数」这条链路：
解析主机导出的采集目录 → 离线仿真后端 → 计算客观指标 → 求解并给出参数建议。
**不依赖硬件**，只需要 numpy / matplotlib / Pillow。

目前的实现针对海信主机的导出格式（`data/hisense_medical/<场次>/<采集>/`），
因此模块以 `hisense_` 前缀标明与厂商相关的部分；控制律与指标本身与厂商无关。

关键结论：`Algo_BC0.bin`（256 线 × 870 点 × uint16，对数域包络数据）是在**整条后端处理链之前**
抽头的 —— 改变 TGC 时它不变（帧间离散 < 0.2 dB），而显示图像变化超过 20 dB。
因此 BC0 是与显示参数无关的组织观测量，后端旋钮可以完全离线仿真。

| 模块 | 作用 |
|---|---|
| `hisense_loader.py` | 解析 `.pdt` 参数文件、按 `Algo_PartitionInfo.pdt` 校验并加载 BC0、定位截图中的图像区 |
| `hisense_backend_sim.py` | 后端前向模型（TGC / 增益 / 动态范围 / 深度响应）与各常数的标定 |
| `hisense_metrics.py` | 仿体客观指标：深度均匀性、斑点 SNR、囊肿 CNR、点目标 −6 dB 宽度、饱和/压黑占比 |
| `hisense_tgc_optimizer.py` | 闭环 TGC 求解器，输出建议的 8 档滑块值与预测的指标变化 |
| `hisense_display_response.py` | 从均匀 TGC 扫描反解显示响应曲线（证明显示灰阶非线性）|
| `hisense_analyze.py` | 重跑全部标定与验证，生成图与 `summary.md` |

查看采集目录摘要：

```powershell
python image_autotune/hisense_loader.py --data-dir data/hisense_medical/20260819
```

标定后端模型并对照主机截图做留出验证：

```powershell
python image_autotune/hisense_backend_sim.py --data-dir data/hisense_medical/20260819
```

对某一帧给出 TGC 建议值：

```powershell
python image_autotune/hisense_tgc_optimizer.py --data-dir data/hisense_medical/20260819 --capture data/hisense_medical/20260819/S0-a
```

生成完整分析报告：

```powershell
python image_autotune/hisense_analyze.py --data-dir data/hisense_medical/20260819 --out-dir output/hisense19
```

### 标定现状

TGC 是 dB 域**加性、深度均匀、线性**的执行器，中性点 127 —— 这一点由 20260819 全量程扫描确证：
逐带斜率经响应修正后收敛到 1.08×（bands 1–7 变异系数 2.2%），
且「Gain +50 档」与「TGC +127 档」产生的图像逐带差异 ≤ 3 灰阶，
而 Gain 作为全局标量不可能带深度权重。

**已知未解决**：`hisense_backend_sim.py` 仍假设显示灰阶与 dB 线性，
而 20260819 的数据显示显示端在暗部压缩约 3 倍（见 `hisense_display_response.py`）。
受影响的是 `C(z)` 与 `db_per_level` 两个标定量，导致建议档位偏移平均 9 档（约 0.74 dB）、最大 18 档。
优化器的**相对形状可信，绝对档位尚不可信**；滑块因此仍限制在 70–185，
`--allow-extrapolation` 可显式放开。

Gain 轴有一个来自 S1-L 的初步估计（1 Gain 档 ≈ 2.51 TGC 档），但尚未验证线性度；
动态范围轴未标定。绝对 dB 刻度依赖「`UIDynamicRangeLevel` 即字面 dB」这一未验证假设，
由 [docs/hisense_acquisition_protocol.md](docs/hisense_acquisition_protocol.md) 的序列 S2/S3 解决。

`image_autotune/` implements the image-feedback parameter tuning loop. `Algo_BC0.bin` is
tapped upstream of the back end, so it is invariant to the display knobs and every back-end
parameter can be simulated without the console in the loop. The TGC actuator is identified as
a depth-uniform additive dB gain, which lets the optimiser invert it analytically instead of
searching. The display response is known to be nonlinear and is not yet wired into the model.

## MATLAB Parameter Generation

MATLAB 脚本位于 `tools/para_cal/`，用于生成或更新 BRAM 相关默认 JSON 配置及可复制到 Vitis 工程的 C 数组 TXT，包括 delay profile、demod coefficients、log table、x element、hamming、dfilter、timing 和 sin beta 等。

在 MATLAB 中运行：

```matlab
cd tools/para_cal
generate_bram_defaults
```

The MATLAB generators write 8 JSON files to `tools/configs/` and 8 matching C source files to `tools/configs/c_array/` by default. Demodulation sine and cosine arrays share `demod_coeffs.c`. Both formats come from the same quantized word arrays. Pass a custom output directory to preserve the same JSON plus `c_array/` layout without overwriting production defaults during experiments.

延时数组输出为 `tools/configs/c_array/delay_profile_fpga.c`，可直接复制到 Vitis 工程。

## Notes

- Demo 模式不需要硬件，适合验证 GUI、灰阶映射和 TGC 交互。
- 真实采集模式会访问 USB 设备 `0x0424:0x4940`，请确认硬件、固件和驱动已就绪。
- 配置工具默认使用仓库内 `tools/configs/` 的 JSON 文件；修改配置前建议先执行 `--dry-run`。
- RF 分析脚本默认采样率为 `25e6` Hz，可通过 `--fs` 覆盖。
- 当前仓库不包含大型采集数据和生成图片；建议把实验输出放到 `log/` 或独立数据目录。

Demo mode is safe for software-only testing. Hardware mode depends on the external ultrasound platform and firmware, so review generated commands, confirm driver setup, and keep large experiment data outside the repository.
