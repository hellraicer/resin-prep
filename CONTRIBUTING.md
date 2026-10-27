# Contributing

Bug reports, reproducible meshes and improvements are welcome. Please describe the Blender version, operating system, units mode, selected settings and exact steps. A small synthetic mesh is preferable to an unrelated large scene. Remove personal or confidential data before attaching files.

## Setup

Clone the repository, install a supported Blender version, then run:

```sh
python scripts/build.py
python scripts/run_tests.py --blender /path/to/blender
```

No third-party Python packages are needed for the build scripts. Tests run inside Blender and use its bundled NumPy. UVtools integration tests require a separate installation; pass `--uvtools` to include them.

## Changes

- Keep all model edits on copies until the user commits the preview.
- Preserve millimeter semantics and scene-unit conversion.
- Do not treat uncheckable results as zero errors.
- Preserve source validation and cancellation/visibility restoration.
- Add a meaningful geometry or lifecycle test for repair behavior changes.
- Update both READMEs or the detailed guide when user-visible behavior changes.
- Do not add generated logs, personal paths, test profiles or private models.

Submit a pull request with the problem, final behavior and validation. Contributions must be distributable under GPL-3.0-or-later. Do not copy incompatible code or third-party assets into the project.
