import os
import io
import time
import random
import streamlit as st
import streamlit.components.v1 as components
from PIL import Image
from dotenv import load_dotenv
from google import genai
from google.genai import types

# 載入環境變數 (.env)
load_dotenv()

# 讀取多組 API Key（支援以逗號分隔的多帳號備援）
raw_keys = os.getenv("GEMINI_API_KEYS") or os.getenv("GEMINI_API_KEY") or ""
API_KEYS = [
    k.strip() for k in raw_keys.split(",")
    if k.strip() and k.strip() != "你的API_Key貼在這裡"
]
STORE_NAME = os.getenv("STORE_NAME", "").strip()

# 嚴格鎖定高速 gemini-3.8-flash 為第一順位主力，4-hydrogen 僅作為最後備援
MODELS_TO_TRY = ["gemini-3.8-flash", "gemini-4-hydrogen"]

# 網頁基本設定
st.set_page_config(
    page_title="消費品漂綠快篩助手｜服飾專版",
    page_icon="🌿",
    layout="wide"
)

# 鎖定預覽縮圖高度與緊湊間距，確保標題、描述、輸入框與按鈕全都在同一畫面內完整顯示
st.markdown("""
<style>
.block-container {
    padding-top: 2rem !important;
    padding-bottom: 2rem !important;
}
[data-testid="stImage"] img {
    max-height: 160px !important;
    width: auto !important;
    max-width: 100% !important;
    object-fit: contain !important;
    border-radius: 8px;
    border: 1px solid #dcdcdc;
    padding: 3px;
    background-color: #fafafa;
}
</style>
""", unsafe_allow_html=True)

# 左側邊欄：僅保留乾淨的系統判讀架構說明
with st.sidebar:
    st.header("🌿 系統判讀架構說明")
    st.markdown("""
    本系統針對**服飾類消費品**進行「宣傳 vs. 成分」交叉查核，核心依據三大知識庫模組：
    1. **國際紡織認證白名單**（GOTS、GRS、OCS、OEKO-TEX、bluesign）
    2. **混紡回收難度拆解**（單一純質 / 同質混紡 / 異質與彈性纖維死角）
    3. **國際漂綠七宗罪分類**（無證據宣稱、隱藏權衡、模糊不清等）
    """)

# 主畫面頂部標題與說明
st.title("🌿 消費品漂綠快篩助手（服飾專版）")
st.caption("輸入品牌宣傳文案並上傳衣物標籤或商品圖片，AI 自動交叉查核永續真偽")

# 左文字、右傳圖並排雙欄設計
col_text, col_img = st.columns([1, 1.35])

with col_text:
    st.subheader("📝 步驟一：宣傳文案（選填）")
    claim_text = st.text_area(
        "請貼上商品頁面描述、環保宣言或吊牌上的宣傳文字（無宣傳文字可直接跳過）：",
        placeholder="例如：本款採用 Eco-Conscious 環境友善純淨棉料製成，愛地球永續穿搭首選...",
        height=135
    )

with col_img:
    st.subheader("📸 步驟二：上傳水洗標與宣傳圖片（可多張）")
    uploaded_files = st.file_uploader(
        "可同時上傳「成分水洗標（必填）」、「環保吊牌」、「實體海報」或「網購商品頁截圖」",
        type=["jpg", "jpeg", "png", "webp"],
        accept_multiple_files=True,
        key="unified_uploader"
    )

    processed_images = []
    if uploaded_files:
        cols = st.columns(4)
        for i, file in enumerate(uploaded_files):
            raw_img = Image.open(file).convert("RGB")
            raw_img.thumbnail((800, 800))
            buf = io.BytesIO()
            raw_img.save(buf, format="JPEG", quality=85)
            buf.seek(0)
            jpeg_img = Image.open(buf)
            jpeg_img.load()

            processed_images.append(jpeg_img)
            with cols[i % 4]:
                st.image(jpeg_img, caption=f"圖片 {i + 1}")

st.divider()

# 雙操作按鈕區
btn_col1, btn_col2 = st.columns([2, 1])
with btn_col1:
    run_real_ai = st.button("🔍 開始 AI 漂綠快篩分析", type="primary", use_container_width=True)
