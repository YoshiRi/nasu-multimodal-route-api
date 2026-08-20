# 実装計画書

更新日: 2026-08-20

## 方針

このリポジトリはPoC段階として進める。
最初の目標は、那須エリアGTFSを読み込み、CLI上で指定停留所間の直通バス候補を検索できるようにすること。
FastAPI化、Google Maps連携、AI探索用グラフ構築は、Phase 1 の動作確認後に段階的に追加する。

## Phase Goal

### Phase 1 Goal

**CLI（ターミナル）上でGTFSデータを解析し、バス停間の時刻表検索ができる**

確認方法:

- スクリプトを実行し、指定時刻以降に乗れる直通バスの便名・発着時刻がコンソールに出力されること。

### Phase 2 Goal

**FastAPIを起動し、Web API（Swagger UI）からバス・徒歩経路を個別に検索できる**

確認方法:

- ブラウザ（`http://localhost:8000/docs`）からバス時刻検索APIやGoogle連携の徒歩計算APIを実行し、正しいJSONが返ること。

### Phase 3 Goal

**AIエージェント（Function Calling）がAPIを解釈・呼び出しでき、全地点間の移動グラフを一括取得できる**

確認方法:

- `openapi.json` をAIに読み込ませて自律実行できること。
- `POST /plan/graph-data` 1回で複数スポット間の移動コストが網羅されたJSONが手に入ること。

## Phase 1: GTFS解析コア

ステータス: 完了

対象:

- `stops.txt`, `routes.txt`, `trips.txt`, `stop_times.txt` をGTFSディレクトリから読み込む
- `search_next_bus(origin_stop_id, dest_stop_id, departure_time)` を提供する
- `departure_time` は `"HH:MM"` または `"HH:MM:SS"` を受け付ける
- GTFS仕様に合わせて `24:10:00` のような24時超え時刻を内部で分に変換する
- 乗り換えなし、同一 `trip_id` の直通便のみを対象にする
- 出発時刻昇順で最大3件返す
- CLIからGTFSディレクトリ、出発停留所、到着停留所、出発時刻、任意の日付を指定できる
- CLIのコンソール出力に便名・発着時刻・所要時間を表示する

返却項目:

- `route_id`
- `route_short_name`
- `route_long_name`
- `line_name`
- `trip_id`
- `trip_headsign`
- `origin_stop_id`
- `destination_stop_id`
- `departure_time`
- `arrival_time`
- `duration_min`

このPhaseでは扱わない:

- `calendar.txt` / `calendar_dates.txt` による運行日判定
- 乗り換えあり探索
- GTFS faresによる運賃
- 複数GTFSフィード統合

実装済み:

- `app/services/gtfs_engine.py`
- `app/cli.py`
- `tests/fixtures/minimal_gtfs/`
- `tests/test_gtfs_engine.py`
- `tests/test_cli.py`

CLI実行例:

```bash
python -m app.cli search-bus \
  --gtfs-dir tests/fixtures/minimal_gtfs \
  --from stop_yumoto \
  --to stop_ropeway \
  --time 10:00 \
  --date 2026-08-17
```

インストール後のconsole script:

```bash
nasu-route search-bus \
  --gtfs-dir tests/fixtures/minimal_gtfs \
  --from stop_yumoto \
  --to stop_ropeway \
  --time 10:00 \
  --date 2026-08-17
```

検証:

- `python -m pytest`
- 14 tests passed
- `python -m app.cli search-bus ...` で便名・発着時刻のコンソール出力を確認
- `nasu-route search-bus ...` で便名・発着時刻のコンソール出力を確認

## Phase 1.5: GTFS実データ対応強化

ステータス: 一部完了

- `departure_date` を受け付ける
- `service_id` と運行日を判定する
- optional file 欠落時の挙動を整理する
- 複数フィードを扱う場合のID prefix 方針を決める

実装済み:

