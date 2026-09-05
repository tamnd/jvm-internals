// HeapLens: what one object looks like in memory, byte by byte.
//
// This file draws and measures nothing. It is handed a list of slots that somebody else
// measured and turns them into a picture, which is what makes it testable without a JVM
// to point at: the test can hand it a layout it invented and check the drawing, and
// separately check that the measuring produces the layout it should.
//
// The picture is an SVG inside an img with a data URI, because probes/widgets measured
// that as the only kind of picture that renders in all four places a reader might be,
// including a saved notebook nobody has run. Inside the img the SVG is never sanitized,
// so the drawing can use anything SVG has.

class Lens {

    /**
     * One run of bytes in an object, and what is living there.
     *
     * `kind` is what it is rather than what it looks like: header, field, gap or padding.
     * The difference between a gap and padding matters and is the whole reason a reader
     * is looking at this. A gap is alignment inside the object, put there because the
     * next field could not start where the last one ended. Padding is at the end, put
     * there because the whole object has to be a multiple of the alignment. One is the
     * field order's fault and can be fixed by reordering. The other cannot.
     */
    record Slot(String label, long offset, long width, String kind) {}

    static final int CELL = 34;      // one byte
    static final int ROW = 42;       // one 8 byte word
    static final int GUTTER = 46;    // the offset down the left
    static final int TOP = 28;       // the byte ruler across the top
    static final int PAD = 12;

    static String colour(String kind, int index) {
        if (kind.equals("header")) return "#4c6ef5";
        if (kind.equals("padding")) return "#adb5bd";
        if (kind.equals("gap")) return "#ffa94d";
        // Fields cycle, so two neighbours never share a colour and a reader can see
        // where one stops without reading the label.
        String[] wheel = { "#2f9e44", "#1098ad", "#7048e8", "#e64980", "#f08c00" };
        return wheel[Math.floorMod(index, wheel.length)];
    }

    static String ink(String kind) {
        return kind.equals("padding") ? "#495057" : "#ffffff";
    }