with btn_col2:
    run_mock_demo = st.button("🎨 載入模擬展示版面（不耗額度）", use_container_width=True)

# 底部說明列
st.caption("💡 使用說明：上傳圖片僅於記憶體中進行即時 AI 判讀，不會儲存於本機硬碟；若未提供外部宣傳文案或吊牌，系統將自動針對水洗標材質與國際認證進行單獨體檢。")

# 內建核心系統指令（加入「動態思考效率原則」，讓 AI 依難度在上限內彈性提前結束思考）
SYSTEM_PROMPT = """
你是一位專業的「服飾消費品漂綠（Greenwashing）審查員」。
使用者會一次上傳一張或多張圖片（可能包含【衣服成分水洗標】、【品牌環保吊牌】、【廣告海報】或【電商商品頁面截圖】），並可能附上【文字宣傳文案】。

【思考效率原則（極重要）】：
系統為你配置的思考額度（Thinking Budget）僅為「最高上限」，並非強制用滿。
- 若使用者僅上傳單張清晰的水洗標且無外部宣傳文案，只需快速確認材質、產地與洗標文字是否矛盾，即可**立即結束思考並開始輸出報告**，切勿在背景重複推演。
- 僅在遇到「多張圖片交叉比對」、「複雜多種纖維混紡」或「宣傳文案與實際材質存在隱晦矛盾」時，才動用較多思考額度進行深度核對。

【你的首要任務】：
請先自動辨識並歸類每一張上傳圖片與文字的內容：
- 找出所有「品牌宣傳資訊」：吊牌標語、宣傳海報、電商賣場截圖、廣告文案，以及使用者輸入的文字宣傳。
- 找出所有「實際成分資訊」：縫在衣物內側的成分水洗標、洗滌說明標、認證吊牌背面成分表。
接著將「品牌宣傳資訊」與「實際成分資訊」進行嚴格交叉比對（若使用者僅上傳水洗標而無任何外部宣傳，則進行單一水洗標材質與永續認證體檢）。

【排版與格式鐵則】：
1. 每個主標題必須使用 `### ` 開頭並獨立成行，標題下方必須空一行再寫內文或表格。
2. Markdown 表格前面必須有完整空行，絕對禁止把表格直接接在標題冒號後面，也禁止使用 `<br>` 等任何 HTML 標籤（多項資訊請用全形分號「；」隔開）。
3. 表格的第一列欄位標題必須簡潔乾淨，嚴格使用：`品牌宣傳宣稱`、`水洗標實際成分與產地`、`辨識到的第三方認證標章` 這三個名稱（不要在欄位標題裡加括號提示語）。
4. 內容必須充實具體，第 3、4、5 大項請使用條列式子項目深入展開分析。

請嚴格依照以下 5 大架構用繁體中文輸出報告：

### 1. 【風險評級】
第一行明確標示「🔴 高風險（高度漂綠疑慮）」、「🟡 中風險（資訊模糊或回收受限）」或「🟢 低風險（具備公信力認證或純質易回收）」，並用一段完整文字說明綜合判定理由。

### 2. 【宣傳 vs. 實際成分交叉對照表】
（此處空一行後輸出標準 Markdown 表格，欄位標題固定為：`| 品牌宣傳宣稱 | 水洗標實際成分與產地 | 辨識到的第三方認證標章 |`。若無提供外部宣傳，請在第一欄內容填寫「未提供外部宣傳，僅就水洗標體檢」）

### 3. 【混紡回收難度拆解】
* **回收分級判定**：明確判定屬於「第一級：可閉環循環回收（100%單一純質）」、「第二級：僅能降級回收（同屬性混紡）」或「第三級：實質不可回收死角（天然+石化異質混紡，或含 Spandex/Elastane 彈性纖維）」，並解釋纖維物理／化學回收原理。
* **附屬裝飾配件與實務回收影響**：檢視標籤是否註明「不含附屬品及裝飾配件」或特殊洗滌限制，分析鈕扣、車縫線、印花塗層或鬆緊帶對實際進廠回收純度的干擾。

### 4. 【漂綠七宗罪與環境足跡查核】
* **漂綠行為／傳統製程資源足跡**：若有提供宣傳（包含吊牌、電商截圖或文字）則逐條檢視是否觸犯漂綠七宗罪；若僅有水洗標，則客觀深入剖析該材質在無第三方有機認證（如 GOTS、OCS）下的耗水、農藥與肥料環境足跡。
* **印染化學殘留與供應鏈透明度**：檢視是否缺乏 OEKO-TEX Standard 100 或 bluesign 等無毒化學品管理認證，並分析產地標示對運輸碳足跡與供應鏈透明度的意義。
* **水洗標細節與印刷準確度查核**：仔細檢查中英文洗滌標示、圖示與成分文字之間是否存在漏字、誤植或資訊矛盾。

### 5. 【永續替代與洗護建議】
* **延長衣物壽命的具體洗護行動**：根據洗標圖示提供水溫、洗劑選擇（無磷中性洗劑、避免柔軟精）、脫水與陰乾等減碳延壽建議。
* **未來選購與淘汰替代方案**：提供具體替代認證標章建議（如 GOTS、OCS、GRS、OEKO-TEX）及衣物汰換時的二手傳承／回收管道建議。
"""

