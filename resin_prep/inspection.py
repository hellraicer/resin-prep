"""Blender integration for Resin Prep 0.2 inspection and report persistence."""
import bpy
import numpy as np
import csv
import json
import uuid
import subprocess
from pathlib import Path
from datetime import datetime, timezone
from mathutils import Vector
from bpy.props import IntProperty, BoolProperty
from bpy.app.handlers import persistent
from . import _cache, mm_factor, snapshot, fingerprint, text_report
from .volume import analyze_volume, section_rgba, clip_volume, surface_mesh

_simulation=False
PALETTE=[('Material',(0.62,0.69,0.75,1)),('Ueberhang',(1,0.47,0.05,1)),
         ('Schwebend',(1,0.06,0.08,1)),('Harzfalle',(0.8,0.08,0.95,1)),
         ('Saugrisiko',(0.08,0.4,1,1)),('Kontakt',(0.1,0.9,0.4,1))]
HEAT=[('0',(0.1,0.2,0.7,1)),('1',(0.05,0.7,0.9,1)),('2',(0.1,0.85,0.4,1)),
      ('3',(0.9,0.85,0.1,1)),('4',(1,0.4,0.05,1)),('5',(0.9,0.05,0.12,1))]


def signatures(context,objects):
    result=[]
    for obj in objects:
        mesh=snapshot(context,obj)
        try:result.append((obj.name,int(obj.as_pointer()),fingerprint(mesh)))
        finally:bpy.data.meshes.remove(mesh)
    return result


def install_analysis(context,solid,origin,objects):
    stop_simulation();clear_helpers()
    s=context.scene.resin_prep
    report,maps=analyze_volume(solid,origin,s.xy,s.dz)
    sources=signatures(context,objects)
    report.update(objects=[o.name for o in objects],xy_mm=s.xy,z_mm=s.dz,shape=list(solid.shape),
                  origin_mm=list(map(float,origin)),units=s.units,mm_per_blender_unit=mm_factor(context),
                  source_hashes={name:sha for name,_,sha in sources},
                  warning='Grobe Modell-Voranalyse. Endkontrolle der geslicten Druckdatei in UVtools erforderlich.')
    old_reference=_cache.get('reference')
    if old_reference is not None:
        old_grid,old_origin,old_xy,old_dz=old_reference
        if old_grid.shape!=solid.shape or not np.allclose(old_origin,origin) or old_xy!=s.xy or old_dz!=s.dz:
            _cache.pop('reference',None)
            s.compare_reference=False
    _cache['layers']=(solid,np.asarray(origin),s.xy,s.dz,report)
    _cache['maps']=maps;_cache['sources']=sources
    _cache['scene_pointer']=int(context.scene.as_pointer())
    _cache['analysis_factor']=mm_factor(context);_cache['analysis_units']=s.units
    _cache['session']=uuid.uuid4().hex
    issues=report['islands']+report['resin_traps']+report['suction_risks']
    _cache['issues']=sorted(issues,key=lambda r:(r['start_layer'],r['type'],r['label']))
    s.layer_index=min(s.layer_index,len(solid)-1);s.issue_index=1
    text_report('Resin Prep - Schichtbericht.json',report)
    s.layer_status=f"{len(report['islands'])} Inselgruppen | {len(report['resin_traps'])} Harzfallen | {len(report['suction_risks'])} Saugrisiken"
    return report


def guard(context):
    cached=_cache.get('layers')
    if cached is None:raise ValueError('Zuerst die ausgewaehlten Modelle als Schichten analysieren.')
    if _cache.get('scene_pointer')!=int(context.scene.as_pointer()):raise ValueError('Andere Szene: Schichten neu analysieren.')
    s=context.scene.resin_prep
    if s.units!=_cache.get('analysis_units') or not np.isclose(mm_factor(context),_cache.get('analysis_factor',0)):
        raise ValueError('Einheiten wurden geaendert: Schichten neu analysieren.')
    if s.xy!=cached[2] or s.dz!=cached[3]:raise ValueError('Rastermasse wurden geaendert: Schichten neu analysieren.')
    for name,pointer,sha in _cache.get('sources',[]):
        obj=bpy.data.objects.get(name)
        if obj is None or int(obj.as_pointer())!=pointer:raise ValueError('Analysequelle wurde entfernt oder umbenannt: neu analysieren.')
        mesh=snapshot(context,obj)
        try:
            if fingerprint(mesh)!=sha:raise ValueError('Quellmodell wurde veraendert: Schichten neu analysieren.')
        finally:bpy.data.meshes.remove(mesh)
    return cached


def filtered_issues(context):
    kind=context.scene.resin_prep.issue_kind
    return [r for r in _cache.get('issues',[]) if kind=='ALL' or r['type']==kind]


