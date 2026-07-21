# v0.1.5 功能界線與驗收邊界

## 本版範圍

1. IPv4／Hostname、TCP Port、Unit ID 與 Modbus TCP FC01～FC04。
2. 通訊設備與點位 CRUD、群組篩選、Shift 範圍選取、複製並遞增地址。
3. 設備級非同步輪詢、自動重連、Ping 可達狀態與有限容量通訊除錯紀錄。
4. BOOL、BIT、INT16、UINT16、INT32、UINT32、FLOAT32、HEX、BINARY、ASCII。
5. 0 Based／Reference 地址模式、資料型別互鎖、占用位址數與 Bit Index 防呆。
6. 連續地址合併；不跨設備、Unit ID 或功能碼，不拆斷多 Register 點位。
7. CSV 交易式匯入、錯誤報告、UTF-8 BOM 匯出。
8. `.kafei` JSON 專案原子保存、備份、自動保存與異常復原。

## 本版不處理

- 所有 Modbus 寫入功能。
- 歷史資料、趨勢、告警與報表。
- Modbus RTU、RTU over TCP、設備模擬器與測試腳本。
- XLSX 直接匯入／匯出。
- 遠端管理、帳號權限與多協議功能。

## 驗收邊界

自動化驗證涵蓋單元測試、Mock Modbus 整合測試、UI 冒煙測試、50 台設備／5,000 點位規劃負載、CSV 匯入、專案保存與 PyInstaller 建置。

正式現場驗收仍需另外完成：至少一種實體 Modbus 設備交叉驗證、目標 Windows 顯示縮放測試、休眠／網路介面復原，以及 72 小時耐久測試。未完成這些項目前，本版定位為測試交付版，不宣稱完成生產環境驗收。
