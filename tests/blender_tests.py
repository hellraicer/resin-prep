import bpy, bmesh, sys, os, json, struct, tempfile, importlib
from pathlib import Path
from mathutils import Vector
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import resin_prep as rp
from resin_prep.layers import diagnose

passed=[]
def test(name,condition):
    assert condition,name
    passed.append(name)
    print('PASS:',name,flush=True)

def clear():
    bpy.ops.object.select_all(action='SELECT'); bpy.ops.object.delete(use_global=False)

def cube(size=10,location=(0,0,5)):
    bpy.ops.mesh.primitive_cube_add(size=size,location=location)
    return bpy.context.object

def analysis(obj):
    mesh=rp.snapshot(bpy.context,obj)
    try: return rp.mesh_checks(bpy.context,mesh)
    finally: bpy.data.meshes.remove(mesh)

rp.register()
s=bpy.context.scene.resin_prep
test('registered settings',s.units=='MM')
clear(); obj=cube()
r=analysis(obj)
test('10mm cube volume = 1ml',abs(r['volume_ml']-1)<1e-6)
test('cube manifold/no intersections',r['nonmanifold_edges']==0 and not r['issues']['intersections'])
test('cube inside bed',r['in_build_volume'])
test('cube one shell',r['shells']==1)
test('cube surface 600 mm2',abs(r['surface_mm2']-600)<1e-6)
test('cube wall thickness',not r['issues']['thin'])
obj.scale.x=-1
test('mirrored cube maintains outward thickness rays',not analysis(obj)['issues']['thin'])
obj.scale.x=1
obj.scale.z=0.05
r=analysis(obj)
test('thin 0.5mm slab detected',len(r['issues']['thin'])>=2)
obj.scale.z=1
obj.location.x=200
test('out of bounds detected',not analysis(obj)['in_build_volume'])
obj.location.x=0
bm=bmesh.new(); bm.from_mesh(obj.data); bm.faces.ensure_lookup_table()
bmesh.ops.delete(bm,geom=[bm.faces[0]],context='FACES'); bm.to_mesh(obj.data); bm.free()
test('open cube detected',analysis(obj)['nonmanifold_edges']>=4)
try: bpy.ops.resin_prep.hollow()
except RuntimeError: pass
test('open mesh hollow rejected without hidden source',not obj.hide_get())

clear(); obj=cube(); original_verts=[v.co.copy() for v in obj.data.vertices]
s.wall=2
test('hollow operator finishes',bpy.ops.resin_prep.hollow()=={'FINISHED'})
hollow=bpy.context.object
test('hollow keeps and hides source',obj.hide_get() and obj.name in bpy.data.objects)
test('source coordinates unchanged',all((a-b.co).length<1e-8 for a,b in zip(original_verts,obj.data.vertices)))
r=analysis(hollow)
test('hollow volume 0.784ml',abs(r['volume_ml']-0.784)<1e-5)
test('hollow manifold',r['nonmanifold_edges']==0)
test('hollow inner + outer surfaces',r['shells']==2)
test('hollow cleanup preserves cavity orientation',bpy.ops.resin_prep.repair()=={'FINISHED'} and abs(analysis(bpy.context.object)['volume_ml']-0.784)<1e-5)
hollow=bpy.context.object

s.xy=0.5;s.dz=0.5
solid,origin=rp.rasterize(bpy.context,[hollow])
lr=diagnose(solid,origin,s.xy,s.dz)
test('closed chamber trapped 0.216ml',abs(lr['trapped_volume_ml']-0.216)<0.001)
test('closed chamber suction risk',len(lr['suction_risks'])>0)
s.hole=4;s.hole_depth=6;s.hole_axis='Z'
bpy.context.scene.cursor.location=(0,0,0)
test('drain operator finishes',bpy.ops.resin_prep.drain()=={'FINISHED'})
drained=bpy.context.object
r=analysis(drained)
test('drain maintains manifold',r['nonmanifold_edges']==0)
test('drain reduces volume',r['volume_ml']<0.784)
solid,origin=rp.rasterize(bpy.context,[drained])
lr=diagnose(solid,origin,s.xy,s.dz)
test('bottom drain releases trapped volume',lr['trapped_voxels']==0)
test('bottom drain vents suction risk',not lr['suction_risks'])

clear();obj=cube(location=(20,10,20));s.lift=5
test('center/lift finishes',bpy.ops.resin_prep.place()=={'FINISHED'})
r=analysis(obj)
test('placed model bottom = 5mm',abs(r['bounds_min_mm'][2]-5)<1e-5)
test('placed model centered',abs(sum([r['bounds_min_mm'][0],r['bounds_max_mm'][0]]))<1e-5)
solid,origin=rp.rasterize(bpy.context,[obj]);lr=diagnose(solid,origin,s.xy,s.dz)
test('floating cube one island at start',len(lr['islands'])==1)
bpy.context.scene.cursor.location=(0,0,0)
s.tip=1.0  # Resolve the contact at XY=0.5mm; 0.4mm tips can vanish in a coarse grid.
test('manual support finishes',bpy.ops.resin_prep.support()=={'FINISHED'})
support=next(o for o in bpy.context.scene.objects if o.get('rp_support'))
sr=analysis(support)
test('support closed',sr['nonmanifold_edges']==0)
test('support begins on bed',abs(sr['bounds_min_mm'][2])<1e-6)
test('support penetrates contact',sr['bounds_max_mm'][2]>r['bounds_min_mm'][2])
solid,origin=rp.rasterize(bpy.context,[obj,support]);lr=diagnose(solid,origin,s.xy,s.dz)
test('supported cube loses island at test resolution',not lr['islands'])
test('auto support finishes',bpy.ops.resin_prep.autosupport()=={'FINISHED'})

