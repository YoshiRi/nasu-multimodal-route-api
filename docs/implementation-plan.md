# 実装計画書

更新日: 2026-10-12

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

- （2026-08-21時点では未着手だった「複数フィードを扱う場合のID prefix方針」「実GTFS ZIPでの検証」は、いずれも下記セクションで完了済み）

### ID衝突調査結果（2026-08-21）

3フィード（nasushiobara, nasutown, kanto_auto）間で `stop_id` / `route_id` / `trip_id` / `service_id` の衝突を調査。

- `route_id` / `trip_id` / `service_id`: 3フィード間で衝突なし
- `stop_id`: **nasushiobara ⇔ nasutown間で105件衝突**（nasushiobara 219件中、nasutown 116件中）。関東自動車は`S0904...`のように事業者コードがID自体に入っているため衝突なし。
- 衝突している`stop_id`は同一停留所の共有ではなく、両フィードが独立に同じ連番体系（`1_01`, `10_01`など）を使っているだけの偶然の一致。停留所名・座標は全て不一致（例: `stop_id=1_01`は那須塩原市で「西那須野駅西口」、那須町で「追分」）。
- 現状は`GTFSEngine`が1フィードずつしか扱わないため実害はないが、フィード横断のnearby検索や統合グラフ（Phase 3の`/plan/graph-data`）を実装する際は、各フィードのID（特に`stop_id`）に`nasushiobara:`のようなフィード名プレフィックスを付けて名前空間分離する方針にする。

対応済み: `GTFSEngine.from_feeds({feed_name: gtfs_dir, ...})` を実装（`app/services/gtfs_engine.py`）。各フィードを個別に読み込んだ上で `stop_id` / `route_id` / `trip_id` / `service_id` を `"<feed_name>:"` でプレフィックスして結合する。`stop_times`・`trips`・`calendar`・`calendar_dates`もfeed内で整合するように同じプレフィックスを適用するため、`search_next_bus` / `nearby_stops` / `active_service_ids` はマージ後もそのまま動作する。3フィード実データ（nasushiobara + nasutown + kanto_auto、計4565停留所）で全IDがユニークになることを確認済み。`tests/fixtures/minimal_gtfs_other/`（IDを意図的に衝突させたfixture）でも検証。

API結線: 完了。`app/core/config.py` の `Settings.gtfs_feeds` は `dict[str, Path]` に変更し、`discover_gtfs_feeds()` が `data/gtfs/` 配下の有効なGTFSサブディレクトリ（stops/routes/trips/stop_times全て揃っているもの）を自動検出する。

- `GTFS_DIR` 環境変数が設定されていればそれを唯一のフィード（`{"default": ...}`）として使う（従来通り、IDはプレフィックスなし）。
- 未設定時、`data/gtfs/` 配下に有効なフィードが1つ以上見つかればそれらを使う。1つなら単一フィードとしてそのまま（プレフィックスなし）、2つ以上なら `GTFSEngine.from_feeds()` でマージ（`"<feed_name>:"` プレフィックス）。
- どちらもなければ `tests/fixtures/minimal_gtfs`（プレフィックスなし）にフォールバック。
- `app/api/deps.py` の `get_gtfs_engine` がこのロジックでエンジンをキャッシュする。

検証: 那須塩原市・那須町・関東自動車の3フィードをダウンロード済みの状態で `uvicorn app.main:app` を起動し、`/transit/stops/nearby` が3事業者の停留所を横断して距離昇順で返すこと、`/transit/routes` がプレフィックス付きID（例: `nasushiobara:1_01`）で検索できることを確認。`GTFS_DIR` で単一ディレクトリを指定した場合は、従来通りプレフィックスなしのIDで動作することも確認。

テスト: `tests/test_config.py`（`discover_gtfs_feeds`）を追加。`tests/test_api_transit.py`はローカルの`data/gtfs/`実データの有無に影響されないよう、`app.dependency_overrides[get_gtfs_engine]`でfixtureに固定するよう修正。
- 那須塩原市などの実GTFS ZIP取得・展開は、データ更新確認を含む別工程として扱う

### TODO: 最新GTFSデータ自動取得・展開スクリプト

ステータス: 実装済み（実URL未設定）

`scripts/download_gtfs.py` を実装。各フィードのダウンロードURLは `.env` の環境変数から読み込み、未設定のフィードはスキップする設計。Zip Slip対策として、展開前に全エントリの展開先パスが `dest_dir` 配下に収まることを検証してから展開する。既存ディレクトリは展開前にクリアする。

実装済み:

- `scripts/download_gtfs.py`（`download_zip`, `extract_gtfs_zip`, `update_feed`, `main`）
- `.env.example`（`NASUSHIOBARA_GTFS_URL`, `NASUTOWN_GTFS_URL`, `KANTO_BUS_GTFS_URL`）
- `python-dotenv` を依存関係に追加
- `tests/test_download_gtfs.py`: Zip Slip拒否、既存ファイル上書き、env未設定時のスキップ、`httpx.MockTransport` によるダウンロード処理のテスト

検証:

- `python -m pytest` で24 tests passed
- ローカルHTTPサーバー越しに `python scripts/download_gtfs.py --feed nasushiobara` を実行し、Downloadsの実ZIPを手動展開した結果と`diff -rq`で完全一致することを確認

実URL・秘匿方針:

- 那須塩原市地域バス・那須町町民バスは `https://api.gtfs-data.jp/v2/organizations/<org>/feeds/<feed>/files/feed.zip` 形式、認証不要（S3への302リダイレクト）。**この2つは公開URLなので秘密にする必要がなく**、`scripts/download_gtfs.py` の `FEEDS` にデフォルトURLとしてハードコードしている。`.env`での設定は不要（上書き用の`<SLUG>_GTFS_URL`は任意で使える）。
- 関東自動車（PTD-HS）は `GetData?agency_id=0904&uid=<APIキー>` の形で**URLそのものにAPIキーが埋め込まれる**ため、URL全体を隠す代わりに、公開部分（エンドポイントと`agency_id=0904`）をソースの`default_url`テンプレートに書き、秘密の`uid`値だけを`.env`の`KANTO_BUS_GTFS_API_KEY`に置く設計にした（`FeedConfig.resolve_url()`が実行時に合成する）。
- `python scripts/download_gtfs.py` で全フィードを実際にダウンロード・展開し、Downloadsの手動展開データと `diff -rq` で完全一致することを確認済み。`.env`に`KANTO_BUS_GTFS_API_KEY`のみを置いた状態で3フィードとも取得できることも確認済み。
- 副次対応: `httpx`の標準ロガーがリクエストURL（=APIキー入り）をINFOログにそのまま出力していたため、`logging.getLogger("httpx").setLevel(logging.WARNING)`で抑止した。

更新状況の記録（`data/gtfs_manifest.json`）:

- ダウンロード成功時に、各フィードの `feed_info.txt` から `feed_start_date` / `feed_end_date` / `feed_version` を読み取り、取得日時・ZIPのsha256とともに `data/gtfs_manifest.json` に記録する。
- `data/gtfs/` はGTFS実データごと`.gitignore`対象だが、`data/gtfs_manifest.json` はそのサブディレクトリの外にあるため通常どおりgit管理される。更新のたびにcommitすれば、`git log`がそのまま「いつ・どのフィードを更新したか」の履歴になる。
- `python scripts/download_gtfs.py --status` で、フィードごとの最終取得日時・`feed_end_date`・状態（`ok` / `update soon (Nd left)` / `EXPIRED` / `not downloaded yet`）を一覧表示する。`feed_end_date`まで30日以内、または経過済みの場合に警告する。

関東自動車:

- 配信元は PTD-HS（`https://www.ptd-hs.jp`）。`POST /GetAgencyList?uid=<APIキー>` で事業者一覧が取得でき、`agency_id=0904`が関東自動車と判明した。
- `GET /GetData?agency_id=0904&uid=<APIキー>` でGTFS ZIP（stops 4230件、trips 4425件）を取得・`GTFSEngine`での読み込み・直通便検索を確認済み。`feed_end_date`は`20270730`。
- APIキー・URLは `.env` の `KANTO_BUS_GTFS_API_KEY` / `KANTO_BUS_GTFS_URL` に設定済み（repoにはcommitしない）。

残作業:

- 実運用では `--status` を定期実行（cronなど）してPhase 3以降の運行日判定に古いデータを使わないようにする

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

ステータス: 一部完了（バス系のみ、`/non-transit/route`はロック中）

方針:

- ユーザー指示により、Google Maps連携が必要な `/non-transit/route`（徒歩・タクシー）はこのPhaseでは実装しない。GTFSベースのバス機能のみ進める。
- 実GTFSデータはまだDownloadsにしかなく `data/gtfs/` に未配置のため、`GTFS_DIR` 環境変数が未設定の場合は `tests/fixtures/minimal_gtfs` をデフォルトで使う（`app/core/config.py`）。実データ配置後は `GTFS_DIR` を向け替えるだけでよい。

対象:

- `GET /transit/stops/nearby`（`lat`, `lng`, `radius_m`、距離昇順）
- `GET /transit/routes`（`origin_stop_id`, `destination_stop_id`, `departure_time`, 任意の`departure_date`）
- `GET /non-transit/route`: 未着手（ロック中、Google API key入手後に着手）

実装済み:

- `app/main.py`, `app/api/transit.py`, `app/api/deps.py`, `app/core/config.py`, `app/models/transit.py`
- `GTFSEngine.nearby_stops()` / `haversine_distance_m()`（`app/services/gtfs_engine.py`）
- `tests/test_api_transit.py`

検証:

