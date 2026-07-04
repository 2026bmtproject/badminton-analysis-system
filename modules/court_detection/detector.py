#!/usr/bin/env python3
"""
court_detect.py -- Robust, color-agnostic badminton court boundary detection.

A badminton broadcast court is a planar surface, so the mapping court->image is a
homography fully determined by the 4 outer corners. This detector finds those
corners and projects the 16 standard key points.

Pipeline (bounded runtime, no O(n^4) blow-up):
  1. Playing-surface segmentation from a central colour seed in Lab (works for
     green / red / blue / any court). A texture gate handles same-colour
     surrounds; a luminance fallback handles near-grayscale scenes.
  2. Colour-agnostic white line-pixel detection inside the surface ROI.
  3. Vanishing-point RANSAC groups line segments into the court's two perspective
     families (rejects banners / crowd / text); falls back to all family segments
     for near head-on (low-tilt) cameras where lines are almost parallel.
  4. GRID-FIT: cluster the line families, then try every (left,right)x(top,bottom)
     combination and keep the quad whose FULL 12-line grid (incl. service / centre
     / singles lines) best matches the pixels -- corners are confirmed by the
     inner lines, not by trusting a single extreme line.
  5. ICP lock + guarded per-edge boundary refinement (kept only if grid coverage
     does not drop). Candidate scoring by per-line grid coverage.

Processing is done at a normalised working width (1920 px) which makes the
pixel-tuned parameters resolution-agnostic and adds noise robustness.

VIDEO: detect_video() median-composites sampled frames first, so moving players
vanish and only the static court remains -> clean, occlusion-free detection.

Public API:
    detect(bgr_image)          -> np.ndarray (16x2) of key points, or None
    detect_video(path)         -> np.ndarray (16x2), composites frames first
    detect_from_frame(frame)   -> (list[(x,y)] | None, info_dict)   [drop-in]

Dependencies: numpy, opencv-python only.
"""
import cv2
import numpy as np
import time


# === court geometry ===

H_LINES = np.array([0.00, 0.76, 4.72, 6.705, 8.685, 12.65, 13.41])
V_LINES = np.array([0.00, 0.46, 3.05, 5.64, 6.10])

OUTPUT_IDX = [
    (0, 0), (0, 4), (6, 0), (6, 4),
    (0, 1), (0, 3), (6, 1), (6, 3),
    (2, 0), (2, 4), (4, 0), (4, 4),
    (2, 2), (4, 2), (3, 0), (3, 4),
]

CORNER_COURT = np.float32([
    [V_LINES[0],  H_LINES[0]],
    [V_LINES[-1], H_LINES[0]],
    [V_LINES[0],  H_LINES[-1]],
    [V_LINES[-1], H_LINES[-1]],
])

def load_csv(path):
    pts = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line: continue
            a, b = line.split(';')
            pts.append((float(a), float(b)))
    return np.array(pts, dtype=np.float64)

def homography_from_corners(corners):
    H, _ = cv2.findHomography(CORNER_COURT, np.float32(corners))
    return H

def project_16(H):
    pts = []
    for hi, vi in OUTPUT_IDX:
        p = H @ np.array([V_LINES[vi], H_LINES[hi], 1.0])
        pts.append((p[0]/p[2], p[1]/p[2]))
    return np.array(pts)

def eval_error(pred16, gt16):
    d = np.linalg.norm(pred16 - gt16, axis=1)
    tl, tr, bl, br = gt16[0], gt16[1], gt16[2], gt16[3]
    diag = (np.linalg.norm(br - tl) + np.linalg.norm(bl - tr)) / 2
    return {
        'corner_mean': float(np.mean(d[:4])),
        'corner_max': float(np.max(d[:4])),
        'all_mean': float(np.mean(d)),
        'all_max': float(np.max(d)),
        'corner_pct': float(np.mean(d[:4]) / diag * 100),
        'per_point': d,
    }

# === vanishing point ===

def _vp_seg_line(x1,y1,x2,y2):
    a=y2-y1; b=x1-x2; c=-(a*x1+b*y1)
    n=np.hypot(a,b)+1e-9
    return np.array([a/n,b/n,c/n])

def _vp_intersect(l1,l2):
    a1,b1,c1=l1; a2,b2,c2=l2
    D=a1*b2-a2*b1
    if abs(D)<1e-9: return None
    return np.array([(b1*c2-b2*c1)/D,(c1*a2-c2*a1)/D])

