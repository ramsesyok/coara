# coara-cli（取り込み・索引用CLI）詳細設計書

ファイル: docs/detailed_designed_coara-cli.md
版: v0.1（ドラフト）
更新日: 2026-01-29（Asia/Tokyo）

## 1. 目的

本書は coara-cli（取り込み・索引用CLI）の詳細設計を定義する。coara-cli は次を満たす。

* Gitリポジトリを対象に、差分検知・解析・チャンク化を行う
* coara-embed（gRPC）へ embedding_profile 解決と埋め込み生成を依頼する
* VectorDB（Qdrant）へチャンクベクトルとメタデータを upsert する
* MetaDB（SQLite）更新は coara-embed が所有者として実施する前提で、coara-cli は gRPC 経由で更新依頼する

本書は、AIエージェントによる VibeCoding 実装の入力文書として利用する。

## 2. 参照

* docs/requirements_specification.md（v0.5）
* docs/interface_specification.md（v0.5）
* docs/basic_design.md（v0.2）

## 3. スコープ

### 3.1 本書に含む

* CLI サブコマンド/引数/設定
* Git 取得、差分検知、ファイル選別
* チャンク化（tree-sitter優先、フォールバックあり）
* chunk_id 生成、メタデータ設計
* coara-embed 呼び出し設計（ResolveProfile/Embed/Health と、MetaDB更新系の拡張RPC）
* Qdrant への削除・upsert、コレクション作成/検証
* エラーハンドリング、冪等性、リトライ
* テスト方針、ソース構成

### 3.2 本書に含まない

* rag_api（coara-mcp内部）の検索・回答アルゴリズム詳細
* Qdrant の運用設計（HA、バックアップ、チューニング）
* モデル配布/更新の運用手順（別資料）

## 4. 要件トレーサビリティ（抜粋）

| 要件ID        | 要件名          | 本設計での対応                          |
| ----------- | ------------ | -------------------------------- |
| FR-CLI-*    | 取り込み・索引      | index/re-embed コマンド、Git/差分/チャンク化 |
| FR-EMB-*    | 埋め込み・MetaDB  | coara-embed gRPC 呼び出しと更新依頼       |
| 制約: オンプレ/秘匿 | 外部送信禁止       | ログ/出力の秘匿、モデルはサーバ側                |
| 代表フロー 6.2   | インデックス/問い合わせ | 本設計はインデックス側を確定                   |

注記: interface_specification.md v0.5 では coara-embed の gRPC を ResolveProfile/Embed/Health としている。本詳細設計では MetaDB 更新（Repository/IndexRun/Chunk/EmbeddingRecord）を実装可能にするため、同一 gRPC service に拡張RPCを追加する前提を置く（既存RPCの互換性は維持）。

## 5. coara-cli の責務と非責務

### 5.1 責務

* 対象リポジトリの取得（clone/fetch）と作業ツリー生成
* 差分検知（full/incremental/re-embed）
* 除外ルール適用（パス/拡張子/サイズ/バイナリ）
* 解析・チャンク化、チャンクメタ生成
* coara-embed へ embedding_profile 解決/埋め込み生成依頼
* Qdrant へ削除・upsert（チャンクメタをpayloadに含める）
* MetaDB 更新のための情報を coara-embed に送る（gRPC拡張）
* 実行ログ、統計、失敗時の継続（設定により）

### 5.2 非責務

* MetaDB（SQLite）への直接アクセス
* 埋め込みモデルの直接選択（model_id 指定は原則しない）
* 生成AIによる回答生成（rag_api 側）

## 6. CLI 設計

### 6.1 実行ファイル名

* coara-cli

### 6.2 サブコマンド

1. index

* 目的: full または incremental のインデックス（チャンク化 + 埋め込み + Qdrant upsert + MetaDB更新依頼）

2. re-embed

* 目的: 新しい embedding_profile で再埋め込みし、新コレクションへ登録する

3. health

* 目的: coara-embed と Qdrant の疎通確認

4. list-profiles（任意だが推奨）

* 目的: coara-embed が提供する embedding_profile 一覧表示（運用確認用）

5. validate-config（任意だが推奨）

* 目的: YAML と必須項目、除外/上限などの整合性チェック

### 6.3 共通オプション（例）

