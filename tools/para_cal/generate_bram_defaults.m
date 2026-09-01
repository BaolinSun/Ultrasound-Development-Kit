function generate_bram_defaults(output_dir)
    %GENERATE_BRAM_DEFAULTS Generate BRAM parameter JSON and C source files.
    %
    % Without an argument this updates ../configs. JSON files are written to
    % that directory and C arrays to its c_array subdirectory. Pass a temporary
    % output_dir during tests to avoid overwriting production defaults.
    this_dir = fileparts(mfilename('fullpath'));
    addpath(fullfile(this_dir, 'scripts'));
    if nargin < 1 || isempty(output_dir)
        output_dir = fullfile(this_dir, '..', 'configs');
    end

    para_transmit_delay(output_dir);
    para_fdemod_quant(output_dir);
    para_log_quant(output_dir);
    para_x_element_quant(output_dir);
    para_hamming_quant(output_dir);
    para_dfilter_quant(output_dir);
    para_timing_quant(output_dir);
    para_sin_beta_quant(output_dir);
end