def _vp_vp_ransac(segs, iters=300, tol=0.02, seed=0):
    """segs: list of (x1,y1,x2,y2,ang,len). Find dominant vanishing point.
    A segment is inlier if the VP lies near the segment's infinite line
    (normalized perpendicular distance small relative to VP distance)."""
    if len(segs)<2: return None, []
    rng=np.random.RandomState(seed)
    lines=[_vp_seg_line(*s[:4]) for s in segs]
    mids=np.array([[ (s[0]+s[2])/2,(s[1]+s[3])/2] for s in segs])
    lens=np.array([s[5] for s in segs])
    best_inl=[]; best_vp=None
    idxs=np.arange(len(segs))
    for _ in range(iters):
        i,j=rng.choice(idxs,2,replace=False)
        vp=_vp_intersect(lines[i],lines[j])
        if vp is None: continue
        # inlier if line passes near vp: perpendicular distance of vp to line,
        # normalized by distance from segment midpoint to vp (angular tolerance)
        inl=[]
        for k,l in enumerate(lines):
            pd=abs(l[0]*vp[0]+l[1]*vp[1]+l[2])
            dist=np.hypot(vp[0]-mids[k,0],vp[1]-mids[k,1])+1e-9
            if pd/dist < tol:
                inl.append(k)
        # weight by length
        w=lens[inl].sum() if inl else 0
        if w > (lens[best_inl].sum() if best_inl else 0):
            best_inl=inl; best_vp=vp
    return best_vp, best_inl

# === boundary refinement ===

def _sample_model_line(H, kind, val, n=140):
    if kind=='v':
        ys=np.linspace(H_LINES[0],H_LINES[-1],n)
        pts=np.stack([np.full(n,val),ys,np.ones(n)],1)
    else:
        xs=np.linspace(V_LINES[0],V_LINES[-1],n)
        pts=np.stack([xs,np.full(n,val),np.ones(n)],1)
    P=(H@pts.T).T
    return P[:,:2]/P[:,2:3]

def refine_boundary(H, lp, band=16.0, outward_bias=True):
    """Snap the 4 outer model lines (v=0,v=6.10,h=0,h=13.41) to real line pixels.
    Returns refined corners [TL,TR,BL,BR] or None."""
    ys,xs=np.where(lp>0)
    if len(xs)<50: return None
    P=np.column_stack([xs,ys]).astype(np.float32)
    sides={'left':('v',V_LINES[0]),'right':('v',V_LINES[-1]),
           'top':('h',H_LINES[0]),'bot':('h',H_LINES[-1])}
    fitted={}
    for name,(kind,val) in sides.items():
        line=_sample_model_line(H,kind,val)
        # direction & normal along the projected line (use endpoints)
        d=line[-1]-line[0]; L=np.hypot(*d)+1e-9; d=d/L
        nrm=np.array([-d[1],d[0]])
        # perpendicular & tangential coord of every line pixel wrt line[0]
        rel=P-line[0]
        perp=rel@nrm
        tang=rel@d
        # keep pixels whose tangential coord within the segment span and perp within band
        seg_len=L
        m=(tang>-0.05*seg_len)&(tang<1.05*seg_len)&(np.abs(perp)<band*3)
        if m.sum()<30:
            fitted[name]=None; continue
        pk=perp[m]
        # histogram of perpendicular offsets -> find line clusters
        hist,edges=np.histogram(pk,bins=np.arange(-band*3,band*3+2,2))
        centers=(edges[:-1]+edges[1:])/2
        # significant peaks
        thr=max(hist.max()*0.35, 15)
        peakmask=hist>=thr
        # merge adjacent
        cand=centers[peakmask]
        if len(cand)==0:
            off=np.median(pk)
        else:
            # outward direction (away from court centre)
            c=(H@np.array([V_LINES[2],H_LINES[3],1.0])); c=c[:2]/c[2]
            outsign=np.sign((c-line[0])@nrm)*-1
            # signed outward offset of each peak
            outv=cand*outsign
            # prefer the OUTERMOST significant peak that is still within ~band of
            # the projection (avoids grabbing lines far outside), i.e. the true
            # outer boundary rather than an inner (singles/long-service) line.
            near=cand[np.abs(cand)<band]
            if len(near)>0:
                nout=near*outsign
                best=near[np.argmax(nout)]
            else:
                best=min(cand,key=lambda x:abs(x))
            off=best
        # collect pixels within +-band of chosen offset, fit robust line
        sel=m.copy()
        sel[m]= np.abs(pk-off)<band
        pts=P[sel]
        if len(pts)<20:
            fitted[name]=None; continue
        vx,vy,x0,y0=cv2.fitLine(pts,cv2.DIST_HUBER,0,0.01,0.01).ravel()
        # line as a,b,c
        a=vy; b=-vx; cc_=-(a*x0+b*y0)
        fitted[name]=(a,b,cc_)
    # need 4 lines to intersect; fall back to projected line if missing
    def proj_line(kind,val):
        line=_sample_model_line(H,kind,val)
        p1,p2=line[0],line[-1]
        a=p2[1]-p1[1]; b=p1[0]-p2[0]; c=-(a*p1[0]+b*p1[1]); return (a,b,c)
    for name,(kind,val) in sides.items():
        if fitted[name] is None:
            fitted[name]=proj_line(kind,val)
    def inter(l1,l2):
        a1,b1,c1=l1;a2,b2,c2=l2;D=a1*b2-a2*b1
        if abs(D)<1e-9:return None
        return np.array([(b1*c2-b2*c1)/D,(c1*a2-c2*a1)/D])
    TL=inter(fitted['top'],fitted['left']); TR=inter(fitted['top'],fitted['right'])
    BL=inter(fitted['bot'],fitted['left']); BR=inter(fitted['bot'],fitted['right'])
    if any(p is None for p in (TL,TR,BL,BR)): return None
    return np.float32([TL,TR,BL,BR])


