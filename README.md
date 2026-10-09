# SF6 MR記録（Raspberry Pi 用）

毎日 5:00 と 19:00 に Buckler's Boot Camp の PLAY ページを読み、スプシに書いた人のメインキャラの MR を記録します。

- 記録先：SQLite（`data/sf6mr.sqlite3`）、スプシの「記録」タブ、グラフ用 `mr.json`
- Discord：毎回の MR（前回比と24時間比）、エラー時の通知
- ページに埋め込まれたデータ（`__NEXT_DATA__`）を読むので、タブ操作は不要です。1人につき1回のアクセスで済み、人と人の間は4〜8秒空けます。

## 1. Pi に設置

```bash
cd ~ && git clone https://github.com/masashi-bayman/sf6mr.git && cd sf6mr
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/playwright install --with-deps chromium
```

Playwright のブラウザが入らない場合は、`sudo apt install chromium` で入れて `.env` に `CHROMIUM_PATH=/usr/bin/chromium` を書いてください（64bit OS が前提です）。

## 2. 設定

```bash
cp .env.example .env
chmod 600 .env
nano .env
```

## 3. スプレッドシート

1. Google Cloud でサービスアカウントを作り、JSON 鍵を `service_account.json` として置きます（`chmod 600`）。
2. スプシをそのサービスアカウントのメールアドレス（`xxx@xxx.iam.gserviceaccount.com`）に「編集者」で共有します。
3. 「対象」タブの1行目を `ID / 名前 / キャラ名` にします。
   - キャラ名は「ダルシム」「DHALSIM」「dhalsim」のどれでも大丈夫です。
   - 空欄にすると、その人の「よく使うキャラ」を記録します。
4. 「記録」タブは無ければ自動で作られます。

列は 日時 / ID / 名前 / キャラ / MR / LP / MR順位 / 前回比 です。

`SHEET_ID` を空にすると、代わりに `targets.csv` を読みます（動作確認用）。

## 4. 動作確認

```bash
.venv/bin/python sf6mr.py login             # ログインしてセッション保存
.venv/bin/python sf6mr.py run --dry-run     # 取得して表示のみ
.venv/bin/python sf6mr.py run               # 本番1回
```

## 5. 定期実行（5:00 / 19:00）

`systemd/sf6mr.service` のユーザー名とパスを Pi に合わせてから、次を実行します。

```bash
sudo cp systemd/sf6mr.* /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now sf6mr.timer
systemctl list-timers sf6mr.timer        # 次回の実行時刻
journalctl -u sf6mr.service -n 50        # ログ
```

## 6. グラフページ（身内ポータル）

`web/mr.html` と `mr.json` を同じフォルダに置けば表示できます。例えば `.env` を `EXPORT_JSON=/var/www/portal/sf6/mr.json` にして、`mr.html` もそこにコピーします（そのフォルダに書き込み権限が必要です）。毎回の実行後に JSON が更新されます。

## ログインの仕組み

- ログイン状態は `data/session_state.json` に保存して使い回します。
- 「ログインが必要」と出たときだけ、`.env` の ID とパスワードで自動ログインし直します。

### ログインがうまくいかないとき

- 失敗すると、そのときの画面が `data/debug/` に PNG と HTML で保存されます。
- 画像認証や2段階認証が出る場合は、自動ログインできません。そのときは画面のある PC で次を実行し、手でログインを済ませてから `data/session_state.json` を Pi の同じ場所にコピーしてください。

```bash
python sf6mr.py login --headed
```

## 未確認の点

- CAPCOM ID のログイン画面で使う入力欄の指定（メール欄、パスワード欄、送信ボタン）は一般的な形にしてあり、実際の画面ではまだ試していません。初回の `login` で失敗したら、`data/debug/` の HTML を見せてもらえれば合わせます。
- ログイン状態がどれくらい持つかは、運用してみないと分かりません。
