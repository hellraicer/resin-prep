"""Selectable, transactional automatic mesh and resin repair. GPL-3.0-or-later."""
import bpy,bmesh,json,math,hashlib,uuid
import numpy as np
from pathlib import Path
from bpy_extras.io_utils import ExportHelper
from datetime import datetime,timezone
from mathutils import Vector,Matrix
from mathutils.bvhtree import BVHTree
from bpy.props import BoolProperty,EnumProperty,FloatProperty,IntProperty,StringProperty,CollectionProperty,PointerProperty
from . import _cache,mm_factor,snapshot,fingerprint,mesh_checks,targets,tree_from_mesh,remove_copy
from .volume import analyze_volume,surface_mesh

KINDS=[('duplicates','Doppelte Punkte / Flaechen'),('loose','Lose Punkte / Kanten'),
       ('degenerate','Nullkanten / Nullflaechen'),('normals','Normalen / Innenwaende'),
       ('holes','Offene Randloecher'),('distorted','Nichtplanare Flaechen'),
       ('intersections','Selbstueberschneidungen'),('nonmanifold','Mehrfach belegte Kanten'),
       ('thin','Zu duenne Waende'),('islands','Ungestuetzte Inseln'),
       ('resin_trap','Eingeschlossenes Harz'),('suction','Saugrisiken')]
LABELS=dict(KINDS)


def data():return _cache.setdefault('repair',{})


def combined(context,objects):
    verts=[];edges=[];faces=[]
    for obj in objects:
        mesh=snapshot(context,obj);offset=len(verts)
        try:
            verts.extend(tuple(v.co) for v in mesh.vertices)
            edges.extend(tuple(offset+i for i in e.vertices) for e in mesh.edges)
            faces.extend(tuple(offset+i for i in f.vertices) for f in mesh.polygons)
        finally:bpy.data.meshes.remove(mesh)
    mesh=bpy.data.meshes.new('RP Reparatur - ausgewerte Quellen')
    mesh.from_pydata(verts,edges,faces);mesh.update()
    if not mesh.polygons:
        bpy.data.meshes.remove(mesh);raise ValueError('Keine reparierbaren Modellflaechen ausgewaehlt.')
    return mesh


def face_components(bm):
    pending=set(bm.faces);result=[]
    while pending:
        todo=[pending.pop()];group=[]
        while todo:
            face=todo.pop();group.append(face)
            for edge in face.edges:
                for other in edge.link_faces:
                    if other in pending:pending.remove(other);todo.append(other)
        result.append(group)
    return result


def signed_volume(faces):
    if not faces:return 0.0
    origin=faces[0].verts[0].co;volume=0.0
    for face in faces:
        points=[v.co-origin for v in face.verts]
        for i in range(1,len(points)-1):volume+=points[0].dot(points[i].cross(points[i+1]))/6
    return volume


def inside(tree,point):
    direction=Vector((1,.371,.529)).normalized();pos=point.copy();count=0
    for _ in range(2048):
        hit,_,_,_=tree.ray_cast(pos,direction,1e9)
        if hit is None:return bool(count%2)
        count+=1;pos=hit+direction*1e-6
    raise ValueError('Normalenpruefung: zu viele Flaechenschnitte.')


def normal_orientation(bm,fix=False):
    """Closed nested shells alternate signs; an inner cavity stays inward."""
    groups=face_components(bm);closed=[];open_groups=[]
    for faces in groups:
        face_set=set(faces)
        if all(len([f for f in e.link_faces if f in face_set])==2 for f in faces for e in f.edges):closed.append(faces)
        else:open_groups.append(faces)
    trees=[]
    for faces in closed:
        coords=[];polys=[]
        for f in faces:
            start=len(coords);coords.extend(v.co.copy() for v in f.verts);polys.append(tuple(range(start,len(coords))))
        trees.append(BVHTree.FromPolygons(coords,polys))
    inverted=0
    for i,faces in enumerate(closed):
        point=faces[0].calc_center_median()
        depth=sum(inside(t,point) for j,t in enumerate(trees) if i!=j)
        target=-1 if depth%2 else 1
        if signed_volume(faces)*target < -1e-12:
            inverted+=1
            if fix:bmesh.ops.reverse_faces(bm,faces=faces)
    return inverted


def boundary_loops(bm):
    pending={e for e in bm.edges if e.is_boundary};loops=[];branched=0
    while pending:
        todo=[pending.pop()];group=[];vertices=set()
        while todo:
            edge=todo.pop();group.append(edge);vertices.update(edge.verts)
            for v in edge.verts:
                for other in v.link_edges:
                    if other in pending:pending.remove(other);todo.append(other)
        if all(sum(v in e.verts for e in group)==2 for v in vertices) and len(group)>=3:
            coords=np.asarray([tuple(v.co) for v in vertices]);low=coords.min(axis=0);high=coords.max(axis=0)
            loops.append(dict(edges=group,center=coords.mean(axis=0).tolist(),diameter=float(np.linalg.norm(high-low)),bounds=[low.tolist(),high.tolist()]))
        else:branched+=1
    return loops,branched


