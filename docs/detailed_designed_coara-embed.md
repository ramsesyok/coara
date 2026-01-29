#  coara-embed（埋め込みサービス）詳細設計書

ファイル: docs/detailed_designed_coara-embed.md
版: v0.1（ドラフト）
更新日: 2026-01-27（Asia/Tokyo）

## 1. 目的

本書は coara-embed（埋め込みサービス）の詳細設計を定義する。coara-embed は coara-cli（取り込み）および rag_api（coara-mcp 内部、問い合わせ）の双方から利用され、次を満たす。

* embedding_profile_id に基づくモデル解決
* テキストの埋め込み生成（ベクトル化）
* MetaDB（SQLite）の所有者として、Profile/Repository/IndexRun/Chunk/EmbeddingRecord 等のメタ情報を更新・参照できること

本書は、AIエージェントによる VibeCoding 実装の入力文書として利用する。

## 2. 参照

* docs/requirements_specification.md（v0.5）
* docs/interface_specification.md（v0.5）
* docs/basic_design.md（v0.2）

## 3. スコープ

### 3.1 本書に含む

* gRPC API（公開I/F）と、MetaDB更新のための補助I/F（同一gRPC内拡張）
* embedding_profile レジストリ設計（設定とロード）
* モデルロード、キャッシュ、埋め込み生成、正規化
* MetaDB（SQLite）スキーマ、制約、更新ポリシー
* エラーハンドリング、冪等性、ログ、テスト方針
* ソースコード構成、主要モジュール設計

### 3.2 本書に含まない

* Qdrant（VectorDB）の物理運用設計、インデックス最適化
* チャンク化アルゴリズム（coara-cli 側）
* rag_api の検索・再ランキング・回答生成の詳細
* 認証基盤の統合（mTLS 等は導入余地のみ記載）

## 4. 要件トレーサビリティ（抜粋）

| 要件ID        | 要件名          | 本設計での対応                                |
| ----------- | ------------ | -------------------------------------- |
| FR-EMB-001  | 埋め込み生成       | Embed RPC、モデルロード、正規化                   |
| FR-EMB-002  | モデル列挙        | ListProfiles RPC（拡張）                   |
| FR-EMB-003  | モデル切り替え      | embedding_profile_id 指定、ResolveProfile |
| FR-EMB-004  | MetaDB管理     | MetaDBスキーマ、Upsert/Record RPC（拡張）       |
| FR-EMB-005  | リポジトリ登録      | UpsertRepository RPC（拡張）               |
| FR-EMB-006  | インデックス実行記録   | Start/FinishIndexRun RPC（拡張）           |
| FR-CLI-008  | MetaDB更新依頼   | coara-cli が拡張RPCを呼び出す                  |
| NFR-SEC-003 | オンプレ運用       | 外部通信不要、ローカルモデル参照                       |
| NFR-SEC-002 | 秘匿情報をログに出さない | ログ方針に反映                                |

注記: interface_specification.md v0.5 では gRPC の主要RPCとして ResolveProfile/Embed/Health が示されている。本詳細設計では要件（FR-EMB-002/004/005/006, FR-CLI-008/011）を実装可能にするため、同一 service に拡張RPCを追加する。既存RPC名は維持し、互換性を壊さない。

## 5. 全体構成

### 5.1 利用者と呼び出し関係

* coara-cli → coara-embed（gRPC）

  * ResolveProfile, Embed
  * UpsertRepository, StartIndexRun, UpsertChunks, UpsertEmbeddingRecords, FinishIndexRun（拡張）
* rag_api（coara-mcp 内部）→ coara-embed（gRPC）

  * ResolveProfile, Embed, Health
  * 参照系（必要なら ListProfiles を許可）

coara-mcp は外部へ gRPC を公開しない。外部I/Fは MCP（HTTP+SSE）および rag_api HTTP/JSON（/v1/query 等）。

### 5.2 プロセス構成

