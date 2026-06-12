function write_bram_json(output_dir, file_name, description, section_names, section_values)
%WRITE_BRAM_JSON Write BRAM parameter sections using the UART JSON schema.
if numel(section_names) ~= numel(section_values)
    error('write_bram_json:SectionMismatch', ...
          'section_names and section_values must have the same length.');
end

if ~exist(output_dir, 'dir')
    mkdir(output_dir);
end

json_path = fullfile(output_dir, file_name);
fid = fopen(json_path, 'w', 'n', 'UTF-8');
if fid == -1
    error('write_bram_json:OpenFailed', 'Failed to create %s.', json_path);
end

cleanup = onCleanup(@() fclose(fid));

fprintf(fid, '{\n');
fprintf(fid, '  "description": "%s",\n', escape_json_string(description));
fprintf(fid, '  "bram": {\n');

for s = 1:numel(section_names)
    section_name = section_names{s};
    words = uint32(section_values{s}(:));

    fprintf(fid, '    "%s": [\n', escape_json_string(section_name));
    for i = 1:numel(words)
        suffix = ',';
        if i == numel(words)
            suffix = '';
        end
        fprintf(fid, '      "0x%08X"%s\n', words(i), suffix);
    end

    section_suffix = ',';
    if s == numel(section_names)
        section_suffix = '';
    end
    fprintf(fid, '    ]%s\n', section_suffix);
end

fprintf(fid, '  }\n');
fprintf(fid, '}\n');
end

function out = escape_json_string(in)
out = strrep(in, '\', '\\');
out = strrep(out, '"', '\"');
end
