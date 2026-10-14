# 筋トレ共有アプリ（試作）

スポーツチームで筋トレを記録して、タイムラインで見せ合い、トレーナーがコメントやメニューを届けるアプリ。

- 何ができるか・どう作ってあるか：[docs/設計書.md](docs/設計書.md)
- インターネット上に置く手順：[SETUP.md](SETUP.md)

## パソコンの中で動かす

`start.bat` をダブルクリックする。サーバーが動き出すと、ブラウザで http://localhost:8200 と http://127.0.0.1:8200 の 2つが開く。
この2つは ログインが別々なので、片方でトレーナー、もう片方で選手、と同時に試せる。

自分で打つなら:

```
.venv\Scripts\python -m uvicorn app.main:app --host localhost --port 8200
```

- データは `data/app.db`、写真は `data/photos/` に入る（`data` フォルダを消すと 最初からになる）
- 表に項目を足したときは、起動したときに 自動で足す（今までの記録は残る）
- 最初に「チームを作る」→ 作った人がトレーナーになる。「管理」→「チームの管理」に 招待の合言葉と 競技が出る

## テスト

```
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest -q
```

本番と同じ PostgreSQL で流すときは、`TEST_DATABASE_URL` に 空のデータベースの接続文字列を入れる（テストのたびに 中身を消すので、本物のデータベースには 向けない）。

## 設定（環境変数）

パソコンの中で試すだけなら 何もいらない。Render では「必要」のものが ないと 起動しない。

| 名前 | 中身 | Render |
|---|---|---|
| `DATABASE_URL` | PostgreSQL（Neon）の接続文字列。なければ `data/app.db` | 必要 |
| `SECRET_KEY` | ログインの印にサインする鍵。なければ `data/secret.key` に作る | 必要（自動で作られる） |
| `S3_ENDPOINT_URL` / `S3_BUCKET` / `S3_ACCESS_KEY_ID` / `S3_SECRET_ACCESS_KEY` | 写真の置き場（Cloudflare R2）。なければ `data/photos/` | 必要 |
| `VAPID_PUBLIC_KEY` / `VAPID_PRIVATE_KEY` | 通知の鍵。`scripts/make_vapid_keys.py` で作る。なければ `data/vapid.json` に作る | 必要 |
| `OPERATOR_CODE` | チームを作るための 運営用の合言葉。**入れないと だれでもチームを作れる** | 入れる |
| `VAPID_SUBJECT` | 通知の送り主の連絡先。入れなければ Render のアドレスを使う | なくてもいい |
| `APP_TIMEZONE` | 入れなければ `Asia/Tokyo` | render.yaml で入る |

## 通知について

| 開き方 | 通知 |
|---|---|
| パソコンの Chrome・Edge で `http://localhost:8200` | 使える（「自分」タブ →「通知をオンにする」→「ためしに送る」） |
| スマホから パソコンのアドレス（`http://192.168...`）で開く | 使えない（https でないため） |
| インターネット上に置いたあと（https）の Android | 使える |
| インターネット上に置いたあと（https）の iPhone | ホーム画面に追加したアプリから開くと 使える（iOS 16.4 以上） |

## 中身

| 場所 | 中身 |
|---|---|
| `app/routers/` | API（`users` ログイン・メンバー・知らせ／`workouts` 記録／`posts` 投稿／`body` 体重／`menus` メニュー／`admin` トレーナー用） |
| `app/models.py` | データベースの表 |
| `app/rules.py` | 自己ベスト・セットの書き方（記録の仕方ごと） |
| `app/push.py` | プッシュ通知 |
| `app/storage.py` | 写真の置き場（R2 ／ フォルダ） |
| `app/checks.py` | Render で 設定が足りないとき 起動を止める |
| `static/js/` | 画面（`rest.js` 休憩タイマー／`notify.js` 知らせ方を 1か所にまとめたもの） |
| `tests/` | テスト |
