# IngeTrazo 已提供外掛原始碼盤點報告

盤點日期：2026-11-12  \
來源：使用者上傳 `plugins.7z`（僅靜態讀取，不執行任何外掛）  \
目標環境：Windows x64 / IngeTrazo 0.6.0  \
文件性質：原始碼與檔案結構盤點；不是實際安裝運作驗收

## 1. 確認的數量

- 壓縮檔大小：9.10 MiB；SHA256：`24e048b4f507d62190e4e0195ee881a8192088f8dd6342f0161ed81394fbfa3d`。
- 解壓內容：402 個實體檔案、25 個資料夾（依封存項目計 427 筆）。
- Python 原始檔：141 個，全部通過 AST 語法解析；**不等於程式已能啟動或功能測試通過**。
- `.igz`：47 個；`.png`：112 個；`.pyc`：22 個。
- 專案根目錄共 30 個項目，其中可確認具備 `setup()` 的外掛入口 **23 個**。
- 注意：23 個入口不代表 23 個互不相依的外掛；部分功能在大型整合腳本內。

## 2. 具備入口的 23 個模組

| ID | 外掛／模組 | 現存原始碼路徑 | 原始碼標示版本 | 功能摘要 |
|---|---|---|---|---|
| PB-01 | 快速輪廓生成器 | `ingetrazo_profile_builder_v0_1/` | 2.2.3（由模組說明與更新紀錄確認） | 剖面、掃掠、踢腳、線板、戳記、圖庫 |
| INT-01 | 整合工具集合 | `lime_integrated_tools_ingetrazo.py` | 0.19.0 | 參數櫃體、FFD、Lathe、地板、百葉、CAD 高度等 |
| DOOR-01 | 櫃門片樣式工具 | `one_degree_cabinet_door.py` | 1.25.1 | 門框、門片、把手、玻璃格子、百葉等 |
| D5-01 | D5 LiveSync | `ingetrazo_d5_livesync/` | 0.9.9 | IngeTrazo → Blender → D5 同步及診斷 |
| VENT-01 | 兩點出風口與燈條 | `lime_two_point_vent.py` | 0.2.0 | 滑鼠起終點、出風口與燈條 |
| ROT-01 | 方向鍵旋轉 | `lime_directional_mirror.py` | 未標示 | 目前原始碼內有 LimeRotation，檔名仍為 mirror |
| FRAME-01 | 固定邊框縮放 | `it_frame_scale/` | 0.6.1 | 中段伸縮及內建柔化／平滑顯示 |
| MAT-01 | 材質工作室 | `it_material_studio/` | 0.5.1 | 材質、顏色、貼圖與油漆桶 |
| LIGHT-01 | 燈具生成器 | `it_lighting_generator/` | 1.0.2 | 21 款 IGZ 燈具資產與排列 |
| IMPORT-01 | 模型匯入 | `it_model_import/` | 0.5.1 | SKP／MAX／CAD 與拖放匯入 |
| INSPECT-01 | 模型檢查器 | `it_model_inspector/` | 1.2.0 | 選取模型檢查、可選修正 |
| UV-01 | 滑鼠 UV 工具 | `it_mouse_uv/` | 0.3.0 | 滑鼠貼圖操作、UV 設定 |
| BEVEL-01 | 倒角工具 | `ingetrazo_bevel/` | 1.0.2 | 獨立網格倒角 |
| FLAT-01 | 一鍵壓平 | `it_flatten_plan/` | 1.0.0 | 選取模型壓至 Z=0 |
| FACE-01 | 補面工具 | `lime_fill_faces.py` | 0.1.1 | 選取輪廓建面 |
| CLEAN-01 | 共平面清理 | `lime_clean_coplanar.py` | 1.0.2 | 共平面及邊線整理 |
| ISO-01 | ALT+Q 快速隔離 | `lime_altq_isolate.py` | 未標示 | 暫時隔離顯示／還原 |
| LAYERS-01 | 圖層工具 | `lime_layer_tools.py` | 0.1.0 | 指定圖層、清理相關功能 |
| LAYERBG-01 | 圖層背景色 | `lime_layer_background.py` | 0.1.0 | 圖層底色顯示 |
| SPLIT-01 | 剪刀分割 | `lime_scissors_split.py` | 1.2.0 | 平面切割群組 |
| SOFT-01 | 柔化邊線 | `soften_edges.py` | 1.0.0 | 柔化／平滑工具（與 FrameScale 需比較） |
| FIX-01 | 共平面移動修正 | `BUG_FIX_Lime_Coplanar_Move/` | 0.1.0 | IngeTrazo 0.6.0 主程式行為補丁 |
| FIX-02 | 巢狀群組推拉顯示修正 | `BUG_FIX_Lime_Nested_PushPull/` | 0.1.0 | IngeTrazo 0.6.0 視埠路徑補丁 |