* --config, -c: 設定ファイルパス（既定: configs/coara-cli.yaml）
* --repo: 対象 repo_id または repo alias（設定から解決）
* --profile: embedding_profile_id（設定の既定を上書き）
* --workdir: 作業ディレクトリ（cloneや一時ファイル）
* --dry-run: 計画のみ表示し、Embed/Qdrant/MetaDB更新を行わない
* --verbose: 詳細ログ

### 6.4 代表コマンド例

```bash
# full index（設定の既定profile）
coara-cli index --repo repoA --mode full

# incremental index（差分はGitで検知）
coara-cli index --repo repoA --mode incremental

# re-embed（新profileで新コレクション作成・登録）
coara-cli re-embed --repo repoA --profile prof-code

# 疎通
coara-cli health
```

## 7. 設定設計（configs/coara-cli.yaml）

### 7.1 設定ファイル例（概略）

```yaml
service:
  version: "0.1.0"
  default_mode: "incremental"   # full / incremental
  continue_on_error: true

paths:
  workdir: "./.coara/work"
  state_dir: "./.coara/state"   # ローカル状態（任意）
  log_dir: "./.coara/logs"

targets:
  repos:
    - repo_key: "repoA"                 # CLI指定用キー
      git_url: "ssh://git/repoA.git"
      default_profile: "prof-default"
      checkout_ref: "main"              # 任意（未指定ならHEAD）
      include:
        - "**/*"
      exclude:
        - "**/.git/**"
        - "**/node_modules/**"
        - "**/vendor/**"
        - "**/dist/**"
        - "**/*.png"
        - "**/*.jpg"
      limits:
        max_file_bytes: 1048576         # 1MiB
        max_total_files: 200000
        max_chars_per_chunk: 20000

embed:
  endpoint: "coara-embed:50051"
  timeout_sec: 30
  retry:
    max_attempts: 5
    backoff_ms: 200
    backoff_max_ms: 5000
  batch:
    max_inputs: 64
    max_chars_per_input: 20000

qdrant:
  endpoint: "http://qdrant:6333"
  api_key: ""                    # 任意（閉域なら空でもよい）
  distance: "Cosine"
  shard_number: 1
  replication_factor: 1
  wait: true
  upsert_batch_points: 256
  retry:
    max_attempts: 5
    backoff_ms: 200
    backoff_max_ms: 5000

chunking:
  strategy: "tree-sitter"        # tree-sitter / fallback
  tree_sitter:
    timeout_ms: 200
    max_nodes: 200000
  fallback:
    by: "lines"                  # lines / chars
    lines_per_chunk: 200

logging:
  level: "INFO"
  redact:
    enabled: true
```

### 7.2 設計ルール

* file_path は repo ルートからの相対パスで保持し、区切りは常に "/"（Windowsでも同様）とする
* exclude は glob で評価（doublestar 推奨）
* 取り込み対象の上限を設定し、暴走を防ぐ（max_total_files など）

## 8. データモデル（coara-cli 内部）

### 8.1 基本構造

* RepoTarget

  * repo_key, git_url, checkout_ref, default_profile, include/exclude/limits

* IndexContext

  * repo_id（coara-embed 側で確定）
  * embedding_profile_id
  * resolved_profile（collection_name, dimension, normalize, model_id, model_version）
  * mode（full/incremental/re-embed）
  * head_commit_id
  * index_run_id（MetaDB記録用、coara-embed が発行）
  * client_run_id（ULID、coara-cli 生成。再送/冪等性キー）

* SourceFile

  * file_path（相対、"/"区切り）
  * abs_path
  * size_bytes
  * language（推定）
  * content_hash（sha256）

* Chunk

  * chunk_id（安定ID）
  * file_path
  * start_line, end_line（1始まり）
  * symbol（任意: 関数/クラス名など）
  * text（Embed入力。ログには出さない）
  * char_count
  * content_hash（chunk本文のsha256）

* QdrantPoint

  * id（推奨: chunk_id）
  * vector
  * payload（後述）

## 9. chunk_id 生成方針

### 9.1 目的

* 同一の論理チャンクを同一IDで識別し、upsert で更新できること
* 変更で消滅したチャンクを削除できること（後述の file_path 単位削除でも担保）

### 9.2 生成規則（推奨）

* chunk_key（文字列）を以下で構成し、sha256 hex を chunk_id とする

構成要素（上から順に連結）:

