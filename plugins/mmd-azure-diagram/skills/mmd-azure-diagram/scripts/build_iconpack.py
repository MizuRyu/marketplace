#!/usr/bin/env python3
"""Microsoft 公式 Azure アイコン ZIP から、Mermaid 用の Iconify アイコン集 (azure.json) を作る。

使い方:
    python build_iconpack.py --accept-terms -o azure.json        # 公式から取得して変換
    python build_iconpack.py Azure_Public_Service_Icons_V24.zip  # 手元の ZIP を変換

アイコンは Microsoft の利用条件 (構成図・研修資料・ドキュメント用途に限る) に従う。
https://learn.microsoft.com/azure/architecture/icons/
取得するのは、利用者が条件に同意したことを --accept-terms で示したときだけ。
"""

import argparse
import io
import json
import re
import sys
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

ICONS_URL = "https://arch-center.azureedge.net/icons/Azure_Public_Service_Icons_V24.zip"
NAME = re.compile(r"^\d+-icon-service-(.+)\.svg$")
SVG = re.compile(r"<svg\b([^>]*)>(.*)</svg>\s*$", re.S)


def slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")


def to_icon(svg_text: str, prefix: str) -> dict:
    m = SVG.search(svg_text)
    if not m:
        raise ValueError("not an <svg> document")
    attrs, body = m.groups()
    vb = re.search(r'viewBox="([\d.\s-]+)"', attrs)
    left, top, width, height = (float(v) for v in vb.group(1).split()) if vb else (0, 0, 18, 18)
    # why: 1 枚の図に複数アイコンを埋め込むとグラデーション等の id が衝突するため、アイコン単位で接頭辞を付ける
    ids = set(re.findall(r'\bid="([^"]+)"', body))
    for i in sorted(ids, key=len, reverse=True):
        body = re.sub(rf'\bid="{re.escape(i)}"', f'id="{prefix}-{i}"', body)
        body = body.replace(f"url(#{i})", f"url(#{prefix}-{i})").replace(f'href="#{i}"', f'href="#{prefix}-{i}"')
    icon = {"body": body.strip(), "width": width, "height": height}
    if left or top:
        icon.update(left=left, top=top)
    return icon


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("zip", type=Path, nargs="?", help="手元の Azure_Public_Service_Icons_V*.zip (省略時は公式から取得)")
    ap.add_argument("--accept-terms", action="store_true", help="Microsoft のアイコン利用条件に同意して公式から取得する")
    ap.add_argument("--url", default=ICONS_URL, help="取得する ZIP の URL (新しい版が出たとき)")
    ap.add_argument("-o", "--output", type=Path, default=Path("azure.json"))
    ap.add_argument("--prefix", default="azure", help="Mermaid で使う接頭辞 (azure:app-services の azure)")
    args = ap.parse_args()

    if args.zip:
        source, data = args.zip.name, args.zip.read_bytes()
    elif args.accept_terms:
        source = args.url
        with urllib.request.urlopen(args.url, timeout=120) as r:
            data = r.read()
    else:
        ap.error("ZIP を指定するか、Microsoft のアイコン利用条件に同意して --accept-terms を付ける"
                 " (https://learn.microsoft.com/azure/architecture/icons/)")

    icons, titles, categories = {}, {}, {}
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for name in sorted(z.namelist()):
            p = PurePosixPath(name)
            m = NAME.match(p.name)
            if not m or "__MACOSX" in p.parts:
                continue
            title = m.group(1).replace("-", " ")
            key = slug(m.group(1))
            if key in icons:
                continue
            try:
                icons[key] = to_icon(z.read(name).decode("utf-8"), f"az{len(icons)}")
            except ValueError as e:
                print(f"skip {name}: {e}", file=sys.stderr)
                continue
            titles[key] = title
            categories[key] = p.parent.name

    pack = {"prefix": args.prefix, "icons": icons, "width": 18, "height": 18,
            "info": {"name": "Azure Public Service Icons", "author": {"name": "Microsoft"},
                     "license": {"title": "Microsoft Azure icon terms (diagrams, training, documentation)"},
                     "source": source}}
    args.output.write_text(json.dumps(pack, ensure_ascii=False))
    catalog = args.output.with_name(args.output.stem + ".catalog.json")
    catalog.write_text(json.dumps({k: {"title": titles[k], "category": categories[k]} for k in icons},
                                  ensure_ascii=False, indent=1))
    print(f"{args.output}: {len(icons)} icons (prefix '{args.prefix}'), catalog: {catalog}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
