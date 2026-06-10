function lut = load_graymap_txt(filename)
% LOAD_GRAYMAP_TXT
% Load 256-entry graymap LUT from text file

fid = fopen(filename,'r');
txt = fread(fid,'char=>char')';
fclose(fid);

fid = fopen('graymap_lut.txt','r');
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
    error('LUT must contain 256 entries.');
end

lut = uint8(lut);
end