# 指示書レビュー

レビュー日: 2026-08-20

## 総評

FastAPI、Pydantic v2、GTFS、Google Maps API を組み合わせる構成は、AIエージェント向けの経路探索APIとして妥当です。
ただし、指示書のまま実装に入ると「日付を考慮した運行判定」「複数GTFSフィード統合」「タクシー料金推定」「グラフ構造の粒度」で仕様不足が出ます。
Phase 1 は小さく始められますが、後続の `/plan/graph-data` に耐えるにはデータモデルを先に固める必要があります。

## 実装前に決めるべき点

1. **対象日付**
   - 現在の `departure_time: HH:MM` だけでは、`calendar.txt` / `calendar_dates.txt` による運行日判定ができません。
   - APIでは `departure_date: YYYY-MM-DD` を追加するのが安全です。

2. **GTFSフィードの範囲**
   - 指示書では「那須塩原市・関東自動車」とありますが、同一GTFSなのか複数GTFSを統合するのかが未定義です。
   - 複数フィードを扱う場合、`stop_id` / `route_id` / `trip_id` の衝突回避が必要です。

3. **乗り換え**
   - Phase 1 は「乗り換えなし」で明確ですが、プロジェクト目的のマルチモーダル探索では乗り換えあり探索が必要になる可能性があります。
   - 初期実装では direct route のみに限定し、レスポンスに `transfers: 0` を含めるのがよいです。

4. **時刻表の時刻形式**
   - GTFSの時刻は `24:10:00` のように24時を超える場合があります。
   - 内部では分単位または秒単位に正規化し、表示時だけ `HH:MM` に戻すべきです。

5. **Google Maps APIの位置づけ**
   - Google Directions API / Routes API は `walking` と `driving` を扱えますが、`taxi` という移動モードは標準ではありません。
   - `taxi` は `driving` ETA + 独自料金モデルとして扱う設計にする必要があります。

6. **タクシー料金**
   - 「推定運賃」はGoogleのレスポンスだけでは一般に得られません。
   - 初期実装では距離・時間から概算する料金モデルを設定ファイル化するのが現実的です。

7. **運賃**
   - GTFSに `fare_attributes.txt` / `fare_rules.txt` が含まれるとは限りません。
   - Phase 1 のバス運賃は nullable にし、運賃データがある場合のみ返す設計が安全です。

8. **スポットデータ**
   - ノード例には観光地が含まれますが、観光地マスタの取得元が未定義です。
   - `data/spots/*.json` のようなローカルマスタを用意するか、外部データを指定する必要があります。

9. **OpenAPI for AI**
   - AIエージェント用途では、単なるSwagger自動生成だけでは足りません。
   - query/body/response の Pydantic モデルに `description`, `examples`, enum, nullable を明示するべきです。

## 推奨する初期スコープ

Phase 1 は次の範囲に絞るのが妥当です。

- GTFSディレクトリから `stops.txt`, `routes.txt`, `trips.txt`, `stop_times.txt` を読み込む
- `search_next_bus()` は同一 `trip_id` 内で origin が destination より前に出る直通便のみ返す
- 時刻は24時超えを許容して内部分数に変換する
- 返却件数は出発時刻昇順で最大3件
- `service_id` と運行日判定は Phase 1.5 として追加する

## 推奨ディレクトリ構成

```text
nasu-multimodal-route-api/
  app/
    api/
    core/
    models/
    services/
  data/
    gtfs/
    spots/
  docs/
    project-instruction.md
    spec-review.md
  tests/
    fixtures/
```

## 参照確認メモ

- 那須塩原市の地域バスGTFSデータは公開ページがあり、2026年時点の更新情報が確認できる。
- CKAN横断検索上では、GTFSデータセットのライセンスは Creative Commons Attribution として確認できる。
- Google Maps Platform の Routes API / Directions API は徒歩・自動車ルートを扱えるが、タクシー専用モードは標準モードとしては扱わない。

## 次の作業案

1. Pythonプロジェクトの最小構成を作る
2. Phase 1 の `GTFSEngine` を実装する
3. GTFS最小fixtureで直通便検索のユニットテストを作る
4. FastAPIのモデルと `/transit/routes` に接続する
