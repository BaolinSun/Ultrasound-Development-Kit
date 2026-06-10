clear all;

fs = 25e6;

raw_data = readmatrix('rfdata\rfdata_1.csv');

[Nsamp, Nch] = size(raw_data);

% fprintf('Nsamp = %d\n', Nsamp);
% fprintf('Nch   = %d\n', Nch);
% 显示矩阵图像
% img_data = double(raw_data);
% img_data = abs(img_data);

% img_data = img_data / max(img_data(:));
% img_data = 20 * log10(img_data + eps);
% imagesc(img_data);
% colorbar;



t = (0:Nsamp-1) / fs;   % 单位：s

channel_id = 46;
rfdata_46 = raw_data(:, channel_id);
[f_half_46, mag_db_46, fc_46] = rfdata_spectrum(rfdata_46, fs);
fprintf('Peak frequency = %.2f MHz\n', fc_46 / 1e6);

channel_id = 47;
rfdata_47 = raw_data(:, channel_id);
[f_half_47, mag_db_47, fc_47] = rfdata_spectrum(rfdata_47, fs);
fprintf('Peak frequency = %.2f MHz\n', fc_47 / 1e6);

channel_id = 48;
rfdata_48 = raw_data(:, channel_id);
[f_half_48, mag_db_48, fc_48] = rfdata_spectrum(rfdata_48, fs);
fprintf('Peak frequency = %.2f MHz\n', fc_48 / 1e6);

channel_id = 49;
rfdata_49 = raw_data(:, channel_id);
[f_half_49, mag_db_49, fc_49] = rfdata_spectrum(rfdata_49, fs);
fprintf('Peak frequency = %.2f MHz\n', fc_49 / 1e6);

figure;

subplot(4,2,1);
plot(t * 1e6, rfdata_46, 'LineWidth', 1.2);
xlabel('Time (\mus)');
ylabel('Amplitude');
title(['Time-Domain RF Signal - Channel ', num2str(46)]);
grid on;
set(gca, 'FontSize', 12);

subplot(4,2,2);
plot(f_half_46/1e6, mag_db_46, 'LineWidth', 1.2);
xlabel('Frequency (MHz)');
ylabel('Magnitude (dB)');
title(['Frequency Spectrum - Channel ', num2str(46)]);
grid on;
xlim([0 10]);      % 对 2.5 MHz 探头，通常看 0~10 MHz 即可
% ylim([-80 5]);
set(gca, 'FontSize', 12);

subplot(4,2,3);
plot(t * 1e6, rfdata_47, 'LineWidth', 1.2);
xlabel('Time (\mus)');
ylabel('Amplitude');
title(['Time-Domain RF Signal - Channel ', num2str(47)]);
grid on;
ylim([-10 10]);
set(gca, 'FontSize', 12);

subplot(4,2,4);
plot(f_half_47/1e6, mag_db_47, 'LineWidth', 1.2);
xlabel('Frequency (MHz)');
ylabel('Magnitude (dB)');
title(['Frequency Spectrum - Channel ', num2str(47)]);
grid on;
xlim([0 10]);      % 对 2.5 MHz 探头，通常看 0~10 MHz 即可
% ylim([-80 5]);
set(gca, 'FontSize', 12);

subplot(4,2,5);
plot(t * 1e6, rfdata_48, 'LineWidth', 1.2);
xlabel('Time (\mus)');
ylabel('Amplitude');
title(['Time-Domain RF Signal - Channel ', num2str(48)]);
grid on;
ylim([-10 10]);
set(gca, 'FontSize', 12);

subplot(4,2,6);
plot(f_half_48/1e6, mag_db_48, 'LineWidth', 1.2);
xlabel('Frequency (MHz)');
ylabel('Magnitude (dB)');
title(['Frequency Spectrum - Channel ', num2str(48)]);
grid on;
xlim([0 10]);      % 对 2.5 MHz 探头，通常看 0~10 MHz 即可
% ylim([-80 5]);
set(gca, 'FontSize', 12);

subplot(4,2,7);
plot(t * 1e6, rfdata_49, 'LineWidth', 1.2);
xlabel('Time (\mus)');
ylabel('Amplitude');
title(['Time-Domain RF Signal - Channel ', num2str(49)]);
grid on;
ylim([-10 10]);
set(gca, 'FontSize', 12);

subplot(4,2,8);
plot(f_half_49/1e6, mag_db_49, 'LineWidth', 1.2);
xlabel('Frequency (MHz)');
ylabel('Magnitude (dB)');
title(['Frequency Spectrum - Channel ', num2str(49)]);
grid on;
xlim([0 10]);      % 对 2.5 MHz 探头，通常看 0~10 MHz 即可
% ylim([-80 5]);
set(gca, 'FontSize', 12);



function [f_half, mag_db, fc] = rfdata_spectrum(rfdata, fs)
% Input:
%   rf_data : RF data matrix, size = Nsamp × Nch
%   fs      : Sampling frequency, Hz
%   ch      : Channel index

    rf = rfdata;

    % win = hanning(length(rf));
    % rf = rf .* win;

    NFFT = 2^nextpow2(length(rf));
    RF_FFT = fft(rf, NFFT);
    f = (0:NFFT-1) * fs / NFFT;

    mag = abs(RF_FFT);
    % max(mag)
    max_mag = 3e5;  % 根据实际数据调整这个值，使得频谱图的幅度在合理范围内显示
    % mag = mag / max(mag);
    mag = mag / max_mag;

    % 仅显示单边频谱
    half_idx = 1:NFFT/2;
    f_half = f(half_idx);
    mag_half = mag(half_idx);


    % 转dB
    mag_db = 20 * log10(mag_half + eps);

    % Peak frequency
    [~, idx_max] = max(mag_db);
    fc = f_half(idx_max);
end