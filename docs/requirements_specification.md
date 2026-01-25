# ソースコード特化RAGシステム 要件定義書
ファイル: docs/requirements_specification.md  
版: v0.4（ドラフト）  
更新日: 2026-01-24（Asia/Tokyo）

## 1. 目的
社内のソースコード資産を対象に、根拠（ファイルパスと行範囲）を明示しながら検索・要約・質疑応答を提供するオンプレミスRAGシステムを構築する。IDE、Web、チャット（Mattermost等）から共通的に利用できることを目的とする。

## 2. 背景
- インターネット非接続（運用時）の環境で、社内コードと関連設定を対象にナレッジ化したい
- 回答は根拠提示が必須であり、監査・証跡が必要
- Embeddingモデル切り替え（再埋め込み含む）を運用で実現したい
- チャット連携のため、MCPサーバを実装し、HTTPベースのSSE（Server-Sent Events）に対応したい
- システム複雑化を避けるため、キューや常駐Workerは採用せず、取り込みとインデックス登録はCLIの一連処理として実装する
- Embeddingは Ingestion と MCP の双方から利用するため独立サービスとして構築する

## 3. 適用範囲

### 3.1 対象データ
- Gitリポジトリ（mono/multi、サブモジュール含む）
- ソースコード（C/C++、Rust、Go、Java、TypeScript/JavaScript、Python、SQL、YAML/JSON、Shell 等）
- 周辺ファイル（CMake、Gradle/Maven設定、CI定義、Dockerfile、Kubernetesマニフェスト、各種設定ファイル）

### 3.2 対象機能
- リポジトリ取り込み、差分検知、解析・チャンク化、Embedding生成、VectorDB登録、MetaDB更新（CLI）
- 根拠スパン付き検索・回答（RAG API）
- Webフロントエンド（検索、根拠表示、履歴、参照情報表示）
- MCPサーバ（HTTP + SSE）によるIDE/チャット連携
- Mattermost Bot/App連携（問い合わせ受付、回答投稿）
- 監査ログ、運用監視、バックアップ
- 再埋め込み（Re-embed）によるモデル切替運用（CLI）

### 3.3 対象外（初期）
- バイナリ解析、逆コンパイル、OCR
- インターネット検索を前提とする外部情報統合
- ソースコードの自動改変・自動コミット（提案生成は可、適用は人が実施）
- Webやチャットからのインデックス実行要求（取り込みはCLI運用）
- Queue/Job Bus、常駐Indexer Worker（本要件では採用しない）

## 4. 用語
- コレクション: VectorDB上の論理区分（embedding_profile単位で分離）
- チャンク: 検索単位の分割片（関数、クラス、設定ブロック等）
- スパン（根拠スパン）: file_path + start_line + end_line で示す根拠範囲
- embedding_profile: モデル、前処理、チャンクポリシー等を束ねた運用上のプロファイル
- Re-embed: 既存チャンクを新しいembedding_profileで再埋め込みし、新コレクションを構築すること
- MCP: 統合用ゲートウェイ。クライアント（IDE、Mattermost等）に対してツール呼び出しを提供する
- インデックス（本書）: チャンク化＋埋め込み生成＋VectorDB登録＋MetaDB更新までの一連の処理

## 5. 前提・制約
- 完全オンプレミス、運用時はインターネット非接続
- モデル・依存パッケージはオフライン配布（社内ミラー、NAS等）で更新可能
- 機密コードの外部送信は禁止
- 監査・証跡が必要（誰が、いつ、何を問い合わせ、どの根拠を返したか）
- 利用者規模は数十〜100人程度、同時利用は小〜中規模を想定
- IngestionはCLI（バッチ）として提供し、常駐サーバやHTTP APIは要求しない
- 取り込みとインデックス登録は、常駐WorkerではなくCLIの一連処理として完結させる
- Embeddingは独立サービスとして提供し、Ingestion CLI と MCP/RAG API から利用できる
- 再埋め込みは運用者のCLI実行により行う

## 6. システム概要

### 6.1 論理コンポーネント
- Ingestion+Indexing CLI（単一コンポーネント）
  - Git取り込み、差分検知、解析・チャンク化
  - Embedding Service呼び出し（バッチ）
  - VectorDB upsert（登録/更新）
  - MetaDB更新（Repository/IndexVersion/Chunk/EmbeddingRecord等）
  - Re-embed実行（新profileで再埋め込みし新コレクション構築）
