"""ノード (アイコンとラベル) を障害物として避ける直交経路を求め、突き抜けがないことを検査する。"""

import heapq
import itertools

STEP = 10      # 探索格子の間隔 (px)
PAD = 8        # 障害物の周りに空ける余白 (px)
BEND = 12      # 曲がり 1 回の重み
SHARED = 8     # 既存の線と同じ格子を通る重み
DIRS = {"right": (1, 0), "left": (-1, 0), "top": (0, -1), "bottom": (0, 1)}


def rects_of(node) -> list[tuple[float, float, float, float]]:
    """ノードが占める矩形 (x0, y0, x1, y1)。アイコンはラベルの範囲も含める。"""
    rs = [(node.x, node.y, node.x + node.w, node.y + node.h)]
    if node.label_box:
        lx, ly, lw, lh = node.label_box
        rs.append((lx, ly, lx + lw, ly + lh))
    return rs


def ports(node) -> list[str]:
    # why: アイコンの下にはラベルがあるため、下辺からの出入りはラベルを貫く。アイコンは上・左・右だけ使う
    return ["top", "left", "right"] if node.label_box else list(DIRS)


def port_point(node, side: str, offset: float) -> tuple[float, float]:
    cx, cy = node.x + node.w / 2, node.y + node.h / 2
    return {"right": (node.x + node.w, cy + offset), "left": (node.x, cy + offset),
            "top": (cx + offset, node.y), "bottom": (cx + offset, node.y + node.h)}[side]


def hits(seg, rect, shrink: float = 0.0) -> bool:
    """線分が矩形の内部を通るか (斜めの線分も扱う。Liang–Barsky のクリッピング)。"""
    (x0, y0), (x1, y1) = seg
    rx0, ry0, rx1, ry1 = rect[0] + shrink, rect[1] + shrink, rect[2] - shrink, rect[3] - shrink
    if rx0 >= rx1 or ry0 >= ry1:
        return False
    dx, dy = x1 - x0, y1 - y0
    t0, t1 = 0.0, 1.0
    for p_, q in ((-dx, x0 - rx0), (dx, rx1 - x0), (-dy, y0 - ry0), (dy, ry1 - y0)):
        if p_ == 0:
            if q <= 0:
                return False
            continue
        t = q / p_
        if p_ < 0:
            t0 = max(t0, t)
        else:
            t1 = min(t1, t)
        if t0 >= t1:
            return False
    return True


