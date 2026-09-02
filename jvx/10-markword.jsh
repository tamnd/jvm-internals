// The mark word is the first eight bytes of every object on the heap, and it carries
// several unrelated things at once: the lock state, the identity hash, the age the
// collector uses to decide about promotion, and on JDK 27 the class pointer as well.
//
// None of the numbers below are typed by hand. The placeholder line further down is
// replaced by tools/build.py using docs/generated/markword.json, which
// tools/gen_markword.py works out from HotSpot's own markWord.hpp at the pinned tag.
// The citation on each field is the line of markWord.hpp it came from, so a reader
// who does not believe a number can go and look at the line that produced it.

class MarkWord {

    static final class Field {
        final String name;
        final int shift;
        final int bits;
        final String meaning;
        final String citation;

        Field(String name, int shift, int bits, String meaning, String citation) {
            this.name = name;
            this.shift = shift;
            this.bits = bits;
            this.meaning = meaning;
            this.citation = citation;
        }

        /** The value of this field in the given word. */
        long of(long word) {
            return (word >>> shift) & ((1L << bits) - 1);
        }

        /** The bit positions this field occupies, high end first, the way a diagram reads. */
        String span() {
            return (shift + bits - 1) + ".." + shift;
        }
    }

    // @jvx:markword_fields@

    static Field field(String name) {
        for (Field f : FIELDS) {
            if (f.name.equals(name)) return f;
        }
        throw new IllegalArgumentException(
            "no field called " + name + " in the mark word at " + SOURCE_TAG);
    }

    static long get(long word, String name) {
        return field(name).of(word);
    }

    static String hex(long word) {
        return String.format("0x%016x", word);
    }

    /** All 64 bits, grouped in bytes, so the reader can count them. */
    static String bin(long word) {
        StringBuilder b = new StringBuilder(72);
        for (int i = 63; i >= 0; i--) {
            b.append((word >>> i) & 1L);
            if (i % 8 == 0 && i != 0) b.append(' ');
        }
        return b.toString();
    }

    /**
     * The same 64 bits with each field's own bits shown and everything else dimmed to
     * a dot. Reading a hex number and believing where the boundaries are is exactly
     * the step where people go wrong, so this draws the boundaries.
     */
    static String ruler(long word) {
        StringBuilder out = new StringBuilder();
        for (int i = FIELDS.length - 1; i >= 0; i--) {
            Field f = FIELDS[i];
            StringBuilder row = new StringBuilder(72);
            for (int bit = 63; bit >= 0; bit--) {
                boolean mine = bit >= f.shift && bit < f.shift + f.bits;
                row.append(mine ? Character.forDigit((int) ((word >>> bit) & 1L), 10) : '.');
                if (bit % 8 == 0 && bit != 0) row.append(' ');
            }
            out.append(row).append("  ").append(f.name).append('\n');
        }
        return out.toString();
    }

    static String decode(long word) {
        StringBuilder b = new StringBuilder();
        b.append(hex(word)).append('\n');
        b.append(bin(word)).append('\n');
        b.append('\n');
        b.append(ruler(word));
        b.append('\n');
        b.append(String.format("%-8s %-9s %-12s %s%n", "bits", "field", "value", "meaning"));
        b.append("-".repeat(78)).append('\n');
        for (int i = FIELDS.length - 1; i >= 0; i--) {
            Field f = FIELDS[i];
            b.append(String.format("%-8s %-9s %-12s %s%n",
                f.span(), f.name, "0x" + Long.toHexString(f.of(word)), f.meaning));
        }
        b.append('\n');
        int lock = (int) get(word, "lock");
        b.append("lock ").append(LOCK_BITS[lock]).append(", ").append(LOCK_MEANING[lock]).append('\n');
        return b.toString();
    }

    /** Where every number above came from, for a reader who wants to check one. */
    static String provenance() {
        StringBuilder b = new StringBuilder();
        b.append("generated from ").append(SOURCE_PATH).append(" at ").append(SOURCE_TAG).append('\n');
        b.append("sha256 ").append(SOURCE_SHA256).append('\n');
        b.append('\n');
        for (int i = FIELDS.length - 1; i >= 0; i--) {
            Field f = FIELDS[i];
            b.append(String.format("%-9s %s%n", f.name, f.citation));
        }
        return b.toString();
    }
}
