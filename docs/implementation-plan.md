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

- 複数フィードを扱う場合のID prefix 方針 → 下記「ID衝突調査結果」で調査済み。実装は未着手
- 実GTFS ZIPを読み込んだfixtureまたは手動検証 → 完了（下記Phase 2セクション参照）

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

- `/non-transit/route`（Google Maps Directions API連携）はGoogle API key入手後に着手
- 実GTFSデータを `data/gtfs/` に配置し、`GTFS_DIR` を向け替えて動作確認

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

## 現時点の設計メモ

- タクシーはGoogle Maps上の専用modeではなく、`driving` ETA + 独自料金モデルとして扱う。
- バス運賃はGTFSに含まれない可能性があるため、初期APIでは nullable とする。
- 本番に近づける段階では、経路探索リクエストに `departure_date` を必須化する。
