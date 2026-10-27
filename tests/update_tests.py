"""0.2 golden geometry/topology, operators, persistence and real UVtools tests."""
import bpy,bmesh,sys,json,struct,hashlib,os
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import resin_prep as rp
from resin_prep.volume import analyze_volume,section_rgba,clip_volume,surface_mesh,floating_labels
from resin_prep import inspection as ins
passed=[]
def check(name,condition):
    assert condition,name
    passed.append(name);print('PASS:',name,flush=True)
def cube(size=8,location=(0,0,4)):
    bpy.ops.mesh.primitive_cube_add(size=size,location=location);return bpy.context.object
def clear():
    ins.clear_helpers();rp._cache.clear()
    bpy.ops.object.select_all(action='SELECT');bpy.ops.object.delete(use_global=False)
def analysis(grid,origin=(0,0,0),xy=1,dz=1):return analyze_volume(grid,np.array(origin),xy,dz)
def mesh_info(vertices,faces):
    mesh=bpy.data.meshes.new('Test');mesh.from_pydata(vertices,[],faces);mesh.update()
    bm=bmesh.new();bm.from_mesh(mesh)
    value=(all(e.is_manifold for e in bm.edges),bm.calc_volume(signed=True))
    bm.free();bpy.data.meshes.remove(mesh);return value
def cancelled(call):
    try:return call()=={'CANCELLED'}
    except RuntimeError:return True

# Causal grouping, including merges, splits and later valid support.
grid=np.zeros((8,10,12),bool);grid[2:6,3:5,3:5]=True
r,m=analysis(grid)
check('floating descendants remain unsupported',np.count_nonzero(m['island'])==16)
check('floating column is one grouped issue',len(r['islands'])==1 and r['islands'][0]['start_layer']==2 and r['islands'][0]['end_layer']==5)
grid=np.zeros((8,10,12),bool);grid[2:5,3:5,2:4]=True;grid[2:5,3:5,7:9]=True
r,m=analysis(grid);check('separate floating branches stay separate',len(r['islands'])==2)
grid[5,3:5,2:9]=True
r,m=analysis(grid);check('merging floating branches groups ancestors',len(r['islands'])==1 and len(np.unique(m['island'][m['island']>0]))==1)
grid[6:8,3:5,2:4]=True;grid[6:8,3:5,7:9]=True
r,m=analysis(grid);check('split descendants retain shared group',len(r['islands'])==1 and r['islands'][0]['end_layer']==7)
grid=np.zeros((8,10,12),bool);grid[0:8,3:5,0:2]=True;grid[2:8,3:5,6:8]=True;grid[5,3:5,1:8]=True
r,m=analysis(grid)
check('later grounded connection stops floating propagation',not m['island'][5:].any())
check('later connection does not heal earlier floating layers',m['island'][2:5].any() and r['islands'][0]['end_layer']==4)
grid=np.zeros((5,6,6),bool);grid[0,1:3,1:3]=True;grid[1:4,3:5,3:5]=True
r,m=analysis(grid);check('diagonal-only layer contact is not grounded',len(r['islands'])==1)

# Air topology and dimensional correctness.
grid=np.zeros((9,10,10),bool);grid[1:8,1:9,1:9]=True;grid[3:6,3:7,3:7]=False
r,m=analysis(grid,xy=0.25,dz=0.05)
check('trapped chamber is one 3D issue',len(r['resin_traps'])==1)
check('trapped volume has ml units',np.isclose(r['trapped_volume_ml'],48*.25*.25*.05/1000))
check('issue area is mm2 not volume',np.isclose(r['resin_traps'][0]['area_mm2'],16*.25*.25))
check('issue volume and area stored separately',np.isclose(r['resin_traps'][0]['volume_ml'],r['trapped_volume_ml']))
grid[3:9,4:6,4:6]=False;r,m=analysis(grid)
check('future top vent removes final resin trap',r['trapped_voxels']==0)
check('future top vent retains earlier cup warning',len(r['suction_risks'])>0)
check('layer curves cover all layers',len(r['layer_curve'])==len(grid))
check('geometry risk score is dimensionless and bounded',all(0<=p['area_risk_index']<=1 for p in r['layer_curve']))

# Contact footprint and uniform-density centroid.
grid=np.zeros((6,8,10),bool);grid[0:4,2:6,2:8]=True
r,m=analysis(grid)
check('voxel centroid is correct',np.allclose(r['stability']['center_of_mass_mm'],[5,4,2]))
check('contact area correct',r['stability']['contact_area_mm2']==24)
check('centroid lies in contact hull',r['stability']['center_projection_in_contact_hull'])
grid=np.zeros((6,8,12),bool);grid[0,2:4,1:3]=True;grid[1:5,2:6,6:11]=True
r,m=analysis(grid);check('overhanging centroid outside contact hull flagged',not r['stability']['center_projection_in_contact_hull'])
grid[0]=False;r,m=analysis(grid);check('floating model has no contact footprint',r['stability']['contact_area_mm2']==0)