def refine_boundary_iter(H, lp, bands=(24,16,11,8)):
    corners=None
    for b in bands:
        c=refine_boundary(H,lp,band=float(b))
        if c is None: break
        Hn=homography_from_corners(c)
        if Hn is None: break
        H=Hn; corners=c
    return corners,H

# === detection pipeline ===


# model lines in court space: each as ('v', x) or ('h', y)
MODEL_V = [('v', float(x)) for x in V_LINES]
MODEL_H = [('h', float(y)) for y in H_LINES]

def _largest_cc(mask):
    n, lab, stats, cent = cv2.connectedComponentsWithStats(mask, 8)
    if n <= 1:
        return mask
    big = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    return (lab == big).astype(np.uint8) * 255

def surface_mask(bgr):
    """Color-agnostic playing-surface mask (largest color-coherent region)."""
    h, w = bgr.shape[:2]
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2Lab)
    y0, y1 = int(0.55*h), int(0.80*h)
    x0, x1 = int(0.35*w), int(0.65*w)
    seed = lab[y0:y1, x0:x1].reshape(-1, 3).astype(np.float32)
    med = np.median(seed, axis=0)
    dist = np.linalg.norm(lab[:, :, 1:3].astype(np.float32) - med[1:3], axis=2)
    thr = max(18.0, 3.0 * np.std(seed[:, 1:3], axis=0).mean())
    mask = (dist < thr).astype(np.uint8) * 255
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k, iterations=2)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, k, iterations=1)
    mask = _largest_cc(mask)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE,
                            cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(25,25)), iterations=2)
    ff = mask.copy(); hh, ww = mask.shape
    m3 = np.zeros((hh+2, ww+2), np.uint8)
    cv2.floodFill(ff, m3, (0,0), 255)
    mask = mask | cv2.bitwise_not(ff)
    return mask


def surface_mask_tex(bgr):
    """Texture-gated variant: color-coherent AND smooth (drops busy banners)."""
    h, w = bgr.shape[:2]
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2Lab)
    y0, y1 = int(0.55*h), int(0.80*h)
    x0, x1 = int(0.35*w), int(0.65*w)
    seed = lab[y0:y1, x0:x1].reshape(-1, 3).astype(np.float32)
    med = np.median(seed, axis=0)
    dist = np.linalg.norm(lab[:, :, 1:3].astype(np.float32) - med[1:3], axis=2)
    thr = max(18.0, 3.0 * np.std(seed[:, 1:3], axis=0).mean())
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY).astype(np.float32)
    m1 = cv2.blur(gray, (25, 25)); m2 = cv2.blur(gray*gray, (25, 25))
    std = np.sqrt(np.maximum(m2 - m1*m1, 0))
    mask = ((dist < thr) & (std < 30)).astype(np.uint8) * 255
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k, iterations=2)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, k, iterations=1)
    mask = _largest_cc(mask)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE,
                            cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(31,31)), iterations=3)
    ff = mask.copy(); hh, ww = mask.shape
    m3 = np.zeros((hh+2, ww+2), np.uint8)
    cv2.floodFill(ff, m3, (0,0), 255)
    mask = mask | cv2.bitwise_not(ff)
    return mask

