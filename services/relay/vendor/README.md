# Vendored Packages

This directory holds third-party / cross-repo Python packages that Relay
ships with at build time, per CSSV1 §3 D8 ("vendored into ... Relay
container ... at build time").

## helium_hash

**Source of truth:** `helium-services-phase3/packages/helium-hash/src/helium_hash/`

**Spec:** `helium-services-phase3/HeartBeat/Documentation/HASHING_CONTRACT.md`

**Why vendored, not pip-installed:**
- `helium-hash` is not (yet) published to PyPI — it's a path-only package
  shared across four consumers (Float SDK, Reader/Scout SDK, Relay, HB).
- Vendoring keeps the Relay container self-contained for Docker build.
- The CSSV1 chip status doc (`CSSV1_CHIP_STATUS.md` §0) explicitly
  contemplates this: "vendored into Relay container at build time."

**Version pin:** v1.0.0 (from `helium-services-phase3@6665d39`, PR #113).

**How `from helium_hash import ...` resolves:**
- `services/relay/pytest.ini` has `pythonpath = vendor` so pytest adds
  this directory to `sys.path`.
- `services/relay/Dockerfile` does `COPY vendor/ vendor/` and sets
  `ENV PYTHONPATH=/app/vendor` so the runtime container resolves
  imports the same way.

## Updating

To re-sync after a helium-hash bump in the source repo:

```powershell
$src = "C:\Users\PROBOOK\helium-services-phase3\packages\helium-hash\src\helium_hash"
$dst = "C:\Users\PROBOOK\helium-multitenant-demo\services\relay\vendor\helium_hash"
Copy-Item "$src\*" $dst -Force
```

Then re-run `pytest tests/` to confirm vectors still pass.

## What MUST NOT be vendored here

- Anything Relay-internal — that goes in `src/`.
- Anything published to PyPI — that goes in `requirements.txt`.
- Anything specific to a single deploy environment — that's container
  config (env vars + volumes).
