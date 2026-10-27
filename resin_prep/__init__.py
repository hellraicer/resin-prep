"""Resin Prep: original Blender mesh preparation tools, GPL-3.0-or-later."""
import bpy
import bmesh
import math
import os
import json
import struct
import hashlib
import subprocess
import tempfile
import textwrap
from pathlib import Path
import numpy as np
from mathutils import Vector, Matrix, Euler
from mathutils.bvhtree import BVHTree
from bpy.props import (BoolProperty, EnumProperty, FloatProperty, IntProperty,
                       PointerProperty, StringProperty)
from bpy_extras.io_utils import ExportHelper
from .volume import diagnose, analyze_volume

bl_info = {"name": "Resin Prep", "author": "Resin Prep contributors", "version": (0, 3, 0),
           "blender": (4, 2, 0), "location": "3D View > Sidebar > Resin Prep",
           "description": "Resin model preparation, coarse layer checks and UVtools bridge",
           "category": "Mesh"}

_cache = {}
_uv_process = None
_uv_log = None


def wrapped(layout,text):
    for line in textwrap.wrap(text,width=36):layout.label(text=line)


def mm_factor(context):
    s = context.scene.resin_prep
    return 1.0 if s.units == 'MM' else context.scene.unit_settings.scale_length * 1000.0


def targets(context):
    return [o for o in context.selected_objects if o.type == 'MESH' and not o.get('rp_helper')]


def require_mesh(context):
    obj = context.active_object
    if context.mode != 'OBJECT' or not obj or obj.type != 'MESH' or obj.get('rp_helper'):
        raise ValueError('Im Objektmodus ein Druckmodell als aktives Mesh waehlen.')
    return obj


def snapshot(context, obj):
    depsgraph = context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(depsgraph)
    mesh = bpy.data.meshes.new_from_object(evaluated, depsgraph=depsgraph)
    transform = Matrix.Scale(mm_factor(context), 4) @ obj.matrix_world
    mesh.transform(transform)
    if transform.to_3x3().determinant() < 0:
        mesh.flip_normals()
    mesh.update()
    return mesh


def fingerprint(mesh):
    mesh.calc_loop_triangles()
    h = hashlib.sha256()
    for v in mesh.vertices:
        h.update(struct.pack('<3f', *v.co))
    for t in mesh.loop_triangles:
        h.update(struct.pack('<3I', *t.vertices))
    return h.hexdigest()


def tree_from_mesh(mesh):
    mesh.calc_loop_triangles()
    if not mesh.loop_triangles:
        raise ValueError('Das Mesh enthaelt keine Flaechen.')
    verts = [v.co.copy() for v in mesh.vertices]
    tris = [tuple(t.vertices) for t in mesh.loop_triangles]
    return BVHTree.FromPolygons(verts, tris, all_triangles=True), verts, tris


def mesh_checks(context, mesh):
    s = context.scene.resin_prep
    bm = bmesh.new()
    bm.from_mesh(mesh)
    bm.normal_update()
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()
    issues = {k: set() for k in ('open', 'winding', 'degenerate', 'distorted', 'sharp', 'intersections', 'thin', 'overhang')}
    bad_edges = [e for e in bm.edges if not e.is_manifold]
    for e in bad_edges:
        issues['open'].update(f.index for f in e.link_faces)
    for e in bm.edges:
        if e.is_manifold and not e.is_contiguous:
            issues['winding'].update(f.index for f in e.link_faces)
        if e.is_manifold and e.calc_face_angle() > math.radians(150):
            issues['sharp'].update(f.index for f in e.link_faces)
    for f in bm.faces:
        if f.calc_area() < 1e-10:
            issues['degenerate'].add(f.index)
        if len(f.verts)>3:
            center=f.calc_center_median()
            if any(abs((v.co-center).dot(f.normal))>0.05 for v in f.verts):
                issues['distorted'].add(f.index)
        if f.normal.z < -math.cos(math.radians(s.overhang)) and min(v.co.z for v in f.verts) > 0.05:
            issues['overhang'].add(f.index)
    unvisited = set(bm.verts)
    shells = 0
    while unvisited:
        shells += 1
        stack = [unvisited.pop()]
        while stack:
            v = stack.pop()
            for edge in v.link_edges:
                other = edge.other_vert(v)
                if other in unvisited:
                    unvisited.remove(other)
                    stack.append(other)
    tree, verts, tris = tree_from_mesh(mesh)
    intersection_checked = len(tris) <= s.intersection_limit
    if intersection_checked:
        for a, b in tree.overlap(tree):
            if a < b and not set(tris[a]).intersection(tris[b]):
                issues['intersections'].add(mesh.loop_triangles[a].polygon_index)
                issues['intersections'].add(mesh.loop_triangles[b].polygon_index)
    stride = max(1, math.ceil(len(mesh.polygons)/s.sample_limit))
    samples = 0
    for face in mesh.polygons[::stride]:
        n = face.normal
        if n.length < 0.5:
            continue
        samples += 1
        hit, normal, index, distance = tree.ray_cast(face.center - n*0.0001, -n, s.min_wall)
        if hit is not None and index is not None:
            # An inward ray must exit the far surface, not strike a neighboring fold.
            if normal.dot(n) < -0.1 and distance + 0.0001 < s.min_wall:
                issues['thin'].add(face.index)
    dimensions = []
    mins, maxs = [], []
    for axis in range(3):
        coords = [v.co[axis] for v in mesh.vertices]
        mins.append(min(coords)); maxs.append(max(coords))
        dimensions.append(max(coords)-min(coords))
    within = (mins[0] >= -s.bed_x/2 and maxs[0] <= s.bed_x/2 and
              mins[1] >= -s.bed_y/2 and maxs[1] <= s.bed_y/2 and
              mins[2] >= -0.001 and maxs[2] <= s.bed_z)
    volume = abs(bm.calc_volume(signed=True))/1000
    result = dict(dimensions_mm=dimensions, bounds_min_mm=mins, bounds_max_mm=maxs,
                  in_build_volume=within, nonmanifold_edges=len(bad_edges),
                  loose_vertices=sum(not v.link_edges for v in bm.verts),
                  loose_edges=sum(not e.link_faces for e in bm.edges),
                  zero_edges=sum(e.calc_length() < 1e-6 for e in bm.edges),
                  shells=shells, faces=len(mesh.polygons), triangles=len(tris),
                  volume_ml=volume, volume_reliable=not bad_edges and not issues['winding']
                  and not issues['intersections'] and intersection_checked,
                  surface_mm2=sum(f.calc_area() for f in bm.faces),
                  mass_g=volume*s.density, cost=volume*s.density/1000*s.price,
                  thickness_samples=samples, thickness_stride=stride,
                  intersections_checked=intersection_checked,
                  issues={k: sorted(v) for k, v in issues.items()})
    bm.free()
    return result