* 単一プロセスの gRPC サーバ
* 同一プロセス内で次を保持

  * embedding_profile レジストリ（設定ロード、リロードは任意）
  * モデルキャッシュ（プロファイル単位のロード済みモデル）
  * MetaDB 接続（SQLite、SQLAlchemy）
* 並列実行

  * gRPC スレッドプールで同時呼び出しを処理
  * モデル推論は内部キューでバッチ化してもよい（初期は単純実装で開始）

## 6. 技術スタック

* 言語: Python 3.11+（推奨）
* gRPC: grpcio, protobuf
* DB: SQLite, SQLAlchemy, Alembic（マイグレーション）
* Embedding:

  * sentence-transformers または Transformers（ローカルモデル参照）
  * torch（CPU/GPUは環境に合わせる）
* 設定: YAML（ruamel.yaml または PyYAML）
* ログ: 標準 logging（JSON整形は任意）、相関ID対応
* テスト: pytest, grpcio-testing（または実起動E2E）

## 7. 設定設計

### 7.1 設定ファイル

* configs/coara-embed.yaml

例（概略）:

```yaml
service:
  version: "0.1.0"
  grpc_listen: "0.0.0.0:50051"
  max_workers: 16
  request_max_inputs: 256
  request_max_chars: 20000

paths:
  profile_registry: "services/coara-embed/config/profiles.yaml"
  metadb_sqlite: "data/metadb.sqlite3"
  model_cache_dir: "data/models"   # 事前配置 or キャッシュ
  tmp_dir: "data/tmp"

embedding:
  default_device: "cpu"            # "cuda" 等
  model_load_timeout_sec: 300
  model_cache:
    max_loaded_models: 2           # LRU
  batching:
    enabled: true
    max_batch_size: 64
    max_wait_ms: 10

security:
  tls:
    enabled: false
    cert_file: ""
    key_file: ""
    client_ca_file: ""             # mTLS の場合
logging:
  level: "INFO"
  redact:
    enabled: true
```

### 7.2 embedding_profile レジストリ

* services/coara-embed/config/profiles.yaml

例（概略）:

```yaml
profiles:
  prof-default:
    model:
      kind: "sentence_transformers"
      model_path: "data/models/bge-small-ja"   # ローカルパス
      model_id: "bge-small-ja"
      model_version: "2025-xx"
    embedding:
      dimension: 384
      normalize: true
      max_chars: 20000
    collection:
      name: "coara_prof-default"
  prof-code:
    model:
      kind: "transformers"
      model_path: "data/models/e5-code"
      model_id: "e5-code"
      model_version: "2025-xx"
    embedding:
      dimension: 768
      normalize: true
      max_chars: 20000
    collection:
      name: "coara_prof-code"
```

設計ルール:

* embedding_profile_id は profiles のキー
* collection.name は embedding_profile 単位で一意
* model_id/model_version/dimension/normalize は ResolveProfile/Embed の応答に必ず含める

## 8. gRPC API 設計

### 8.1 パッケージとサービス

* package: coara.embed.v1
* service: CoaraEmbed

インタフェース仕様 v0.5 の主要RPCを維持しつつ、要件充足のために拡張RPCを追加する。

### 8.2 主要RPC（互換維持）

#### 8.2.1 ResolveProfile

用途:

* embedding_profile_id から、モデル情報とコレクション名を解決する
* rag_api/coara-cli が、以降の処理（Embed、Qdrant検索/登録）に必要なメタ情報を得る

入力:

* embedding_profile_id

出力:

* embedding_profile_id
* model_id, model_version
* dimension
* normalize（プロファイル既定）
* collection_name

エラー:

* NOT_FOUND: embedding_profile_id が存在しない
* INTERNAL: レジストリ読み込み不整合

#### 8.2.2 Embed

用途:

* テキスト入力をベクトル化する
* rag_api（問い合わせ）と coara-cli（取り込み）で共通利用

入力:

* embedding_profile_id
* inputs[]
* normalize（任意、未指定ならプロファイル既定に従う）

出力:

* vectors（入力順を維持）
* model_id, model_version, dimension

