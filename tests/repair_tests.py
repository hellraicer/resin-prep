import bpy,bmesh,sys,json
import numpy as np
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import resin_prep as rp
from resin_prep import repair as ar
passed=[]
def check(name,value):
    assert value,name
    passed.append(name);print('PASS:',name,flush=True)
def cancelled(call):
    try:return call()=={'CANCELLED'}
    except RuntimeError:return True
def clear():
    ar.cancel_preview()
    from resin_prep.inspection import clear_helpers
    clear_helpers();rp._cache.clear()
    if bpy.context.mode!='OBJECT':bpy.ops.object.mode_set(mode='OBJECT')
    bpy.ops.object.select_all(action='SELECT');bpy.ops.object.delete(use_global=False)
def cube(size=8,location=(0,0,4)):
    bpy.ops.mesh.primitive_cube_add(size=size,location=location);return bpy.context.object
def scan(kinds):
    check('repair scan works',bpy.ops.resin_prep.repair_scan()=={'FINISHED'})
    for item in bpy.context.scene.resin_repair.findings:item.enabled=item.kind in kinds
def preview():
    check('repair preview works',bpy.ops.resin_prep.repair_preview()=={'FINISHED'})
    return ar.data()['preview'],ar.data()['report']
def sha(obj):
    mesh=rp.snapshot(bpy.context,obj)
    try:return rp.fingerprint(mesh)
    finally:bpy.data.meshes.remove(mesh)
def open_top(obj):
    def edit(bm):
        bm.normal_update();top=max(bm.faces,key=lambda f:f.calc_center_median().z)
        bmesh.ops.delete(bm,geom=[top],context='FACES_ONLY')
    ar.edit_bmesh(obj,edit)
rp.register();s=bpy.context.scene.resin_repair;p=bpy.context.scene.resin_prep;p.xy=.5;p.dz=.5

clear();obj=cube();original=sha(obj)
ar.edit_bmesh(obj,lambda bm:bmesh.ops.reverse_faces(bm,faces=list(bm.faces)))
original=sha(obj);scan({'normals'});check('inward closed shell detected',ar.data()['scan']['counts']['normals']>0)
result,r=preview();check('inward closed shell corrected',r['after']['counts']['normals']==0)
check('normal repair source untouched',sha(obj)==original)
check('preview excluded from STL',result.get('rp_helper'))
check('source hidden only for preview',obj.hide_get())
check('repair report text persisted',ar.data()['report_text'] in bpy.data.texts)
check('repair report on copy',bool(result.get('rp_repair_report')))
check('preview cancel works',bpy.ops.resin_prep.repair_cancel()=={'FINISHED'})
check('preview cancellation restores source',not obj.hide_get() and sha(obj)==original)

clear();obj=cube(size=10,location=(0,0,5));p.wall=2;bpy.ops.resin_prep.hollow();obj=bpy.context.object
scan({'normals'});result,r=preview()
check('cavity normals preserved by repair',np.isclose(r['after']['mesh']['volume_ml'],.784,atol=1e-5))
check('nested shells have correct alternating orientation',r['after']['counts']['normals']==0)

clear();obj=cube();open_top(obj);original=sha(obj);scan({'holes','normals'})
check('open hole appears in selectable findings',any(i.kind=='holes' and not json.loads(i.detail).get('all_new') for i in s.findings))
result,r=preview();check('automatic hole fill closes cube',r['after']['mesh']['nonmanifold_edges']==0)
check('hole repair clears selected finding',not r['remaining_selected'])
check('hole repair keeps original unchanged',sha(obj)==original)
check('commit repaired copy works',bpy.ops.resin_prep.repair_apply()=={'FINISHED'})
check('committed copy becomes exportable',not result.get('rp_helper'))
check('committed source remains available',obj.name in bpy.data.objects and sha(obj)==original)

clear();obj=cube();open_top(obj);scan({'holes'});s.hole_limit=1
result,r=preview();check('hole size limit respected',r['after']['counts']['holes']==1)
check('unresolved hole explicitly reported',r['remaining_selected'].get('holes')==1)
s.hole_limit=20

clear();left=cube(location=(-10,0,4));open_top(left);right=cube(location=(10,0,4));open_top(right)
left.select_set(True);bpy.context.view_layer.objects.active=left;bpy.ops.object.join();obj=bpy.context.object
scan({'holes'});hole_rows=[i for i in s.findings if i.kind=='holes']
check('two holes separately selectable',len(hole_rows)==2)
hole_rows[1].enabled=False
result,r=preview();check('only chosen hole repaired',r['after']['counts']['holes']==1)
check('unselected remaining hole is not a selected failure',r['remaining_selected'].get('holes',0)==0)