class Router:
    def __init__(self, nodes, width: float, height: float, extra: dict[str, tuple] | None = None):
        """extra: ノード以外に避ける矩形 (グループのタイトルなど)。キーは識別名。"""
        self.nodes = {n.id: n for n in nodes}
        self.ox, self.oy = -80, -80
        self.cols, self.rows = int((width + 160) / STEP) + 1, int((height + 160) / STEP) + 1
        self.owner: dict[tuple[int, int], str] = {}
        blocks = [(n.id, r) for n in nodes for r in rects_of(n)] + list((extra or {}).items())
        for owner, (x0, y0, x1, y1) in blocks:
            for cx in range(self.cell(x0 - PAD, 0)[0], self.cell(x1 + PAD, 0)[0] + 1):
                for cy in range(self.cell(0, y0 - PAD)[1], self.cell(0, y1 + PAD)[1] + 1):
                    self.owner[(cx, cy)] = owner
        self.used: set[tuple[int, int]] = set()
        self.slots: dict[tuple[str, str], int] = {}

    def cell(self, x: float, y: float) -> tuple[int, int]:
        return round((x - self.ox) / STEP), round((y - self.oy) / STEP)

    def point(self, c) -> tuple[float, float]:
        return c[0] * STEP + self.ox, c[1] * STEP + self.oy

    def route(self, src: str, dst: str) -> tuple[str, str, list[tuple[float, float]]]:
        """src から dst への直交経路。戻り値は (出る辺, 入る辺, 端点を含む折れ点列)。"""
        a, b = self.nodes[src], self.nodes[dst]
        starts, goals = {}, {}
        for side in ports(a):
            p = self._snap(a, side, port_point(a, side, self._slot(src, side)))
            dx, dy = DIRS[side]
            starts[self._escape(p, side, a.id)] = (side, p, (dx, dy))
        for side in ports(b):
            p = self._snap(b, side, port_point(b, side, self._slot(dst, side)))
            goals[self._escape(p, side, b.id)] = (side, p)
        best = self._search(starts, goals)
        if best is None:
            raise RuntimeError(f"no obstacle-free route from {src} to {dst}")
        cells, s_side, s_pt, g_side, g_pt = best
        self.used.update(cells)
        self.slots[(src, s_side)] = self.slots.get((src, s_side), 0) + 1
        self.slots[(dst, g_side)] = self.slots.get((dst, g_side), 0) + 1
        first, last = self.point(cells[0]), self.point(cells[-1])
        # why: ポートからは辺に垂直に出てから曲がる。先に曲がると自ノードの内側を通る
        lead = (first[0], s_pt[1]) if s_side in ("left", "right") else (s_pt[0], first[1])
        tail = (last[0], g_pt[1]) if g_side in ("left", "right") else (g_pt[0], last[1])
        pts = [s_pt, lead] + [self.point(c) for c in cells] + [tail, g_pt]
        return s_side, g_side, simplify(pts)

    def _snap(self, node, side: str, p):
        """ポート位置を探索格子に揃え、端で小さな段差が出ないようにする (辺の範囲内に収める)。"""
        x, y = p
        if side in ("left", "right"):
            y = min(max(round((y - self.oy) / STEP) * STEP + self.oy, node.y + 4), node.y + node.h - 4)
        else:
            x = min(max(round((x - self.ox) / STEP) * STEP + self.ox, node.x + 4), node.x + node.w - 4)
        return x, y

    def _slot(self, nid: str, side: str) -> float:
        k = self.slots.get((nid, side), 0)
        return (k + 1) // 2 * 2 * STEP * (1 if k % 2 else -1)

    def _escape(self, p, side, nid):
        """ポートから外側へ、障害物 (自ノードを含む) の外に出る最初の格子。"""
        dx, dy = DIRS[side]
        c = self.cell(*p)
        while self.owner.get(c) == nid:
            c = (c[0] + dx, c[1] + dy)
        return c

    def _search(self, starts, goals):
        tie = itertools.count()
        heap, seen = [], {}
        for c, (side, p, d) in starts.items():
            if c not in self.owner:
                heapq.heappush(heap, (self._h(c, goals), next(tie), 0, c, d, (c,), side, p))
        while heap:
            _, _, g, c, d, path, s_side, s_pt = heapq.heappop(heap)
            if c in goals:
                g_side, g_pt = goals[c]
                return path, s_side, s_pt, g_side, g_pt
            if seen.get((c, d), 1e18) <= g:
                continue
            seen[(c, d)] = g
            for nd in DIRS.values():
                if nd == (-d[0], -d[1]):
                    continue
                n = (c[0] + nd[0], c[1] + nd[1])
                if not (0 <= n[0] < self.cols and 0 <= n[1] < self.rows) or n in self.owner:
                    continue
                cost = g + 1 + (BEND if nd != d else 0) + (SHARED if n in self.used else 0)
                heapq.heappush(heap, (cost + self._h(n, goals), next(tie), cost, n, nd, path + (n,), s_side, s_pt))
        return None

    @staticmethod
    def _h(c, goals):
        return min(abs(c[0] - g[0]) + abs(c[1] - g[1]) for g in goals)


def simplify(pts):
    """同一直線上の中間点を除く。"""
    out = [pts[0]]
    for p in pts[1:]:
        if p == out[-1]:
            continue
        if len(out) >= 2 and ((out[-2][0] == out[-1][0] == p[0]) or (out[-2][1] == out[-1][1] == p[1])):
            out[-1] = p
        else:
            out.append(p)
    return out


def violations(routes, nodes, shrink: float = 2.0) -> list[str]:
    """経路が端点以外のノード (アイコンとラベル) を通っていないか調べ、通っていれば列挙する。"""
    found = []
    for (src, dst), pts in routes:
        for n in nodes:
            if n.id in (src, dst):
                continue
            for seg in zip(pts, pts[1:], strict=False):
                if any(hits(seg, r, shrink) for r in rects_of(n)):
                    found.append(f"{src} -> {dst} passes through {n.id}")
                    break
    return found
