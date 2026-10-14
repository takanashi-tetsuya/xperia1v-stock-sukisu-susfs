# Xperia 1 V SukiSU 40900 + SUSFS 2.3 — r3

適用 Sony XQ-DQ72 / pdx234、67.2.A.3.178、Android 15。r3 恢復 `CONFIG_IKCONFIG_PROC=y`，修復 r2 關閉 `/proc/config.gz` 導致 Android 15 `Debug.isVmapStack` 中止 system_server 的開機循環。SukiSU 保持官方 40900 / UAPI 2；核心相對 r2 僅此配置變更。

各項結果已綁定實際產物雜湊，完整範圍與限制見 runtime_manifest.json 及 verification/runtime_r3；頂層manifest的runtime_test_details逐項列出程序、限制、證據等級與報告雜湊，runtime_qualification及unresolved_runtime_findings直接列出整體限制與未解故障。主機報告原 NOT_RUN 值保留，不以手機結果覆寫歷史證據。

證據是維護者持有的私人原始紀錄與機主回報；公開摘要及雜湊可指認紀錄，但讀者沒有原始檔便無法獨立核驗其中觀察。流程內由另一位審查者唯讀覆核，不等於外部第三方重現。原廠／r3已完成單機A1/B1/B2/A2對照，每組兩輪，並驗證恢復原r3/ReZygisk；實際結果直接引用r3_stock_ab_runtime.json如下，不預設發生次數或因果。有限樣本仍有因果限制，PASS不代表整體無故障。

| Phase | Comparison role | init SIGABRT count | IWLAN NPE count | Observed uptime (s) |
|---|---|---|---|---|
| `a1_r3_off` | Matched modules-disabled group | 2 | 0 | 62.13–300.93 |
| `b1_stock` | Matched modules-disabled group | 2 | 0 | 14.23–301.64 |
| `b2_stock` | Matched modules-disabled group | 2 | 0 | 11.25–300.92 |
| `a2_r3_off` | Matched modules-disabled group | 2 | 0 | 11.55–301.54 |
| `restore_r3` | Separate return to original enabled modules | 1 | 2 | 11.79–300.97 |

init [INIT_CLASS_REPRODUCED_ON_STOCK_CAUSE_UNRESOLVED]: The same broad class of init-named SIGABRT was also recorded on stock. The custom kernel is therefore not necessary for this observed class. This does not establish identical causes, harmlessness, frequency equivalence, or that additional r3 events are unrelated to the custom kernel/root stack.

iwlan [IWLAN_REPRODUCED_AFTER_MODULE_RESTORATION_TRIGGER_UNRESOLVED]: The four matched module-isolated/stock windows had no positive IWLAN NPE reproduction, but the separate fifth boot on the same r3 kernel with the original modules restored recorded 2 IWLAN NPE events. This is a positive restoration-phase reproduction, not a matched third r3 replicate. It does not identify a particular module or establish module causation: startup, carrier/modem state, timing and other uncontrolled conditions differ. A later replacement QNS service was observed with 2 providers, 2 processed responses and 7 network-type updates; the same service/providers were present at the window endpoint, whose ring dump had no response/update entries. This bounded later activity means startup restart/backoff messages alone do not establish a continuing outage; it is not an IMS, VoWiFi, calling or long-term functionality test.

恢復PASS只核對記錄的原檔案、flags、分區、設定與映射，不代表恢復後沒有崩潰。restore_r3是恢復原有啟用模組的獨立一輪，不屬於前四輪停用模組的配對組別；該輪故障仍完整保留在結果中。

對照實驗限制：

