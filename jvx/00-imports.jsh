// JShell imports a useful default set, but not these. They are separate snippets on
// purpose: an import in JShell applies to everything typed afterwards, so putting
// them first means a reader's own cells get them too without asking.
//
// The second group is here for a reason worth knowing about, because it cost an
// afternoon. A jshell you start in a terminal imports java.nio.file.* for you. The
// notebook kernel does not: JJava sets its own list, which is java.util, java.io,
// java.math, java.net, java.time, java.util.concurrent, java.util.prefs and
// java.util.regex, and nothing else. So a helper surface that loads perfectly in a
// terminal can fail to compile in the kernel every reader uses, and the error a reader
// sees is `cannot find symbol: variable jvx`, which points at the wrong thing entirely.
// tools/test_jvx_ui.py loads the whole surface with only the kernel's imports for that
// reason.
import java.lang.management.ManagementFactory;
import java.lang.management.RuntimeMXBean;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.lang.reflect.Modifier;
import com.sun.management.HotSpotDiagnosticMXBean;
import com.sun.management.VMOption;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