def current_issue(context):
    issues=filtered_issues(context)
    if not issues:raise ValueError('Keine Befunde fuer diesen Filter vorhanden.')
    index=min(context.scene.resin_prep.issue_index,len(issues))-1
    return issues[max(0,index)]


def update_image(context):
    solid,origin,xy,dz,report=_cache['layers'];s=context.scene.resin_prep
    index=min(s.layer_index,len(solid)-1);s.layer_index=index
    reference=None
    if s.compare_reference:
        ref=_cache.get('reference')
        if ref is None:raise ValueError('Zuerst einen Vergleichsstand sichern.')
        reference=ref[0]
    pixels=section_rgba(solid,index,s.preview_mode,s.compare_offset,s.compare_split/100,reference)
    if s.show_image_grid:
        # Physical X/Y spacing, no printer resolution or aspect-ratio assumptions.
        h,w=solid[index].shape
        x=origin[0]+(np.arange(w)+0.5)*xy;y=origin[1]+(np.arange(h)+0.5)*xy
        columns=np.abs((x+s.grid_mm/2)%s.grid_mm-s.grid_mm/2)<xy*0.51
        rows=np.abs((y+s.grid_mm/2)%s.grid_mm-s.grid_mm/2)<xy*0.51
        pixels[:,columns,:3]=0.35*pixels[:,columns,:3]+0.65*np.array([0.15,0.7,0.8])
        pixels[rows,:,:3]=0.35*pixels[rows,:,:3]+0.65*np.array([0.15,0.7,0.8])
    h,w=solid[index].shape
    image=bpy.data.images.get('RP Schichtvorschau') or bpy.data.images.new('RP Schichtvorschau',width=w,height=h)
    image.scale(w,h);image.pixels.foreach_set(pixels.ravel());image.update()
    image['z_mm']=float(origin[2]+(index+0.5)*dz);image['xy_mm']=xy
    image['mode']=s.preview_mode;image['layer_index']=index
    image['physical_width_mm']=w*xy;image['physical_height_mm']=h*xy
    s.layer_status=f'Schicht {index}/{len(solid)-1}: Z={image["z_mm"]:.3f} mm | A={report["layer_curve"][index]["area_mm2"]:.2f} mm²'
    if context.screen:
        for area in context.screen.areas:
            if area.type=='IMAGE_EDITOR':area.spaces.active.image=image
    return image


def collection(context):
    uid=_cache.get('session','no-cache')
    for coll in bpy.data.collections:
        if coll.get('rp_session')==uid:return coll
    coll=bpy.data.collections.new('RP Analyse '+uid[:8]);coll['rp_session']=uid
    context.scene.collection.children.link(coll)
    return coll


def clear_helpers():
    uid=_cache.get('session')
    if uid is None:return
    for obj in list(bpy.data.objects):
        if obj.get('rp_session')==uid and obj.get('rp_helper'):
            data=obj.data;bpy.data.objects.remove(obj,do_unlink=True)
            if isinstance(data,bpy.types.Mesh) and data.users==0:bpy.data.meshes.remove(data)
    for coll in list(bpy.data.collections):
        if coll.get('rp_session')==uid and not coll.objects and not coll.children:
            bpy.data.collections.remove(coll)
    restore_view()


def restore_view():
    for space,xray,alpha,color_type in _cache.pop('view_settings',[]):
        try:
            space.shading.show_xray=xray;space.shading.xray_alpha=alpha
            space.shading.color_type=color_type
        except ReferenceError:pass
    for obj,was_hidden in _cache.pop('source_visibility',[]):
        try:
            obj.hide_set(was_hidden)
            if 'rp_preview_hidden_by' in obj:del obj['rp_preview_hidden_by']
            if 'rp_preview_previous_hidden' in obj:del obj['rp_preview_previous_hidden']
        except ReferenceError:pass


def materials(mode='ISSUES'):
    result=[]
    for name,color in (PALETTE if mode=='ISSUES' else HEAT):
        key='RP 0.2 '+mode+' '+name
        material=bpy.data.materials.get(key) or bpy.data.materials.new(key)
        material.diffuse_color=color;result.append(material)
    return result