def text_report(name, data):
    text = bpy.data.texts.get(name) or bpy.data.texts.new(name)
    text.clear()
    text.write(json.dumps(data, indent=2, ensure_ascii=False))
    return text


def make_copy(context, obj, suffix):
    mesh = snapshot(context, obj)
    mesh.transform(Matrix.Scale(1/mm_factor(context), 4))
    new = bpy.data.objects.new(obj.name + suffix, mesh)
    context.collection.objects.link(new)
    for selected in list(context.selected_objects):
        selected.select_set(False)
    new.select_set(True)
    context.view_layer.objects.active = new
    return new


def finish_copy(context, original, new):
    original.hide_set(True)
    new['rp_source'] = original.name
    context.scene.resin_prep.status = 'Kopie erstellt: ' + new.name


def remove_copy(obj):
    mesh = obj.data
    bpy.data.objects.remove(obj, do_unlink=True)
    if mesh.users == 0:
        bpy.data.meshes.remove(mesh)


class RP_Settings(bpy.types.PropertyGroup):
    units: EnumProperty(name='Einheiten', items=[('MM', '1 Blender-Einheit = 1 mm', 'Typisch fuer STL ohne Einheiten'),
                      ('SCENE', 'Szeneneinheiten verwenden', 'scale_length mal 1000 mm')], default='MM')
    bed_x: FloatProperty(name='Bauraum X (mm)', default=211.68, min=1)
    bed_y: FloatProperty(name='Bauraum Y (mm)', default=118.37, min=1)
    bed_z: FloatProperty(name='Bauraum Z (mm)', default=220, min=1)
    min_wall: FloatProperty(name='Min. Wand (mm)', default=1.5, min=0.01)
    wall: FloatProperty(name='Hohlwand (mm)', default=2, min=0.1)
    overhang: FloatProperty(name='Ueberhang zu -Z (Grad)', default=45, min=1, max=89)
    sample_limit: IntProperty(name='Wand-Messpunkte max.', default=10000, min=100, max=100000)
    intersection_limit: IntProperty(name='Schnitt-Test max. Dreiecke', default=100000, min=1000, max=500000)
    merge: FloatProperty(name='Verschmelzen (mm)', default=0.005, min=0.00001)
    hole: FloatProperty(name='Lochdurchmesser (mm)', default=4, min=0.2)
    hole_depth: FloatProperty(name='Bohrlaenge (mm)', default=10, min=0.5)
    hole_axis: EnumProperty(name='Bohr-Richtung', items=[('X','X','Welt-X'),('Y','Y','Welt-Y'),('Z','Z','Welt-Z')], default='Z')
    lift: FloatProperty(name='Abstand zur Platte (mm)', default=5, min=0)
    angle_x: FloatProperty(name='Drehung X (Grad)', default=30, min=-180, max=180)
    angle_y: FloatProperty(name='Drehung Y (Grad)', default=0, min=-180, max=180)
    angle_z: FloatProperty(name='Drehung Z (Grad)', default=0, min=-180, max=180)
    tip: FloatProperty(name='Support-Spitze D (mm)', default=0.4, min=0.1)
    shaft: FloatProperty(name='Support-Schaft D (mm)', default=1.2, min=0.2)
    foot: FloatProperty(name='Support-Fuss D (mm)', default=5, min=0.5)
    auto_count: IntProperty(name='Support-Vorschlaege max.', default=80, min=1, max=300)
    support_spacing: FloatProperty(name='Support-Abstand (mm)', default=3, min=0.5)
    xy: FloatProperty(name='Analyse XY (mm)', default=0.75, min=0.05, max=5)
    dz: FloatProperty(name='Analyse Z (mm)', default=0.5, min=0.01, max=5)
    voxel_limit: IntProperty(name='Zellenlimit', default=1500000, min=10000, max=5000000)
    layer_index: IntProperty(name='Vorschauschicht', default=0, min=0)
    preview_mode: EnumProperty(name='Schichtansicht',items=[('PLAIN','Material','Weiss = Material'),('DIFFERENCE','Differenz','Gruen = neu, rot = entfernt, grau = gleich'),('ONION','7 Schichten ueberlagern','Bis zu drei davor und danach'),('SPLIT','Geteilter Vergleich','Links aktuelle Schicht, rechts Vergleich')],default='PLAIN')
    compare_offset: IntProperty(name='Vergleich: Schichtversatz',default=-1,min=-100,max=100)
    compare_reference: BoolProperty(name='Mit gesichertem Stand vergleichen',default=False)
    compare_split: FloatProperty(name='Trennlinie (%)',default=50,min=0,max=100)
    show_image_grid: BoolProperty(name='Millimeter-Raster im Bild',default=False)
    grid_mm: FloatProperty(name='Rasterlinien-Abstand (mm)',default=5,min=0.1)
    preview_style: EnumProperty(name='3D-Darstellung',items=[('SOLID','Solid','Farbige Oberflaechen'),('XRAY','Durchsicht / X-Ray','Blender-X-Ray, wirkt auf den aktuellen Viewport'),('WIRE','Drahtgitter','Kantenansicht')],default='SOLID')
    color_mode: EnumProperty(name='3D-Farben',items=[('ISSUES','Fehler / Ueberhang','Rot: schwebend; orange: Ueberhang; violett: Harzfalle; blau: Saugrisiko'),('HEIGHT','Hoehenzonen','Farbbereiche nach Z'),('AREA','Querschnittsindex','Relativ zur groessten Schicht, keine Newton-Werte')],default='ISSUES')
    clip_mode: EnumProperty(name='Schnittmodus',items=[('FULL','Ganzes Raster','Vollstaendig'),('BELOW','Unterhalb Schnitt','Kleinere Koordinaten behalten'),('ABOVE','Oberhalb Schnitt','Groessere Koordinaten behalten'),('SLAB','Scheibe','Bereich um die Schnittebene')],default='FULL')
    clip_axis: EnumProperty(name='Schnittachse',items=[('X','X','X'),('Y','Y','Y'),('Z','Z','Z')],default='Z')
    clip_position: FloatProperty(name='Schnittposition (%)',default=50,min=0,max=100)
    clip_slab: FloatProperty(name='Scheibenbreite (%)',default=10,min=0.1,max=100)
    show_cavities: BoolProperty(name='Harzfallen / Saugrisiken anzeigen',default=True)
    solo_preview: BoolProperty(name='Quellmodelle fuer 3D-Vorschau ausblenden',default=True)
    surface_limit: IntProperty(name='3D-Flaechenlimit',default=250000,min=1000,max=500000)
    issue_index: IntProperty(name='Befundnummer (ab 1)',default=1,min=1)
    issue_kind: EnumProperty(name='Befundfilter',items=[('ALL','Alle',''),('island','Inseln',''),('resin_trap','Harzfallen',''),('suction','Saugrisiken','')],default='ALL')
    auto_focus: BoolProperty(name='Beim Befund zentrieren',default=True)
    simulation_fps: IntProperty(name='Simulation: Schichten/s',default=8,min=1,max=30)
    uv_group_islands: BoolProperty(name='UVtools: schwebende Teile gruppieren',default=True)
    uv_status: StringProperty(default='UVtools-Funktionen noch nicht geprueft.')
    report_folder: StringProperty(name='Berichtsordner',subtype='DIR_PATH')
    density: FloatProperty(name='Harzdichte (g/ml)', default=1.1, min=0.1)
    price: FloatProperty(name='Harzpreis pro kg', default=30, min=0)
    uv_exe: StringProperty(name='UVtools Programm', subtype='FILE_PATH', default='C:\\Program Files\\UVtools\\UVtools.exe' if os.name=='nt' else '')
    sliced_file: StringProperty(name='Geslicte Druckdatei', subtype='FILE_PATH')
    status: StringProperty(default='Modell waehlen, Einheiten kontrollieren, pruefen.')
    layer_status: StringProperty(default='Noch keine Schichtanalyse.')


