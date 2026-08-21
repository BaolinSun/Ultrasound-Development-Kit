function channel_map = load_probe_channel_map()
%LOAD_PROBE_CHANNEL_MAP Load and validate the fixed probe/TX wiring map.

this_dir = fileparts(mfilename('fullpath'));
map_path = fullfile(this_dir, '..', 'configs', 'probe_channel_map.csv');
channel_map = readtable(map_path, 'Delimiter', ',');

expected_columns = {'physical_element', 'tx_global_channel', 'tx_device', ...
                    'tx_local_channel', 'synthetic_aperture', 'afe_channel'};
assert(isequal(channel_map.Properties.VariableNames, expected_columns), ...
    'probe_channel_map.csv has unexpected columns.');
assert(height(channel_map) == 64, ...
    'probe_channel_map.csv must contain exactly 64 physical elements.');

physical_element = double(channel_map.physical_element);
tx_global_channel = double(channel_map.tx_global_channel);
assert(isequal(physical_element, (1:64)'), ...
    'physical_element must be the ordered sequence 1..64.');
assert(isequal(sort(tx_global_channel), (1:64)'), ...
    'tx_global_channel must be a bijection over 1..64.');

for physical = 1:64
    expected_tx_channel = physical + 1;
    if mod(physical, 2) == 0
        expected_tx_channel = physical - 1;
    end
    expected_device = floor((physical - 1) / 32) + 1;
    expected_local = mod(expected_tx_channel - 1, 32) + 1;
    expected_aperture = floor((physical - 1) / 32);

    assert(channel_map.tx_global_channel(physical) == expected_tx_channel, ...
        'Physical element %d has an invalid TX global channel.', physical);
    assert(channel_map.tx_device(physical) == expected_device, ...
        'Physical element %d has an invalid TX device.', physical);
    assert(channel_map.tx_local_channel(physical) == expected_local, ...
        'Physical element %d has an invalid TX local channel.', physical);
    assert(channel_map.synthetic_aperture(physical) == expected_aperture, ...
        'Physical element %d has an invalid synthetic aperture.', physical);
    assert(channel_map.afe_channel(physical) == expected_local, ...
        'Physical element %d has an invalid AFE channel.', physical);
end

for aperture = 0:1
    afe_channels = channel_map.afe_channel( ...
        channel_map.synthetic_aperture == aperture);
    assert(isequal(sort(double(afe_channels)), (1:32)'), ...
        'AFE channels in aperture %d are not a 1..32 bijection.', aperture);
end
end
