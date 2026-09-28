#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["python-pptx>=1.0", "resvg-py>=0.2"]
# ///
"""azurify.py が描いた SVG の配置を読み取り、編集できる draw.io / PowerPoint に書き出す。

使い方:
    uv run export.py diagram.svg --icons azure.json --drawio --pptx

配置 (座標) は Mermaid のレイアウトをそのまま使う。矢印は座標ではなく接続元・接続先の図形に
吸着させるので、draw.io / PowerPoint 上で図形を動かしても矢印が付いてくる。
"""

import argparse
import base64
import json
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from azurify import group_icon, icon_svg  # noqa: E402
from route import Router, hits, rects_of, violations  # noqa: E402

NS = {"s": "http://www.w3.org/2000/svg", "x": "http://www.w3.org/1999/xhtml"}
TRANSLATE = re.compile(r"translate\(\s*([-\d.]+)[,\s]+([-\d.]+)\s*\)")
ICON_DEF = re.compile(r'^\s*([\w-]+)@\{\s*icon:\s*"azure:([\w-]+)"')
DIRECTIVE = re.compile(r"^\s*%%\s*azure:\s*([\w-]+)\s*=\s*([\w-]+)\s*$")


@dataclass
class Box:
    id: str
    x: float
    y: float
    w: float
    h: float
    label: str = ""
    icon: str | None = None          # アイコン名 (ノード) またはグループの種類アイコン
    label_box: tuple[float, float, float, float] | None = None  # アイコン下のラベル (x, y, w, h)

    @property
    def cx(self):
        return self.x + self.w / 2

    @property
    def cy(self):
        return self.y + self.h / 2


@dataclass
class Edge:
    src: str
    dst: str
    label: str = ""
    dashed: bool = False


@dataclass
class Diagram:
    width: float
    height: float
    groups: list[Box] = field(default_factory=list)
    nodes: list[Box] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)


def text_of(el) -> str:
    """foreignObject 内の HTML ラベルを、<br> を改行にしたプレーンテキストにする。"""
    if el is None:
        return ""
    parts = []
    for node in el.iter():
        tag = node.tag.split("}")[-1]
        if tag == "br":
            parts.append("\n")
        if node.text and tag not in ("style", "img"):
            parts.append(node.text)
        if node.tail and node is not el:
            parts.append(node.tail)
    return re.sub(r"[ \t]+", " ", "".join(parts)).strip()


def translate(el) -> tuple[float, float]:
    m = TRANSLATE.search(el.get("transform", "") or "")
    return (float(m.group(1)), float(m.group(2))) if m else (0.0, 0.0)


def offset_to(ancestor, target) -> tuple[float, float]:
    """ancestor の直下から target 自身までの translate を足し合わせた移動量 (ancestor 自身は含めない)。"""
    def walk(el, ox, oy):
        for child in el:
            cx, cy = translate(child)
            if child is target:
                return ox + cx, oy + cy
            found = walk(child, ox + cx, oy + cy)
            if found:
                return found
        return None
    return walk(ancestor, 0.0, 0.0) or (0.0, 0.0)


