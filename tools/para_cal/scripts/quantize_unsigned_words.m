function words = quantize_unsigned_words(values, word_length, frac_length)
%QUANTIZE_UNSIGNED_WORDS Quantize unsigned fixed-point values into BRAM words.
% Shared helper for BRAM parameter generators.
scaled = round(double(values(:).') * 2^frac_length);
max_val = 2^word_length - 1;
clipped = min(max(scaled, 0), max_val);
words = uint32(clipped);
end