    /** The picture. One row per eight byte word, one rect per slot per row. */
    static String svg(List<Slot> slots, long size) {
        long rows = (size + 7) / 8;
        int width = PAD * 2 + GUTTER + 8 * CELL;
        int height = (int) (TOP + rows * ROW + PAD);

        StringBuilder out = new StringBuilder();
        out.append("<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"").append(width)
           .append("\" height=\"").append(height)
           .append("\" viewBox=\"0 0 ").append(width).append(" ").append(height)
           .append("\" font-family=\"ui-monospace, SFMono-Regular, Menlo, monospace\">");
        out.append("<rect width=\"").append(width).append("\" height=\"").append(height)
           .append("\" fill=\"#ffffff\"/>");

        // The byte ruler. Which byte of the word, not which byte of the object, because
        // the offset down the side already says that and saying it twice is noise.
        for (int b = 0; b < 8; b++) {
            out.append(text(PAD + GUTTER + b * CELL + CELL / 2, TOP - 10, String.valueOf(b),
                11, "#adb5bd", "middle", false));
        }

        for (long row = 0; row < rows; row++) {
            int y = (int) (TOP + row * ROW);
            out.append(text(PAD + GUTTER - 12, y + ROW / 2 + 4, String.valueOf(row * 8),
                12, "#868e96", "end", false));
        }

        int fieldIndex = 0;
        for (Slot slot : slots) {
            int index = slot.kind().equals("field") ? fieldIndex++ : 0;
            long from = slot.offset();
            long to = slot.offset() + slot.width();
            // A slot that crosses a word boundary is drawn once per row it touches, so
            // the picture stays a grid and a long field still reads as one thing.
            for (long start = from; start < to; ) {
                long row = start / 8;
                long end = Math.min(to, (row + 1) * 8);
                int x = (int) (PAD + GUTTER + (start % 8) * CELL);
                int y = (int) (TOP + row * ROW);
                int w = (int) ((end - start) * CELL) - 3;
                out.append("<rect x=\"").append(x + 1).append("\" y=\"").append(y + 3)
                   .append("\" width=\"").append(w).append("\" height=\"").append(ROW - 9)
                   .append("\" rx=\"4\" fill=\"").append(colour(slot.kind(), index))
                   .append(slot.kind().equals("padding")
                       ? "\" fill-opacity=\"0.35\" stroke=\"#adb5bd\" stroke-dasharray=\"3 3\"/>"
                       : "\"/>");
                // A label needs room. Two bytes is not room, and a clipped word is worse
                // than no word, because the offset in the details below is exact anyway.
                if (w >= 62) {
                    out.append(text(x + 1 + w / 2, y + ROW / 2 + 1, slot.label(), 13,
                        ink(slot.kind()), "middle", true));
                }
                start = end;
            }
        }
        out.append("</svg>");
        return out.toString();
    }

    static String text(int x, int y, String body, int size, String fill, String anchor,
                       boolean bold) {
        return "<text x=\"" + x + "\" y=\"" + y + "\" font-size=\"" + size + "\" fill=\"" + fill
            + "\" text-anchor=\"" + anchor + "\""
            + (bold ? " font-weight=\"600\"" : "") + ">" + Ui.esc(body) + "</text>";
    }

    /**
     * What the picture says, for somebody who cannot see it.
     *
     * Not an afterthought and not generated from the same string twice. A screen reader
     * gets this, and so does anyone whose front end blocks images, and it is the same
     * sentence the text version prints in a terminal.
     */
    static String alt(String title, List<Slot> slots, long size) {
        StringBuilder out = new StringBuilder("the layout of " + title + ", " + size + " bytes: ");
        for (int i = 0; i < slots.size(); i++) {
            Slot s = slots.get(i);
            if (i > 0) out.append(", ");
            out.append(s.label()).append(" at ").append(s.offset())
               .append(" for ").append(s.width()).append(s.width() == 1 ? " byte" : " bytes");
        }
        return out.toString();
    }

    /** The card: the picture, then the numbers behind it one click away. */
    static String card(String title, List<Slot> slots, long size, String note) {
        StringBuilder rows = new StringBuilder();
        int fieldIndex = 0;
        for (Slot s : slots) {
            // Counted the same way the picture counts, so the dot beside a name is the
            // colour of the box it points at. Two lists that drift apart are worse than
            // one list, so there is exactly one rule and both of them use it.
            int index = s.kind().equals("field") ? fieldIndex++ : 0;
            // white-space:pre, or the alignment in `row` does nothing: HTML collapses runs
            // of spaces, and a monospace font with collapsed spaces lines nothing up.
            rows.append("<div style=\"margin:3px 0;font-family:" + Ui.MONO
                    + ";font-size:13px;white-space:pre\">")
                .append("<span style=\"display:inline-block;width:9px;height:9px;border-radius:2px;")
                .append("background:").append(colour(s.kind(), index))
                .append(";margin-right:8px\"></span>")
                .append(Ui.esc(row(s)))
                .append("</div>");
        }
        String body =
            Ui.img(svg(slots, size), alt(title, slots, size))
            + Ui.line("<b>" + Ui.esc(title) + "</b> is " + size + " bytes.")
            + Ui.details("The exact offsets", rows.toString(), false)
            + (note.isEmpty() ? "" : Ui.small(Ui.prose(note)));
        return Ui.card(Ui.BLUE, "layout", body);
    }

    /**
     * One slot as a line of text, used by both renderings.
     *
     * One format string rather than two, because the terminal version and the list
     * behind the picture say the same thing and there is no version of this project
     * where it is good for them to drift.
     */
    static String row(Slot s) {
        return String.format("%-18s bytes %2d to %2d  (%d)",
            s.label(), s.offset(), s.offset() + s.width() - 1, s.width());
    }

    /** The same thing for a terminal, where there is no picture to look at. */
    static String text(String title, List<Slot> slots, long size) {
        StringBuilder out = new StringBuilder(title + " is " + size + " bytes\n\n");
        for (Slot s : slots) {
            out.append("  ").append(row(s)).append("\n");
        }
        return out.toString();
    }
}