def object_from_volume(context,name,grid,tags=None,helper=True,color_mode='ISSUES'):
    _,origin,xy,dz,_=_cache['layers'];s=context.scene.resin_prep
    vertices,faces,colors=surface_mesh(grid,origin,xy,dz,tags,max_faces=s.surface_limit)
    if not faces:raise ValueError('In diesem Schnitt ist keine Geometrie vorhanden.')
    factor=mm_factor(context)
    mesh=bpy.data.meshes.new(name);mesh.from_pydata([tuple(c/factor for c in p) for p in vertices],[],faces);mesh.update()
    obj=bpy.data.objects.new(name,mesh)
    (collection(context) if helper else context.collection).objects.link(obj)
    obj['rp_session']=_cache['session'];obj['rp_helper']=helper
    obj['rp_grid_xy_mm']=xy;obj['rp_grid_z_mm']=dz;obj['rp_reconstruction']=True
    obj['rp_provenance']=json.dumps(_cache['layers'][4]['source_hashes'])
    obj.hide_render=helper
    for material in materials(color_mode):mesh.materials.append(material)
    for polygon,color in zip(mesh.polygons,colors):polygon.material_index=max(0,min(int(color),5))
    return obj


def visible_grid(context,simulation=False):
    solid,origin,xy,dz,_=_cache['layers'];s=context.scene.resin_prep
    grid=clip_volume(solid,origin,xy,dz,s.clip_axis,s.clip_mode,s.clip_position,s.clip_slab)
    if simulation:grid[min(s.layer_index+1,len(grid)):]=False
    return grid


def create_3d(context,simulation=False):
    solid,origin,xy,dz,report=_cache['layers'];s=context.scene.resin_prep
    grid=visible_grid(context,simulation)
    if not grid.any():raise ValueError('Leerer Schnitt / leere Simulationsschicht. Andere Position waehlen.')
    tags=np.zeros_like(solid,dtype=np.uint8);maps=_cache['maps']
    if s.color_mode=='ISSUES':
        tags[maps['overhang']]=1;tags[maps['island']>0]=2
    else:
        for z in range(len(grid)):
            fraction=(z/max(1,len(grid)-1)) if s.color_mode=='HEIGHT' else report['layer_curve'][z]['area_risk_index']
            tags[z]=min(5,int(fraction*5))
    # Build replacement first, so a limit error does not erase the old preview.
    obj=object_from_volume(context,'RP Schichtmodell',grid,tags,color_mode=s.color_mode)
    for old in list(bpy.data.objects):
        if old!=obj and old.get('rp_session')==_cache['session'] and old.get('rp_preview'):
            mesh=old.data;bpy.data.objects.remove(old,do_unlink=True)
            if mesh.users==0:bpy.data.meshes.remove(mesh)
    obj['rp_preview']=True
    obj.display_type='WIRE' if s.preview_style=='WIRE' else 'SOLID'
    obj.show_in_front=False
    if s.solo_preview:
        visibility=_cache.setdefault('source_visibility',[])
        for name,pointer,_ in _cache.get('sources',[]):
            source=bpy.data.objects.get(name)
            if source is not None and int(source.as_pointer())==pointer:
                if not any(saved[0]==source for saved in visibility):visibility.append((source,source.hide_get()))
                if 'rp_preview_hidden_by' not in source:
                    source['rp_preview_hidden_by']=_cache['session']
                    source['rp_preview_previous_hidden']=source.hide_get()
                source.hide_set(True)
    else:
        for source,was_hidden in _cache.pop('source_visibility',[]):
            try:
                source.hide_set(was_hidden)
                if 'rp_preview_hidden_by' in source:del source['rp_preview_hidden_by']
                if 'rp_preview_previous_hidden' in source:del source['rp_preview_previous_hidden']
            except ReferenceError:pass
    # Only the invoking viewport's shading settings are touched, with restore.
    if context.area and context.area.type=='VIEW_3D':
        space=context.space_data
        existing=_cache.setdefault('view_settings',[])
        if not any(saved[0]==space for saved in existing):
            existing.append((space,space.shading.show_xray,space.shading.xray_alpha,space.shading.color_type))
        space.shading.color_type='MATERIAL';space.shading.show_xray=s.preview_style=='XRAY'
        if s.preview_style=='XRAY':space.shading.xray_alpha=0.3
    return obj


def create_issue_meshes(context):
    solid,origin,xy,dz,_=_cache['layers'];s=context.scene.resin_prep;maps=_cache['maps']
    made=[]
    for kind,color in [('island',2),('resin_trap',3),('suction',4)]:
        if kind!='island' and not s.show_cavities:continue
        grid=maps[kind]>0
        grid=clip_volume(grid,origin,xy,dz,s.clip_axis,s.clip_mode,s.clip_position,s.clip_slab)
        if not grid.any():continue
        tags=np.full(grid.shape,color,dtype=np.uint8)
        obj=object_from_volume(context,'RP Befund - '+kind,grid,tags)
        obj['rp_issue_overlay']=True;obj.show_in_front=True;obj.display_type='WIRE'
        made.append(obj)
    for old in list(bpy.data.objects):
        if old not in made and old.get('rp_session')==_cache['session'] and old.get('rp_issue_overlay'):
            mesh=old.data;bpy.data.objects.remove(old,do_unlink=True)
            if mesh.users==0:bpy.data.meshes.remove(mesh)
    return made