def topology(mesh,merge):
    bm=bmesh.new();bm.from_mesh(mesh);bm.normal_update();bm.faces.ensure_lookup_table()
    try:
        loops,branched=boundary_loops(bm)
        duplicates=len(bmesh.ops.find_doubles(bm,verts=list(bm.verts),dist=merge)['targetmap'])
        seen=set();duplicate_faces=0
        for face in bm.faces:
            key=frozenset(v.index for v in face.verts)
            if key in seen:duplicate_faces+=1
            seen.add(key)
        inverted=normal_orientation(bm)
        return dict(duplicates=duplicates+duplicate_faces,duplicate_vertices=duplicates,duplicate_faces=duplicate_faces,
                    loose=sum(not v.link_faces for v in bm.verts)+sum(not e.link_faces for e in bm.edges),
                    degenerate=sum(e.calc_length()<1e-6 for e in bm.edges)+sum(f.calc_area()<1e-10 for f in bm.faces),
                    inverted_shells=inverted,nonmanifold=sum(len(e.link_faces)>2 for e in bm.edges),
                    boundary_branches=branched,holes=[{k:v for k,v in loop.items() if k!='edges'} for loop in loops])
    finally:bm.free()


def measure(context,obj,with_layers=True):
    mesh=snapshot(context,obj)
    try:report=mesh_checks(context,mesh);extra=topology(mesh,context.scene.resin_prep.merge)
    finally:bpy.data.meshes.remove(mesh)
    counts=dict(duplicates=extra['duplicates'],loose=extra['loose'],degenerate=extra['degenerate'],
                normals=len(report['issues']['winding'])+extra['inverted_shells'],holes=len(extra['holes']),
                distorted=len(report['issues']['distorted']),intersections=len(report['issues']['intersections']) if report['intersections_checked'] else -1,
                nonmanifold=extra['nonmanifold']+extra['boundary_branches'],thin=len(report['issues']['thin']),
                islands=-1,resin_trap=-1,suction=-1)
    result={'mesh':report,'topology':extra,'counts':counts,'layers':None,'layer_error':None}
    if with_layers:
        from . import rasterize
        try:
            solid,origin=rasterize(context,[obj]);s=context.scene.resin_prep
            lr,maps=analyze_volume(solid,origin,s.xy,s.dz)
            counts.update(islands=len(lr['islands']),resin_trap=len(lr['resin_traps']),suction=len(lr['suction_risks']))
            result['layers']=(solid,origin,lr,maps)
        except ValueError as exc:result['layer_error']=str(exc)
    return result


def sources_signature(context,objects):
    from .inspection import signatures
    return signatures(context,objects)


def scan_parameters(context):
    s=context.scene.resin_prep
    return (s.units,mm_factor(context),s.merge,s.min_wall,s.overhang,s.xy,s.dz,s.sample_limit,s.intersection_limit)


def guard_plan(context):
    cache=data()
    if not cache.get('sources'):raise ValueError('Zuerst einen Reparaturplan erstellen.')
    if cache['scene']!=int(context.scene.as_pointer()) or cache['scan_parameters']!=scan_parameters(context):
        raise ValueError('Szene, Einheiten oder Pruefparameter geaendert: Reparaturplan neu erstellen.')
    objects=[]
    for name,pointer,sha in cache['sources']:
        obj=bpy.data.objects.get(name)
        if obj is None or int(obj.as_pointer())!=pointer:raise ValueError('Reparaturquelle fehlt oder wurde umbenannt: neu pruefen.')
        mesh=snapshot(context,obj)
        try:
            if fingerprint(mesh)!=sha:raise ValueError('Reparaturquelle geaendert: Plan neu erstellen.')
        finally:bpy.data.meshes.remove(mesh)
        objects.append(obj)
    return objects


def selected_rows(context):
    return [r for r in context.scene.resin_repair.findings if r.enabled]


def configuration(context):
    s=context.scene.resin_repair
    values={p.identifier:getattr(s,p.identifier) for p in s.bl_rna.properties if p.identifier not in {'rna_type','findings','status','active_index'} and p.type in {'BOOLEAN','STRING','INT','FLOAT','ENUM'}}
    values['selected']=[r.code for r in selected_rows(context)]
    return json.dumps(values,sort_keys=True)


def cancel_preview(context=None):
    cache=data()
    for obj,hidden in cache.pop('visibility',[]):
        try:
            obj.hide_set(hidden)
            if 'rp_repair_hidden' in obj:del obj['rp_repair_hidden']
            if 'rp_repair_previous_hidden' in obj:del obj['rp_repair_previous_hidden']
        except ReferenceError:pass
    preview=cache.pop('preview',None)
    if preview is not None:
        try:
            if preview.name in bpy.data.objects and preview.get('rp_repair_preview'):remove_copy(preview)
        except ReferenceError:pass
    cache.pop('preview_hash',None);cache.pop('preview_configuration',None)


def create_work(context,objects):
    mesh=combined(context,objects);mesh.transform(Matrix.Scale(1/mm_factor(context),4))
    obj=bpy.data.objects.new('RP Automatische Reparatur - Vorschau',mesh);context.collection.objects.link(obj)
    obj['rp_helper']=True;obj['rp_repair_preview']=True
    for selected in list(context.selected_objects):selected.select_set(False)
    obj.select_set(True);context.view_layer.objects.active=obj
    return obj


