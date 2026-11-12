# 整合工具箱｜子功能總索引

目前主程式仍為 `plugins/lime_integrated_tools_ingetrazo.py`，資產仍為 `plugins/lime_integrated_assets/`。
**FFD、參數櫃體等子資料夾只用來獨立管理規格、Bug、測試，不代表已把 Python 程式拆成多份。**

| 子資料夾 | 目前程式內註冊 ID | 功能 |
|---|---|---|
| `features/01_FFD` | `ffd;ffd_snap` | FFD 自由變形 |
| `features/02_CabinetBuilder` | `cabinet;cabinet_edit` | 參數櫃體 |
| `features/03_Lathe` | `lathe` | Lathe 旋轉成形 |
| `features/04_FloorGenerator` | `floor` | 產生輪廓地板 |
| `features/05_CenterAlign` | `center_object;center_face` | 置中對齊 |
| `features/06_ResizeHeight` | `resize` | 調整尺寸與高度 |
| `features/07_CADHeight` | `cad_height` | CAD 圖層長高度 |
| `features/08_MaterialPaint` | `paint` | 群組上色 |
| `features/09_Clearance` | `clearance` | 物件離地高度 |
| `features/10_GlassDoor` | `glass_new;glass_edit` | 玻璃門 |
| `features/11_Louvers` | `louver_new;louver_edit` | 百葉窗 |
| `features/12_DecorativeDoor` | `decorative_new;decorative_edit` | 造型門片 |
| `features/13_AccessoryLibrary` | `profile_library` | 配件資料庫 |
| `features/14_BatchExport` | `export` | 元件批次匯出 |
| `features/15_CompositeCabinet` | `cabinet_composite` | 組合櫃 |
| `features/16_LegacyHandles` | `legacy_handles` | 舊版把手相容 |
| `features/17_Dashboard` | `home` | 整合工具箱主介面 |