def focus_issue(context,issue):
    s=context.scene.resin_prep;s.layer_index=issue['start_layer']
    position=Vector(issue['position'])/mm_factor(context)
    context.scene.cursor.location=position
    coll=collection(context)
    obj=next((o for o in coll.objects if o.get('rp_focus')),None)
    if obj is None:
        obj=bpy.data.objects.new('RP Aktueller Befund',None);coll.objects.link(obj)
        obj['rp_helper']=True;obj['rp_session']=_cache['session'];obj['rp_focus']=True
        obj.empty_display_type='SPHERE';obj.show_in_front=True;obj.hide_render=True
    obj.location=position;obj.empty_display_size=max(_cache['layers'][2],1)/mm_factor(context)
    obj['rp_issue']=json.dumps(issue)
    if s.auto_focus and context.screen:
        for area in context.screen.areas:
            if area.type=='VIEW_3D':
                area.spaces.active.region_3d.view_location=position
    update_image(context)
    return obj


def uv_capabilities(ui):
    ui=Path(ui);cmd=ui.with_name('UVtoolsCmd.exe' if __import__('os').name=='nt' else 'UVtoolsCmd')
    args=[str(cmd)] if cmd.is_file() else [str(ui),'--cmd']
    key=tuple(args)+(ui.stat().st_mtime_ns,(cmd if cmd.is_file() else ui).stat().st_mtime_ns)
    cached=_cache.get('uv_capabilities')
    if cached is not None and cached[0]==key:return cached[1]
    proc=subprocess.run(args+['print-issues','--help'],capture_output=True,text=True,encoding='utf-8',errors='replace',
                        timeout=15,creationflags=subprocess.CREATE_NO_WINDOW if __import__('os').name=='nt' else 0)
    output=proc.stdout+proc.stderr
    if '--islands' not in output:raise ValueError('Die gewaehlte UVtools-CLI liefert keine bekannte print-issues-Schnittstelle.')
    result=dict(group_islands='--group-islands' in output,help=output,command=args)
    _cache['uv_capabilities']=(key,result)
    return result


def stop_simulation():
    global _simulation
    _simulation=False
    if bpy.app.timers.is_registered(simulation_tick):bpy.app.timers.unregister(simulation_tick)


@persistent
def load_reset(_):
    from . import __name__ as module_name
    import sys
    root=sys.modules[module_name]
    from .repair import cancel_preview
    cancel_preview()
    stop_simulation();restore_view()
    if root._uv_process is not None:
        root._uv_process.terminate();root._uv_process.wait(timeout=5);root._uv_process=None
    if root._uv_log is not None:root._uv_log.close();root._uv_log=None
    if bpy.app.timers.is_registered(root.uv_poll):bpy.app.timers.unregister(root.uv_poll)
    _cache.clear()


@persistent
def recover_visibility(_=None):
    """Restore visibility even when a .blend was saved with a solo preview."""
    for obj in bpy.data.objects:
        if 'rp_repair_hidden' in obj:
            for scene in bpy.data.scenes:
                for layer in scene.view_layers:
                    if obj.name in layer.objects:obj.hide_set(bool(obj.get('rp_repair_previous_hidden',False)),view_layer=layer)
            del obj['rp_repair_hidden']
            if 'rp_repair_previous_hidden' in obj:del obj['rp_repair_previous_hidden']
        if obj.get('rp_repair_preview'):
            for scene in bpy.data.scenes:
                for layer in scene.view_layers:
                    if obj.name in layer.objects:obj.hide_set(True,view_layer=layer)
        if 'rp_preview_hidden_by' in obj:
            for scene in bpy.data.scenes:
                for layer in scene.view_layers:
                    if obj.name in layer.objects:obj.hide_set(bool(obj.get('rp_preview_previous_hidden',False)),view_layer=layer)
            del obj['rp_preview_hidden_by']
            if 'rp_preview_previous_hidden' in obj:del obj['rp_preview_previous_hidden']
        if obj.get('rp_session') and obj.get('rp_helper') and obj.get('rp_reconstruction'):
            for scene in bpy.data.scenes:
                for layer in scene.view_layers:
                    if obj.name in layer.objects:obj.hide_set(True,view_layer=layer)


