// A prediction gate.
//
// The rule this project runs on is that a reader who has not committed to an answer
// does not really read the reveal. They skim it and come away feeling like they knew.
// Committing to a wrong answer first is what makes the correction stick, so the gate
// makes you write one down and does not show you anything until you have.
//
// This is the text version and it works everywhere: a notebook with no widgets, a
// terminal, a printed transcript. The PredictGate widget replaces it when probe #4
// has an answer, and nothing in any lesson changes when that happens, because a
// lesson only ever calls jvx.gate, jvx.answer and jvx.reveal.

class Gate {

    static final Map<String, String> question = new LinkedHashMap<>();
    static final Map<String, String> answered = new LinkedHashMap<>();

    static void ask(String id, String text, String... options) {
        question.put(id, text);
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
            System.out.println("No gate called " + id + " is open. Run the gate cell above first.");
            return;
        }
        String cleaned = choice.trim().toLowerCase();
        answered.put(id, cleaned);
        System.out.println("Recorded " + cleaned + " for " + id + ". Now run the next cell.");
    }

    static void reveal(String id, String correct) {
        String expected = correct.trim().toLowerCase();
        String mine = answered.get(id);

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
}
