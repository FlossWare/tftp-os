# tftp-os Decision Log

Repo-specific decisions. For org-wide decisions (naming, testing standards, licensing), see [FlossWare/DECISIONS.md](https://github.com/FlossWare/FlossWare/blob/master/DECISIONS.md).

AI-assisted decisions with full model/API traceability.

---

## 2026-07-29: App repo naming convention

**Issue:** [#18](https://github.com/FlossWare/tftp-os/issues/18)
**Decision:** App repo will be named `flossware-tftpos` (lowercase-kebab)
**Alternatives considered:** `FlossWare-TftpOS` (PascalCase)

**Models consulted:**

| Model | API | Recommendation |
|-------|-----|---------------|
| Llama 3.3 70B | Groq (free) | `flossware-tftpos` |
| Gemma 4 31B | Cerebras (free) | `flossware-tftpos` |
| Nemotron 3 Ultra 550B | OpenRouter (free) | `flossware-tftpos` |

**Consensus:** Unanimous
**Rationale:** `flossware-*` lowercase-kebab is the app repo convention (precedent: `flossware-nexus`).

**Orchestrator:** Claude opus-4-6 (arbiter), 3 free model APIs (reviewers)

---

## 2026-07-29: Library/app split architecture

**Issue:** [#18](https://github.com/FlossWare/tftp-os/issues/18)
**Decision:** Split tftp-os into `tftpos` (library, PyPI) + `flossware-tftpos` (6 UI frontends)
**Pattern:** Mirrors nexus-java / flossware-nexus split

**Frontends planned:**
1. Python TUI (curses-themes)
2. Python tkinter GUI
3. Python web app (FastAPI + htmx)
4. Java TUI (curses-java)
5. Kotlin Android (Jetpack Compose)
6. Swift iOS (SwiftUI)

**Rationale:** Multiple languages (Python, Java, Kotlin, Swift), multiple build systems (setuptools, Maven, Gradle, Swift PM), multiple platforms (desktop, mobile, terminal) — same pattern that justified the nexus-java split.

**Orchestrator:** Claude opus-4-6