def simulation_tick():
    global _simulation
    if not _simulation:return None
    try:
        context=bpy.context;cached=guard(context);s=context.scene.resin_prep
        if s.layer_index>=len(cached[0])-1:_simulation=False;return None
        s.layer_index+=1;update_image(context)
        # Rebuild a bounded diagnostic mesh. No changes to source geometry.
        if cached[0][:s.layer_index+1].any():create_3d(context,True)
        return 1/s.simulation_fps
    except Exception as exc:
        _simulation=False
        if hasattr(bpy.context.scene,'resin_prep'):bpy.context.scene.resin_prep.layer_status='Simulation angehalten: '+str(exc)
        return None


class RP_OT_3d(bpy.types.Operator):
    bl_idname='resin_prep.inspect3d';bl_label='3D-Schichtmodell / Schnitt aktualisieren'
    bl_options={'REGISTER','UNDO'}
    def execute(self,context):
        try:guard(context);create_3d(context);return {'FINISHED'}
        except Exception as e:self.report({'ERROR'},str(e));return {'CANCELLED'}


class RP_OT_issues(bpy.types.Operator):
    bl_idname='resin_prep.issue_overlays';bl_label='Schichtfehler raeumlich markieren'
    bl_options={'REGISTER','UNDO'}
    def execute(self,context):
        try:guard(context);create_issue_meshes(context);return {'FINISHED'}
        except Exception as e:self.report({'ERROR'},str(e));return {'CANCELLED'}


class RP_OT_focus(bpy.types.Operator):
    bl_idname='resin_prep.issue_focus';bl_label='Befund anspringen / Cursor setzen'
    step:IntProperty(default=0,min=-1,max=1)
    def execute(self,context):
        try:
            guard(context);issues=filtered_issues(context)
            if not issues:raise ValueError('Keine Befunde fuer diesen Filter.')
            s=context.scene.resin_prep
            s.issue_index=((s.issue_index-1+self.step)%len(issues))+1
            issue=current_issue(context);focus_issue(context,issue)
            self.report({'INFO'},f"{s.issue_index}/{len(issues)}: {issue['type']}, Schichten {issue['start_layer']}–{issue['end_layer']}")
            return {'FINISHED'}
        except Exception as e:self.report({'ERROR'},str(e));return {'CANCELLED'}


class RP_OT_reference(bpy.types.Operator):
    bl_idname='resin_prep.reference';bl_label='Vergleichsstand sichern'
    def execute(self,context):
        try:
            solid,origin,xy,dz,_=guard(context)
            _cache['reference']=(solid.copy(),origin.copy(),xy,dz)
            self.report({'INFO'},'Raster als Vergleichsstand gesichert; bleibt nur bei identischem Rasterursprung und Aufloesung vergleichbar.')
            return {'FINISHED'}
        except Exception as e:self.report({'ERROR'},str(e));return {'CANCELLED'}


class RP_OT_step(bpy.types.Operator):
    bl_idname='resin_prep.layer_step';bl_label='Schicht wechseln'
    delta:IntProperty(default=1)
    def execute(self,context):
        try:
            solid,*_=guard(context);s=context.scene.resin_prep
            s.layer_index=max(0,min(s.layer_index+self.delta,len(solid)-1));update_image(context)
            return {'FINISHED'}
        except Exception as e:self.report({'ERROR'},str(e));return {'CANCELLED'}


class RP_OT_simulate(bpy.types.Operator):
    bl_idname='resin_prep.simulate';bl_label='Druckaufbau abspielen / stoppen'
    def execute(self,context):
        global _simulation
        if _simulation:stop_simulation();return {'FINISHED'}
        try:
            solid,*_=guard(context)
            s=context.scene.resin_prep
            if s.layer_index>=len(solid)-1:s.layer_index=0
            _simulation=True;bpy.app.timers.register(simulation_tick,first_interval=0.05)
            return {'FINISHED'}
        except Exception as e:self.report({'ERROR'},str(e));return {'CANCELLED'}


class RP_OT_reconstruct(bpy.types.Operator):
    bl_idname='resin_prep.reconstruct';bl_label='Raster / Schnitt als Mesh-Kopie'
    bl_description='Erstellt treppenfoermige Geometrie in Diagnoseaufloesung. Danach ausgewaehlt als STL exportierbar'
    bl_options={'REGISTER','UNDO'}
    def execute(self,context):
        try:
            guard(context);obj=object_from_volume(context,'RP Rekonstruktion - Rasterauflösung',visible_grid(context),helper=False)
            for selected in list(context.selected_objects):selected.select_set(False)
            obj.select_set(True);context.view_layer.objects.active=obj
            self.report({'WARNING'},'Raster-Mesh erstellt: grobe Diagnosegeometrie, Details koennen fehlen. Vor Druck pruefen.')
            return {'FINISHED'}
        except Exception as e:self.report({'ERROR'},str(e));return {'CANCELLED'}