* repo_id
* file_path
* chunk_kind（例: "function" / "class" / "block" / "fallback"）
* symbol（存在する場合は qualified name、無い場合は空）
* ordinal（同一 symbol が複数出る場合の出現順。tree-sitter traversal の安定順）

例:

```text
repo_id|src/foo/bar.go|function|Foo.Bar|0
```

* fallback チャンクの場合

  * chunk_kind="fallback"
  * symbol=""
  * ordinal は file 内の chunk 連番

注記:

* content_hash は chunk_id に含めない（更新を upsert で上書きできるようにする）
* ただし chunking の再計算で ordinal が大きく変わると chunk_id が変わるため、削除は file_path 単位削除で担保する（10章）

## 10. インデックス処理設計

### 10.1 共通パイプライン（概要）

1. 設定読み込み、引数反映
2. 対象 repo を解決（repo_key → git_url）
3. Git 取得（clone/fetch）と checkout（checkout_ref）
4. coara-embed へ UpsertRepository（repo_id確定）
5. coara-embed へ ResolveProfile（collection_name/dimension 取得）
6. index_run 開始（StartIndexRun）
7. 差分計画（full/incremental/re-embed）
8. 対象ファイル列挙・フィルタ（exclude、バイナリ、サイズ）
9. ファイルごとに以下を実施

   * 旧チャンク削除（Qdrant delete filter: repo_id + file_path + 必要なら profile）
   * チャンク化 → Embed（バッチ）→ Qdrant upsert
   * MetaDB更新（UpsertChunks, UpsertEmbeddingRecords）
10. index_run 終了（FinishIndexRun）
11. 結果サマリ出力（JSON/テキスト）

dry-run の場合は 9) 以降を実行せず、計画のみ出力する。

### 10.2 モード別の差分計画

* full

  * 対象 repo の全ファイルを再処理する
  * 事前に repo 単位で削除してから登録してもよい

    * Qdrant delete filter: repo_id（profileコレクション内）
  * 処理量が大きい場合は file_path 単位削除で逐次登録でもよい

* incremental

  * Git diff で変更ファイル集合を得る

    * 変更/追加: 当該 file_path を削除して再登録
    * 削除: 当該 file_path を削除のみ（登録なし）
    * rename: 旧 file_path を削除、 新 file_path を追加として扱う
  * base commit の決め方

    * 優先: ローカル状態ファイル（state_dir）に last_indexed_commit があればそれを base とする
    * 次点: --base-commit 明示指定
    * どちらも無い場合は full にフォールバック（安全側）

* re-embed

  * 新 embedding_profile の collection へ full 同等に登録する
  * 旧 profile との差分は見ない（初期は単純実装）
  * 終了後に active profile 切替（16章の拡張）を行う

### 10.3 Git 操作設計

* 実装方針

  * 既定: 外部 git コマンド呼び出し（LFS/submodule/認証の成熟度を優先）
  * オプション: go-git 実装（外部 git 不可環境向け）
* clone と fetch

  * workdir/repo_key を作業領域にする
  * 初回: git clone
  * 2回目以降: git fetch --all（必要に応じて prune）
* diff

  * base..head の changed files を取得
  * rename も検知（git diff --name-status -M 相当）

### 10.4 ファイル選別

* include/exclude glob を適用（相対パス）
* バイナリ判定

  * NUL 文字検出、または簡易MIME推定（実装は軽量に）
* サイズ上限

  * max_file_bytes 超過は skip（ログとカウントのみ）

### 10.5 チャンク化

* 優先: tree-sitter

  * 言語は拡張子で推定（例: .go, .rs, .cpp, .h, .java, .py, .ts, .vue など）
  * node 走査で関数/クラス/メソッド/設定ブロックなどを抽出
  * chunk テキストは元ソースの該当範囲
  * start_line/end_line を保持
  * symbol は可能な限り qualified name（例: Class#method）
* フォールバック

  * lines_per_chunk ごとに分割
  * 先頭/末尾行番号を保持

実装注意:

* 1ファイルのチャンク数に上限を設ける（max_nodes, max_chunks_per_file など）
* 文字数が max_chars_per_chunk を超える場合はさらに分割する（過大入力防止）

### 10.6 Qdrant 登録（削除と upsert）

#### 10.6.1 コレクション

* collection_name は ResolveProfile 応答に従う（embedding_profile 単位）
* 起動時に存在確認し、無ければ作成する
* vector size は dimension と一致させる（不一致ならエラーで中断）

