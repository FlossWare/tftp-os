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

---

## 2026-07-30: Plugin discovery — lazy class registration

**Issue:** [#44](https://github.com/FlossWare/tftp-os/issues/44)
**Decision:** `discover()` loads classes but never instantiates; `register()` accepts both instances and classes with `**kwargs`
**Alternatives considered:** Factory-only pattern (Worker 1), default-arg plugin (Grok Option 2), `**kwargs` on register only (Worker 3)

**Workers:**

| Model | Provider | Key Type | Recommendation |
|-------|----------|----------|---------------|
| llama-3.3-70b-versatile | Groq | Personal | Factory pattern — entry points point to factories |
| gemma-4-31b | Cerebras | Personal | Hybrid — lazy class registration + optional factories |
| command-r-08-2024 | Cohere | Personal | Change register API — add `**kwargs` |

**Arbiter:** gpt-oss-120b (Cerebras, Personal)

**Arbiter decisions:**

| Worker proposal | Verdict | Rationale |
|----------------|---------|-----------|
| Factory pattern (Worker 1) | Partially accepted | Factory support accepted; `config` arg on `discover()` rejected — forces all callers to supply dict even for listing |
| Lazy class registration (Worker 2) | Accepted | Core of the synthesis — separates discovery from instantiation |
| `**kwargs` on register (Worker 3) | Partially accepted | `**kwargs` accepted; embedding args in entry-point string rejected — not supported by `importlib.metadata` |

**Consensus:** 3/3 agreed on logging errors instead of silent swallow; 3/3 agreed register API must change
**Implementation:** `discover()` stores classes in `discovered` dict, tries zero-arg instantiation for backward compat, logs info when config needed. `register()` accepts `FirmwarePlugin` instances or subclasses with `**kwargs`.

---

## 2026-07-30: Optional extras — honest docs over real isolation

**Issue:** [#48](https://github.com/FlossWare/tftp-os/issues/48)
**Decision:** Update README to be transparent that module extras are documentation-only markers; all modules always ship with `pip install tftpos`
**Alternatives considered:** Real isolation via lazy imports or namespace packages

**Workers:**

| Model | Provider | Key Type | Recommendation |
|-------|----------|----------|---------------|
| llama-3.3-70b-versatile | Groq | Personal | Option A — honest docs, avoid premature complexity |
| command-r-08-2024 | Cohere | Personal | Option A — transparency and clarity, reduced maintenance |
| llama-3.1-70b-instruct | Cloudflare | Personal | Option A — focus on core development, reconsider at 1.0 |

**Arbiter:** gpt-oss-120b (Cerebras, Personal)

**Consensus:** 3/3 unanimous for Option A
**Rationale:** Alpha library should prioritize transparency over packaging complexity. The extras (`power`, `hypervisor`, `cloud`, `cluster`, `observability`) carry no pip dependencies today and exist as documentation-only markers. Real isolation deferred to 1.0 or when external deps are added.

---

## 2026-07-30: Promote staging to core stable API

**Issue:** [#58](https://github.com/FlossWare/tftp-os/issues/58)
**Decision:** Promote `tftpos.staging` and `FirmwareEngine.stage()` from extended to core stable API
**Alternatives considered:** Keep staging as extended and reposition project as firmware-path-resolver only

**Workers:**

| Model | Provider | Key Type | Recommendation |
|-------|----------|----------|---------------|
| llama-3.1-70b-instruct | Cloudflare | Personal | Option A — staging integral to TFTP operations |
| llama-3.3-70b-instruct-fp8 | Cloudflare | Personal | Option A — directly related to project purpose |
| gemma-7b-it | Cloudflare | Personal | Option A — makes library more accessible |

**Arbiter:** llama-3.1-70b-instruct (Cloudflare, Personal)

**Consensus:** 3/3 unanimous for Option A
**Rationale:** The project name is "tftp-os" — without staging, the library only returns a path string and doesn't deliver on its TFTP promise. Staging is the product path: resolve → stage → external TFTP daemon serves. Promoted to core in `__all__`, CONTRACT.md, and SCOPE.md.