def edit_bmesh(obj,operation):
    bm=bmesh.new();bm.from_mesh(obj.data);bm.normal_update()
    try:result=operation(bm);bm.to_mesh(obj.data);obj.data.update();return result
    finally:bm.free()


def repair_basic(context,obj,kinds,rows,log):
    s=context.scene.resin_repair;factor=mm_factor(context)
    def run(bm):
        if 'duplicates' in kinds:
            bmesh.ops.remove_doubles(bm,verts=list(bm.verts),dist=context.scene.resin_prep.merge/factor)
            seen=set();duplicate=[]
            for f in bm.faces:
                key=frozenset(f.verts)
                if key in seen:duplicate.append(f)
                seen.add(key)
            if duplicate:bmesh.ops.delete(bm,geom=duplicate,context='FACES_ONLY')
        if 'loose' in kinds:
            loose=[v for v in bm.verts if not v.link_faces]
            if loose:bmesh.ops.delete(bm,geom=loose,context='VERTS')
        if 'degenerate' in kinds:
            bmesh.ops.dissolve_degenerate(bm,edges=list(bm.edges),dist=1e-6/factor)
            bad=[f for f in bm.faces if f.calc_area()<(1e-10/factor**2)]
            if bad:bmesh.ops.delete(bm,geom=bad,context='FACES_ONLY')
        if 'normals' in kinds:
            bmesh.ops.recalc_face_normals(bm,faces=list(bm.faces));normal_orientation(bm,fix=True)
        if 'holes' in kinds:
            loops,branches=boundary_loops(bm);chosen=[json.loads(r.detail) for r in rows if r.kind=='holes']
            filled=skipped=0
            for loop in loops:
                center=np.asarray(loop['center'])*factor
                requested=any(r.get('all_new') or np.linalg.norm(center-np.asarray(r['center']))<max(.01,context.scene.resin_prep.merge*5) for r in chosen)
                if not requested:continue
                if loop['diameter']*factor>s.hole_limit:skipped+=1;continue
                bmesh.ops.holes_fill(bm,edges=loop['edges'],sides=0);filled+=1
            log.append({'step':'holes','filled_loops':filled,'skipped_size_limit':skipped,'branched_boundaries':branches})
        if 'distorted' in kinds:
            faces=[]
            for f in bm.faces:
                if len(f.verts)>3 and any(abs((v.co-f.calc_center_median()).dot(f.normal))*factor>.05 for v in f.verts):faces.append(f)
            if faces:bmesh.ops.triangulate(bm,faces=faces)
        if 'loose' in kinds:
            loose=[v for v in bm.verts if not v.link_faces]
            if loose:bmesh.ops.delete(bm,geom=loose,context='VERTS')
        if 'normals' in kinds:
            bmesh.ops.recalc_face_normals(bm,faces=list(bm.faces));normal_orientation(bm,fix=True)
    edit_bmesh(obj,run)


def volume_repair(context,obj,log):
    s=context.scene.resin_repair;factor=mm_factor(context)
    corners=[obj.matrix_world@Vector(c) for c in obj.bound_box]
    span=[(max(v[a] for v in corners)-min(v[a] for v in corners))*factor for a in range(3)]
    cells=math.prod(max(1,math.ceil(d/s.voxel)+8) for d in span)
    if cells>s.voxel_limit:raise ValueError(f'Volumenreparatur braucht ca. {cells:,} Zellen. Reparaturvoxel vergroessern oder Limit anpassen.')
    modifier=obj.modifiers.new('RP Volumetrische Reparatur','REMESH');modifier.mode='VOXEL'
    modifier.voxel_size=s.voxel/factor;modifier.adaptivity=0;modifier.use_smooth_shade=False
    bpy.context.view_layer.objects.active=obj
    bpy.ops.object.modifier_apply(modifier=modifier.name)
    log.append({'step':'volume_reconstruction','voxel_mm':s.voxel,'estimated_cells':cells,'note':'Gesamte Oberflaeche rekonstruiert; kleine Details/Spalten koennen sich aendern.'})


def thicken(context,obj,log):
    s=context.scene.resin_repair;factor=mm_factor(context);minimum=context.scene.resin_prep.min_wall
    moved=0
    for _ in range(s.iterations):
        mesh=snapshot(context,obj)
        try:
            tree,_,_=tree_from_mesh(mesh);deltas={}
            for f in mesh.polygons:
                normal=f.normal
                hit,other,_,distance=tree.ray_cast(f.center-normal*.0001,-normal,minimum)
                if hit is None or other.dot(normal)>=-.1 or distance+.0001>=minimum-.001:continue
                delta=normal*((minimum-distance+.002)/2)
                for index in f.vertices:deltas.setdefault(index,[]).append(delta)
            if not deltas:break
            for index,values in deltas.items():
                shift=sum(values,Vector())/len(values)
                if shift.length>s.max_thicken:shift=shift.normalized()*s.max_thicken
                obj.data.vertices[index].co+=shift/factor;moved+=1
            obj.data.update();context.view_layer.update()
        finally:bpy.data.meshes.remove(mesh)
    log.append({'step':'thin_walls','moved_vertex_operations':moved,'target_mm':minimum,'note':'Lokaler Normalenversatz; anschliessend erneut auf Schnitte und Wandstaerken pruefen.'})


