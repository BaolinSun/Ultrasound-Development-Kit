import matplotlib.pyplot as plt
import numpy as np
from matplotlib.animation import FuncAnimation


WORDS_PER_CH = 4096
RTC_LINES = 128
CH_PER_RTC_LINE = 32
FRAME_WORDS = RTC_LINES * CH_PER_RTC_LINE * WORDS_PER_CH


def hex_to_signed_int(hex_str, bits=16):
    """将16进制补码转换为有符号整数"""
    val = int(hex_str, 16)
    if val & (1 << (bits - 1)):
        val -= 1 << bits
    return val

def convert_result_to_numpy(result):
    num_lines = 64
    num_channels = 64
    numpy_array = np.zeros((num_lines, num_channels, 4096), dtype=np.int16)
    for i in range(num_lines):
        for j in range(num_channels):
            numpy_array[i, j, :4093] = result[i][j]
            numpy_array[i, j, 4093:] = result[i][j][4090:]
    return numpy_array


def find_frame_start(lines):

    n = len(lines)

    for i in range(n):
        if lines[i].upper() != "A55A":
            continue

        rtc_line = int(lines[(i + 1) % n], 16)
        channel_id = int(lines[(i + 2) % n], 16)

        if rtc_line == 0 and channel_id == 0:
            return i

    raise RuntimeError("未找到帧头 A55A 0000 0000,请确认DDR dump中包含完整RF帧")


def circular_get(lines, start, offset):
    return lines[(start + offset) % len(lines)]


def process_ultrasound_sa_data(file_path):
    # 结构: {rtc_line: {channel_id: [data]}}
    raw_data = {}

    with open(file_path, "r") as f:
        lines = [line.strip().upper() for line in f if line.strip()]

    if len(lines) < FRAME_WORDS:
        raise RuntimeError(
            f"文件word数不足: {len(lines)}, 期望至少 {FRAME_WORDS}"
        )

    frame_start = find_frame_start(lines)
    print(f"环形帧头位置 word index = {frame_start}, byte offset = 0x{frame_start * 2:08X}")

    for block_idx in range(RTC_LINES * CH_PER_RTC_LINE):
        base = block_idx * WORDS_PER_CH

        magic = circular_get(lines, frame_start, base)
        rtc_line = int(circular_get(lines, frame_start, base + 1), 16)
        channel_id = int(circular_get(lines, frame_start, base + 2), 16)

        if magic != "A55A":
            raise RuntimeError(
                f"通道包头错误: block={block_idx}, "
                f"offset={base}, got={magic}, "
                f"rtc_line={rtc_line}, ch={channel_id}"
            )

        samples = [
            hex_to_signed_int(circular_get(lines, frame_start, base + k))
            for k in range(3, WORDS_PER_CH)
        ]

        raw_data.setdefault(rtc_line, {})[channel_id] = samples

    # 合成孔径合并: {logical_focus_line: {channel_id: [data]}}
    rfdata = {}

    for rtc in range(0, RTC_LINES, 2):
        logical_line = rtc // 2
        rfdata[logical_line] = {}

        if rtc in raw_data:
            rfdata[logical_line].update(raw_data[rtc])

        if rtc + 1 in raw_data:
            rfdata[logical_line].update(raw_data[rtc + 1])

    return rfdata


# 执行解析
rfdata = process_ultrasound_sa_data("log\\output.txt")


for line_idx in sorted(rfdata.keys()):
    channels = sorted(rfdata[line_idx].keys())
    if len(channels) != 64:
        raise RuntimeError(
            f"警告: 聚焦线 {line_idx} 包含通道数 {len(channels)}, "
            f"未达到预期的64通道 (通道范围 {min(channels)}-{max(channels)})"
        )


plt.figure(figsize=(10, 8))

plt.subplot(3, 1, 1)
plt.plot(rfdata[36][31])

plt.subplot(3, 1, 2)
plt.plot(rfdata[36][32])

plt.subplot(3, 1, 3)
plt.plot(rfdata[36][33])

plt.tight_layout()
plt.show()

# data = convert_result_to_numpy(rfdata)
# data = 20 * np.log10(np.abs(data) + 1e-10)  # 转换为dB，避免log(0)导致的负无穷


# fig, ax = plt.subplots()
# im = ax.imshow(data[0].T, aspect='auto')
# ax.set_title("Frame 0")
# plt.colorbar(im)

# def update(frame):
#     # 更新图像数据
#     im.set_array(data[frame].T)

#     vmin = np.min(data[frame])
#     vmax = np.max(data[frame])
#     im.set_clim(vmin, vmax)

#     # 更新标题
#     ax.set_title(f"Frame {frame} / 64 | | Range: [{vmin:.1f}, {vmax:.1f}] dB")
#     return [im]

# ani = FuncAnimation(fig, update, frames=64, interval=10)
# # ani.save('line_animation.gif', writer='pillow', fps=8)
# plt.tight_layout()
# plt.show()