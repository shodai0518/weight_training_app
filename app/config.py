"""アプリの決まりごと（数字や 最初から用意するもの）。"""

import os

TIMEZONE = os.getenv("APP_TIMEZONE", "Asia/Tokyo")  # サーバーがどこにあっても、この時間で記録する
DAY_START_HOUR = 4  # 1日の区切り（朝4時）

# ---- チーム ----
TEAM_MAX_MEMBERS = 100  # 新しいチームの 選手の上限（チームごとに持つので、あとで変えられる）
TEAM_MAX_TRAINERS = 10  # 新しいチームの トレーナーの上限
INACTIVE_DAYS = 7  # この日数 筋トレをしていない人に 印を付ける

# ---- 種目 ----
BODY_PARTS = {
    "chest": "胸", "shoulders": "肩", "arms": "腕", "back": "背中", "abs": "腹", "legs": "脚", "cardio": "有酸素",
}
# 最初から用意しておく種目（全チーム共通）。有酸素は 時間と距離を記録する
PRESET_EXERCISES = {
    "chest": ["ベンチプレス", "ダンベルプレス", "インクラインベンチプレス", "チェストプレス", "ダンベルフライ", "腕立て伏せ"],
    "back": ["デッドリフト", "ラットプルダウン", "懸垂", "ベントオーバーロウ", "シーテッドロウ"],
    "legs": ["スクワット", "レッグプレス", "ブルガリアンスクワット", "レッグエクステンション", "レッグカール", "ランジ"],
    "shoulders": ["ショルダープレス", "サイドレイズ", "リアレイズ"],
    "arms": ["アームカール", "ハンマーカール", "トライセプスエクステンション", "ディップス"],
    "abs": ["クランチ", "レッグレイズ", "アブローラー", "プランク"],
    "cardio": ["ランニング", "ウォーキング", "バイク", "トレッドミル", "水泳"],
}
BIG3_EXERCISES = ("ベンチプレス", "スクワット", "デッドリフト")

# 記録の仕方（種目ごとに 1つ）
RECORD_KINDS = {
    "weight_reps": "重さ×回数",
    "reps": "回数だけ",
    "count": "本数",  # 練習：カット練 10本 など
    "minutes": "時間（分）",  # 練習：7on7 20分 など
    "time": "時間（秒）",
    "distance_sets": "距離×本数",
    "cardio": "時間・距離（有酸素）",
}
PRESET_KINDS = {"プランク": "time"}  # 用意した種目のうち「重さ×回数」でないもの（有酸素は 部位で決まる）

# チームの競技（チームを作るときに選ぶ）。競技の欄（記録の画面の 最後のボタン）に 競技向けの種目が出る
SPORTS = {
    "football": "アメフト", "baseball": "野球", "soccer": "サッカー", "basketball": "バスケットボール",
    "volleyball": "バレーボール", "rugby": "ラグビー", "track": "陸上", "swimming": "水泳", "tennis": "テニス", "other": "その他",
}
SPORT_PART = "sport"  # 競技の欄の 部位のキー
# 競技ごとに 最初から用意する 練習（名前, 記録の仕方）。筋トレではなく、その競技の練習を入れる。
# ない競技は、チームで足していく
SPORT_EXERCISES = {
    "football": [
        ("カット練", "count"), ("ヒット練", "count"), ("タックル練", "count"), ("ブロック練", "count"),
        ("スタート練", "count"), ("ルート練", "count"), ("キャッチ練", "count"), ("パスラッシュ練", "count"),
        ("ボールキャリー練", "count"), ("スナップ練", "count"), ("キック練", "count"), ("アジリティ練", "count"),
        ("1on1", "count"), ("7on7", "minutes"), ("スクリメージ", "minutes"), ("ポジション練", "minutes"),
    ],
}
# 前の版で 競技の欄に入れていた 筋トレの種目。もう記録があるチームでは、消さずに ふつうの部位の欄へ移す
RETIRED_SPORT_PARTS = {
    "パワークリーン": "legs", "ハングクリーン": "legs", "プッシュプレス": "shoulders", "スレッドプッシュ": "legs",
    "スレッドプル": "legs", "ダッシュ": "legs", "プロアジリティ": "legs", "ボックスジャンプ": "legs",
    "メディシンボールスロー": "abs", "タイヤフリップ": "legs", "ネックトレーニング": "shoulders", "バーピー": "abs",
}
EXERCISE_NOTE_MAX = 200  # 種目ごとのメモ
EXERCISE_NAME_MAX = 30

# ---- 筋トレ ----
WORKOUT_MAX_MINUTES = 300  # 1回の筋トレとして記録できる 最大の時間
WORKOUT_BACKFILL_DAYS = 7  # 何日前の分まで「あとから記録」できるか
BACKFILL_DEFAULT_MINUTES = 60
HISTORY_DAYS = 90  # 履歴に出す日数
GRAPH_DAYS = 365  # グラフに出す日数

# ---- 体重・体脂肪率 ----
BODY_WEIGHT_MIN, BODY_WEIGHT_MAX = 20, 300  # kg（入れまちがいを はじく）
BODY_FAT_MIN, BODY_FAT_MAX = 1, 70  # %

# ---- セットの間の休憩タイマー ----
# 最初の長さ（秒）。種目ごとに ±15秒で変えられて、変えた長さは その人の その種目に覚えておく
REST_BIG3_SECONDS = 180  # ビッグ3：重い重さなので 長め
REST_SECONDS_BY_PART = {"chest": 120, "back": 120, "legs": 120, "shoulders": 120, "arms": 90, "abs": 90, "sport": 90}
REST_STEP_SECONDS = 15
REST_MIN_SECONDS = 15
REST_MAX_SECONDS = 600

# ---- トレーナーからのメニュー ----
MENU_TITLE_MAX = 40
MENU_NOTE_MAX = 300
MENU_MAX_ITEMS = 15  # 1つのメニューに入れられる 種目の数
MENU_MAX_ACTIVE = 20  # 同時に配れる メニューの数
MENU_MAX_SETS = 20
MENU_MAX_PHOTOS = 4  # メニューに付けられる 写真の数
MENU_KEEP_DAYS_AFTER_END = 183  # 期間がすぎても、この日数（半年）は「過去のメニュー」として見られる

# ---- 投稿 ----
REACTIONS = ["👍", "🔥", "💪", "👏", "😂", "😮"]  # スタンプの種類
POST_MAX_PHOTOS = 4
PHOTO_KEEP_DAYS = 183  # 写真の保存期間（半年）。すぎたら 写真だけ消す（記録・ひとこと・コメントは残る）
PHOTO_MAX_BYTES = 1_500_000  # 写真1枚の大きさの上限（画面側で 小さくしてから送る）
POST_BODY_MAX = 1000
COMMENT_MAX = 500
TIMELINE_PAGE = 20  # タイムラインを 1回に読みこむ数
QUICK_REPLY_MAX = 12  # トレーナーが登録できる よく使う言葉の数
QUICK_REPLY_BODY_MAX = 60
NOTICE_LIST = 50  # 知らせの一覧に出す数

# ---- ログイン ----
LOGIN_DAYS = 180  # 1回ログインすると この日数 そのまま使える
LOGIN_MAX_FAILURES = 10  # この回数 まちがえると しばらくログインできない
LOGIN_LOCK_MINUTES = 10
PASSWORD_MIN = 8
NAME_MAX = 20
