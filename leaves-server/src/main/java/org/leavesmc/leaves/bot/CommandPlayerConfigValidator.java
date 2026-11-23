package org.leavesmc.leaves.bot;

import org.leavesmc.leaves.config.api.ConfigValidator;

import java.util.List;
import java.util.Locale;

/**
 * Validator for the carpet-compatible {@code fakeplayer.command-player} setting.
 * <p>
 * Carpet treats the rule as a string ("ops", "true"/"false", "0".."4"). YAML
 * parses unquoted {@code false}/{@code true}/numbers as booleans/integers, which
 * would otherwise fail the String cast and silently fall back to the default
 * "ops" - so accept those scalars and normalise them to the carpet string form.
 */
public final class CommandPlayerConfigValidator implements ConfigValidator<String> {

    public static final List<String> VALUES = List.of("true", "false", "ops", "0", "1", "2", "3", "4");

    private static String normalise(Object value) {
        if (value instanceof Boolean bool) {
            return bool.toString();
        }
        if (value instanceof Number number) {
            return Integer.toString(number.intValue());
        }
        return String.valueOf(value);
    }

    private static String parse(Object value) {
        String normalised = normalise(value).toLowerCase(Locale.ROOT);
        if (!VALUES.contains(normalised)) {
            throw new IllegalArgumentException("invalid command-player value: " + normalised
                + " (expected one of " + VALUES + ")");
        }
        return normalised;
    }

    @Override
    public String stringConvert(String value) throws IllegalArgumentException {
        return parse(value);
    }

    @Override
    public String loadConvert(Object value) throws IllegalArgumentException {
        return parse(value);
    }

    @Override
    public List<String> valueSuggest() {
        return VALUES;
    }
}