- `python -m pytest` で19 tests passed
- `uvicorn app.main:app` 起動後、`curl` で `/health`, `/transit/routes`, `/transit/stops/nearby` の200応答とJSONを確認
- `http://localhost:8000/docs`, `/openapi.json` の200応答を確認

残作業:

- （2026-08-23時点で完了: `/non-transit/route`はGoogle Mapsではなく既存のORS連携で実装済み。詳細はPhase 5参照）
- （完了: 実GTFSデータは`data/gtfs/`に配置済み、`GTFS_DIR`未設定時は自動検出される）

## Phase 3: OpenAPI整理

ステータス: 一部完了（バスのみ）

方針: `/plan/graph-data` はバス・徒歩・タクシー統合が本来の仕様だが、`/non-transit/route`がロック中のため、今回はバスのみで先行実装。徒歩・タクシー分はGoogle API key取得後にedgeへ追加する。

対象・実装済み:

- `app/models/errors.py`: 共通エラーレスポンス `ErrorResponse`（`detail: str`）。`/transit/routes`・`/plan/graph-data`の400レスポンスをOpenAPI上で明示（`responses={400: {"model": ErrorResponse}}`）
- `app/models/transit.py` / `app/models/plan.py`: 全フィールドに`description`と`examples`を追加
- 各エンドポイントに`summary`/`description`を追加、`app/main.py`に`openapi_tags`（transit/plan）とバージョン情報を追加
- `POST /plan/graph-data`（`app/api/plan.py`）: リクエストの`stop_ids`（2〜15件）の全順序対について`search_next_bus`をバッチ実行し、`edges`配列として返す。各edgeは`mode: "bus"`固定、`bus_options`に直通便候補（最大3件、空配列もあり得る）を含む
- `tests/test_api_plan.py`: 正常系（往復で異なる便）、直通便なし、`stop_ids`不足時422、重複ID時400を検証

検証:

- `python -m pytest` で44 tests passed
- `uvicorn app.main:app`起動後、`/docs`・`/openapi.json`が200で、`tags`に`transit`/`plan`が反映されていることを確認
- 実データ（3フィード統合）で`POST /plan/graph-data`にプレフィックス付き`stop_id`を渡し、正しい`edges`が返ることを確認

残作業:

- `/non-transit/route`実装後、`GraphEdge`に徒歩・タクシーedgeを追加する
- `openapi.json`を実際にAIエージェントに読み込ませた自律実行テストは未実施

## Phase 4: AIコンシェルジュ向け一括ルート提案（route-plan）

ステータス: 実装完了（詳細は下記「実装結果」「実ORS_API_KEYでの検証」参照）

背景: 2026-08-22付けで、外部AIエージェント（AIトラベルコンシェルジュ）がFunction Calling経由で呼ぶ一括行程提案エンドポイントの実装指示を受けた。Phase 3の`/plan/graph-data`（全ペアのコストグラフをAIに渡し、行程の組み立てはAI側に委ねる設計）とは異なり、こちらはAPI側が1つの行程案（bus/walking/stayのステップ配列）を直接組み立てて返す、より上位のオーケストレーション層になる。両者は代替ではなく別用途として併存させる。

### レビュー結果（実装者観点）

合意（指示どおり採用）:

- **`status`/`ai_hint`による自己修復パターン**: 「リクエストは正しいが行程が成立しない」場合はHTTP 500ではなくHTTP 200 + `status="error"` + 自然言語の`ai_hint`で返す設計に合意。ただし**リクエスト自体が不正な場合（未知のspot_id、不正な時刻形式など）は既存の`ErrorResponse`/`HTTPException`(4xx)パターンを維持**し、`status="error"`と混同しない。「入力エラー」と「入力は正しいが解なし」を明確に分けて実装する。
- **脱Google Maps・OpenRouteService採用**: 費用面・公開時のスケーラビリティ面で妥当。`get_walking_route`は非同期関数（`httpx.AsyncClient`）とし、呼び出し元のルーターも`async def`にする。
- **モック行程ロジック**（TSPなし、入力配列の順序どおりに拠点→スポット→拠点で1パターンのみ計算）: PoCとして妥当。ORS呼び出し回数もスポット数に比例する程度に収まり、無料枠のレート制限面でも安全な設計。
- `.env.example`への`ORS_API_KEY`追加は、既存の秘密情報管理方針（`.env`はgitignore、実値はcommitしない、ログにキーを出さない）をそのまま踏襲する。

実装者判断で調整する点（合意の上で、指示から変更して採用）:

- **ファイル配置**: 指示にある`app/schemas.py`・`app/routers/planner.py`は、既存の`app/models/*.py`・`app/api/*.py`という確立済みの構成と食い違うため、既存構成に合わせて `app/models/route_plan.py` と `app/api/route_plan.py` にする。
- **依存関係管理**: `requirements.txt`は使わない。本プロジェクトは`pyproject.toml`（setuptools）で依存管理しており、`fastapi`/`uvicorn`/`httpx`/`pydantic`は既に`[project.dependencies]`にあるため追加不要。新規に必要なのは`ORS_API_KEY`の設定のみ。
- **エンドポイントパス**: `POST /api/route-plan`ではなく、既存の`/plan/graph-data`と同じ`plan`タグ・プレフィックスに揃えて `POST /plan/route-plan` にする（URL体系の一貫性）。
- **リクエストフィールド名**: `date`は`datetime.date`と紛らわしく、既存API（`/transit/routes`・`/plan/graph-data`）は`departure_date`という名前を使っているため、それに合わせて`departure_date`に統一する。
- `target_spot_ids`のField descriptionにある「システムが順序を最適化するため順不同」という説明は、実際のv1実装（配列順どおりに回るモック）と矛盾する。AIエージェント向けOpenAPI descriptionが実装と食い違うのは実害があるため、「現バージョンでは配列順で訪問します（並び替え最適化は未実装）」という記述に修正して実装する。
- GTFSデータ取得は既存の`app/api/deps.py`の`get_gtfs_engine`（マルチフィード自動マージ済み）をそのまま再利用する。新規のGTFS読み込み経路は作らない。

未確定・実装前にブロッカーとなる点（要ユーザー判断）:

- **スポットマスタデータが存在しない**（最重要ブロッカー）: `base_location_id`/`target_spot_ids`はGTFSの`stop_id`ではなく観光地スポットのIDだが、本プロジェクトには現状スポットの緯度経度・名称を保持するマスタが一切ない。これは`docs/spec-review.md`の未決事項8番と同一の課題で、今回のPhase 4着手により仮説ではなく実際のブロッカーになった。実装には最低限 `{spot_id, name, lat, lng}` を持つデータソースが必要。なお、最寄りバス停は既存の`GTFSEngine.nearby_stops()`で動的に解決できるため、`nearest_stop_id`を事前計算・保持する必要はない。
- ORS呼び出し失敗時（レート制限・タイムアウト等）の扱いが指示に明記されていない。`status="error"` + `ai_hint`パターンに寄せる想定でよいか要確認。
- `stay`ステップの滞在時間を誰が/どう決めるか未定義。スポットマスタに`dwell_minutes`を持たせるか、固定値（例: 60分）で仮置きするか要確認。
- タクシーモードはこのエンドポイントのスコープ外という理解でよいか（指示文に言及なし、Phase 2/3では`/non-transit/route`として別途ロック中）。

### 実装結果（2026-08-22）

ステータス: 実装完了（バス＋徒歩、PoCスポットマスタ・実ORS_API_KEYとも検証済み）

- スポットマスタは `data/spots/nasu_spots.json` にPoC用の3件（那須塩原駅・那須湯本温泉・那須ロープウェイ）を作成。座標は`tests/fixtures/minimal_gtfs/stops.txt`と一致させ、テストで最寄り停留所解決（`GTFSEngine.nearby_stops()`）がそのまま既存GTFSフィクスチャに紐づくようにした。`nearest_stop_id`は保持せず、常に動的解決する。
- `app/services/spot_repository.py`: `SpotRepository`（JSON読み込み、`spot_id`未存在時は`SpotNotFoundError`）
- `app/services/ors_client.py`: `get_walking_route()`（非同期、`httpx.AsyncClient`、GET `.../directions/foot-walking`）。レスポンス形式はORS公式ドキュメントのGeoJSON構造（`features[0].properties.summary.{duration,distance}`）どおりで、実APIキーで動作確認済み（2026-08-22、下記「実ORS_API_KEYでの検証」参照）。
- `app/services/route_planner.py`: `build_route_plan()`が本体。拠点→スポット1→スポット2→…→拠点の順に、各区間を「徒歩(スポット→最寄り停)→バス→徒歩(最寄り停→スポット)」として計算。バスが見つからない区間があれば直ちに`status="error"`＋`ai_hint`を返す。ORS呼び出し失敗（`OrsRequestError`）も同様に`status="error"`に変換する（500にしない）。滞在時間は固定`DWELL_MINUTES=60`分。最寄り停留所探索半径は`NEAREST_STOP_RADIUS_M=1500`m。
- `app/models/route_plan.py`, `app/models/errors.py`（既存流用）, `app/api/route_plan.py`（`POST /plan/route-plan`）, `app/core/config.py`（`Settings.spots_file`、`SPOTS_FILE`環境変数）, `app/api/deps.py`（`get_spot_repository`）
- `.env.example`に`ORS_API_KEY`・`GTFS_DIR`/`SPOTS_FILE`のコメントを追加
- テスト: `tests/test_spot_repository.py`, `tests/test_ors_client.py`（`httpx.MockTransport`でORSレスポンスをモック）, `tests/test_route_planner.py`, `tests/test_api_route_plan.py`。`tests/fixtures/minimal_gtfs`に午後の復路便`trip_reverse2`を追加（往復が成立する成功シナリオのテストに必要。既存テストへの影響は`tests/test_api_plan.py`の期待値更新のみ）。