class RP_OT_analyze(bpy.types.Operator):
    bl_idname = 'resin_prep.analyze'
    bl_label = 'Modell pruefen'
    bl_description = 'Aktives ausgewertetes Mesh in Weltkoordinaten pruefen; Bericht im Texteditor'
    def execute(self, context):
        mesh = None
        try:
            obj = require_mesh(context)
            mesh = snapshot(context, obj)
            result = mesh_checks(context, mesh)
            result['object'] = obj.name
            result['units'] = context.scene.resin_prep.units
            result['notes'] = ['Wandstaerke: Strahlen-Stichprobe, keine vollstaendige Garantie.',
                               'Ueberhaenge sind geometrische Hinweise, keine Support-Freigabe.',
                               'Schnitt-Test ignoriert Dreiecke mit gemeinsamen Vertices.',
                               'Volumen kann bei verschachtelten oder ueberlappenden Schalen ungenau sein.']
            _cache['mesh'] = (obj.name, fingerprint(mesh), result)
            text_report('Resin Prep - Modellbericht.json', result)
            s = context.scene.resin_prep
            s.status = f"{result['volume_ml']:.2f} ml | {result['shells']} Schalen | offene Kanten: {result['nonmanifold_edges']}"
            self.report({'INFO'}, 'Pruefung fertig. Details: Texteditor > Resin Prep - Modellbericht.json')
            return {'FINISHED'}
        except Exception as e:
            self.report({'ERROR'}, str(e)); return {'CANCELLED'}
        finally:
            if mesh: bpy.data.meshes.remove(mesh)


class RP_OT_highlight(bpy.types.Operator):
    bl_idname = 'resin_prep.highlight'
    bl_label = 'Fundstellen anzeigen'
    bl_options = {'REGISTER', 'UNDO'}
    kind: StringProperty()
    def execute(self, context):
        mesh = None
        try:
            obj = require_mesh(context)
            mesh = snapshot(context, obj)
            cached = _cache.get('mesh')
            if not cached or cached[0] != obj.name or cached[1] != fingerprint(mesh):
                raise ValueError('Modell geaendert oder anderer Benutzerkontext: zuerst erneut pruefen.')
            indices = cached[2]['issues'][self.kind]
            if not indices:
                raise ValueError('Keine Fundstellen dieser Kategorie.')
            verts, faces = [], []
            for index in indices:
                f = mesh.polygons[index]
                start = len(verts)
                verts.extend([mesh.vertices[i].co/mm_factor(context) for i in f.vertices])
                faces.append(tuple(range(start, len(verts))))
            overlay = bpy.data.meshes.new('RP Fundstellen')
            overlay.from_pydata(verts, [], faces)
            marker = bpy.data.objects.new('RP Hinweis - '+self.kind, overlay)
            context.collection.objects.link(marker)
            marker['rp_helper'] = True
            marker.hide_render = True
            marker.show_in_front = True
            marker.display_type = 'WIRE'
            self.report({'INFO'}, 'Fundstellen als Drahtgitter-Hilfsobjekt erstellt.')
            return {'FINISHED'}
        except Exception as e:
            self.report({'ERROR'}, str(e)); return {'CANCELLED'}
        finally:
            if mesh: bpy.data.meshes.remove(mesh)


class RP_OT_repair(bpy.types.Operator):
    bl_idname = 'resin_prep.repair'
    bl_label = 'Bereinigte Kopie'
    bl_description = 'Doppelte Punkte und lose Geometrie entfernen; Normalenrichtung und offene Loecher beibehalten'
    bl_options = {'REGISTER', 'UNDO'}
    def execute(self, context):
        new = None
        try:
            original = require_mesh(context)
            new = make_copy(context, original, ' - bereinigt')
            bm = bmesh.new(); bm.from_mesh(new.data)
            bmesh.ops.remove_doubles(bm, verts=list(bm.verts), dist=context.scene.resin_prep.merge/mm_factor(context))
            bmesh.ops.dissolve_degenerate(bm, edges=list(bm.edges), dist=1e-6/mm_factor(context))
            loose = [v for v in bm.verts if not v.link_faces]
            if loose: bmesh.ops.delete(bm, geom=loose, context='VERTS')
            bm.to_mesh(new.data); bm.free(); new.data.update()
            finish_copy(context, original, new)
            self.report({'INFO'}, 'Bereinigte Kopie erstellt; Normalenrichtung bleibt erhalten. Erneut pruefen.')
            return {'FINISHED'}
        except Exception as e:
            if new: remove_copy(new)
            self.report({'ERROR'}, str(e)); return {'CANCELLED'}


