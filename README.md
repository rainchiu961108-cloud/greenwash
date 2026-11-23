# 服飾永續與漂綠檢驗助手 — 專案完整交接規格書與開發紀錄（README.md）

> 最後更新：2026-11-23  
> 本檔案為專案完整技術文件、操作手冊、問題與瓶頸全紀錄；AI 每次開新對話會自動讀取同目錄的 `GEMINI.md`（精簡核心規則與記憶索引），詳細背景與操作方式以此檔為準。

---

## 1. 專案目標與目前完成狀態

- **產品名稱**：**🌿 服飾永續與漂綠檢驗助手**（主程式：`app.py`）
- **核心用途**：協助消費者上傳衣物「水洗標照片」與選填的「品牌環保宣傳文案／吊牌／商品頁截圖」，由 AI 結合本地服飾永續規範知識庫進行視覺辨識與交叉查核，快速產出結構化 **5 大面向繁體中文檢驗報告**。
- **效能目標與實測表現**：
  - 目標：在**開啟 AI 思考模式**且**掛載 3 份本地知識庫**的前提下，單次完整分析總耗時控制在 **20 秒左右**。
  - 實測成果：完成快取與省資源重構後，實際測試開啟 3 份知識庫分析「100% 純棉台灣製造水洗標」，首發一次通關總耗時僅 **11.1 秒**（成功辨識材質、產地、歸類為第一級閉環回收、核對缺乏國際認證並揪出水洗標英文印刷漏字）。

---

## 2. 所有使用到的軟體、套件與服務總覽

| 軟體／工具／套件 | 類型 | 版本／位置 | 在專案中負責什麼工作 |
| :--- | :--- | :--- | :--- |
| **Python 3** | 程式語言與執行環境 | Python 3.13.15（Windows 11，指令用 `python`） | 執行主程式；直接透過內建標準庫 `zipfile`、`xml.etree.ElementTree`、`io`、`re`、`html` 解析 `.docx` 與處理資料，零額外 Word 套件依賴。 |
| **Streamlit** | 網頁前後端介面框架 | `streamlit 1.67.0`（含 `components.v1`） | 建構互動式網頁、左右雙欄工作台、分頁、側邊欄開關、手機相機輸入（`st.camera_input`）、記憶體快取（`@st.cache_data` / `@st.cache_resource`）與即時串流顯示，搭配自訂 CSS 與輕量 JS 碼表。 |
| **Google Gemini API** | AI 視覺辨識與推理核心 | Google AI Studio 免費版（Free tier） | 判讀水洗標／吊牌圖片、交叉查核知識庫並撰寫 5 大架構報告。主力模型 `gemini-3.5-flash`、備援模型 `gemini-3.8-flash`，搭配多組 API Key 自動輪替與 401 黑名單跳過；思考模式已設為 `thinking_budget=-1`（官方自動思考）。 |
| **google-genai** | 官方新版 Python SDK | `from google import genai` | 呼叫 Gemini 串流與非串流 API，透過 `types.HttpRetryOptions(attempts=1)` 關閉隱藏重試、以 `types.Part.from_bytes` 直接送出 JPEG 位元組。 |
| **Pillow (`PIL`)** | 影像處理套件 | `Image`、`ImageOps` | 在**純記憶體（`io.BytesIO`）**內自動修正手機照片 EXIF 方向、縮放至 `512×512` 並壓縮為輕量 JPEG（不寫入硬碟、單圖超 40KB 自動二次縮壓至 `448×448`）。 |
| **python-dotenv** | 環境變數管理工具 | `load_dotenv(override=True)` | 讀取本機 `.env` 內的逗號分隔 `GEMINI_API_KEYS`；同時程式碼預留 `st.secrets` 讀取機制以支援未來雲端部署。 |
| **Cloudflare Tunnel (`cloudflared`)** | 免費內網穿透／公用連結工具 | `C:\Program Files (x86)\cloudflared\cloudflared.exe` | 將本機 `http://localhost:8501` 暫時映射為公開 `https://xxxx.trycloudflare.com` 網址，讓手機與其他裝置跨網存取並支援瀏覽器相機權限。 |
| **Microsoft Word（`.docx`）** | 本地專業規範知識庫 | `kb_docs/`（3 份共約 9.2 萬字） | 存放國際紡織認證白名單、混紡回收三級分類與漂綠七宗罪規範，修改 Word 並重整網頁即自動生效。 |
| **網頁瀏覽器（Chrome 等）** | 使用者操作端 | 電腦／手機瀏覽器 | 開啟本機預覽或公用連結、上傳圖片或調用手機相機拍攝水洗標。 |
| **GitHub（規劃中）** | 程式碼與檔案託管平台 | 免費版（尚未推送） | 存放 `app.py`、`kb_docs/`、`requirements.txt` 等專案檔（注意：GitHub Pages **不能**直接執行 Python）。 |
| **Streamlit Community Cloud（規劃中）** | 免費雲端部署平台 | 免費版（尚未部署） | 串接 GitHub 自動執行 `app.py` 並提供 24 小時公開網址，透過後台 Secrets 安全存放 API Key，讓手機跨裝置免開個人電腦使用。 |

