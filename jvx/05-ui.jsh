// Everything a lesson draws on the screen goes through here, and the shape of it comes
// straight out of a measurement rather than out of taste.
//
// probes/widgets measured twelve ways of getting something in front of a reader, in four
// places each, and a saved notebook that nobody has run keeps four of them: a style
// attribute on the element, details and summary, an img whose src is an SVG data URI, and
// markdown. Everything else is sanitized away. Style tags are removed while the class
// attribute is kept, so the rule is gone and the hook that wanted it is still there. An id
// is renamed to data-jupyter-id, so every selector quietly stops matching. Form controls
// arrive disabled. Scripts, onclick and iframes are removed outright.
//
// That is the state a reader is in when they click a link and read the page, which is most
// readers most of the time. So this file uses those four things and nothing else. There is
// no style tag, no id, no script and no input anywhere below, and tools/test_jvx_ui.py
// fails the build if one appears. The full reading is in docs/probes/widgets.md.

class Ui {

    static final String FONT = "system-ui, -apple-system, Segoe UI, Roboto, sans-serif";
    static final String MONO = "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace";
    static final String INK = "#212529";
    static final String MUTED = "#868e96";
    static final String BLUE = "#4c6ef5";
    static final String GREEN = "#2f9e44";
    static final String ORANGE = "#e8590c";

    // -- getting a payload to the front end ----------------------------------------
    //
    // The kernel is JJava, and the `display` it puts in scope is a static method on
    // org.dflib.jjava.jupyter.kernel.BaseNotebookStatics. Calling it by name would work
    // in a notebook and would break everywhere else, because the same helper surface
    // gets piped into a plain jshell when somebody debugs the bootstrap, and JShell
    // refuses to run a method whose body names something that does not exist. Looking it
    // up reflectively answers both questions at once: whether there is a screen to draw
    // on, and how to draw on it.

    private static Method displayMethod;
    private static boolean lookedForIt = false;

    /** Is there a front end here that can render markup, or are we in a terminal. */
    static boolean rich() {
        if (!lookedForIt) {
            lookedForIt = true;
            try {
                Class<?> statics =
                    Class.forName("org.dflib.jjava.jupyter.kernel.BaseNotebookStatics");
                displayMethod = statics.getMethod("display", Object.class, String[].class);
            } catch (Throwable notANotebook) {
                displayMethod = null;
            }
        }
        return displayMethod != null;
    }

    /**
     * Put markup on the screen. False means there was no screen, so print text instead.
     *
     * The return value of `display` is thrown away on purpose and this is the only place
     * in the project that calls it. `display` hands back the id it assigned, JShell
     * prints the value of the last expression it evaluates, and the result is a line of
     * hex under every widget on the page. Measured on twelve cells out of twelve. One
     * assignment here is the whole fix.
     */
    static boolean html(String markup) {
        if (!rich()) return false;
        try {
            String ignored = (String) displayMethod.invoke(
                null, markup, new String[] { "text/html" });
            return true;
        } catch (Throwable t) {
            // A front end that turned out not to want it is not worth an exception in a
            // reader's face. Saying false sends the caller to the text version.
            return false;
        }
    }

    // -- building the markup ---------------------------------------------------------

    static String esc(String text) {
        return text.replace("&", "&amp;")
                   .replace("<", "&lt;")
                   .replace(">", "&gt;")
                   .replace("\"", "&quot;");
    }

    /** Escaped text, with `backticks` turned into code spans, which lessons already write. */
    static String prose(String text) {
        String[] parts = esc(text).split("`", -1);
        StringBuilder out = new StringBuilder();
        for (int i = 0; i < parts.length; i++) {
            // The odd numbered pieces are the ones between a pair of backticks. An
            // unclosed backtick leaves the last piece odd, and that one gets its backtick
            // put back and goes out as plain text, because a lesson with a typo in it
            // should look wrong on the page rather than quietly lose a character.
            if (i % 2 == 1 && i < parts.length - 1) {
                out.append(code(parts[i]));
            } else if (i % 2 == 1) {
                out.append("`").append(parts[i]);
            } else {
                out.append(parts[i]);
            }
        }
        return out.toString();
    }

    /** Already escaped text in a code span. Use prose() for anything a lesson typed. */
    static String code(String escaped) {
        return "<code style=\"font-family:" + MONO + ";font-size:0.92em;background:#e9ecef;"
            + "padding:1px 5px;border-radius:3px\">" + escaped + "</code>";
    }

    static String card(String accent, String label, String body) {
        return "<div style=\"font-family:" + FONT + ";color:" + INK + ";max-width:46em;"
            + "border:1px solid #dee2e6;border-left:5px solid " + accent + ";"
            + "border-radius:6px;background:#f8f9fa;padding:14px 16px;margin:4px 0\">"
            + "<div style=\"font-size:11px;font-weight:700;letter-spacing:0.08em;"
            + "text-transform:uppercase;color:" + accent + ";margin-bottom:8px\">"
            + esc(label) + "</div>"
            + body
            + "</div>";
    }

    /**
     * The one interactive element that survives everywhere.
     *
     * No CSS and no JavaScript, so there is nothing for a sanitizer to take away. Open it
     * when the reader has earned what is inside and leave it shut when they have not.
     */
    static String details(String summary, String body, boolean open) {
        return "<details" + (open ? " open" : "") + " style=\"margin-top:10px\">"
            + "<summary style=\"cursor:pointer;font-weight:600;color:" + BLUE + "\">"
            + esc(summary) + "</summary>"
            + "<div style=\"margin-top:8px\">" + body + "</div></details>";
    }

    /**
     * A picture, as an img with the SVG base64 encoded into the src.
     *
     * This is the useful half of the whole widget probe. An `image/svg+xml` output is
     * shown as escaped source text in a notebook nobody has run, which is worse than
     * showing nothing, and the identical bytes inside an img render in every environment
     * measured. So anything this project can draw as an SVG, it can put on any page.
     */
    static String img(String svg, String alt) {
        String encoded = Base64.getEncoder().encodeToString(svg.getBytes(StandardCharsets.UTF_8));
        return "<img alt=\"" + esc(alt) + "\" style=\"max-width:100%;display:block;"
            + "margin:4px 0\" src=\"data:image/svg+xml;base64," + encoded + "\">";
    }

    static String line(String body) {
        return "<div style=\"margin:4px 0;line-height:1.5\">" + body + "</div>";
    }

    static String small(String body) {
        return "<div style=\"margin-top:10px;font-size:13px;color:" + MUTED
            + ";line-height:1.5\">" + body + "</div>";
    }
}