エラー:

* NOT_FOUND: embedding_profile_id が存在しない
* INVALID_ARGUMENT: inputs が空、サイズ超過など
* RESOURCE_EXHAUSTED: バッチ上限超過など
* INTERNAL: 推論失敗

部分失敗方針:

* 初期実装は「RPC単位で失敗」に寄せる（入力が無効なら INVALID_ARGUMENT）
* 将来拡張として、入力ごとの結果を返す形式（EmbeddingResult）へ拡張してもよい

  * VibeCoding 初期段階では、呼び出し側（coara-cli）が入力を事前検証し、失敗を局所化する

#### 8.2.3 Health

用途:

* 死活監視、バージョン確認

入力:

* なし

出力:

* status（例: ok）
* version（coara-embed のサービス版）

### 8.3 拡張RPC（MetaDB更新・参照）

本拡張は、要件 FR-EMB-002/004/005/006 および FR-CLI-008/011 を実装可能にするための設計である。実装初期から提供する。

#### 8.3.1 ListProfiles

用途:

* 利用可能な embedding_profile の一覧とメタ情報を返す（FR-EMB-002）

入力:

* なし（将来 filter 追加可）

出力:

* profiles[]（embedding_profile_id, model_id, model_version, dimension, normalize, collection_name, max_chars 等）

#### 8.3.2 UpsertRepository

用途:

* リポジトリ情報の登録・更新、repo_id の払い出し（FR-EMB-005）

入力:

* repo_hint（任意）: 既存repo_idが分かっている場合
* display_name（任意）
* git_url（任意）
* local_path_hint（任意）

出力:

* repo_id（新規なら発行、既存なら同一を返す）

冪等性:

* repo_hint があればそれをキーに更新
* repo_hint が無い場合、git_url を一意キーとして upsert（運用でルール化）

  * git_url が無い場合は新規発行のみ（重複の可能性は許容し、運用で回避）

#### 8.3.3 StartIndexRun / FinishIndexRun

用途:

* インデックス実行の開始・終了を記録し、検索可能にする（FR-EMB-006）
* coara-cli の実行結果追跡（FR-CLI-011）

Start 入力:

* repo_id
* embedding_profile_id
* mode（full/incremental/re-embed）
* commit_id（任意）
* started_by（任意: ユーザ名/マシン名）
* client_run_id（任意: coara-cli 側の実行ID）

Start 出力:

* index_run_id（発行）

Finish 入力:

* index_run_id
* status（success/failed/canceled）
* counts（processed/skipped/errors）
* error_summary（任意、秘匿情報を含めない）

冪等性:

* Finish は index_run_id をキーに上書き可能（最後の状態を正とする）
* Start の再送は client_run_id が同一の場合、同一 index_run_id を返す（重複防止）

#### 8.3.4 UpsertChunks

用途:

* Chunk メタ情報を MetaDB に登録する（FR-EMB-004）
* VectorDB のメタデータと同一の識別子（chunk_id 等）を持たせる

入力（代表）:

* repo_id
* commit_id
* chunks[]:

  * chunk_id（安定ID、coara-cli生成）
  * file_path
  * start_line, end_line
  * language（任意）
  * content_hash（任意）
  * char_count（任意）

出力:

* upserted_count
* warnings（任意）

冪等性:

* (repo_id, commit_id, chunk_id) を一意キーとして upsert

#### 8.3.5 UpsertEmbeddingRecords

用途:

* 埋め込み生成と VectorDB 登録の対応を追跡する（FR-EMB-004, FR-CLI-008）
* chunk と embedding_profile の組を記録し、再埋め込みや差分追跡に利用する

入力（代表）:

* repo_id
* commit_id
* embedding_profile_id
* index_run_id（任意）
* records[]:

  * chunk_id
  * qdrant_point_id（推奨: chunk_id をそのまま使用、または ULID）
  * vector_hash（任意: 生成ベクトルのハッシュ、または入力hashの再掲）
  * dimension（任意: プロファイルと整合チェック用）