def _scene_chroma(bgr):
    """Median chroma magnitude of the central seed region. Low => near-grayscale
    scene where colour segmentation is unreliable."""
    h, w = bgr.shape[:2]
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2Lab)
    seed = lab[int(0.55*h):int(0.80*h), int(0.35*w):int(0.65*w), 1:3].astype(np.float32).reshape(-1, 2)
    return float(np.median(np.linalg.norm(seed - 128.0, axis=1)))

def surface_mask_lum(bgr):
    """Near-grayscale fallback: segment the surface by LIGHTNESS similarity to
    the central seed plus low local texture (court is smooth; crowd/banners are
    busy). Used when the scene has too little colour for chroma segmentation."""
    h, w = bgr.shape[:2]
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2Lab)
    L = lab[:, :, 0].astype(np.float32)
    y0, y1 = int(0.55*h), int(0.80*h)
    x0, x1 = int(0.35*w), int(0.65*w)
    seedL = L[y0:y1, x0:x1]
    medL = np.median(seedL)
    thrL = max(30.0, 2.5 * np.std(seedL))
    distL = np.abs(L - medL)
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY).astype(np.float32)
    m1 = cv2.blur(gray, (25, 25)); m2 = cv2.blur(gray*gray, (25, 25))
    std = np.sqrt(np.maximum(m2 - m1*m1, 0))
    mask = ((distL < thrL) & (std < 26)).astype(np.uint8) * 255
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k, iterations=2)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, k, iterations=1)
    mask = _largest_cc(mask)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE,
                            cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(31,31)), iterations=3)
    ff = mask.copy(); hh, ww = mask.shape
    m3 = np.zeros((hh+2, ww+2), np.uint8)
    cv2.floodFill(ff, m3, (0,0), 255)
    mask = mask | cv2.bitwise_not(ff)
    return mask

def line_pixels(bgr, roi):
    """Detect thin bright line pixels inside roi (color-agnostic)."""
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    g = gray.astype(np.int16)
    d = 7  # neighbor distance for prominence test
    # horizontal prominence (brighter than left & right neighbors) -> vertical-ish lines
    left  = np.roll(g, d, axis=1); right = np.roll(g, -d, axis=1)
    hp = (g - left > 18) & (g - right > 18)
    # vertical prominence -> horizontal-ish lines
    up    = np.roll(g, d, axis=0); down  = np.roll(g, -d, axis=0)
    vp = (g - up > 18) & (g - down > 18)
    bright = gray > 120
    lines = ((hp | vp) & bright).astype(np.uint8) * 255
    roi_e = cv2.erode(roi, cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(5,5)))
    lines = cv2.bitwise_and(lines, roi_e)
    return lines

def hough_lines(binary):
    segs = cv2.HoughLinesP(binary, 1, np.pi/180, threshold=60,
                           minLineLength=80, maxLineGap=30)
    out = []
    if segs is None:
        return out
    for s in segs:
        x1,y1,x2,y2 = s[0]
        ang = np.degrees(np.arctan2(y2-y1, x2-x1))
        out.append((x1,y1,x2,y2,ang,np.hypot(x2-x1,y2-y1)))
    return out

def _line_from_seg(x1,y1,x2,y2):
    # returns (a,b,c) with a*x+b*y+c=0, normalized
    a = y2-y1; b = x1-x2; c = -(a*x1+b*y1)
    n = np.hypot(a,b)+1e-9
    return a/n, b/n, c/n

def _intersect(l1, l2):
    a1,b1,c1 = l1; a2,b2,c2 = l2
    D = a1*b2 - a2*b1
    if abs(D) < 1e-9: return None
    x = (b1*c2 - b2*c1)/D
    y = (c1*a2 - c2*a1)/D
    return np.array([x,y])

