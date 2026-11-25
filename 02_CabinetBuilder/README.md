# 萊姆整合工具箱

正式版：**1.0.0**，目標 IngeTrazo 0.6.0／Windows x64，預設單位 cm。

[下載正式安裝包](https://github.com/bossmbl-glitch/IngeTrazo-Extensions/releases/tag/toolbox-v1.0.0)

整合參數櫃體、抽屜分格、門片／門組、玻璃與百葉門、窗型、面板放置、統一配件資料庫、定位、局部材質與木紋工具。本版由 rc3 封存，沒有增加新的建模功能。

關閉 IngeTrazo 後解壓，將入口 `lime_integrated_tools_ingetrazo.py` 與整個 `lime_integrated_assets` 覆蓋至 `%APPDATA%\ingetrazo\plugins\`，再重新啟動。更新前保留舊外掛及模型副本；配件資料庫不隨套件覆寫。

- [版本說明](releases/1.0.0/RELEASE_NOTES.md)
- [40 項操作驗收清單](releases/1.0.0/package/40項驗收清單.md)
- [驗證範圍與限制](releases/1.0.0/package/VALIDATION.md)
- [套件來源及重建程式](releases/1.0.0/)

Windows 原生視口、GPU 效能、顯示縮放及 PowerShell 安裝仍待實機驗收，正式發布不代表這些項目已通過。資料庫引用檢查限目前模型與本機範本；不猜測修復已破壞參數的舊模型。