出力:

* upserted_count
* warnings（任意）

冪等性:

* (repo_id, commit_id, embedding_profile_id, chunk_id) を一意キーとして upsert

### 8.4 proto（詳細設計案）

実装時の proto は、interface_specification.md の概略に加えて拡張RPC/メッセージを定義する。例:

```proto
syntax = "proto3";
package coara.embed.v1;

service CoaraEmbed {
  rpc ResolveProfile(ResolveProfileRequest) returns (ResolveProfileResponse);
  rpc Embed(EmbedRequest) returns (EmbedResponse);
  rpc Health(HealthRequest) returns (HealthResponse);

  rpc ListProfiles(ListProfilesRequest) returns (ListProfilesResponse);
  rpc UpsertRepository(UpsertRepositoryRequest) returns (UpsertRepositoryResponse);
  rpc StartIndexRun(StartIndexRunRequest) returns (StartIndexRunResponse);
  rpc FinishIndexRun(FinishIndexRunRequest) returns (FinishIndexRunResponse);
  rpc UpsertChunks(UpsertChunksRequest) returns (UpsertChunksResponse);
  rpc UpsertEmbeddingRecords(UpsertEmbeddingRecordsRequest) returns (UpsertEmbeddingRecordsResponse);
}

message ResolveProfileRequest { string embedding_profile_id = 1; }
message ResolveProfileResponse {
  string embedding_profile_id = 1;
  string model_id = 2;
  string model_version = 3;
  uint32 dimension = 4;
  bool normalize = 5;
  string collection_name = 6;
  uint32 max_chars = 7;
}

message EmbedRequest {
  string embedding_profile_id = 1;
  repeated string inputs = 2;
  bool normalize = 3;
}

message Vector { repeated float values = 1; }
message EmbedResponse {
  string embedding_profile_id = 1;
  string model_id = 2;
  string model_version = 3;
  uint32 dimension = 4;
  repeated Vector vectors = 5;
}

message HealthRequest {}
message HealthResponse { string status = 1; string version = 2; }

message ListProfilesRequest {}
message ProfileInfo {
  string embedding_profile_id = 1;
  string model_id = 2;
  string model_version = 3;
  uint32 dimension = 4;
  bool normalize = 5;
  string collection_name = 6;
  uint32 max_chars = 7;
}
message ListProfilesResponse { repeated ProfileInfo profiles = 1; }

message UpsertRepositoryRequest {
  string repo_hint = 1;
  string display_name = 2;
  string git_url = 3;
  string local_path_hint = 4;
}
message UpsertRepositoryResponse { string repo_id = 1; }

message StartIndexRunRequest {
  string repo_id = 1;
  string embedding_profile_id = 2;
  string mode = 3;        // full/incremental/re-embed
  string commit_id = 4;
  string started_by = 5;
  string client_run_id = 6;
}
message StartIndexRunResponse { string index_run_id = 1; }

message FinishIndexRunRequest {
  string index_run_id = 1;
  string status = 2;      // success/failed/canceled
  uint32 processed = 3;
  uint32 skipped = 4;
  uint32 errors = 5;
  string error_summary = 6;
}
message FinishIndexRunResponse { string status = 1; }

message ChunkInfo {
  string chunk_id = 1;
  string file_path = 2;
  uint32 start_line = 3;
  uint32 end_line = 4;
  string language = 5;
  string content_hash = 6;
  uint32 char_count = 7;
}

message UpsertChunksRequest {
  string repo_id = 1;
  string commit_id = 2;
  repeated ChunkInfo chunks = 3;
}
message UpsertChunksResponse { uint32 upserted = 1; repeated string warnings = 2; }

message EmbeddingRecordInfo {
  string chunk_id = 1;
  string qdrant_point_id = 2;
  string vector_hash = 3;
  uint32 dimension = 4;
}
message UpsertEmbeddingRecordsRequest {
  string repo_id = 1;
  string commit_id = 2;
  string embedding_profile_id = 3;
  string index_run_id = 4;
  repeated EmbeddingRecordInfo records = 5;
}
message UpsertEmbeddingRecordsResponse { uint32 upserted = 1; repeated string warnings = 2; }
```

