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
- タクシーモードはスコープ外のまま
- スポットマスタは3件のPoCデータのみ。実データへの拡充は別途

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

## TODO: デバッグ・確認用のHuman Interface UI（2026-08-23メモ、未着手）

現在バックエンドAPI（FastAPI）のみで、動作確認はcurl/pythonスクリプト/実データ検証に頼っている。ユーザーより、次のステップとして人間が使うためのUIを設計したいとの要望を受けた。

要望されている画面構成（1画面に収めたい）:

- 現在ロードされている路線図の可視化
- 駅名（停留所）の検索
- スポットの可視化
- スポットの追加
- 日付・時刻の選択

ルーティング結果（`route-plan`）は時系列表示＋地図上での選択が必要になるため、メイン画面とは別タブまたは別窓に分離するのが現時点の想定（要件はこれから詰める）。

このタスクは、GTFSバグ調査中にユーザーから「那須塩原⇔那須高原エリアの路線カバレッジを地図で可視化して確認したい」という要望が出たことがきっかけ。バックエンド側の調査は`data/gtfs/`の生データを直接pandasで読む形で代替したが、今後同様の確認を繰り返すなら専用UIが効率的という判断。
