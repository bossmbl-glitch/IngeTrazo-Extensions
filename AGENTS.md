# IngeTrazo Extensions - AI Development Rules

## Development Environment
- Target application: IngeTrazo 0.6.0
- Operating system: Windows x64
- Default units: cm
- Interface language: Traditional Chinese
- Do not assume SketchUp Ruby API compatibility.
- Inspect the IngeTrazo source code and available APIs before implementation.

## Development Rules
- Do not modify code unless the user explicitly requests implementation.
- Preserve existing working functionality.
- Do not change model geometry, materials, groups, layers, or transforms unless requested.
- Keep each extension in its own directory.
- Maintain version history and CHANGELOG.md for every extension.
- Keep trial and release builds separate when applicable.
- Never claim a bug is fixed without reporting the actual tests performed.
- Avoid unrelated refactoring during bug fixes.

## Testing and Release
- Test nested groups, overlapping geometry, face generation, materials, undo/redo, and error handling where applicable.
- Distinguish automated tests from real IngeTrazo application tests.
- Do not mark a release as verified without application-level testing.
- Document known limitations and unresolved problems.
- Require user approval before publishing a formal release.

## Git Workflow
- 以下流程適用於本儲存庫內所有子專案與外掛。
- 使用者負責提出想法、需求與驗收；AI 負責程式開發及 GitHub 操作，不要求使用者自行操作 GitHub。
- 開發前先閱讀現有程式、釐清需求並說明預計修改的內容；需求不明確時先詢問使用者。
- `develop` 是持續開發與測試的主線；`main` 是經使用者驗收並批准的正式版本主線。
- 使用者要求開發時，直接在最新的 `develop` 分支修改、測試與提交，不另建 feature、fix、dev、release 或其他臨時分支。
- 開發完成後，說明修改的檔案、原因、實際測試結果、風險與限制。需要 Windows／IngeTrazo／D5 實機驗收時，提供測試包與操作步驟；不得把自動測試通過當成實機驗收完成。
- 每次執行 Git／GitHub 變更前，先說明具體動作並取得使用者確認，包括建立或切換分支、提交、推送、建立或修改 PR、合併及刪除分支。一次確認只涵蓋當次明確列出的動作，不視為後續操作的永久授權。
- 唯讀檢查及使用者已要求的本地檔案修改可先進行；Git／GitHub 變更仍依前述確認規則執行。
- 日常開發直接更新 `develop`，不以建立額外分支或 PR 作為必要步驟。
- 每次正式發布後，必須盤點 `main`、`develop` 以外的既有分支；需要保留的修改先確認內容並合併，確認沒有遺漏後刪除不再需要的分支。合併及刪除前仍須取得使用者對具體操作的確認，不得直接丟棄未合併修改。
- 實機驗收完成且使用者明確批准正式發布後，才處理合併到 `main`、正式版本標籤與 GitHub Release。更新 `develop` 不等於正式發布。
- 保留既有正式版本及回復途徑，不覆寫舊正式版本。
- 不提交密碼、存取權杖或私人憑證。

### 臨時分支可見性與正式發布清理（2026-11-17）
- GitHub 不提供同一儲存庫內「僅擁有者可見」的分支。不得宣稱遠端臨時分支是私人分支；改名、保護分支或發布草稿不會提供這種可見性隔離。
- GitHub 遠端常設分支只保留 `main`、`develop`；臨時 `dev/`、`feature/`、`fix/`、`release/` 等分支僅可存在本地。不得推送此類分支或使用 `push --all`、`push --mirror` 將其帶到遠端。
- 本地不推送只代表不經遠端分支公開，不能保證本地工作區僅使用者可存取；仍受該電腦／工作區權限管理。尚未核准公開的內容也不得透過 `develop`、`main`、PR、標籤或 Release 繞過限制發布。
- 正式發布時檢查已存在的遠端臨時分支，先核對內容是否已合併或另有可回復保存；有未保存修改時不得直接刪除。保留 `main`、`develop` 與正式版本標籤，依當次明確核准的範圍清理。
- 已推送的臨時分支不會因寫入本規則而自動隱藏或刪除；刪除遠端分支也無法收回他人已下載的副本或保證移除 PR 等引用的提交。

## Required Project Documentation

Before modifying any extension, read:

1. docs/DEVELOPMENT_WORKFLOW.md
2. docs/EXTENSIONS_INDEX.md
3. docs/FULL_EXTENSIONS_ORGANIZATION.md
4. docs/SOURCE_FILE_MAP.csv
5. The target extension's SPEC.md and KNOWN_ISSUES.md

The plugins/ directory contains the original source code.

Do not move or split existing Python modules without compatibility testing.

For IntegratedTools, read the corresponding features/ subdirectory before editing the main Python module.

Always update the relevant CHANGELOG.md after code changes.

Never mark an extension as officially released without user approval and application-level verification.

每個子專案發布正式版時，須在該專案目錄的 CHANGELOG 與該版發布資料夾記錄從上一個正式版到本版的完整累積變更，涵蓋期間所有測試版，註明最終操作方式、修正、相容性、限制及驗證結果。安裝包與發布說明須提供同一份紀錄。
