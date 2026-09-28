#!/usr/bin/env python3
"""Mermaid flowchart の Azure リソース名ノードを公式アイコンに置き換え、SVG/PNG に描画する。

使い方:
    python azurify.py diagram.mmd --icons azure.json            # diagram.azure.mmd と diagram.svg を出力
    python azurify.py diagram.mmd --icons azure.json --png      # PNG も出力
    python azurify.py diagram.mmd --icons azure.json --dry-run  # 置き換え結果の表だけ表示

ノードごとの指定 (Mermaid のコメントなので元の図の描画には影響しない):
    %% azure: web=app-services     アイコンを明示
    %% azure: web=none             置き換えない
"""

import argparse
import base64
import json
import re
import shutil
import socket
import subprocess
import sys
import threading
from functools import partial
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

HERE = Path(__file__).resolve().parent
ALIASES = json.loads((HERE.parent / "data/aliases.json").read_text())
CONFIG = HERE.parent / "data/mermaid-config.json"
MERMAID_CLI = "@mermaid-js/mermaid-cli@11.17.0"

# 開き括弧の長いものから順に並べ、`[(`（円柱）や `((`（円）を `[` / `(` より先に拾う
SHAPES = [("[[", "]]"), ("[(", ")]"), ("([", "])"), ("((", "))"), ("{{", "}}"), ("[/", "/]"), ("[\\", "\\]"),
          ("[", "]"), ("(", ")"), ("{", "}"), (">", "]")]
OPEN = "|".join(re.escape(o) for o, _ in SHAPES)
CLOSE = "|".join(re.escape(c) for _, c in SHAPES)
NODE = re.compile(rf'(?<![\w@-])([A-Za-z_][\w-]*)\s*({OPEN})(?:"([^"]*)"|([^\]\)\}}"|]*?))({CLOSE})')
SKIP = re.compile(r"^\s*(subgraph|end\b|classDef|class\s|style\s|linkStyle|click\s|direction\s|%%|flowchart|graph\s)")
SUBGRAPH = re.compile(r'^(\s*subgraph\s+)([\w-]+)\s*\[\s*"?(.*?)"?\s*\]\s*$')
# why: 部分一致なので、より狭い種類を先に判定する（"AzureFirewallSubnet" は VNet ではなく subnet）
GROUP_ICONS = [(r"\bsnet-|subnet", "subnet"), (r"\bvnet\b|virtual network", "virtual-networks"),
               (r"subscription", "subscriptions"), (r"resource group|\brg\b", "resource-groups"),
               (r"management group", "management-groups")]
DIRECTIVE = re.compile(r"^\s*%%\s*azure:\s*([\w-]+)\s*=\s*([\w-]+)\s*$")


def norm(s: str) -> str:
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I).split("\n")[0]
    s = re.sub(r"<[^>]+>|[`*_\"']", "", s).lower()
    s = re.sub(r"\((.*?)\)", " ", s)
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def resolve(label: str, catalog: dict) -> str | None:
    """ラベル先頭行を、別名表 → 公式アイコン名 → 語の一致の順で照合する。"""
    n = norm(label)
    if not n:
        return None
    for cand in (n, re.sub(r"^(azure|microsoft) ", "", n)):
        if cand in ALIASES:
            return ALIASES[cand]
        key = cand.replace(" ", "-")
        for k in (key, key + "s", key.rstrip("s")):
            if k in catalog:
                return k
    words = f" {n} "
    # why: 「App Service P1v3」のように名前の後ろに補足が続く書き方を拾う。長い別名を優先して誤爆を減らす
    for alias in sorted(ALIASES, key=len, reverse=True):
        if len(alias) >= 3 and f" {alias} " in words:
            return ALIASES[alias]
    return None


def group_icon(title: str) -> str | None:
    t = title.lower()
    return next((slug for pat, slug in GROUP_ICONS if re.search(pat, t)), None)


def icon_svg(catalog: dict, slug: str) -> str:
    i = catalog[slug]
    vb = f"{i.get('left', 0)} {i.get('top', 0)} {i['width']} {i['height']}"
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{vb}">{i["body"]}</svg>'


def iconify_subgraph(line: str, forced: dict, catalog: dict) -> str:
    """サブスクリプション・RG・VNet・サブネットの枠タイトルの前に公式アイコンを付ける。"""
    m = SUBGRAPH.match(line)
    if not m:
        return line
    head, gid, title = m.groups()
    slug = forced.get(gid) or group_icon(title)
    if slug not in catalog:
        return line
    data = base64.b64encode(icon_svg(catalog, slug).encode()).decode()
    img = f"<img src='data:image/svg+xml;base64,{data}' width='18' height='18' style='vertical-align:-4px'/>"
    return f'{head}{gid}["{img} {title}"]'