class RP_OT_hollow(bpy.types.Operator):
    bl_idname = 'resin_prep.hollow'
    bl_label = 'Hohle Kopie erstellen'
    bl_description = 'Solidify-Schale nach innen erzeugen. Enge Stellen koennen sich ueberschneiden; danach pruefen'
    bl_options = {'REGISTER', 'UNDO'}
    def execute(self, context):
        new = None
        try:
            original = require_mesh(context)
            new = make_copy(context, original, ' - hohl')
            bm = bmesh.new(); bm.from_mesh(new.data)
            closed = all(e.is_manifold for e in bm.edges) and len(bm.faces)>0
            if not closed:
                bm.free(); raise ValueError('Aushöhlen braucht ein geschlossenes Mesh. Zuerst Modell pruefen.')
            pending=set(bm.verts); shells=0
            while pending:
                shells+=1; todo=[pending.pop()]
                while todo:
                    v=todo.pop()
                    for edge in v.link_edges:
                        other=edge.other_vert(v)
                        if other in pending: pending.remove(other); todo.append(other)
            if shells != 1:
                bm.free(); raise ValueError('Aushöhlen erwartet einen einzelnen Vollkoerper. Schalen trennen oder bereits hohles Modell verwenden.')
            # A closed source solid is expected, not an already hollow shell.
            bmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))
            bm.to_mesh(new.data); bm.free()
            mod = new.modifiers.new('RP Innenwand', 'SOLIDIFY')
            mod.thickness = context.scene.resin_prep.wall/mm_factor(context)
            mod.offset = -1
            mod.use_even_offset = True
            mod.use_quality_normals = True
            mod.thickness_clamp = 0
            bpy.ops.object.modifier_apply(modifier=mod.name)
            finish_copy(context, original, new)
            self.report({'WARNING'}, 'Hohle Kopie erstellt: Ablauflöcher setzen und Wandstaerke/Selbstschnitte pruefen.')
            return {'FINISHED'}
        except Exception as e:
            if new: remove_copy(new)
            self.report({'ERROR'}, str(e)); return {'CANCELLED'}


class RP_OT_drain(bpy.types.Operator):
    bl_idname = 'resin_prep.drain'
    bl_label = 'Ablaufloch am 3D-Cursor'
    bl_description = 'Bohrt entlang gewaehlter Weltachse, mittig am 3D-Cursor. Cursor auf die Wand setzen'
    bl_options = {'REGISTER', 'UNDO'}
    def execute(self, context):
        new = cutter = None
        try:
            original = require_mesh(context)
            s = context.scene.resin_prep; factor = mm_factor(context)
            new = make_copy(context, original, ' - gebohrt')
            direction = Vector({'X':(1,0,0),'Y':(0,1,0),'Z':(0,0,1)}[s.hole_axis])
            bpy.ops.mesh.primitive_cylinder_add(vertices=64, radius=s.hole/2/factor,
                                              depth=s.hole_depth/factor, location=context.scene.cursor.location)
            cutter = context.object
            cutter.rotation_euler = Vector((0,0,1)).rotation_difference(direction).to_euler()
            mod = new.modifiers.new('RP Ablaufloch', 'BOOLEAN')
            mod.operation = 'DIFFERENCE'; mod.solver = 'EXACT'; mod.object = cutter
            cutter.select_set(False); new.select_set(True)
            context.view_layer.objects.active = new
            bpy.ops.object.modifier_apply(modifier=mod.name)
            remove_copy(cutter); cutter = None
            finish_copy(context, original, new)
            self.report({'INFO'}, 'Bohrung auf Kopie angewendet. Durchgang in der Schichtvorschau pruefen.')
            return {'FINISHED'}
        except Exception as e:
            if cutter: remove_copy(cutter)
            if new: remove_copy(new)
            self.report({'ERROR'}, str(e)); return {'CANCELLED'}


class RP_OT_bed(bpy.types.Operator):
    bl_idname = 'resin_prep.bed'
    bl_label = 'Bauraum anzeigen'
    bl_options = {'REGISTER', 'UNDO'}
    def execute(self, context):
        s = context.scene.resin_prep; f = mm_factor(context)
        mesh = bpy.data.meshes.new('RP Bauraum')
        verts = [(x*s.bed_x/2/f,y*s.bed_y/2/f,z*s.bed_z/f)
                 for z in (0,1) for y in (-1,1) for x in (-1,1)]
        edges = [(0,1),(0,2),(1,3),(2,3),(4,5),(4,6),(5,7),(6,7),(0,4),(1,5),(2,6),(3,7)]
        mesh.from_pydata(verts, edges, [])
        obj = bpy.data.objects.new('RP Bauraum', mesh); context.collection.objects.link(obj)
        obj['rp_helper'] = True; obj.hide_render = True; obj.hide_select = True
        return {'FINISHED'}


class RP_OT_profile(bpy.types.Operator):
    bl_idname = 'resin_prep.profile'
    bl_label = 'Saturn 4 Ultra 16K'
    bl_options = {'REGISTER', 'UNDO'}
    def execute(self, context):
        s = context.scene.resin_prep
        s.bed_x, s.bed_y, s.bed_z = 211.68, 118.37, 220
        return {'FINISHED'}


class RP_OT_place(bpy.types.Operator):
    bl_idname = 'resin_prep.place'
    bl_label = 'Auswahl zentrieren / anheben'
    bl_description = 'Alle ausgewaehlten Druckmeshes als Gruppe zentrieren; Abstand zur Platte einstellen'
    bl_options = {'REGISTER', 'UNDO'}
    def execute(self, context):
        try:
            require_mesh(context)
            objs = targets(context)
            corners = []
            for obj in objs:
                mesh = snapshot(context,obj)
                corners.extend(v.co.copy() for v in mesh.vertices)
                bpy.data.meshes.remove(mesh)
            if not corners: raise ValueError('Keine Geometrie ausgewaehlt.')
            lo = Vector(tuple(min(p[a] for p in corners) for a in range(3)))
            hi = Vector(tuple(max(p[a] for p in corners) for a in range(3)))
            delta = Vector((-(lo.x+hi.x)/2, -(lo.y+hi.y)/2, context.scene.resin_prep.lift-lo.z))/mm_factor(context)
            # Set world matrices from a captured state; parent-child selection is safe.
            matrices = [(o, o.matrix_world.copy()) for o in objs]
            def depth(o):
                n=0
                while o.parent: n+=1; o=o.parent
                return n
            for obj, matrix in sorted(matrices, key=lambda item:depth(item[0])):
                matrix.translation += delta; obj.matrix_world = matrix
            return {'FINISHED'}
        except Exception as e:
            self.report({'ERROR'},str(e)); return {'CANCELLED'}


class RP_OT_orient(bpy.types.Operator):
    bl_idname='resin_prep.orient'
    bl_label='Gedrehte Kopie erstellen'
    bl_description='Dreht eine Kopie relativ zur aktuellen Weltlage, zentriert sie und setzt den Plattenabstand'
    bl_options={'REGISTER','UNDO'}
    def execute(self,context):
        new=None
        try:
            original=require_mesh(context);s=context.scene.resin_prep
            new=make_copy(context,original,' - ausgerichtet')
            verts=new.data.vertices
            center=Vector(tuple((min(v.co[a] for v in verts)+max(v.co[a] for v in verts))/2 for a in range(3)))
            rotation=Euler(tuple(math.radians(a) for a in (s.angle_x,s.angle_y,s.angle_z)),'XYZ').to_matrix().to_4x4()
            new.data.transform(Matrix.Translation(center)@rotation@Matrix.Translation(-center))
            lo=Vector(tuple(min(v.co[a] for v in verts) for a in range(3)))
            hi=Vector(tuple(max(v.co[a] for v in verts) for a in range(3)))
            delta=Vector((-(lo.x+hi.x)/2,-(lo.y+hi.y)/2,s.lift/mm_factor(context)-lo.z))
            new.data.transform(Matrix.Translation(delta));new.data.update()
            finish_copy(context,original,new)
            return {'FINISHED'}
        except Exception as e:
            if new: remove_copy(new)
            self.report({'ERROR'},str(e));return {'CANCELLED'}


