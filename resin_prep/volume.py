"""Bounded voxel diagnostics. All distances mm; arrays [z,y,x]. GPL-3.0-or-later."""
from collections import deque
import numpy as np


def components(mask):
    h,w=mask.shape;seen=np.zeros_like(mask,dtype=bool)
    for y,x in zip(*np.nonzero(mask)):
        if seen[y,x]:continue
        q=deque([(int(y),int(x))]);seen[y,x]=True;points=[]
        while q:
            cy,cx=q.popleft();points.append((cy,cx))
            for ny,nx in ((cy-1,cx),(cy+1,cx),(cy,cx-1),(cy,cx+1)):
                if 0<=ny<h and 0<=nx<w and mask[ny,nx] and not seen[ny,nx]:
                    seen[ny,nx]=True;q.append((ny,nx))
        yield np.asarray(points,dtype=np.int32)


def label3(mask):
    nz,ny,nx=mask.shape;labels=np.zeros(mask.shape,dtype=np.int32);counter=0
    for z,y,x in zip(*np.nonzero(mask)):
        if labels[z,y,x]:continue
        counter+=1;labels[z,y,x]=counter;q=deque([(int(z),int(y),int(x))])
        while q:
            z0,y0,x0=q.popleft()
            for a,b,c in ((z0-1,y0,x0),(z0+1,y0,x0),(z0,y0-1,x0),(z0,y0+1,x0),(z0,y0,x0-1),(z0,y0,x0+1)):
                if 0<=a<nz and 0<=b<ny and 0<=c<nx and mask[a,b,c] and not labels[a,b,c]:
                    labels[a,b,c]=counter;q.append((a,b,c))
    return labels


def flood3(mask):
    nz,ny,nx=mask.shape;seen=np.zeros_like(mask,dtype=bool);q=deque()
    boundary=np.zeros_like(mask,dtype=bool)
    boundary[0]=boundary[-1]=True;boundary[:,0]=boundary[:,-1]=True;boundary[:,:,0]=boundary[:,:,-1]=True
    for z,y,x in zip(*np.nonzero(mask&boundary)):
        seen[z,y,x]=True;q.append((int(z),int(y),int(x)))
    while q:
        z,y,x=q.popleft()
        for a,b,c in ((z-1,y,x),(z+1,y,x),(z,y-1,x),(z,y+1,x),(z,y,x-1),(z,y,x+1)):
            if 0<=a<nz and 0<=b<ny and 0<=c<nx and mask[a,b,c] and not seen[a,b,c]:
                seen[a,b,c]=True;q.append((a,b,c))
    return seen


def floating_labels(solid,origin_z,dz):
    """Causal support: plate/grounded pixels anchor, floating pixels never do.

    Floating branches retain their ancestry across splits. Merging branches
    union their earlier labels. A later grounded connection stops propagation,
    without retroactively making the earlier floating layers printable.
    """
    nz,ny,nx=solid.shape;labels=np.zeros(solid.shape,dtype=np.int32)
    grounded=np.zeros((ny,nx),dtype=bool);parent=[0]
    def root(n):
        while parent[n]!=n:parent[n]=parent[parent[n]];n=parent[n]
        return n
    for z in range(nz):
        next_grounded=np.zeros_like(grounded)
        prior=labels[z-1] if z else np.zeros((ny,nx),dtype=np.int32)
        bottom=origin_z+z*dz;on_plate=bottom<=1e-6 and bottom+dz>1e-6
        for pts in components(solid[z]):
            ys,xs=pts.T
            if on_plate or grounded[ys,xs].any():next_grounded[ys,xs]=True;continue
            ancestors={root(int(i)) for i in np.unique(prior[ys,xs]) if i}
            if ancestors:
                group=min(ancestors)
                for other in ancestors:parent[other]=group
            else:group=len(parent);parent.append(group)
            labels[z,ys,xs]=group
        grounded=next_grounded
    lookup=np.asarray([root(i) for i in range(len(parent))],dtype=np.int32)
    return lookup[labels]


