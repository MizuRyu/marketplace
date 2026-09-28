# marketplace

Claude Code のプラグインと [Agent Skills](https://agentskills.io)。

## インストール

```
/plugin marketplace add MizuRyu/marketplace
/plugin install <plugin>@mizuryu
```

スキルは Agent Skills 形式なので、Codex などのエージェントでは `plugins/<plugin>/skills/<skill>/`
をそのエージェントのスキル用ディレクトリに置けば使える。

## プラグイン

| プラグイン | 説明 |
| --- | --- |
| [mmd-azure-diagram](plugins/mmd-azure-diagram) | Mermaid の構成図を、Azure 公式アイコンの構成図 (PNG/SVG・draw.io・PowerPoint) にする |

## 追加する

1. `plugins/<plugin>/` に `.claude-plugin/plugin.json`・`README.md`・中身 (スキル・サブエージェント・
   コマンド・hook・MCP サーバー) を置く。
2. `.claude-plugin/marketplace.json` と上の表に 1 行ずつ足す。
3. `claude plugin validate . --strict` を通す。

再配布が許されていない第三者の素材 (アイコン集・フォント・モデルなど) はコミットせず、
利用者が入手する手順をプラグイン側に用意する。

## ライセンス

[MIT](LICENSE)