def add_support(context, top):
    """Straight support to plate. Caller must ray-test its free path."""
    s = context.scene.resin_prep; f = mm_factor(context)
    z = top.z
    if z < 1.5: raise ValueError('Kontaktpunkt muss mindestens 1.5 mm ueber der Platte liegen.')
    if s.tip > s.shaft or s.shaft > s.foot:
        raise ValueError('Support-Durchmesser muessen Spitze <= Schaft <= Fuss sein.')
    verts, faces = [], []
    # Slight penetration into the model creates an actual joined contact in a slicer.
    rings = [(0,s.foot/2),(0.5,s.foot/2),(0.8,s.shaft/2),
             (max(0.9,z-1),s.shaft/2),(z+0.15,s.tip/2)]
    n=16
    for height, radius in rings:
        verts.extend([( (top.x+math.cos(i*2*math.pi/n)*radius)/f,
                        (top.y+math.sin(i*2*math.pi/n)*radius)/f, height/f) for i in range(n)])
    faces.append(tuple(reversed(range(n))))
    for ring in range(len(rings)-1):
        for i in range(n):
            j=(i+1)%n; a=ring*n; b=(ring+1)*n
            faces.append((a+i,a+j,b+j,b+i))
    faces.append(tuple(range((len(rings)-1)*n,len(rings)*n)))
    mesh=bpy.data.meshes.new('RP Support'); mesh.from_pydata(verts,[],faces); mesh.update()
    obj=bpy.data.objects.new('RP Support - pruefen',mesh); context.collection.objects.link(obj)
    obj['rp_support']=True
    return obj


class RP_OT_support(bpy.types.Operator):
    bl_idname='resin_prep.support'
    bl_label='Support unter Cursor'
    bl_description='Sucht die erste Modellunterseite ueber der Cursorposition und baut einen geraden Plattensupport'
    bl_options={'REGISTER','UNDO'}
    def execute(self,context):
        mesh=None
        try:
            obj=require_mesh(context); mesh=snapshot(context,obj)
            tree,_,_=tree_from_mesh(mesh)
            cursor=context.scene.cursor.location*mm_factor(context)
            # A plate support is valid only for the first surface above the plate.
            hit,normal,_,_=tree.ray_cast(Vector((cursor.x,cursor.y,0.001)),Vector((0,0,1)),10000)
            if hit is None or normal.z>=0:
                raise ValueError('Keine nach unten zeigende Unterseite oberhalb der Platte an Cursor-XY gefunden.')
            add_support(context,hit)
            self.report({'INFO'},'Support erstellt. Kontakt und Stabilitaet im Slicer pruefen.')
            return {'FINISHED'}
        except Exception as e:
            self.report({'ERROR'},str(e)); return {'CANCELLED'}
        finally:
            if mesh: bpy.data.meshes.remove(mesh)


class RP_OT_autosupport(bpy.types.Operator):
    bl_idname='resin_prep.autosupport'
    bl_label='Einfache Support-Vorschlaege'
    bl_description='Raster unter der ersten sichtbaren Unterseite. Keine vollstaendige Inselabdeckung, keine inneren Supports'
    bl_options={'REGISTER','UNDO'}
    def execute(self,context):
        mesh=None
        try:
            obj=require_mesh(context); mesh=snapshot(context,obj)
            tree,verts,_=tree_from_mesh(mesh); s=context.scene.resin_prep
            if s.tip>s.shaft or s.shaft>s.foot: raise ValueError('Spitze <= Schaft <= Fuss einstellen.')
            lo=Vector(tuple(min(v[a] for v in verts) for a in range(3)))
            hi=Vector(tuple(max(v[a] for v in verts) for a in range(3)))
            nx=max(1,math.ceil((hi.x-lo.x)/s.support_spacing))
            ny=max(1,math.ceil((hi.y-lo.y)/s.support_spacing))
            if nx*ny>100000: raise ValueError('Support-Raster zu gross; Abstand erhoehen.')
            points=[]
            for j in range(ny):
                for i in range(nx):
                    x=lo.x+(i+0.5)*(hi.x-lo.x)/nx
                    y=lo.y+(j+0.5)*(hi.y-lo.y)/ny
                    hit,normal,_,_=tree.ray_cast(Vector((x,y,0.001)),Vector((0,0,1)),10000)
                    if hit is not None and hit.z>=1.5 and normal.z<-math.cos(math.radians(s.overhang)):
                        points.append(hit)
            stride=max(1,math.ceil(len(points)/s.auto_count))
            for point in points[::stride]: add_support(context,point)
            self.report({'WARNING'},f'{len(points[::stride])} Vorschlaege erstellt. Inseln, Kontakt und Kollisionen im Slicer pruefen.')
            return {'FINISHED'}
        except Exception as e:
            self.report({'ERROR'},str(e)); return {'CANCELLED'}
        finally:
            if mesh: bpy.data.meshes.remove(mesh)


