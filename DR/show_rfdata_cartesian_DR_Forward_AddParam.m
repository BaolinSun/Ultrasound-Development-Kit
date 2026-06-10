% clear; close all; clc;

%% ultrasound parameters
params.fs = 25e6;                       % Sampling frequency [Hz]
params.fc = 4e6;                        % Center frequency [Hz]
params.c = 1540;                        % Speed of sound [m/s]
params.theta_min_deg = -45;             % First scan-line angle [deg]
params.theta_max_deg = 45;              % Last scan-line angle [deg]
params.range_start_m = 0;               % Radius of the first sample [m]
params.dynamic_range_db = 45;           % Display dynamic range [dB]
params.brightness_offset = 3;
params.contrast_gain = 1.0; % Low:0.8  Normal:1.0  High:1.2
params.noise_floor_db = -45;
params.log10_frac_bits = 14;            % rfdata log10 fixed-point fractional bits (FPGA log10: Q2.14)
params.cart_step_m = (params.c / params.fc) / 2;  % Cartesian pixel spacing [m]

params.input_mat = 'rfdata.mat';
params.output_png = 'rfdata_cartesian.png';
params.output_uint8_png = 'rfdata_cartesian_uint8.png';

%% Load polar log-compressed beamformed data
in = load(params.input_mat);

polar_data = in.rfdata;
polar_data = double(polar_data);
[num_lines, num_range_samples] = size(polar_data);


%% Build polar axes
angles_deg = linspace(params.theta_min_deg, params.theta_max_deg, num_lines);
angles_rad = deg2rad(angles_deg);
range_step_m = params.c / (2 * params.fs);
r_axis_m = params.range_start_m + (0:num_range_samples - 1) * range_step_m;


%% Build Cartesian query grid
r_max_m = r_axis_m(end);
x_max_m = r_max_m * sind(max(abs([params.theta_min_deg, params.theta_max_deg])));
x_axis_m = -x_max_m : params.cart_step_m : x_max_m;
z_axis_m = params.range_start_m : params.cart_step_m : r_max_m;

[xx, zz] = meshgrid(x_axis_m, z_axis_m);
theta_query = atan2(xx, zz);
r_query = hypot(xx, zz);


%% ================== Dynamic range（扫描变换前） ==================

% 1. 定点 → 浮点 log10
cart_log10 = double(polar_data) ./ (2 ^ params.log10_frac_bits);

% 2. 峰值归一化
cart_log10_max = max(cart_log10(:));
cart_log10 = cart_log10 - cart_log10_max;

% 3. 转 dB
cart_db = 20 * cart_log10;

%% ---------- 新增调节参数 ----------
cart_db = cart_db + params.brightness_offset;                 % 亮度
cart_db = params.contrast_gain * cart_db;                      % 对比度
cart_db(cart_db < params.noise_floor_db) = params.noise_floor_db; % 噪声门限
%% -----------------------------------

% 4. 动态范围裁剪
cart_db = max(cart_db, -params.dynamic_range_db);
cart_db = min(cart_db, 0);

% 5. 非法值处理
cart_db(isnan(cart_db) | isinf(cart_db)) = -params.dynamic_range_db;

%% ================== Scan conversion ==================
F = griddedInterpolant({angles_rad(:), r_axis_m(:)}, cart_db, 'linear', 'none');
cart_data = F(theta_query, r_query);

outside = theta_query < min(angles_rad) | theta_query > max(angles_rad) | ...
          r_query < min(r_axis_m) | r_query > max(r_axis_m);
cart_data(outside) = -params.dynamic_range_db;

%% ================== uint8 ==================
cart_uint8 = uint8(round( ...
    (cart_data + params.dynamic_range_db) ./ params.dynamic_range_db .* 255 ));
cart_uint8(outside) = uint8(0);

%% Graymap
gaylut = load_graymap_txt('graymap_lut.txt');
graymapout = apply_graymap(cart_uint8, gaylut);

%% Display
fig = figure('Color', 'w', 'Name', 'rfdata Cartesian B-mode');
imagesc(x_axis_m * 1e3, z_axis_m * 1e3, graymapout, [0 255]);
set(gca, 'Color', [0 0 0], 'YDir', 'reverse');
axis image;
colormap(gray);
colorbar;
xlabel('Lateral distance [mm]');  
ylabel('Depth [mm]');
title(sprintf('Cartesian B-mode, dynamic range %.1f dB', ...
    params.dynamic_range_db));

exportgraphics(fig, params.output_png, 'Resolution', 200);
imwrite(graymapout, params.output_uint8_png);

fprintf('Loaded rfdata: %d lines x %d range samples\n', num_lines, num_range_samples);
fprintf('Range axis: %.3f mm to %.3f mm, step %.3f mm\n', ...
    r_axis_m(1) * 1e3, r_axis_m(end) * 1e3, range_step_m * 1e3);
fprintf('Cartesian image: %d z-pixels x %d x-pixels\n', ...
    numel(z_axis_m), numel(x_axis_m));
fprintf('Display dynamic range: %.1f dB\n', params.dynamic_range_db);