## 9. モデル実装設計

### 9.1 モデルロード

* embedding_profile_id ごとに model_path を解決
* 初回利用時にロードし、キャッシュする
* キャッシュ数は max_loaded_models を上限とし、LRU で退避
* ロードが重い場合は運用で事前ウォームアップ（起動時に指定プロファイルを先読み）を許容

### 9.2 埋め込み生成

* inputs[] をバッチにまとめて推論
* 入力サイズ制限

  * request_max_inputs を超える場合は INVALID_ARGUMENT
  * request_max_chars を超える入力は INVALID_ARGUMENT（呼び出し側で分割を推奨）
* normalize

  * リクエスト指定があればそれを優先
  * 未指定ならプロファイル既定
  * L2 正規化を適用

### 9.3 デバイス選択

* default_device に従い CPU/GPU を選択
* 例外時は CPU フォールバックは初期は行わない（内部整合を優先）

  * 将来拡張としてフォールバック設定を追加可能

## 10. MetaDB（SQLite）設計

### 10.1 DBの所有と配置

* coara-embed が単独所有する SQLite ファイル
* 外部プロセスが直接ファイルを参照しない運用を前提
* 参照・更新は gRPC 経由のみ

### 10.2 テーブル設計（案）

テーブル: embedding_profiles

* embedding_profile_id (PK)
* model_id
* model_version
* dimension
* normalize
* collection_name
* max_chars
* updated_at

注記: 実体は profiles.yaml がソースオブトゥルース。DBにはキャッシュとして格納し、起動時同期（upsert）する。

テーブル: repositories

* repo_id (PK, ULID推奨)
* display_name
* git_url (UNIQUE, NULL可)
* local_path_hint
* created_at
* updated_at

テーブル: index_runs

* index_run_id (PK, ULID推奨)
* repo_id (FK)
* embedding_profile_id (FK)
* mode
* commit_id
* status (running/success/failed/canceled)
* started_by
* client_run_id (UNIQUE, NULL可)
* started_at
* finished_at
* processed
* skipped
* errors
* error_summary

テーブル: chunks

* repo_id (FK)
* commit_id
* chunk_id
* file_path
* start_line
* end_line
* language
* content_hash
* char_count
* created_at
* updated_at
* PRIMARY KEY (repo_id, commit_id, chunk_id)

テーブル: embedding_records

* repo_id (FK)
* commit_id
* embedding_profile_id (FK)
* chunk_id
* qdrant_point_id
* vector_hash
* dimension
* index_run_id (FK, NULL可)
* created_at
* updated_at
* PRIMARY KEY (repo_id, commit_id, embedding_profile_id, chunk_id)

推奨インデックス:

* repositories(git_url)
* index_runs(repo_id, started_at desc)
* chunks(repo_id, commit_id, file_path)
* embedding_records(repo_id, commit_id, embedding_profile_id)

### 10.3 マイグレーション

* Alembic でスキーマ管理
* 起動時に自動マイグレーションは任意（運用方針で選択）

  * 初期は起動時自動適用を有効化してもよい（閉域運用前提）

## 11. 代表シーケンス（coara-cli 側のMetaDB更新を含む）

### 11.1 インデックス（詳細）

1. coara-cli → UpsertRepository（repo_id取得/確定）
2. coara-cli → ResolveProfile（collection_name 等取得）
3. coara-cli → StartIndexRun（index_run_id取得）
4. coara-cli でチャンク化
5. チャンクをバッチ化して Embed
6. coara-cli → Qdrant upsert（point_idは chunk_id を推奨）
7. coara-cli → UpsertChunks（chunkメタ情報）
8. coara-cli → UpsertEmbeddingRecords（chunkとprofileの紐付け）
9. coara-cli → FinishIndexRun（集計と結果）