def rasterize(context, objects):
    s=context.scene.resin_prep
    trees=[]; all_verts=[]
    for obj in objects:
        mesh=snapshot(context,obj)
        try:
            bm=bmesh.new(); bm.from_mesh(mesh)
            closed=len(bm.faces)>0 and all(e.is_manifold for e in bm.edges)
            bm.free()
            if not closed: raise ValueError(obj.name+': Schichtanalyse braucht ein geschlossenes Mesh.')
            tree,verts,_=tree_from_mesh(mesh)
            trees.append((tree,verts)); all_verts.extend(verts)
        finally: bpy.data.meshes.remove(mesh)
    if not all_verts: raise ValueError('Keine Druckmeshes ausgewaehlt.')
    low=np.array([min(v[a] for v in all_verts) for a in range(3)],dtype=float)
    high=np.array([max(v[a] for v in all_verts) for a in range(3)],dtype=float)
    if low[2]<-0.001: raise ValueError('Geometrie liegt unter der Druckplatte. Zuerst platzieren.')
    # Align Z to build plate. XY origin spans selected geometry with exterior padding.
    origin=np.array([math.floor(low[0]/s.xy)*s.xy-s.xy,
                     math.floor(low[1]/s.xy)*s.xy-s.xy,-s.dz])
    nx=int(math.ceil((high[0]-origin[0])/s.xy))+1
    ny=int(math.ceil((high[1]-origin[1])/s.xy))+1
    nz=int(math.ceil((high[2]-origin[2])/s.dz))+1
    if nx*ny*nz>s.voxel_limit:
        raise ValueError(f'{nx*ny*nz:,} Zellen: Analyse XY/Z groeber einstellen (Limit {s.voxel_limit:,}).')
    if ny*nz>150000:
        raise ValueError('Zu viele Strahlzeilen; Analyse XY/Z groeber einstellen.')
    solid=np.zeros((nz,ny,nx),dtype=bool)
    xs=origin[0]+(np.arange(nx)+0.5)*s.xy
    eps=min(s.xy,s.dz)*0.00001
    ambiguous=0
    for tree,verts in trees:
        minx=min(v.x for v in verts)-s.xy
        miny=min(v.y for v in verts); maxy=max(v.y for v in verts)
        minz=min(v.z for v in verts); maxz=max(v.z for v in verts)
        for z in range(nz):
            pz=origin[2]+(z+0.5)*s.dz
            if pz<minz or pz>maxz: continue
            for y in range(ny):
                py=origin[1]+(y+0.5)*s.xy
                if py<miny or py>maxy: continue
                crossings=[]
                # Closed Boolean meshes can place an exact tangent/vertex on a
                # sample ray. Retry odd parity with sub-voxel offsets rather
                # than reporting a sound manifold as an open volume.
                jitter=max(eps*17,max(abs(py),abs(pz),1)*2e-6)
                for oy,oz in ((0,0),(jitter,jitter*1.31),(-jitter,-jitter*1.73)):
                    crossings=[];pos=Vector((minx,py+oy,pz+oz))
                    for _ in range(1024):
                        hit,_,_,_=tree.ray_cast(pos,Vector((1,0,0)),float(high[0]-minx+s.xy*2))
                        if hit is None:break
                        crossings.append(hit.x);pos=Vector((hit.x+eps,py+oy,pz+oz))
                    else:raise ValueError('Mehr als 1024 Flaechenschnitte pro Zeile; Mesh pruefen.')
                    if len(crossings)%2==0:break
                if len(crossings)%2:
                    ambiguous+=1; continue
                for left,right in zip(crossings[::2],crossings[1::2]):
                    solid[z,y] |= (xs>=left)&(xs<right)
    if ambiguous:
        raise ValueError(f'{ambiguous} uneindeutige Strahlzeilen. Geometrie pruefen oder Analyse-Aufloesung leicht aendern.')
    if not solid.any(): raise ValueError('Kein Volumen im Raster; Aufloesung erhoehen oder Einheiten pruefen.')
    return solid,origin


class RP_OT_layers(bpy.types.Operator):
    bl_idname='resin_prep.layers'
    bl_label='Grobe Schichten pruefen'
    bl_description='Voxel-Voranalyse der Auswahl: Inseln, eingeschlossene Hohlraeume, Saugrisiken, Querschnitt. Kann etwas dauern'
    def execute(self,context):
        try:
            # Solo inspection may hide/deselect the analyzed originals. Restore
            # them before requesting a fresh analysis, without selecting backups.
            prior_sources=[]
            if context.active_object is None and _cache.get('source_visibility'):
                prior_sources=[obj for obj,_ in _cache['source_visibility'] if obj.name in context.scene.objects]
            clear_helpers()
            if prior_sources:
                for obj in prior_sources:obj.select_set(True)
                context.view_layer.objects.active=prior_sources[0]
            require_mesh(context); s=context.scene.resin_prep
            objects=[o for o in targets(context) if o.visible_get()]
            solid,origin=rasterize(context,objects)
            from .inspection import install_analysis
            install_analysis(context,solid,origin,objects)
            self.report({'INFO'},'Voranalyse fertig. Bericht im Texteditor; grobe Aufloesung beachten.')
            return {'FINISHED'}
        except Exception as e:
            self.report({'ERROR'},str(e)); return {'CANCELLED'}


class RP_OT_preview(bpy.types.Operator):
    bl_idname='resin_prep.preview'
    bl_label='Schichtbild erstellen'
    def execute(self,context):
        try:
            from .inspection import guard,update_image
            guard(context);update_image(context)
            self.report({'INFO'},'Schichtbild erstellt. Im Image Editor: RP Schichtvorschau.')
            return {'FINISHED'}
        except Exception as e:
            self.report({'ERROR'},str(e));return {'CANCELLED'}


class RP_OT_export(bpy.types.Operator,ExportHelper):
    bl_idname='resin_prep.export'
    bl_label='Auswahl als STL (mm)'
    bl_description='Sichtbare ausgewaehlte Druckmeshes inkl. Modifier und Supports, ohne RP-Hilfsobjekte; immer Millimeter'
    filename_ext='.stl'
    filter_glob:StringProperty(default='*.stl',options={'HIDDEN'})
    def execute(self,context):
        try:
            require_mesh(context)
            objects=[o for o in targets(context) if o.visible_get()]
            if not objects: raise ValueError('Keine sichtbaren Druckmeshes ausgewaehlt.')
            meshes=[]
            try:
                for obj in objects:
                    mesh=snapshot(context,obj); mesh.calc_loop_triangles(); meshes.append(mesh)
                count=sum(len(m.loop_triangles) for m in meshes)
                # Write atomically to avoid leaving a truncated existing file on failure.
                destination=Path(self.filepath)
                if not destination.parent.is_dir(): raise ValueError('Zielordner existiert nicht.')
                fd,tmp=tempfile.mkstemp(prefix='rp_',suffix='.stl',dir=str(destination.parent)); os.close(fd)
                try:
                    with open(tmp,'wb') as out:
                        out.write(b'Resin Prep 0.3 - millimeters'.ljust(80,b'\0')); out.write(struct.pack('<I',count))
                        for mesh in meshes:
                            for tri in mesh.loop_triangles:
                                a,b,c=(mesh.vertices[i].co for i in tri.vertices)
                                normal=(b-a).cross(c-a).normalized()
                                out.write(struct.pack('<12fH',*normal,*a,*b,*c,0))
                    os.replace(tmp,destination)
                finally:
                    if os.path.exists(tmp): os.unlink(tmp)
            finally:
                for mesh in meshes: bpy.data.meshes.remove(mesh)
            self.report({'INFO'},f'{count} Dreiecke in mm exportiert. Jetzt im Drucker-Slicer slicen.')
            return {'FINISHED'}
        except Exception as e:
            self.report({'ERROR'},str(e)); return {'CANCELLED'}


