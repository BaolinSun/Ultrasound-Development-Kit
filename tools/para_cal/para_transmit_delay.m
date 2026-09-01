function delay_fpga = para_transmit_delay(output_dir, write_outputs, F)
%PARA_TRANSMIT_DELAY Generate the mapped TX7332 delay profile.
%
% delay_fpga = para_transmit_delay(output_dir, write_outputs, F) calculates all
% per-element parameters in natural physical order, maps them once at the
% TX hardware boundary, and packs 11 active register groups into each
% fixed 128-word block. JSON and C source output remain enabled by default.

this_dir = fileparts(mfilename('fullpath'));
addpath(fullfile(this_dir, 'scripts'));
if nargin < 3 || isempty(F)
    F = 80e-3;
end
if nargin < 2
    write_outputs = true;
end
if nargin < 1
    output_dir = [];
end
if write_outputs && isempty(output_dir)
    output_dir = fullfile(this_dir, '..', 'configs');
end

pitch       = 0.3e-3;
element_num = 64;
c           = 1540;
nxmits      = 64;

idx = (0:element_num-1)';
probe_geometry_vectors = zeros(element_num, 3);
probe_geometry_vectors(:, 1) = (idx - (element_num-1) / 2) * pitch;

angles = linspace(-45, 45, nxmits);
theta = deg2rad(angles);
xx = F * sin(theta);
yy = zeros(1, nxmits);
zz = F * cos(theta);

physical_delay = zeros(nxmits, element_num);
for ixmit = 1:nxmits
    physical_delay(ixmit, :) = cal_phased_array_transmit_delay(probe_geometry_vectors, [xx(ixmit), yy(ixmit), zz(ixmit)], F, c);
end

% Quantization stays in natural physical-element order. Mapping happens
% once, immediately before the TX7332 hardware-channel packing boundary.
physical_delay_int = int32(floor(physical_delay / 10e-9));
channel_map = load_probe_channel_map();
hardware_delay_int = map_physical_to_tx_hardware(physical_delay_int, channel_map);

% TX7332 profile-0 delay register layout.
grp1_addr = [0x28, 0x29, 0x2A, 0x2B, 0x2C, 0x2D, 0x2E, 0x2F];
grp1_hreg = [16, 12,  8,  4, 15, 11,  7,  3];
grp1_lreg = [14, 10,  6,  2, 13,  9,  5,  1];
grp2_addr = [0x20, 0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27];
grp2_hreg = [32, 28, 24, 20, 31, 27, 23, 19];
grp2_lreg = [30, 26, 22, 18, 29, 25, 21, 17];

delay_reg_addr = uint32([grp1_addr; grp2_addr]);
delay_high_channel = [grp1_hreg; grp2_hreg];
delay_low_channel = [grp1_lreg; grp2_lreg];

% Pulser enables are expressed only in natural physical-element order.
physical_tx_enable = true(nxmits, element_num);
hardware_tx_enable = logical(map_physical_to_tx_hardware(physical_tx_enable, channel_map));
pdn_mask_chip1 = zeros(nxmits, 1, 'uint32');
pdn_mask_chip2 = zeros(nxmits, 1, 'uint32');
for ixmit = 1:nxmits
    pdn_mask_chip1(ixmit) = pack_tx7332_pdn_mask(hardware_tx_enable(ixmit, 1:32));
    pdn_mask_chip2(ixmit) = pack_tx7332_pdn_mask(hardware_tx_enable(ixmit, 33:64));
end

WORDS_PER_GROUP = 8;
GROUPS_PER_BLOCK = 16;
BLOCKS_PER_LINE = 2;
ACTIVE_GROUPS = 11;

DELAY_GROUPS = 1:8;
GROUP_TR_SWITCH = 9;
GROUP_PULSER_MASK = 10;
GROUP_LOAD_PROFILE = 11;

% The two synthetic apertures are likewise defined in physical order.
physical_tr_enable = false(BLOCKS_PER_LINE, element_num);
physical_tr_enable(1, 1:32) = true;
physical_tr_enable(2, 33:64) = true;
hardware_tr_enable = logical(map_physical_to_tx_hardware(physical_tr_enable, channel_map));
tr_mask_chip1 = zeros(BLOCKS_PER_LINE, 1, 'uint32');
tr_mask_chip2 = zeros(BLOCKS_PER_LINE, 1, 'uint32');
for acquisition = 1:BLOCKS_PER_LINE
    tr_mask_chip1(acquisition) = pack_tx7332_tr_switch_mask(hardware_tr_enable(acquisition, 1:32));
    tr_mask_chip2(acquisition) = pack_tx7332_tr_switch_mask(hardware_tr_enable(acquisition, 33:64));
end

self_check_channel_mapping(channel_map, delay_reg_addr, delay_high_channel, delay_low_channel);

