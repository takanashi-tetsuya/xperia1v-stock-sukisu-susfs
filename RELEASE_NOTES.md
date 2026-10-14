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
| `sensors` | PASS |
| `sus_path` | PASS |
| `wifi` | PASS |
| `zygisk` | PASS |

模組 PASS 只代表已載入 367 個模組的名稱比對及保留日誌；不等於每個硬體功能通過。ctime/sus_path 是一般 App 的有限夾具測試。AK3 為已開機 rooted Android 同版本重裝，不是 recovery 測試。管理器顯示40900、Wi-Fi開網頁、前後相機預覽/拍照與揚聲器播放，由機主在模組恢復重啟後確認正常；這些是機主回報，並非自動化品質測量。後續一般 App 實測九種感測器、十個公開介面的事件與有限響應，包含兩個接近感測介面的遠近切換；不代表校準、休眠喚醒或全部30項清單通過。Wi-Fi 與行動網路各完成兩次綁定介面的 HTTPS 204 及憑證驗證，原網路設定已恢復；不涵蓋速度、通話、SMS、IMS 或 VoWiFi。詳細範圍見 sensor/network 附錄。未測試或執行 OTA，不宣稱修復 App 登入、DRM 或達成完美隱藏。

原方案是 ReZygisk v1.0.0（515-333d423-release）與 Vector v2.2 （3080-88f8e1fa-JingMatrix-Vector）。B7 僅證明服務啟動及 32/64 位元 zygote、system_server 的相關程式庫映射，不代表所有 hook 或 App 通過；後續已實測官方 Zygisk Next 1.5.2（864-a4a72ce-release）：安裝完成、識別核心40900、雙zygote注入，PIF與Vector在兩個架構列出且issues為0；不延伸為所有App相容。官方 Shamiko 1.2.5／414 則被版本檢查拒絕，installer exit 1，runtime仍為NOT_RUN，沒有繞過檢查。原 ReZygisk 恢復結果為 PASS_REZYGISK_BASELINE_RESTORED，詳見 r3_zygisk_next_shamiko_runtime.json 的完成及清理證據。

廠商 .qtidataservices／QualifiedNetworksServiceImpl 的直接原因已由實機DEX與堆疊確認：廠商分支明確傳入null List，框架隨後解引用而拋出例外。該次開機為何觸發這個分支仍未定，廠商APK未修補；後續行動HTTPS通過不會抹去例外，也不代表VoWiFi通過。非PID1、仍名為init的abort在可用40922與r3均出現，父程序及native原因仍未確定。不稱為無害或零崩潰，詳見 r3_vendor_qns_init_diagnosis.json。

原候選從核對過的r2來源包離線解出；後續又從最終r3 full_source.tar.gz重新解出，在同主機無網路乾淨容器空out完整重編及封裝，97項檢查通過。配置、kernel.release、Module.symvers與五個編譯模組逐位元一致；Image差異只有已分別定位的1324-byte公開簽名憑證與20-byte ELF build ID，其餘bytes一致，沒有沿用舊私鑰或刷入重建產物。主機證據另見 verification/final_archive_rebuild/；未做網路重建或第三方獨立重建。來源包不隨後續真機附錄重包。boot/ZIP 是替代安裝方式，目標 boot 分區；請配套使用 bit 8 修正版 CLI。

RELEASE_SHA256SUMS 校驗本次平鋪 Release 資產；DELIVERY_SHA256SUMS.txt 對應原始凍結 dist 目錄，不是更新後 runtime manifest 的清單。遠端上傳/來源提交/CI 核對另見 RELEASE_STATUS.json。

SUSFS GPL-3.0 與 Linux GPL-2.0-only 再散布相容性仍未解決，保持 draft。
