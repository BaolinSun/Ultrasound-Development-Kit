function ultrasound_bmode_gui_pro()
% ULTRASOUND_BMODE_GUI_PRO
% 工程交付级：参数默认 + 灰度映射 + 一键保存/恢复

%% ================== 默认参数（文件开头） ==================
params.fs = 25e6;
params.fc = 4e6;
params.c = 1540;
params.theta_min_deg = -45;
params.theta_max_deg = 45;
params.range_start_m = 0;
params.log10_frac_bits = 14;
params.cart_step_m = (params.c / params.fc) / 2;
params.input_mat = 'rfdata.mat';

% ---- 默认显示参数 ----
params.dynamic_range_db   = 45;
params.brightness_offset  = 3;
params.contrast_gain      = 1.0;
params.noise_floor_db     = -45;

%% ================== 加载数据 ==================
in = load(params.input_mat);
polar_data = double(in.rfdata);
[num_lines, num_range] = size(polar_data);

angles_rad = deg2rad(linspace(params.theta_min_deg, params.theta_max_deg, num_lines));
range_step = params.c / (2 * params.fs);
r_axis = params.range_start_m + (0:num_range-1)' * range_step;

%% ================== 笛卡尔网格 ==================
r_max = r_axis(end);
x_max = r_max * sind(45);
x_axis = -x_max:params.cart_step_m:x_max;
z_axis = params.range_start_m:params.cart_step_m:r_max;
[xx, zz] = meshgrid(x_axis, z_axis);
theta_q = atan2(xx, zz);
r_q = hypot(xx, zz);

outside = theta_q < min(angles_rad) | theta_q > max(angles_rad) | ...
          r_q < min(r_axis) | r_q > max(r_axis);

%% ================== 扫描变换 ==================
fprintf('Scan converting...\n'); tic;
F = griddedInterpolant({angles_rad(:), r_axis(:)}, polar_data, 'linear', 'none');
cart_db = F(theta_q, r_q);
cart_db(outside) = NaN;
fprintf('Done (%.2f s)\n', toc);

%% ================== GUI ==================
fig = figure('Name','Ultrasound B-mode (Pro)','NumberTitle','off',...
             'Color','w','Position',[100 60 1300 820]);

%% ---- 图像 ----
axImg = axes('Parent',fig,'Units','normalized',...
             'Position',[0.04 0.42 0.58 0.52]);
axis(axImg,'image');
set(axImg,'YDir','reverse','Color',[0 0 0]);
xlabel(axImg,'Lateral [mm]'); ylabel(axImg,'Depth [mm]');
title(axImg,'B-mode Image');

hImg = imagesc(axImg, x_axis*1e3, z_axis*1e3, zeros(size(cart_db)), [0 255]);

%% ---- 灰度映射曲线 ----
axCurve = axes('Parent',fig,'Units','normalized',...
              'Position',[0.64 0.42 0.32 0.52]);
grid(axCurve,'on'); hold(axCurve,'on');
xlabel(axCurve,'Input Gray'); ylabel(axCurve,'Output Gray');
title(axCurve,'Graymap Curve');

ctrlX = linspace(0,255,9);
ctrlY = linspace(0,255,9);
lut = uint8(0:255);

hLine = plot(axCurve, 0:255, lut, 'k-', 'LineWidth',1.5);
hPts  = plot(axCurve, ctrlX, ctrlY, 'ro','MarkerSize',8,'MarkerFaceColor','r');

%% ================== 参数滑块 ==================
panelY = 0.05;

uicontrol('Style','text','Position',[60 panelY+80 120 22],'String','Dynamic Range (dB)');
hDR = uicontrol('Style','slider','Min',20,'Max',70,'Value',params.dynamic_range_db,...
               'Position',[60 panelY+50 160 22],'Callback',@updateImage);

uicontrol('Style','text','Position',[260 panelY+80 120 22],'String','Brightness (dB)');
hBright = uicontrol('Style','slider','Min',-10,'Max',20,'Value',params.brightness_offset,...
                   'Position',[260 panelY+50 160 22],'Callback',@updateImage);

