# 実装計画書

更新日: 2026-08-20

## 方針

このリポジトリはPoC段階として進める。
最初の目標は、那須エリアGTFSを読み込み、指定停留所間の直通バス候補を返す `GTFSEngine` を実装すること。
FastAPI化、Google Maps連携、AI探索用グラフ構築は、Phase 1 の動作確認後に段階的に追加する。

## Phase 1: GTFS解析コア

ステータス: 完了

対象:

- `stops.txt`, `routes.txt`, `trips.txt`, `stop_times.txt` をGTFSディレクトリから読み込む
- `search_next_bus(origin_stop_id, dest_stop_id, departure_time)` を提供する
- `departure_time` は `"HH:MM"` または `"HH:MM:SS"` を受け付ける
- GTFS仕様に合わせて `24:10:00` のような24時超え時刻を内部で分に変換する
- 乗り換えなし、同一 `trip_id` の直通便のみを対象にする
- 出発時刻昇順で最大3件返す

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
- `tests/fixtures/minimal_gtfs/`
- `tests/test_gtfs_engine.py`

検証:

- `python -m pytest`
- 6 tests passed

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