- Embedding Service（独立サービス）
  - 埋め込み生成API、モデル管理、モデル切替
- Query/Retriever Service（RAG API）
  - 検索（ハイブリッド）、再ランキング（任意）、回答生成、根拠提示
  - Query埋め込み生成時にEmbedding Serviceを利用
- VectorDB
  - embedding_profile単位のコレクションを保持
- MetaDB
  - リポジトリ、インデックス状態、チャンクメタ、監査ログ、profile/ルーティング情報等を保持
- Web Frontend（RAGポータル）
  - 検索UI、根拠表示、履歴、参照情報表示
- MCP Server（Integration Gateway）
  - IDE/チャット連携用ゲートウェイ
  - HTTPベースSSE対応（必須）
- Mattermost Connector（Bot/App）
  - MCP経由でRAGを利用し、回答を投稿する

### 6.2 代表的データフロー
- 取り込み系（バッチ、単一CLIで完結）
  - Ingestion+Indexing CLI → Embedding Service →（戻り値）→ Ingestion+Indexing CLI → VectorDB/MetaDB
- 問い合わせ系
  - Web Frontend → RAG API →（Embedding Service）→ VectorDB → RAG API → Web Frontend
  - IDE/Mattermost → MCP（SSE）→ RAG API → MCP（SSE）→ IDE/Mattermost

## 7. 機能要件

### 7.1 Ingestion+Indexing CLI 要件（FR-CLI）
- FR-CLI-001 リポジトリ設定の入力
  - 対象リポジトリ（URL/パス、ブランチ、認証参照、除外ルール等）をCLIが参照できる形式で管理できる
- FR-CLI-002 初回フルインデックス実行
  - 指定ブランチの全ファイルを取得し、解析・チャンク化・埋め込み生成・登録まで一連で実行する
- FR-CLI-003 差分インデックス実行
  - コミット差分を検知し、差分のみ再処理して一連の登録まで実行できる
- FR-CLI-004 除外ルール
  - パス、拡張子、サイズ、生成物ディレクトリを除外設定できる
- FR-CLI-005 チャンクの安定ID
  - chunk_idは安定生成（同一内容は同一ID）できる
  - 推奨キー: repo_id, commit_id, file_path, start_line, end_line, content_hash
- FR-CLI-006 Embedding Service連携（バッチ）
  - チャンク本文（または参照）をEmbedding Serviceへバッチ送信し、ベクトルを取得できる
- FR-CLI-007 VectorDB登録
  - 取得したベクトルをVectorDBへupsertできる
- FR-CLI-008 MetaDB更新
  - Repository/IndexVersion/Chunk/EmbeddingRecord等の更新を行い、追跡可能とする
- FR-CLI-009 冪等性
  - 再実行しても重複登録を抑制できる（chunk_id + content_hashを基準）
- FR-CLI-010 再開性（最小要件）
  - 実行失敗時、再実行で復旧できる（少なくとも冪等upsertにより破綻しない）
- FR-CLI-011 実行結果の記録
  - 実行開始・終了、対象repo/commit、成功・失敗、失敗理由をMetaDBまたはログとして追跡できる
- FR-CLI-012 Re-embed実行
  - 運用者がCLIで新embedding_profileを指定し、再埋め込みして新コレクションを構築できる

注記:
- 本CLIはHTTP APIを提供しない。
- Queue/Job Bus、常駐Workerは採用しない。

### 7.2 チャンク化 要件（FR-CHK）
- FR-CHK-001 構造ベース分割
  - 関数/メソッド、クラス、設定ブロックを優先単位とする
- FR-CHK-002 フォールバック分割
  - パース不能なファイルは行数またはトークン相当で分割する
- FR-CHK-003 スパン復元
  - 検索結果から元ファイルの行範囲を確実に復元できる
- FR-CHK-004 メタデータ付与
  - file_path、language、symbol（任意）、start_line/end_line、commit_idを保持する

### 7.3 Embedding Service 要件（FR-EMB）
- FR-EMB-001 埋め込み生成API
  - 入力文字列（バッチ）を受け、ベクトル配列を返す
- FR-EMB-002 モデル列挙
  - 利用可能なmodel_idとメタ情報（次元数、最大入力長、正規化有無等）を取得できる
