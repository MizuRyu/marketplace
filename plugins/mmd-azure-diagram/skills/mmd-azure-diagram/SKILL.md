---
name: mmd-azure-diagram
description: Mermaid の flowchart で書いた構成図を、Microsoft 公式の Azure アイコンを使った構成図にする。subgraph の入れ子と矢印のラベルを保ち、矢印がアイコンを突き抜けない PNG/SVG・draw.io・PowerPoint を出力する。Mermaid の構成図がある (または書きたい) ときに、Azure アイコン入りの図、設計書やスライド用の図を求められたら使う。Azure 以外のクラウドや手描き風の図には使わない。
---

# Mermaid → Azure アイコン構成図

図は Mermaid の `flowchart` のまま保つ (境界は subgraph、流れはラベル付きの矢印)。
`scripts/azurify.py` は、ラベルが Azure リソースを指すノードだけを Mermaid のアイコン図形
(`id@{ icon: "azure:<name>" }`) に書き換え、mermaid-cli で配置を決める。subgraph の入れ子、
矢印のラベル、`classDef`、Azure 以外のノードはそのまま通す。続いて `scripts/export.py` が
すべてのアイコンを避けるように矢印を引き直し、成果物を書き出す。
以下の `<skill_path>` は、この SKILL.md があるディレクトリを指す。

## 1. アイコン集 (マシンごとに 1 回)

アイコンは Microsoft の公式配布物から取り込み、このスキルには**同梱しない**。

```bash
ls ~/.cache/mmd-azure-diagram/azure.json
```

無ければ、取得の前に利用者に確認する。アイコンは Microsoft の利用条件
(https://learn.microsoft.com/azure/architecture/icons/ 、構成図・研修資料・ドキュメントでの
利用に限る) のもとで公開されており、利用者がその条件に同意する必要がある。同意を得たら:

```bash
mkdir -p ~/.cache/mmd-azure-diagram
python3 <skill_path>/scripts/build_iconpack.py --accept-terms -o ~/.cache/mmd-azure-diagram/azure.json
```

公式の ZIP を取得し、`azure.json` (Iconify 形式のアイコン集、接頭辞 `azure`) と
`azure.catalog.json` (アイコン名 → 名称・カテゴリ) を作る。Microsoft が新しい版を出したら
`--url` に新しい ZIP の URL を渡して作り直す。

## 2. Mermaid を書く・受け取る

`flowchart` / `graph` を使う。各リソースのラベルは Azure のサービス名を先頭に書き、補足は
`<br>` の後に置く (`app[App Service<br>P1v3 x3]`)。サブスクリプション → VNet → サブネットの
入れ子は subgraph で表す。Azure リソースでないノード (他システムの利用者、SaaS) は四角のまま残る。

ノードごとの指定は Mermaid のコメントで書くので、元の図はどこでもそのまま描ける:

```
%% azure: proxy=network-interfaces   アイコンを明示 (名前は azure.catalog.json から)
%% azure: saas=none                  置き換えない
```

## 3. 対応を確認してから描画する

```bash
python3 <skill_path>/scripts/azurify.py diagram.mmd --icons ~/.cache/mmd-azure-diagram/azure.json --dry-run
python3 <skill_path>/scripts/azurify.py diagram.mmd --icons ~/.cache/mmd-azure-diagram/azure.json --png
```

- `--dry-run` は各ノードと対応したアイコンを一覧で出す。`-- 未対応 --` の行は、指定を足すか、
  よく使う呼び名を `data/aliases.json` に足して解消する (値は catalog に存在する名前にする)。
- 描画で `diagram.azure.mmd` (書き換え後の Mermaid。元ファイルの隣に残す) と、手順 4 の配置の
  もとになる `diagram.svg` ができる。`--png` を付けると確認用の PNG も出る。

## 4. 最終画像・draw.io・PowerPoint (矢印はアイコンを突き抜けない)

```bash
uv run <skill_path>/scripts/export.py diagram.svg --icons ~/.cache/mmd-azure-diagram/azure.json --png --pptx
```

Mermaid が描いた PNG ではなく、これらを成果物として渡す。`export.py` は Mermaid の配置を
使いつつ、矢印はすべて自分で引き直す:

- アイコン、その下のラベル、各グループのタイトルを障害物とし、それを避ける直交経路
  (10 px 格子上の A*) で矢印を引く。アイコンからは上・左・右からだけ出入りし (下にはラベルがある)、
  同じ辺を複数の矢印が使うときは辺上にずらす。矢印のラベルは経路上の空いた位置に置く。
- 経路かラベルがアイコン・ラベル・グループのタイトルに触れる場合は `routing check failed` で
  止まる。このエラーの後に図を報告してはならない。
- `diagram.drawio` には経路を経由点として明示的に渡す (draw.io に再配線させない)。その後
  draw.io CLI で描画し、描かれた矢印と描かれたアイコンを照合する。違反があるか、アイコンの数が
  足りなければ止まる。`--png` / `--svg-out` は、この検査済みファイルから `diagram.final.png|svg`
  を書き出す。draw.io Desktop (`drawio` が PATH にあること) が必要。
- `diagram.pptx`: 使う辺を独占でき、PowerPoint のカギ型で表せる矢印は、曲がる位置を経路に
  合わせた吸着コネクタにする。それ以外は経路どおりの折れ線で描く (吸着しない)。矢印のラベルは
  独立したテキストボックス。

報告の前に最終 PNG を見て確認する。PowerPoint は、macOS のクイックルックが折れ線の矢じりを
描かないので、PowerPoint が入っていれば PowerPoint 自体で確認する。

## 補足

- 照合の順序: `data/aliases.json` の完全一致 → catalog のアイコン名 → ラベル 1 行目に含まれる
  最長の別名。照合するのはラベルの 1 行目だけ。
- 現行の Microsoft のアイコン集には Microsoft Entra ID 単体のアイコンがない (Entra 系は別配布)。
  指定しない限り Entra のノードは四角のまま残る。
- 配置は Mermaid (dagre) のもので、引き直すのは矢印だけ。Mermaid 自身の矢印がアイコンを
  突き抜けると `azurify.py` が警告を出す。これも `export.py` の出力を渡す理由の 1 つ。
- PowerPoint の文字は Segoe UI (Office に同梱。クイックルックのプレビューでは明朝体で代替表示される)。
- mermaid-cli は `pnpm dlx` で動かす (無ければ `bunx`、次に `npx`)。mermaid-cli は `file://` の
  アイコン集を読めないため、スクリプトが一時的な localhost サーバーでアイコン集を配信する。
