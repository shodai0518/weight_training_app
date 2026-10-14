@echo off
rem パソコンの中でアプリを動かす。開くアドレス： http://localhost:8200
cd /d "%~dp0"
if not exist .venv (
  python -m venv .venv
  .venv\Scripts\python -m pip install -r requirements-dev.txt
)
rem サーバーが動き出すのを待ってから ブラウザを開く（0.3秒ごとに確かめる。30秒たっても動かなければ そのまま開く）
rem タブを 2つ開く：localhost と 127.0.0.1 は ログインが別々なので、トレーナーと選手を 同時に試せる
start "" /min powershell -NoProfile -Command "for($i=0;$i -lt 100;$i++){try{Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8200/healthz -TimeoutSec 2 | Out-Null; break}catch{Start-Sleep -Milliseconds 300}}; Start-Process http://localhost:8200; Start-Sleep -Milliseconds 800; Start-Process http://127.0.0.1:8200"
rem --host localhost：localhost と 127.0.0.1 の両方で待ち受ける（片方だけだと localhost が 毎回2秒おそくなる）
.venv\Scripts\python -m uvicorn app.main:app --host localhost --port 8200
