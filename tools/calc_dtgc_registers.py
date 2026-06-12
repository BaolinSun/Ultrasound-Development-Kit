import math


def depth_to_clocks(depth_m, adc_clk_hz, sound_speed=1540.0):
    """Convert imaging depth to ADC clock cycles using round-trip echo time."""
    echo_time = 2.0 * depth_m / sound_speed
    return round(echo_time * adc_clk_hz)


def calc_dtgc_registers(
    adc_clk_hz=50e6,
    sound_speed=1540.0,
    lna_gain_db=21,
    pga_gain_db=27,
    dtgc_start_depth_cm=4.0,
    max_gain_depth_cm=10.0,
    ramp_down_depth_cm=12.0,
    start_gain_code=0,
    stop_gain_code=144,
    pos_step_code=1,
    neg_step_code=71,
    slope_fac=0,
    mem_bank=0,
):
    """
    Calculate AFE5832 DTGC Internal Non-Uniform Mode registers.

    Gain formula:
        Gain = LNA_GAIN + PGA_GAIN - 36 + 0.25 * GAIN_CODE

    POS/NEG step formula:
        Step = (STEP_CODE + 1) * 0.125 dB

    Memory word format:
        bit7 / bit15     : direction, 0 = positive step, 1 = negative step
        bit6:0 / bit14:8 : wait count
    """

    assert 0 <= start_gain_code <= 144
    assert 0 <= stop_gain_code <= 144
    assert stop_gain_code >= start_gain_code
    assert 0 <= pos_step_code <= 255
    assert 0 <= neg_step_code <= 255
    assert 0 <= slope_fac <= 15
    assert 0 <= mem_bank <= 3

    adc_period_ns = 1e9 / adc_clk_hz

    start_depth_m = dtgc_start_depth_cm / 100.0
    max_gain_depth_m = max_gain_depth_cm / 100.0
    ramp_down_depth_m = ramp_down_depth_cm / 100.0

    start_gain_time = depth_to_clocks(start_depth_m, adc_clk_hz, sound_speed)
    max_gain_time = depth_to_clocks(max_gain_depth_m, adc_clk_hz, sound_speed)
    ramp_down_time = depth_to_clocks(ramp_down_depth_m, adc_clk_hz, sound_speed)

    ramp_up_clocks = max_gain_time - start_gain_time
    stop_gain_time = ramp_down_time - max_gain_time

    if ramp_up_clocks <= 0:
        raise ValueError("max_gain_depth_cm must be larger than dtgc_start_depth_cm")

    if stop_gain_time < 0:
        raise ValueError("ramp_down_depth_cm must be larger than max_gain_depth_cm")

    pos_step_db = (pos_step_code + 1) * 0.125
    neg_step_db = (neg_step_code + 1) * 0.125

    gain_code_delta = stop_gain_code - start_gain_code
    gain_delta_db = gain_code_delta * 0.25

    ramp_steps = round(gain_delta_db / pos_step_db)

    if ramp_steps <= 0:
        raise ValueError("Invalid ramp step count")

    if ramp_steps > 320:
        raise ValueError("Too many ramp steps. AFE5832 memory supports at most 320 gain events.")

    wait_per_step_float = ramp_up_clocks / ramp_steps
    wait_per_step_base = int(round(wait_per_step_float / (2 ** slope_fac)))

    if wait_per_step_base < 1 or wait_per_step_base > 127:
        raise ValueError(
            f"Memory wait count {wait_per_step_base} out of range. "
            "Adjust POS_STEP, SLOPE_FAC, or depth range."
        )

    # Distribute clock error across gain events
    wait_counts = []
    accumulated = 0
    for i in range(ramp_steps):
        target = round((i + 1) * ramp_up_clocks / ramp_steps)
        wait = target - accumulated
        accumulated += wait

        wait_base = round(wait / (2 ** slope_fac))
        wait_base = max(1, min(127, wait_base))
        wait_counts.append(wait_base)

    # Pack two gain events into one MEM_WORD
    mem_words = []
    for i in range(0, len(wait_counts), 2):
        low_wait = wait_counts[i]
        high_wait = wait_counts[i + 1] if i + 1 < len(wait_counts) else 0

        low_event = low_wait & 0x7F          # bit7 = 0, positive step
        high_event = high_wait & 0x7F        # bit15 = 0, positive step

        mem_word = (high_event << 8) | low_event
        mem_words.append(mem_word)

    stop_index = len(mem_words) - 1

    # Register values
    reg_A1 = ((start_gain_code & 0xFF) << 8) | (stop_gain_code & 0xFF)
    reg_A2 = ((pos_step_code & 0xFF) << 8) | (neg_step_code & 0xFF)
    reg_A3 = (0x00 << 8) | (stop_index & 0xFF)
    reg_A4 = start_gain_time & 0xFFFF
    reg_A5 = stop_gain_time & 0xFFFF

    # B5:
    # bit15    = SLOPE_FAC[0]
    # bit14    = ENABLE_INT_START
    # bit13:12 = MEM_BANK_SEL
    # bit10    = MANUAL_START
    # bit7:0   = MANUAL_GAIN_DTGC
    reg_B5 = ((slope_fac & 0x1) << 15) | ((mem_bank & 0x3) << 12)

    # B6:
    # bit15:14 = MODE_SEL = 11, Internal Non-Uniform Mode
    # bit13:12 = PROFILE_REG_SEL = 00, Profile 0
    # bit11    = PROFILE_EXT_DIS = 1, use register profile selection
    # bit4:2   = SLOPE_FAC[3:1]
    reg_B6 = (0b11 << 14) | (1 << 11) | (((slope_fac >> 1) & 0x7) << 2)

    reg_B7 = 0x0000

    start_total_gain_db = lna_gain_db + pga_gain_db - 36 + 0.25 * start_gain_code
    stop_total_gain_db = lna_gain_db + pga_gain_db - 36 + 0.25 * stop_gain_code

    return {
        "timing": {
            "adc_clk_hz": adc_clk_hz,
            "adc_period_ns": adc_period_ns,
            "start_gain_time_clocks": start_gain_time,
            "ramp_up_clocks": ramp_up_clocks,
            "stop_gain_time_clocks": stop_gain_time,
            "ramp_down_start_clocks": ramp_down_time,
            "start_gain_time_us": start_gain_time / adc_clk_hz * 1e6,
            "ramp_up_time_us": ramp_up_clocks / adc_clk_hz * 1e6,
            "stop_gain_time_us": stop_gain_time / adc_clk_hz * 1e6,
            "ramp_down_start_time_us": ramp_down_time / adc_clk_hz * 1e6,
        },
        "gain": {
            "start_total_gain_db": start_total_gain_db,
            "stop_total_gain_db": stop_total_gain_db,
            "pos_step_db": pos_step_db,
            "neg_step_db": neg_step_db,
            "ramp_steps": ramp_steps,
        },
        "registers": {
            "A1": reg_A1,
            "A2": reg_A2,
            "A3": reg_A3,
            "A4": reg_A4,
            "A5": reg_A5,
            "B5": reg_B5,
            "B6": reg_B6,
            "B7": reg_B7,
        },
        "memory_words": mem_words,
    }