def matching(rows,kind,records,context):
    selected=[json.loads(r.detail) for r in rows if r.kind==kind]
    if any(r.get('all_new') for r in selected):return records
    tolerance=max(context.scene.resin_prep.xy,context.scene.resin_prep.dz)*2.5
    result=[]
    for item in selected:
        ordered=sorted(records,key=lambda r:np.linalg.norm(np.asarray(r['position'])-np.asarray(item['position'])))
        if not ordered:continue
        distance=np.linalg.norm(np.asarray(ordered[0]['position'])-np.asarray(item['position']))
        if distance<=tolerance and ordered[0] not in result:result.append(ordered[0])
    return result


def replace_grid(context,obj,solid,origin,log,reason):
    s=context.scene.resin_prep;vertices,faces,_=surface_mesh(solid,origin,s.xy,s.dz,max_faces=s.surface_limit)
    if not faces:raise ValueError('Reparatur wuerde das gesamte Modell entfernen. Auswahl aendern.')
    mesh=bpy.data.meshes.new('RP Repariertes Raster');factor=mm_factor(context)
    mesh.from_pydata([tuple(c/factor for c in v) for v in vertices],[],faces);mesh.update()
    old=obj.data;obj.data=mesh
    if old.users==0:bpy.data.meshes.remove(old)
    log.append({'step':reason,'xy_mm':s.xy,'z_mm':s.dz,'note':'Gesamte ausgewaehlte Geometrie als Rasterkopie rekonstruiert.'})


def drill_down(context,obj,position,log):
    s=context.scene.resin_repair;factor=mm_factor(context);mesh=snapshot(context,obj)
    try:bottom=min(v.co.z for v in mesh.vertices)-s.drain_diameter;top=position[2]+max(context.scene.resin_prep.dz,.2)
    finally:bpy.data.meshes.remove(mesh)
    length=top-bottom
    if length>s.drain_length:raise ValueError('Automatischer Ablauf ueberschreitet die maximale Bohrlaenge.')
    cutter=None
    try:
        bpy.ops.mesh.primitive_cylinder_add(vertices=48,radius=s.drain_diameter/2/factor,depth=length/factor,location=(position[0]/factor,position[1]/factor,(top+bottom)/2/factor))
        cutter=context.object;modifier=obj.modifiers.new('RP Automatischer Bodenablauf','BOOLEAN')
        modifier.operation='DIFFERENCE';modifier.solver='EXACT';modifier.object=cutter
        cutter.select_set(False);obj.select_set(True);context.view_layer.objects.active=obj
        bpy.ops.object.modifier_apply(modifier=modifier.name)
        log.append({'step':'drain','position_mm':position,'diameter_mm':s.drain_diameter,'length_mm':length})
    finally:
        if cutter:remove_copy(cutter)


def add_pillar(context,obj,x,y,approx_z,log):
    s=context.scene.resin_repair;factor=mm_factor(context);mesh=snapshot(context,obj)
    try:
        tree,_,_=tree_from_mesh(mesh)
        hit,normal,_,_=tree.ray_cast(Vector((x,y,approx_z+context.scene.resin_prep.dz)),Vector((0,0,-1)),context.scene.resin_prep.dz*4)
        # From a voxel in the first floating layer, cast upward from below.
        start=max(.001,approx_z-context.scene.resin_prep.dz*2)
        top,n,_,_=tree.ray_cast(Vector((x,y,start)),Vector((0,0,1)),context.scene.resin_prep.dz*5)
        if top is None or n.z>=-.05:raise ValueError('Kein geeigneter Unterseitenkontakt fuer diese Insel gefunden.')
        lower,lower_n,_,_=tree.ray_cast(top-Vector((0,0,.001)),Vector((0,0,-1)),max(top.z,0))
        base=max(0.0,lower.z-.1) if lower is not None else 0.0
        if top.z-base<.5:raise ValueError('Zu wenig Platz fuer einen automatischen Support.')
        tip=max(s.tip,context.scene.resin_prep.xy*1.5)
        shaft=max(s.shaft,tip);foot=max(shaft,s.foot if base==0 else shaft*1.2)
        rings=[(base,foot/2),(min(base+.5,top.z-.2),foot/2),
               (min(base+.8,top.z-.15),shaft/2),(max(base+.2,top.z-1),shaft/2),(top.z+.2,tip/2)]
        rings=sorted(rings,key=lambda item:item[0]);vertices=[];faces=[];segments=16
        for z,radius in rings:
            vertices.extend(((x+math.cos(i*2*math.pi/segments)*radius)/factor,(y+math.sin(i*2*math.pi/segments)*radius)/factor,z/factor) for i in range(segments))
        faces.append(tuple(reversed(range(segments))))
        for ring in range(len(rings)-1):
            for i in range(segments):
                j=(i+1)%segments;a=ring*segments;b=(ring+1)*segments;faces.append((a+i,a+j,b+j,b+i))
        faces.append(tuple(range((len(rings)-1)*segments,len(rings)*segments)))
        pillar_mesh=bpy.data.meshes.new('RP Auto-Support Boolean');pillar_mesh.from_pydata(vertices,[],faces);pillar_mesh.update()
        pillar=bpy.data.objects.new('RP Auto-Support Boolean',pillar_mesh);context.collection.objects.link(pillar)
        try:
            modifier=obj.modifiers.new('RP Support vereinigen','BOOLEAN');modifier.operation='UNION';modifier.solver='EXACT';modifier.object=pillar
            context.view_layer.objects.active=obj;obj.select_set(True)
            bpy.ops.object.modifier_apply(modifier=modifier.name)
        finally:remove_copy(pillar)
        log.append({'step':'support','top_mm':list(top),'base_z_mm':base,'tip_diameter_mm':tip,'note':'Gerader geometrischer Support, keine Festigkeitsgarantie.'})
    finally:bpy.data.meshes.remove(mesh)