問い合わせ（rag_api）では 5) の Embed のみを利用し、MetaDB更新拡張RPCは呼ばない（必要なら検索ログ用途として別途設計）。

## 12. エラーハンドリング設計

### 12.1 gRPC ステータス指針

* INVALID_ARGUMENT

  * embedding_profile_id 未指定、inputs が空、サイズ超過
* NOT_FOUND

  * embedding_profile_id 不明、repo_id 不明（拡張RPCで）
* FAILED_PRECONDITION

  * DBマイグレーション未適用など、実行前提が崩れている
* RESOURCE_EXHAUSTED

  * サーバが受け付け可能な上限を超えた（入力数、キュー）
* UNAVAILABLE

  * サービス起動直後でモデルロード中など（必要なら）
* INTERNAL

  * 予期せぬ例外、推論失敗

### 12.2 ログ方針

* リクエストごとに request_id（クライアントが渡す場合はメタデータで受け取る）を相関IDとして出力
* 入力テキストは原則ログに出さない（サイズ・件数など統計のみ）
* エラー要約は秘匿情報を含めない
* DB更新系RPCは、repo_id/index_run_id を必ずログに含める

## 13. セキュリティ設計（最小）

* 外部ネットワークに依存しない（モデルはローカル配置）
* TLS/mTLS は設定で有効化可能にする（初期は無効でもよい）
* 認証・認可は将来拡張（閉域内前提）
* ログに秘密情報（APIキー、入力全文）を残さない

## 14. テスト設計

### 14.1 単体テスト

* profiles.yaml のロードと検証（必須項目、重複）
* ResolveProfile の正常/異常
* Embed の入力制限、normalize の挙動
* DB upsert の冪等性（同じ入力を複数回）

### 14.2 結合テスト

* sqlite を一時ファイルで起動し、UpsertRepository → StartIndexRun → UpsertChunks → UpsertEmbeddingRecords → FinishIndexRun の一連
* gRPC 経由での呼び出し（実サーバ起動）

### 14.3 疑似モデル

* CI/ローカルで重いモデルを使わないため、ダミー embedder を用意

  * inputs を固定次元の擬似ベクトルに変換（ハッシュベース）
  * モデル差分や正規化の挙動を再現

## 15. ソースコード構成（coara-embed）

リポジトリ: coara/services/coara-embed

```text
services/coara-embed/
  app/
    server.py                 # gRPCサーバ起動
    config.py                 # coara-embed.yaml ロード
    registry/
      profiles.py             # profiles.yaml ロード、検証、ProfileInfo生成
    embedding/
      loader.py               # モデルロード、キャッシュ
      embedder.py             # Embed実装（正規化含む）
    rpc/
      coara_embed_pb2.py
      coara_embed_pb2_grpc.py
      service.py              # CoaraEmbedServicer 実装
    metadb/
      engine.py               # SQLAlchemy engine/session
      models.py               # ORMモデル
      repo.py                 # DB操作（upsert等）
      migrations/             # Alembic
    util/
      ids.py                  # ULID生成等
      hashing.py              # content_hash, vector_hash
      logging.py              # ログ初期化
  config/
    profiles.yaml
  tests/
    unit/
    integration/
  pyproject.toml
```

実装順序（VibeCoding向けの推奨）:

1. 設定ロード（coara-embed.yaml, profiles.yaml）
2. ResolveProfile/Health を実装
3. ダミー embedder で Embed を実装し、E2E を通す
4. 実モデル loader/embedder を実装
5. MetaDB（SQLite）スキーマと拡張RPC（Upsert/IndexRun）を実装
6. 結合テストを増やす（冪等性、再実行）

## 16. 未決事項（実装時に固定する）

* repo_id の一意性ルール（git_url を必須にするか）
* qdrant_point_id のルール（chunk_id を採用するか、別IDにするか）
* Embed の部分失敗をどの形式で返すか（初期はRPC単位失敗、将来は結果配列拡張）
* TLS/mTLS を初期から必須にするか（閉域前提で段階導入か）

以上。
