function ultrasound_bmode_gui_export()
% ULTRASOUND_BMODE_GUI_EXPORT
% 实时调节 + 一键导出参数

%% ========== 参数 ==========
params.fs = 25e6;
params.fc = 4e6;
params.c = 1540;
params.theta_min_deg = -45;
params.theta_max_deg = 45;
params.range_start_m = 0;
params.log10_frac_bits = 14;
params.cart_step_m = (params.c / params.fc) / 2;
params.input_mat = 'rfdata.mat';

%% ========== 加载数据 ==========
in = load(params.input_mat);
polar_data = double(in.rfdata);
[num_lines, num_range] = size(polar_data);

angles_rad = deg2rad(linspace(params.theta_min_deg, params.theta_max_deg, num_lines));
range_step = params.c / (2 * params.fs);
r_axis = params.range_start_m + (0:num_range-1)' * range_step;

%% ========== 笛卡尔网格 ==========
r_max = r_axis(end);
x_max = r_max * sind(45);
x_axis = -x_max:params.cart_step_m:x_max;
z_axis = params.range_start_m:params.cart_step_m:r_max;
[xx, zz] = meshgrid(x_axis, z_axis);
theta_q = atan2(xx, zz);
r_q = hypot(xx, zz);

outside = theta_q < min(angles_rad) | theta_q > max(angles_rad) | ...
          r_q < min(r_axis) | r_q > max(r_axis);

%% ========== 扫描变换 ==========
fprintf('Scan converting...\n');
tic;
F = griddedInterpolant({angles_rad(:), r_axis(:)}, polar_data, 'linear', 'none');
cart_db = F(theta_q, r_q);
cart_db(outside) = NaN;
fprintf('Done (%.2f s)\n', toc);

%% ========== GUI ==========
fig = figure('Name', 'Ultrasound B-mode Tuner', ...
             'NumberTitle', 'off', ...
             'Color', 'w', ...
             'Position', [300 200 960 640]);

ax = axes('Parent', fig, ...
          'Units', 'normalized', ...
          'Position', [0.08 0.28 0.78 0.68]);
axis(ax, 'image');
colormap(ax, gray);
set(ax, 'YDir', 'reverse', 'Color', [0 0 0]);
xlabel(ax, 'Lateral [mm]');
ylabel(ax, 'Depth [mm]');
title(ax, 'Drag sliders to tune dynamic range');

hImg = imagesc(ax, x_axis*1e3, z_axis*1e3, zeros(size(cart_db)), [0 255]);
colorbar(ax);

%% ========== 滑块 ==========
uicontrol('Style','text','Position',[50 170 140 20],'String','Dynamic Range (dB)');
hDR = uicontrol('Style','slider','Min',20,'Max',70,'Value',52,...
               'Position',[50 140 200 25],'Callback',@updateImage);

uicontrol('Style','text','Position',[270 170 140 20],'String','Brightness (dB)');
hBright = uicontrol('Style','slider','Min',-10,'Max',20,'Value',6,...
                   'Position',[270 140 200 25],'Callback',@updateImage);

uicontrol('Style','text','Position',[490 170 140 20],'String','Contrast Gain');
hContrast = uicontrol('Style','slider','Min',0.5,'Max',2,'Value',1,...
                     'Position',[490 140 200 25],'Callback',@updateImage);

uicontrol('Style','text','Position',[710 170 140 20],'String','Noise Floor (dB)');
hNoise = uicontrol('Style','slider','Min',-60,'Max',-20,'Value',-45,...
                  'Position',[710 140 200 25],'Callback',@updateImage);

%% ========== 导出按钮 ==========
uicontrol('Style','pushbutton', ...
          'String','Export Params to TXT', ...
          'FontWeight','bold', ...
          'Position',[400 60 200 35], ...
          'Callback',@exportParams);

%% ========== 更新图像 ==========
function updateImage(~,~)
    DR     = hDR.Value;
    bright = hBright.Value;
    gain   = hContrast.Value;
    floor  = hNoise.Value;

    cart_log10 = cart_db ./ (2^params.log10_frac_bits);
    cart_log10_max = max(cart_log10(isfinite(cart_log10)));
    db = 20 * (cart_log10 - cart_log10_max);

    db = db + bright;
    db = gain * db;
    db(db < floor) = floor;

    db = max(db, -DR);
    db = min(db, 0);

    img = uint8(round((db + DR) / DR * 255));
    img(outside) = 0;
    colormap(gray(256));
    set(hImg, 'CData', img);
    title(ax, sprintf('DR=%.1f dB | Bright=%.1f dB | Gain=%.2f | Floor=%.1f dB', ...
          DR, bright, gain, floor));
end

%% ========== 导出参数 ==========
function exportParams(~,~)
    DR     = hDR.Value;
    bright = hBright.Value;
    gain   = hContrast.Value;
    floor  = hNoise.Value;

    filename = 'bmode_params.txt';
    fid = fopen(filename, 'w');

    fprintf(fid, '# Ultrasound B-mode Parameters\n');
    fprintf(fid, '# Generated: %s\n\n', datetime('now'));
    fprintf(fid, 'dynamic_range_db   = %.1f\n', DR);
    fprintf(fid, 'brightness_offset  = %.1f\n', bright);
    fprintf(fid, 'contrast_gain       = %.2f\n', gain);
    fprintf(fid, 'noise_floor_db      = %.1f\n', floor);

    fclose(fid);

    msgbox(['Parameters exported to: ' filename], 'Export Done');
end

%% 初始化
updateImage();
end