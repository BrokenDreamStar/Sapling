// Ported from fabric-carpet @9c1bec181eebae1cd8fbab3e1872bb6cd8139071 (MIT)
// https://github.com/gnembon/fabric-carpet/blob/26.1/src/main/java/carpet/utils/Messenger.java
// Trimmed to the subset required by the fake player command, formatting semantics kept identical.
package org.leavesmc.leaves.bot;

import net.minecraft.ChatFormatting;
import net.minecraft.commands.CommandSourceStack;
import net.minecraft.network.chat.ClickEvent;
import net.minecraft.network.chat.Component;
import net.minecraft.network.chat.HoverEvent;
import net.minecraft.network.chat.MutableComponent;
import net.minecraft.network.chat.Style;
import net.minecraft.network.chat.TextColor;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.net.URI;
import java.util.function.BiFunction;
import java.util.function.Function;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

public class Messenger {
    public static final Logger LOG = LoggerFactory.getLogger("Messaging System");

    private static final Pattern colorExtract = Pattern.compile("#([0-9a-fA-F]{6})");

    public enum CarpetFormatting {
        ITALIC('i', (s, f) -> s.withItalic(true)),
        STRIKE('s', (s, f) -> s.applyFormat(ChatFormatting.STRIKETHROUGH)),
        UNDERLINE('u', (s, f) -> s.applyFormat(ChatFormatting.UNDERLINE)),
        BOLD('b', (s, f) -> s.withBold(true)),
        OBFUSCATE('o', (s, f) -> s.applyFormat(ChatFormatting.OBFUSCATED)),

        WHITE('w', (s, f) -> s.withColor(ChatFormatting.WHITE)),
        YELLOW('y', (s, f) -> s.withColor(ChatFormatting.YELLOW)),
        LIGHT_PURPLE('m', (s, f) -> s.withColor(ChatFormatting.LIGHT_PURPLE)),
        RED('r', (s, f) -> s.withColor(ChatFormatting.RED)),
        AQUA('c', (s, f) -> s.withColor(ChatFormatting.AQUA)),
        GREEN('l', (s, f) -> s.withColor(ChatFormatting.GREEN)),
        BLUE('t', (s, f) -> s.withColor(ChatFormatting.BLUE)),
        DARK_GRAY('f', (s, f) -> s.withColor(ChatFormatting.DARK_GRAY)),
        GRAY('g', (s, f) -> s.withColor(ChatFormatting.GRAY)),
        GOLD('d', (s, f) -> s.withColor(ChatFormatting.GOLD)),
        DARK_PURPLE('p', (s, f) -> s.withColor(ChatFormatting.DARK_PURPLE)),
        DARK_RED('n', (s, f) -> s.withColor(ChatFormatting.DARK_RED)),
        DARK_AQUA('q', (s, f) -> s.withColor(ChatFormatting.DARK_AQUA)),
        DARK_GREEN('e', (s, f) -> s.withColor(ChatFormatting.DARK_GREEN)),
        DARK_BLUE('v', (s, f) -> s.withColor(ChatFormatting.DARK_BLUE)),
        BLACK('k', (s, f) -> s.withColor(ChatFormatting.BLACK)),

        COLOR('#', (s, f) -> {
            TextColor color;
            try {
                color = TextColor.parseColor("#" + f).getOrThrow(RuntimeException::new);
            } catch (RuntimeException e) {
                return s;
            }
            return color == null ? s : s.withColor(color);
        }, s -> {
            Matcher m = colorExtract.matcher(s);
            return m.find() ? m.group(1) : null;
        }),
        ;

        public char code;
        public BiFunction<Style, String, Style> applier;
        public Function<String, String> container;

        CarpetFormatting(char code, BiFunction<Style, String, Style> applier) {
            this(code, applier, s -> s.indexOf(code) >= 0 ? Character.toString(code) : null);
        }

        CarpetFormatting(char code, BiFunction<Style, String, Style> applier, Function<String, String> container) {
            this.code = code;
            this.applier = applier;
            this.container = container;
        }

        public Style apply(String format, Style previous) {
            String fmt;
            if ((fmt = container.apply(format)) != null) return applier.apply(previous, fmt);
            return previous;
        }
    }

    public static Style parseStyle(String style) {
        Style myStyle = Style.EMPTY.withColor(ChatFormatting.WHITE);
        for (CarpetFormatting cf : CarpetFormatting.values()) myStyle = cf.apply(style, myStyle);
        return myStyle;
    }

    private static MutableComponent getChatComponentFromDesc(String message, MutableComponent previousMessage) {
        if (message.equalsIgnoreCase("")) {
            return Component.literal("");
        }
        if (Character.isWhitespace(message.charAt(0))) {
            message = "w" + message;
        }
        int limit = message.indexOf(' ');
        String desc = message;
        String str = "";
        if (limit >= 0) {
            desc = message.substring(0, limit);
            str = message.substring(limit + 1);
        }
        if (previousMessage == null) {
            MutableComponent text = Component.literal(str);
            text.setStyle(parseStyle(desc));
            return text;
        }
        Style previousStyle = previousMessage.getStyle();
        MutableComponent ret = previousMessage;
        previousMessage.setStyle(switch (desc.charAt(0)) {
            case '?' -> previousStyle.withClickEvent(new ClickEvent.SuggestCommand(message.substring(1)));
            case '!' -> previousStyle.withClickEvent(new ClickEvent.RunCommand(message.substring(1)));
            case '^' -> previousStyle.withHoverEvent(new HoverEvent.ShowText(c(message.substring(1))));
            case '@' -> previousStyle.withClickEvent(new ClickEvent.OpenUrl(URI.create(message.substring(1))));
            case '&' -> previousStyle.withClickEvent(new ClickEvent.CopyToClipboard(message.substring(1)));
            default -> {
                ret = Component.literal(str);
                ret.setStyle(parseStyle(desc));
                yield previousStyle;
            }
        });
        return ret;
    }

    //message source
    public static void m(CommandSourceStack source, Object... fields) {
        if (source != null)
            source.sendSuccess(() -> Messenger.c(fields), source.getServer() != null && source.getServer().overworld() != null);
    }

    public static Component c(Object... fields) {
        MutableComponent message = Component.literal("");
        MutableComponent previousComponent = null;
        for (Object o : fields) {
            if (o instanceof MutableComponent) {
                message.append((MutableComponent) o);
                previousComponent = (MutableComponent) o;
                continue;
            }
            String txt = o.toString();
            MutableComponent comp = getChatComponentFromDesc(txt, previousComponent);
            if (comp != previousComponent) message.append(comp);
            previousComponent = comp;
        }
        return message;
    }

    public static Component s(String text) {
        return s(text, "");
    }

    public static Component s(String text, String style) {
        MutableComponent message = Component.literal(text);
        message.setStyle(parseStyle(style));
        return message;
    }
}