def region_records(labels,origin,xy,dz,kind,min_pixels=2):
    records=[]
    # One pass over occupied voxels, then aggregate sorted label groups.
    zs,ys,xs=np.nonzero(labels)
    if not len(zs):return records
    ids=labels[zs,ys,xs];order=np.argsort(ids,kind='stable');ids=ids[order]
    zs=zs[order];ys=ys[order];xs=xs[order]
    starts=np.r_[0,np.flatnonzero(np.diff(ids))+1];ends=np.r_[starts[1:],len(ids)]
    for left,right in zip(starts,ends):
        zz=zs[left:right];yy=ys[left:right];xx=xs[left:right]
        if len(zz)<min_pixels:continue
        start=int(zz.min());end=int(zz.max());first=zz==start
        counts=np.bincount(zz,minlength=labels.shape[0])
        position=[float(origin[0]+(xx[first].mean()+0.5)*xy),float(origin[1]+(yy[first].mean()+0.5)*xy),float(origin[2]+(start+0.5)*dz)]
        records.append(dict(type=kind,label=int(ids[left]),start_layer=start,end_layer=end,
                            z_mm=position[2],position=position,z_start_mm=float(origin[2]+start*dz),
                            z_end_mm=float(origin[2]+(end+1)*dz),area_mm2=float(counts.max()*xy*xy),
                            voxel_count=int(len(zz)),volume_ml=float(len(zz)*xy*xy*dz/1000)))
    return sorted(records,key=lambda r:(r['start_layer'],r['label']))


def convex_hull(points):
    points=sorted(set(tuple(map(float,p)) for p in points))
    if len(points)<=1:return points
    def cross(a,b,c):return (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])
    low=[];high=[]
    for p in points:
        while len(low)>=2 and cross(low[-2],low[-1],p)<=0:low.pop()
        low.append(p)
    for p in reversed(points):
        while len(high)>=2 and cross(high[-2],high[-1],p)<=0:high.pop()
        high.append(p)
    return low[:-1]+high[:-1]


def stability(solid,origin,xy,dz):
    zs,ys,xs=np.nonzero(solid)
    center=[float(origin[0]+(xs.mean()+0.5)*xy),float(origin[1]+(ys.mean()+0.5)*xy),float(origin[2]+(zs.mean()+0.5)*dz)] if len(zs) else None
    contact=np.zeros(solid.shape[1:],dtype=bool)
    for z in range(len(solid)):
        bottom=origin[2]+z*dz
        if bottom<=1e-6 and bottom+dz>1e-6:contact|=solid[z]
    points=[]
    for y in np.flatnonzero(contact.any(axis=1)):
        occupied=np.flatnonzero(contact[y]);left=int(occupied[0]);right=int(occupied[-1])+1
        for a in (left,right):
            for b in (int(y),int(y)+1):points.append((origin[0]+a*xy,origin[1]+b*xy))
    hull=convex_hull(points);inside=False;margin=None
    if center is not None and len(hull)>=3:
        distances=[]
        for a,b in zip(hull,hull[1:]+hull[:1]):
            dx=b[0]-a[0];dy=b[1]-a[1]
            distances.append((dx*(center[1]-a[1])-dy*(center[0]-a[0]))/(dx*dx+dy*dy)**0.5)
        margin=float(min(distances));inside=margin>=-1e-6
    return dict(center_of_mass_mm=center,contact_area_mm2=float(contact.sum()*xy*xy),
                contact_hull_mm=[list(p) for p in hull],center_projection_in_contact_hull=bool(inside),
                contact_margin_mm=margin,note='Rastergeometrie bei homogener Dichte; kein Festigkeitsnachweis oder Abloesekraftmodell.')