検証:

- `python -m pytest` で60 tests passed
- `uvicorn app.main:app`起動後、`/openapi.json`に`/plan/route-plan`が追加されていること、`ORS_API_KEY`未設定時に500ではなく`status="error"`のHTTP 200が返ること、未知の`spot_id`でHTTP 400が返ることを確認

### 実ORS_API_KEYでの検証（2026-08-22）

`.env`に実際に発行された`ORS_API_KEY`を設定し、`get_walking_route()`単体・`/plan/route-plan`エンドツーエンドの両方で実APIレスポンスを確認した。

- `get_walking_route(37.0950, 140.0010, 37.1240, 139.9850)`（那須湯本温泉→那須ロープウェイ）で `WalkingRoute(duration_min=58, distance_m=4815.0)` が返り、実装時に前提していたレスポンス形式（`features[0].properties.summary.{duration,distance}`）は正しかったことを確認。パース処理の修正は不要だった。
- fixtureのスポット座標はGTFS停留所座標と一致させてあるため、`/plan/route-plan`のE2E確認では徒歩区間が0分/0mで返るのは想定通り（同一座標間のクエリのため）。実スポットマスタでは実距離になる。

残作業・未解決事項:

- ORS呼び出し失敗時に`status="error"`とする設計は実装者判断で採用した。ユーザー未確認のため、要望と異なる場合は調整する
- 最寄り停留所探索半径1500mはPoCの仮値
- （完了: タクシーは2026-08-23に「最寄り停留所なし・徒歩3000m超」時のフォールバックとして追加済み）
- （完了: スポットマスタは2026-08-29に3件→29件に拡充。詳細は下記セクション参照）

### 追加: 滞在時間の補助入力（2026-08-22）

`target_spot_ids`を`list[str]`から`list[TargetSpot]`（`{spot_id: str, dwell_minutes: int | None}`）に変更。`dwell_minutes`省略時は`DEFAULT_DWELL_MINUTES=60`分を使う。`app/models/route_plan.py`の`DEFAULT_DWELL_MINUTES`定数、`app/services/route_planner.py`はターゲットごとの滞在時間リストを組み立てて各`stay`ステップに反映する。

検証: `tests/test_route_planner.py`・`tests/test_api_route_plan.py`にカスタム滞在時間（90分）・デフォルト滞在時間（60分）・`dwell_minutes<=0`のバリデーションエラー（422）のテストを追加。`tests/fixtures/minimal_gtfs`に往復シナリオ用の復路便を`trip_reverse3`（15:00発）まで拡充（長めの滞在でも既存fixtureで往復が成立するようにするため）。`python -m pytest`で64 tests passed。

### 追加: 集合時間（予約制約）の補助入力（2026-08-22）

`TargetSpot`に`required_arrival_time: str | None`（"HH:MM"）を追加。予約・集合時刻の制約を表す。

- 計算上の到着時刻が`required_arrival_time`より**早い**場合: `mode="wait"`のステップを挿入し、到着時刻から集合時刻まで待機してから`stay`を開始する（時刻はズレない）。
- 計算上の到着時刻が`required_arrival_time`より**遅い**場合: 予約に間に合わないため、その時点で`status="error"`（`_missed_reservation_response`）を返し、`ai_hint`に集合時刻に間に合わない旨と対処案を含める。
- `ItineraryStep.mode`に`"wait"`を追加（既存の`bus`/`walking`/`stay`と区別し、AIが「待機時間」であることを説明しやすくするため）。

検証: `test_build_route_plan_inserts_wait_step_when_early_for_reservation`・`test_build_route_plan_returns_error_when_reservation_cannot_be_met`（および対応するAPIレベルテスト）を追加。`python -m pytest`で68 tests passed。

## Phase 5: `/non-transit/route`（徒歩・タクシー独立エンドポイント）

ステータス: 実装完了

方針: 元のproject-instruction.mdはGoogle Maps `driving`を想定していたが、Phase 4で導入済みのORSを流用し、Google Maps API keyなしで完結させる。`/plan/route-plan`とは独立した単発の点対点エンドポイントとして実装し（呼び出し1回はO(1)、組み合わせ爆発の懸念なし）、実際に爆発しうるのは「区間ごとに全モードの組み合わせを試す」ような探索ロジックを将来route-planner側に足した場合のみと整理した上で着手。ユーザー指示により、ORS呼び出し自体には共有のレート制限を設けた。

実装済み:

- `app/services/rate_limiter.py`: `SlidingWindowRateLimiter`（インメモリ・単一プロセス向け、汎用）
- `app/services/ors_client.py`: `WalkingRoute`を汎用`RouteEstimate`にリネーム。`get_walking_route`/`get_driving_route`は共通の`_get_route(profile, ...)`を呼ぶ形に統一し、実際のORS HTTPリクエストの直前でレート制限（デフォルト30回/分、ORS無料枠40回/分より保守的な値）を通す。エンドポイント単位ではなく**ORS呼び出しそのもの**にレート制限をかけているため、`/non-transit/route`と`/plan/route-plan`内部の呼び出し両方が同じ予算を共有する。制限超過時は`OrsRateLimitError`（`OrsRequestError`のサブクラス）。
- `app/services/taxi_fare.py`: `estimate_fare_jpy(distance_m)`。**那須エリアの実際の料金は未確認のプレースホルダー**（初乗り500円/1km + 400円/km換算の一般的な日本のタクシー料金体系を模したもの）。実料金が分かり次第、定数を差し替える。
- `app/models/non_transit.py`: `NonTransitRouteResponse`（`mode`, `distance_m`, `duration_min`, `cost_jpy`。徒歩は`cost_jpy=null`）
- `app/api/non_transit.py`: `GET /non-transit/route`（`origin_lat/lng`, `dest_lat/lng`, `mode: walking|taxi`）。`OrsRateLimitError`→429、その他の`OrsRequestError`→503で返す。
- `app/main.py`にルーター・`non-transit`タグを追加

検証:

- `python -m pytest`で81 tests passed（新規: `test_rate_limiter.py`, `test_taxi_fare.py`, `test_api_non_transit.py`、`test_ors_client.py`に`get_driving_route`とレート制限超過のテストを追加）
- 実ORS_API_KEYで`/non-transit/route?mode=walking`と`mode=taxi`を実行し、那須湯本温泉↔那須ロープウェイ間で徒歩58分/4.8km（運賃null）、タクシー8分/6.1km（概算運賃2,550円）を確認
- レート制限が実際に発火し`OrsRateLimitError`を送出することを確認

残作業・未解決事項:

- タクシー運賃モデルは仮の定数。実際の那須エリアの料金相場が分かれば差し替えが必要
- レート制限は単一プロセス内のインメモリ実装のため、複数ワーカー構成にする場合は共有ストア（Redis等）への置き換えが必要

### `route-plan`のタクシーフォールバック（2026-08-23）

ユーザーからのフィードバック「独立エンドポイントとしては作っておいて呼び出しには制限を設ける」を受け、`route-plan`側にもタクシーを組み込んだ。

- 実データ（ユーザー提供の観光地16件）で検証中、最寄り停留所が1500m圏内にない場合（例: 那須どうぶつ王国）に「スポット間を丸ごと徒歩」フォールバックが発動し、徒歩349分（29km）という非現実的な行程になるバグを発見。
- 修正: `_build_leg`に直線距離チェックを追加。`MAX_DIRECT_WALKING_DISTANCE_M=3000`m を超える場合はタクシー（`get_driving_route` + `estimate_fare_jpy`）にフォールバックする。`ItineraryStep`に`mode="taxi"`と`cost_jpy`（taxi以外はnull）を追加。
- 検証: `tests/test_route_planner.py`に閾値未満（徒歩のまま）・閾値超過（タクシーに切り替わる）の両方のテストを追加。実データで那須どうぶつ王国ケースを再実行し、349分の徒歩がタクシー25分・概算11,252円に置き換わったことを確認。

### GTFSの「乗車不可能な親駅」バグ修正（2026-08-23）

ユーザー提供の実際の観光地データで動作確認中、「那須塩原駅⇔那須ロープウェイ間に那須線の直通便があるはず」という指摘を受けて調査した結果、ロジックのバグが見つかった（データの問題ではなかった）。

- 原因: `nearby_stops()`が`stops.txt`の全行を対象にしていたため、`stop_times.txt`に一度も登場しない「乗車不可能な停留所」（GTFSのparent station、`location_type=1`など）を最寄り停留所として返すことがあった。関東自動車データでは4230件中1401件（約33%）がこれに該当し、`那須塩原駅西口`もこのケースで、実際に便がある子ノード（`S090400154200100`）ではなく便が一切ない親ノード（`S0904001542`）を返していたため、`search_next_bus`が常に0件になっていた。
- 検証方法: `search_next_bus(station_stop, ropeway_stop, "00:00")`を日付・時刻フィルタなしで実行しても0件だったため、カレンダー起因ではなくデータ結合そのものの問題と判断。`routes.txt`を`那須`で検索し、`那須塩原駅～黒磯駅～那須ＲＷ`などの実在route_idを確認した上で、該当trip_idのstop_times実データを直接確認して原因を特定した。
- 修正: `GTFSEngine._prepare_tables()`で、`self.stops`を`stop_times.stop_id`に実在するIDのみへフィルタするようにした（`nearby_stops()`以外は`self.stops`を参照しないため副作用なし）。
- テスト影響: `tests/fixtures/minimal_gtfs`の`stop_station`（元々どの便にも登場しない想定のfixture）がこのフィルタで`stops`から除外されてしまうため、「乗車不可能だが直通便もない」という元のテスト意図を保つために`stop_station`⇔新設の`stop_extra`間のダミー便`trip_station_extra`を追加し、`stop_station`自体は乗車可能だが那須線とは接続していない状態にした。`test_from_feeds_prefixes_ids_to_avoid_collisions`の期待件数を5→6に修正。
- 検証: 修正後、`那須塩原駅～黒磯駅～那須ＲＷ`線の直通便が正しく検出され、実データでの`route-plan`（駅→ロープウェイ）が成立することを確認。