# 情境一：按下「模擬展示版面」
if run_mock_demo:
    st.subheader("📋 快篩分析報告（模擬範例展示）")
    st.error("🔴 **高風險（高度漂綠疑慮）** — 宣傳文案與實際材質回收性嚴重不符")
    st.markdown("""
    | 品牌宣傳宣稱 | 水洗標實際成分與產地 | 辨識到的第三方認證標章 |
    | :--- | :--- | :--- |
    | 標榜「Eco-Conscious 永續系列」 | `62% 聚酯纖維、33% 棉、5% 彈性纖維`；中國製造 | 無 GOTS、GRS 或 bluesign 等任何國際認證（僅有品牌自繪樹葉圖案） |
    """)

# 情境二：按下「開始 AI 漂綠快篩分析」
elif run_real_ai:
    if not processed_images and not claim_text.strip():
        st.warning("👆 請至少上傳一張照片（水洗標或宣傳圖），或輸入一段品牌宣傳文案再開始分析！")
    elif not API_KEYS:
        st.error("❌ 尚未在 .env 檔案中填入真實的 GEMINI_API_KEYS。")
    else:
        # 依上傳圖片張數與有無宣傳文字，在 1500 tokens 上限內自動計算動態思考預算
        num_imgs = len(processed_images)
        has_text = bool(claim_text.strip())
        if num_imgs <= 1 and not has_text:
            dynamic_budget = 600   # 單張水洗標快速體檢
        elif num_imgs == 2 or (num_imgs == 1 and has_text):
            dynamic_budget = 1024  # 雙端標準交叉比對
        else:
            dynamic_budget = 1500  # 多圖＋文案複雜審查（最高上限）

        user_contents = [
            "以下是使用者提供的所有待審查資料，請先自動辨識哪些屬於「品牌宣傳資訊」、哪些屬於「實際成分水洗標資訊」，再進行完整交叉比對分析："
        ]
        for idx_img, img_obj in enumerate(processed_images, start=1):
            user_contents.extend([f"【上傳圖片 {idx_img}】", img_obj])

        if has_text:
            user_contents.append(f"【使用者輸入的品牌／賣場文字宣傳】：\n{claim_text.strip()}")

        user_contents.append("請嚴格依據系統指令與知識庫規範，產出內容充實、條理分明的完整快篩分析報告。")

        status_placeholder = st.empty()
        banner_placeholder = st.empty()
        report_placeholder = st.empty()

        # 前端 JS 即時碼表：每 0.1 秒平滑跳動，完全不干擾 Python 單線程網路傳輸
        with status_placeholder.container():
            components.html(
                """
                <div style="font-family: 'Source Sans Pro', sans-serif; padding: 14px 16px; background-color: #e8f4f8; color: #0c5460; border-radius: 8px; border: 1px solid #bee5eb; font-size: 15px; display: flex; align-items: center; box-sizing: border-box;">
                    <span style="margin-right: 10px; font-size: 18px;">⚡</span>
                    <span>AI 正在讀取圖片並依難度彈性核對邏輯...（已思考 <strong id="timer_sec">0.0</strong> 秒）</span>
                </div>
                <script>
                    const startTime = Date.now();
                    setInterval(() => {
                        const el = document.getElementById("timer_sec");
                        if (el) {
                            el.innerText = ((Date.now() - startTime) / 1000).toFixed(1);
                        }
                    }, 100);
                </script>
                """,
                height=62,
            )

        start_time = time.time()
        first_token_time = None
        full_text = ""
        used_key_index = None
        last_chunk = None
        error_logs = []

        # 隨機選取起始金鑰，分散免費額度負載
        num_keys = len(API_KEYS)
        random_offset = random.randrange(num_keys)
        key_order = [(random_offset + i) % num_keys for i in range(num_keys)]

        for model_name in MODELS_TO_TRY:
            for k_idx in key_order:
                current_key = API_KEYS[k_idx]
                display_key_num = k_idx + 1
                client = genai.Client(api_key=current_key)
                tools_config = []
                if STORE_NAME and display_key_num == 1:
                    tools_config.append(
                        types.Tool(file_search=types.FileSearch(file_search_store_names=[STORE_NAME]))
                    )

                try:
                    stream = client.models.generate_content_stream(
                        model=model_name,
                        contents=user_contents,
                        config=types.GenerateContentConfig(
                            system_instruction=SYSTEM_PROMPT,
                            temperature=0.2,
                            max_output_tokens=2800,
                            thinking_config=types.ThinkingConfig(thinking_budget=dynamic_budget),
                            tools=tools_config if tools_config else None,
                        ),
                    )
                    for chunk in stream:
                        last_chunk = chunk
                        if chunk.text:
                            if first_token_time is None:
                                first_token_time = time.time() - start_time
                            full_text += chunk.text
                            elapsed = time.time() - start_time
                            status_placeholder.info(
                                f"✍️ 正在即時生成深度分析報告...（首字回應 **{first_token_time:.1f}** 秒｜累計 **{elapsed:.1f}** 秒）"
                            )
                            clean_live = full_text.replace("<br>", "；").replace("<br/>", "；").replace("<br />", "；")
                            report_placeholder.markdown(clean_live + " ▌")

                    if full_text:
                        used_key_index = display_key_num
                        break
                except Exception as e:
                    error_logs.append(f"節點 {display_key_num} ({model_name}) 異常：{e}")
                    full_text = ""
                    first_token_time = None
                    continue

            if full_text:
                break

        total_elapsed = time.time() - start_time

        if full_text:
            status_placeholder.success(
                f"✅ 分析完成！（首字回應 **{first_token_time:.1f}** 秒｜完整報告生成共 **{total_elapsed:.1f}** 秒）"
            )

            header_preview = full_text[:150]
            if "🔴" in header_preview:
                banner_placeholder.error("🚨 **綜合快篩判定：高風險（高度漂綠疑慮）**")
            elif "🟡" in header_preview:
                banner_placeholder.warning("⚠️ **綜合快篩判定：中風險（資訊模糊或回收受限）**")
            elif "🟢" in header_preview:
                banner_placeholder.success("✅ **綜合快篩判定：低風險（具備公信力認證或純質易回收）**")

            clean_final = full_text.replace("<br>", "；").replace("<br/>", "；").replace("<br />", "；")
            report_placeholder.markdown(clean_final)

            if (
                STORE_NAME
                and used_key_index == 1
                and last_chunk
                and last_chunk.candidates
                and last_chunk.candidates[0].grounding_metadata
            ):
                chunks = last_chunk.candidates[0].grounding_metadata.grounding_chunks
                if chunks:
                    with st.expander("📚 查看本次判讀引用的雲端知識庫檔案"):
                        sources = {
                            c.retrieved_context.title
                            for c in chunks
                            if c.retrieved_context and c.retrieved_context.title
                        }
                        for src in sources:
                            st.write(f"- 📄 `{src}`")
        else:
            status_placeholder.error(f"❌ 分析連線失敗（耗時 {total_elapsed:.1f} 秒）")
            for err in error_logs:
                st.write(f"- {err}")