class RP_OT_fill(bpy.types.Operator):
    bl_idname='resin_prep.fill_trap';bl_label='Aktuelle Harzfalle als Rasterkopie fuellen'
    bl_description='Fuellt nur die gewaehlte eingeschlossene Harzfalle in einer rekonstruierten Kopie; Original bleibt erhalten'
    bl_options={'REGISTER','UNDO'}
    def execute(self,context):
        try:
            solid,*_=guard(context);issue=current_issue(context)
            if issue['type']!='resin_trap':raise ValueError('Zum Fuellen eine Harzfalle waehlen. Saugrisiken besser gezielt entlueften.')
            grid=solid|(_cache['maps']['resin_trap']==issue['label'])
            obj=object_from_volume(context,'RP Harzfalle gefuellt - Rasterkopie',grid,helper=False)
            obj['rp_filled_issue']=issue['label']
            for selected in list(context.selected_objects):selected.select_set(False)
            obj.select_set(True);context.view_layer.objects.active=obj
            self.report({'WARNING'},'Gefuellte Rasterkopie erstellt. Original unveraendert; Kopie erneut analysieren.')
            return {'FINISHED'}
        except Exception as e:self.report({'ERROR'},str(e));return {'CANCELLED'}


class RP_OT_clear(bpy.types.Operator):
    bl_idname='resin_prep.clear_inspection';bl_label='Analyse-Hilfsobjekte entfernen'
    bl_options={'REGISTER','UNDO'}
    def execute(self,context):
        stop_simulation();clear_helpers();return {'FINISHED'}


class RP_OT_stats(bpy.types.Operator):
    bl_idname='resin_prep.stability_markers';bl_label='Schwerpunkt und Kontaktpolygon markieren'
    bl_options={'REGISTER','UNDO'}
    def execute(self,context):
        try:
            *_,report=guard(context);info=report['stability'];factor=mm_factor(context)
            coll=collection(context)
            for old in list(coll.objects):
                if old.get('rp_stats'):
                    data=old.data;bpy.data.objects.remove(old,do_unlink=True)
                    if isinstance(data,bpy.types.Mesh) and data.users==0:bpy.data.meshes.remove(data)
            if info['center_of_mass_mm'] is not None:
                marker=bpy.data.objects.new('RP Schwerpunkt',None);coll.objects.link(marker)
                marker.location=Vector(info['center_of_mass_mm'])/factor;marker.empty_display_type='PLAIN_AXES';marker.empty_display_size=3/factor
                marker['rp_session']=_cache['session'];marker['rp_helper']=True;marker['rp_stats']=True;marker.hide_render=True;marker.show_in_front=True
            hull=info['contact_hull_mm']
            if len(hull)>1:
                mesh=bpy.data.meshes.new('RP Kontaktpolygon');mesh.from_pydata([(x/factor,y/factor,0.01/factor) for x,y in hull],[(i,(i+1)%len(hull)) for i in range(len(hull))],[])
                marker=bpy.data.objects.new('RP Kontaktpolygon',mesh);coll.objects.link(marker)
                marker['rp_session']=_cache['session'];marker['rp_helper']=True;marker['rp_stats']=True;marker.hide_render=True;marker.show_in_front=True
            return {'FINISHED'}
        except Exception as e:self.report({'ERROR'},str(e));return {'CANCELLED'}


class RP_OT_uvcapabilities(bpy.types.Operator):
    bl_idname='resin_prep.uvcapabilities';bl_label='UVtools-Schnittstelle pruefen'
    def execute(self,context):
        try:
            s=context.scene.resin_prep;ui=Path(bpy.path.abspath(s.uv_exe))
            if not ui.is_file():raise ValueError('Vorhandenes UVtools-Programm waehlen.')
            info=uv_capabilities(ui)
            s.uv_status='Inselgruppierung verfuegbar' if info['group_islands'] else 'CLI ohne Inselgruppierung'
            text=bpy.data.texts.get('Resin Prep - UVtools Schnittstelle.txt') or bpy.data.texts.new('Resin Prep - UVtools Schnittstelle.txt')
            text.clear();text.write(info['help']);return {'FINISHED'}
        except Exception as e:self.report({'ERROR'},str(e));return {'CANCELLED'}


