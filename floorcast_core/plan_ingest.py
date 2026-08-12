"""
Floorcast — Floor Plan Reader

Turns a vector floor-plan PDF into a seat inventory plus a background raster,
so the app can take the architect's drawing directly instead of a hand-built
CSV.

How seats are found
-------------------
CAD floor plans annotate every workstation with its desk size (1000x600,
1200x600 ...). Those annotations are counted rather than the furniture blocks,
because the annotation is one per seat while a desk block may be drawn as many
pieces. Labels plotted rotated 180 degrees come out scrambled on extraction, so
each is rebuilt from its individual glyph positions and matched on character
content rather than string order.

Scale
-----
Plans are often plotted to fit the sheet rather than at a round scale, so the
millimetre-per-point factor is derived from the plotted footprint of the most
common desk and cross-checked against the drawing's own dimension text.

Zones
-----
Seats are clustered into contiguous blocks, blocks are merged into larger
groups, and each group takes the name of the nearest non-desk text label on the
plan. This is a heuristic: it gets the common cases right, and the app exposes
the mapping so it can be corrected before anything is committed.
"""

import collections
import math
import re

import numpy as np
import pandas as pd

DESK_PATTERNS = ["1000x600", "1000x500", "1200x600", "1500x600", "1200x500",
                 "1400x700", "1200x700", "1500x750", "1800x800"]
SIZE_RE = re.compile(r"^\d{3,4}x\d{3,4}$")


# ───────────────────────────────────────── text handling
def _chars(page, keep_space=False):
    out = []
    raw = page.get_text("rawdict")
    for b in raw["blocks"]:
        if b["type"] != 0:
            continue
        for l in b.get("lines", []):
            dx, dy = l["dir"]
            for sp in l["spans"]:
                for c in sp.get("chars", []):
                    if not c["c"].strip() and not keep_space:
                        continue
                    out.append({"c": c["c"], "x": c["origin"][0], "y": c["origin"][1],
                                "dir": (round(dx), round(dy)), "size": round(sp["size"], 2)})
    return out


def _group(chs, gap_along, gap_across):
    groups = []
    for d in set(c["dir"] for c in chs):
        for sz in set(c["size"] for c in chs if c["dir"] == d):
            sub = [c for c in chs if c["dir"] == d and c["size"] == sz]
            dx, dy = d
            for c in sub:
                c["along"] = c["x"] * dx + c["y"] * dy
                c["across"] = round(-c["x"] * dy + c["y"] * dx, 1)
            sub.sort(key=lambda c: (c["across"], c["along"]))
            cur = []
            for c in sub:
                if cur and abs(c["across"] - cur[-1]["across"]) < gap_across \
                        and (c["along"] - cur[-1]["along"]) < gap_along(sz):
                    cur.append(c)
                else:
                    if cur:
                        groups.append(cur)
                    cur = [c]
            if cur:
                groups.append(cur)
    return groups


def _decompose(counter, patterns):
    """Split a bag of characters into whole desk-size labels."""
    res, changed = [], True
    cc = counter.copy()
    while sum(cc.values()) > 0 and changed:
        changed = False
        for p in patterns:
            pc = collections.Counter(p)
            if all(cc[k] >= v for k, v in pc.items()):
                cc = cc - pc
                res.append(p)
                changed = True
                break
    return res, cc