## 3. 沒有獨立 setup() 的重要資產與紀錄

| 路徑 | 性質 | 建議 |
|---|---|---|
| `lime_integrated_assets/` | `lime_integrated_tools_ingetrazo.py` 的圖示、JSON 資料庫與資產 | 必須與主腳本保持同一層的相對位置 |
| `lime_two_point_vent_assets/` | 出風口／燈條圖示、燈條材料與幾何 JSON | 必須與 `lime_two_point_vent.py` 保持同一層 |
| `櫃門片樣式工具_IngeTrazo套件/` | 26 個 `.igz` 參考模型、56 張 PNG、使用說明及主程式副本 | 當作來源素材與歷史套件備份；先保留，不當作第二個獨立啟動入口 |
| `livesync-validation.json` | D5 LiveSync 測試資料 | 保留，檢查是否含本機環境路徑再上傳 |
| `LiveSync驗證與效能.txt` | D5 測試記錄 | 納入 D5 模組的文件資料 |
| `移機安裝說明.txt` | D5 舊版安裝說明 | 納入 D5 文檔，標註紀錄版本 0.5.0 |
| `燈條_原始材質設定.json` | 燈條資產的材質設定 | 連結至燈條模組的來源／驗證資料 |

## 4. 重點相依關係與不應直接拆分的區域

1. **整合工具**：`lime_integrated_tools_ingetrazo.py` 標示 v0.19.0，約 4,671 行，包含 FFD、Lathe、參數櫃體、百葉、地板等多個 UI／幾何類別，並直接讀取 `lime_integrated_assets/`。不應直接改名或把功能類別切成獨立檔而不重新測試。
2. **快速輪廓生成器**：`ingetrazo_profile_builder_v0_1/` 實際內容標示 v2.2.3，且包含 `profile_storage.py`、`profile_browser.py`、`skp_profiles.py`、各代測試及更新紀錄。原資料夾名稱不等於實際版本，請暫時保持不變。
3. **櫃門片**：根目錄 `one_degree_cabinet_door.py` 和 `櫃門片樣式工具_IngeTrazo套件/one_degree_cabinet_door.py` 的 SHA256 完全相同；同份程式勿當作兩個獨立版本。素材包含 26 個 `.igz`。
4. **兩點出風口**：`lime_two_point_vent.py` 從同層的 `lime_two_point_vent_assets/lamp_stock.json` 載入燈條資料；兩者必須成組保存。
5. **D5 LiveSync**：`ingetrazo_d5_livesync/` 具 40 個 `.py` 模組；依賴外部 Blender 和官方 D5 LiveSync，這些第三方程式不在壓縮包內。
6. **模型匯入**：`it_model_import/vendor/` 含第三方來源與授權文字，匯入 GitHub 時保留原授權。
7. **柔化工具**：`soften_edges.py` 與 `it_frame_scale/soften.py`、`smooth_render.py` 有功能重疊，是否同時啟用衝突，需要實機驗證，不能只因名稱相似就刪掉其一。

## 5. 已發現的風險與待驗證事項