---

## 3. 檔案結構與路徑現況

```text
專案目錄（正式工作目錄：C:\Users\rainc\OneDrive\Desktop\greenwash）
├── app.py                              # 主程式：Streamlit UI、快取引擎、本地 Smart RAG、API 輪替與報告儀表板
├── GEMINI.md                           # AI 自動載入記憶／規則檔（每次開新對話自動遵守）
├── README.md                           # 本文件：完整專案規格書、操作手冊與問題紀錄
├── .env                                # 環境變數檔：存放逗號分隔的 GEMINI_API_KEYS（嚴禁上傳 GitHub）
├── kb_docs/                            # 本地知識庫資料夾（3 份 Word 檔原始合計約 92,043～92,820 字）
│   ├── 01_textile_certifications.docx  # 國際紡織認證白名單與門檻（GOTS、OCS、GRS、RCS、OEKO-TEX、bluesign 等）
│   ├── 02_blended_recycling.docx       # 混紡材質回收難度三級分類與纖維回收原理
│   └── 03_greenwashing_sins.docx       # 國際漂綠七宗罪分類與判讀基準
└── （尚未建立）
    ├── requirements.txt                # 雲端部署套件清單（streamlit, google-genai, Pillow, python-dotenv）
    ├── .gitignore                      # 版控排除清單（.env, __pycache__/, ~$*.docx）
    └── .streamlit/config.toml          # 限制前端單檔上傳大小（預計 maxUploadSize = 10 MB）
```

> **路徑提醒**：`app.py` 已改為以自身所在目錄 `BASE_DIR` 定位 `.env` 與 `kb_docs/`，無論在 `Desktop\greenwash` 或未來 Streamlit Cloud 皆能正確找到檔案（需確認執行目錄內同時放有 `.env` 與 `kb_docs/`）。

---

## 4. 詳細操作步驟

### 4.1 本機啟動網頁
打開 PowerShell 視窗執行：
```powershell
cd "C:\Users\rainc\OneDrive\Desktop\greenwash"
python -m streamlit run app.py
```
- 確認 `.env` 與 `kb_docs/` 皆位於 `C:\Users\rainc\OneDrive\Desktop\greenwash` 同層目錄內。
- 啟動後本機瀏覽器開啟：`http://localhost:8501`。
- **注意**：本機實測 `py` 指令曾出現 `No installed Python found!`，請改用 `python`（確認對應 Python 3.13.15）。

### 4.2 開啟暫時公用連結（給手機或其他裝置跨網使用）
需**同時開啟兩個 PowerShell 視窗**並保持運作：
1. **視窗 1（啟動網頁）**：照 4.1 步驟執行 `python -m streamlit run app.py`。
2. **視窗 2（開啟 Cloudflare 暫時通道）**：
   ```powershell
   cloudflared tunnel --url http://localhost:8501
   ```
