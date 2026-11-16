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
- 每次開發以最新的 `develop` 為基礎；需要隔離修改時建立臨時開發分支。
- 開發完成後，說明修改的檔案、原因、實際測試結果、風險與限制。需要 Windows／IngeTrazo／D5 實機驗收時，提供測試包與操作步驟；不得把自動測試通過當成實機驗收完成。
- 每次執行 Git／GitHub 變更前，先說明具體動作並取得使用者確認，包括建立或切換分支、提交、推送、建立或修改 PR、合併及刪除分支。一次確認只涵蓋當次明確列出的動作，不視為後續操作的永久授權。
- 唯讀檢查及使用者已要求的本地檔案修改可先進行；Git／GitHub 變更仍依前述確認規則執行。
- 完成且經使用者同意的修改，以 PR 合併回 `develop`；確認臨時分支沒有未合併內容後，取得使用者確認再清理。
- 實機驗收完成且使用者明確批准正式發布後，才處理合併到 `main`、正式版本標籤與 GitHub Release。合併到 `develop` 不等於正式發布。
- 保留既有正式版本及回復途徑，不覆寫舊正式版本。
- 不提交密碼、存取權杖或私人憑證。

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