uicontrol('Style','text','Position',[460 panelY+80 120 22],'String','Contrast Gain');
hContrast = uicontrol('Style','slider','Min',0.5,'Max',2,'Value',params.contrast_gain,...
                     'Position',[460 panelY+50 160 22],'Callback',@updateImage);

uicontrol('Style','text','Position',[660 panelY+80 120 22],'String','Noise Floor (dB)');
hNoise = uicontrol('Style','slider','Min',-60,'Max',-20,'Value',params.noise_floor_db,...
                  'Position',[660 panelY+50 160 22],'Callback',@updateImage);

%% ================== 控制按钮 ==================
uicontrol('Style','pushbutton','String','Save Optimized Params',...
          'Position',[880 panelY+85 180 30],'Callback',@saveParams);

uicontrol('Style','pushbutton','String','Reset Graymap to Linear',...
          'Position',[880 panelY+50 180 30],'Callback',@resetLUT);

uicontrol('Style','pushbutton','String','Save Graymap LUT',...
          'Position',[1060 panelY+85 160 30],'Callback',@saveLUT);

uicontrol('Style','pushbutton','String','Load Graymap LUT',...
          'Position',[1060 panelY+50 160 30],'Callback',@loadLUT);
      uicontrol('Style','pushbutton','String','Save Image (Physical Scale)',...
          'Position',[880 panelY+15 180 30],'Callback',@saveImage);

%% ================== 状态 ==================
currentPt = 0;

%% ================== 更新图像 ==================
function updateImage(~,~)
    DR     = hDR.Value;
    bright = hBright.Value;
    gain   = hContrast.Value;
    floorV = hNoise.Value;

    cart_log10 = cart_db ./ (2^params.log10_frac_bits);
    cart_log10_max = max(cart_log10(isfinite(cart_log10)));
    db = 20 * (cart_log10 - cart_log10_max);

    db = db + bright;
    db = gain * db;
    db(db < floorV) = floorV;
    db = max(db, -DR);
    db = min(db, 0);

    img = uint8(round((db + DR) / DR * 255));
    img(outside) = 0;

    img = lut(img + 1);
    
    mean(img(:))
    std(double(img(:)))
    
    colormap(axImg, gray(256));   % <<< 强制使用 gray(256)
    set(hImg,'CData',img);
    title(axImg,sprintf('DR=%.1f dB | Bright=%.1f dB | Gain=%.2f | Floor=%.1f dB',...
          DR,bright,gain,floorV));
end

%% ================== 曲线拖拽 ==================
set(hPts,'ButtonDownFcn',@startDrag);

function startDrag(~,~)
    cp = get(axCurve,'CurrentPoint');
    d = (ctrlX - cp(1,1)).^2 + (ctrlY - cp(1,2)).^2;
    [~, currentPt] = min(d);
    set(fig,'WindowButtonMotionFcn',@dragging);
    set(fig,'WindowButtonUpFcn',@stopDrag);
end

function dragging(~,~)
    cp = get(axCurve,'CurrentPoint');
    x = max(0,min(255,cp(1,1)));
    y = max(0,min(255,cp(1,2)));
    ctrlX(currentPt) = x;
    ctrlY(currentPt) = y;
    set(hPts,'XData',ctrlX,'YData',ctrlY);
    updateLUT();
end

function stopDrag(~,~)
    set(fig,'WindowButtonMotionFcn','');
    set(fig,'WindowButtonUpFcn','');
    currentPt = 0;
end

function updateLUT()
    xq = 0:255;
    [xs, idx] = sort(ctrlX);
    ys = ctrlY(idx);
    lut = uint8(interp1(xs, ys, xq, 'pchip', 'extrap'));
    set(hLine,'YData',lut);
    updateImage();
end

%% ================== 保存优化参数 ==================
function saveParams(~,~)
    filename = 'optimized_bmode_params.txt';
    fid = fopen(filename,'w');
    fprintf(fid,'# Optimized B-mode Parameters\n');
    fprintf(fid,'# Generated: %s\n\n', datetime('now'));
    fprintf(fid,'dynamic_range_db   = %.1f\n', hDR.Value);
    fprintf(fid,'brightness_offset  = %.1f\n', hBright.Value);
    fprintf(fid,'contrast_gain       = %.2f\n', hContrast.Value);
    fprintf(fid,'noise_floor_db      = %.1f\n', hNoise.Value);
    fclose(fid);
    msgbox(['Saved: ' filename],'Done');
