function hardware_values = map_physical_to_tx_hardware(physical_values, channel_map)
%MAP_PHYSICAL_TO_TX_HARDWARE Convert natural element order to TX channels.
% Shared helper for the transmit-delay generator.
%
% Input column N is physical element N. Output column N is TX global
% hardware channel N. The explicit map prevents compensation in packers.

if nargin < 2 || isempty(channel_map)
    channel_map = load_probe_channel_map();
end

assert(size(physical_values, 2) == 64, ...
    'physical_values must have exactly 64 physical-element columns.');

hardware_values = zeros(size(physical_values), 'like', physical_values);
hardware_values(:, channel_map.tx_global_channel) = ...
    physical_values(:, channel_map.physical_element);
end