#### 10.6.2 削除ポリシー（重要）

* stale チャンクを残さないため、変更対象 file_path は登録前に一括削除する

削除フィルタ例（概念）:

* repo_id == <repo_id>
* file_path == <file_path>

これにより、チャンクIDの安定性が完璧でなくても、当該ファイル内の旧チャンクは残らない。

削除ケース:

* incremental の変更/追加ファイル: 削除→再登録
* incremental の削除ファイル: 削除のみ
* rename: 旧パス削除、新パスは追加として処理
* full: repo_id 一括削除（任意）または file 逐次削除

#### 10.6.3 upsert payload（推奨）

Qdrant payload には最低限以下を含める。

* repo_id
* embedding_profile_id
* commit_id（head）
* file_path
* start_line
* end_line
* language
* chunk_id
* symbol（任意）
* content_hash（任意: chunk本文hash）
* indexed_at（RFC3339文字列）

注記:

* snippet 生成は rag_api 側で file_path + line 範囲を基に取得する想定のため、payload には行範囲が必須
* chunk本文を payload に保存するかは運用次第（容量と秘匿の観点）。初期は保存しない前提を推奨

### 10.7 coara-embed 呼び出し（gRPC）

#### 10.7.1 必須RPC（v0.5）

* Health
* ResolveProfile
* Embed

#### 10.7.2 MetaDB更新用の拡張RPC（推奨）

coara-cli が MetaDB を直接触れない前提を成立させるため、以下を同一 service に追加する。

* UpsertRepository（repo_id 確定）
* StartIndexRun / FinishIndexRun
* UpsertChunks
* UpsertEmbeddingRecords
* ListProfiles（運用確認）

拡張RPCの詳細は coara-embed 詳細設計に従う（本書では呼び出しタイミングと入力要件を確定する）。

#### 10.7.3 Embed バッチング

* 1回の Embed に送る inputs は max_inputs 以内
* 1入力の文字数は max_chars_per_input 以内
* 失敗時は指数バックオフで再試行（gRPCコードにより判定）

  * 再試行対象: UNAVAILABLE, RESOURCE_EXHAUSTED, DEADLINE_EXCEEDED（状況依存）
  * 再試行しない: INVALID_ARGUMENT, NOT_FOUND

#### 10.7.4 相関ID

* gRPC metadata で request_id（ULID）を付与し、coara-embed のログと相関させる
* index_run_id / client_run_id もメタデータに付与してよい（運用が楽になる）

### 10.8 MetaDB 更新（coara-embed へ依頼）

* UpsertRepository: リポジトリ単位で最初に実施し repo_id を確定
* StartIndexRun: 実行開始時
* UpsertChunks: file 処理単位、または一定件数ごとにバッチ送信
* UpsertEmbeddingRecords: Qdrant upsert 成功後に送信（point_id と chunk_id の対応を確定）
* FinishIndexRun: 実行終了時（success/failed と集計値）

失敗時の原則:

* Qdrant upsert に失敗した chunk は UpsertEmbeddingRecords に含めない
* UpsertChunks は「観測されたチャンクメタ」の記録であり、UpsertEmbeddingRecords で実体登録を追跡する

## 11. re-embed 設計

### 11.1 目的

* embedding_profile を変更し、新しい collection に全チャンクを再登録する
* 旧 collection は保持し、切替後に必要なら削除する（運用ポリシー）

### 11.2 手順（推奨）

1. UpsertRepository（repo_id確定）
2. ResolveProfile（新 profile → new collection）
3. StartIndexRun（mode="re-embed"）
4. full 相当の登録（file単位削除→Embed→upsert→MetaDB更新）
5. FinishIndexRun（success）
6. active profile 切替（下記 16章）

## 12. エラーハンドリング

### 12.1 失敗分離

* 既定は continue_on_error=true とし、ファイル単位で失敗しても継続する
* 重大エラー（設定不整合、Qdrantコレクション次元不一致、ResolveProfile失敗など）は即中断

### 12.2 リトライ方針

* gRPC と Qdrant のネットワーク系は指数バックオフ
* Git 操作は回数を限定して再試行
* 失敗したファイルは最終サマリに一覧化（機密を含めない粒度）

### 12.3 冪等性