end

%% ================== 重置 Graymap ==================
function resetLUT(~,~)
    ctrlX = linspace(0,255,9);
    ctrlY = linspace(0,255,9);
    set(hPts,'XData',ctrlX,'YData',ctrlY);
    updateLUT();
end

%% ================== 保存 LUT ==================
function saveLUT(~,~)
    xq = 0:255;
    [xs, idx] = sort(ctrlX);
    ys = ctrlY(idx);
    lut = uint8(interp1(xs, ys, xq, 'pchip', 'extrap'));

    filename = 'graymap_lut.txt';
    fid = fopen(filename,'w');
    fprintf(fid,'# Graymap LUT (256 entries)\n');
    fprintf(fid,'lut = [\n');
    fprintf(fid,'%d', lut(1));
    for i = 2:256
        fprintf(fid,', %d', lut(i));
    end
    fprintf(fid,'\n];\n');
    fclose(fid);
    msgbox(['Graymap LUT saved: ' filename],'Done');
end

%% ================== 加载 LUT ==================
function loadLUT(~,~)
    [file, path] = uigetfile('*.txt','Select Graymap LUT File');
    if isequal(file,0), return; end

    % ---- 读取 LUT ----
    fid = fopen(fullfile(path,file),'r');
    txt = fread(fid,'char=>char')';
    fclose(fid);

    % 去掉 # 注释
    txt = regexprep(txt,'#.*?\n','');
    
    % 去掉 lut = 和 ;
    txt = regexprep(txt,'lut\s*=\s*','');
    txt = regexprep(txt,';','');
    
    % 去掉 [] 和 ,
    txt = strrep(txt,'[','');
    txt = strrep(txt,']','');
    txt = strrep(txt,',',' ');
    
    % 解析数值
    lut = uint8(str2double(strsplit(strtrim(txt))));

    if numel(lut) ~= 256
        errordlg('LUT must contain 256 entries.');
        return;
    end

    lut = uint8(lut);

    % ---- 更新曲线显示 ----
    ctrlX = linspace(0,255,9);
    ctrlY = interp1(0:255, double(lut), ctrlX, 'pchip');
    set(hPts,'XData',ctrlX,'YData',ctrlY);

    % ---- 更新 LUT 并刷新图像 ----
    updateLUT();
    msgbox('Graymap LUT loaded','Done');
end

%%
function saveImage(~,~)
    % ---- 用当前参数重新生成 uint8 ----
    DR     = hDR.Value;
    bright = hBright.Value;
    gain   = hContrast.Value;
    floorV = hNoise.Value;

    cart_log10 = cart_db ./ (2^params.log10_frac_bits);
    cart_log10_max = max(cart_log10(isfinite(cart_log10)));
    db = 20 * (cart_log10 - cart_log10_max);

    db = db + bright;
    db = gain * db;
    db(db < floorV) = floorV;
    db = max(db, -DR);
    db = min(db, 0);

    img = uint8(round((db + DR) / DR * 255));
    img(outside) = 0;

    % ---- 应用当前 LUT ----
    img = lut(img + 1);

    % ---- 新建 figure，严格物理比例 ----
    figOut = figure('Visible','off','Color','w');
    imagesc(x_axis*1e3, z_axis*1e3, img, [0 255]);
    axis image;
    set(gca,'YDir','reverse','Color',[0 0 0]);
    colormap(gray(256));
    xlabel('Lateral distance [mm]');
    ylabel('Depth [mm]');
    title(sprintf('DR=%.1f dB | Bright=%.1f dB | Gain=%.2f | Floor=%.1f dB',...
          DR,bright,gain,floorV));

    % ---- 保存 ----
    exportgraphics(figOut,'bmode_output.png','Resolution',200);
    close(figOut);

    msgbox('Image saved: bmode_output.png','Done');
end
%% 初始化
updateImage();
end