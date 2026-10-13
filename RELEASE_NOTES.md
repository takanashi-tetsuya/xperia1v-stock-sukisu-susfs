上傳狀態：原始碼與 CI 已同步成功。草稿目前有 18 個已核對資產；Image、boot_XQ-DQ72_67.2.A.3.178_sukisu40900.img、full_source.tar.gz 尚未上傳。首次批次遇到 GitHub HTTP 408；獨立連線重試速度約 10–30 KB/s，已停止額外的大檔上傳。三個完整檔案仍保留本機。下方檔案清單是完整候選的組成，不代表所有資產已在 GitHub 上可下載。原始碼重建可使用倉庫的鎖定來源與腳本。

Xperia 1 V / XQ-DQ72，Android 15，韌體 67.2.A.3.178 的 SukiSU 40900 + SUSFS 2.3 修復候選。

以官方 v4.2.0 / 40900 對應的 builtin e2912817、Manager/ksud 904c60d1、UAPI 2 為基線；回移簽名驗證與 SELinux 修復，修正 SUSFS 隱藏路徑、ctime 工具協定與 AnyKernel3 日期範圍，改善可重建性及封裝檢查。保留手動/inline hook；KPM 停用。

驗證：官方網路來源重新取得後完成空白 out 全量編譯；BTF/config/映像檢查通過；556 個 vendor 模組靜態 ABI、保護表、vermagic 檢查通過；boot/ZIP 的 header、AVB metadata 與 kernel payload 檢查通過。完整來源包已重新解壓並離線核對 source-lock。

尚未刷入、開機或執行 AK3 安裝器；不宣稱已解決 App 相容性或實機模組載入問題。這是一份草稿 Release，舊版 v0.1.0-beta.1 保留。

boot 映像與 AnyKernel3 ZIP 是替代安裝方式，目標為 boot，不是 init_boot。先閱讀 README.md 的韌體、slot、init_boot、備份與回滾條件。

完整來源：full_source.tar.gz。小型補丁/重建材料：source_changes.tar.gz。配套修正 CLI：userspace.tar.gz（解壓後 userspace/ksu_susfs）。驗證報告：verification.tar.gz；封裝詳細資料：packaging_metadata.tar.gz。個別下載資產以 RELEASE_SHA256SUMS 核對；DELIVERY_SHA256SUMS.txt 保留原始目錄結構的交付雜湊。官方 Manager APK 未修改。

SUSFS GPL-3.0 與 Linux GPL-2.0-only 的二進位再散布相容性仍未解決；本次不將草稿發布為公開二進位 Release。