def print_dtgc_config(cfg):
    print("========== DTGC Timing ==========")
    for k, v in cfg["timing"].items():
        print(f"{k}: {v}")

    print("\n========== DTGC Gain ==========")
    for k, v in cfg["gain"].items():
        print(f"{k}: {v}")

    print("\n========== Register Values ==========")
    for reg, val in cfg["registers"].items():
        print(f"{reg}h = 0x{val:04X}")

    print("\n========== C Register Write Code ==========")
    print("afe5832_set_bits(0x00, 0x0010);   // DTGC_WR_EN = 1")
    print("afe5832_write(0xB5, 0x%04X);" % cfg["registers"]["B5"])

    mem_words = cfg["memory_words"]

    for i, word in enumerate(mem_words):
        addr = 0x01 + i
        print(f"afe5832_write(0x{addr:02X}, 0x{word:04X});   // MEM_WORD_{i}")

    for i in range(len(mem_words), 160):
        addr = 0x01 + i
        print(f"afe5832_write(0x{addr:02X}, 0x0000);   // Clear unused MEM_WORD_{i}")

    print("afe5832_write(0xA1, 0x%04X);" % cfg["registers"]["A1"])
    print("afe5832_write(0xA2, 0x%04X);" % cfg["registers"]["A2"])
    print("afe5832_write(0xA3, 0x%04X);" % cfg["registers"]["A3"])
    print("afe5832_write(0xA4, 0x%04X);" % cfg["registers"]["A4"])
    print("afe5832_write(0xA5, 0x%04X);" % cfg["registers"]["A5"])
    print("afe5832_write(0xB6, 0x%04X);" % cfg["registers"]["B6"])
    print("afe5832_write(0xB7, 0x%04X);" % cfg["registers"]["B7"])


if __name__ == "__main__":
    cfg = calc_dtgc_registers(
        adc_clk_hz=50e6,
        sound_speed=1540.0,
        lna_gain_db=21,
        pga_gain_db=27,
        dtgc_start_depth_cm=4.0,
        max_gain_depth_cm=10.0,
        ramp_down_depth_cm=12.0,
        start_gain_code=0,
        stop_gain_code=144,
        pos_step_code=1,     # 0.25 dB/step
        neg_step_code=255,    # 9 dB/step, fast ramp down
        slope_fac=0,
        mem_bank=0,
    )

    print_dtgc_config(cfg)