# Image modes with explicit expected pixels.
grid=np.zeros((7,5,8),bool);grid[2,1,1]=True;grid[3,1,1]=True;grid[3,2,2]=True;grid[4,3,3]=True
rgba=section_rgba(grid,3,'DIFFERENCE',-1)
check('difference new pixels green',np.allclose(rgba[2,2,:3],[.1,1,.25]))
check('difference common pixels grey',np.allclose(rgba[1,1,:3],[.65,.65,.65]))
rgba=section_rgba(grid,2,'DIFFERENCE',1)
check('difference missing pixels red',np.allclose(rgba[2,2,:3],[1,.15,.1]))
rgba=section_rgba(grid,3,'ONION')
check('onion current layer stays white',np.allclose(rgba[2,2,:3],[1,1,1]))
check('onion next layer visible',rgba[3,3,0]>0)
check('all image modes opaque',all(np.all(section_rgba(grid,3,mode)[:,:,3]==1) for mode in ['PLAIN','DIFFERENCE','ONION','SPLIT']))
ref=np.zeros_like(grid);ref[3,4,7]=True
rgba=section_rgba(grid,3,'SPLIT',reference=ref,split=.5)
check('split right side uses comparison snapshot',np.allclose(rgba[4,7,:3],1))
check('split left side uses current layer',np.allclose(rgba[2,2,:3],1))

# Closed outward reconstruction and axis-specific, capped clipping.
grid=np.ones((4,6,8),bool)
v,f,c=surface_mesh(grid,(10,20,30),.5,.25)
closed,volume=mesh_info(v,f)
check('reconstructed block has manifold edges',closed)
check('reconstructed block outward volume',np.isclose(volume,4*6*8*.5*.5*.25))
check('reconstruction physical origin preserved',np.allclose(np.min(v,axis=0),[10,20,30]))
for axis in ['X','Y','Z']:
    clipped=clip_volume(grid,(0,0,0),1,1,axis,'BELOW',50,10)
    v,f,_=surface_mesh(clipped,(0,0,0),1,1);closed,volume=mesh_info(v,f)
    check('capped '+axis+' half-cut is manifold',closed)
    check('capped '+axis+' half-cut has half volume',np.isclose(volume,grid.sum()/2))
check('slab keeps a centered band',0<clip_volume(grid,(0,0,0),1,1,'X','SLAB',50,25).sum()<grid.sum())
try:surface_mesh(grid,(0,0,0),1,1,max_faces=1);limited=False
except ValueError:limited=True
check('mesh surface budget enforced',limited)

# Actual Blender operators, cache validity, restore and persistence.
rp.register();s=bpy.context.scene.resin_prep;clear();obj=cube();s.xy=1;s.dz=1
check('updated analysis operator finishes',bpy.ops.resin_prep.layers()=={'FINISHED'})
check('source hash recorded',obj.name in rp._cache['layers'][4]['source_hashes'])
check('comparison snapshot operator works',bpy.ops.resin_prep.reference()=={'FINISHED'})
for mode in ['PLAIN','DIFFERENCE','ONION','SPLIT']:
    s.preview_mode=mode
    check('Blender image mode '+mode,bpy.ops.resin_prep.preview()=={'FINISHED'})
s.compare_reference=True;s.show_image_grid=True
check('reference + physical image grid',bpy.ops.resin_prep.preview()=={'FINISHED'})
s.compare_reference=False;s.show_image_grid=False
s.clip_axis='Z';s.clip_mode='BELOW';s.clip_position=50
check('3D cut operator finishes',bpy.ops.resin_prep.inspect3d()=={'FINISHED'})
check('source visibility hidden for isolated preview',obj.hide_get())
check('guard accepts same hidden source',ins.guard(bpy.context) is not None)
preview=next(o for o in bpy.data.objects if o.get('rp_preview'))
check('preview is helper excluded from STL',preview.get('rp_helper'))
check('reconstruction operator creates print mesh copy',bpy.ops.resin_prep.reconstruct()=={'FINISHED'} and not bpy.context.object.get('rp_helper'))
reconstruction=bpy.context.object
check('reconstruction carries resolution metadata',reconstruction.get('rp_grid_xy_mm')==1)
check('reconstruction STL exporter works',bpy.ops.resin_prep.export(filepath=str(ROOT/'tests'/'reconstructed.stl'))=={'FINISHED'})
check('clearing helpers works',bpy.ops.resin_prep.clear_inspection()=={'FINISHED'})
check('clearing restores source visibility',not obj.hide_get())
check('clearing preserves reconstructed result',reconstruction.name in bpy.data.objects)
s.report_folder=str(ROOT/'reports'/'test-runs');s.clip_mode='FULL'
check('full detail report export succeeds',bpy.ops.resin_prep.save_reports()=={'FINISHED'})
folder=Path(rp._cache['last_report_folder'])
check('report contains expected files',all((folder/n).is_file() for n in ['Analyse.html','Schichtbericht.json','Schichtkurve.csv','Einstellungen.json','Diagnoseraster.npz','Schichtvorschau.png']))
with np.load(folder/'Diagnoseraster.npz',allow_pickle=False) as archive:
    check('saved raster roundtrips exactly',np.array_equal(archive['solid'],rp._cache['layers'][0]))