class RP_OT_uvopen(bpy.types.Operator):
    bl_idname='resin_prep.uvopen'
    bl_label='Druckdatei in UVtools oeffnen'
    def execute(self,context):
        try:
            s=context.scene.resin_prep
            exe=bpy.path.abspath(s.uv_exe); file=bpy.path.abspath(s.sliced_file)
            if not Path(exe).is_file(): raise ValueError('UVtools-Programmpfad einstellen.')
            if not Path(file).is_file(): raise ValueError('Vorhandene geslicte Druckdatei waehlen, z.B. GOO oder CTB.')
            if Path(file).suffix.lower() in ('.stl','.obj','.blend'):
                raise ValueError('UVtools braucht hier eine geslicte Druckdatei, kein STL/OBJ/Blend.')
            subprocess.Popen([exe,file],shell=False)
            self.report({'INFO'},'Datei an UVtools uebergeben. Dort Endkontrolle ausfuehren.')
            return {'FINISHED'}
        except Exception as e:
            self.report({'ERROR'},str(e)); return {'CANCELLED'}


def uv_poll():
    global _uv_process,_uv_log
    if _uv_process is None: return None
    if _uv_process.poll() is None: return 0.5
    _uv_log.seek(0)
    output=_uv_log.read().decode('utf-8',errors='replace')
    text=bpy.data.texts.get('Resin Prep - UVtools.txt') or bpy.data.texts.new('Resin Prep - UVtools.txt')
    text.clear(); text.write('UVtools print-issues exit code: '+str(_uv_process.returncode)+
                            '\nCommand (JSON): '+json.dumps(_cache.get('uv_command',[]),ensure_ascii=False)+
                            '\nDatei (Metadaten): '+json.dumps(_cache.get('uv_input',{}),ensure_ascii=False)+'\n'+output)
    for scene in bpy.data.scenes:
        if hasattr(scene,'resin_prep'): scene.resin_prep.status='UVtools-Bericht im Texteditor: Resin Prep - UVtools.txt'
    _uv_log.close(); _uv_process=None; _uv_log=None
    return None