class RP_OT_reports(bpy.types.Operator):
    bl_idname='resin_prep.save_reports';bl_label='Detailberichte und Raster speichern'
    bl_description='Erstellt im Berichtsordner einen neuen Zeitstempelordner mit JSON, CSV, NPZ, PNG, HTML und UVtools-Protokoll'
    def execute(self,context):
        try:
            solid,origin,xy,dz,report=guard(context);s=context.scene.resin_prep
            if not s.report_folder:raise ValueError('Einen Berichtsordner waehlen.')
            root=Path(bpy.path.abspath(s.report_folder))
            root.mkdir(parents=True,exist_ok=True)
            name=datetime.now(timezone.utc).strftime('Analyse-%Y%m%dT%H%M%SZ-')+uuid.uuid4().hex[:6]
            folder=root/name;folder.mkdir()
            (folder/'Schichtbericht.json').write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
            with open(folder/'Schichtkurve.csv','w',newline='',encoding='utf-8') as stream:
                writer=csv.DictWriter(stream,fieldnames=list(report['layer_curve'][0]));writer.writeheader();writer.writerows(report['layer_curve'])
            arrays={'solid':solid,'origin_mm':origin,'xy_mm':np.array(xy),'z_mm':np.array(dz)}
            arrays.update(_cache['maps']);np.savez_compressed(folder/'Diagnoseraster.npz',**arrays)
            image=update_image(context);image.filepath_raw=str(folder/'Schichtvorschau.png');image.file_format='PNG';image.save()
            for source,destination in [('Resin Prep - UVtools.txt','UVtools.txt')]:
                text=bpy.data.texts.get(source)
                if text:(folder/destination).write_text(text.as_string(),encoding='utf-8')
            model=_cache.get('mesh')
            if model and report['source_hashes'].get(model[0])==model[1]:
                (folder/'Modellbericht.json').write_text(json.dumps(model[2],indent=2,ensure_ascii=False),encoding='utf-8')
            repair=_cache.get('repair',{})
            if repair.get('report'):
                (folder/'Reparaturbericht.json').write_text(json.dumps(repair['report'],indent=2,ensure_ascii=False),encoding='utf-8')
            settings={p.identifier:getattr(s,p.identifier) for p in s.bl_rna.properties if p.identifier!='rna_type' and p.type in {'STRING','BOOLEAN','INT','FLOAT','ENUM'}}
            (folder/'Einstellungen.json').write_text(json.dumps(settings,indent=2,ensure_ascii=False),encoding='utf-8')
            write_summary_html(folder,report)
            _cache['last_report_folder']=str(folder);s.status='Detailberichte erfolgreich gespeichert.'
            self.report({'INFO'},str(folder));return {'FINISHED'}
        except Exception as e:self.report({'ERROR'},str(e));return {'CANCELLED'}


def write_summary_html(folder,report):
    import html
    points=report['layer_curve'];maximum=max(report['max_area_mm2'],1e-6)
    def poly(key):return ' '.join(f'{40+i/max(1,len(points)-1)*820:.2f},{260-p[key]/maximum*220:.2f}' for i,p in enumerate(points))
    svg='<svg viewBox="0 0 900 310" role="img" aria-label="Querschnitt und Saugrisiko nach Schicht"><path d="M40 30V260H870" fill="none" stroke="#97a9ba"/>'
    for key,color in [('area_mm2','#5acbbd'),('floating_area_mm2','#ff5566'),('suction_area_mm2','#6198ff')]:
        svg+=f'<polyline fill="none" stroke="{color}" stroke-width="2" points="{poly(key)}"/>'
    svg+='<text x="40" y="293" fill="#cbd5df">Schichtindex → | Grün: Materialfläche · Rot: schwebend · Blau: Saugrisiko</text></svg>'
    rows=''.join('<tr>'+''.join('<td>'+html.escape(str(issue[k]))+'</td>' for k in ('type','start_layer','end_layer','area_mm2','volume_ml'))+'</tr>' for issue in report['islands']+report['resin_traps']+report['suction_risks'])
    page='<!doctype html><html lang="de"><meta charset="utf-8"><title>Resin Prep – Analyse</title><style>body{font:16px/1.6 system-ui;background:#14202c;color:#e6edf4;max-width:1100px;margin:36px auto;padding:20px}svg{width:100%}td,th{padding:8px;border-bottom:1px solid #506070;text-align:left}a{color:#8ccaff}</style>'
    page+='<h1>Resin Prep 0.2 – Detailanalyse</h1><p>Grobe geometrische Vorprüfung; keine Druckfreigabe oder physikalische Kraftsimulation.</p>'
    page+=f'<p>Raster: {report["xy_mm"]} mm XY / {report["z_mm"]} mm Z · Material: {report["voxel_volume_ml"]:.4f} ml · Harzfallen: {report["trapped_volume_ml"]:.4f} ml</p>'+svg
    page+='<p>Alle Flächen in mm²; Volume in ml. Saugrisiko-Flächen können größer als die Materialfläche sein.</p><h2>Befunde</h2><table><tr><th>Typ</th><th>Start</th><th>Ende</th><th>Max. Fläche mm²</th><th>Volumen ml</th></tr>'+rows+'</table>'
    page+='<p><a href="Schichtbericht.json">Vollständiger Bericht</a> · <a href="Schichtkurve.csv">Schichtkurve</a> · <a href="Einstellungen.json">Einstellungen</a></p><h2>Schichtbild</h2><img style="max-width:100%;image-rendering:pixelated" src="Schichtvorschau.png" alt="Aktuelle Diagnoseschicht"></html>'
    (folder/'Analyse.html').write_text(page,encoding='utf-8')


