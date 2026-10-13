# Xperia 1 V SukiSU 40900 + SUSFS 2.3 — r2

適用 XQ-DQ72 / pdx234、原廠韌體 67.2.A.3.178，Android 15。

修復公開證據的工作站路徑與 SHA-256 連結、維護者交付輸入、測試缺檔提示及產物忽略規則。核心配置改為原廠 boot 的完整版本 `5.15.189-android13-8-00016-g51bba4309aac-ab14546557`，從空白 out 重新編譯。SukiSU 維持官方 40900 / UAPI 2。

完整交付包含 Image、直接 boot 映像、AnyKernel3 ZIP、官方 Manager APK、修正 ctime bit 8 的 CLI、兩種來源包及測試／授權資料。核對下載檔請使用 RELEASE_SHA256SUMS；DELIVERY_SHA256SUMS.txt 則對應原始 dist 目錄布局。實際 GitHub 資產完整性另見倉庫 RELEASE_STATUS.json。

boot 映像與 ZIP 是替代安裝方式，目標分區為 boot；安裝前請讀 README 的原廠 init_boot、備份及 OTA 說明。此候選尚未刷機或執行 AK3 安裝器，不保證 App 登入、DRM 或 root 隱藏效果。

本機完整編譯、來源重建與主機測試、靜態模組分析、第三方完整重建及實機結果分開記錄。GitHub CI 只驗證來源與主機測試，不編譯整顆核心。第三方完整重建、手機模組／BTF 實際載入與 VFS 併發壓測仍待驗證。

SUSFS GPL-3.0 與 Linux GPL-2.0-only 的再散布相容性仍未解決，本版維持草稿。舊版本及 Git 歷史保留；舊提交仍可能含先前工作站路徑。