残作業: 「駅→湯本温泉→ロープウェイ」の2スポット周遊は引き続き不成立。原因調査中（データの実態か、別のロジック起因かを切り分け予定）。

### 最寄り停留所が「近いが未接続」を返すロジックバグ修正（2026-08-23）

上記の残作業を調査した結果、**データではなくロジックの限界**と判明した。

- 原因: 那須湯本温泉の座標には、kanto_auto（関東自動車）自身の停留所が183.7m、nasutown（那須町）の同名停留所が176.8mとほぼ同距離で存在する。`_nearest_stop()`が「全事業者中で最寄りの1件のみ」を候補にしていたため、わずか7m近いnasutown側（那須線ネットワークと未接続）を採用してしまい、実際に接続しているkanto_auto側を無視していた。
- 切り分け方法: `search_next_bus`で該当区間の直通便を無条件検索し0件だったため、まずデータそのものを疑ったが、`nearby_stops()`が返す候補停留所とその事業者の内訳を個別に確認したところ、より近い「未接続」の停留所と、わずかに遠い「接続済み」の停留所が両方1500m圏内に存在することが分かった。→ ロジック（候補を1件しか見ない設計）の限界と特定。
- 対応方針（ユーザー承認済み）: (1)まずこの挙動を再現する回帰テストを追加し現行コードで失敗することを確認 → (2)ロジックを修正 → (3)テストが通ることを確認、の順で対応。
- 回帰テスト: `tests/fixtures/multi_operator_near/` ・ `tests/fixtures/multi_operator_far/` という最小fixtureを新設。destination地点から1.1m（未接続の事業者）と11.1m（接続済みの事業者）に停留所がある状況を再現し、`test_build_route_plan_finds_bus_via_a_farther_but_connected_stop`で検証。修正前は`status="error"`（失敗を確認済み）。
- 修正: `route_planner.py`に`MAX_STOP_CANDIDATES=3`を導入。`_nearest_stop_candidates()`が上位3件を返すようにし、`_build_leg()`は origin候補×destination候補の組み合わせを近い順に試して、直通便が見つかった最初の組み合わせを採用する（該当なしなら不成立）。origin側の徒歩ステップは候補ごとに使い回すためキャッシュし、ORS呼び出し回数を抑えている。
- 副次的なリファクタ: `_walking_step`/`_taxi_step`/`_stay_step`/`_wait_step`を、共有の`_Cursor`を直接ミューテートする方式から `(step, end_minutes)` を返す方式に変更（複数候補を投機的に評価する際、確定していない候補の計算で状態を汚さないようにするため）。
- 検証: `python -m pytest`で84 tests passed。実データで「駅→湯本温泉→ロープウェイ」が成立することを確認（那須塩原駅～黒磯駅～那須ＲＷ線を経由）。

## 現時点の設計メモ

- バス運賃はGTFSに含まれない可能性があるため、初期APIでは nullable とする。
- 本番に近づける段階では、経路探索リクエストに `departure_date` を必須化する。

## デバッグ・確認用のHuman Interface UI（一画面版）

ステータス: 実装完了（ルーティング結果の別タブ表示まで含む）

背景: バックエンドAPI（FastAPI）のみで、動作確認はcurl/pythonスクリプト/実データ検証に頼っていた。GTFSバグ調査中にユーザーから「那須塩原⇔那須高原エリアの路線カバレッジを地図で可視化して確認したい」という要望が出たことがきっかけで、ブラウザベースの確認用UIを作ることになった。

要望されている画面構成（1画面）:

- 現在ロードされている路線図（停留所・shapes）の可視化
- 停留所名の検索
- スポットの可視化
- スポットの追加
- 日付・時刻の選択（値の保持のみ）

ルーティング結果（`route-plan`）の時系列＋地図表示は別タブ/別窓の次ステップとして今回は対象外。

### 実装結果（2026-08-23）

バックエンド:

- `GTFSEngine`に`shapes.txt`対応を追加（`OPTIONAL_GTFS_FILES`・`ID_COLUMNS`に`shapes`を追加、`trips`の`ID_COLUMNS`に`shape_id`を追加）。新規`route_shapes()`メソッドが`shape_id`ごとの点列（`shape_pt_sequence`順）と、紐づく1つの`route_id`/`line_name`を返す。
- `SpotRepository`に`list_all()`・`add(name, lat, lng)`を追加。`add()`は`spot_id`を`spot_<uuid8桁>`で自動生成し、一時ファイル書き出し→`os.replace`でJSONファイルへアトミックに永続化（`threading.Lock`で書き込みを直列化）。ユーザー承認どおりSQLiteは使わず、単一デバッグ用途向けの軽量実装とした。プロセス内シングルトン（`app/api/deps.py`の`lru_cache`）の内部状態を直接更新するため、追加後は`route-plan`からも即座に参照可能。
- 新規API: `GET /transit/network`（`engine.stops`全件＋`route_shapes()`）、`GET /spots`・`POST /spots`（`app/api/spots.py`、新規タグ`spots`）。
- `app/main.py`に`fastapi.staticfiles.StaticFiles`を`/ui`にマウント（新規Python依存なし）。

フロントエンド（`app/static/`、ビルド不要）:

- `index.html`/`app.js`/`style.css`。地図はLeaflet（CDN経由）＋OpenStreetMapタイル。
- 起動時に`/transit/network`・`GET /spots`を取得し、停留所（`L.circleMarker`、canvasレンダラー）・路線shapes（`L.polyline`、route_idハッシュで色分け）・スポット（別色マーカー）を描画。
- 停留所名検索はクライアント側の部分一致フィルタ（新規検索APIは追加せず、取得済みリストをそのまま使用）。
- スポット追加は「地図クリックで追加」モードを切り替え、クリック地点の緯度経度でフォーム表示→`POST /spots`→即座に地図へ反映。
- 日付・時刻inputは値を保持するのみ（次ステップのルーティングUIに引き継ぐ想定）。

テスト: `tests/fixtures/minimal_gtfs/`に`shapes.txt`と`trips.txt`への`shape_id`列を追加。`tests/test_gtfs_engine.py`（`route_shapes()`の単体・フィードなし・`from_feeds`でのプレフィックス確認）、`tests/test_spot_repository.py`（`list_all`/`add`/永続化/既存データ非破壊）、`tests/test_api_transit.py`（`/transit/network`）、新規`tests/test_api_spots.py`（`GET`/`POST /spots`）を追加。`python -m pytest`で96 tests passed。

検証: `claude-in-chrome`スキルで実際にブラウザを操作し、実データ（停留所3164件・路線形状299件）で地図描画・停留所検索（クリックでパン/ズーム＋ポップアップ）・スポット追加（クリック→フォーム→追加→地図即時反映）が動作することを確認。手動検証中に誤って`GTFS_DIR`のみ設定し`SPOTS_FILE`を未指定のままサーバーを起動し、本番の`data/spots/nasu_spots.json`にテスト用スポットを2回書き込んでしまったため、その都度手動で3件の元データに復元した（実装上の問題ではなく、検証時の環境変数指定漏れ。今後同様の手動検証をする際は`SPOTS_FILE`を必ず隔離すること）。

### ルーティング結果ビュー（地図＋時系列、別タブ・左右分割、2026-08-23）

ユーザー承認どおり「左右分割表示」「ルート作成モードONでスポットを順にクリック」の仕様で実装。

バックエンド:

- `ItineraryStep`に`from_lat`/`from_lng`/`to_lat`/`to_lng`（必須）を追加。これらの座標は`route_planner.py`の各ステップ生成箇所（`_walking_step`/`_taxi_step`/`_stay_step`/`_wait_step`/バスステップ生成部）が元々内部で保持していた値をそのままレスポンスに含めるようにしただけで、新規の座標解決ロジックは不要だった。滞在・待機ステップは`from`=`to`=スポット自身の座標。

フロントエンド:

- メイン画面（`index.html`/`app.js`）に「ルート作成モード」を追加。ONにすると既存スポットマーカーのクリックが選択リストへの追加として扱われる（1件目=起点、以降=訪問地、`×`で個別削除）。「スポット追加モード」とは相互排他（片方ONでもう片方は自動OFF）。日付・時刻inputと合わせて`POST /plan/route-plan`を実行し、レスポンスを`localStorage`（キー`latest_route_plan`）に保存してから`results.html`を新規タブで開く。
- 新規`results.html`/`results.js`/`results.css`: `localStorage`から結果を読み込み、左右分割で左に地図（Leaflet、各ステップを`from`→`to`のpolyline/circleMarkerで描画、モードごとに色分け、`step_order`の番号アイコン付き）、右に時系列Plot（自前実装の横棒タイムライン、開始〜終了時刻に応じて幅・位置を計算、モード別配色の凡例つき）を表示。タイムラインの行をクリックすると対応する地図要素にズーム＋ポップアップ表示する連動あり。`status="error"`時はタイトルを「ルート不成立」にして`ai_hint`のみ表示。