def repair_layers(context,obj,kinds,rows,log):
    state=measure(context,obj);layer=state['layers'];s=context.scene.resin_repair
    if layer is None:
        log.append({'step':'layer_repairs','skipped':state['layer_error']});return
    solid,origin,report,maps=layer
    trap=matching(rows,'resin_trap',report['resin_traps'],context) if 'resin_trap' in kinds else []
    cups=matching(rows,'suction',report['suction_risks'],context) if 'suction' in kinds else []
    if s.cavity_mode=='FILL' and trap:
        grid=solid.copy()
        for issue in trap[:s.max_regions]:grid |= maps['resin_trap']==issue['label']
        replace_grid(context,obj,grid,origin,log,'fill_selected_resin_traps')
        state=measure(context,obj);layer=state['layers']
    elif trap:
        for issue in trap[:s.max_regions]:
            try:drill_down(context,obj,issue['position'],log)
            except ValueError as exc:log.append({'step':'drain','unresolved':str(exc),'position_mm':issue['position']})
    if cups:
        for issue in cups[:s.max_regions]:
            try:drill_down(context,obj,issue['position'],log)
            except ValueError as exc:log.append({'step':'suction_drain','unresolved':str(exc)})
    if 'islands' not in kinds:return
    state=measure(context,obj);layer=state['layers']
    if layer is None:log.append({'step':'islands','unresolved':state['layer_error']});return
    solid,origin,report,maps=layer;islands=matching(rows,'islands',report['islands'],context)
    if s.island_mode=='REMOVE' and islands:
        grid=solid.copy()
        for issue in islands[:s.max_regions]:grid[maps['island']==issue['label']]=False
        replace_grid(context,obj,grid,origin,log,'remove_selected_floating_parts');return
    count=0
    for issue in islands[:s.max_regions]:
        z=issue['start_layer'];ys,xs=np.nonzero(maps['island'][z]==issue['label'])
        points=[(float(origin[0]+(x+.5)*context.scene.resin_prep.xy),float(origin[1]+(y+.5)*context.scene.resin_prep.xy)) for y,x in zip(ys,xs)]
        points.sort(key=lambda p:(p[0]-issue['position'][0])**2+(p[1]-issue['position'][1])**2)
        used=[]
        for x,y in points:
            if count>=s.max_supports:break
            if any((x-a)**2+(y-b)**2<s.spacing**2 for a,b in used):continue
            try:add_pillar(context,obj,x,y,issue['position'][2],log);count+=1;used.append((x,y))
            except ValueError as exc:
                if not used:log.append({'step':'support','unresolved':str(exc),'position_mm':[x,y,issue['position'][2]]})
    log.append({'step':'islands','supports_added':count,'support_limit':s.max_supports})


def public_state(state):
    result={k:v for k,v in state.items() if k!='layers'}
    if state['layers'] is not None:result['layer_report']=state['layers'][2]
    return result


def pipeline(context,obj,rows):
    kinds={row.kind for row in rows};log=[];s=context.scene.resin_repair
    before=measure(context,obj);repair_basic(context,obj,kinds,rows,log)
    interim=measure(context,obj,False)
    if ('intersections' in kinds and interim['counts']['intersections']>0) or ('nonmanifold' in kinds and interim['counts']['nonmanifold']>0):
        volume_repair(context,obj,log)
        if 'normals' in kinds:edit_bmesh(obj,lambda bm:(bmesh.ops.recalc_face_normals(bm,faces=list(bm.faces)),normal_orientation(bm,True)))
    if 'thin' in kinds and measure(context,obj,False)['counts']['thin']>0:thicken(context,obj,log)
    if kinds.intersection({'islands','resin_trap','suction'}):repair_layers(context,obj,kinds,rows,log)
    if 'thin' in kinds and measure(context,obj,False)['counts']['thin']>0:thicken(context,obj,log)
    after=measure(context,obj)
    selected_remaining={kind:after['counts'][kind] for kind in kinds}
    if 'holes' in kinds:
        requested=[json.loads(r.detail) for r in rows if r.kind=='holes']
        if not any(r.get('all_new') for r in requested):
            selected_remaining['holes']=sum(any(np.linalg.norm(np.asarray(hole['center'])-np.asarray(r['center']))<max(.01,context.scene.resin_prep.merge*5) for r in requested) for hole in after['topology']['holes'])
    if after['layers'] is not None:
        report=after['layers'][2]
        for kind,key in [('islands','islands'),('resin_trap','resin_traps'),('suction','suction_risks')]:
            if kind in kinds:selected_remaining[kind]=len(matching(rows,kind,report[key],context))
    residual={kind:count for kind,count in selected_remaining.items() if count!=0}
    return dict(version='0.3.0',status='residual_findings' if residual else 'selected_checks_clear',
                selected=[{'kind':r.kind,'code':r.code,'detail':json.loads(r.detail)} for r in rows],
                before=public_state(before),after=public_state(after),remaining_selected=residual,operations=log,
                caveat='Keine Druckfreigabe. Stichproben und Rastergrenzen gelten auch nach automatischer Reparatur. -1 bedeutet nicht pruefbar.')


