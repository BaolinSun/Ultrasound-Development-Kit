import matplotlib.pyplot as plt

# ==========================================
# 1. 配置数据 (Configuration Data)
# ==========================================

# 实际延时配置 (Actual Delay Config)
DELAY_CFG_DICT = {
    1: 0,       2: 92,      5: 390,     6: 486,
    9: 776,     10: 874,    13: 1170,   14: 1270,
    17: 1560,   18: 1660,   21: 1950,   22: 2050,
    25: 2330,   26: 2430,   29: 2730,   30: 2820,
    33: 3120,   34: 3210,   37: 3510,   38: 3600,
    41: 3900,   42: 4000,   45: 4280,   46: 4380,
    49: 4680,   50: 4760,   53: 5060,   54: 5150,
    57: 5440,   58: 5540,   61: 5840,   62: 5940,
}

# 理论延时设定 (Theoretical Delay Setting)
DELAY_SET_DICT = {ch: (ch-1) * 100 for ch in range(1, 65)}


def plot_delay_performance(cfg_data, set_data):
    """
    绘制通道延时对比图，单位为 ns
    """
    # 提取并排序数据
    cfg_channels = sorted(cfg_data.keys())
    cfg_values = [cfg_data[ch] for ch in cfg_channels]
    
    set_channels = sorted(set_data.keys())
    set_values = [set_data[ch] for ch in set_channels]

    # 设置绘图风格
    plt.style.use('seaborn-v0_8-muted') 
    plt.figure(figsize=(11, 6), dpi=100)

    # 绘制曲线：实际值用实线+圆点，设定值用虚线+小方块
    plt.plot(cfg_channels, cfg_values, 
             marker='o', linestyle='-', linewidth=1.8, 
             markersize=5, label='Measured Delay', color='#1f77b4')
    
    plt.plot(set_channels, set_values, 
             marker='s', linestyle='--', linewidth=1.2, 
             markersize=3, label='Target Setting', color='#ff7f0e', alpha=0.7)

    # --- 核心修改：标签与标题 ---
    plt.title('RF Channel Delay Consistency Analysis', fontsize=14, pad=15)
    plt.xlabel('Channel ID', fontsize=12)
    plt.ylabel('Delay (ns)', fontsize=12, fontweight='bold') # 设置单位为 ns
    
    # 图表细节修饰
    plt.grid(True, linestyle=':', alpha=0.6)
    plt.legend(loc='upper left', frameon=True)
    
    # 优化坐标轴刻度
    plt.xticks(range(0, 65, 4)) 
    plt.xlim(-1, 64)
    
    # 自动调整布局，防止标签重叠
    plt.tight_layout()
    plt.show()


if __name__ == "__main__":
    plot_delay_performance(DELAY_CFG_DICT, DELAY_SET_DICT)