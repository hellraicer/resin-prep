# Testing

## Verified development environment

- Blender 5.2.0 LTS on Windows.
- UVtools UI 7.1.2 / UVtoolsCmd 2.2.0.
- 58 original mesh/export assertions, 83 layer/inspection assertions and 113 repair assertions: **254 total**.
- Separate real extension installation/enable/disable and UVtools grouped end-check.
- No real resin print validation.

The public scripts preserve the three geometry suites. Two UVtools capability assertions are skipped when no UVtools executable is present. A typical Linux CI run therefore has 252 geometry/lifecycle assertions plus the package-install scenario. Pass `--uvtools` to include the optional bridge test and capability checks.

## Run

Python 3.11+ is needed for the standard-library build scripts (`tomllib`). Blender supplies the Python API and NumPy used by the geometry tests.

```sh
python scripts/run_tests.py --blender /path/to/blender
```

The runner builds the extension, redirects Blender configuration/extension paths into `.test-profile/`, and executes each suite in a fresh background process with factory startup and a Python error exit code. It does not save preferences to a normal Blender profile.

Generated logs, test meshes, Blend files and reports are ignored by Git. Expected failures, such as rejecting stale previews or oversized voxel jobs, can appear as `Error:` lines in otherwise passing logs. The process exit code and assertion summary determine success.

## GitHub Actions

The workflow downloads Blender 5.2.0 from Blender's official distribution, verifies the vendor SHA-256 file, runs the suites and validates the extension manifest. Linux execution remains unverified until the first GitHub run. The workflow does not install UVtools or claim equivalent coverage for that optional integration.

No CI success badge is included before a real GitHub run exists.
