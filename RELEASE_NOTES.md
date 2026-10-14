# Xperia 1 V SukiSU 40900 + SUSFS 2.3 — r3

適用 Sony XQ-DQ72 / pdx234、67.2.A.3.178、Android 15。r3 恢復 `CONFIG_IKCONFIG_PROC=y`，修復 r2 關閉 `/proc/config.gz` 導致 Android 15 `Debug.isVmapStack` 中止 system_server 的開機循環。SukiSU 保持官方 40900 / UAPI 2；核心相對 r2 僅此配置變更。

各項結果已綁定實際產物雜湊，完整範圍與限制見 runtime_manifest.json 及 verification/runtime_r3。主機報告原 NOT_RUN 值保留，不以手機結果覆寫歷史證據。

| Test | Result |
|---|---|
| `audio` | PASS |
| `boot` | PASS |
| `camera` | PASS |
| `ctime` | PASS |
| `flash` | PASS |
| `installer` | PASS |
| `ksu` | PASS |
| `module_loading` | PASS |
| `ota` | NOT_RUN |
| `sensors` | NOT_RUN |
| `sus_path` | PASS |
| `wifi` | PASS |
| `zygisk` | PASS |

模組 PASS 只代表已載入 367 個模組的名稱比對及保留日誌；不等於每個硬體功能通過。ctime/sus_path 是一般 App 的有限夾具測試。AK3 為已開機 rooted Android 同版本重裝，不是 recovery 測試。管理器顯示40900、Wi-Fi開網頁、前後相機預覽/拍照與揚聲器播放，由機主在模組恢復重啟後確認正常；這些是機主回報，並非自動化品質測量。感測器未測。硬體與 Zygisk 結果需看相應報告，未測試或執行 OTA，不宣稱修復 App 登入、DRM 或達成完美隱藏。

此次方案是 ReZygisk v1.0.0（515-333d423-release）與 Vector v2.2 （3080-88f8e1fa-JingMatrix-Vector）。B7 僅證明服務啟動及 32/64 位元 zygote、system_server 的相關程式庫映射，不代表所有 hook 或 App 通過；這輪未測 Zygisk Next 或 Shamiko。未見 system_server 致命錯誤。

保留未解問題：早期非 PID1、仍名為 init 的程序曾 abort，具體原因未定；恢復模組後一次開機出現廠商 .qtidataservices／QualifiedNetworksServiceImpl NullPointerException。不能直接判定為核心或某模組導致，也不能稱為無害或零崩潰；行動數據與 VoWiFi 仍需另外驗證。

本次從核對過的 r2 來源包離線解出，在同主機乾淨容器空 out 重建；未做 r3 網路重建或第三方獨立重建。來源包不隨後續真機附錄重包。boot/ZIP 是替代安裝方式，目標 boot 分區；請配套使用 bit 8 修正版 CLI。

RELEASE_SHA256SUMS 校驗本次平鋪 Release 資產；DELIVERY_SHA256SUMS.txt 對應原始凍結 dist 目錄，不是更新後 runtime manifest 的清單。遠端上傳/來源提交/CI 核對另見 RELEASE_STATUS.json。

SUSFS GPL-3.0 與 Linux GPL-2.0-only 再散布相容性仍未解決，保持 draft。