3. 等待數秒，複製終端機印出的 `https://xxxx-xxxx.trycloudflare.com` 網址，傳給手機或其他裝置即可開啟使用。
- **使用須知**：
  - 這是暫時連結，電腦關機或關閉任一 PowerShell 視窗即失效，下次重開會換新網址。
  - 手機若要使用「📷 啟用相機現場拍攝」，**必須透過 `https://` 網址**（同 Wi-Fi 的 `http://192.168.x.x:8501` 會被手機瀏覽器阻擋相機權限）。

### 4.3 網頁內實際使用流程
1. **側邊欄**：可檢視 5 大檢驗項目簡介、透過滑桿開關 `開啟知識庫`（預設開啟），以及在分析多次後從「🕘 本次檢驗紀錄」點擊切換最近 5 筆結果。
2. **左欄輸入區**：
   - **步驟一**：上傳 1 張以上圖片（成分水洗標、環保吊牌、海報、商品頁截圖），或打開 `📷 啟用相機現場拍攝` 直接拍照。
   - **步驟二**：選填貼上品牌宣傳文案（未填寫則自動進行水洗標單獨體檢）。
   - 點擊 **「🔍 開始 AI 漂綠快篩分析」**。
3. **右欄結果舞台**：
   - 尚未分析時顯示「🎯 我們如何判定漂綠風險？」（紅／黃／綠燈觸發條件、評分邏輯與三大核心查核支柱）。
   - 分析中顯示累計不歸零即時碼表（可偵測右上角停止鍵）與節流串流預覽。
   - 分析完成後呈現 **3 張頂部體檢總覽卡**（風險評級／混紡回收分級／國際認證標章）＋ **5 個互動分頁**（`總評與對照`、`混紡回收`、`漂綠與足跡`、`洗護與替代`、`完整報告`）＋ **⬇️ 下載完整檢驗報告 (.md)**，並可展開查看評分邏輯與本次參考的知識庫條文。

### 4.4 未來正式部署上雲端（脫離本機電腦，24 小時免開機）
1. 補齊 `requirements.txt`、` .gitignore` 與 `.streamlit/config.toml`。
2. 建立 GitHub 儲存庫，上傳 `app.py`、`kb_docs/`、`requirements.txt`、`.gitignore`、`.streamlit/config.toml`（**嚴禁上傳 `.env`**）。
3. 登入 **Streamlit Community Cloud** 連結該 GitHub 儲存庫，於後台 `Settings → Secrets` 填入 `GEMINI_API_KEYS = "金鑰1,金鑰2,..."`。
4. 部署完成後，雲端伺服器會直接執行 Python 與本地 `.docx` 智慧檢索，任何手機或電腦連上固定網址皆可直接使用。

---

## 5. 知識庫架構與本地智慧檢索原理

- **讀取與快取**：透過 `get_kb_signature()` 計算 `kb_docs/*.docx` 的「檔名、修改時間、檔案大小」指紋，搭配 `@st.cache_data` 快取切塊結果；平時操作零磁碟重讀，修改 Word 並重整網頁自動更新。
- **關鍵字計分篩選（Local Smart RAG）**：
  - 每份檔案切為數百字區塊後，於本機端計分：
    - 每份檔案開頭前 2 塊（通常為總綱與分級定義）：`+6 分`
    - 命中內建 33 個核心領域關鍵詞（GOTS、OCS、GRS、OEKO-TEX、第一／二／三級、混紡、彈性纖維、七宗罪等）：每詞 `+2 分`
    - 命中使用者輸入的「品牌宣傳文案」中英文詞彙：每詞 **`+5 分`（最高權重）**
  - 各檔由高分到低分挑選至字數上限後，再按原文順序排好注入 `system_instruction`。
- **動態 vs. 靜態行為**：
  - **有輸入宣傳文案時**：每次會依文案關鍵詞動態拉出不同條文。
  - **僅上傳圖片、無任何文字時**：本機尚未做圖片文字辨識，每次抽取皆為同一批由「前 2 塊＋33 個核心詞」決定的通用條文。

---

## 6. 本次對話開發與改版歷程（2026-11-20 ～ 2026-11-23）