clear();obj=cube();other=cube(location=(5,0,5))
obj.select_set(True);other.select_set(True)
solid,origin=rp.rasterize(bpy.context,[obj,other])
lr=diagnose(solid,origin,s.xy,s.dz)
test('overlapping meshes union in voxel analysis',abs(lr['voxel_volume_ml']-1.5)<0.001)
# Join overlapping shells to test BVH intersection candidates.
bpy.context.view_layer.objects.active=obj;bpy.ops.object.join()
r=analysis(obj)
test('crossing shells intersection detected',len(r['issues']['intersections'])>0)

clear();obj=cube();obj.scale=(1,2,3)
bpy.ops.resin_prep.bed()
helper=next(o for o in bpy.context.scene.objects if o.get('rp_helper'))
helper.hide_select=False;helper.select_set(True)
out=ROOT/'tests'/'export_mm.stl'
test('binary STL export finishes',bpy.ops.resin_prep.export(filepath=str(out))=={'FINISHED'})
raw=out.read_bytes();tri_count=struct.unpack_from('<I',raw,80)[0]
test('STL excludes helper',tri_count==12 and len(raw)==84+12*50)
coords=[]
for i in range(tri_count):
    values=struct.unpack_from('<12fH',raw,84+i*50)
    coords.extend([values[3:6],values[6:9],values[9:12]])
extent=[max(v[a] for v in coords)-min(v[a] for v in coords) for a in range(3)]
test('STL applies nonuniform scale in mm',np.allclose(extent,[10,20,30]))

clear();s.units='SCENE';bpy.context.scene.unit_settings.scale_length=1
obj=cube(size=0.01,location=(0,0,0.005))
r=analysis(obj)
test('scene meter conversion gives 1ml',abs(r['volume_ml']-1)<1e-5)
s.wall=2
bpy.ops.resin_prep.hollow();r=analysis(bpy.context.object)
test('hollow honors scene unit conversion',abs(r['volume_ml']-0.784)<1e-4)
s.units='MM'

clear();obj=cube();s.angle_x=45;s.angle_y=0;s.angle_z=0;s.lift=5
test('orientation copy finishes',bpy.ops.resin_prep.orient()=={'FINISHED'})
r=analysis(bpy.context.object)
test('orientation preserves volume',abs(r['volume_ml']-1)<1e-5)
test('orientation sets plate clearance',abs(r['bounds_min_mm'][2]-5)<1e-5)
test('orientation changes bounds',r['dimensions_mm'][2]>14)

clear();obj=cube()
bpy.ops.resin_prep.analyze()
test('analysis report written',bpy.data.texts.get('Resin Prep - Modellbericht.json') is not None)
obj.location.z+=1
try: result=bpy.ops.resin_prep.highlight(kind='overhang')
except RuntimeError: result={'CANCELLED'}
test('stale highlight rejected',result=={'CANCELLED'})
s.voxel_limit=10000;s.xy=0.05;s.dz=0.05
try: rp.rasterize(bpy.context,[obj]);limited=False
except ValueError as e: limited='Zellen' in str(e)
test('voxel memory limit enforced',limited)
s.xy=1;s.dz=1;s.voxel_limit=1500000
bpy.ops.resin_prep.layers();s.layer_index=3
test('layer preview image created',bpy.ops.resin_prep.preview()=={'FINISHED'} and bpy.data.images.get('RP Schichtvorschau') is not None)
test('repair copy finishes',bpy.ops.resin_prep.repair()=={'FINISHED'})

# Pure grid topology fixtures independent of mesh rasterization.
grid=np.zeros((12,12,12),bool);grid[2:10,2:10,2:10]=True;grid[4:8,4:8,4:8]=False
d=diagnose(grid,(0,0,0),1,1)
test('voxel fixture trapped volume',abs(d['trapped_volume_ml']-0.064)<1e-8)
grid[4:12,5:7,5:7]=False
d=diagnose(grid,(0,0,0),1,1)
test('top vent removes final resin trap',d['trapped_voxels']==0)
test('top vent does not eliminate earlier suction',len(d['suction_risks'])>0)
grid=np.zeros((8,8,8),bool);grid[0:2,2:6,2:6]=True;grid[5:7,2:6,2:6]=True
d=diagnose(grid,(0,0,0),1,1)
test('interior empty layers detected',len(d['empty_z_mm'])==3)
test('restart after empty layers is island',len(d['islands'])==1)
rp.unregister();rp.register()
test('unregister/reregister works',hasattr(bpy.context.scene,'resin_prep'))
rp.unregister()
Path(ROOT/'tests'/'test_results.json').write_text(json.dumps({'blender':bpy.app.version_string,'passed':passed,'count':len(passed)},indent=2),encoding='utf-8')
print('ALL TESTS PASSED:',len(passed),flush=True)