block_num = nxmits * BLOCKS_PER_LINE;
fpga_blocks = zeros(WORDS_PER_GROUP, GROUPS_PER_BLOCK, block_num, 'uint32');

for ixmit = 1:nxmits
    chip1_delay = uint32(hardware_delay_int(ixmit, 1:32));
    chip2_delay = uint32(hardware_delay_int(ixmit, 33:64));
    
    for acquisition = 1:BLOCKS_PER_LINE
        block_index = BLOCKS_PER_LINE * (ixmit - 1) + acquisition;
        
        % Groups 1..8: profile-0 delays for all 64 TX channels.
        for delay_group = DELAY_GROUPS
            fpga_blocks(:, delay_group, block_index) = pack_delay_group( chip1_delay, chip2_delay, delay_group, delay_reg_addr, delay_high_channel, delay_low_channel);
        end
        
        % Group 9: T/R switch mask (register 0x1A).
        fpga_blocks(:, GROUP_TR_SWITCH, block_index) = pack_global_register_group(0x1A, tr_mask_chip1(acquisition), tr_mask_chip2(acquisition));
        
        % Group 10: pulser power-down mask (register 0x1B).
        fpga_blocks(:, GROUP_PULSER_MASK, block_index) = pack_global_register_group(0x1B, pdn_mask_chip1(ixmit), pdn_mask_chip2(ixmit));
        
        % Group 11: load the delay profile written by groups 1..8.
        fpga_blocks(:, GROUP_LOAD_PROFILE, block_index) = pack_global_register_group(0x00, uint32(0x00000008), uint32(0x00000008));
    end
end

% The FPGA reads the active-group count from the final word of every block.
fpga_blocks(WORDS_PER_GROUP, GROUPS_PER_BLOCK, :) = uint32(ACTIVE_GROUPS);

verify_register_blocks(fpga_blocks, tr_mask_chip1, tr_mask_chip2, ...
    pdn_mask_chip1, pdn_mask_chip2, WORDS_PER_GROUP, GROUPS_PER_BLOCK, ...
    BLOCKS_PER_LINE, ACTIVE_GROUPS, GROUP_TR_SWITCH, ...
    GROUP_PULSER_MASK, GROUP_LOAD_PROFILE);

% MATLAB column-major flattening yields 8 words/group, 16 groups/block.
delay_fpga = reshape(fpga_blocks, 1, []);
assert(numel(delay_fpga) == 16384, '64 lines x 2 acquisitions x 128 words must equal 16384 words.');

if write_outputs
    write_bram_json( ...
        output_dir, ...
        'delay_profile_default.json', ...
        'Transmit delay profile generated by para_transmit_delay.m', ...
        {'delay_profile'}, ...
        {delay_fpga});
    write_C_array_file( ...
        fullfile(output_dir, 'c_array', 'delay_profile_fpga.c'), ...
        'u32 delay_profile[16384]', delay_fpga, 8, 8, sprintf('\t'), ...
        sprintf('#include "bram.h"\n\n\n'));
end
end

function words = pack_delay_group(chip1_delay, chip2_delay, group_index, delay_reg_addr, delay_high_channel, delay_low_channel)
%PACK_DELAY_GROUP Pack one delay-register group for all four SPI streams.

chip1_group1 = pack_delay_pair(chip1_delay(delay_high_channel(1, group_index)), chip1_delay(delay_low_channel(1, group_index)));
chip1_group2 = pack_delay_pair(chip1_delay(delay_high_channel(2, group_index)), chip1_delay(delay_low_channel(2, group_index)));
chip2_group1 = pack_delay_pair(chip2_delay(delay_high_channel(1, group_index)), chip2_delay(delay_low_channel(1, group_index)));
chip2_group2 = pack_delay_pair(chip2_delay(delay_high_channel(2, group_index)), chip2_delay(delay_low_channel(2, group_index)));

words = uint32([ ...
    delay_reg_addr(1, group_index); chip1_group1; ...
    delay_reg_addr(2, group_index); chip1_group2; ...
    delay_reg_addr(1, group_index); chip2_group1; ...
    delay_reg_addr(2, group_index); chip2_group2]);
end

function packed_delay = pack_delay_pair(high_delay, low_delay)
%PACK_DELAY_PAIR Pack two 13-bit TX7332 delay values into one register word.

delay_mask = uint32(0x1FFF);
high_delay = bitand(uint32(high_delay), delay_mask);
low_delay = bitand(uint32(low_delay), delay_mask);
packed_delay = bitor(bitshift(high_delay, 16), low_delay);
end

function words = pack_global_register_group( ...
    register_addr, chip1_data, chip2_data)
%PACK_GLOBAL_REGISTER_GROUP Broadcast one global register to four SPI paths.