def analyze_volume(solid,origin,xy,dz,min_pixels=2):
    if solid.ndim!=3 or min(solid.shape)<1 or xy<=0 or dz<=0:raise ValueError('Gueltiges 3D-Raster und positive Rastermasse erforderlich.')
    solid=np.asarray(solid,dtype=bool);origin=np.asarray(origin,dtype=float)
    nz,ny,nx=solid.shape;floating=floating_labels(solid,float(origin[2]),float(dz))
    cups=np.zeros_like(solid);unsupported=np.zeros_like(solid)
    reachable_previous=np.ones((ny,nx),dtype=bool);previous=np.zeros((ny,nx),dtype=bool)
    counts=np.count_nonzero(solid,axis=(1,2));nonempty=np.flatnonzero(counts)
    empty=[];overhang=[];curve=[]
    for z in range(nz):
        layer=solid[z];z_mm=float(origin[2]+(z+0.5)*dz)
        if nonempty.size and nonempty[0]<z<nonempty[-1] and not counts[z]:empty.append(z_mm)
        if origin[2]+z*dz>1e-6:
            unsupported[z]=layer&~previous;area=float(unsupported[z].sum()*xy*xy)
            if area:overhang.append(dict(z_mm=z_mm,area_mm2=area))
        reachable=np.zeros((ny,nx),dtype=bool)
        for pts in components(~layer):
            ys,xs=pts.T
            vents=((xs==0)|(xs==nx-1)|(ys==0)|(ys==ny-1)).any() or reachable_previous[ys,xs].any()
            if vents:reachable[ys,xs]=True
            elif len(pts)>=min_pixels:cups[z,ys,xs]=True
        reachable_previous=reachable;previous=layer
        area=float(counts[z]*xy*xy);prior=float(counts[z-1]*xy*xy) if z else 0.0
        curve.append(dict(layer=z,z_mm=z_mm,area_mm2=area,area_change_mm2=area-prior,
                          unsupported_area_mm2=float(unsupported[z].sum()*xy*xy),
                          floating_area_mm2=float(np.count_nonzero(floating[z])*xy*xy),suction_area_mm2=float(cups[z].sum()*xy*xy)))
    trapped=(~solid)&~flood3(~solid);trap_labels=label3(trapped);cup_labels=label3(cups)
    islands=region_records(floating,origin,xy,dz,'island',min_pixels)
    traps=region_records(trap_labels,origin,xy,dz,'resin_trap',min_pixels)
    suction=region_records(cup_labels,origin,xy,dz,'suction',min_pixels)
    maximum=int(np.argmax(counts));max_area=float(counts[maximum]*xy*xy)
    for point in curve:point['area_risk_index']=point['area_mm2']/max_area if max_area else 0.0
    report=dict(islands=islands,resin_traps=traps,suction_risks=suction,empty_z_mm=empty,
                unsupported_area_by_layer=overhang,layer_curve=curve,
                trapped_volume_ml=float(trapped.sum()*xy*xy*dz/1000),trapped_voxels=int(trapped.sum()),
                max_area_mm2=max_area,max_area_z_mm=float(origin[2]+(maximum+0.5)*dz),
                voxel_volume_ml=float(solid.sum()*xy*xy*dz/1000),stability=stability(solid,origin,xy,dz),
                grouped_islands=True,analysis_version='0.2.0',
                limits=['Raster kann kleine Details/Kontakte verlieren.','Keine physikalische Abloesekraftsimulation.',
                        'Spaetere Verbindungen heilen fruehere schwebende Schichten nicht.'])
    return report,dict(island=floating,resin_trap=trap_labels,suction=cup_labels,overhang=unsupported)


def diagnose(solid,origin,xy,dz,min_pixels=2):
    return analyze_volume(solid,origin,xy,dz,min_pixels)[0]


