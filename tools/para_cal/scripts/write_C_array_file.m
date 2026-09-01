function output_path = write_C_array_file( ...
        output_path, declaration, words, hex_digits, values_per_line, ...
        indent, preamble, inline_array, append_output)
%WRITE_C_ARRAY_FILE Write uint32 words as a Vitis-ready C source array.
% Shared output helper for BRAM parameter generators.
if nargin < 6 || isempty(indent)
    indent = '';
end
if nargin < 7 || isempty(preamble)
    preamble = '';
end
if nargin < 8
    inline_array = false;
end
if nargin < 9
    append_output = false;
end

if isempty(output_path)
    error('write_C_array_file:OutputPathRequired', ...
        'An explicit output path is required.');
end
if ~isscalar(hex_digits) || hex_digits < 1 || hex_digits ~= floor(hex_digits)
    error('write_C_array_file:InvalidHexDigits', ...
        'hex_digits must be a positive integer.');
end
if ~inline_array && (~isscalar(values_per_line) || values_per_line < 1 || ...
        values_per_line ~= floor(values_per_line))
    error('write_C_array_file:InvalidValuesPerLine', ...
        'values_per_line must be a positive integer.');
end

output_path = char(output_path);
declaration = char(declaration);
indent = char(indent);
preamble = char(preamble);
words = uint32(words(:));

[output_dir, ~, ~] = fileparts(output_path);
if ~isempty(output_dir) && ~exist(output_dir, 'dir')
    mkdir(output_dir);
end

file_mode = 'w';
if append_output
    file_mode = 'a';
end
file_id = fopen(output_path, file_mode, 'n', 'UTF-8');
if file_id < 0
    error('write_C_array_file:OpenFailed', ...
        'Unable to open %s for writing.', output_path);
end
cleanup = onCleanup(@() fclose(file_id)); %#ok<NASGU>

fprintf(file_id, '%s', preamble);
value_format = sprintf('0x%%0%dX, ', hex_digits);

if inline_array
    fprintf(file_id, '%s = {', declaration);
    for word_index = 1:numel(words)
        fprintf(file_id, value_format, words(word_index));
    end
    fprintf(file_id, '};\n');
    return;
end

fprintf(file_id, '%s = {\n', declaration);
if ~isempty(words)
    fprintf(file_id, '%s', indent);
end
for word_index = 1:numel(words)
    fprintf(file_id, value_format, words(word_index));
    if mod(word_index, values_per_line) == 0
        fprintf(file_id, '\n');
        if word_index < numel(words)
            fprintf(file_id, '%s', indent);
        end
    end
end
if ~isempty(words) && mod(numel(words), values_per_line) ~= 0
    fprintf(file_id, '\n');
end
fprintf(file_id, '};\n');
end