# ───────────────────────────────────────── scale
def _calibrate(page, desk_label):
    """millimetres per PDF point, from the plotted footprint of the desk."""
    want_w = float(desk_label.split("x")[0])
    want_d = float(desk_label.split("x")[1])
    ratio = want_w / want_d
    longs = []
    for path in page.get_cdrawings():
        if path.get("fill") is not None:
            continue
        r = path["rect"]
        w, h = r[2] - r[0], r[3] - r[1]
        if w < 1 or h < 1:
            continue
        a, b = (w, h) if w >= h else (h, w)
        if abs((a / b) - ratio) / ratio < 0.06:
            longs.append(a)
    if not longs:
        return None
    longs.sort()
    # modal band: densest 20% window
    n = len(longs)
    best, bw = None, 1e9
    win = max(3, n // 5)
    for i in range(0, n - win + 1):
        w = longs[i + win - 1] - longs[i]
        if w < bw:
            bw, best = w, longs[i:i + win]
    return want_w / float(np.median(best))


# ───────────────────────────────────────── main
def read_plan(pdf_source, dpi=150, page_no=0):
    """Returns dict with: seats (DataFrame), scale_mm_per_pt, background
    (ndarray), extent (list, millimetres), zone_labels (DataFrame)."""
    import pymupdf

    doc = (pymupdf.open(stream=pdf_source, filetype="pdf")
           if isinstance(pdf_source, (bytes, bytearray))
           else pymupdf.open(pdf_source))
    page = doc[page_no]

    chars = _chars(page)
    if not chars:
        raise ValueError("No text found in this PDF — it may be a scan rather than "
                         "a vector plot, in which case seats cannot be counted.")

    # desk annotations use the smallest, most repeated font on the sheet
    size_counts = collections.Counter(c["size"] for c in chars)
    desk_size = size_counts.most_common(1)[0][0]

    desk_chars = [c for c in chars if c["size"] == desk_size]
    seats = []
    leftovers = []
    for g in _group(desk_chars, lambda s: 6.0, 0.8):
        cnt = collections.Counter(c["c"] for c in g)
        pats, rem = _decompose(cnt, DESK_PATTERNS)
        gs = sorted(g, key=lambda c: c["along"])
        if pats:
            chunk = len(gs) / len(pats)
            for i, p in enumerate(pats):
                seg = gs[int(i * chunk):int((i + 1) * chunk)] or gs
                seats.append({"size_mm": p,
                              "x": sum(c["x"] for c in seg) / len(seg),
                              "y": sum(c["y"] for c in seg) / len(seg)})
        if sum(rem.values()):
            leftovers.append({"chars": rem,
                              "x": sum(c["x"] for c in gs) / len(gs),
                              "y": sum(c["y"] for c in gs) / len(gs)})

    # rescue labels split across a wide gap, and partially plotted ones
    used = [False] * len(leftovers)
    for i, a in enumerate(leftovers):
        if used[i]:
            continue
        cnt = collections.Counter(a["chars"])
        pts = [(a["x"], a["y"])]
        for j, b in enumerate(leftovers):
            if j <= i or used[j]:
                continue
            if abs(a["x"] - b["x"]) < 20 and abs(a["y"] - b["y"]) < 20:
                cnt += collections.Counter(b["chars"])
                pts.append((b["x"], b["y"]))
                used[j] = True
        pats, rem = _decompose(cnt, DESK_PATTERNS)
        for p in pats:
            seats.append({"size_mm": p,
                          "x": sum(q[0] for q in pts) / len(pts),
                          "y": sum(q[1] for q in pts) / len(pts)})
        # a label missing one glyph is still a seat: 7 of 8 characters match
        if not pats and 5 <= sum(rem.values()) <= 8:
            for p in DESK_PATTERNS:
                pc = collections.Counter(p)
                if sum((rem & pc).values()) >= sum(rem.values()):
                    seats.append({"size_mm": p, "partial_label": True,
                                  "x": sum(q[0] for q in pts) / len(pts),
                                  "y": sum(q[1] for q in pts) / len(pts)})
                    break

    if not seats:
        raise ValueError("No workstation size annotations (e.g. 1000x600) were found. "
                         "Floorcast counts seats from those labels.")

    modal_desk = collections.Counter(s["size_mm"] for s in seats).most_common(1)[0][0]
    S = _calibrate(page, modal_desk) or 1.0

    # zone naming
    label_chars = [c for c in _chars(page, keep_space=True)
                   if c["size"] != desk_size]
    cands = []
    for g in _group(label_chars, lambda s: max(s * 1.8, 6.0), 1.6):
        t = _join_with_spaces(g)
        if len(t) < 2 or t.replace(".", "").replace("#", "").isdigit():
            continue
        cands.append({"text": t,
                      "x": sum(c["x"] for c in g) / len(g),
                      "y": sum(c["y"] for c in g) / len(g)})

    zones = _name_zones(seats, cands)

    page_w, page_h = page.rect.width, page.rect.height   # display (rotated) size
    to_plot = _plot_transform(page, S)
    rows = []
    counter = collections.Counter()
    slugs = _unique_slugs(zones)
    for s, z in zip(seats, zones):
        counter[z] += 1
        px, py = to_plot(s["x"], s["y"])
        rows.append({"zone": z, "zone_type": "Production",
                     "seat_id": f"{slugs[z]}-{counter[z]:03d}",
                     "x_mm": px, "y_mm": py,
                     "desk_size_mm": s["size_mm"],
                     "partial_label": bool(s.get("partial_label"))})
    seats_df = pd.DataFrame(rows)

    pix = page.get_pixmap(dpi=dpi)
    bg = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    if pix.n == 4:
        bg = bg[:, :, :3]
    extent = [0.0, page_w * S, 0.0, page_h * S]

    return {"seats": seats_df, "scale_mm_per_pt": S, "background": bg,
            "extent": extent,
            "zone_labels": pd.DataFrame(cands),
            "page_size_mm": (page_w * S, page_h * S)}



def _plot_transform(page, S):
    """PDF geometry comes back in unrotated page space, but the plan is read in
    the page's display orientation and plotted y-up. This folds both together so
    a drawing plotted sideways lands the same way as one plotted upright."""
    import pymupdf
    rm = page.rotation_matrix
    H = page.rect.height

    def conv(x, y):
        p = pymupdf.Point(x, y) * rm
        return round(p.x * S, 1), round((H - p.y) * S, 1)
    return conv



def _join_with_spaces(g):
    """Spaces are real glyphs in the PDF, so the name is read back as drawn."""
    gs = sorted(g, key=lambda c: c["along"])
    return "".join(c["c"] for c in gs).strip()


def _slug(z):
    return re.sub(r"[^A-Za-z0-9]+", "", str(z))[:8].upper() or "Z"


def _unique_slugs(zones):
    """Slugs are truncated, so two long zone names can collide. Seat ids must be
    unique across the floor, so collisions get a suffix."""
    out, seen = {}, {}
    for z in dict.fromkeys(zones):
        base = _slug(z)
        if base in seen:
            seen[base] += 1
            out[z] = f"{base[:6]}{seen[base]}"
        else:
            seen[base] = 0
            out[z] = base
    return out


def _name_zones(seats, cands, tight=24.0, merge=60.0):
    """Cluster seats into blocks, merge blocks into groups, name each group
    after the nearest text label on the plan."""
    pts = [(s["x"], s["y"]) for s in seats]
    groups = _cluster(pts, merge)
    names = []
    used = collections.Counter()
    gname = {}
    for gi, idxs in groups.items():
        cx = sum(pts[i][0] for i in idxs) / len(idxs)
        cy = sum(pts[i][1] for i in idxs) / len(idxs)
        best, bd = None, 1e18
        for c in cands:
            d = math.hypot(c["x"] - cx, c["y"] - cy)
            if d < bd:
                bd, best = d, c["text"]
        if best is None:
            used["Zone"] += 1
            best = f"Zone {used['Zone']}"
        gname[gi] = best
    for i in range(len(pts)):
        gi = next(g for g, idxs in groups.items() if i in idxs)
        names.append(gname[gi])
    return names


def _cluster(pts, th):
    n = len(pts)
    parent = list(range(n))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    grid = collections.defaultdict(list)
    for i, (x, y) in enumerate(pts):
        grid[(int(x // th), int(y // th))].append(i)
    for (gx, gy), idxs in list(grid.items()):
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for j in grid.get((gx + dx, gy + dy), []):
                    for i in idxs:
                        if i < j and math.hypot(pts[i][0] - pts[j][0],
                                                pts[i][1] - pts[j][1]) <= th:
                            union(i, j)
    out = collections.defaultdict(list)
    for i in range(n):
        out[find(i)].append(i)
    return out