clear();obj=cube()
def add_loose(bm):
    first=next(iter(bm.verts)).co.copy()
    a=bm.verts.new((100,100,100));b=bm.verts.new((101,100,100));bm.edges.new((a,b))
    bm.verts.new(tuple(first))
ar.edit_bmesh(obj,add_loose);scan({'duplicates','loose','degenerate'})
result,r=preview();check('loose geometry removed',r['after']['counts']['loose']==0)
check('duplicate vertices removed',r['after']['counts']['duplicates']==0)

clear();a=cube();b=cube(location=(3,2,5));a.select_set(True);bpy.context.view_layer.objects.active=a;bpy.ops.object.join()
scan({'intersections','nonmanifold','normals'});check('intersecting shells detected',ar.data()['scan']['counts']['intersections']>0)
s.voxel=.4
result,r=preview();check('volumetric self-intersection repair ran',any(o['step']=='volume_reconstruction' for o in r['operations']))
check('reconstructed intersections eliminated',r['after']['counts']['intersections']==0)
check('reconstructed mesh manifold',r['after']['mesh']['nonmanifold_edges']==0)

clear();obj=cube();obj.scale.z=.08;p.min_wall=1.5;scan({'thin','normals'})
check('thin walls detected for automatic correction',ar.data()['scan']['counts']['thin']>0)
result,r=preview();check('thin wall correction removes sampled findings',r['after']['counts']['thin']==0)
check('wall correction logged',any(o['step']=='thin_walls' for o in r['operations']))

clear();obj=cube(location=(0,0,10));scan({'islands'})
check('floating group selectable',any(i.kind=='islands' and not json.loads(i.detail).get('all_new') for i in s.findings))
s.island_mode='SUPPORT';result,r=preview()
check('automatic supports created',any(o['step']=='support' for o in r['operations']))
if r['after']['counts']['islands']!=0:print('SUPPORT_DEBUG',json.dumps({'counts':r['after']['counts'],'error':r['after']['layer_error'],'operations':r['operations']},indent=2),flush=True)
check('automatic support removes diagnosed island',r['after']['counts']['islands']==0)

clear();obj=cube();p.wall=1.5;bpy.ops.resin_prep.hollow();obj=bpy.context.object
scan({'resin_trap','suction'});s.cavity_mode='DRILL';result,r=preview()
check('automatic drain action logged',any(o['step']=='drain' for o in r['operations']))
check('automatic drain clears trapped cavity',r['after']['counts']['resin_trap']==0)
check('automatic drain clears suction risk',r['after']['counts']['suction']==0)

clear();obj=cube();p.wall=1.5;bpy.ops.resin_prep.hollow();scan({'resin_trap'})
s.cavity_mode='FILL';result,r=preview()
check('automatic selected cavity fill removes trap',r['after']['counts']['resin_trap']==0)
check('raster fill explicitly recorded',any(o['step']=='fill_selected_resin_traps' for o in r['operations']))
s.cavity_mode='DRILL'

clear();obj=cube();obj.data.vertices[0].co.z+=1;obj.data.update();scan({'distorted'})
check('nonplanar faces detected',ar.data()['scan']['counts']['distorted']>0)
result,r=preview();check('nonplanar faces triangulated',r['after']['counts']['distorted']==0)

clear();obj=cube()
def zero_face(bm):
    vertices=[bm.verts.new(point) for point in [(15,0,2),(16,0,2),(17,0,2)]];bm.faces.new(vertices)
ar.edit_bmesh(obj,zero_face);scan({'degenerate','loose'})
check('zero-area face detected',ar.data()['scan']['counts']['degenerate']>0)
result,r=preview();check('zero-area face removed',r['after']['counts']['degenerate']==0)
check('deleting degenerate faces leaves no loose vertices',r['after']['counts']['loose']==0)

clear();left=cube();right=cube(location=(8,0,4));left.select_set(True);bpy.context.view_layer.objects.active=left;bpy.ops.object.join();obj=bpy.context.object
ar.edit_bmesh(obj,lambda bm:bmesh.ops.remove_doubles(bm,verts=list(bm.verts),dist=.001))
scan({'nonmanifold','normals'});check('multiply used edges detected',ar.data()['scan']['counts']['nonmanifold']>0)
result,r=preview();check('nonmanifold reconstruction closes topology',r['after']['mesh']['nonmanifold_edges']==0)

clear();left=cube();right=cube(location=(20,0,10));left.select_set(True);bpy.context.view_layer.objects.active=left
scan({'islands'});s.island_mode='REMOVE';result,r=preview()
check('selected floating component removed',r['after']['counts']['islands']==0)
check('grounded unselected component retained',np.isclose(r['after']['mesh']['volume_ml'],.512,atol=.01))
s.island_mode='SUPPORT'

