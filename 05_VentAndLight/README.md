# 兩點出風口與燈條

- **管理 ID：** `VENT-01`
- **來源程式標示版本：** `0.2.0`
- **本次盤點到的檔案數：** 7 個（含可能被忽略的快取）
- **主要功能：** 兩點拉設出風口、燈條及對應尺寸和材質

## 原始碼路徑（在 GitHub 專案內）

- `plugins/lime_two_point_vent.py`
- `plugins/lime_two_point_vent_assets/`
- `plugins/燈條_原始材質設定.json`

## 管理原則

- 本目錄管理此外掛的需求、版本紀錄、測試與 Bug；程式原件目前留在 `plugins/`。
- 在未核對相依與真實安裝測試前，不直接搬動 Python 程式或資產。
- 迭代時先根據上述來源路徑定位原始碼，再於獨立 Git 分支修改。
- 目前的版本為來源程式標示，**不代表已重新實機驗證**。

## 核對重點

- Python 主檔與 lime_two_point_vent_assets 必須保持可用相對位置。

## 文件指引

- `SPEC.md`：功能規格及後續變更需求。
- `KNOWN_ISSUES.md`：問題與驗收狀態。
- `CHANGELOG.md`：後續每版實際修改紀錄。