- `search_next_bus(..., departure_date=None)` の任意引数追加
- `calendar.txt` による曜日・有効期間判定
- `calendar_dates.txt` の `exception_type=1` 追加運行、`exception_type=2` 運休判定
- `calendar.txt` / `calendar_dates.txt` がないGTFSでは、従来通り全 `service_id` を候補扱いにするPoC fallback

検証:

- `python -m pytest`
- 11 tests passed

残作業:

- 複数フィードを扱う場合のID prefix 方針
- 実GTFS ZIPを読み込んだfixtureまたは手動検証
- 那須塩原市などの実GTFS ZIP取得・展開は、データ更新確認を含む別工程として扱う

### TODO: 最新GTFSデータ自動取得・展開スクリプト

ステータス: 未着手

このTODOはユーザー指示により後回し。実行前に、実際の配布URLと認証情報の取り扱いを確認する。

目的:

- Phase 1で構築したGTFSベースのバス検索システムで利用する、最新GTFSデータを自動取得・展開する。
- 外部サーバーからGTFSデータ（ZIP形式）をダウンロードし、プロジェクト内の指定ディレクトリに自動展開する `scripts/download_gtfs.py` を作成する。

対象データ:

1. 那須塩原市（ゆーバス）
   - 認証: 不要（オープンデータ）
   - ダウンロードURL例: `https://opendata.city.nasushiobara.lg.jp/dataset/gtfs/yu-bus.zip`
   - 上記URLはダミー扱い。実装時は引数または環境変数からURLを渡せるようにする。

2. 関東自動車（路線バス）
   - 認証: 必要（PTD-HSのUID）
   - ダウンロードURL仕様: 環境変数 `KANTO_BUS_GTFS_URL` に設定された、UIDパラメータを含むエンドポイントから取得する。

実装要件:

- 言語: Python 3.11+
- ライブラリ: `requests`, `python-dotenv`
- 標準ライブラリ: `zipfile`, `os`, `pathlib`, 必要に応じて `tempfile`, `shutil`, `logging`
- `.env` ファイルから対象URLを読み込む
- 対象URLにGETリクエストを送り、ZIPファイルをメモリ上または一時ファイルとしてダウンロードする
- ダウンロードしたZIPファイルを以下へ展開する
  - 那須塩原市: `data/gtfs/nasushiobara/`
  - 関東自動車: `data/gtfs/kanto_auto/`
- 古いデータが存在する場合は上書き、または事前にディレクトリをクリアする
- 実行状況（成功・失敗・エラー内容）を `print` または `logging` で標準出力する

出力物:

- `scripts/download_gtfs.py`
- 追加ライブラリが必要な場合は `requirements.txt` または `pyproject.toml` に反映する
- `.env.example` に以下の環境変数例を追加する
  - `NASUSHIOBARA_GTFS_URL`
  - `KANTO_BUS_GTFS_URL`

実装時の注意:

- 実URLとPTD-HS UIDは機密または利用規約に関わる可能性があるため、repoに実値をcommitしない。
- ZIP展開では Zip Slip 対策として、展開先ディレクトリ外へファイルが出ないことを検証する。
- データ更新のたびに `data/gtfs/` 以下が大量差分にならないよう、GTFS実データは `.gitignore` 対象のままにする。

## Phase 2: FastAPIルーター

ステータス: 未着手

- `/transit/stops/nearby`
- `/transit/routes`
- `/non-transit/route`
- 設定ファイルまたは環境変数でGTFSパスとGoogle API keyを読み込む

## Phase 3: OpenAPI整理

ステータス: 未着手

- Pydantic v2モデルに `Field(description=...)` と examples を追加する
- AIエージェントが使いやすいレスポンスモデルに整える
- エラー応答を標準化する

## 現時点の設計メモ

- タクシーはGoogle Maps上の専用modeではなく、`driving` ETA + 独自料金モデルとして扱う。
- バス運賃はGTFSに含まれない可能性があるため、初期APIでは nullable とする。
- 本番に近づける段階では、経路探索リクエストに `departure_date` を必須化する。
