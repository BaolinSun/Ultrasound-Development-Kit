function words = quantize_integer_words(values, word_length)
%QUANTIZE_INTEGER_WORDS Convert integer values to unsigned BRAM words.
mask_mod = 2^word_length;
words = uint32(mod(round(double(values(:).')), mask_mod));
end