- FR-EMB-003 モデル切り替え
  - model_id指定で埋め込みを生成できる
  - embedding_profileによる切り替え運用を可能にする
- FR-EMB-004 結果メタ付与
  - 出力にmodel_id、model_version（またはartifact hash）、dimension、正規化方式を含める
- FR-EMB-005 エラー処理
  - バッチ入力で部分失敗を返せる（失敗インデックスと理由を返す）
- FR-EMB-006 オフライン運用
  - モデルの配置・更新がオフラインで可能（チェックサム検証を含む）

### 7.4 検索・回答（RAG API）要件（FR-RAG）
- FR-RAG-001 質問入力
  - 自然言語、コード片、エラーメッセージ、スタックトレースを受け付ける
- FR-RAG-002 フィルタ
  - repo、ブランチ、パスprefix、言語、期間、タグ等で絞り込める
- FR-RAG-003 ハイブリッド検索
  - ベクトル検索とキーワード検索（BM25等）の併用を可能にする
- FR-RAG-004 再ランキング（任意）
  - 上位候補の再ランキングを設定で有効/無効化できる
- FR-RAG-005 根拠提示
  - 回答には必ず根拠スパン（file_path + start_line + end_line）を含める
- FR-RAG-006 不確実性の扱い
  - 根拠が不足する場合は断定せず、不明または追加情報要求とする
- FR-RAG-007 出力形式
  - Markdown（既定）とJSON（連携用）を選択可能にする
- FR-RAG-008 embedding_profile整合
  - クエリ埋め込みは対象コレクションと同一embedding_profileで生成する
- FR-RAG-009 コレクション解決
  - embedding_profile_idから対象VectorDBコレクションを一意に解決できる

### 7.5 Web Frontend 要件（FR-WEB）
- FR-WEB-001 認証
  - AD/LDAP/OIDC等の認証に対応し、ユーザ識別を監査ログへ連携できる
- FR-WEB-002 検索UI
  - クエリ入力、回答表示、根拠一覧表示ができる
- FR-WEB-003 根拠ビューア
  - 根拠スパンの前後行（N行）を表示できる
- FR-WEB-004 フィルタUI
  - repo、ブランチ、パスprefix、言語等のフィルタをUIで操作できる
- FR-WEB-005 履歴
  - ユーザごとの問い合わせ履歴を参照できる（権限制約下）
- FR-WEB-006 参照情報表示（任意）
  - リポジトリ一覧、インデックス状態、利用可能なembedding_profile一覧を参照表示できる

注記: Web Frontendはインデックス実行や再埋め込み実行を行わない。

### 7.6 MCP Server 要件（FR-MCP）
- FR-MCP-001 ツール提供
  - query（検索＋回答）
  - search（検索のみ）
  - get_snippet（根拠スパン本文取得）
  - index_status（参照のみ）
  - list_repos / list_profiles（参照のみ、必要に応じて）
- FR-MCP-002 伝送方式
  - HTTPベースで動作する
  - SSE（Server-Sent Events）によるサーバ→クライアントのストリーミングに対応する（必須）
- FR-MCP-003 双方向性の補完
  - SSEは片方向のため、クライアント→サーバ要求はHTTP POST等で送信できる
- FR-MCP-004 ストリーミング応答
  - 部分回答（partial）と最終回答（final）をSSEで段階的に送信できる
  - 根拠（citations）も段階的に追加送信できる
- FR-MCP-005 セッション管理
  - session_idとrequest_idでリクエストとイベントを関連付ける
  - 再接続（Last-Event-ID等）の考慮を行い、可能な範囲で再送する
- FR-MCP-006 認証・認可
  - クライアント種別（IDE、Bot等）ごとにクレデンシャルを分離できる
  - 代理実行（on-behalf-of）情報を監査に残せる
- FR-MCP-007 監査ログ連携
  - MCP経由の全リクエストについて、監査ログに必要項目が欠落しない
- FR-MCP-008 SSEイベント仕様固定
  - SSE data(JSON)必須フィールド: type, session_id, request_id, payload
  - typeは partial/final/error/notification をサポートする
- FR-MCP-009 クライアント要求方式固定
  - クライアント→MCPはHTTP POST /mcp/request に統一する

### 7.7 Mattermost連携 要件（FR-MM）
- FR-MM-001 問い合わせ受付
  - チャンネルまたはDMでの問い合わせを受付できる
