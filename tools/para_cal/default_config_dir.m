function output_dir = default_config_dir()
%DEFAULT_CONFIG_DIR Return the production JSON config directory.
this_dir = fileparts(mfilename('fullpath'));
output_dir = fullfile(this_dir, '..', 'configs');
end
