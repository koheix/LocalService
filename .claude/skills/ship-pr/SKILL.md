---
name: ship-pr
description: 現在のブランチをテストして、結果のレポートを本文に埋め込んだ PR を作る。レビュー時にテスト結果が必ず目に入るようにするための必須の手順。「PR を作って」「PR を出して」と言われたときに使う。
---

# テスト結果つきの PR を作る

llm-console の非機能要件のひとつは「変更ごとのテスト結果を、レビュー時に見られること」。
テスト結果の載っていない PR は未完成として扱う。

## 1. ブランチを確認する

```powershell
git status --short --branch
git log --oneline origin/main..HEAD
```

- `main` にいるなら、ここで止めてユーザーに知らせる（PR にできない）
- 未コミットの変更があるなら、コミットするか退避するかをユーザーに確認する
- コミットが 1 つも無いなら、PR にするものが無い

## 2. origin と同期する

```powershell
git fetch origin --prune
git rev-list --left-right --count HEAD...origin/main
```

`origin/main` が大きく進んでいるなら、rebase するかをユーザーに確認する。

## 3. テストを実行する

```powershell
make test-report
```

**失敗が 1 件でもあるなら、PR を作らずに報告する。**
「落ちているが PR は出す」場合は、ユーザーが明示的にそう言ったときだけ。
その場合は PR を下書き（`--draft`）にし、本文の先頭に失敗している旨を書く。

`reports/test-report.md` と `reports/verification.json` が生成される。後者は
`guard-gh.ps1` が読む記録で、いまのコミットで全緑でなければ `gh pr create` が通らない。

lint（`ruff check` / `ruff format --check`、gateway ディレクトリ）も併せて確認し、
通っていなければ同様に報告する。`make fmt` で自動修正できるものは修正してよいが、
その場合は修正後に再度コミットしてからテストを実行し直す。

## 3.5. レビューを済ませて記録する

`pr-reviewer`（差分のレビュー）と、テストを書いたなら `test-auditor`（テストの監査）を
実行する。

**記録はコミットの SHA に紐づく。** 指摘を直したら、必ずこの順でやり直す。

1. 直す
2. **コミットする**
3. `make test-report`（`reports/verification.json` が新しい HEAD で作られる）
4. レビューをやり直して記録する

順番を間違えて「記録してからコミット」にすると、記録が古いコミットのものになり、
PR 作成が拒否される。理由が分かりにくいので気をつける。

```powershell
python scripts/record_review.py --reviewers test-auditor,pr-reviewer --verdict pass
```

**この記録が無いと `gh pr create` は拒否される。**

## 4. push する

```powershell
git push -u origin <ブランチ名>
```

## 5. PR 本文を組み立てる

`reports/test-report.md` の内容を読み、次の構成で本文を作る。

```markdown
## 何をしたか
（1〜3 行。何を解決したのか）

## 変更の内容
- `gateway/app/routers/v1.py` — 何をどう変えたか
- `gateway/tests/routers/test_v1.py` — 何を検証しているか

## なぜこうしたか
（設計上の判断と、他の選択肢を採らなかった理由。自明なら省略）

## テスト結果

| 項目 | 件数 |
|---|---|
| 成功 | 42 |
| 失敗 | 0 |
| スキップ | 3 |

スキップの内訳: 実バックエンド呼び出しの手動確認テスト 3 件（既定では実行しない）

<details>
<summary>テストの詳細</summary>

（reports/test-report.md の本体をそのまま貼る）

</details>

## レビューで見てほしいところ
- （判断が必要だった箇所、設計の選択）

## やっていないこと
- （意図的に範囲外にしたもの）

🤖 Generated with [Claude Code](https://claude.com/claude-code)
```

**テスト結果の表は `<details>` の外に置く。** 折りたたむと見られない。

## 6. PR を作る

本文は一時ファイル経由で渡す（改行と日本語が壊れないように）。

```powershell
$body = Get-Content -Raw -Encoding UTF8 <一時ファイル>
gh pr create --title "<日本語のタイトル>" --body-file <一時ファイル>
```

一時ファイルは `$env:TEMP` に置き、作成後に消す。リポジトリに残さない。

## 7. 報告して、止まる

PR の URL とテスト結果の要約をユーザーに伝える。**ここで終わり。** マージ・デプロイへ
進まない。次に何をするかは人間が決める。

## 守ること

- **PR をマージしない。** 承認とマージは人間がやる（`guard-gh.ps1` が機械的に止める）
- テストを実行せずに PR を作らない
- テスト結果を「全部通りました」と要約だけ書かない。件数と内訳を載せる
- スキップされたテストがあれば、その理由を必ず書く