class RP_RepairFinding(bpy.types.PropertyGroup):
    enabled:BoolProperty(name='Reparieren',default=True)
    kind:StringProperty();code:StringProperty();label:StringProperty();detail:StringProperty();count:IntProperty()


class RP_RepairSettings(bpy.types.PropertyGroup):
    findings:CollectionProperty(type=RP_RepairFinding)
    active_index:IntProperty(default=0,min=0)
    filter_kind:EnumProperty(name='Fehlerfilter',items=[('ALL','Alle Fehlerarten','')]+[(k,v,'') for k,v in KINDS],default='ALL')
    hole_limit:FloatProperty(name='Loch-Fuelldurchmesser max. (mm)',default=20,min=.01)
    voxel:FloatProperty(name='Volumen-Reparaturvoxel (mm)',default=.25,min=.03,max=5)
    voxel_limit:IntProperty(name='Reparatur-Zellenlimit',default=3000000,min=10000,max=10000000)
    iterations:IntProperty(name='Wandkorrektur-Durchlaeufe',default=3,min=1,max=8)
    max_thicken:FloatProperty(name='Versatz pro Durchlauf max. (mm)',default=2,min=.01,max=20)
    cavity_mode:EnumProperty(name='Harzfallen reparieren',items=[('DRILL','Ablauf nach unten bohren','Kann weitere Geometrie unterhalb durchbohren'),('FILL','Ausgewaehlte Harzfalle fuellen','Gesamtes Modell wird als Rasterkopie rekonstruiert')],default='DRILL')
    drain_diameter:FloatProperty(name='Auto-Ablaufdurchmesser (mm)',default=3,min=.5,max=20)
    drain_length:FloatProperty(name='Bohrlaenge max. (mm)',default=60,min=1,max=500)
    island_mode:EnumProperty(name='Inseln reparieren',items=[('SUPPORT','Automatische Supports','Geometrische Vorschlaege; Nachpruefung erforderlich'),('REMOVE','Ausgewaehlte schwebende Teile entfernen','Veraendert die Form; erzeugt eine Rasterkopie')],default='SUPPORT')
    tip:FloatProperty(name='Auto-Support Spitze (mm)',default=1,min=.2)
    shaft:FloatProperty(name='Auto-Support Schaft (mm)',default=1.6,min=.3)
    foot:FloatProperty(name='Auto-Support Fuss (mm)',default=4,min=.5)
    spacing:FloatProperty(name='Auto-Support Abstand (mm)',default=3,min=.5)
    max_supports:IntProperty(name='Auto-Supports max.',default=100,min=1,max=500)
    max_regions:IntProperty(name='Regionen je Fehlerart max.',default=32,min=1,max=200)
    status:StringProperty(default='Quellmodelle waehlen und Reparaturplan erstellen.')


class RP_UL_repair(bpy.types.UIList):
    def draw_item(self,context,layout,data,item,icon,active_data,active_propname,index):
        row=layout.row(align=True);row.prop(item,'enabled',text='');row.label(text=item.label)
    def filter_items(self,context,data,property):
        items=getattr(data,property);kind=context.scene.resin_repair.filter_kind
        return [self.bitflag_filter_item if kind=='ALL' or i.kind==kind else 0 for i in items],[]


class RP_OT_repair_scan(bpy.types.Operator):
    bl_idname='resin_prep.repair_scan';bl_label='Reparaturplan erstellen'
    def execute(self,context):
        temp=None
        try:
            if context.mode!='OBJECT':bpy.ops.object.mode_set(mode='OBJECT')
            prior=data().get('sources',[]);cancel_preview(context)
            from .inspection import clear_helpers
            clear_helpers()
            objects=[o for o in targets(context) if o.visible_get()]
            if not objects:
                objects=[bpy.data.objects.get(name) for name,ptr,_ in prior if bpy.data.objects.get(name) is not None and int(bpy.data.objects[name].as_pointer())==ptr]
            if not objects:raise ValueError('Mindestens ein sichtbares Druckmesh auswaehlen.')
            signatures=sources_signature(context,objects);temp=create_work(context,objects)
            state=measure(context,temp);s=context.scene.resin_repair;s.findings.clear()
            for kind,title in KINDS:
                records=[]
                if kind=='holes':records=[dict(entry,label=f'Randloch {i+1}: D≤{entry["diameter"]:.2f} mm') for i,entry in enumerate(state['topology']['holes'])]
                elif kind in {'islands','resin_trap','suction'} and state['layers'] is not None:
                    key={'islands':'islands','resin_trap':'resin_traps','suction':'suction_risks'}[kind]
                    records=[dict(entry,label=f'{title} {i+1}: Z={entry["z_mm"]:.2f} mm') for i,entry in enumerate(state['layers'][2][key])]
                if not records:records=[{'all_new':True,'label':title+': '+('nach Geometriestufe pruefen' if state['counts'][kind]<0 else str(state['counts'][kind]))}]
                for i,entry in enumerate(records):
                    item=s.findings.add();item.kind=kind;item.code=kind+':'+str(i)
                    item.label=entry.pop('label');item.detail=json.dumps(entry);item.count=state['counts'][kind];item.enabled=True
            cache=data();cache.update(sources=signatures,scene=int(context.scene.as_pointer()),scan_parameters=scan_parameters(context),scan=public_state(state))
            s.status=f'{len(objects)} Quelle(n); {len(s.findings)} auswaehlbare Eintraege.'
            self.report({'INFO'},'Plan erstellt. Gewuenschte Fehler ankreuzen, dann Vorschau reparieren.')
            return {'FINISHED'}
        except Exception as e:self.report({'ERROR'},str(e));return {'CANCELLED'}
        finally:
            if temp:remove_copy(temp)
            if 'objects' in locals() and objects:
                for obj in objects:obj.select_set(True)
                context.view_layer.objects.active=objects[0]


