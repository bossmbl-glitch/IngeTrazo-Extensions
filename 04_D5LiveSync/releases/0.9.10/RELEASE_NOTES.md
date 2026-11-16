# IngeTrazo D5 LiveSync 0.9.10

依使用者本次指示，將已封存的 **0.9.10-test.4** 範圍整理為正式發布版本。

- 包含原 test.1／test.2 的同步判定與診斷改善、test.3 的材質識別保留修正，以及 test.4 的版本化橋接備份／還原。
- 自動保存已確認 bridge.blend＋配對快照；最近 10 版及 7 個每日點，高風險與手動點保留。
- 提供停止後手動備份、驗證、保留切換及交易式還原；不自動同步。
- **不包含 test.5 的移機功能，也不包含 test.6 的逐群組可見性修正**。這些功能留在 develop 測試。

本地 Linux／Qt 78 項測試通過，封存包 SHA-256 已核對。**Windows／實際 Blender／D5 SDK 材質繼承與還原未在此環境驗證**，發布狀態不代表新增實機通過紀錄；既有測試風險與限制仍有效。

安裝前停止同步並關閉 IngeTrazo，備份原外掛、完整 `%LOCALAPPDATA%\IngeTrazoD5Bridge\projects\`、對應 IGZ 與 D5 專案。將 ZIP 內 `ingetrazo_d5_livesync` 放入 `%APPDATA%\IngeTrazo\plugins\`。初次更新仍先用 D5 副本確認材質。

橋接備份不含 D5／IGZ 原始專案，不會找回以往已遺失的 SDK UUID；還原點限同一專案及原路徑。回復時需停止同步，還原舊外掛及同時期完整 projects／IGZ／D5 配對，不可只混用某一個 .blend。