テスト: `tests/test_route_planner.py`の`test_build_route_plan_success`に座標フィールドのアサーションを追加。`python -m pytest`で96 tests passed（既存テストへの副作用なし、`ItineraryStep`の必須フィールド追加は全生成箇所で対応済み）。

検証: `claude-in-chrome`で実際にルート作成モードをON→那須ロープウェイ（起点）→那須湯本温泉の順にスポットをクリック→「この行程を検索」→別タブで結果ページが開き、地図（バスルートのpolyline、番号付きマーカー）と時系列Plot（7ステップの横棒、時刻・詳細）が正しく表示されることを確認。タイムライン行クリックで地図が該当ステップにズーム・ポップアップすることも確認。この検証は`SPOTS_FILE`を`/tmp`の複製に隔離した状態で実施し、本番`data/spots/nasu_spots.json`への影響がないことも確認済み。

残作業: スポット選択時の`dwell_minutes`/`required_arrival_time`のUI指定（現状はデフォルト値のみでリクエストする）、訪問順の並べ替えUI。→ 下記セクションで対応済み。

### 各拠点の制約・設定パネル（マスト・バス乗り継ぎの余裕、2026-08-23）

ユーザー承認どおり「マストでないスポットはスキップして残りで再検索」「バス乗り継ぎの余裕は行程全体で1つ（グローバル）」の仕様で実装。

バックエンド:

- `TargetSpot`に`required: bool = True`を追加。`RoutePlanRequest`に`transfer_buffer_minutes: int = 0`（0〜60分）を追加。
- `build_route_plan()`を再構成: `_compute_plan()`が不成立を返した場合、`remaining`リストの中から`required=False`の最初の1件をドロップして再計算するループに変更。成立したら、ドロップしたスポット名を`ai_hint`に追記する。全て`required=False`でドロップし尽くしてもリストが空になった場合は専用のエラーメッセージを返す。
- `transfer_buffer_minutes`は`_build_leg()`のバス検索時刻（`search_next_bus`の`departure_time`）に単純に加算するだけで実装（`board_minutes + transfer_buffer_minutes`）。

副次的なバグ修正: 上記の実装中に、バスの`ItineraryStep.start_time`が実際の発車時刻ではなく「停留所への到着時刻」になっていた既存バグを発見（`bus['duration_min']`は正しいが表示される所要時間と時刻範囲が食い違っていた）。バス発車時刻（`bus['departure_time']`）を正しく`start_time`に使うよう修正し、到着から発車までの間隔がある場合は新しい`mode="wait"`のバス待ちステップ（タイトル「〜でバス待ち」、集合時刻待機とは文言・判定条件で区別）を挿入するようにした。`transfer_buffer_minutes`を使うとこの待ち時間が意図的に発生しやすくなるため、正確な時刻表示のために必須の修正だった。

フロントエンド:

- メイン画面の「日付・時刻」セクション下に「バス乗り継ぎの余裕（分）」の数値inputを追加（グローバル、0〜60）。
- ルート選択リストの各訪問地（起点を除く）に「⚙」ボタンを追加。クリックで滞在時間（分）・集合時刻・マスト（チェックボックス、デフォルトON）を編集するミニフォームが展開される。

テスト: `tests/test_route_planner.py`に`required=False`のドロップ・全ドロップ時のエラー・`transfer_buffer_minutes`の効果を検証する3テストを追加。既存の待ち時間関連テストは新しい「バス待ち」waitステップの挿入に合わせてmodesリストと該当ステップの特定方法（`"集合時刻" in step.details`で予約待機と区別）を更新。`python -m pytest`で99 tests passed。

検証: `claude-in-chrome`で実際に設定パネルを開き、滞在時間を90分に変更・マストのチェックを外す・グローバルバッファを5分に設定してから検索を実行。結果ページで滞在時間90分・新しい「バス待ち」ステップ・凡例への「待機」追加が正しく反映されていることを確認（今回は両スポットとも成立したため、マスト解除によるドロップ自体の目視確認はできていないが、バックエンド側は単体テストで検証済み）。この検証も`SPOTS_FILE`を隔離した状態で実施。

## スポットマスタの拡充（2026-08-29）

背景: ユーザーより「徒歩・タクシー検索はあくまで補助（バス接続範囲での観光探索が本来のユースケース）」「スポットはもっとたくさん網羅しておきたい」という優先順位の共有があり、まずスポットデータの拡充から着手した。

- 既存3件（`station_nasushiobara`, `spot_nasu_yumoto_onsen`, `spot_nasu_ropeway`）は既存テスト・READMEの例が参照しているためID・座標とも変更せずそのまま維持。
- ユーザーが以前のメッセージで提示した那須町の観光地13件（南ヶ丘牧場・那須サファリパーク・那須どうぶつ王国・那須ハイランドパークなど、既存3件と重複するロープウェイ・湯本温泉は除く）をそのまま追加。
- 那須地域オープンデータカタログ（`opendata-nasu.opendatastack.jp`、那須塩原市・大田原市・那須町・那珂川町共同運営）から、那須塩原市 商工観光課公開の「観光施設」「観光スポットセレクション」データセット（CSV/GeoJSON、緯度経度付き）を取得し、13件を追加。取得元:
  - 観光施設: `https://nasu-ckan.s3.amazonaws.com/ckan/resources/6b5efa78-714b-439e-aa94-3149094b886a/010901.csv`
  - 観光スポットセレクション: `https://nasu-ckan.s3.amazonaws.com/ckan/resources/e0bdb244-72f9-4c20-b8e3-509eebc237ef/050202.csv`
- 那須町自体の観光施設に特化したオープンデータセットは見つからなかった（「【那須町】生活関連施設」データセットは消費生活センター1件のみで観光地は含まれず）。那須町側の観光地はユーザー提供分でカバーしている。
- 合計 3件 → 29件（那須町側16件、那須塩原市側13件）。スキーマは変更せず`{spot_id, name, lat, lng}`のまま（説明文・カテゴリは今回追加しない）。

検証: `python -m pytest`で99 tests passed（既存3件のIDと座標を変更していないため影響なし）。`GET /spots`で29件が正しく返ることを確認。

残作業: 那須町自体の観光施設のオープンデータが見つからなかったため、今回追加したユーザー提供の16件以外に那須町側の抜けがある可能性がある。必要なら那須町へ個別に確認するか、UIの「スポット追加」で随時補う。

## 実データでのドッグフーディングとバグ修正（2026-08-29）

ユーザーより「登録したデータとこの機能を使って実際にプランを立ててみて使用感やバグを検証したい。駅からエピナールをゴールにいくつかの観光地を回るパターンを探索してみて」という依頼を受け、実GTFS（3フィード統合）と実ORSを使って`build_route_plan()`を直接呼び出し検証した。

- スポットマスタに`spot_hotel_epinard`（ホテルエピナール那須）が未登録だったため追加。座標は実GTFS（`data/gtfs/kanto_auto/stops.txt`、`stop_id=S0904001565`）から取得（`grep "エピナール" data/gtfs/*/stops.txt`）。
- `route-plan`は現状ラウンドトリップ専用（`waypoints = [base, *targets, base]`）。「駅からエピナールをゴールに」という依頼は片道（非対称な出発地・目的地）を示唆しているが、現状のAPIでは表現できないため、往復（駅→...→エピナール→...→駅）として検証した。片道対応は別途の設計検討が必要。
- 那須塩原駅発→那須湯本温泉→殺生石→那須ロープウェイ→那須ステンドグラス美術館→ホテルエピナール那須（往復、2026-08-31 08:30発）を試したところ`status: "error"`（バス不成立）になった。

**バグ**: `_nearest_stop_candidates()`が`MAX_STOP_CANDIDATES=3`件に絞ってからバス検索するため、実際に接続しているバス停が上位3件に入らないと`_build_leg()`が候補を試す前に諦めてしまう。実データでは同一の物理停留所が上り/下り等で複数件登録されており（例: 「上新屋」「新屋」がそれぞれ2件ずつ）、これが上位枠を消費してしまう。今回のルートでは「那須ロープウェイ→那須ステンドグラス美術館」区間で接続停留所「上新屋」が距離順4〜5位、「ホテルエピナール那須→那須塩原駅」区間で接続停留所「新屋」が6〜7位に位置しており、上位3件では届かなかった。
- 同種のバグは`test_build_route_plan_finds_bus_via_a_farther_but_connected_stop`で以前修正済み（単一最近接停留所のみ試す実装から複数候補を試す実装への変更）だったが、実データの停留所密度に対して`MAX_STOP_CANDIDATES=3`という上限値そのものが小さすぎることが今回判明した。
- 対応: `MAX_STOP_CANDIDATES`を3→8に変更。`_build_leg()`は未採用の候補1件につきORS徒歩ルート呼び出しを最大1回消費する構造のため、値を上げるほどORSの共有レート制限（`ORS_MAX_REQUESTS_PER_MINUTE=30`）への負荷が増える（最悪ケースで1レグあたり最大9回のORS呼び出し）というトレードオフがある。実際の往復5スポットルート（6レグ）でも制限内に収まることを確認済みだが、スポットがさらに密集するエリアが増えた場合は要再検討（候補の重複停留所名での重複排除、バス接続の有無を先にGTFSのみで判定してからORSを呼ぶ、などが次の改善候補）。

テスト: `tests/fixtures/many_decoys_near`・`many_decoys_real`（decoyとなる無関係な停留所4件が実際に接続する停留所より僅かに近い、という実データの縮図）を新規作成し、`test_build_route_plan_finds_bus_via_stop_ranked_beyond_top_three`を追加。`MAX_STOP_CANDIDATES=3`（旧デフォルト）では失敗し、`=8`（新デフォルト）で成立することを確認した上で修正を適用。`python -m pytest`で100 tests passed。