obj.location.x+=1
check('changed source invalidates image preview',cancelled(lambda:bpy.ops.resin_prep.preview()))
check('changed source invalidates reconstruction',cancelled(lambda:bpy.ops.resin_prep.reconstruct()))
obj.location.x-=1;s.units='SCENE'
check('changed units invalidate analysis',cancelled(lambda:bpy.ops.resin_prep.preview()))
s.units='MM'

# Analyze a floating closed object to test issue selection and cursor navigation.
clear();obj=cube(location=(0,0,10));s.xy=1;s.dz=1;s.clip_mode='FULL'
bpy.ops.resin_prep.layers();s.issue_kind='island'
check('issue overlay operator finishes',bpy.ops.resin_prep.issue_overlays()=={'FINISHED'})
check('island overlay geometry exists',any(o.get('rp_issue_overlay') for o in bpy.data.objects))
check('issue navigation succeeds',bpy.ops.resin_prep.issue_focus()=={'FINISHED'})
issue=ins.current_issue(bpy.context)
check('issue navigation sets cursor to lowest affected layer',np.allclose(bpy.context.scene.cursor.location,issue['position']))
check('stability markers operator succeeds',bpy.ops.resin_prep.stability_markers()=={'FINISHED'})
check('centroid marker exists',any(o.get('rp_stats') for o in bpy.data.objects))
check('playback starts',bpy.ops.resin_prep.simulate()=={'FINISHED'})
before=s.layer_index;ins.simulation_tick()
check('playback advances one layer',s.layer_index==before+1)
ins.stop_simulation();check('playback stop removes timer',not bpy.app.timers.is_registered(ins.simulation_tick))
ins.clear_helpers()

# Repair one trapped region on a separate voxel reconstruction.
clear();obj=cube();s.xy=1;s.dz=1;s.wall=1
bpy.ops.resin_prep.hollow();obj=bpy.context.object;bpy.ops.resin_prep.layers();s.issue_kind='resin_trap';s.issue_index=1
source_hash=rp._cache['sources'][0][2]
check('fill trapped region creates copy',bpy.ops.resin_prep.fill_trap()=={'FINISHED'})
filled=bpy.context.object
mesh=rp.snapshot(bpy.context,obj)
check('fill leaves source geometry unchanged',rp.fingerprint(mesh)==source_hash)
bpy.data.meshes.remove(mesh)
check('fill result is independently exportable',not filled.get('rp_helper') and filled.get('rp_filled_issue') is not None)
ins.clear_helpers()

# Real UVtools capability / integration check is separate from pure geometry.
s.uv_exe=os.environ.get('RESIN_PREP_UVTOOLS',s.uv_exe)
if s.uv_exe and Path(s.uv_exe).is_file():
    info=ins.uv_capabilities(Path(s.uv_exe))
    check('installed UVtools supports group-islands',info['group_islands'])
    check('UI UVtools capability check',bpy.ops.resin_prep.uvcapabilities()=={'FINISHED'})
else:
    print('SKIP: two optional UVtools capability checks; set RESIN_PREP_UVTOOLS to include them.',flush=True)
clear();obj=cube();bpy.ops.resin_prep.layers();bpy.ops.resin_prep.inspect3d()
reload_name=obj.name
bpy.ops.wm.save_as_mainfile(filepath=str(ROOT/'tests'/'reload_visibility.blend'))
bpy.ops.wm.open_mainfile(filepath=str(ROOT/'tests'/'reload_visibility.blend'))
check('file-load handler clears cached analysis',not rp._cache)
check('reopening saved solo preview restores source visibility',not bpy.data.objects[reload_name].hide_get())
check('reopening hides stale reconstruction helpers',all(o.hide_get() for o in bpy.data.objects if o.get('rp_helper') and o.get('rp_reconstruction')))
obj=bpy.data.objects[reload_name];obj.select_set(True);bpy.context.view_layer.objects.active=obj
bpy.ops.resin_prep.layers();bpy.ops.resin_prep.inspect3d();rp.unregister()
check('disabling addon restores source visibility',not obj.hide_get())
rp.register()
check('0.2 unregister/register clean',hasattr(bpy.context.scene,'resin_prep'))
rp.unregister()
(ROOT/'tests'/'update_results.json').write_text(json.dumps({'version':'0.2.0','blender':bpy.app.version_string,'count':len(passed),'passed':passed},indent=2),encoding='utf-8')
print('ALL UPDATE TESTS PASSED:',len(passed),flush=True)