clear();left=cube(location=(-10,0,4));p.wall=1.5;bpy.ops.resin_prep.hollow();left=bpy.context.object
right=cube(location=(10,0,4));bpy.ops.resin_prep.hollow();right=bpy.context.object;left.select_set(True);bpy.context.view_layer.objects.active=left
scan({'resin_trap'});selected=[i for i in s.findings if i.kind=='resin_trap']
check('two trapped cavities individually selectable',len(selected)==2)
selected[1].enabled=False;s.cavity_mode='FILL';result,r=preview()
check('only selected cavity filled',r['after']['counts']['resin_trap']==1)
check('unselected cavity not counted as selected failure',r['remaining_selected'].get('resin_trap',0)==0)
s.cavity_mode='DRILL'
destination=ROOT/'reports'/'Reparatur-Beispielbericht.json'
check('repair JSON export works',bpy.ops.resin_prep.repair_save_report(filepath=str(destination))=={'FINISHED'})
check('export includes before and after results',all(k in json.loads(destination.read_text(encoding='utf-8')) for k in ['before','after','operations','selected','remaining_selected']))

clear();left=cube();right=cube(location=(3,0,4));left.select_set(True);bpy.context.view_layer.objects.active=left;bpy.ops.object.join();obj=bpy.context.object
original=sha(obj);scan({'intersections'});s.voxel=.03;s.voxel_limit=10000
check('volume repair resource limit rejects excessive job',cancelled(lambda:bpy.ops.resin_prep.repair_preview()))
check('failed repair preserves source and visibility',sha(obj)==original and not obj.hide_get())
check('failed repair leaves no working preview',not any(o.get('rp_repair_preview') for o in bpy.data.objects))
s.voxel=.4;s.voxel_limit=3000000

clear();obj=cube();open_top(obj);original=sha(obj)
check('full repair plan generated',bpy.ops.resin_prep.repair_scan()=={'FINISHED'})
s.filter_kind='holes';bpy.ops.resin_prep.repair_select(enabled=False)
check('filtered deselection touches only selected error kind',all(i.enabled for i in s.findings if i.kind!='holes') and all(not i.enabled for i in s.findings if i.kind=='holes'))
bpy.ops.resin_prep.repair_select(enabled=True);s.filter_kind='ALL'
result,r=preview()
check('all-category automatic pass closes an open model',r['after']['mesh']['nonmanifold_edges']==0)
check('all-category pass verifies selected checks',not r['remaining_selected'])
check('all-category pass leaves original geometry intact',sha(obj)==original)

clear();obj=cube();scan({'normals'});obj.location.x+=1
check('changed source blocks automatic repair',cancelled(lambda:bpy.ops.resin_prep.repair_preview()))
obj.location.x-=1;scan({'normals'});result,r=preview();s.hole_limit+=1
check('changed parameters block preview commit',cancelled(lambda:bpy.ops.resin_prep.repair_apply()))
s.hole_limit-=1
result.data.vertices[0].co.x+=.1
check('edited preview blocks commit',cancelled(lambda:bpy.ops.resin_prep.repair_apply()))
ar.cancel_preview()
scan(set())
check('empty repair selection rejected',cancelled(lambda:bpy.ops.resin_prep.repair_preview()))
clear();obj=cube();scan({'normals'});preview();source_name=obj.name
bpy.ops.wm.save_as_mainfile(filepath=str(ROOT/'tests'/'repair_reload.blend'))
bpy.ops.wm.open_mainfile(filepath=str(ROOT/'tests'/'repair_reload.blend'))
obj=bpy.data.objects[source_name]
check('loading saved repair preview restores original visibility',not obj.hide_get())
check('loading hides stale uncommitted repair copy',all(o.hide_get() for o in bpy.data.objects if o.get('rp_repair_preview')))
check('loading invalidates old repair plan',cancelled(lambda:bpy.ops.resin_prep.repair_apply()))
obj.select_set(True);bpy.context.view_layer.objects.active=obj;scan({'normals'});preview();rp.unregister()
check('disable restores uncommitted source',not obj.hide_get())
rp.register();check('repair settings reregister cleanly',hasattr(bpy.context.scene,'resin_repair'));rp.unregister()
(ROOT/'tests'/'repair_results.json').write_text(json.dumps({'version':'0.3.0','blender':bpy.app.version_string,'count':len(passed),'passed':passed},indent=2),encoding='utf-8')
print('ALL REPAIR TESTS PASSED:',len(passed),flush=True)