def iconify_line(line: str, forced: dict, catalog: dict, done: set, report: list) -> list[str]:
    """1 行の中で初出のリソースノードをアイコン定義に置き換え、定義行を元の行の直前に並べて返す。"""
    indent = re.match(r"\s*", line).group(0)
    defs = []

    def repl(m):
        nid, label = m.group(1), m.group(3) if m.group(3) is not None else m.group(4)
        if nid in done:
            return m.group(0)
        done.add(nid)
        slug = forced.get(nid) or resolve(label, catalog)
        if slug == "none" or slug not in catalog:
            report.append((nid, label, "none" if slug == "none" else None))
            return m.group(0)
        report.append((nid, label, slug))
        text = label.replace('"', "'")
        defs.append(f'{indent}{nid}@{{ icon: "azure:{slug}", label: "{text}", pos: "b", h: 56 }}')
        return nid

    rest = NODE.sub(repl, line)
    # why: ノード定義だけの行は定義行に置き換わるので、残った裸の id 行は捨てる
    bare = {d.strip().split("@")[0] for d in defs}
    return defs + ([rest] if rest.strip() and rest.strip() not in bare else [])


def convert(src: str, catalog: dict):
    lines = src.splitlines()
    forced = {m.group(1): m.group(2) for line in lines if (m := DIRECTIVE.match(line))}
    done, report, out = set(), [], []
    for line in lines:
        if SUBGRAPH.match(line):
            out.append(iconify_subgraph(line, forced, catalog))
        else:
            out.extend([line] if SKIP.match(line) else iconify_line(line, forced, catalog, done, report))
    return "\n".join(out) + "\n", report


class CORS(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        super().end_headers()

    def log_message(self, *args):
        pass


def render(mmd: Path, icons: Path, outputs: list[Path]) -> None:
    runner = shutil.which("pnpm") and ["pnpm", "dlx"] or shutil.which("bunx") and ["bunx"] or ["npx", "-y"]
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    # why: mermaid-cli は file:// のアイコン集を読めず、http でも CORS ヘッダが要る
    srv = HTTPServer(("127.0.0.1", port), partial(CORS, directory=str(icons.parent)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        for out in outputs:
            cmd = [*runner, MERMAID_CLI, "-i", str(mmd), "-o", str(out), "-c", str(CONFIG), "-b", "white",
                   "--iconPacksNamesAndUrls", f"azure#http://127.0.0.1:{port}/{icons.name}"]
            if out.suffix == ".png":
                cmd += ["-s", "2"]
            r = subprocess.run(cmd, capture_output=True, text=True)
            if r.returncode != 0 or not out.exists():
                sys.exit(f"mermaid-cli failed for {out}:\n{r.stderr[-2000:]}")
    finally:
        srv.shutdown()


def mermaid_crossings(svg: Path, mmd: Path) -> list[str]:
    """Mermaid が引いた矢印 (曲線を折れ線で近似) が、端点以外のノードを通っていないか調べる。"""
    import base64 as b64
    import xml.etree.ElementTree as ET

    from export import edge_ends, parse
    from route import violations

    d = parse(svg, mmd)
    root = ET.parse(svg).getroot()
    vx, vy = (float(v) for v in root.get("viewBox").split()[:2])
    prefix = root.get("id", "my-svg") + "-"
    ids = {n.id for n in d.nodes}
    routes = []
    for p in root.iter("{http://www.w3.org/2000/svg}path"):
        m = re.fullmatch(r"L_(.+)_\d+", p.get("id", "").removeprefix(prefix))
        if not m or not p.get("data-points"):
            continue
        ends = edge_ends(m.group(0), ids)
        if ends:
            pts = [(q["x"] - vx, q["y"] - vy) for q in json.loads(b64.b64decode(p.get("data-points")))]
            routes.append((ends, pts))
    return violations(routes, d.nodes)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", type=Path, help="Mermaid flowchart (.mmd)")
    ap.add_argument("--icons", type=Path, required=True, help="build_iconpack.py で作った azure.json")
    ap.add_argument("-o", "--output", type=Path, help="出力 SVG (既定: 入力と同名の .svg)")
    ap.add_argument("--png", action="store_true", help="PNG も出力する")
    ap.add_argument("--dry-run", action="store_true", help="置き換え表だけ表示して描画しない")
    args = ap.parse_args()

    catalog = json.loads(args.icons.read_text())["icons"]
    converted, report = convert(args.input.read_text(), catalog)
    for nid, label, slug in report:
        mark = f"azure:{slug}" if slug and slug != "none" else ("(skip)" if slug == "none" else "-- 未対応 --")
        print(f"{nid:<16} {norm(label)[:40]:<42} {mark}")
    missing = [r for r in report if r[2] is None]
    if missing:
        print(f"\n未対応 {len(missing)} 件: `%% azure: <id>=<icon>` で指定するか、data/aliases.json に追加する。"
              f" アイコン名は {args.icons.stem}.catalog.json を参照。", file=sys.stderr)
    if args.dry_run:
        return 0

    out_mmd = args.input.with_suffix(".azure.mmd")
    out_mmd.write_text(converted)
    svg = args.output or args.input.with_suffix(".svg")
    outs = [svg] + ([svg.with_suffix(".png")] if args.png else [])
    render(out_mmd, args.icons.resolve(), outs)
    print("\n" + "\n".join(f"wrote {p}" for p in [out_mmd, *outs]))
    crossings = mermaid_crossings(svg, out_mmd)
    if crossings:
        print(f"\n警告: Mermaid の配線で矢印がアイコンを {len(crossings)} か所突き抜けている。"
              " 突き抜けのない図は export.py --png で作る:\n  " + "\n  ".join(crossings), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
