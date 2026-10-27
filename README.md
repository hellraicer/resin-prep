# Resin Prep

Blender tools for resin 3D-print preparation: inspect meshes, create hollow copies and drain holes, diagnose coarse layers, and repair selected errors on a preview copy.

**Version 0.3.0 · experimental · GPL-3.0-or-later**

[Deutsche Anleitung](README.de.md) · [Automatic repair guide](docs/Automatische-Reparatur.html) · [Contributing](CONTRIBUTING.md)

## Install

1. Download **[resin-prep-0.3.0.zip](dist/resin-prep-0.3.0.zip)**. On GitHub, open that file and use **Download raw file**. Do not unzip it. A future GitHub Release may also provide the same installable ZIP.
2. In Blender, open **Edit → Preferences → Get Extensions**.
3. Open the menu in the upper-right corner and choose **Install from Disk…**.
4. Select `resin-prep-0.3.0.zip`, then enable **Resin Prep** if needed.
5. In the 3D Viewport, press **N** and open the **Resin Prep** tab.

GitHub's **Code → Download ZIP** downloads the source repository, which is different from the installable extension in `dist/`.

The manifest targets Blender **4.2+**; actual development testing was on **Blender 5.2.0 LTS / Windows**. Other Blender versions and operating systems need community testing. UVtools is optional and installed separately; the integration was tested with **UVtools 7.1.2**.

## Features

- Mesh checks: open/nonmanifold edges, inconsistent normals, intersections, loose and degenerate geometry, distorted faces, sampled wall thickness and overhangs.
- Hollow copies, cursor-positioned Boolean drain holes, orientation and build-volume helpers.
- Manual and suggested straight supports.
- Coarse voxel diagnostics: causal floating-island groups, resin traps and suction risks.
- 3D voxel reconstruction, X/Y/Z cuts, slabs, wireframe and Blender X-Ray.
- Layer differences, seven-layer overlays, split comparison and compatible before/after snapshots.
- Center of mass, contact footprint, layer-area curves and geometric area index.
- Selectable automatic repairs with preview, after-checks, commit/cancel and JSON reports.
- Millimeter STL export and a read-only UVtools end-check with grouped islands.

## Units and first use

The default interpretation is **1 Blender unit = 1 mm**. Choose **scene units** for models built with Blender's scene scale. STL does not contain units, so verify a known dimension in both Blender and your printer slicer.

The build plate is world **Z=0** and the print grows along **+Z**. A Saturn 4 Ultra 16K preset is included; build dimensions can be changed for any resin printer. Native printer files and exposure settings are produced by your external slicer.

## Automatic repair

Select the source meshes, open **Automatische Reparatur – Fehlerauswahl**, and choose **Reparaturplan erstellen**. Check the error categories or individual findings to repair, create the automatic preview, inspect the after-checks, and commit the repaired copy or discard it.

Supported operations include duplicate/loose geometry cleanup, normal orientation with nested-shell handling, bounded hole filling, triangulation, voxel reconstruction of detected intersections/nonmanifold topology, sampled thin-wall correction, automatic downward drains, selected cavity filling and island supports/removal.

Volumetric/raster repair can change the whole surface and small details, including areas outside individual findings. Straight supports are geometric suggestions, not a structural proof. Some issues remain unresolved or uncheckable; they are reported explicitly. Keep source models and verify the final sliced file.

## Scope and limits

This is a **coarse geometric preflight tool**, not a native-resolution slicer or a full UVtools replacement. Small contacts and holes can disappear in the diagnostic grid. Thickness and intersection checks have sampling/budget limits. The area index is not a peel-force calculation. There has been no real resin test print as part of development.

All model operations and reports are local. The UVtools bridge starts your selected local executable and does not upload models. Its end-check does not rewrite the input print file.

## Development

```sh
python scripts/build.py
python scripts/run_tests.py --blender /path/to/blender
```

The build produces `dist/resin-prep-0.3.0.zip`. The test runner uses a repository-local test profile and records generated outputs under ignored folders. UVtools checks are optional:

```sh
python scripts/run_tests.py --blender /path/to/blender --uvtools /path/to/UVtools
```

The original Windows development run passed **254 assertions** plus a real extension-install test and UVtools end-check. The prepared GitHub Actions workflow has not yet run on GitHub. See [testing details](docs/TESTING.md).

## License and attribution

GPL-3.0-or-later; see [LICENSE](LICENSE). Source is included in the extension ZIP. UVtools is a separate project with its own license; no UVtools source or binaries are bundled. Features were informed by its public release notes. This project is not affiliated with Blender, ELEGOO, UVtools or Superhive, and contains no code from the commercial Resin Hollow & Drain Holes add-on.