- Only two matched boots per group on one phone; no statistical rate or frequency-equivalence conclusion.
- The stock baseline changes only to the exact same-firmware stock boot image on this previously root-modified phone with existing userdata. It is not a factory-clean phone; no factory reset or proof of pristine vendor/system/userdata is provided. Installed APK consistency, when verified, is not independent Sony-firmware authenticity verification.
- A1 has an 11.63-to-62.13-second startup collection interruption; the readiness retry was added for later phases. Instrumentation is not claimed identical across every boot.
- A2 additionally has a root-only early-kernel logging observer, unlike stock. This read-only supplement can add observer load; its success is not required for this report. Comparison counts use common crash records only, and absence in dmesg is not used to infer absence of a fault.
- ADB startup and finite retained ring buffers do not prove capture from process start; zero recorded events is not proof of absence.
- Manual unlock timing, carrier/modem/SIM state and background scheduling were not fully controlled.
- Wall-clock correction appears in device logs. Counts deduplicate timestamp/PID/TID anchors within each phase only; wall-clock fields are removed from public excerpts and do not establish boot-relative event timing.
- Stock reproduction establishes only that the custom kernel is not necessary for the broad observed class, not that all aborts have the same cause, are harmless or that excess r3 events are kernel-unrelated.
- The prior IWLAN null-list stack identifies a userspace failure site, not its upstream trigger. Negative A/B windows cannot exclude the kernel or module stack.
- The fifth boot with original modules restored is a separate observation, not a third matched r3 replicate. Any IWLAN NPE there is a positive reproduction that must accompany the four-window result; it does not isolate a module or prove causation. Restoration PASS describes restored state, not absence of crashes.
- After-window bugreports are private supplementary captures, not extra matched observation windows. dumpstate access must not be confused with direct-shell permissions.
- The A2 bugreport was collected later after a user interruption and same-boot confirmation; the first three were collected soon after their windows. Post-window timing is therefore not identical. The comparison counts remain those from the completed common crash captures, including retained startup events; the delayed bugreport is not used to extend or recalculate the matched counts.
- No call, SMS, IMS, VoWiFi, roaming, throughput, OTA or long-term stability validation is claimed.

| Test | Result | Bounded procedure | Not established |
|---|---|---|---|
| `audio` | PASS | Device owner confirmed speaker playback. | No automated audio-quality measurement, microphone, Bluetooth or all-codec coverage. |
| `boot` | PASS | Boot completion and sampled or endpoint boot/system_server continuity in retained windows. | No continuous or long-term stability proof; known IWLAN and non-PID1 init events remain unresolved. |
| `camera` | PASS | Device owner confirmed front/rear preview and photographs. | No automated image-quality, video, all-mode or sustained-load coverage. |
| `ctime` | PASS | One before/after ordinary-app F2FS fixture test observed substituted ctime seconds and nanoseconds. | Not all filesystems, timestamp consumers, concurrent workloads or applications. |
| `flash` | PASS | Candidate written to the selected active boot slot and checked against its bound image hash. | One device/firmware/slot workflow; not other models or firmware, or a general recovery guarantee. |
| `installer` | PASS | Actual rooted-Android same-version AK3 reinstall and boot partition readback. | No recovery-mode installation or real-device rejection test on unsupported firmware. |
| `ksu` | PASS | Root shell and paired SUSFS CLI worked; owner confirmed Manager displays 40900. | Not exhaustive authorization-policy, security or application-compatibility testing. |
| `module_loading` | PASS | 367 loaded module names matched stock/working references in the retained snapshots. | Stock direct-shell dmesg unavailable in the historical comparison; r3 comparison window starts at about six seconds. Does not prove all 556 inventoried files load or every associated hardware function works. |
| `ota` | NOT_RUN | Deliberately not executed; no firmware update was attempted in this session. | Incremental OTA, post-OTA boot, rollback/update/rebuild workflow remain unverified. |
| `sensors` | PASS | Nine public sensor types/ten interfaces delivered events; dedicated proximity run observed near/far. | Not calibration, suspend wakeup, all 30 inventory entries or long-term stability. Candidate identity comes from the operator session; physical-run v3 APK hash was not retained. |
| `sus_path` | PASS | Disposable F2FS fixtures exercised ordinary-app lookup and mutation, including bounded parallel reads. | One before/after run; not all paths/filesystems, global hiding or sustained race testing. The listing probe did not separately retain final readdir errno. |
| `wifi` | PASS | Owner web browsing plus two interface-bound HTTPS 204/TLS checks and later restoration checks. | Not throughput, roaming, all applications or long-term Wi-Fi reliability. |
| `zygisk` | PASS | ReZygisk and separately Next startup/library injection were observed; original ReZygisk restored. | Not every hook/application; Next observation is bounded to about five minutes. Official Shamiko installer rejected 40900; Shamiko runtime remains NOT_RUN. |