class RP_OT_uvcheck(bpy.types.Operator):
    bl_idname='resin_prep.uvcheck'
    bl_label='UVtools-Endkontrolle starten'
    bl_description='Externe UVtoolsCmd print-issues Analyse; liest die Druckdatei, aendert sie nicht. Bericht im Texteditor'
    def execute(self,context):
        global _uv_process,_uv_log
        try:
            if _uv_process is not None: raise ValueError('UVtools-Analyse laeuft bereits.')
            s=context.scene.resin_prep
            ui=Path(bpy.path.abspath(s.uv_exe)); file=Path(bpy.path.abspath(s.sliced_file))
            cmd=ui.with_name('UVtoolsCmd.exe' if os.name=='nt' else 'UVtoolsCmd')
            if not ui.is_file(): raise ValueError('UVtools-Programmpfad einstellen.')
            if not file.is_file() or file.suffix.lower() in ('.stl','.obj','.blend'):
                raise ValueError('Vorhandene geslicte Druckdatei auswaehlen.')
            args=[str(cmd),'print-issues',str(file)] if cmd.is_file() else [str(ui),'--cmd','print-issues',str(file)]
            args += ['--islands','--overhangs','--resin-traps','--suction-cups','--touching-bounds','--print-height','--empty-layers']
            if s.uv_group_islands:
                from .inspection import uv_capabilities
                capabilities=uv_capabilities(ui)
                if not capabilities['group_islands']:
                    raise ValueError('Diese UVtools-CLI unterstuetzt --group-islands nicht. UVtools aktualisieren oder Gruppierung deaktivieren.')
                args.append('--group-islands')
            _cache['uv_command']=args.copy()
            _cache['uv_input']={'path':str(file),'size_bytes':file.stat().st_size,'modified_ns':file.stat().st_mtime_ns}
            _uv_log=tempfile.TemporaryFile()
            try:
                _uv_process=subprocess.Popen(args,stdout=_uv_log,stderr=subprocess.STDOUT,shell=False,
                                            creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            except Exception:
                _uv_log.close(); _uv_log=None; raise
            bpy.app.timers.register(uv_poll,first_interval=0.5)
            s.status='UVtools-Endkontrolle laeuft im Hintergrund.'
            return {'FINISHED'}
        except Exception as e:
            self.report({'ERROR'},str(e)); return {'CANCELLED'}


class RP_OT_uvcancel(bpy.types.Operator):
    bl_idname='resin_prep.uvcancel'
    bl_label='UVtools-Analyse abbrechen'
    def execute(self,context):
        if _uv_process is not None: _uv_process.terminate()
        return {'FINISHED'}


class RP_PT_main(bpy.types.Panel):
    bl_label='Resin Prep 0.3'
    bl_idname='RP_PT_main'
    bl_space_type='VIEW_3D'; bl_region_type='UI'; bl_category='Resin Prep'
    def draw(self,context):
        layout=self.layout; s=context.scene.resin_prep
        layout.label(text='Z=0 Druckplatte | Druckrichtung +Z')
        layout.prop(s,'units')
        layout.label(text=f'Bauraum {s.bed_x:g} × {s.bed_y:g} × {s.bed_z:g} mm')
        header,box=layout.panel('rp_printer_settings',default_closed=True)
        header.label(text='Bauraum, Platzierung und Drehung')
        if box:
            box.operator('resin_prep.profile')
            for p in ('bed_x','bed_y','bed_z'): box.prop(s,p)
            box.label(text='Frei editierbar fuer andere Resin-Drucker')
            box.operator('resin_prep.bed')
            box.prop(s,'lift'); box.operator('resin_prep.place')
            for p in ('angle_x','angle_y','angle_z'):box.prop(s,p)
            box.operator('resin_prep.orient')
        wrapped(layout,s.status)


class RP_PT_checks(bpy.types.Panel):
    bl_label='1 - Modell pruefen und bereinigen'
    bl_idname='RP_PT_checks'
    bl_parent_id='RP_PT_main'
    bl_space_type='VIEW_3D'; bl_region_type='UI'; bl_category='Resin Prep'
    bl_options={'DEFAULT_CLOSED'}
    def draw(self,context):
        l=self.layout; s=context.scene.resin_prep
        l.prop(s,'min_wall'); l.prop(s,'overhang')
        l.operator('resin_prep.analyze',icon='VIEWZOOM')
        cache=_cache.get('mesh')
        if cache:
            r=cache[2]; l.label(text='Letzte Pruefung: '+cache[0])
            l.label(text='Bauraum: '+('innerhalb' if r['in_build_volume'] else 'AUSSERHALB'))
            l.label(text=f"{r['volume_ml']:.2f} ml / {r['mass_g']:.2f} g / Kosten {r['cost']:.2f}")
            if not r['volume_reliable']: l.label(text='Volumen/Kosten unsicher: Mesh pruefen',icon='ERROR')
            for key,label in [('open','Offene / Mehrfach-Kanten'),('winding','Normalen-Widerspruch'),
                              ('degenerate','Nullflaechen'),('distorted','Nichtplanare Flaechen'),
                              ('sharp','Sehr scharfe Kanten'),('intersections','Schnitt-Kandidaten'),
                              ('thin','Duenne Stellen (Stichprobe)'),('overhang','Ueberhang-Flaechen')]:
                op=l.operator('resin_prep.highlight',text=f'{label}: {len(r["issues"][key])}'); op.kind=key
            if not r['intersections_checked']: l.label(text='Schnitt-Test uebersprungen: Dreiecklimit',icon='ERROR')
            l.label(text=f"Lose Kanten {r['loose_edges']} | lose Punkte {r['loose_vertices']}")
        l.prop(s,'merge'); l.operator('resin_prep.repair')
        l.prop(s,'sample_limit'); l.prop(s,'intersection_limit')


class RP_PT_hollow(bpy.types.Panel):
    bl_label='2 - Aushoehlen und Ablauflöcher'
    bl_idname='RP_PT_hollow'; bl_parent_id='RP_PT_main'
    bl_space_type='VIEW_3D'; bl_region_type='UI'; bl_category='Resin Prep'
    bl_options={'DEFAULT_CLOSED'}
    def draw(self,context):
        l=self.layout; s=context.scene.resin_prep
        l.prop(s,'wall'); l.operator('resin_prep.hollow')
        l.label(text='Innenoffset: enge Stellen danach pruefen')
        l.separator(); l.label(text='3D-Cursor auf gewuenschte Wand setzen')
        l.prop(s,'hole'); l.prop(s,'hole_depth'); l.prop(s,'hole_axis')
        l.operator('resin_prep.drain')
        l.label(text='Durchgang und Entlueftung kontrollieren')


class RP_PT_supports(bpy.types.Panel):
    bl_label='3 - Einfache Plattensupports'
    bl_idname='RP_PT_supports'; bl_parent_id='RP_PT_main'
    bl_space_type='VIEW_3D'; bl_region_type='UI'; bl_category='Resin Prep'
    bl_options={'DEFAULT_CLOSED'}
    def draw(self,context):
        l=self.layout; s=context.scene.resin_prep
        for p in ('tip','shaft','foot'): l.prop(s,p)
        l.operator('resin_prep.support')
        l.prop(s,'support_spacing'); l.prop(s,'auto_count'); l.operator('resin_prep.autosupport')
        l.label(text='Vorschlaege: keine volle Inselabdeckung')
        l.label(text='Supports mit Modell zum Export auswaehlen')


class RP_PT_layers(bpy.types.Panel):
    bl_label='4 - Schicht-Voranalyse (grob)'
    bl_idname='RP_PT_layers'; bl_parent_id='RP_PT_main'
    bl_space_type='VIEW_3D'; bl_region_type='UI'; bl_category='Resin Prep'
    def draw(self,context):
        l=self.layout; s=context.scene.resin_prep
        l.prop(s,'xy'); l.prop(s,'dz'); l.prop(s,'voxel_limit')
        l.operator('resin_prep.layers')
        wrapped(l,s.layer_status)
        cached=_cache.get('layers')
        if cached:
            wrapped(l,f'Cache: {len(cached[0])} Schichten; nach Aenderung neu pruefen')
            l.label(text=f"Max. Querschnitt: {cached[4]['max_area_mm2']:.1f} mm²")
        l.label(text='Kleine Fehler/Loecher koennen fehlen',icon='ERROR')


class RP_PT_export(bpy.types.Panel):
    bl_label='8 - Export und UVtools'
    bl_idname='RP_PT_export'; bl_parent_id='RP_PT_main'
    bl_space_type='VIEW_3D'; bl_region_type='UI'; bl_category='Resin Prep'
    bl_options={'DEFAULT_CLOSED'}
    def draw(self,context):
        l=self.layout; s=context.scene.resin_prep
        l.prop(s,'density'); l.prop(s,'price')
        l.operator('resin_prep.export',icon='EXPORT')
        l.label(text='STL im Drucker-Slicer slicen, dann:')
        l.prop(s,'uv_exe'); l.prop(s,'sliced_file')
        l.operator('resin_prep.uvcapabilities');wrapped(l,s.uv_status)
        l.prop(s,'uv_group_islands')
        l.operator('resin_prep.uvopen'); l.operator('resin_prep.uvcheck')
        if _uv_process is not None: l.operator('resin_prep.uvcancel')
        l.label(text='Berichte: Blender-Texteditor')


from .inspection import classes as inspection_classes,stop_simulation,load_reset,clear_helpers,recover_visibility
from .repair import classes as repair_classes,register_settings as register_repair,unregister_settings as unregister_repair

classes=(RP_Settings,RP_OT_analyze,RP_OT_highlight,RP_OT_repair,RP_OT_hollow,RP_OT_drain,
         RP_OT_bed,RP_OT_profile,RP_OT_place,RP_OT_orient,RP_OT_support,RP_OT_autosupport,RP_OT_layers,
         RP_OT_preview,RP_OT_export,RP_OT_uvopen,RP_OT_uvcheck,RP_OT_uvcancel,
         RP_PT_main,RP_PT_checks,RP_PT_hollow,RP_PT_supports,RP_PT_layers,RP_PT_export)+inspection_classes+repair_classes


def register():
    for cls in classes: bpy.utils.register_class(cls)
    bpy.types.Scene.resin_prep=PointerProperty(type=RP_Settings)
    register_repair()
    if load_reset not in bpy.app.handlers.load_pre:bpy.app.handlers.load_pre.append(load_reset)
    if recover_visibility not in bpy.app.handlers.load_post:bpy.app.handlers.load_post.append(recover_visibility)
    if not bpy.app.timers.is_registered(recover_visibility):
        bpy.app.timers.register(recover_visibility,first_interval=0.0)


def unregister():
    global _uv_process,_uv_log
    stop_simulation()
    unregister_repair()
    clear_helpers()
    if load_reset in bpy.app.handlers.load_pre:bpy.app.handlers.load_pre.remove(load_reset)
    if recover_visibility in bpy.app.handlers.load_post:bpy.app.handlers.load_post.remove(recover_visibility)
    if bpy.app.timers.is_registered(recover_visibility):bpy.app.timers.unregister(recover_visibility)
    if bpy.app.timers.is_registered(uv_poll): bpy.app.timers.unregister(uv_poll)
    if _uv_process is not None:
        _uv_process.terminate()
        _uv_process.wait(timeout=5)
        _uv_process=None
    if _uv_log is not None: _uv_log.close(); _uv_log=None
    if hasattr(bpy.types.Scene,'resin_prep'): del bpy.types.Scene.resin_prep
    for cls in reversed(classes): bpy.utils.unregister_class(cls)
    _cache.clear()