def section_rgba(solid,index,mode='PLAIN',offset=-1,split=0.5,reference=None,onion=3):
    index=max(0,min(int(index),len(solid)-1));current=solid[index]
    h,w=current.shape;rgb=np.zeros((h,w,4),dtype=np.float32);rgb[:,:,3]=1
    rgb[:,:,:3]=current[:,:,None]
    other_index=max(0,min(index+int(offset),len(solid)-1))
    other=reference[index] if reference is not None else solid[other_index]
    if mode=='DIFFERENCE':
        rgb[:,:,:3]=0;rgb[current&other,:3]=(0.65,0.65,0.65)
        rgb[current&~other,:3]=(0.1,1.0,0.25);rgb[~current&other,:3]=(1.0,0.15,0.1)
    elif mode=='SPLIT':
        cut=int(np.clip(split,0,1)*w);rgb[:,cut:,:3]=other[:,cut:,None]
        if 0<cut<w:rgb[:,cut-1,:3]=(1,0.65,0.05)
    elif mode=='ONION':
        for distance in range(min(3,int(onion)),0,-1):
            for sign,color in ((-1,(0.1,0.35,1.0)),(1,(1.0,0.25,0.1))):
                z=index+sign*distance
                if 0<=z<len(solid):
                    mask=solid[z]&~current
                    rgb[mask,:3]=np.maximum(rgb[mask,:3],np.asarray(color,dtype=np.float32)*(0.65/distance))
        rgb[current,:3]=1
    return rgb


def clip_volume(solid,origin,xy,dz,axis='Z',mode='FULL',position=50,slab=10):
    if mode=='FULL':return solid.copy()
    coordinate={'X':2,'Y':1,'Z':0}[axis];length=solid.shape[coordinate]
    center=np.clip(position/100,0,1)*length;indices=np.arange(length)+0.5
    if mode=='BELOW':keep=indices<=center
    elif mode=='ABOVE':keep=indices>=center
    else:keep=abs(indices-center)<=max(0.5,slab/100*length/2)
    shape=[1,1,1];shape[coordinate]=length
    return solid&keep.reshape(shape)


def surface_mesh(solid,origin,xy,dz,tags=None,max_faces=350000):
    """Outward, shared-vertex exposed voxel faces. Cuts are automatically capped."""
    padded=np.pad(solid,1);nz,ny,nx=solid.shape
    specs=[((0,0,-1),[(0,0,0),(0,0,1),(0,1,1),(0,1,0)]),
           ((0,0,1),[(1,0,0),(1,1,0),(1,1,1),(1,0,1)]),
           ((0,-1,0),[(0,0,0),(1,0,0),(1,0,1),(0,0,1)]),
           ((0,1,0),[(0,1,0),(0,1,1),(1,1,1),(1,1,0)]),
           ((-1,0,0),[(0,0,0),(0,1,0),(1,1,0),(1,0,0)]),
           ((1,0,0),[(0,0,1),(1,0,1),(1,1,1),(0,1,1)])]
    exposed=[];count=0
    for (oz,oy,ox),corners in specs:
        neighbor=padded[1+oz:1+oz+nz,1+oy:1+oy+ny,1+ox:1+ox+nx]
        positions=np.argwhere(solid&~neighbor);count+=len(positions)
        if count>max_faces:raise ValueError('Zu viele Vorschauflaechen. Raster vergroessern oder engeren Schnitt waehlen.')
        exposed.append((positions,corners))
    verts=[];faces=[];colors=[];lookup={}
    for positions,corners in exposed:
        for z,y,x in positions:
            face=[]
            for dx,dy,dzz in corners:
                point=(int(x)+dx,int(y)+dy,int(z)+dzz);index=lookup.get(point)
                if index is None:
                    index=len(verts);lookup[point]=index
                    verts.append((float(origin[0]+point[0]*xy),float(origin[1]+point[1]*xy),float(origin[2]+point[2]*dz)))
                face.append(index)
            faces.append(tuple(face));colors.append(int(tags[z,y,x]) if tags is not None else 0)
    return verts,faces,colors