def parse(svg_path: Path, mmd_path: Path) -> Diagram:
    root = ET.parse(svg_path).getroot()
    vx, vy, vw, vh = (float(v) for v in root.get("viewBox").split())
    prefix = root.get("id", "my-svg") + "-"
    src = mmd_path.read_text().splitlines()
    icons = {m.group(1): m.group(2) for line in src if (m := ICON_DEF.match(line))}
    forced = {m.group(1): m.group(2) for line in src if (m := DIRECTIVE.match(line))}
    d = Diagram(vw, vh)

    for g in root.iterfind(".//s:g[@class='cluster']", NS):
        r = g.find("s:rect", NS)
        gid = g.get("id", "").removeprefix(prefix)
        title = text_of(g.find(".//x:span", NS)) or text_of(g.find(".//x:p", NS))
        slug = forced.get(gid) or group_icon(title)
        d.groups.append(Box(gid, float(r.get("x")) - vx, float(r.get("y")) - vy, float(r.get("width")),
                            float(r.get("height")), title, slug))

    for g in root.iterfind(".//s:g[@id]", NS):
        m = re.fullmatch(re.escape(prefix) + r"flowchart-(.+)-\d+", g.get("id"))
        if not m:
            continue
        nid = m.group(1)
        tx, ty = translate(g)
        label = text_of(g.find(".//x:p", NS)) or text_of(g.find(".//x:span", NS))
        inner = g.find(".//s:svg", NS)
        if nid in icons and inner is not None:
            ox, oy = offset_to(g, inner)
            w, h = float(inner.get("width")), float(inner.get("height"))
            lg, fo = g.find(".//s:g[@class='label']", NS), g.find(".//s:foreignObject", NS)
            lx, ly = offset_to(g, lg) if lg is not None else (-w / 2, h)
            lw, lh = (float(fo.get("width")), float(fo.get("height"))) if fo is not None else (w, 20)
            d.nodes.append(Box(nid, tx + ox - vx, ty + oy - vy, w, h, label, icons[nid],
                               (tx + lx - vx, ty + ly - vy, lw, lh)))
        else:
            fo = g.find(".//s:foreignObject", NS)
            fw = float(fo.get("width", 120)) if fo is not None else 120
            fh = float(fo.get("height", 24)) if fo is not None else 24
            w, h = fw + 32, fh + 24
            d.nodes.append(Box(nid, tx - w / 2 - vx, ty - h / 2 - vy, w, h, label))

    if not d.nodes:
        sys.exit(f"no flowchart nodes found in {svg_path}")
    ids = {n.id for n in d.nodes}
    labels = {g.get("data-id"): text_of(g) for g in root.iterfind(".//s:g[@data-id]", NS)}
    for p in root.iterfind(".//s:path[@data-points]", NS):
        eid = p.get("id", "").removeprefix(prefix)
        ends = edge_ends(eid, ids)
        if not ends:
            print(f"skip edge {eid}: endpoints not found", file=sys.stderr)
            continue
        cls = p.get("class", "")
        d.edges.append(Edge(*ends, labels.get(eid, ""), "dotted" in cls or "dashed" in cls))
    return d


def edge_ends(eid: str, ids: set) -> tuple[str, str] | None:
    """Mermaid の矢印 id 'L_<接続元>_<接続先>_<連番>' を、既知のノード id で区切って両端を得る。"""
    m = re.fullmatch(r"L_(.+)_\d+", eid)
    if not m:
        return None
    body = m.group(1)
    for i in range(1, len(body)):
        if body[i] == "_" and body[:i] in ids and body[i + 1:] in ids:
            return body[:i], body[i + 1:]
    return None