1. **階段一：效能重構與省資源優化**
   - 實作知識庫檔案指紋快取、圖片壓縮快取（移除多餘回轉 `PIL.Image`、加入 `ImageOps.exif_transpose` 自動轉正與 `optimize=True`）、`genai.Client` 連線池快取（`@st.cache_resource`）、`.env` 修改時間快取。
   - 串流改以 `list` 累積並 `0.12` 秒節流重繪；即時碼表 JS 輪詢降頻由 `100ms` 改為 `250ms` 並補上 `html.escape`；`<br>` 清洗升級為預編譯正則 `_BR_TAG_RE`；遇 `401` 立即切換下一節點免等 `0.8` 秒。
2. **階段二：介面去技術化與文案修正**
   - 移除側邊欄「系統運作與判讀標準」、主力／備援模型、推理模式、節點數、「自訂優先模型」及「全面診斷」按鈕；改為產品簡介與 `開啟知識庫` 滑桿開關，並修復 `.docx` 清單第二點圓點 `•` 跑版問題。
   - 刪除「載入模擬展示版面」按鈕；標題改為「🌿 服飾永續與漂綠檢驗助手」；分析中與完成狀態列移除模型、節點、通道與字數技術字樣。
3. **階段三：全新左右工作台與視覺化儀表板改版**
   - 套用亞麻紙感＋森林深綠自然永續主題；改為左輸入（新增選配相機拍照）＋右結果舞台，解決結果一路往下跳的問題。
   - 右欄加入清楚合乎邏輯的「🔴／🟡／🟢 風險評分標準與三大查核支柱」；完成後自動解析產出 3 張頂部 KPI 總覽卡（認證精準解析第 2 區對照表第 3 欄並過濾否定語句）、5 個互動分頁、一鍵下載 `.md` 報告，並透過 `st.session_state` 保存目前報告與最近 5 筆紀錄。
   - 實測 100% 純棉台灣製造水洗標＋3 份知識庫僅耗時 **11.1 秒**。
4. **階段四：思考模式升級為官方自動思考**
   - 考量多張圖片交叉比對時 `128` tokens 草稿可能偏緊，依使用者決定調整為 `THINKING_BUDGET = -1`（官方自動思考）並將 `MAX_OUTPUT_TOKENS` 由 `1800` 放寬至 `4096`。
5. **階段五：降低上傳負載以防伺服器拒絕（部分完成、待補齊接線）**
   - 為減少免費版 API 與雲端伺服器拒絕機率，開始縮減知識庫與圖片體積（目前前兩項修改已寫入 `app.py`，後三項因操作中斷尚未套入，詳見第 7 節）。

---

## 7. 過去遇到的問題與目前瓶頸全紀錄

### A. 已解決問題（✅）
1. **Google Cloud File Search 與圖片串流衝突**：已全面放棄雲端 File Search，改以 Python 標準庫直讀本地 `kb_docs/*.docx`。
2. **9.2 萬字知識庫塞爆免費版 API 觸發 `503`**：改為本地智慧檢索精選核心條文（早期壓至 ~8,400 字，現已進一步下調上限至 ~5,400 字）。
3. **SDK 隱藏指數重試造成畫面卡死、直接送 `PIL.Image` 變超大 PNG 逾時**：已透過 `HttpRetryOptions(attempts=1)` 強制關閉底層隱藏重試，並改用 `types.Part.from_bytes(..., mime_type="image/jpeg")` 傳送壓縮位元組。
4. **Streamlit 每次互動都重讀 9 萬字 Word、重壓縮圖片、串流過度重繪**：已透過多層快取與 `0.12s` 串流節流徹底消除本機重工。
5. **側邊欄 `.docx` 檔名清單第二點 `•` 跑版黏到第一行後方**：因 Markdown 單一 `\n` 會合併段落，已改用 `"  \n"` 強制換行解決。
6. **舊版介面由上往下一直拉長、且點選任一元件就導致報告消失**：已改為左右雙欄工作台＋5 分頁儀表板＋`st.session_state` 狀態保存。
7. **本機執行 `py` 指令報找不到 Python**：確認改用 `python`（Python 3.13.15）即可正常執行。
8. **忘記暫時公用連結開啟方式**：已確認電腦內建 `cloudflared`，並補齊雙視窗指令與手機 `https://` 相機注意事項。