* Qdrant upsert は chunk_id を point_id とすることで冪等になる
* file 事前削除→upsert の組み合わせにより、複数回実行しても整合が取りやすい
* StartIndexRun は client_run_id を冪等キーにして再送に耐える（coara-embed側実装）

## 13. ログと出力

### 13.1 ログ

* 入力テキスト（チャンク本文、検索文など）はログに出さない
* 代わりに件数、サイズ、file_path、chunk数、処理時間を記録
* 相関ID: client_run_id / index_run_id / request_id を必ず含める

### 13.2 実行結果サマリ

* 既定は標準出力に概要、詳細は JSON を log_dir に出力（任意）
* 出力例（概念）:

  * repo_key, repo_id, profile_id, collection_name, head_commit
  * files_total, files_processed, files_failed
  * chunks_total, chunks_upserted, chunks_failed
  * embed_calls, embed_failed
  * qdrant_upsert_calls, qdrant_failed
  * elapsed_ms

## 14. 性能・並列性

* file 単位でワーカープール並列を許可（設定で concurrency）
* Embed はバッチ化しつつ、過負荷にならないよう最大同時呼び出し数を制限
* Qdrant upsert はバッチ点数上限を設定し、HTTPリクエストサイズを制御

推奨初期値:

* file concurrency: 2〜4
* embed batch max_inputs: 64
* qdrant upsert_batch_points: 256

## 15. テスト設計

### 15.1 単体テスト

* 設定ロードとバリデーション（必須項目、上限値）
* exclude 判定、パス正規化（Windows/Unix差）
* chunk_id 生成の安定性（同一入力で同一ID）
* バイナリ判定、サイズ上限スキップ

### 15.2 結合テスト

* ダミー coara-embed（ローカルgRPC）を立て、Embed/ResolveProfile を固定応答
* Qdrant は実コンテナ、または HTTP モック
* incremental で変更/削除/rename を含むケース
* file 単位削除→upsert が期待通りに反映されること

### 15.3 E2E（任意）

* 小規模リポジトリ（fixtures）を clone して index → query（rag_api側は別途）までの整合

## 16. 未決事項（実装時に確定）

1. active embedding_profile 切替の実現方法

* 要件として re-embed 後の「どの profile を active とみなすか」が必要になる。
* 推奨案: coara-embed に拡張RPC UpdateRepositoryActiveProfile を追加し、repositories に active_profile_id を保持する。
* 本件は coara-embed 詳細設計と合わせて確定し、proto を更新する。

2. base commit の自動取得

* 望ましい案: coara-embed へ GetLatestIndexRun(repo_id) を追加し、last indexed commit を取得して incremental の base に使う。
* 初期はローカル state_dir で代替し、将来拡張で統合する。

3. chunking の言語対応範囲と精度

* 初期は主要言語（C/C++/Go/Rust/Java/Python/TS/Vue）を優先し、未対応はフォールバックで扱う。

## 17. ソースコード構成（coara-cli）

リポジトリ: coara/cmd/coara-cli と coara/internal/ingestion

```text
cmd/coara-cli/
  main.go

internal/ingestion/
  config/
    load.go
    validate.go
  git/
    runner.go           # 外部git実行
    diff.go
    checkout.go
  exclude/
    glob.go
  chunking/
    chunker.go
    treesitter/
      parser.go
      extract.go
    fallback/
      split.go
  chunk_id/
    id.go
  embed_client/
    client.go           # gRPC client（ResolveProfile/Embed/拡張RPC）
    retry.go
  vdb_client/
    qdrant.go           # collection check/create, delete filter, upsert
    retry.go
  pipeline/
    planner.go          # mode別の計画（full/incremental/re-embed）
    worker.go
    stats.go
  runlog/
    summary.go          # JSONサマリ出力
  common/
    path.go             # "/" 正規化
    hashing.go
    ulid.go
    logging.go
```

実装順序（VibeCoding向け推奨）:

1. config/load/validate と health コマンド
2. git clone/fetch/checkout と file 列挙 + exclude
3. chunking（fallback先行でOK）と chunk_id
4. coara-embed gRPC（ResolveProfile/Embed）を接続し、ダミーEmbedでE2E
5. Qdrant client（create/delete/upsert）
6. index パイプライン（file単位削除→Embed→upsert）
7. MetaDB更新拡張RPC（Start/Finish/UpsertChunks/UpsertEmbeddingRecords）
8. incremental/re-embed の仕上げ（rename/delete含む）