@dataclass
class Route:
    edge: Edge
    src_side: str
    dst_side: str
    pts: list[tuple[float, float]]
    label_xy: tuple[float, float, float] | None = None  # ラベル中心 x, y と経路長に対する位置の割合

    def candidates(self):
        """ラベルを置ける経路上の点を、長い区間の中央に近い順に返す (x, y, 経路長に対する割合)。"""
        segs = list(zip(self.pts, self.pts[1:], strict=False))
        lens = [abs(b[0] - a[0]) + abs(b[1] - a[1]) for a, b in segs]
        total = max(sum(lens), 1)
        out = []
        for i, ((ax, ay), (bx, by)) in enumerate(segs):
            for k in range(1, int(lens[i] // 10)):
                t = k * 10 / lens[i]
                out.append((abs(t - 0.5) - lens[i] / total, ax + (bx - ax) * t, ay + (by - ay) * t,
                            (sum(lens[:i]) + lens[i] * t) / total))
        return [(x, y, f) for _, x, y, f in sorted(out)]


def label_rect(text: str, x: float, y: float) -> tuple[float, float, float, float]:
    lines = text.split("\n") or [""]
    # why: 文字幅は 11px 表示で 1 字 6.5px 程度。周りに 8px の余白を取り、他の文字と接しないようにする
    w, h = max(len(line) for line in lines) * 6.5 + 16, len(lines) * 15 + 12
    return (x - w / 2, y - h / 2, x + w / 2, y + h / 2)


def route_all(d: Diagram) -> list[Route]:
    """全矢印を、ノードを避ける経路で引く。ノード・ラベルを突き抜ける経路があれば止める。"""
    titles = {f"title:{g.id}": title_rect(g) for g in d.groups}
    router = Router(d.nodes, d.width, d.height, titles)
    # why: 長い矢印ほど迂回の選択肢が多いので、短い矢印から先に場所を確保させる
    order = sorted(d.edges, key=lambda e: abs(by_id(d, e.src).cx - by_id(d, e.dst).cx)
                   + abs(by_id(d, e.src).cy - by_id(d, e.dst).cy))
    routes = [Route(e, *router.route(e.src, e.dst)) for e in order]
    problems = violations([((r.edge.src, r.edge.dst), r.pts) for r in routes], d.nodes)
    problems += [f"{r.edge.src} -> {r.edge.dst} crosses the title of group {key[6:]}" for r in routes
                 for key, rect in titles.items() if any(hits(s, rect, 2) for s in zip(r.pts, r.pts[1:], strict=False))]
    taken = [r for n in d.nodes for r in rects_of(n)] + list(titles.values())
    for r in routes:
        if not r.edge.label:
            continue
        # why: 経路上の候補点のうち、アイコン・ラベル・タイトル・既存の矢印ラベルと重ならない最初の点に置く
        spot = next(((x, y, f) for x, y, f in r.candidates()
                     if not any(overlap(label_rect(r.edge.label, x, y), t) for t in taken)), None)
        if spot is None:
            problems.append(f"label '{r.edge.label}' ({r.edge.src} -> {r.edge.dst}) has no free spot on its route")
            continue
        r.label_xy = spot
        taken.append(label_rect(r.edge.label, spot[0], spot[1]))
    if problems:
        sys.exit("routing check failed:\n  " + "\n  ".join(problems))
    return routes


def title_rect(g: Box) -> tuple[float, float, float, float]:
    """グループ左上のアイコンとタイトル文字が占める範囲 (draw.io / PowerPoint の配置に合わせる)。"""
    return (g.x, g.y, g.x + (30 if g.icon else 8) + len(g.label) * 6.8 + 8, g.y + 24)


def overlap(a, b) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def by_id(d: Diagram, nid: str) -> Box:
    return next(n for n in d.nodes if n.id == nid)


# ---------------------------------------------------------------- draw.io
def to_drawio(d: Diagram, catalog: dict) -> str:
    def data_uri(slug):
        # why: draw.io の style 内では ';' が区切りなので、'data:image/svg+xml,<base64>' 形式にする
        return "data:image/svg+xml," + base64.b64encode(icon_svg(catalog, slug).encode()).decode()

    root = ET.Element("root")
    ET.SubElement(root, "mxCell", id="0")
    ET.SubElement(root, "mxCell", id="1", parent="0")

    def vertex(cid, value, style, x, y, w, h):
        c = ET.SubElement(root, "mxCell", id=cid, value=value, style=style, vertex="1", parent="1")
        ET.SubElement(c, "mxGeometry", x=f"{x:.1f}", y=f"{y:.1f}", width=f"{w:.1f}", height=f"{h:.1f}",
                      attrib={"as": "geometry"})

    for g in sorted(d.groups, key=lambda g: -g.w * g.h):
        vertex(f"g-{g.id}", g.label, "rounded=1;arcSize=2;absoluteArcSize=1;whiteSpace=wrap;html=1;fillColor=#F5F9FD;"
               "strokeColor=#0078D4;verticalAlign=top;align=left;spacingTop=0;fontStyle=1;fontColor=#0F3D6E;fontSize=11;"
               + ("spacingLeft=30;" if g.icon else "spacingLeft=8;"), g.x, g.y, g.w, g.h)
        if g.icon in catalog:
            vertex(f"gi-{g.id}", "", f"shape=image;aspect=fixed;image={data_uri(g.icon)};", g.x + 8, g.y + 6, 18, 18)
    for n in d.nodes:
        lab = n.label.replace("\n", "<br>")
        if n.icon in catalog:
            vertex(f"n-{n.id}", lab, f"shape=image;aspect=fixed;html=1;image={data_uri(n.icon)};verticalLabelPosition=bottom;"
                   "verticalAlign=top;labelBackgroundColor=none;fontSize=12;", n.x, n.y, n.w, n.h)
        else:
            vertex(f"n-{n.id}", lab, "rounded=1;whiteSpace=wrap;html=1;fillColor=#FFFFFF;strokeColor=#8A8886;fontSize=12;",
                   n.x, n.y, n.w, n.h)
    for i, r in enumerate(route_all(d)):
        a, b, e = by_id(d, r.edge.src), by_id(d, r.edge.dst), r.edge
        (sx, sy), (tx, ty) = r.pts[0], r.pts[-1]
        # why: 経路は自前で計算済み。draw.io に再配線させないよう edgeStyle=none で折れ点をそのまま渡す
        style = ("edgeStyle=none;rounded=1;html=1;endArrow=block;endFill=1;strokeColor=#605E5C;fontSize=11;"
                 "labelBackgroundColor=#FFFFFF;" + ("dashed=1;" if e.dashed else "")
                 + f"exitX={(sx - a.x) / a.w:.3f};exitY={(sy - a.y) / a.h:.3f};exitDx=0;exitDy=0;"
                 + f"entryX={(tx - b.x) / b.w:.3f};entryY={(ty - b.y) / b.h:.3f};entryDx=0;entryDy=0;")
        c = ET.SubElement(root, "mxCell", id=f"e{i}", value=e.label, style=style, edge="1", parent="1",
                          source=f"n-{e.src}", target=f"n-{e.dst}")
        frac = r.label_xy[2] if r.label_xy else 0.5
        geo = ET.SubElement(c, "mxGeometry", x=f"{frac * 2 - 1:.3f}", relative="1", attrib={"as": "geometry"})
        arr = ET.SubElement(geo, "Array", attrib={"as": "points"})
        for px, py in r.pts[1:-1]:
            ET.SubElement(arr, "mxPoint", x=f"{px:.1f}", y=f"{py:.1f}")

    model = ET.Element("mxGraphModel", grid="1", gridSize="10", guides="1", connect="1", arrows="1", page="1",
                       pageWidth=str(int(d.width)), pageHeight=str(int(d.height)))
    model.append(root)
    mx = ET.Element("mxfile", host="mmd-azure-diagram")
    ET.SubElement(mx, "diagram", id="p1", name="Architecture").append(model)
    ET.indent(mx)
    return ET.tostring(mx, encoding="unicode")


def verify_drawio(path: Path, expected_icons: int) -> list[str]:
    """draw.io CLI に SVG を描かせ、実際に描かれた矢印の線が端点以外のアイコン画像を通っていないか調べる。"""
    exe = shutil.which("drawio") or "/Applications/draw.io.app/Contents/MacOS/draw.io"
    if not Path(exe).exists():
        return ["draw.io CLI not found: the rendered-output check could not run"]
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "check.svg"
        subprocess.run([exe, "-x", "-f", "svg", "-o", str(out), str(path)], capture_output=True, timeout=120, check=True)
        root = ET.parse(out).getroot()

    href = "{http://www.w3.org/1999/xlink}href"
    icons, lines = [], []

    def walk(el, ox, oy):
        dx, dy = translate(el)
        ox, oy = ox + dx, oy + dy
        tag = el.tag.split("}")[-1]
        # why: draw.io CLI は文字ラベルを PNG 画像で埋め込むことがあるため、SVG 画像 (アイコン) だけを対象にする
        if tag == "image" and el.get(href, "").startswith("data:image/svg+xml"):
            x, y = ox + float(el.get("x", 0)), oy + float(el.get("y", 0))
            icons.append((x, y, x + float(el.get("width")), y + float(el.get("height"))))
        if tag == "path" and el.get("fill") == "none" and el.get("stroke") == "#605e5c":
            xy = [float(v) for v in re.findall(r"-?[\d.]+", el.get("d", ""))]
            lines.append([(ox + x, oy + y) for x, y in zip(xy[0::2], xy[1::2], strict=False)])
        for child in el:
            walk(child, ox, oy)

    walk(root, 0.0, 0.0)
    if len(icons) < expected_icons:
        return [f"found only {len(icons)} of {expected_icons} icons in the rendered SVG: the check cannot be trusted"]
    found = []
    for pts in lines:
        segs = [(a, b) for a, b in zip(pts, pts[1:], strict=False) if a[0] == b[0] or a[1] == b[1]]
        for rect in icons:
            if any(rect[0] - 6 <= px <= rect[2] + 6 and rect[1] - 6 <= py <= rect[3] + 6 for px, py in (pts[0], pts[-1])):
                continue  # この画像は矢印の端点 (接続元・接続先)
            if any(hits(seg, rect, 3) for seg in segs):
                found.append(f"rendered arrow {pts[0]} -> {pts[-1]} passes through icon at ({rect[0]:.0f}, {rect[1]:.0f})")
    return found


# ---------------------------------------------------------------- PowerPoint
def to_pptx(d: Diagram, catalog: dict, out: Path) -> None:
    import io

    import resvg_py
    from pptx import Presentation
    from pptx.dml.color import RGBColor
    from pptx.enum.dml import MSO_LINE
    from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
    from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
    from pptx.oxml.ns import qn
    from pptx.util import Emu, Inches, Pt

    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    margin = Inches(0.3)
    k = min((prs.slide_width - 2 * margin) / d.width, (prs.slide_height - 2 * margin) / d.height)  # EMU / px
    pt = k / 12700  # pt / px

    def E(v):
        return Emu(int(v * k))

    def X(v):
        return Emu(int(margin + v * k))

    def png(slug, px=128):
        i = catalog[slug]
        h = round(px * i["height"] / i["width"])
        return io.BytesIO(bytes(resvg_py.svg_to_bytes(svg_string=icon_svg(catalog, slug), width=px, height=h)))

    def text(shape, s, size, bold=False, color="201F1E", align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.TOP):
        tf = shape.text_frame
        tf.word_wrap, tf.vertical_anchor = False, anchor
        for side in ("margin_left", "margin_right", "margin_top", "margin_bottom"):
            setattr(tf, side, 0)
        for i, line in enumerate(s.split("\n")):
            para = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
            para.alignment = align
            r = para.add_run()
            r.text = line
            r.font.name = "Segoe UI"
            r.font.size, r.font.bold, r.font.color.rgb = Pt(max(size * pt, 5)), bold, RGBColor.from_string(color)

    for g in sorted(d.groups, key=lambda g: -g.w * g.h):
        box = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, X(g.x), X(g.y), E(g.w), E(g.h))
        box.fill.solid()
        box.fill.fore_color.rgb = RGBColor.from_string("F5F9FD")
        box.line.color.rgb, box.line.width = RGBColor.from_string("0078D4"), Pt(0.75)
        box.shadow.inherit = False
        text(box, g.label, 12, bold=True, color="0F3D6E", align=PP_ALIGN.LEFT)
        box.text_frame.margin_left, box.text_frame.margin_top = E(30 if g.icon else 8), E(6)
        if g.icon in catalog:
            slide.shapes.add_picture(png(g.icon, 64), X(g.x + 8), X(g.y + 6), E(18), E(18))

    shapes = {}
    for n in d.nodes:
        if n.icon in catalog:
            pic = slide.shapes.add_picture(png(n.icon), X(n.x), X(n.y), E(n.w), E(n.h))
            lab = slide.shapes.add_textbox(X(n.x - 40), X(n.y + n.h + 4), E(n.w + 80), E(34))
            text(lab, n.label, 12)
            shapes[n.id] = pic
        else:
            sh = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, X(n.x), X(n.y), E(n.w), E(n.h))
            sh.fill.solid()
            sh.fill.fore_color.rgb = RGBColor.from_string("FFFFFF")
            sh.line.color.rgb = RGBColor.from_string("8A8886")
            sh.shadow.inherit = False
            text(sh, n.label, 12, anchor=MSO_ANCHOR.MIDDLE)
            shapes[n.id] = sh

    site = {"top": 0, "left": 1, "bottom": 2, "right": 3}  # PowerPoint の矩形・画像の接続点番号
    def glue_shape(r):
        """計算済みの経路を、吸着コネクタでそのまま再現できるなら (種類, 曲がり位置の調整値) を返す。"""
        pts, sides_ = r.pts, {r.src_side, r.dst_side}
        if len(pts) == 2:
            return MSO_CONNECTOR.STRAIGHT, None
        if len(pts) != 4 or sides_ != {"left", "right"}:
            return None
        # why: bentConnector3 は横→縦→横の形しか描けない。曲がる位置 (adj1) を経路に合わせれば同じ線になる
        (sx, sy), (m1x, m1y), (m2x, m2y), (tx, ty) = pts
        if m1y == sy and m1x == m2x and m2y == ty and sx != tx:
            return MSO_CONNECTOR.ELBOW, round((m1x - sx) / (tx - sx) * 100000)
        return None

    def style_line(line, dashed, arrow=True):
        line.color.rgb, line.width = RGBColor.from_string("605E5C"), Pt(1)
        if dashed:
            line.dash_style = MSO_LINE.DASH
        if arrow:
            ln = line._get_or_add_ln()
            ln.append(ln.makeelement(qn("a:tailEnd"), {"type": "triangle", "w": "med", "len": "med"}))

    routes = route_all(d)
    # why: 吸着先は辺の中点 1 か所だけなので、同じ辺を共有する矢印を吸着させると 1 点に重なる
    share = Counter([(r.edge.src, r.src_side) for r in routes] + [(r.edge.dst, r.dst_side) for r in routes])
    for r in routes:
        e = r.edge
        alone = share[(e.src, r.src_side)] == 1 and share[(e.dst, r.dst_side)] == 1
        glue = glue_shape(r) if alone else None
        if glue:
            kind, adj = glue
            c = slide.shapes.add_connector(kind, 0, 0, 0, 0)
            c.begin_connect(shapes[e.src], site[r.src_side])
            c.end_connect(shapes[e.dst], site[r.dst_side])
            if adj is not None:
                geom = c._element.spPr.find(qn("a:prstGeom"))
                av = geom.find(qn("a:avLst"))
                if av is None:
                    av = geom.makeelement(qn("a:avLst"), {})
                    geom.append(av)
                av.append(av.makeelement(qn("a:gd"), {"name": "adj1", "fmla": f"val {adj}"}))
            style_line(c.line, e.dashed)
        else:
            # why: 吸着コネクタでは再現できない形なので、検査済みの経路どおりの折れ線で描く (吸着はしない)
            ff = slide.shapes.build_freeform(X(r.pts[0][0]), X(r.pts[0][1]), scale=1.0)
            ff.add_line_segments([(X(px), X(py)) for px, py in r.pts[1:]], close=False)
            shape = ff.convert_to_shape()
            shape.fill.background()
            style_line(shape.line, e.dashed)
        if r.label_xy:
            x, y, _ = r.label_xy
            lab = slide.shapes.add_textbox(X(x - 60), X(y - 10), E(120), E(20))
            lab.fill.solid()
            lab.fill.fore_color.rgb = RGBColor.from_string("FFFFFF")
            text(lab, e.label, 11, color="323130", anchor=MSO_ANCHOR.MIDDLE)
    prs.save(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("svg", type=Path, help="azurify.py が出力した SVG")
    ap.add_argument("--icons", type=Path, required=True)
    ap.add_argument("--mmd", type=Path, help="変換後の Mermaid (既定: <name>.azure.mmd)")
    ap.add_argument("--drawio", action="store_true")
    ap.add_argument("--pptx", action="store_true")
    ap.add_argument("--png", action="store_true", help="検査済みの draw.io から PNG を書き出す (--drawio を含む)")
    ap.add_argument("--svg-out", action="store_true", help="検査済みの draw.io から SVG を書き出す (--drawio を含む)")
    args = ap.parse_args()
    mmd = args.mmd or args.svg.with_suffix(".azure.mmd")
    catalog = json.loads(args.icons.read_text())["icons"]
    d = parse(args.svg, mmd)
    print(f"{len(d.groups)} groups, {len(d.nodes)} nodes, {len(d.edges)} edges")
    if args.drawio or args.png or args.svg_out:
        out = args.svg.with_suffix(".drawio")
        out.write_text(to_drawio(d, catalog))
        problems = verify_drawio(out, sum(1 for n in d.nodes if n.icon in catalog))
        if problems:
            sys.exit("draw.io rendering check failed:\n  " + "\n  ".join(problems))
        print(f"wrote {out} (rendered by draw.io: no arrow passes through an icon)")
        exe = shutil.which("drawio") or "/Applications/draw.io.app/Contents/MacOS/draw.io"
        for flag, fmt, extra in ((args.png, "png", ["-s", "2"]), (args.svg_out, "svg", [])):
            if flag:
                img = out.with_suffix(f".final.{fmt}")
                subprocess.run([exe, "-x", "-f", fmt, *extra, "-o", str(img), str(out)], capture_output=True, timeout=120,
                               check=True)
                print(f"wrote {img}")
    if args.pptx:
        out = args.svg.with_suffix(".pptx")
        to_pptx(d, catalog, out)
        print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