def init_corners_from_lines(segs, shape):
    h, w = shape[:2]
    segs = sorted(segs, key=lambda z: -z[5])[:180]         # cap by length -> bounded time
    verts = [s for s in segs if abs(abs(s[4])-90) < 50]   # steep -> sidelines
    horis = [s for s in segs if abs(s[4]) < 40]            # shallow -> baselines
    if len(verts) < 2 or len(horis) < 2:
        return None
    vp_v, inv = _vp_vp_ransac(verts)
    vp_h, inh = _vp_vp_ransac(horis)
    if len(inv) < 2 or len(inh) < 2:
        return None
    verts_in = [verts[k] for k in inv]
    horis_in = [horis[k] for k in inh]
    yref = np.mean([ (s[1]+s[3])/2 for s in horis_in ])
    xref = np.mean([ (s[0]+s[2])/2 for s in verts_in ])
    def x_at(s, y):
        a,b,c = _vp_seg_line(*s[:4])
        if abs(a) < 1e-9: return (s[0]+s[2])/2
        return -(b*y + c)/a
    def y_at(s, x):
        a,b,c = _vp_seg_line(*s[:4])
        if abs(b) < 1e-9: return (s[1]+s[3])/2
        return -(a*x + c)/b
    left  = min(verts_in, key=lambda s: x_at(s, yref))
    right = max(verts_in, key=lambda s: x_at(s, yref))
    top   = min(horis_in, key=lambda s: y_at(s, xref))
    bot   = max(horis_in, key=lambda s: y_at(s, xref))
    Ll=_vp_seg_line(*left[:4]); Lr=_vp_seg_line(*right[:4])
    Lt=_vp_seg_line(*top[:4]);  Lb=_vp_seg_line(*bot[:4])
    TL=_vp_intersect(Lt,Ll); TR=_vp_intersect(Lt,Lr)
    BL=_vp_intersect(Lb,Ll); BR=_vp_intersect(Lb,Lr)
    if any(p is None for p in (TL,TR,BL,BR)):
        return None
    return np.float32([TL,TR,BL,BR])


def _cluster_family(segs_in, kind, ref, merge=14.0):
    """Cluster VP-inlier segments into distinct physical lines and fit each.
    kind 'v': position = x at y=ref; 'h': position = y at x=ref."""
    items = []
    for s in segs_in:
        a, b, c = _vp_seg_line(*s[:4])
        if kind == 'v':
            if abs(a) < 1e-6: continue
            pos = -(b*ref + c) / a
        else:
            if abs(b) < 1e-6: continue
            pos = -(a*ref + c) / b
        items.append((pos, s))
    if not items: return []
    items.sort(key=lambda z: z[0])
    clusters = [[items[0]]]
    for it in items[1:]:
        if abs(it[0] - clusters[-1][-1][0]) < merge:
            clusters[-1].append(it)
        else:
            clusters.append([it])
    out = []
    for cl in clusters:
        pts = []
        for _, s in cl:
            pts.append([s[0], s[1]]); pts.append([s[2], s[3]])
        pts = np.array(pts, np.float32)
        vx, vy, x0, y0 = cv2.fitLine(pts, cv2.DIST_L2, 0, 0.01, 0.01).ravel()
        a = vy; b = -vx; c = -(a*x0 + b*y0)
        out.append((float(np.mean([z[0] for z in cl])), (a, b, c)))
    return out


def grid_candidate(segs, dt, shape):
    """Cluster the court line families, then try every (left,right)x(top,bottom)
    combination and keep the quad whose FULL 12-line grid (incl. service/centre/
    singles lines) best matches the detected pixels -- i.e. confirm the corners
    using the inner lines rather than trusting the single most-extreme line."""
    import itertools
    segs = sorted(segs, key=lambda z: -z[5])[:200]
    verts = [s for s in segs if abs(abs(s[4]) - 90) < 50]
    horis = [s for s in segs if abs(s[4]) < 40]
    if len(verts) < 2 or len(horis) < 2:
        return None, -1.0
    _, inv = _vp_vp_ransac(verts)
    _, inh = _vp_vp_ransac(horis)
    # VP filtering rejects banner/crowd lines, but degenerates when a family is
    # near-parallel (low-tilt, near head-on camera). Fall back to all family
    # segments (already ROI-masked) when VP finds too few inliers.
    vin = [verts[k] for k in inv] if len(inv) >= 3 else verts
    hin = [horis[k] for k in inh] if len(inh) >= 3 else horis
    if len(vin) < 2 or len(hin) < 2:
        return None, -1.0
    yref = np.mean([(s[1]+s[3])/2 for s in hin])
    xref = np.mean([(s[0]+s[2])/2 for s in vin])
    Vc = _cluster_family(vin, 'v', yref)
    Hc = _cluster_family(hin, 'h', xref)
    if len(Vc) < 2 or len(Hc) < 2:
        return None, -1.0
    Vc = Vc[:10]; Hc = Hc[:12]
    def inter(l1, l2):
        a1, b1, c1 = l1; a2, b2, c2 = l2
        D = a1*b2 - a2*b1
        if abs(D) < 1e-9: return None
        return np.array([(b1*c2 - b2*c1)/D, (c1*a2 - c2*a1)/D])
    best = -1.0; bestC = None
    for li, ri in itertools.combinations(range(len(Vc)), 2):
        L = Vc[li][1]; R = Vc[ri][1]
        for ti, bi in itertools.combinations(range(len(Hc)), 2):
            T = Hc[ti][1]; B = Hc[bi][1]
            TL = inter(T, L); TR = inter(T, R); BL = inter(B, L); BR = inter(B, R)
            if any(p is None for p in (TL, TR, BL, BR)): continue
            corners = np.float32([TL, TR, BL, BR])
            H = homography_from_corners(corners)
            if H is None or not _valid_H(H, shape): continue
            cov = coverage_score(H, dt, shape)
            if cov > best:
                best = cov; bestC = corners
    return bestC, best