検証: 実GTFS・実ORSで上記の往復5スポットルートが成立することを確認（那須塩原駅08:35発→...→ホテルエピナール那須15:00-16:00滞在→...→那須塩原駅17:26着、全26ステップ）。ブラウザUIでの動作確認（`claude-in-chrome`）は未実施。

残作業: `MAX_STOP_CANDIDATES`増加に伴うORS呼び出し増の恒久対策（重複停留所名の除去、バス接続確認とORS呼び出しの分離）。

検証（追記）: `claude-in-chrome`で実際のUIを操作し（ルート作成モードON→マーカーをJS経由でクリック→日付時刻設定→検索）、上記5スポット往復ルートが結果ページ（地図＋時系列タイムライン、クリックでのズーム連動）に正しく表示されることを確認した。ブラウザUI経由での検証も完了。

## route-planの片道対応（2026-08-30）

前セクションで「駅からエピナールをゴールに」という依頼が片道（非対称start/end）を示唆していたが現状は往復専用、という残課題を報告したところ、ユーザーから設計案の提案があった: 「目的地を重複設定できるようにすればワンウェイかどうかを指定できるのでは？出発とゴールを強い制約にするとか」。

採用した設計（新規フィールド追加なし）:

- `_compute_plan()`の`waypoints`を`[base, *targets, base]`（常に往復）から`[base, *targets]`（`target_spot_ids`の最後の要素で行程が終わる）に変更。
- 往復にしたい場合は、呼び出し側が`target_spot_ids`の最後にもう一度`base_location_id`と同じspot_idを指定する。
- 確認事項として、「最後の訪問地＝実際の目的地になりうる」ため、`required_arrival_time`（例: ホテルのチェックイン期限）を最後の訪問地でも有効にすべきか質問したところ、「最後の地点でも有効にする」の回答を得た。一方、滞在（stay）ステップは最後の訪問地では引き続き省略する（行程がそこで終わるため、次に移動するまでの滞在時間という概念自体が発生しない）。
- タイトル末尾の文言を「周遊プラン」（往復を含意）から「行程プラン」（中立的）に変更。

バックエンド (`app/services/route_planner.py`, `app/models/route_plan.py`):
- `_compute_plan()`: `waypoints`から末尾の自動`base`追加を削除。`required_arrival_time`のチェックを「最終レグ以外のみ」から「毎レグ（最終レグ含む）」に変更。滞在ステップの生成は引き続き最終レグのみスキップ。
- `RoutePlanRequest.base_location_id`/`target_spot_ids`のフィールド説明を新しい意味論（片道がデフォルト、往復は`base_location_id`の重複指定）に合わせて更新。

フロントエンド (`app/static/index.html`/`app.js`/`style.css`):
- ルート作成パネルに「往復にする（起点に戻る）」チェックボックスを追加（デフォルトOFF＝片道）。
- 選択リストの最後の訪問地は、チェックボックスOFF時は「終点」バッジ、ON時は通常の番号バッジを表示（チェック状態の変更で再描画）。
- 検索実行時、チェックボックスONなら送信直前に`target_spot_ids`の末尾へ`{spot_id: base_location_id}`を追加する（`routeSelection`自体は変更しない＝マーカーの重複クリックガードには触れていない）。

テスト: 往復を前提にしていた既存テスト（`test_build_route_plan_success`ほか）は、`target_spot_ids`の末尾に明示的に`base_location_id`を重複指定する形に更新（アサーション自体は変更不要だった＝往復の挙動は完全互換）。新規に片道の基本ケース（`test_build_route_plan_one_way_ends_at_last_target`/API版）と、往復の最終レグで`required_arrival_time`が効くこと・効かせて不成立にできることを検証する2テスト（`test_build_route_plan_honors_required_arrival_time_on_round_trip_return`/`..._errors_when_round_trip_return_deadline_missed`）を追加。`python -m pytest`で104 tests passed。

検証: 実データ・実UIで「那須塩原駅→那須湯本温泉→ホテルエピナール那須」を片道（チェックボックスOFF）で検索し、往路のみで終了（復路なし、最終地点にstayステップなし）することを確認。同じ選択でチェックボックスをONにすると選択リストの表示が「終点」から番号バッジに切り替わることを確認。往復（チェックボックスON）でのリクエスト送信自体も確認できたが、この session で実ORS APIを繰り返し叩いたためレート制限（`ORS_MAX_REQUESTS_PER_MINUTE`ローカル制限とは別に、ORS側実クォータと思われる）により最終確認は`徒歩・タクシー区間の経路計算に失敗`で不成立となった。ロジック自体はpytestの往復系テストで検証済みのため問題はないと判断。

## デバッグUIの実用性改善 + 実経路表示（2026-09-01）

ユーザーが実際にデバッグUIを使ってみたフィードバックとして2点の要望があった:

1. 「候補地追加がやりにくいので専用のUIが欲しい。経由地を名前だけいれて列挙していき、終点と始点などの装飾をつけるようにしたい」
2. 「経路の表示にて地図で直線で結ぶのではなく通る経路で見たい。」

### 1. 名前検索によるルート作成UI

これまで経由地の追加は「ルート作成モード」ONにして地図上の小さいスポットマーカーを正確にクリックする方式のみで、密集エリアでのクリック精度が課題だった（過去のセッションでも同様の課題が記録されている）。

- `app/static/index.html`/`app.js`: 「ルート作成」パネルに、スポット名で絞り込み検索してクリックで追加するテキスト入力（`#route-spot-search`、既存の「停留所検索」と同じUXパターン）を新設。追加ロジックは`addSpotToRoute(spot)`として既存のマーカークリックハンドラと共通化。
- 地図クリックでの追加方式（旧「ルート作成モード」）はボタンを「地図クリックで経由地を追加」に改名して残し、既存の`スポット追加`パネルの「地図クリックで追加」ボタンとのラベル衝突を避けた。
- `spots`（読み込み済みスポット一覧）をモジュールスコープの`let`変数として保持するよう変更（従来は`loadSpots()`内のローカル変数だった）。
- 起点/`番号`/終点のバッジ表示は既存の`renderRouteSelection()`のロジックがそのまま適用されるため変更不要だった。

検証中に判明した副次的な問題: ブラウザがJS/HTML変更後も古い`app.js`をキャッシュから配信し続け、新しいUIの見た目（HTML）は反映されるがロジック（JS）は反映されないという不整合が発生した（`fetch(..., {cache:'no-store'})`では新しい内容が返るのに、`<script src>`タグ経由の読み込みはキャッシュを使い続けていた）。ハードリロード（Cmd+Shift+R）で解消。実装上の問題ではなく検証環境（同一タブを何度も使い回した）起因。

### 2. 実際の経路に沿った地図表示

これまで`results.html`は各ステップの`from_lat/lng`→`to_lat/lng`を直線で結んでいた。実際の道路・バス路線に沿った表示に変更。

バックエンド:

- `app/services/ors_client.py`: `RouteEstimate`に`path: list[list[float]]`を追加。ORSのGeoJSONレスポンス（`features[0].geometry.coordinates`、`[lng,lat]`の配列）をパースして`[lat,lng]`に変換して格納。geometryが取得できない場合は起点・終点のみの直線にフォールバック。
- `app/services/gtfs_engine.py`: `DirectBusOption`に`shape_id`を追加（`search_next_bus()`の返り値に含める）。新規メソッド`bus_leg_path(trip_id, origin_stop_id, dest_stop_id)`: トリップのshape全体（`shapes.txt`由来、`route_shapes()`と共通の`_shape_points()`ヘルパーに切り出し）から、乗車停留所・降車停留所それぞれに最も近い点のインデックスを求め、その区間だけをスライスして返す（乗車区間のみを表示するため）。shapeが無い・停留所が見つからない・スライス方向が逆転する場合は`None`を返し、呼び出し側は直線にフォールバックする。
- `app/services/route_planner.py`: `_walking_step`/`_taxi_step`は`route.path`をそのまま`ItineraryStep.path`に設定。バスステップ生成部は`engine.bus_leg_path(...)`の結果を設定。
- `app/models/route_plan.py`: `ItineraryStep`に`path: list[list[float]]`（デフォルト空リスト）を追加。AIエージェント向けのフィールド説明も付記。

フロントエンド:

- `app/static/results.js`: `L.polyline([from, to], ...)`だった箇所を、`step.path`が2点以上あればそれを使い、無ければ直線にフォールバックするよう変更。地図の`fitBounds`も直線の2点ではなく実際のpath全点を使うよう変更（実経路が直線区間の外側に膨らむケースでも全体が収まるように）。

テスト: `tests/test_gtfs_engine.py`に`bus_leg_path()`の3テスト（区間スライス成功・shape無しトリップ・shapeファイル自体が無いフィード）を追加。既存の`search_next_bus`の完全一致アサーションに`shape_id`を追記。`tests/test_ors_client.py`にgeometryありレスポンスのパーステスト、geometry無し時の直線フォールバックのアサーションを追加。`tests/test_route_planner.py`の共有フェイク（`fake_walking_route`）に`path=[[lat1,lng1],[lat2,lng2]]`を設定するよう変更（既存テストへの副作用なし、`path`はデフォルト空リストのため未設定でも壊れない設計）。フラグシップの成功テストにバスステップ・徒歩ステップの`path`アサーションを追加。`python -m pytest`で108 tests passed。

