function words = quantize_signed_words(values, word_length, frac_length)
%QUANTIZE_SIGNED_WORDS Quantize signed fixed-point values into BRAM words.
% Shared helper for BRAM parameter generators.
scaled = round(double(values(:).') * 2^frac_length);
max_val = 2^(word_length - 1) - 1;
min_val = -2^(word_length - 1);
clipped = min(max(scaled, min_val), max_val);
words = quantize_integer_words(clipped, word_length);
end
