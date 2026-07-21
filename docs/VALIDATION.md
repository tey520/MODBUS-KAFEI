# v0.1.5 驗證紀錄

驗證日期：2026-07-21（Asia/Taipei）

## 自動化結果

| 驗證項目 | 結果 |
|---|---|
| Python 編譯檢查 | PASS |
| 單元／整合測試 | 38/38 PASS |
| 來源碼 Headless Smoke | PASS |
| Windows UI 視覺 Smoke | PASS |
| 新增／編輯點位共用 2／4 欄網格 | PASS |
| 地址值預設焦點與淡色邊框 | PASS |
| 點位啟停名稱與加寬開關 | PASS |
| 下拉選單與輸入欄位底色一致 | PASS |
| 表頭分隔線雙擊自動欄寬 | PASS；通訊設備、點位管理、通訊除錯 |
| 點位單選拖曳排序 | PASS；順序寫回專案 |
| 點位多選禁止拖曳 | PASS |
| 篩選畫面拖曳保留隱藏點位位置 | PASS |
| 說明視窗、放大咖啡杯、標語與版權 | PASS |
| FC01～FC04 Mock TCP 讀取與 Exception | PASS |
| 斷線退避、舊值品質、停止中斷 Socket | PASS |
| 地址模式與傳統 Reference 邊界 | PASS |
| 資料型別／占用位址／Bit Index 互鎖 | PASS |
| FC03／FC04 BOOL 嚴格判定 | PASS |
| 點位掃描跟隨設備設定 | PASS |
| 群組階層篩選、Shift 範圍選取、複製遞增 | PASS |
| 50 台設備／5,000 點位讀取區塊規劃 | PASS；50 blocks、0 errors |
| 既有 `555.kafei` 專案載入 | PASS；3 台設備、112 點位 |
| Janitza 測試 CSV 匯入 | PASS；112 新增、0 errors、0 warnings |
| PyInstaller one-file EXE 建置 | PASS |
| EXE 版本資源 | PASS；FileVersion / ProductVersion 0.1.5.0 |
| EXE 咖啡杯圖示資源擷取檢查 | PASS |

主要驗證命令：

```powershell
python -m compileall -q src run.py scripts
python -m unittest discover -s tests -t . -v
python run.py --headless-smoke
python scripts/load_smoke.py
python scripts/ui_visual_smoke.py
.\build.cmd
```

## 交付檔案

- `dist/MODBUS-KAFEI-v0.1.5.exe`
- 大小：9,462,972 bytes
- SHA-256：`A99F0435515A48F74E977956DD6EB1B059E9E8BCD25A70DF7B99B4ACAE92DDC7`
- FileDescription：磨杯咖啡 MODBUS KAFEI

## 已知驗證環境限制

本機 Windows Application Control 會阻擋新建立且未簽章的 EXE，因此無法在此受管制電腦直接執行成品的 `--headless-smoke` 與 `--ui-smoke`。這不是程式測試失敗；同一份來源碼的 Headless、UI、整合與負載測試均通過，且 EXE 已成功封裝並驗證版本資源。正式部署電腦仍應執行 EXE 啟動／關閉 Smoke，或由組織簽章與允許清單流程核准後再執行。

## 尚未完成的正式現場門檻

- 至少一種實體 Modbus TCP 設備交叉驗證。
- 72 小時耐久與記憶體成長量測。
- Windows 100%、125%、150%、200% 顯示縮放實機檢查。
- 電腦休眠恢復、網路介面斷開／恢復測試。
- 目標交付電腦的 EXE 啟動、關閉與安全軟體相容性測試。
