# IngeTrazo 全套件分類與長期開發索引

**版本：** 分類方案 1.0（2026-11-12）  
**來源：** `plugins.7z`，靜態核對，共 402 個檔案，包含 22 個 `.pyc` 快取。
**目標：** 一款外掛一個管理資料夾；整合工具箱內按功能再分子資料夾；原始程式仍保留在 `plugins/`。

> 注意：這份 ZIP 是 **所有外掛的管理分類與文件**，不是新的安裝包或程式重構版；目前不會搬移或修改已可運作的 Python 檔。

## 全部 23 個外掛入口（含兩個宿主修補補丁）

| 資料夾 | 編號 | 名稱 | 來源宣告版本 | 來源檔案數 | 主要來源 |
|---|---|---|---|---:|---|
| `01_ProfileBuilder` | PB-01 | 快速輪廓生成器 | 2.2.4（正式版） | 83 | `ingetrazo_profile_builder_v0_1/` |
| `02_IntegratedTools` | INT-01 | 萊姆整合工具箱 | 0.19.0 | 36 | `lime_integrated_tools_ingetrazo.py`<br>`lime_integrated_assets/` |
| `03_CabinetDoor` | DOOR-01 | 櫃門片樣式工具 | 1.25.1 | 86 | `one_degree_cabinet_door.py`<br>`櫃門片樣式工具_IngeTrazo套件/` |
| `04_D5LiveSync` | D5-01 | D5 LiveSync | 0.9.9 | 70 | `ingetrazo_d5_livesync/`<br>`livesync-validation.json`<br>`LiveSync驗證與效能.txt`<br>`移機安裝說明.txt` |
| `05_VentAndLight` | VENT-01 | 兩點出風口與燈條 | 0.2.0 | 7 | `lime_two_point_vent.py`<br>`lime_two_point_vent_assets/`<br>`燈條_原始材質設定.json` |
| `06_Rotation` | ROT-01 | 方向鍵旋轉工具 | 未標示 | 1 | `lime_directional_mirror.py` |
| `07_FrameScale` | FRAME-01 | 固定邊框縮放 | 0.6.1 | 14 | `it_frame_scale/` |
| `08_MaterialStudio` | MAT-01 | 材質工作室 | 0.5.1 | 15 | `it_material_studio/` |
| `09_LightingGenerator` | LIGHT-01 | 燈光生成器 | 1.0.2 | 49 | `it_lighting_generator/` |
| `10_ModelImport` | IMPORT-01 | 模型匯入 | 0.5.1 | 16 | `it_model_import/` |
| `11_ModelInspector` | INSPECT-01 | 模型檢查器 | 1.2.0 | 8 | `it_model_inspector/` |
| `12_MouseUV` | UV-01 | 滑鼠 UV 工具 | 0.3.0 | 10 | `it_mouse_uv/` |
| `13_Bevel` | BEVEL-01 | 倒角工具 | 1.0.2 | 3 | `ingetrazo_bevel/` |
| `14_FlattenPlan` | FLAT-01 | 平面壓平 | 1.0.0 | 3 | `it_flatten_plan/` |
| `15_FaceFill` | FACE-01 | 補面工具 | 0.1.1 | 1 | `lime_fill_faces.py` |
| `16_CleanCoplanar` | CLEAN-01 | 共平面清理 | 1.0.2 | 1 | `lime_clean_coplanar.py` |
| `17_QuickIsolate` | ISO-01 | ALT+Q 快速隔離 | 未標示 | 1 | `lime_altq_isolate.py` |
| `18_LayerTools` | LAYERS-01 | 圖層管理 | 0.1.0 | 1 | `lime_layer_tools.py` |
| `19_LayerBackground` | LAYERBG-01 | 圖層背景色 | 0.1.0 | 1 | `lime_layer_background.py` |
| `20_ScissorsSplit` | SPLIT-01 | 剪刀分割群組 | 1.2.0 | 1 | `lime_scissors_split.py` |
| `21_SoftenEdges` | SOFT-01 | 柔化邊線 | 1.0.0 | 1 | `soften_edges.py` |
| `22_FixCoplanarMove` | FIX-01 | 共平面移動補丁 | 0.1.0 | 1 | `BUG_FIX_Lime_Coplanar_Move/` |
| `23_FixNestedPushPull` | FIX-02 | 巢狀群組推拉顯示補丁 | 0.1.0 | 1 | `BUG_FIX_Lime_Nested_PushPull/` |

## 整合工具箱內 17 個已登記功能分區

見 [`02_IntegratedTools/FEATURE_INDEX.md`](../02_IntegratedTools/FEATURE_INDEX.md)。

## 檔案映射

PB-01 已於 2026-11-12 更新至使用者驗收的 v2.2.4；該列檔案數為目前來源（排除快取）。其餘套件與 `SOURCE_FILE_MAP.csv` 仍為初次盤點快照。新版來源與正式包見 `01_ProfileBuilder/SOURCE_REFERENCE.md`。

全部 402 個來源檔案都已一對一映射至上面 23 個外掛管理目錄；請查看 `SOURCE_FILE_MAP.csv`。
其中 `.pyc` 22 個標記 `ignore`，已上傳 GitHub 的 `plugins/` 目錄若使用 `.gitignore`，不應包含快取。

## 原始碼為何暫時仍在 plugins/

1. 這是已經在你私人 GitHub 備份的可追溯原始結構。
2. 多個外掛會以相對路徑載入圖片、IGZ 模型及 JSON。直接拆檔會導致執行時找不到相依資源。
3. 在沒有執行 IngeTrazo 0.6.0 實機測試之前，不把「資料夾分類完成」寫成「程式重構完成」。
4. 將來若要求真正搬移程式碼，需另建立 `develop` 上的開發分支、安裝重建流程及測試。

## 既有 02_CabinetBuilder 資料夾的處理

你先前建立的 `02_CabinetBuilder/README.md` **不要現在刪除**。
新目錄 `02_IntegratedTools/features/02_CabinetBuilder/` 代表整合工具箱裡面的參數櫃體功能。
建議等你核對文件後，將舊 README 的內容合併至新目錄並建立 Git 變更紀錄，之後再刪除舊資料夾。

## 額外保護

- `main`：目前保存已上傳的原始備份，**不等於全部已實機驗收的正式版**。
- `develop`：整理文件及日常測試；修改程式建議從 develop 另開功能分支。
- `AGENTS.md`：根目錄全域開發規範，保留原有內容不要覆蓋。
- 本包沒有 `AGENTS.md`、`.gitignore`、`docs/EXTENSIONS_INDEX.md`，不會覆蓋你的舊文件。