### B. 部分改善／尚待收尾問題（⚠️）
1. **降低上傳與請求負載防伺服器拒絕（修改只完成一半）**：
   - **已生效**：`app.py` 已將 `KB_PER_FILE_LIMIT` 降至 `1800`（約 5,400 字），且 `compress_image()` 已降為 `quality=65` 並加入「超過 `40KB` 自動二次縮壓至 `448×448`、`quality=52`」。
   - **尚未完成接線**：`app.py` 頂部雖已定義常數 `KB_CHUNK_SIZE = 350`、`MAX_IMAGES = 4`、`MAX_CLAIM_CHARS = 1500`，但實際程式碼中 `extract_docx_chunks()` 預設參數仍為 `450`、上傳圖片尚未限制最多 4 張、文案輸入框尚未套用 `max_chars=1500`、且尚未建立 `.streamlit/config.toml`（設定 `maxUploadSize = 10` MB）。
2. **無效 API Key（原 `.env` 第 4 組報 `401 UNAUTHENTICATED`）**：
   - 程式端已實作 `invalid_key_indices` 自動黑名單跳過且立即切換不等 `0.8s`，但仍須確認 `.env` 檔案中已手動移除該組無效金鑰。

### C. 目前尚未根治的核心瓶頸（🔴）
1. **Google AI Studio 免費版尖峰時段仍可能 `503 UNAVAILABLE` 或限流拒絕**：
   - 雖已有 4 順位輪替（`gemini-3.5-flash` 串流 ×2 → 標準通道 → `gemini-3.8-flash` 串流）與縮小請求體積，但免費層在傍晚等尖峰時段仍受限於 Google 伺服器排隊池。
   - 可能解法：新增第二家免信用卡免費視覺 API 備援（如 Groq / OpenRouter），或將單一專案升級至 AI Studio Paid Tier 1（設預算上限提醒）。
2. **「純上傳圖片、無宣傳文案」時的知識庫檢索盲區**：
   - 目前僅靠固定前 2 塊＋33 個核心關鍵詞挑出約 5,400 字，若水洗標上出現冷門材質（如特定羊毛、嫘縈、尼龍細則）而使用者沒打字，該段條文可能未被抽中。
   - 建議解法：離線將 3 份共 9.2 萬字的 Word 預先整理濃縮為 **1 份高密度精華規則檔（約 4,000～6,000 字）**，兼顧 100% 規則覆蓋率、體積小且免去漏檢風險。
3. **自動思考模式（`THINKING_BUDGET = -1`）在多圖複雜比對時的耗時波動**：
   - 尚未大規模實測 2～4 張複雜圖片在 `-1` 下的平均耗時；若日後實測常超過 20 秒或容易逾時，建議改回有上限的動態區間（例如單圖 `128`／多圖或有文案 `384`）。
4. **雲端正式部署未完成**：
   - 主目錄已整併至 `C:\Users\rainc\OneDrive\Desktop\greenwash`（含 `.env`、`kb_docs/`、`requirements.txt` 與 `app.py`），正式上雲端前須確認 `.gitignore` 設定完整（排除 `.env` 等機敏檔），並推送至 GitHub ＋ Streamlit Community Cloud。

---

## 8. 下一步優先待辦（TODO）

1. **【立即可做】補完「降低上傳負載防拒絕」後半段接線**：將 `KB_CHUNK_SIZE = 350`、單次最多 `4` 張圖限制、文案上限 `1500` 字接入 `app.py`，並建立 `.streamlit/config.toml`（限制單檔上傳 `10MB`）。
2. **【部署準備】建立 `requirements.txt` 與 `.gitignore`**，整合 `kb_docs/` 並完成 GitHub ＋ Streamlit Community Cloud 免費雲端部署。
3. **【長期提質】將 3 份 9.2 萬字知識庫提煉為 1 份高密度精華規則檔**，徹底解決純圖片檢索盲區並進一步減少雲端檔案體積與 API 輸入負載。
4. **【穩定性補強】實測多圖在 `THINKING_BUDGET = -1` 的回應時間**；若尖峰仍頻繁遇 `503`，再評估接入免卡備援視覺 API 或升級付費層級。