検証: `claude-in-chrome`で実データ・実UI・実ORSを使い、「那須塩原駅→那須湯本温泉→ホテルエピナール那須」の片道ルートを名前検索のみで作成（マップクリック不要）し検索を実行。結果ページで各ステップの`path`が実際に複数点（徒歩10〜58点、バス212〜711点）持つことを確認。徒歩区間をズームして表示したところ、実際の道路の曲がりに沿ったポリラインが描画されていること（直線でないこと）を目視確認した。

## リリースに向けたパッケージ分割と公開準備（2026-09-21）

ユーザーより「そろそろ完成とリリースに向けて動きたい。GTFSデータを集めてそれを横断的に検索する仕組み、それを組み合わせてルーティングを行う仕組み、可視化UI のような粒度ごとに完成した小さなパーツとして公開可能なレベルに修正して公開できるだろうか」という要望。

調査の結果、層構造はすでに綺麗に分かれていた（`gtfs_engine.py` はapp内部への依存がゼロ、pandas と標準ライブラリのみ）。ただし3つの部品は公開適性が等しくない — GTFS層だけが那須非依存で汎用的価値を持ち、ルーティング層は実運賃未検証のtaxi_fare・那須固有のスポットマスタ・乗り継ぎ非対応という制約を抱える。そのため**3等分ではなく非対称分割**を採用した（ユーザー承認済み）。

決定事項: 非対称分割 / MITライセンス / 公開範囲はリポジトリ公開レベルまで（PyPI実アップロードは対象外） / taxi_fareは差し替え可能にする。

### 公開前ブロッカーの解消

- **LICENSEファイルが存在しなかった** → MITをリポジトリrootとライブラリの両方に追加。
- **`gtfs-kit` が依存関係に宣言されていたがコード内で一度も使われていなかった** → 削除。
- 秘密情報の混入は事前に確認しゼロだった（`.env` は未追跡、関東自動車APIキーは全コミット履歴を `git log -S` / `git grep` で走査して不検出）。

### 分割の内容

モノレポ構成を採用（アプリは root の `app/` に残し、ライブラリを `packages/gtfs-multifeed/` に新設）。アプリ側の全パス・`uvicorn app.main:app`・静的ファイルmount・`REPO_ROOT` 計算が変わらず churn を最小化できるため。ライブラリは独自の `pyproject.toml` を持ち単独配布可能なので、将来 `git filter-repo` で別リポジトリに切り出せる。

ライブラリ `gtfs-multifeed`（依存は pandas と httpx のみ）:
- `engine.py` ← `app/services/gtfs_engine.py` をロジック無改造で移動。
- `feeds.py` ← `scripts/download_gtfs.py` の汎用部分。モジュール定数だった `MANIFEST_PATH` とグローバル `FEEDS` を引数で受け取る形に変更し（`load_manifest`/`save_manifest`/`print_status`）、CLI組み立てを `run_cli(feeds, manifest_path, ...)` として公開。ここが唯一の実質的なリファクタ。
- `cli.py` ← `app/cli.py`。`prog` を `gtfs-multifeed` に、那須前提の説明文を一般化。
- `discover_gtfs_feeds()` を `app/core/config.py` から移動（GTFSディレクトリ探索は汎用処理のため）。引数 `data_root` を必須化。

アプリ側:
- 那須3フィードの定義を `app/gtfs_feeds.py` に新設。`scripts/download_gtfs.py` はそれをライブラリCLIに渡すだけの薄いラッパ（289行→39行）に縮小。
- `from app.services.gtfs_engine import ...` を `from gtfs_multifeed import ...` へ全面置換。

### taxi_fare の差し替え対応

`build_route_plan()` に `fare_estimator: FareEstimator = estimate_fare_jpy` を追加し、`_compute_plan` → `_build_leg` → `_direct_step` → `_taxi_step` へ引き回した（既存の `transfer_buffer_minutes` とまったく同じ経路のため既存スタイルと一致）。`taxi_fare.py` に `make_fare_estimator()` を追加して同じ式を別定数で使えるようにし、docstringで「未検証の参考値」であることを明示した。

### テスト

`test_gtfs_engine.py`→`test_engine.py`、`test_download_gtfs.py`→`test_feeds.py`、`test_cli.py` をライブラリ側へ移動。`test_config.py` は `discover_gtfs_feeds` のテストのみだったため内容を `test_engine.py` に統合して削除。fixture `minimal_gtfs`/`minimal_gtfs_other` はライブラリ側へ移し、アプリ側テスト7ファイルも `minimal_gtfs` を使うためアプリ側にコピーを保持（パッケージ間の暗黙結合を作らないことを優先）。`fare_estimator` の差し替えが実際に効くことを検証するテストを追加し、**108→109テスト**。

### 検証

1. root から `python -m pytest` で109テスト通過。
2. **ライブラリの独立性を実証**: クリーンな venv に `gtfs-multifeed` のみをインストール（fastapi/pydantic/uvicorn が入らないことを確認）し、`GTFSEngine`・`search_next_bus`・`nearby_stops`・`bus_leg_path`・`discover_gtfs_feeds`・consoleスクリプトが fixture に対して動作することを確認。この環境では pandas 3.0.6（開発venvは2.x）が入ったが、ライブラリ単体テスト41件も全て通過した。
3. `python scripts/download_gtfs.py --status` が従来通り3フィードの鮮度を表示。
4. 実GTFS（停留所3164件・路線形状299件）・実スポット30件でAPIが起動し、`POST /plan/route-plan` が実経路付き（バス711点・徒歩58点等）で成立。
5. `claude-in-chrome` でデバッグUIを操作し、名前検索でのルート作成 → 起点/番号/終点バッジ → 検索 → 結果画面（実経路の地図＋タイムライン）まで回帰がないことを確認。

### 残作業（今回対象外）

- PyPIへの実アップロード（パッケージ名 `gtfs-multifeed` の空き確認、アカウント/トークン準備、build/upload）
- 乗り継ぎ（transfer）対応
- CI（GitHub Actions）の構築
- `/plan/graph-data` への徒歩エッジ統合
- タクシー実運賃の調査

## バスの乗り継ぎ対応（2026-10-12）

リリース整理後、ユーザーより「バスの乗り継ぎや訪問順の提案に対応していないのはまだ気になる」との指摘。2つは実装の重さが大きく違うため、実データで見積もってから着手した。

見積もりの結果:
- stop_times は3フィード計 **15.5万行** で全てメモリに載る規模。性能は制約にならない。
- どのフィードにも **`transfers.txt` が無い**ため、乗り継ぎ地点は停留所の距離から推定する必要がある。
- `ItineraryStep` は1レグが可変長のステップ列を返す設計なので、**乗り継ぎを入れても出力モデルを変えずに済む**（UI・結果画面に変更不要）。

ユーザーから「徒歩や距離で接続可能性を定義してConnectableな駅を一度軽く作ってから、それベースで乗り継ぎロジックを組む」という方針の提案があり、これを採用。この接続グラフは乗り換え1回でもRAPTOR等の無制限探索でも同じ土台になるため、後から回数を増やす方向に素直に伸ばせる。

**順序の判断**: 訪問順の最適化より乗り継ぎを先にした。乗り継ぎが無いと到達できない地点の組が多く、訪問順を入れ替えても選べる順序自体がほとんど無いため。

### 実装

ライブラリ (`gtfs_multifeed.engine`):

- `transfer_edges(max_distance_m=300)`: 全停留所ペアの距離を numpy のブロック処理で求め、徒歩圏内のペアを返す。粗いフィルタに等距円筒近似を使い、残ったペアのみ正確なヒュベニ距離で再計算する2段構え。半径ごとにキャッシュ。`from_feeds()` が `__new__` でインスタンスを作るため、キャッシュは `__init__` に頼らず遅延初期化している。
- 乗り継ぎ時間は直線距離×迂回係数1.3÷分速80m、最低2分（バスは待ってくれない）。30m以内は同一地点とみなし最低値を適用。5,838ペアをORSで引くとレート制限で3時間以上かかるため、外部API呼び出しは行わない判断。
- `search_journeys(..., max_transfers=1)`: 直通検索の結果と、1回乗り換えの行程をマージして到着時刻順に返す。乗り換え候補は「同一停留所に留まる」と「近接停留所へ歩く」の2系統。
- 中間停留所では**最早到着のみを追跡**している。これは近似ではなく最適で、早く着けば遅く着いた場合に乗れる便には必ず乗れるため（弱支配）。
- `stop_info(stop_id)` を追加（中間停留所の名前・座標解決用）。

アプリ (`route_planner`):

- `_build_leg` が `search_next_bus` ではなく `search_journeys` を呼ぶよう変更。複数の乗車を steps に展開する `_ride_steps()` を切り出した。
- 乗り換えが別停留所への徒歩を伴う場合は「乗り換え徒歩」ステップを挿入。同一停留所での乗り換えは、次のバスを待つ wait ステップが既に表現しているため徒歩ステップを出さない。
- `RoutePlanRequest.max_transfers`（既定1、0〜1）を追加。

### 実データでの効果

14スポットの全順序ペア182件で測定（2026-10-15 09:00発）:

| | |
|---|---|
| 接続グラフ構築 | 0.09秒 / 11,676辺 |
| 直通で到達可能 | 33件（18%） |
| 乗換1回で新たに到達可能 | **116件（64%）** |
| 依然として不可 | 33件（18%） |
| **到達率** | **18% → 82%** |

50m以内のペアが1,563組で、停留所数3,164の約半分と一致した。これは**上り/下りで二重登録された同一停留所**であり、以前 `MAX_STOP_CANDIDATES` を3→8に引き上げる原因になった重複が、接続グラフでは「徒歩0分の乗り継ぎ辺」として原理的に表現されるようになった。