模組 PASS 只代表已載入 367 個模組的名稱比對及保留日誌；不等於每個硬體功能通過。ctime/sus_path 是一般 App 的有限夾具測試。AK3 為已開機 rooted Android 同版本重裝，不是 recovery 測試。管理器顯示40900、Wi-Fi開網頁、前後相機預覽/拍照與揚聲器播放，由機主在模組恢復重啟後確認正常；這些是機主回報，並非自動化品質測量。後續一般 App 實測九種感測器、十個公開介面的事件與有限響應，包含兩個接近感測介面的遠近切換；不代表校準、休眠喚醒或全部30項清單通過。Wi-Fi 與行動網路各完成兩次綁定介面的 HTTPS 204 及憑證驗證。訂閱行動數據原先為mobile_data1=0，臨時啟用只是進行行動網路實驗的前置條件，不是修復核心、IWLAN或網路故障；原網路設定已恢復。不涵蓋速度、通話、SMS、IMS或VoWiFi。詳細範圍見 sensor/network 附錄。OTA刻意保持NOT_RUN，沒有執行韌體更新，不宣稱修復 App 登入、DRM 或達成完美隱藏。

原方案是 ReZygisk v1.0.0（515-333d423-release）與 Vector v2.2 （3080-88f8e1fa-JingMatrix-Vector）。B7 僅證明服務啟動及 32/64 位元 zygote、system_server 的相關程式庫映射，不代表所有 hook 或 App 通過；後續已實測官方 Zygisk Next 1.5.2（864-a4a72ce-release）：安裝完成、識別核心40900、雙zygote注入，PIF與Vector在兩個架構列出且issues為0；不延伸為所有App相容。官方 Shamiko 1.2.5／414 則被版本檢查拒絕，installer exit 1，runtime仍為NOT_RUN，沒有繞過檢查。較早Zygisk Next實驗的ReZygisk恢復結果為 PASS_REZYGISK_BASELINE_RESTORED，詳見 r3_zygisk_next_shamiko_runtime.json 的完成及清理證據。

廠商 .qtidataservices／QualifiedNetworksServiceImpl 的直接呼叫鏈已由實機DEX與堆疊確認：廠商分支明確傳入null List，框架隨後解引用而拋出例外。該次開機為何觸發這個分支仍未定，尚未完成完整根因隔離；實際原廠／r3發生情況見對照報告，有限樣本不能排除所有核心或模組時序／狀態影響。廠商APK未修補；後續行動HTTPS通過不會抹去例外，也不代表VoWiFi通過。非PID1、仍名為init的abort在可用40922與r3均出現，父程序及native原因仍未確定。歷史兩版均出現不能證明無害、同因或排除核心影響；不稱為零崩潰。詳見r3_vendor_qns_init_diagnosis.json、r3_vendor_failure_evidence.json及r3_stock_ab_runtime.json；公開節錄僅保留選定故障行，不是完整原始擷取。

原候選從核對過的r2來源包離線解出；後續又從最終r3 full_source.tar.gz重新解出，在同主機無網路乾淨容器空out完整重編及封裝，97項檢查通過。配置、kernel.release、Module.symvers與五個編譯模組逐位元一致；Image差異只有已分別定位的1324-byte公開簽名憑證與20-byte ELF build ID，其餘bytes一致，沒有沿用舊私鑰或刷入重建產物。主機證據另見 verification/final_archive_rebuild/；未做網路重建或第三方獨立重建。來源包不隨後續真機附錄重包。boot/ZIP 是替代安裝方式，目標 boot 分區；請配套使用 bit 8 修正版 CLI。

RELEASE_SHA256SUMS 校驗本次平鋪 Release 資產；DELIVERY_SHA256SUMS.txt 對應原始凍結 dist 目錄，不是更新後 runtime manifest 的清單。遠端上傳/來源提交/CI 核對另見 RELEASE_STATUS.json。

SUSFS GPL-3.0 與 Linux GPL-2.0-only 再散布相容性仍未解決，保持 draft。
