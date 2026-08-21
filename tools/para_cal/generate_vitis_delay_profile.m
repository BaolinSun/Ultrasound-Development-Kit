function output_path = generate_vitis_delay_profile(output_path)
%GENERATE_VITIS_DELAY_PROFILE Generate a Vitis delay_profile.c file.
%
% The destination is intentionally mandatory so this desktop-tool project
% never overwrites a Zynq/Vitis source tree implicitly. JSON output is
% disabled for this call; both formats share para_transmit_delay.m data.

if nargin < 1 || isempty(output_path)
    error('generate_vitis_delay_profile:OutputPathRequired', ...
        'An explicit delay_profile.c output path is required.');
end
output_path = char(output_path);
[output_dir, ~, ~] = fileparts(output_path);
if isempty(output_dir)
    output_dir = pwd;
end
if ~exist(output_dir, 'dir')
    error('generate_vitis_delay_profile:MissingOutputDirectory', ...
        'Output directory does not exist: %s', output_dir);
end

delay_words = para_transmit_delay([], false);
assert(isa(delay_words, 'uint32') && isrow(delay_words), ...
    'The Vitis delay profile must be a uint32 row vector.');
assert(numel(delay_words) == 16384, ...
    'The Vitis delay profile must contain exactly 16384 words.');

file_id = fopen(output_path, 'w');
if file_id < 0
    error('generate_vitis_delay_profile:OpenFailed', ...
        'Unable to open %s for writing.', output_path);
end
cleanup = onCleanup(@() fclose(file_id)); %#ok<NASGU>

fprintf(file_id, '#include "bram.h"\n\n');
fprintf(file_id, 'u32 delay_profile[16384] = {\n');
for first_word = 1:8:numel(delay_words)
    fprintf(file_id, '\t');
    for word_index = first_word:first_word+7
        fprintf(file_id, '0x%08X,', delay_words(word_index));
        if word_index ~= first_word + 7
            fprintf(file_id, ' ');
        end
    end
    fprintf(file_id, '\n');
end
fprintf(file_id, '};\n');
end
