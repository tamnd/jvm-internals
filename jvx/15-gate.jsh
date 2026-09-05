// A prediction gate.
//
// The rule this project runs on is that a reader who has not committed to an answer
// does not really read the reveal. They skim it and come away feeling like they knew.
// Committing to a wrong answer first is what makes the correction stick, so the gate
// makes you write one down and does not show you anything until you have.
//
// There are two renderings and the text one is not a fallback in the apologetic sense.
// It works in a terminal, in a printed transcript and in any notebook with no HTML, and
// every word of it is in the card version too. What the card adds is a shape the eye can
// find on a long page, and one thing the text cannot do: on a page nobody has run, the
// answer sits inside a details element, so a reader scrolling past a reveal has to decide
// to open it rather than have it handed to them. That is the gate working in the one
// environment where it used to be impossible.
//
// A lesson never names Gate. It calls jvx.gate, jvx.answer and jvx.reveal, which is why
// this file could change shape twice without touching a lesson.

class Gate {

    static final Map<String, String> question = new LinkedHashMap<>();
    static final Map<String, String> answered = new LinkedHashMap<>();

    static void ask(String id, String text, String... options) {
        question.put(id, text);
        if (Ui.html(askHtml(id, text, options))) return;

        System.out.println(text);
        System.out.println();
        for (String option : options) {
            System.out.println("    " + option);
        }
        System.out.println();
        System.out.println("Pick one before you run anything else. There is a right answer and");
        System.out.println("the wrong ones are wrong for reasons worth knowing.");
        System.out.println();
        System.out.println("    jvx.answer(\"" + id + "\", \"a\")");
    }

    static void answer(String id, String choice) {
        if (!question.containsKey(id)) {
            String missing = "No gate called " + id + " is open. Run the gate cell above first.";
            if (!Ui.html(Ui.card(Ui.ORANGE, "nothing to answer", Ui.line(Ui.esc(missing))))) {
                System.out.println(missing);
            }
            return;
        }
        String cleaned = choice.trim().toLowerCase();
        answered.put(id, cleaned);
        if (Ui.html(answerHtml(id, cleaned))) return;
        System.out.println("Recorded " + cleaned + " for " + id + ". Now run the next cell.");
    }

    static void reveal(String id, String correct) {
        String expected = correct.trim().toLowerCase();
        String mine = answered.get(id);
        if (Ui.html(revealHtml(id, expected, mine))) return;

        if (mine == null) {
            // Not a refusal. Somebody who hit Run All should not end up staring at a
            // cell that will not talk to them. But it says what was lost, because it
            // was a real thing and not a formality.
            System.out.println("You did not commit to an answer, so this is worth less to you");
            System.out.println("than it would have been. The answer is " + expected + ".");
            return;
        }
        if (mine.equals(expected)) {
            System.out.println("You said " + mine + ", and that is right.");
            System.out.println("Read on anyway. Being right for the wrong reason is common here.");
        } else {
            System.out.println("You said " + mine + ". The answer is " + expected + ".");
            System.out.println("This is the useful outcome. Read what follows carefully.");
        }
    }

    // -- the card version ------------------------------------------------------------
    //
    // These three build markup and put nothing on the screen, which is what makes them
    // testable without a kernel. tools/test_jvx_ui.py runs them in a plain jshell and
    // checks both what they say and that they use only the four things that survive a
    // notebook nobody has run.

    static String askHtml(String id, String text, String[] options) {
        StringBuilder body = new StringBuilder();
        body.append("<div style=\"font-size:15px;line-height:1.5;margin-bottom:10px\">")
            .append(Ui.prose(text))
            .append("</div>");
        for (String option : options) {
            body.append(optionHtml(option));
        }
        body.append(Ui.small(
            "Pick one before you run anything else. There is a right answer and the wrong "
            + "ones are wrong for reasons worth knowing. Put yours in the next cell: "
            + Ui.code("jvx.answer(&quot;" + Ui.esc(id) + "&quot;, &quot;a&quot;)")));
        return Ui.card(Ui.BLUE, "predict", body.toString());
    }

    /**
     * One option, with its letter pulled out into a chip.
     *
     * Lessons write options as "a) 8", so the letter is already there and splitting it
     * out is presentation. An option written some other way is printed whole rather than
     * mangled into the shape this method was hoping for.
     */
    static String optionHtml(String option) {
        String letter = "";
        String rest = option;
        int bracket = option.indexOf(')');
        if (bracket == 1 && Character.isLetter(option.charAt(0))) {
            letter = option.substring(0, 1);
            rest = option.substring(2).trim();
        }
        String chip = letter.isEmpty() ? "" :
            "<span style=\"font-family:" + Ui.MONO + ";font-weight:700;color:" + Ui.BLUE
            + ";margin-right:10px\">" + Ui.esc(letter) + "</span>";
        return "<div style=\"margin:5px 0 5px 4px;line-height:1.5\">"
            + chip + Ui.prose(rest) + "</div>";
    }

    static String answerHtml(String id, String choice) {
        return Ui.card(Ui.MUTED, "recorded",
            Ui.line("You said " + Ui.code(Ui.esc(choice)) + " for "
                + Ui.code(Ui.esc(id)) + ". It is not marked yet. Run the next cell."));
    }

    static String revealHtml(String id, String expected, String mine) {
        if (mine == null) {
            // Nobody ran the answer cell, which is also what a reader of the published
            // page is looking at, since nothing on it has been run at all. Putting the
            // answer behind a details is the whole reason this rendering exists: it is
            // the only interaction that survives an unrun notebook, so it is the only way
            // a gate can still be a gate on a page somebody is only reading.
            return Ui.card(Ui.MUTED, "not answered",
                Ui.line("You did not commit to an answer, so this is worth less to you than "
                    + "it would have been. Write one down and it will mark it.")
                + Ui.details("Show me the answer anyway",
                    Ui.line("The answer is " + Ui.code(Ui.esc(expected)) + "."), false));
        }
        if (mine.equals(expected)) {
            return Ui.card(Ui.GREEN, "right",
                Ui.line("You said " + Ui.code(Ui.esc(mine)) + ", and that is right.")
                + Ui.small("Read on anyway. Being right for the wrong reason is common here."));
        }
        return Ui.card(Ui.ORANGE, "worth having",
            Ui.line("You said " + Ui.code(Ui.esc(mine)) + ". The answer is "
                + Ui.code(Ui.esc(expected)) + ".")
            + Ui.small("This is the useful outcome. Read what follows carefully."));
    }
}