class RP_PT_inspection(bpy.types.Panel):
    bl_label='5 - 3D-Schnitt und Schichtvergleich';bl_idname='RP_PT_inspection';bl_parent_id='RP_PT_main'
    bl_space_type='VIEW_3D';bl_region_type='UI';bl_category='Resin Prep'
    def draw(self,context):
        l=self.layout;s=context.scene.resin_prep
        l.prop(s,'layer_index')
        row=l.row(align=True);row.operator('resin_prep.layer_step',text='Zurueck').delta=-1;row.operator('resin_prep.layer_step',text='Weiter').delta=1
        l.prop(s,'preview_mode');l.prop(s,'compare_offset')
        if s.preview_mode=='SPLIT':l.prop(s,'compare_split')
        l.operator('resin_prep.reference');l.prop(s,'compare_reference')
        l.prop(s,'show_image_grid')
        if s.show_image_grid:l.prop(s,'grid_mm')
        l.operator('resin_prep.preview')
        l.separator();l.prop(s,'preview_style');l.prop(s,'color_mode');l.prop(s,'solo_preview')
        l.prop(s,'clip_mode');l.prop(s,'clip_axis')
        if s.clip_mode!='FULL':l.prop(s,'clip_position')
        if s.clip_mode=='SLAB':l.prop(s,'clip_slab')
        l.prop(s,'surface_limit');l.operator('resin_prep.inspect3d');l.operator('resin_prep.reconstruct')
        l.prop(s,'simulation_fps');l.operator('resin_prep.simulate',text='Simulation stoppen' if _simulation else 'Druckaufbau abspielen')
        l.operator('resin_prep.clear_inspection')


class RP_PT_issues(bpy.types.Panel):
    bl_label='6 - Befunde und Kontaktstabilitaet';bl_idname='RP_PT_issues';bl_parent_id='RP_PT_main'
    bl_space_type='VIEW_3D';bl_region_type='UI';bl_category='Resin Prep'
    bl_options={'DEFAULT_CLOSED'}
    def draw(self,context):
        l=self.layout;s=context.scene.resin_prep
        l.prop(s,'issue_kind');l.prop(s,'issue_index');l.prop(s,'auto_focus')
        row=l.row(align=True);row.operator('resin_prep.issue_focus',text='Voriger').step=-1;row.operator('resin_prep.issue_focus',text='Naechster').step=1
        l.operator('resin_prep.issue_focus');l.prop(s,'show_cavities');l.operator('resin_prep.issue_overlays')
        if _cache.get('layers'):
            report=_cache['layers'][4];info=report['stability']
            l.label(text=f"Kontakt: {info['contact_area_mm2']:.2f} mm²")
            l.label(text='Schwerpunktprojektion: '+('im Kontaktpolygon' if info['center_projection_in_contact_hull'] else 'AUSSERHALB / kein Kontakt'))
            l.label(text='Geometrischer Hinweis, kein Kraftnachweis')
        l.operator('resin_prep.stability_markers');l.operator('resin_prep.fill_trap')
        l.label(text='Bohrung: Befund-Cursor zur Wand versetzen,')
        l.label(text='dann Ablaufloch-Werkzeug verwenden.')


class RP_PT_reports(bpy.types.Panel):
    bl_label='7 - Detailberichte speichern';bl_idname='RP_PT_reports';bl_parent_id='RP_PT_main'
    bl_space_type='VIEW_3D';bl_region_type='UI';bl_category='Resin Prep'
    bl_options={'DEFAULT_CLOSED'}
    def draw(self,context):
        l=self.layout;s=context.scene.resin_prep
        l.prop(s,'report_folder');l.operator('resin_prep.save_reports')
        l.label(text='HTML · JSON · CSV · NPZ · PNG')


classes=(RP_OT_3d,RP_OT_issues,RP_OT_focus,RP_OT_reference,RP_OT_step,RP_OT_simulate,
         RP_OT_reconstruct,RP_OT_fill,RP_OT_clear,RP_OT_stats,RP_OT_uvcapabilities,RP_OT_reports,
         RP_PT_inspection,RP_PT_issues,RP_PT_reports)