### 検証

- `python -m pytest` で **123件通過**（既存121件に影響なし）。乗り継ぎ用に `transfer_net` フィクスチャを新設し、ライブラリ側12件・アプリ側2件を追加。
- フィクスチャ作成時、当初の配置ではスポットから1.1km圏内に乗り継ぎ地点の停留所があり、`max_transfers=0` でも「途中までバス＋徒歩」で成立してしまっていた。これは実装ではなくテスト前提の誤りだったため、地点間距離を最寄り停留所半径の外に広げて、乗り継ぎ有無の差が明確に出るようにした。
- 実データ・実ORSで `那須塩原駅 → 那須りんどう湖ファミリー牧場` を検証。`max_transfers=0` では不成立、既定（1回）では成立し、**関東自動車から那須高原観光周遊バスへ事業者をまたぐ乗り換え**（友愛の森で徒歩113m・2分）を含む行程が得られた。複数フィード統合と接続グラフが噛み合った結果。
- デバッグUIでも「乗り換え徒歩」ステップとバス2本が地図・タイムラインに正しく描画されることを確認。

### 残作業

- **訪問順の提案**（次の候補）。最大10地点なので探索自体は軽いが、順序を評価するたびにORSを呼ぶとレート制限に当たるため、直線距離で探索して最終案のみORSで精算する等の割り切りが要る。
- 乗り換え2回以上（RAPTOR化）。接続グラフがそのまま使えるため、拡張の土台はできている。
- 乗り継ぎ徒歩時間が直線距離ベースの推定であること。

## 訪問順の提案（2026-10-12）

乗り継ぎ対応の続き。乗り継ぎが入ったことで実際に選べる順序が増えたため、ここで着手した。

### 設計

**ORSを呼ばずに探索する**のが要点。順序の評価は1回のプラン構築より桁違いに多くレグを試すため、実経路APIを呼ぶとレート制限に即座に当たる。そこで:

- `_estimate_leg_arrival()`: バス部分は `search_journeys`（ネットワーク不要）、スポット↔停留所の徒歩は直線距離×迂回係数1.3÷分速80mで推定。`_build_leg` と同じ「最初に成立した停留所の組を採用」という選び方を踏襲しており、順序決定時の想定が実際に構築される行程と乖離しにくい。
- 探索時の停留所候補は `ORDERING_STOP_CANDIDATES = 3`（本番は8）。ここで誤って「到達不能」と判定しても、悪い順序になるだけで悪いプランにはならない（最終プランは8候補で構築される）。実際、実データで推定が不成立としたケースを本番が成立させる例を確認している。

アルゴリズムは2段:

1. **greedy**: 現在地・現在時刻から最も早く着ける未訪問スポットへ。バス便が疎なこの路線網では「次に実際に乗れるもの」が問題の大半を占める。O(n²)。
2. **改善パス**: ペア入れ替えを試し、目的地への到着が早くなるものを採用（最大2周）。不成立の順序は「無限に遅い」として扱うため、**greedyが不成立の順序を返した場合に実行可能な順序を見つける役割も果たす**。レグ推定をキャッシュするためORS呼び出しはゼロのまま。

**最後の訪問地は固定**する。これは行程の目的地であって途中の立ち寄り先ではないため（片道の「ゴール」指定も、往復の起点復帰も、どちらもこれで正しく保たれる）。既定はOFF（`optimize_visit_order: false`）。

### 実データでの効果

那須塩原駅発、りんどう湖・湯本温泉・ステンドグラス美術館を各45分滞在で巡る往復（2026-10-15 08:30発）:

| | 到着 | ステップ数 |
|---|---|---|
| 指定順のまま | 19:56 | 30 |
| 順序最適化あり | **18:31** | 28 |

**1時間25分の短縮**。選ばれた順序は「ステンドグラス美術館 → りんどう湖 → 湯本温泉 → 駅」で、指定順とは別物になった。

改善パスの価値が明確に出たのは別のケース: 4スポット構成で**指定順もgreedy順も不成立**だったものを、入れ替えによって成立する順序（17:27着）に変えている。greedy単体では「不成立」で終わっていた。

所要時間は4スポットで約3.5秒、6スポットで約10秒（いずれもORS呼び出しゼロ）。

### 検証

- `python -m pytest` で **127件通過**。順序用フィクスチャ `order_net`（Hub→P→Q→Hub の順でしか巡回できない便しかない）を新設し、4テストを追加: 並べ替えで不成立→成立になること、最後の訪問地が固定されること、既定では並べ替えないこと、満たせない集合時刻を並べ替えでごまかさないこと。
- UIに「訪問順をおまかせにする」チェックボックスを追加。ONにすると中間地点のバッジが番号から「経由」に変わる（順序が利用者のものでなくなるため、番号を出すと誤解を招く）。
- 実UIで4スポット往復を実行し、指定順と異なる順序・18:31着の行程が得られることを確認。

### 残作業

- greedy＋入れ替えは厳密解ではない。地点数が最大10なので厳密な時刻依存TSPも状態数的には可能だが、1レグあたりの評価コスト（実データで0.05〜1.58秒）が支配的で現実的でない。
- 乗り換え2回以上（RAPTOR化）。
- 乗り継ぎ徒歩時間・順序探索時の徒歩時間がいずれも直線距離ベースの推定であること。

## 行程候補の提案（2026-10-12）

ユーザーの要望は `/plan/route-plan` の一段手前: 「那須塩原駅を始点、エピナールを終点として規定の時間内に2〜3スポット巡れるルートの提案。牧場・登山・動物園などのヒントを与えれば候補を出し、人間がチャットで選ぶ。ミニマップと工程表で提示できるUIの型が欲しい」。つまり**どこへ行くか自体をシステムが提案する**。

### 設計を決めた実測値

| | 実測 |
|---|---|
| レグ1本の推定（ORSなし） | 177ms |
| 駅→全29スポットの到達判定 | 5.1秒 |
| 候補プール12件の全ペア行列 | 23秒 |
| **実プラン1件の構築（ORS込み）** | **13秒** |

候補3件を完成形で返すと40秒超。**候補提示と詳細構築を分離**する二段構えにした（候補はORSを一切呼ばない → 人間が選ぶ → 選ばれた1件だけ `/plan/route-plan`）。これは要望のワークフローとも一致する。

### 実装

- **タグ付け**: `Spot` に `tags: tuple[str, ...]` を追加（JSONに無ければ空、既存データと後方互換）。30件に手で付与、語彙14種（景観10・自然8・登山4・動物4・グルメ4 ほか）。
- **`app/services/travel_estimate.py`**: `route_planner` にあった推定ロジックを切り出し、訪問順最適化と候補提案の双方から使う。モジュールをまたいでアンダースコア関数を呼ばないため。
- **`app/services/suggester.py`**: プール構築（タグ絞り込み＋起点終点の直線からの迂回量で8件に打ち切り）→ ビーム探索（幅3、深さ=spot_count）→ 終点レグを付けて期限超過を除外 → **スポットの集合で重複排除**（同じ組み合わせの順序違いを候補に並べない）→ 到着順。
- 直線距離と上限速度から**楽観的な下界**を出し、到底間に合わない枝は実探索前に刈る。これでヒント無しの最悪ケースが16.4秒→10.9秒。
- **`POST /plan/suggest`** と、候補カード（ミニマップ＋工程表＋タグ＋「この案で詳細を作る」）の比較UI（`suggest.html`）。

### 検証中に見つけたバグ: 時刻グリッドの丸め方向

候補と実プランを突き合わせたところ、**推定14:05着に対し実際15:34着（+89分）** という乖離が2/3件で発生した。

原因はキャッシュ用の時刻離散化（`TIME_GRID_MINUTES`）を**切り捨て**ていたこと。13:56発を13:50として推定するため、実際には既に発車したバスに乗れる前提になり、バスが疎なこの路線網では1本逃すと待ち時間が1時間単位で跳ね上がる。推定ロジック自体は正しく、単体で呼ぶと実際と一致していた。

**切り上げに変更**（遅い出発を仮定する方向にしか丸めない＝保守的にしかならない）。結果:

| 候補 | 修正前の差 | 修正後の差 |
|---|---|---|
| 南ヶ丘牧場 → 那須サファリパーク | +89分 | **−1分** |
| 那須サファリパーク → りんどう湖 | −1分 | −1分 |
| 那須サファリパーク（1件） | +87分 | **−1分** |

順位も正しく入れ替わった（南ヶ丘牧場案が実は最速でないと判明）。丸め方向が1文字分の違いで1時間以上の誤差を生む箇所だったため、全分について `_on_grid(m) >= m` を検証する回帰テストを置いた。

### 検証

- `python -m pytest` で **147件通過**。`suggest_net` フィクスチャと suggester 14件・API 6件を追加。
- テスト追加時、新しいAPIテストのfixtureが `app.dependency_overrides` を `pop()` していたため、import時にoverrideを設定している既存テストモジュールを壊した。退避・復元に修正。
- 実データ: タグ指定で3.2秒、ヒント無しで10.9秒。「牧場・動物」「登山・自然」「特定施設必須」いずれも妥当な候補を返す。
- UIで通し確認: タグ選択→候補3件がカード表示→1件選択→実経路付きの詳細行程へ遷移。

### 残作業

- ビーム探索はヒューリスティック（候補は「選ぶに足る」もので最適解ではない）。
- ヒント無しの10.9秒は対話的にはやや遅い。プール打ち切りを賢くする余地がある。
- 候補カードの地図は直線のスケッチ（実経路は詳細構築後）。
