# Resin Prep 0.3.0 — experimental

Install the attached **resin-prep-0.3.0.zip** through Blender's **Preferences → Get Extensions → Install from Disk** menu. Do not install GitHub's automatic source-code ZIP directly.

This version adds selectable automatic repair with preview, after-checks and copy-based commit/cancel, alongside mesh preparation, coarse layer diagnostics and a UVtools end-check.

Tested locally on Blender 5.2.0 LTS / Windows. The development suites passed 254 assertions, plus real extension installation and UVtools 7.1.2 integration. Other platforms and Blender versions have not yet been validated, and there is no real-print validation. GitHub Actions results will be available after the repository is published and the workflow has run.

Voxel/raster repair may change small details. Inspect the repair preview and verify the final sliced file with the intended printer profile.