def init_corners_from_mask(roi):
    cont,_ = cv2.findContours(roi, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not cont: return None
    c = max(cont, key=cv2.contourArea)
    hull = cv2.convexHull(c)
    peri = cv2.arcLength(hull, True)
    approx = cv2.approxPolyDP(hull, 0.02*peri, True)
    if len(approx) < 4:
        return None
    pts = approx.reshape(-1,2).astype(np.float32)
    # pick 4 extreme corners by (x+y),(x-y)
    s = pts.sum(1); d = pts[:,0]-pts[:,1]
    TL = pts[np.argmin(s)]; BR = pts[np.argmax(s)]
    TR = pts[np.argmax(d)]; BL = pts[np.argmin(d)]
    return np.float32([TL,TR,BL,BR])

def _project_model_lines(H):
    """Return list of (kind, val, (a,b,c)) image-space lines for the 12 grid lines."""
    out = []
    for kind, val in MODEL_V:
        p1 = H @ np.array([val, H_LINES[0], 1.0]); p1 = p1[:2]/p1[2]
        p2 = H @ np.array([val, H_LINES[-1],1.0]); p2 = p2[:2]/p2[2]
        out.append((_line_from_seg(p1[0],p1[1],p2[0],p2[1])))
    for kind, val in MODEL_H:
        p1 = H @ np.array([V_LINES[0], val, 1.0]); p1 = p1[:2]/p1[2]
        p2 = H @ np.array([V_LINES[-1],val, 1.0]); p2 = p2[:2]/p2[2]
        out.append((_line_from_seg(p1[0],p1[1],p2[0],p2[1])))
    return out

def refine_icl(H, wpts, iters=10):
    if wpts.shape[0] > 4000:
        idx = np.random.RandomState(0).choice(wpts.shape[0],4000,replace=False)
        wpts = wpts[idx]
    N = wpts.shape[0]
    # candidate court coords depend on cc_ each iter
    for it in range(iters):
        thr = max(5.0, 26.0 - 2.2*it)
        Hinv = np.linalg.inv(H)
        ph = np.hstack([wpts, np.ones((N,1))])
        cc_ = (Hinv @ ph.T).T
        cc_ = cc_[:,:2]/cc_[:,2:3]           # N x 2 (cx,cy)
        cx = cc_[:,0]; cy = cc_[:,1]
        # build candidates: 5 vertical (x=V, y=cy) + 7 horizontal (x=cx, y=H)
        cand = np.zeros((N, 12, 2), np.float64)
        for k,x in enumerate(V_LINES):
            cand[:,k,0]=x; cand[:,k,1]=cy
        for k,y in enumerate(H_LINES):
            cand[:,5+k,0]=cx; cand[:,5+k,1]=y
        flat = cand.reshape(-1,2)
        fh = np.hstack([flat, np.ones((flat.shape[0],1))])
        proj = (H @ fh.T).T
        proj = proj[:,:2]/proj[:,2:3]
        proj = proj.reshape(N,12,2)
        d2 = ((proj[:,:,0]-wpts[:,None,0])**2 + (proj[:,:,1]-wpts[:,None,1])**2)
        best = np.argmin(d2, axis=1)
        bd2 = d2[np.arange(N), best]
        keep = bd2 < thr*thr
        if keep.sum() < 12: break
        src = cand[np.arange(N), best][keep]
        dst = wpts[keep]
        Hn,_ = cv2.findHomography(np.float32(src), np.float32(dst), cv2.RANSAC, 4.0)
        if Hn is None: break
        H = Hn
    return H




def _poly_overlap(H, mask):
    """Fraction of the projected court quad that lies inside mask."""
    try:
        c = project_16(H)[:4]  # TL,TR,BL,BR
    except Exception:
        return 0.0
    quad = np.array([c[0], c[1], c[3], c[2]], np.int32)  # TL,TR,BR,BL
    h, w = mask.shape
    canvas = np.zeros((h, w), np.uint8)
    cv2.fillConvexPoly(canvas, quad, 255)
    area = np.count_nonzero(canvas)
    if area < 100: return 0.0
    inter = np.count_nonzero(cv2.bitwise_and(canvas, mask))
    return inter / area

def _dist_transform(lp):
    inv = cv2.bitwise_not(lp)
    return cv2.distanceTransform(inv, cv2.DIST_L2, 3)

def coverage_score(H, dt, shape, tol=4.0, n=80):
    """Fraction of each of the 12 model lines supported by nearby line pixels.
    Uses a distance transform of the line image. Returns mean line coverage.
    Penalizes homographies whose model lines fall in empty (banner/void) areas."""
    h, w = shape[:2]
    covs = []
    allc = [('v', float(x)) for x in V_LINES] + [('h', float(y)) for y in H_LINES]
    for kind, val in allc:
        if kind == 'v':
            ys = np.linspace(H_LINES[0], H_LINES[-1], n)
            pts = np.stack([np.full(n, val), ys, np.ones(n)], 1)
        else:
            xs = np.linspace(V_LINES[0], V_LINES[-1], n)
            pts = np.stack([xs, np.full(n, val), np.ones(n)], 1)
        P = (H @ pts.T).T
        P = P[:, :2] / P[:, 2:3]
        xi = np.round(P[:, 0]).astype(int); yi = np.round(P[:, 1]).astype(int)
        inb = (xi >= 0) & (xi < w) & (yi >= 0) & (yi < h)
        if inb.sum() < n * 0.4:
            covs.append(0.0); continue
        d = dt[yi[inb], xi[inb]]
        covs.append(float((d < tol).mean()))
    covs = np.array(covs)
    # mean coverage, but require breadth: penalize if many lines unsupported
    return float(covs.mean() * (covs > 0.3).mean())

def chamfer_score(H, wpts):
    lines = _project_model_lines(H)
    if wpts.shape[0] > 3000:
        idx = np.random.RandomState(1).choice(wpts.shape[0],3000,replace=False)
        wpts = wpts[idx]
    dmin = np.full(wpts.shape[0], 1e9)
    for a,b,c in lines:
        d = np.abs(a*wpts[:,0]+b*wpts[:,1]+c)
        dmin = np.minimum(dmin, d)
    inl = (dmin < 5).mean()
    return inl, dmin.mean()

def detect_core(bgr, return_debug=False):
    t0=time.time()
    roi_c = surface_mask(bgr)
    roi_t = surface_mask_tex(bgr)
    rois = [('color', roi_c), ('tex', roi_t)]
    if _scene_chroma(bgr) < 20.0:          # near-grayscale fallback
        roi_l = surface_mask_lum(bgr)
        # under heavy desaturation the chroma mask degenerates to the whole
        # frame -> drop such degenerate ROIs and rely on the luminance mask.
        rois = [(n, r) for n, r in rois if (r > 0).mean() < 0.85]
        rois.append(('lum', roi_l))
        roi_t = roi_l                      # use luminance mask for overlap prior
    grid_cands=[]; lines_cands=[]; mask_cands=[]
    for rname, roi in rois:
        lp = line_pixels(bgr, roi)
        if np.count_nonzero(lp) < 200:
            continue
        dt = _dist_transform(lp)
        segs = hough_lines(lp)
        cg, _cv = grid_candidate(segs, dt, bgr.shape)     # suggestion-2 primary
        if cg is not None: grid_cands.append((rname+'-grid', cg, lp))
        c1 = init_corners_from_lines(segs, bgr.shape)
        if c1 is not None: lines_cands.append((rname+'-lines', c1, lp))
        c2 = init_corners_from_mask(roi)
        if c2 is not None: mask_cands.append((rname+'-mask', c2, lp))

    def eval_pool(pool):
        bH=None; bsc=-1; bdbg=None
        for nm, c, lp in pool:
            H0=homography_from_corners(c)
            if H0 is None: continue
            wy,wx=np.where(lp>0); wpts=np.column_stack([wx,wy]).astype(np.float32)
            H=H0
            if wpts.shape[0]>=30:
                H=refine_icl(H0, wpts, iters=3)
            dt=_dist_transform(lp)
            if not _valid_H(H, bgr.shape):
                H=H0
            cov = coverage_score(H, dt, bgr.shape) if _valid_H(H, bgr.shape) else -1.0
            # boundary refine, but KEEP it only if grid coverage does not drop
            try:
                cB,HB=refine_boundary_iter(H, lp)
                if HB is not None and _valid_H(HB, bgr.shape):
                    covB=coverage_score(HB, dt, bgr.shape)
                    if covB >= cov - 0.01:
                        H=HB; cov=covB
            except Exception:
                pass
            if not _valid_H(H, bgr.shape):
                continue
            ov=_poly_overlap(H, roi_t)
            sc=cov*(0.8+0.2*ov)
            if sc>bsc:
                bsc=sc; bH=H; bdbg={'cand':nm,'score':sc,'cov':cov,'ov':ov}
        return bH,bsc,bdbg

    best_H,best,best_dbg = eval_pool(grid_cands + lines_cands)
    if best_H is None:
        best_H,best,best_dbg = eval_pool(mask_cands)
    if best_H is None:
        return (None, {}) if return_debug else None
    pts16=project_16(best_H)
    dbg={'time':time.time()-t0,'score':best,'H':best_H}
    if best_dbg: dbg.update(best_dbg)
    return (pts16, dbg) if return_debug else pts16


def _valid_H(H, shape):
    h,w=shape[:2]
    try:
        c=project_16(H)[:4]
    except Exception:
        return False
    if not np.all(np.isfinite(c)): return False
    if np.any(c< -0.5*w) or np.any(c[:,0]>1.5*w) or np.any(c[:,1]>1.5*h): return False
    TL,TR,BL,BR=c
    wtop=np.linalg.norm(TR-TL); wbot=np.linalg.norm(BR-BL)
    hl=np.linalg.norm(BL-TL); hr=np.linalg.norm(BR-TR)
    if min(wtop,wbot,hl,hr)<0.05*w: return False
    if wbot < wtop*0.6: return False
    return True


def detect(bgr, return_debug=False, work_width=1920):
    """Public entry: run detection at a reduced working width for speed and
    noise robustness, then rescale key points back to the input resolution."""
    H0, W0 = bgr.shape[:2]
    # Normalise ANY input to the working width (up- or down-scale). Keeping the
    # processing scale fixed makes the pixel-tuned parameters resolution-agnostic,
    # so low-res captures work as well as high-res ones.
    s = work_width / float(W0)
    if abs(s - 1.0) < 0.05:
        s = 1.0; small = bgr
    else:
        interp = cv2.INTER_AREA if s < 1.0 else cv2.INTER_CUBIC
        small = cv2.resize(bgr, (work_width, int(round(H0 * s))), interpolation=interp)
    res = detect_core(small, return_debug=return_debug)
    if return_debug:
        pts, dbg = res
    else:
        pts, dbg = res, None
    if pts is None:
        return (None, dbg) if return_debug else None
    pts = np.array(pts) / s
    return (pts, dbg) if return_debug else pts


def sample_frames(video_path, n_frames=40, max_frames=60):
    """Uniformly sample up to n_frames decoded frames from a video (fast
    grab/retrieve sequential decode). Returns a list of BGR frames."""
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return []
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if total < 2:
        ok, f = cap.read(); cap.release()
        return [f] if ok else []
    n = min(max(3, n_frames), max_frames, total)
    wanted = sorted(set(int(i) for i in np.linspace(0, total - 1, n)))
    ws = set(wanted); last = wanted[-1]
    frames = []; cur = 0
    while cur <= last:
        if not cap.grab():
            break
        if cur in ws:
            ok, fr = cap.retrieve()
            if ok:
                frames.append(fr)
        cur += 1
    cap.release()
    return frames


def composite_median(frames):
    """Pixel-wise median over frames. The static court survives; moving players
    (different position each frame) are averaged out and disappear."""
    return np.median(np.stack(frames), axis=0).astype(np.uint8)


def detect_video(video_path, n_frames=40, return_debug=False):
    """Detect the court from a video. Frames are median-composited first so that
    moving players vanish and the (static) court lines are clean -> far more
    robust than any single frame. Falls back to the middle frame if compositing
    is not possible. Returns 16 key points (or None)."""
    frames = sample_frames(video_path, n_frames)
    if not frames:
        return (None, {'error': 'cannot read video'}) if return_debug else None
    img = composite_median(frames) if len(frames) >= 3 else frames[len(frames)//2]
    return detect(img, return_debug=return_debug)


def detect_from_frame(frame):
    """Drop-in replacement for the legacy r.detect_from_frame().
    Returns (points, info) where points is a list of 16 (x, y) tuples in the
    input image's pixel coordinates, or None on failure."""
    try:
        res = detect(frame, return_debug=True)
    except Exception as e:
        return None, {'error': str(e)}
    pts, dbg = res
    if pts is None:
        return None, (dbg or {})
    return [(float(x), float(y)) for x, y in pts], (dbg or {})