- FR-MM-002 投稿形式
  - 短い要約と根拠リンク（ファイル/行）を中心に投稿する
  - 長文は折りたたみ、または追加操作で取得できる
- FR-MM-003 権限制御
  - チャンネル/ユーザ権限に応じて参照可能repo/パスを制限する
  - 権限不足時は根拠を伏せる、または拒否する

## 8. データ要件

### 8.1 主要エンティティ（論理）
- Repository: repo_id, name, origin, default_branch, auth_ref, created_at, updated_at
- IndexVersion: repo_id, branch, commit_id, status, started_at, finished_at
- Document: doc_id, repo_id, commit_id, file_path, language, hash, size
- Chunk: chunk_id, doc_id, symbol, start_line, end_line, content_hash, created_at
- EmbeddingRecord: chunk_id, embedding_profile_id, model_id, model_version, dimension, vector_ref, created_at
- AuditLog: audit_id, user_id, client_type, time, query, filters, result_spans, embedding_profile_id, model_id, latency_ms, response_id

### 8.2 VectorDB設計方針
- コレクションはembedding_profile単位で分離する（必須）
- コレクション名はprofile_id（またはprofile_idを含む規約）で一意に決められること
- ペイロードに最低限のメタ情報を保持
  - repo_id, commit_id, file_path, start_line, end_line, language, symbol, chunk_id, content_hash

### 8.3 ルーティング（profile選択）管理
- repo単位（またはシステム既定）で利用するembedding_profileを決定できる
- 切替時は新コレクションを構築してからルーティングを切替する（ロールバック可能）

## 9. 外部インタフェース要件

### 9.1 RAG API（HTTP/JSONの例）
- POST /v1/query
  - 入力: query, filters, mode, top_k, output_format, embedding_profile_id
  - 出力: answer, citations[{file_path,start_line,end_line,score}], meta
- GET /v1/snippet
  - 入力: repo_id, commit_id, file_path, start_line, end_line, context_lines
  - 出力: snippet_text, boundaries
- GET /v1/healthz

### 9.2 Embedding Service API（HTTP/JSONの例）
- POST /v1/embed
  - 入力: model_id（またはprofile解決後のmodel_id）, inputs[], normalize（任意）
  - 出力: vectors[][], model_id, model_version, dimension, warnings（任意）
- GET /v1/models
- GET /v1/healthz

### 9.3 MCP Server（HTTP + SSE）
- GET /mcp/sse
  - SSE接続確立
  - Content-Type: text/event-stream
- POST /mcp/request
  - リクエスト送信（session_id, request_id, tool, args）
- SSEイベント（dataはJSON文字列）
  - type: partial | final | error | notification
  - session_id, request_id, payload
- keep-alive
  - 一定間隔で心拍（コメント行またはnotification）を送る

注記: Ingestion/Indexingに関するHTTP APIは要求しない。

## 10. セキュリティ要件（NFR-SEC）
- NFR-SEC-001 認証
  - Web/IDE/チャットの利用者識別を実現する（AD/LDAP/OIDC等）
- NFR-SEC-002 認可（RBAC）
  - repo/パス単位でアクセス制御し、越境参照を防止する
- NFR-SEC-003 通信保護
  - 外部公開範囲の通信はTLS必須
  - サービス間はmTLSを推奨（範囲を明記して適用）
- NFR-SEC-004 秘密情報管理
  - トークン/鍵は暗号化ストアまたは同等の保護で保管する
- NFR-SEC-005 ログ最小化
  - 監査ログにソース全文を残さず、根拠スパン中心とする
- NFR-SEC-006 チャット投稿制限
  - Mattermost投稿は短文化し、必要最小限のスニペットに制限する

## 11. 性能要件（NFR-PERF）
- NFR-PERF-001 検索応答
  - 通常検索の目標レイテンシを定義し、監視可能とする（環境に合わせて設定）
- NFR-PERF-002 差分インデックス
  - フル再作成に依存しない差分反映を可能にする
- NFR-PERF-003 SSE同時接続
  - MCPの同時接続（目安: 10〜50）に耐え、水平スケール可能な設計とする

## 12. 可用性・運用要件（NFR-OPS）
- NFR-OPS-001 再実行性
  - インデックス処理は再実行で復旧できる（冪等upsertにより破綻しない）
