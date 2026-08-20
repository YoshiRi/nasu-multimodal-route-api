# AI開発指示書：那須エリアマルチモーダル経路探索API（GTFSベース）

## 1. プロジェクト目的

那須エリアにおける路線バス（GTFS）、徒歩、タクシーを組み合わせた「マルチモーダル経路探索バックエンド」を実装する。
外部のAIエージェント（Function Calling / Tool Use）が自律的に経路を検索し、工程表を組み立てられるOpenAPI規格に準拠したFastAPIサーバーを構築する。

## 2. 技術スタック

- **Language:** Python 3.11+
- **Framework:** FastAPI, Pydantic v2
- **Data Processing:** Pandas, `gtfs-kit`
- **External API:** Google Maps Directions API (Walking / Taxi)
- **Data Source:** 那須塩原市・関東自動車 GTFSオープンデータ (ZIP / CSV)
- **Specification:** OpenAPI 3.0 (Swagger UI自動生成)

## 3. データモデル規格 (JSON Schema)

### 3.1 ノード（地点）

```json
{
  "id": "spot_nasu_ropeway",
  "name": "那須ロープウェイ",
  "type": "sightseeing",
  "location": {
    "lat": 37.1234,
    "lng": 139.9876
  },
  "nearest_bus_stop": {
    "id": "stop_001",
    "name": "那須ロープウェイ",
    "distance_m": 50
  }
}
```

### 3.2 エッジ（移動セグメント）

```json
{
  "from_id": "stop_yumoto",
  "to_id": "stop_ropeway",
  "mode": "bus",
  "departure_time": "10:15",
  "arrival_time": "10:35",
  "duration_min": 20,
  "cost_jpy": 500,
  "details": {
    "line_name": "那須線",
    "trip_headsign": "那須ロープウェイ行"
  }
}
```

## 4. REST API エンドポイント仕様

### 1. 近隣バス停検索

- **Endpoint:** `GET /transit/stops/nearby`
- **Params:** `lat: float`, `lng: float`, `radius_m: int = 500`
- **Response:** 指定座標を中心とした最寄りバス停リスト（距離昇順）

### 2. GTFS時刻表経路検索

- **Endpoint:** `GET /transit/routes`
- **Params:** `origin_stop_id: str`, `destination_stop_id: str`, `departure_time: str (HH:MM)`
- **Response:** 指定時刻以降に乗車可能なバス便の一覧（乗車・降車時刻、所要時間、運賃）

### 3. 徒歩・タクシー経路計算

- **Endpoint:** `GET /non-transit/route`
- **Params:** `origin_lat: float`, `origin_lng: float`, `dest_lat: float`, `dest_lng: float`, `mode: str (walking | taxi)`
- **Response:** Google Directions APIを内部呼び出しした距離、所要時間、推定運賃

### 4. 統合グラフ構築（AI探索用）

- **Endpoint:** `POST /plan/graph-data`
- **Body:** 拠点ID、訪問希望候補地IDリスト、希望出発時刻
- **Response:** 選択された全地点間のバス・徒歩・タクシー移動コスト（所要時間・時刻）を網羅したグラフ構造JSON

## 5. 実装ロードマップ & LLMタスク指定

ローカルLLMに対し、以下のステップ順にモジュール単位でコード作成を命じること。

### [Phase 1] GTFS解析コアモジュールの実装

GTFS（`stops.txt`, `routes.txt`, `trips.txt`, `stop_times.txt`）を読み込み、指定されたバス停間・指定時刻での最短乗車便を抽出する `GTFSEngine` クラスを実装させる。

### [Phase 2] FastAPIルーターの実装

`GTFSEngine` および Google Maps Directions APIを呼び出す非同期エンドポイント群（`/transit/routes`, `/non-transit/route`）を定義させる。

### [Phase 3] OpenAPI仕様のクリーンアップ

AIエージェントが各APIの役割・引数を正確に理解できるよう、各エンドポイントの `description` および `pydantic` モデルの `Field(description=...)` を詳細化させる。

## 6. LLM用入力プロンプト（指示開始時コピペ用）

```text
あなたはこのプロジェクトのリードエンジニアです。上記「AI開発指示書」の要件に従って実装を行います。

【最初のタスク】
[Phase 1] に着手してください。
Pythonの `pandas` を利用し、GTFSデータ（CSV群）から以下の条件を満たすクラス `GTFSEngine` を作成してください。

1. 初期化時にGTFSファイルが格納されたディレクトリパスを受け取る。
2. `search_next_bus(origin_stop_id: str, dest_stop_id: str, departure_time: str) -> list[dict]` メソッドを実装する。
3. 指定された出発時刻（"HH:MM" 形式）以降に `origin_stop_id` を発車し、乗り換えなしで `dest_stop_id` に到達する便を最大3件抽出して返却する。
4. 該当する便の「路線名」「乗車時刻」「降車時刻」「所要時間（分）」を辞書形式で含める。

まずこのPythonコードのみを提示してください。
```
