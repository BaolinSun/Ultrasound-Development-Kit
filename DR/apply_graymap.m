function out = apply_graymap(in, lut)
% APPLY_GRAYMAP
% Apply 256-entry graymap LUT to uint8 image
%
% in  : uint8(H,W)
% lut : 256-element vector (uint8 or double)
% out : uint8(H,W)

    % ---- 参数检查 ----
    if numel(lut) ~= 256
        error('LUT must have 256 entries');
    end

    % ---- 转成 uint8 LUT ----
    if ~isa(lut,'uint8')
        lut = uint8(lut);
    end

    % ---- 查表映射 ----
    out = lut(in + 1);  % MATLAB 索引从 1 开始
end