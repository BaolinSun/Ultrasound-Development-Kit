function generate_bram_json_defaults(output_dir)
%GENERATE_BRAM_JSON_DEFAULTS Generate all BRAM parameter JSON files.
%
% Without an argument this updates ../configs. Pass a temporary output_dir
% during tests to avoid overwriting production defaults.
if nargin < 1 || isempty(output_dir)
    output_dir = default_config_dir();
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