class RP_OT_repair_select(bpy.types.Operator):
    bl_idname='resin_prep.repair_select';bl_label='Fehlerauswahl setzen'
    enabled:BoolProperty(default=True)
    def execute(self,context):
        s=context.scene.resin_repair
        for item in s.findings:
            if s.filter_kind=='ALL' or item.kind==s.filter_kind:item.enabled=self.enabled
        return {'FINISHED'}


class RP_OT_repair_locate(bpy.types.Operator):
    bl_idname='resin_prep.repair_locate';bl_label='Aktuellen Befund im Modell zeigen'
    def execute(self,context):
        try:
            sources=guard_plan(context);s=context.scene.resin_repair
            if not s.findings:raise ValueError('Zuerst einen Plan erstellen.')
            row=s.findings[min(s.active_index,len(s.findings)-1)];item=json.loads(row.detail)
            position=item.get('position',item.get('center'))
            if position is None:
                self.report({'INFO'},'Dies ist eine globale Fehlerkategorie. Einzelne Flaechen ueber Modell pruefen markieren.');return {'FINISHED'}
            context.scene.cursor.location=Vector(position)/mm_factor(context)
            if context.screen:
                for area in context.screen.areas:
                    if area.type=='VIEW_3D':area.spaces.active.region_3d.view_location=context.scene.cursor.location
            self.report({'INFO'},row.label);return {'FINISHED'}
        except Exception as e:self.report({'ERROR'},str(e));return {'CANCELLED'}


class RP_OT_repair_preview(bpy.types.Operator):
    bl_idname='resin_prep.repair_preview';bl_label='Ausgewaehlte Fehler automatisch reparieren'
    bl_options={'REGISTER','UNDO'}
    def execute(self,context):
        obj=None
        try:
            if context.mode!='OBJECT':bpy.ops.object.mode_set(mode='OBJECT')
            sources=guard_plan(context);rows=selected_rows(context)
            if not rows:raise ValueError('Mindestens eine Fehlerart oder einen Befund auswaehlen.')
            cancel_preview(context);obj=create_work(context,sources)
            report=pipeline(context,obj,rows);cache=data()
            report['sources']={name:sha for name,_,sha in cache['sources']};report['configuration']=json.loads(configuration(context))
            report['timestamp_utc']=datetime.now(timezone.utc).isoformat()
            cache['preview']=obj;cache['report']=report;cache['preview_configuration']=configuration(context)
            mesh=snapshot(context,obj)
            try:cache['preview_hash']=fingerprint(mesh)
            finally:bpy.data.meshes.remove(mesh)
            cache['visibility']=[(source,source.hide_get()) for source in sources]
            for source,hidden in cache['visibility']:
                source['rp_repair_hidden']=True;source['rp_repair_previous_hidden']=hidden;source.hide_set(True)
            obj['rp_repair_report']=json.dumps(report,ensure_ascii=False)
            name='Resin Prep - Reparatur '+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+' '+uuid.uuid4().hex[:5]+'.json'
            text=bpy.data.texts.new(name);text.write(json.dumps(report,indent=2,ensure_ascii=False));cache['report_text']=name
            residual=report['remaining_selected'];context.scene.resin_repair.status='Vorschau fertig: '+('Restbefunde / ungepruefte Kategorien vorhanden.' if residual else 'Ausgewaehlte Pruefungen ohne Befund.')
            self.report({'WARNING'} if residual else {'INFO'},context.scene.resin_repair.status)
            return {'FINISHED'}
        except Exception as e:
            if obj and obj.name in bpy.data.objects:remove_copy(obj)
            self.report({'ERROR'},str(e));return {'CANCELLED'}


