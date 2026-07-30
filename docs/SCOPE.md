# TftpOS Module Scope

This document defines which modules are **core** (essential for any firmware
path resolution), **extended** (useful but not required for basic usage), and
which are candidates for eventual migration to downstream projects (PxeOS,
flossware-tftpos, or VirtOS).

Decisions were informed by multi-AI consensus (Nemotron-3 Super 120B,
Nemotron-3 Nano 30B, Cohere North Mini Code) queried via OpenRouter on
2026-07-29, and aligned with the architecture locked in issue #17.

No code is moved or deleted by this document. Physical restructuring, if any,
will happen in later issues.

---

## Core Modules (always installed)

These modules form the minimal foundation required to resolve a device MAC
address to a firmware path, manage plugins, and track provisioning state.

| Module | Purpose |
|--------|---------|
| `engine.py` | Firmware resolution engine (MAC -> path) |
| `matcher.py` | Host-rule matching logic |
| `registry.py` | Plugin registry and discovery |
| `config.py` | Configuration loading and validation |
| `models.py` | Data models (ProvisionProfile, DistroAssets, HostRule, etc.) |
| `state.py` | Provisioning state tracking |
| `plugins/base.py` | FirmwarePlugin abstract base class |
| `plugins/__init__.py` | Plugin package init |
| `db.py` | Database abstraction for state persistence |
| `errors.py` | Error types used across the library |
| `validation.py` | Input validation and sanitization |
| `logging_config.py` | Logging setup (used by core modules) |

**Rationale:** All three models agreed that errors.py and validation.py are
core. The remaining modules in this list (engine, matcher, registry, config,
models, state, plugins/base, db) were proposed as core in the original issue
and confirmed by the umbrella architecture in #17.

---

## Extended Modules (installed, documented as optional/non-core)

These modules ship with TftpOS but are not required for basic firmware path
resolution. They provide value for more advanced provisioning workflows. Users
who only need "MAC -> firmware path" can ignore them.

### Infrastructure and Security

| Module | Purpose | Consensus |
|--------|---------|-----------|
| `auth.py` | Authentication and RBAC | 2/3 EXTENDED, 1/3 CORE |
| `tls.py` | TLS certificate handling | Split (CORE/EXTENDED/APP-LAYER) |
| `secrets.py` | Secrets management | 2/3 APP-LAYER -- kept extended as a library utility |
| `cache.py` | Response and object caching | 2/3 EXTENDED, 1/3 CORE |
| `rate_limit.py` | Request rate limiting | 2/3 EXTENDED, 1/3 CORE |
| `named_objects.py` | Named object registry | 2/3 EXTENDED, 1/3 CORE |

### Provisioning Extras

| Module | Purpose | Consensus |
|--------|---------|-----------|
| `cloud_init.py` | Cloud-init config generation | 2/3 APP-LAYER -- kept extended for now |
| `cloud_image.py` | Cloud image handling | 2/3 APP-LAYER -- kept extended for now |
| `iso_detect.py` | ISO file detection and distro identification | Split -- kept extended per #17 |
| `mnemonics.py` | Human-readable distro aliases | 3/3 EXTENDED |
| `repo_mirror.py` | Repository mirror management | Split -- kept extended |
| `cluster.py` | Multi-host ordered provisioning | 3/3 EXTENDED |
| `console.py` | Serial/VNC/SPICE console proxy | 3/3 EXTENDED |
| `power.py` | BMC/IPMI/Redfish power control | 3/3 EXTENDED |

### Hypervisor Backends

| Module | Purpose | Consensus |
|--------|---------|-----------|
| `client/base.py` | VM client abstract base | 3/3 EXTENDED |
| `client/libvirt_backend.py` | Libvirt/KVM backend | 3/3 EXTENDED |
| `client/bhyve_backend.py` | FreeBSD bhyve backend | 3/3 EXTENDED |
| `client/hyperv_backend.py` | Microsoft Hyper-V backend | 3/3 EXTENDED |
| `client/vmm_backend.py` | OpenBSD VMM backend | 3/3 EXTENDED |

---

## App-Layer Modules (candidates for migration)

These modules were classified by the majority of models as belonging in the
application/frontend layer rather than the library. They remain in-tree for
now but are strong candidates for migration to flossware-tftpos (the
application layer) or other downstream projects.

| Module | Purpose | Consensus | Migration Target |
|--------|---------|-----------|-----------------|
| `webhooks.py` | Webhook event notifications | 3/3 APP-LAYER | flossware-tftpos |
| `metrics.py` | Prometheus metrics export | 3/3 APP-LAYER | flossware-tftpos |
| `audit.py` | Audit trail logging | 3/3 APP-LAYER | flossware-tftpos |
| `observability.py` | Observability utilities | 3/3 APP-LAYER | flossware-tftpos |

---

## Future Migration Candidates

These modules are currently classified as extended but may move to downstream
projects as the ecosystem matures.

| Module | Current Home | Likely Future Home | Reason |
|--------|-------------|-------------------|--------|
| `iso_detect.py` | tftpos (extended) | pxe-os | Distro ISO detection is OS-installer logic |
| `mnemonics.py` | tftpos (extended) | pxe-os | Distro aliases are PxeOS UX |
| `cloud_init.py` | tftpos (extended) | pxe-os or flossware-tftpos | Cloud-init is a provisioning workflow concern |
| `cloud_image.py` | tftpos (extended) | pxe-os or flossware-tftpos | Cloud image management is above firmware serving |
| `cluster.py` | tftpos (extended) | flossware-tftpos | Multi-host orchestration belongs in the app |
| `client/*` | tftpos (extended) | virtos or flossware-tftpos | VM lifecycle is not firmware path resolution |
| `console.py` | tftpos (extended) | virtos or flossware-tftpos | Console access is a UI/ops concern |
| `power.py` | tftpos (extended) | flossware-tftpos | BMC power control is an operational concern |
| `repo_mirror.py` | tftpos (extended) | flossware-tftpos | Mirror management is a deployment concern |

---

## Decision Log

- **2026-07-29:** Initial scope document created via multi-AI consensus
  (3 models queried via OpenRouter). No code moved; documentation only.
  Resolves issues #3, #14, #15, #16. Aligned with umbrella #17 architecture.
- **Approach chosen:** Option B from #3 and #14 (keep in-tree, document as
  non-core). Physical restructuring deferred to later issues.
- **iso_detect + mnemonics (#15):** Keep as extended in TftpOS for now;
  reassess after PxeOS composition is finalized.
- **Hypervisor backends (#16):** Documented as non-core extended modules;
  `pip install tftpos` does not require hypervisor dependencies.

---

## Related Issues

- #3 -- Scope clarification: mark or isolate non-core subsystems
- #4 -- Ship at least one built-in FirmwarePlugin
- #14 -- Move or demote PxeOS-oriented modules out of core foundation
- #15 -- Decide ownership of iso_detect + mnemonics
- #16 -- Hypervisor backends: mark optional or move out of core
- #17 -- Umbrella: solidify TftpOS for real