| 項目 | 依據 | 建議動作 |
|---|---|---|
| `Ctrl+Alt+I` 快捷鍵衝突 | `it_model_import/__init__.py` 和 `it_model_inspector/__init__.py` 都宣告同一快捷鍵 | 指定兩者不同快捷鍵並實機驗證，不宜靜默覆蓋 |
| D5 版本文件不一致 | `ingetrazo_d5_livesync/__init__.py` 是 v0.9.9；`移機安裝說明.txt` 是 v0.5.0 | 先確認真正安裝版本，再更新使用說明 |
| 快速輪廓正式／試用標籤不一致 | 現有 `CHANGELOG_v223.txt` 標示 v2.2.3「試用修正版」 | 不直接視為已驗收正式版；核對最後發布包 |
| IngeTrazo 0.6.0 相容性 | 部分模組說明仍寫 0.5.11（例如 `it_frame_scale`、櫃門片） | 在 0.6.0 重新載入與測試 |
| BUG_FIX 修改主程式行為 | `BUG_FIX_Lime_Coplanar_Move`、`BUG_FIX_Lime_Nested_PushPull` 對宿主函式施加補丁 | 隔離測試啟用順序、主版本與更新後的兼容性 |
| `.pyc` 快取 | 共 22 個 | 先保留原始封存備份；正式 Git 倉庫可排除快取，僅提交 `.py` |
| 版本判讀限制 | 部分單檔未標示 `VERSION`，部分文件為歷史紀錄 | 不推測版本，不以檔案日期替代正式版本 |

## 6. 目前可確認的測試證據與限制

- Profile Builder `驗證結果_v2.2.3.txt` **記錄** 33 個測試通過、1 個因缺少歷史 ZIP 而跳過；並明確註明尚未在使用者 Windows 桌面 OpenGL 視窗做最終驗收。這是來源文件中的測試紀錄，**不是本次重新執行的測試**。
- `it_lighting_generator/使用說明.txt` **記錄**在 IngeTrazo 0.5.11 和 0.6.0 原始碼環境各有 38 項測試；本次未重跑。
- 櫃門片工具的 `使用說明.txt` **記錄**驗證環境為 IngeTrazo 0.5.11 與離線 Qt；不能直接推定在 0.6.0 完整通過。
- 本次只完成封存完整讀取、檔案統計、Python AST 語法解析、模組入口／字串相依／版本資訊比對，**沒有執行、修改或安裝任何外掛**。

## 7. 建議的 GitHub 目錄：保留原安裝檔名

```text
IngeTrazo-Extensions/
├── AGENTS.md                       # 已有：AI 開發規則
├── README.md                       # 已有：專案首頁
├── 01_ProfileBuilder/              # 已有：功能索引及 README（可保留）
├── 02_CabinetBuilder/              # 已有：功能索引及 README（可保留）
├── plugins/                        # 建議新增：原安裝路徑鏡像，名稱不更動
│   ├── ingetrazo_profile_builder_v0_1/
│   ├── ingetrazo_d5_livesync/
│   ├── lime_integrated_tools_ingetrazo.py
│   ├── lime_integrated_assets/
│   ├── lime_two_point_vent.py
│   ├── lime_two_point_vent_assets/
│   ├── one_degree_cabinet_door.py
│   └── ... 其餘原始入口保留原名稱
├── reference_assets/              # 建議新增：26 個櫃門參考 IGZ 與圖片等
├── docs/
│   ├── EXTENSIONS_INDEX.md         # 已有：需求整理
│   └── INSTALLED_PLUGINS_AUDIT.md  # 本報告
├── tests/                          # 建議新增：共用測試及實機回歸案例
└── scripts/                        # 建議新增：安裝、版本檢查、打包工具
```

**重要：** `plugins/` 是 GitHub 中的**原始碼備份鏡像**。實際安裝到 Windows `%APPDATA%\IngeTrazo\plugins` 時，應將其中的入口檔與資料夾放到對應的外掛根目錄；不要把 GitHub 的 `plugins` 資料夾再多包一層直接貼入安裝位置。

## 8. 下一步優先順序

1. 保存 `plugins.7z` 作為「初次封存快照」並記下 SHA256；此時不要刪除、改名或移動本機已安裝程式。
2. 在 GitHub 先提交本報告與名稱對照，建立每個邏輯外掛 ID → 實際入口路徑的關聯。
3. 第二階段才上傳 `plugins/` 的原始碼與資產，排除 `.pyc`，避免上傳機器專屬設定與敏感資訊。
4. 先用 Profile Builder、Integrated Tools、Cabinet Door 和 D5 LiveSync 作為四個版本管理試點。
5. 待 Codex 雲端額度恢復，再連接 GitHub 並用 `AGENTS.md`、本報告及 `EXTENSIONS_INDEX.md` 讓它定位正確檔案。
6. 所有正式版由測試模型、Windows IngeTrazo 實機驗證與使用者確認後才建立 Release。