class RP_OT_repair_apply(bpy.types.Operator):
    bl_idname='resin_prep.repair_apply';bl_label='Reparierte Kopie uebernehmen'
    bl_options={'REGISTER','UNDO'}
    def execute(self,context):
        try:
            guard_plan(context);cache=data();obj=cache.get('preview')
            if obj is None or obj.name not in bpy.data.objects:raise ValueError('Zuerst eine Reparaturvorschau erstellen.')
            if configuration(context)!=cache.get('preview_configuration'):raise ValueError('Auswahl/Parameter geaendert: Vorschau neu erstellen.')
            mesh=snapshot(context,obj)
            try:
                if fingerprint(mesh)!=cache['preview_hash']:raise ValueError('Vorschau wurde bearbeitet: Reparatur erneut ausfuehren.')
            finally:bpy.data.meshes.remove(mesh)
            obj['rp_helper']=False;obj['rp_repair_preview']=False;obj.name='RP Reparierte Kopie'
            for source,_ in cache.pop('visibility',[]):
                if 'rp_repair_hidden' in source:del source['rp_repair_hidden']
                if 'rp_repair_previous_hidden' in source:del source['rp_repair_previous_hidden']
            cache.pop('preview',None);obj.select_set(True);context.view_layer.objects.active=obj
            context.scene.resin_repair.status='Kopie uebernommen. Originale bleiben unveraendert und versteckt.'
            self.report({'INFO'},context.scene.resin_repair.status);return {'FINISHED'}
        except Exception as e:self.report({'ERROR'},str(e));return {'CANCELLED'}


class RP_OT_repair_cancel(bpy.types.Operator):
    bl_idname='resin_prep.repair_cancel';bl_label='Vorschau verwerfen / Originale zeigen'
    bl_options={'REGISTER','UNDO'}
    def execute(self,context):cancel_preview(context);context.scene.resin_repair.status='Vorschau verworfen; Originale wiederhergestellt.';return {'FINISHED'}


class RP_OT_repair_save(bpy.types.Operator,ExportHelper):
    bl_idname='resin_prep.repair_save_report';bl_label='Reparaturbericht als JSON speichern'
    filename_ext='.json'
    filter_glob:StringProperty(default='*.json',options={'HIDDEN'})
    def execute(self,context):
        try:
            report=data().get('report')
            if not report:raise ValueError('Zuerst eine Reparaturvorschau ausfuehren.')
            Path(self.filepath).write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
            self.report({'INFO'},'Vorher-/Nachherbericht gespeichert.');return {'FINISHED'}
        except Exception as e:self.report({'ERROR'},str(e));return {'CANCELLED'}


class RP_PT_repair(bpy.types.Panel):
    bl_label='Automatische Reparatur - Fehlerauswahl';bl_idname='RP_PT_repair';bl_parent_id='RP_PT_main'
    bl_space_type='VIEW_3D';bl_region_type='UI';bl_category='Resin Prep'
    def draw(self,context):
        l=self.layout;s=context.scene.resin_repair
        l.label(text='Ausgewaehlte Druckmeshes → eine Kopie')
        l.operator('resin_prep.repair_scan',icon='VIEWZOOM');l.prop(s,'filter_kind')
        l.template_list('RP_UL_repair','',s,'findings',s,'active_index',rows=7)
        row=l.row(align=True);row.operator('resin_prep.repair_select',text='Alle im Filter').enabled=True;row.operator('resin_prep.repair_select',text='Keine im Filter').enabled=False
        l.operator('resin_prep.repair_locate')
        l.prop(s,'hole_limit');l.prop(s,'voxel');l.prop(s,'voxel_limit')
        l.prop(s,'cavity_mode');l.prop(s,'drain_diameter');l.prop(s,'drain_length')
        l.prop(s,'island_mode');l.prop(s,'spacing');l.prop(s,'max_supports')
        header,body=l.panel('rp_repair_advanced',default_closed=True);header.label(text='Weitere Reparaturgrenzen')
        if body:
            for p in ['iterations','max_thicken','tip','shaft','foot','max_regions']:body.prop(s,p)
        l.operator('resin_prep.repair_preview',icon='MODIFIER')
        report=data().get('report')
        if report:
            l.label(text='Nachpruefung ausgewaehlter Fehler:')
            for kind,value in report['remaining_selected'].items():l.label(text=LABELS[kind]+': '+('nicht pruefbar' if value<0 else str(value)),icon='ERROR')
            if not report['remaining_selected']:l.label(text='Kein Befund in ausgewaehlten Pruefungen',icon='CHECKMARK')
        from . import wrapped
        wrapped(l,s.status)
        row=l.row(align=True);row.operator('resin_prep.repair_apply',icon='CHECKMARK');row.operator('resin_prep.repair_cancel',icon='LOOP_BACK')
        l.label(text='Bericht: Blender-Texteditor / Kopie')
        l.operator('resin_prep.repair_save_report')
        l.label(text='Volumen-/Rasterreparatur aendert die Form.')


classes=(RP_RepairFinding,RP_RepairSettings,RP_UL_repair,RP_OT_repair_scan,RP_OT_repair_select,RP_OT_repair_locate,
         RP_OT_repair_preview,RP_OT_repair_apply,RP_OT_repair_cancel,RP_OT_repair_save,RP_PT_repair)


def register_settings():bpy.types.Scene.resin_repair=PointerProperty(type=RP_RepairSettings)
def unregister_settings():
    cancel_preview()
    if hasattr(bpy.types.Scene,'resin_repair'):del bpy.types.Scene.resin_repair