- NFR-OPS-002 バックアップ
  - VectorDB、MetaDB、設定、監査ログのバックアップ手順を提供する
- NFR-OPS-003 オフライン更新
  - モデル、依存パッケージの更新手順をオフラインで確立する
- NFR-OPS-004 監視
  - APIレイテンシ、失敗率、SSE接続数、Embeddingレイテンシ、CLI実行結果を監視する
- NFR-OPS-005 Ingestion/Indexing実行運用
  - CLIの実行主体（運用者）と実行方法（手動/定期）を定義し、実行ログを追跡可能とする
- NFR-OPS-006 Embedding負荷の運用制御
  - Ingestion/Indexing実行時にEmbeddingを過負荷にしない運用ルール（夜間実行、バッチサイズ、レート制限等）を定義できる

## 13. 監査・可観測性要件（NFR-AUD / NFR-OBS）
- NFR-AUD-001 監査ログ必須項目
  - user_id, client_type（Web/IDE/Mattermost）, query, filters, result_spans, embedding_profile_id, model_id, response_id, timestamp
- NFR-AUD-002 保持期間
  - 監査ログの保持期間を設定可能とする（例: 1年）
- NFR-OBS-001 メトリクス
  - model_id別のEmbeddingレイテンシ、スループット
  - SSE接続数、切断率、再接続数、イベント送信数
  - CLI実行成功率、実行時間、失敗理由の集計

## 14. テスト要件（TR）
- TR-001 単体
  - チャンク化、スパン復元、フィルタ、認可
- TR-002 結合
  - CLI（取り込み＋登録）→Embedding→検索→根拠提示のE2E
- TR-003 回帰
  - 差分インデックス反映、Re-embed切替/ロールバック
- TR-004 セキュリティ
  - repo/パス越境がWeb/IDE/チャット経路で発生しないこと
- TR-005 MCP（SSE）
  - partial/finalの送信、切断・再接続後の新規リクエスト処理

## 15. 受入基準（AC）
- AC-001 根拠提示
  - すべての回答で根拠スパン（file_path + 行範囲）が提示される
- AC-002 差分反映
  - リポジトリ更新が差分インデックスで反映される（CLI運用で実現）
- AC-003 モデル切替
  - Re-embedにより新コレクション構築、切替、ロールバックが可能
- AC-004 Web UI
  - 検索→回答→根拠表示→スニペット閲覧が一連で実行できる
- AC-005 MCP（SSE）
  - MCPクライアントがSSEでpartial/finalを受信できる
  - SSE切断後に再接続して新規リクエストが処理できる
- AC-006 Mattermost
  - Mattermostからの問い合わせで、権限に応じた回答と根拠提示が投稿される
- AC-007 監査
  - Web/IDE/Mattermostすべての経路で監査ログ項目が欠落しない
- AC-008 キュー/常駐Worker非採用
  - Queue/Job Busや常駐Workerを用いず、CLI一連処理でインデックスが構築できる

## 16. リスクと対策（要点）
- 誤回答の断定
  - 根拠必須、根拠不足時は不明または追加情報要求
- Embeddingモデル切替による精度変動
  - Re-embedで並行構築し、段階切替と即時ロールバックを可能にする
- 機密漏えい（チャット投稿）
  - 投稿は短文化し、必要最小限のスニペットに制限、権限制御を厳格化する
- SSE接続の運用負荷
  - keep-alive、接続上限、水平スケール、再送範囲を設計に含める
- Ingestion/Indexingの実行漏れ
  - 定期実行（スケジューラ）と実行ログ監視、失敗時通知手順を運用に含める
- IngestionがEmbeddingを飽和させる
  - 夜間実行、バッチサイズ調整、レート制限等の運用制御を定義する

## 17. 付録: 要件ID一覧（概要）
- FR-CLI: 取り込み＋登録（CLI一体）
- FR-CHK: チャンク化
- FR-EMB: Embedding
- FR-RAG: 検索・回答
- FR-WEB: Webフロントエンド
- FR-MCP: MCPサーバ（HTTP + SSE）
- FR-MM: Mattermost連携
- NFR-SEC: セキュリティ
- NFR-PERF: 性能
- NFR-OPS: 運用
- NFR-AUD / NFR-OBS: 監査・可観測性
- TR: テスト
- AC: 受入基準