register_addr = uint32(register_addr);
chip1_data = uint32(chip1_data);
chip2_data = uint32(chip2_data);
words = uint32([ ...
    register_addr; chip1_data; ...
    register_addr; chip1_data; ...
    register_addr; chip2_data; ...
    register_addr; chip2_data]);
end

function tr_mask = pack_tx7332_tr_switch_mask(active_local)
%PACK_TX7332_TR_SWITCH_MASK Generate the TX7332 register-0x1A mask.

assert(numel(active_local) == 32, ...
    'Each TX7332 T/R enable vector must contain exactly 32 channels.');
tr_bit_index = [16:31, 0:15];
disabled_local = ~logical(active_local(:).');
tr_mask = uint32(0);
for channel = find(disabled_local)
    tr_mask = bitor(tr_mask, ...
        bitshift(uint32(1), tr_bit_index(channel)));
end
end

function pdn_mask = pack_tx7332_pdn_mask(active_local)
%PACK_TX7332_PDN_MASK Generate the TX7332 register-0x1B mask.

assert(numel(active_local) == 32, ...
    'Each TX7332 pulser enable vector must contain exactly 32 channels.');
pdn_bit_index = [ ...
    16, 24, 17, 25, 18, 26, 19, 27, ...
    20, 28, 21, 29, 22, 30, 23, 31, ...
    0,  8,  1,  9,  2, 10,  3, 11, ...
    4, 12,  5, 13,  6, 14,  7, 15];
disabled_local = ~logical(active_local(:).');
pdn_mask = uint32(0);
for channel = find(disabled_local)
    pdn_mask = bitor(pdn_mask, ...
        bitshift(uint32(1), pdn_bit_index(channel)));
end
end

function verify_register_blocks(fpga_blocks, tr_mask_chip1, tr_mask_chip2, ...
    pdn_mask_chip1, pdn_mask_chip2, words_per_group, groups_per_block, ...
    blocks_per_line, active_groups, group_tr_switch, ...
    group_pulser_mask, group_load_profile)
%VERIFY_REGISTER_BLOCKS Check all fixed 128-word register block invariants.

block_num = size(fpga_blocks, 3);
assert(isequal(size(fpga_blocks), ...
    [words_per_group, groups_per_block, block_num]), ...
    'FPGA block dimensions are incorrect.');
expected_load_group = pack_global_register_group( ...
    0x00, uint32(0x00000008), uint32(0x00000008));

for block_index = 1:block_num
    ixmit = floor((block_index - 1) / blocks_per_line) + 1;
    acquisition = mod(block_index - 1, blocks_per_line) + 1;
    expected_tr_group = pack_global_register_group(0x1A, ...
        tr_mask_chip1(acquisition), tr_mask_chip2(acquisition));
    expected_pulser_group = pack_global_register_group(0x1B, ...
        pdn_mask_chip1(ixmit), pdn_mask_chip2(ixmit));
    assert(isequal(fpga_blocks(:, group_tr_switch, block_index), ...
        expected_tr_group), ...
        'Block %d has an invalid T/R register group.', block_index);
    assert(isequal(fpga_blocks(:, group_pulser_mask, block_index), ...
        expected_pulser_group), ...
        'Block %d has an invalid pulser register group.', block_index);
    assert(isequal(fpga_blocks(:, group_load_profile, block_index), ...
        expected_load_group), ...
        'Block %d has an invalid load-profile group.', block_index);
end

unused_groups = fpga_blocks(:, active_groups+1:groups_per_block, :);
unused_groups(words_per_group, groups_per_block-active_groups, :) = 0;
assert(all(unused_groups(:) == 0), ...
    'Register groups 12..16 must remain zero-filled.');
assert(all(reshape(fpga_blocks(words_per_group, groups_per_block, :), [], 1) ...
    == uint32(active_groups)), ...
    'Every block must advertise exactly 11 active register groups.');
end

function self_check_channel_mapping(channel_map, delay_reg_addr, ...
    delay_high_channel, delay_low_channel)
%SELF_CHECK_CHANNEL_MAPPING Verify delays and sparse 0x1A/0x1B masks.

physical_delay = uint32(1:64);
expected_hardware = uint32(map_physical_to_tx_hardware( ...
    physical_delay, channel_map));
chip1_delay = expected_hardware(1:32);
chip2_delay = expected_hardware(33:64);
decoded_hardware = zeros(1, 64, 'uint32');

for delay_group = 1:8
    words = pack_delay_group(chip1_delay, chip2_delay, delay_group, ...
        delay_reg_addr, delay_high_channel, delay_low_channel);
    assert(isequal(words([1, 3, 5, 7]), uint32([ ...
        delay_reg_addr(1, delay_group); ...
        delay_reg_addr(2, delay_group); ...
        delay_reg_addr(1, delay_group); ...
        delay_reg_addr(2, delay_group)])), ...
        'Delay group %d has an invalid SPI address order.', delay_group);
    decoded_hardware = decode_delay_group(decoded_hardware, words, ...
        delay_group, delay_high_channel, delay_low_channel);
end

assert(isequal(decoded_hardware, expected_hardware), ...
    'The eight delay groups do not preserve all 64 mapped channels.');
assert(decoded_hardware(1) == 2 && decoded_hardware(2) == 1 && ...
    decoded_hardware(31) == 32 && decoded_hardware(32) == 31 && ...
    decoded_hardware(33) == 34 && decoded_hardware(34) == 33 && ...
    decoded_hardware(63) == 64 && decoded_hardware(64) == 63, ...
    'Boundary physical elements did not reach the expected TX channels.');

tr_bit_index = [16:31, 0:15];
pdn_bit_index = [ ...
    16, 24, 17, 25, 18, 26, 19, 27, ...
    20, 28, 21, 29, 22, 30, 23, 31, ...
    0,  8,  1,  9,  2, 10,  3, 11, ...
    4, 12,  5, 13,  6, 14,  7, 15];

for physical = 1:64
    physical_enable = true(1, 64);
    physical_enable(physical) = false;
    hardware_enable = logical(map_physical_to_tx_hardware( ...
        physical_enable, channel_map));
    
    tr1 = pack_tx7332_tr_switch_mask(hardware_enable(1:32));
    tr2 = pack_tx7332_tr_switch_mask(hardware_enable(33:64));
    pdn1 = pack_tx7332_pdn_mask(hardware_enable(1:32));
    pdn2 = pack_tx7332_pdn_mask(hardware_enable(33:64));
    tr_words = pack_global_register_group(0x1A, tr1, tr2);
    pdn_words = pack_global_register_group(0x1B, pdn1, pdn2);
    
    expected_tr_words = [uint32(0x1A); tr1; uint32(0x1A); tr1; ...
        uint32(0x1A); tr2; uint32(0x1A); tr2];
    expected_pdn_words = [uint32(0x1B); pdn1; uint32(0x1B); pdn1; ...
        uint32(0x1B); pdn2; uint32(0x1B); pdn2];
    assert(isequal(tr_words, expected_tr_words), ...
        'Physical element %d has invalid 0x1A SPI words.', physical);
    assert(isequal(pdn_words, expected_pdn_words), ...
        'Physical element %d has invalid 0x1B SPI words.', physical);
    assert(sum(bitget(tr1, 1:32)) + sum(bitget(tr2, 1:32)) == 1, ...
        'Physical element %d set an invalid number of 0x1A bits.', physical);
    assert(sum(bitget(pdn1, 1:32)) + sum(bitget(pdn2, 1:32)) == 1, ...
        'Physical element %d set an invalid number of 0x1B bits.', physical);
    
    device = channel_map.tx_device(physical);
    local_channel = channel_map.tx_local_channel(physical);
    tr_masks = [tr1, tr2];
    pdn_masks = [pdn1, pdn2];
    assert(bitget(tr_masks(device), tr_bit_index(local_channel) + 1) == 1, ...
        'Physical element %d reached the wrong 0x1A bit.', physical);
    assert(bitget(pdn_masks(device), pdn_bit_index(local_channel) + 1) == 1, ...
        'Physical element %d reached the wrong 0x1B bit.', physical);
end

fprintf('PASS: TX delay, T/R and pulser channel mapping self-check\n');
end

function decoded = decode_delay_group(decoded, words, group_index, ...
    delay_high_channel, delay_low_channel)
%DECODE_DELAY_GROUP Decode one group for self-checking only.

chip_offsets = [0, 0, 32, 32];
row_indices = [1, 2, 1, 2];
data_indices = [2, 4, 6, 8];
for stream = 1:4
    packed = words(data_indices(stream));
    high_delay = bitand(bitshift(packed, -16), uint32(0x1FFF));
    low_delay = bitand(packed, uint32(0x1FFF));
    high_channel = chip_offsets(stream) + ...
        delay_high_channel(row_indices(stream), group_index);
    low_channel = chip_offsets(stream) + ...
        delay_low_channel(row_indices(stream), group_index);
    decoded(high_channel) = high_delay;
    decoded(low_channel) = low_delay;
end
end

function delay_vec = cal_phased_array_transmit_delay( ...
    probe_geometry_vectors, focus_point, F, c)
%CAL_PHASED_ARRAY_TRANSMIT_DELAY Calculate delays in physical order.

fp = focus_point(:)';
diff = probe_geometry_vectors - fp;
L = sqrt(sum(diff .^ 2, 2));
delay_vec = 5e-6 - (L - F) / c;
end
