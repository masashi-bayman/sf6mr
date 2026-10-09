# SF6 MR記録ツール：Claude Code への引き継ぎ

このファイルは、claude.ai での会話で途中まで作ったものの引き継ぎメモです。セットアップの手順は README.md にあります。

## 会話のルール

- ユーザー（Cho）とは日本語の敬語で話してください。英語の説明は避けてください。
- サイトの仕様やログインの挙動など確認できていないことは、事実のように書かず「未確認」と明記してください。
- 自分のミスを指摘されたときは、言い訳をせずにまず謝ってください。

## 目的

スト6（Buckler's Boot Camp）の MR（マスターレート）推移を自動で記録します。

- 対象は Cho 本人（ユーザーコード 4050953661、ファイター名 cho13）と、ほかに最大5人程度です。フレンドでなくても、ユーザーコードが分かれば見られます。
- 記録するのはメインキャラ1体分だけです。
- 実行環境は Cho の Raspberry Pi 4B です。systemd、nginx、Cloudflare Tunnel が動いていて、「身内ポータル」というページを配信しています。

## 決まっている仕様

| 項目 | 内容 |
|---|---|
| 対象リスト | Google スプシの「対象」タブ（列：ID / 名前 / キャラ名）。サービスアカウントで読みます。スプシをCSVとして公開する方法は使いません。 |
| 取得するページ | `https://www.streetfighter.com/6/buckler/ja-jp/profile/{ID}/play` |
| 実行時刻 | 毎日 5:00 と 19:00（日本時間） |
| 保存先 | Pi 上の SQLite と、スプシの「記録」タブ |
| Discord 通知 | 毎回の MR（前回比と24時間比）と、エラーの通知 |
| グラフ | 身内ポータルにページを置く（`web/mr.html` が `mr.json` を読む形） |
| Act（シーズン） | 気にしなくてよいです。ページを開くと常に最新が表示されます。 |

## 分かっていること

- PLAY ページの HTML には `<script id="__NEXT_DATA__">` という JSON が入っていて、全キャラの MR がそこにあります。
  - そのため「キャラクター別マスターレート」タブを押す必要はありません（タブを押しても URL は変わりません）。
- `sample_play.html` は、Cho の PLAY ページを保存したものです。
- 中身の構造は次のとおりです（`pageProps` の下）。

```
props.pageProps
  .common.loginUser                       ← ログインしていない場合は空と思われます（未確認）
  .fighter_banner_info.personal_info      ← fighter_id, short_id
  .fighter_banner_info.favorite_character_id
  .play.character_league_infos[]
      .character_id / character_name（日本語） / character_alpha / character_tool_name
      .league_info.master_rating          ← 0 は未レート（画面では ---MR）
      .league_info.league_point / master_rating_ranking
```

- サンプルの値は、ダルシム 1806MR と リュウ 1475MR で、画面の表示と一致しています。
- ログインしていないと「ご利用いただくにはログインまたは登録が必要です。」と表示されます。
- ログインは CAPCOM ID（auth.cid.capcom.com）で行います。
  - ログイン画面の URL の `state` は使い捨てなので、`https://www.streetfighter.com/6/buckler/ja-jp/auth/loginep?redirect_url=/` から入ります。

## 今の実装（sf6mr.py）

ファイルは1つで、Playwright（Chromium、headless）を使っています。

- `run`：対象を読み、各 ID のページを開いて解析し、保存・スプシ追記・Discord 通知・JSON 書き出しを行います。
  - 1人ごとに4〜8秒あけます。
  - 「ログインが必要」と出たら、`.env` の ID とパスワードで1回だけログインし直します。
- `run --dry-run`：取得して表示するだけで、保存や通知はしません。
- `login [--headed]`：ログインして、ログイン状態を `data/session_state.json` に保存します。
- `parse FILE`：保存した HTML を解析します。
  - `python sf6mr.py parse sample_play.html` で動きます。
- `export`：グラフ用の JSON を書き出します。
- 失敗したときは、画面の PNG と HTML を `data/debug/` に保存します。

## テスト済みのこと

- サンプル HTML の解析
- 本物のサイトの代わりに仮のページを使った `run` の流れ。SQLite への保存、前回比・24h比、未プレイキャラの表示、ログインし直しの分岐、JSON 書き出しまで確認しました。

## まだやっていないこと（ここから進めてほしいこと）

1. **Pi の環境の確認**
   - 64bit OS であること、Python のバージョン、Playwright の Chromium が入るか。
   - 入らない場合は、apt の chromium を `CHROMIUM_PATH` で指定します。
2. **CAPCOM ID のログイン**
   - 入力欄の指定は推測で書いています（`Browser.login`）。
   - 実際の画面を見て合わせてください。失敗時の `data/debug/` を見ると分かります。
   - 画像認証や2段階認証が出る場合は、自動化を無理に進めないでください。Cho に手でログインしてもらい、ログイン状態のファイル（`login --headed` で保存されるもの）を使う運用にします。
3. **本物のサイトでの `run --dry-run`**
   - 本人の ID と、フレンドの ID（2887619134、1957220096）で確認します。
   - ログインしていないときの判定（`parse_play_html` の LoginRequired）が実際のページで正しく動くかも見てください。
4. **スプシの接続**
   - サービスアカウントの作成と共有は、Cho に手順を案内して進めてもらいます。
5. **Discord Webhook の送信テスト**
6. **systemd の timer の設定**
   - `systemd/` のユーザー名とパスを Pi に合わせてください。
7. **身内ポータルへの配置**
   - nginx の配信フォルダを確認し、`EXPORT_JSON` と `mr.html` を置きます。
8. **しばらく運用して、ログイン状態がどれくらい持つかを確認**

## 守ること

- **アクセスは控えめに。** 1日2回、1人1ページだけにします。テストで何度も開かないようにしてください。動作確認には `sample_play.html` や `data/debug/` の保存 HTML を使い、本物のサイトへのアクセスは最小限にしてください。
- **ログイン情報の扱い**
  - CAPCOM ID とパスワードは `.env`（chmod 600）だけに置いてください。スプシやログ、Discord には出さないでください。
  - `data/session_state.json` と `service_account.json` も chmod 600 にしてください。
- **Pi の既存サービスへの配慮**
  - 身内ポータルなど、すでに動いているサービスがあります。nginx の設定を変えるときは、事前に Cho に確認してください。
