"""Real Blender extension install in an isolated workspace-only repository."""
import bpy,addon_utils,sys,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
temporary=ROOT/'.test-profile'/'repo'
temporary.mkdir(parents=True,exist_ok=True)
repo=bpy.context.preferences.extensions.repos.new(name='Resin Prep isolated test',module='resinprep_test',custom_directory=str(temporary))
package=ROOT/'dist'/'resin-prep-0.3.0.zip'
result=bpy.ops.extensions.package_install_files(filepath=str(package),repo=repo.module,enable_on_install=True,overwrite=True)
assert result=={'FINISHED'},result
module='bl_ext.'+repo.module+'.resin_prep'
assert hasattr(bpy.context.scene,'resin_prep')
assert addon_utils.check(module)[1]
assert bpy.ops.resin_prep.place()=={'FINISHED'}
assert bpy.ops.resin_prep.layers()=={'FINISHED'}
rp=sys.modules[module]
assert rp._cache['layers'][4]['grouped_islands']
assert hasattr(bpy.context.scene,'resin_repair')
assert bpy.ops.resin_prep.repair_scan()=={'FINISHED'}
for finding in bpy.context.scene.resin_repair.findings:finding.enabled=finding.kind=='normals'
assert bpy.ops.resin_prep.repair_preview()=={'FINISHED'}
assert bpy.ops.resin_prep.repair_cancel()=={'FINISHED'}
rp.inspection.load_reset(None)
assert not rp._cache
addon_utils.disable(module,default_set=False)
assert not hasattr(bpy.context.scene,'resin_prep')
record={'package':package.name,'module':module,'installed':True,'enabled':True,'analysis':True,'automatic_repair_preview':True,'file_load_handler':True,'disabled_cleanly':True,'preferences_saved':False,'repository':str(temporary),'blender':bpy.app.version_string}
(ROOT/'reports'/'Paketinstallation.json').write_text(json.dumps(record,indent=2),encoding='utf-8')
print('PASS: Real extension install, enable, grouped analysis, load handler and disable; no user preferences saved.')
