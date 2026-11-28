import html
import io
import os
import random
import re
import time
import xml.etree.ElementTree as ET
import zipfile
from dotenv import load_dotenv
from google import genai
from google.genai import types
from PIL import Image, ImageOps
import streamlit as st
import streamlit.components.v1 as components

# --- 核心常數設定 ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MODELS_TO_TRY = ("gemini-3.5-flash", "gemini-3.8-flash")
THINKING_BUDGET = -1  # -1 表示自動思考模式（由模型依圖文複雜度動態決定）
MAX_OUTPUT_TOKENS = 8192  # 預留自動思考與完整白話報告空間，避免最後一區被思考耗盡截斷
KB_FOLDER = os.path.join(BASE_DIR, "kb_docs")
KB_CHUNK_SIZE = 350
KB_PER_FILE_LIMIT = 1800  # 3 份合計約 5,400 字，降低免費版伺服器輸入負載與拒絕機率
IMG_MAX_SIZE = (512, 512)
IMG_FALLBACK_SIZE = (448, 448)
IMG_JPEG_QUALITY = 65
IMG_TARGET_MAX_BYTES = 40 * 1024  # 單圖上限 40KB，超過自動二次輕量化
MAX_IMAGES = 6
MAX_CLAIM_CHARS = 1500
STREAM_UPDATE_INTERVAL = 0.12  # 串流畫面節流間隔（秒），減少瀏覽器頻繁重繪
MAX_HISTORY_ITEMS = 5

WORD_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_BR_TAG_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
_EN_TERM_RE = re.compile(r"[A-Za-z0-9\-]{2,}")
_ZH_TERM_RE = re.compile(r"[\u4e00-\u9fff]{2,4}")
_SEC_HEADER_RE = re.compile(r"(?m)^###\s*([1-5])[\.、\s]")
_TIER_EXPLICIT_RE = re.compile(r"(?:屬於|判定為?|列為|歸類為?|屬)[^。\n]{0,14}(第[一二三]級)")
_TIER_ANY_RE = re.compile(r"(第[一二三]級)")
_SUSTAIN_META_SPLIT_RE = re.compile(r"\n?===+\s*SUSTAIN_DATA[\s\S]*$", re.IGNORECASE)
_SUSTAIN_META_PARTIAL_RE = re.compile(r"\n?={2,}[^\n]*$")
_SUSTAIN_META_BLOCK_RE = re.compile(
    r"===+\s*SUSTAIN_DATA\s*===+\s*([\s\S]*?)(?:===+\s*END_SUSTAIN_DATA\s*===+|$)",
    re.IGNORECASE,
)

KNOWN_CERTS = (
    "GOTS", "OCS", "GRS", "RCS", "OEKO-TEX", "bluesign",
    "Cradle to Cradle", "Fair Trade", "FSC", "RDS", "RWS", "BCI",
)

# 預先轉小寫的核心領域關鍵詞（避免檢索迴圈重複配置字串）
CORE_KEYWORDS = tuple(
    kw.lower()
    for kw in (
        "GOTS", "OCS", "GRS", "RCS", "OEKO-TEX", "bluesign", "認證", "門檻", "有機", "再生", "無毒",
        "第一級", "第二級", "第三級", "閉環", "降級", "混紡", "純棉", "聚酯", "彈性纖維", "Spandex", "Elastane",
        "七宗罪", "隱藏權衡", "無證據", "模糊不清", "無關緊要", "兩害相權", "撒謊", "假標籤", "漂綠", "永續", "碳足跡",
    )
)

BASE_SYSTEM_PROMPT = """你是專業的「服飾消費品漂綠審查員」。請先在思考階段快速核對上傳圖片與文字中的「品牌宣傳資訊」與「實際成分水洗標資訊」，並嚴格對照下方附帶的【專屬審查知識庫精選規則】進行認證門檻、混紡回收分級與漂綠七宗罪判讀（思考僅需精簡心算關鍵成分、認證與級別，切勿在思考過程重複撰寫草稿，將篇幅與時間完整保留給正式報告）。

【核心任務：把專業知識翻成消費者看得懂的白話文】：
1. **專有名詞與英文標籤必附白話解釋**：一般消費者看不懂化學名稱、紡織回收術語與認證縮寫。凡出現任何**印在標籤上的英文／驗證機構／認證專有名詞**（如 `Organic`、`Licence no`、`ETKO`、`Control Union`、`GOTS`、`OCS`、`GRS`、`OEKO-TEX`、`bluesign` 等）或**成分／化工術語**（如 `Spandex / Elastane`、`Polyester`、`機械開松`、`纖維素化學解聚`、`萊賽爾`、`閉環回收`、`降級回收`、`微塑膠`等），**第一次出現時必須用括號或簡短白話一句話說明「它是什麼意思、對消費者或環境有什麼影響」**；至於「非印在衣物上」的工業技術英文縮寫（例如 `NIR`、`Tier 1`、`Tier 2`、`Tier 3` 等），請直接寫純中文白話（如「近紅外線光學掃描分選機器」、「第一級」、「第二級」、「第三級」），不要輸出讓人誤會的英文代號！
   - 白話示範：「標籤載明等級『Organic』（代表有機天然纖維含量須達 95% 以上）、驗證機構『ETKO』（獨立第三方稽核單位，負責實地查核工廠，不是品牌自己說了算）、許可編號『Licence no 4184』（就像這件衣服可追溯查驗的有機身分證字號）」。
   - 白話示範：「不需靠昂貴的近紅外線光學掃描機器費力挑出混雜塑膠，只要切碎重新紡紗或溶解重抽就能變回新布料（即『衣到衣』閉環再生）」。
2. **緊扣本次實物細節（拒絕套版）**：每項分析必須直接引用本次圖片與文字中辨識到的確切成分百分比、產地、洗滌符號、認證字號或宣傳原句進行客製化推理；不同衣物必須產出對應的專屬分析，嚴禁套用一成不變的通用文字。
3. **各區職責獨立不重複**：風險評級負責「總評與判定原因」、交叉對照表負責「圖文宣稱比對」、混紡回收負責「纖維拆解與回收難度」、漂綠與足跡負責「七宗罪與製程環境代價」、洗護與替代負責「專屬洗護方法與選購建議」。各區深挖自身面向，不在不同區塊重複相同論點（建議總報告約 1,000～1,500 字，重點精實、白話好懂，避免冗長造成連線逾時）。
4. **「交叉對照表」動態省略與連貫編號規則**：
   - 若使用者**未提供品牌宣傳文案，且僅上傳單張純粹的成分水洗標照片（圖中無任何品牌環保、永續宣傳標語）**，請**直接省略【宣傳 vs. 實際成分交叉對照表】**，並將後續三個章節直接順編為 **`### 2. 【混紡回收難度拆解】`、`### 3. 【漂綠七宗罪與環境足跡查核】`、`### 4. 【永續替代與洗護建議】`**（維持 `1、2、3、4` 連貫編號，不可跳號）。
   - 若使用者有填寫宣傳文案、上傳多張圖進行對照，或圖片中明確包含品牌環保／永續宣稱，則完整輸出 `1～5` 五大章節。
5. **格式與用詞鐵則**：
   - 每個主標題獨立一行（`### ` 開頭），標題下方空一行再寫內容；Markdown 表格前必須空一行，禁止使用 `<br>` 等 HTML 標籤（多項資訊用全形分號「；」隔開）。
   - **全程嚴禁輸出任何簡體字**（務必使用標準台灣繁體中文，例如「誤以為」不可寫成簡體字形）；使用粗體 `**` 強調文字時，`**` 內側不可多出空白（不可寫成 `**文字 **`），若緊鄰引號請將 `**` 寫在引號裡面（例如 `「**第一級**」`）。
   - 風險評級首行用詞必須嚴格統一為以下三者之一（一字不差）：
     - `🔴 高風險（高度漂綠疑慮）`
     - `🟡 中風險（資訊模糊或回收受限）`
     - `🟢 低風險（具公信力認證或純質易回收）`
   - 若輸出交叉對照表，表格標題固定為：`| 品牌宣傳宣稱 | 水洗標實際成分與產地 | 辨識到的第三方認證標章 |`。

6. **網購無水洗標情境與日常無標章公平判讀**：
   - **網購下單前（無實體水洗標）**：若使用者僅提供賣場文字規格或商品頁截圖、沒有水洗標照片，**不要拒絕分析**，請直接依據已提供的纖維資訊評估回收度與風險，同時明確指出商家未揭露的資訊缺口（例如缺少確切百分比、未說明是否含彈性纖維、未標示產地）。
   - **日常無認證服飾公平判讀**：市面上大多數日常衣物並沒有花費高昂成本申請第三方國際認證（如 `GOTS`、`GRS`、`OEKO-TEX`），因此「沒有認證標章」**不等於漂綠**！認證屬於信任「加分項」，只要沒有誇大環保宣稱、資訊揭露誠實，或是屬於 100% 單一純質易回收材質，皆應在報告中給予公正肯定，並著重在後續洗護與延壽建議。
7. **雙軌自行判別（有無環保永續行銷語氣切換，極重要）**：
   - 請先自行判斷商家是否有拿「環保、永續、愛地球、節能減碳、有機、綠色、環境友善或自繪樹葉／循環環保標章」當作賣點：
   - **【軌道 A｜商家主動以環保／永續／節能減碳行銷】**：採用**嚴格指正語氣**，直接依知識庫逐條拆解觸犯的「漂綠七宗罪」（如模糊不清、無證據、隱藏權衡、假標籤等），引用原句直指宣稱與實物或認證之間的落差，無須委婉客套；遇網購環保賣場時，務必同步查核以下三大關鍵盲點：
     1. **認證字號與範圍查核**：若文案宣稱具備 `GRS`、`GOTS`、`OEKO-TEX`、`bluesign` 等國際認證，須檢查是否附上驗證機構與許可字號（Licence No.），並提醒消費者確認是「整件成衣認證」還是僅「上游紗線／布料／染料認證」（避免「原料有證、成衣未認證」落差）。
     2. **專利代稱 vs. 法定化學成分查核**：若賣場僅寫「環保再生纖維」或品牌專利布料名卻未標示法定纖維名稱（如聚酯纖維、尼龍）與比例，應明確點出資訊缺口；若同時宣稱「彈力」與「100% 再生纖維」，須提醒確認是否含彈性纖維（Spandex／Elastane）；若屬寶特瓶再生聚酯（rPET），須白話提醒其洗滌微塑膠與「瓶到衣」難再閉環回收的環境權衡。
     3. **絕對比較詞查核**：若使用「市售最環保、最高等級、100% 零污染」等絕對最高級詞彙卻未提供第三方檢測或生命週期比較數據，應直指其欠缺佐證。
   - **【軌道 B｜商家未拿環保永續當賣點（僅介紹舒適、透氣、彈性、版型、科技機能如 `Re-Flex™`，或純粹標示成分與洗標）】**：採用**委婉客觀的消費者補充提醒語氣**——**嚴禁**將機能名稱或一般純棉硬扣「觸犯漂綠七宗罪（如模糊不清、隱藏權衡）」，也**嚴禁**使用「隱瞞、包裝成高科技、無法保證不含甲醛／重金屬、牢度較弱」等否定或指控賣家的字眼！應明確宣告「✅ 未檢出漂綠行為（本商品未主打環保人設，不構成漂綠）」，正面肯定誠實揭露處，再以友善中立的口吻向**消費者**補充賣場未提及的產業常態與環境背景（如一般棉耗水屬產業常態、珍惜久穿最能攤平足跡、深色棉穿前先冷水過水更安心、機能名詞與環保認證不同等）；且若成分與產地誠實揭露無誇大環保宣稱，末尾 `===SUSTAIN_DATA===` 的 `誠實度` 請評為 `高`。
8. **風險評級公正校準與防誤判鐵則（極重要）**：
   - **🔴 高風險 vs 🟡 中風險紅線校準**：
     - 若商品**確實採用具公信力大廠之再生或有機原料**（例如台灣或國際大廠經 GRS 認證之再生紗線），僅因「將上游紗線認證寫成成品認證、未標示成衣許可字號、行銷用詞偏誇大（如『市售最環保』）或附自繪綠葉標示」等合規與包裝瑕疵，應判定為 **`🟡 中風險（資訊模糊或回收受限）`**，並正面肯定其原料永續投入、同時誠實指出合規落差與誇大處。
     - **`🔴 高風險` 僅保留給**：① 毫無實質環保投入或材質屬第三級不可回收混紡，卻大肆宣稱環保永續；或 ② 宣稱與實物存在明確造假矛盾。
   - **禁止「未審先判」式猜測加罪**：當賣場或原廠資料已明確說明係以「針織組織結構提供彈性」或「速乾排汗減少細菌滋生」時，**不得**憑空假設其「偷加彈性纖維」或「偷加銀離子化學抗菌劑」來拉高風險評級；僅可客觀提醒消費者下單前或收到實品後核對水洗標確認。
   - **防漏截誤判與自動過濾雜訊**：
     - 若上傳圖片包含無關之穿搭模特照、尺寸表或評價，請自動略過雜訊、專心判讀含材質／認證／宣傳之畫面；若全部圖片均無相關資訊，請友善提醒使用者改截成分或規格區。
     - 當沒看到產地、水洗標或認證證書時，請使用客觀措辭「**目前提供之畫面尚未見……**」，絕不直接武斷指控賣家刻意隱瞞；若文案有提及認證（如 GRS）但圖片未附上證書，請於第 1 區或第 4 區加註一句「📸 **防誤判補件提醒**：建議確認賣場頁面下方是否另有附證書或產地標示，補截重測結果會更精準」。

請用繁體中文依以下架構輸出報告：

### 1. 【風險評級】
第一行僅輸出標準風險標籤（`🔴 高風險（高度漂綠疑慮）`、`🟡 中風險（資訊模糊或回收受限）` 或 `🟢 低風險（具公信力認證或純質易回收）`）。
空一行後依序詳細說明判定原因（遇任何英文等級、驗證機構或專業縮寫皆須用白話解釋意思）：
- **本次實物與認證辨識**：用白話清楚列出辨識到的纖維成分與比例、產地標示（若賣場或圖片未標示請明確寫出「未揭露」），以及實際檢出的第三方認證標章（若有出現等級名稱、驗證機構代碼或許可字號，請一併翻譯成白話意義；若無則標示「未見第三方國際認證（日常服飾常見，屬信任加分項）」）。
- **三大依據判定理由**：分別針對「① 宣傳與材質一致性（含資訊透明度，無環保宣稱時切勿指控漂綠）」、「② 國際認證加分門檻」、「③ 混紡回收分級」逐一說明本件衣服觸發此風險等級的具體原因與知識庫判讀依據。

### 2. 【宣傳 vs. 實際成分交叉對照表】
（僅在提供品牌宣傳文案／宣傳圖／網購賣場規格，或照片內含環保宣傳標語時輸出；單純單張水洗標體檢請直接省略本區塊，並將後方章節直接順編為 2、3、4）
（空一行後輸出標準 Markdown 表格）
- **對照落差與合規性解析**：若屬軌道 A（有環保宣稱），逐句拆解宣傳用語與實際成分、認證之間的落差；若屬軌道 B（僅訴求舒適機能，無環保宣稱），則客觀說明其機能或材質描述是否與實際成分相符，不指控賣家刻意隱瞞環保認證。

### 3. 【混紡回收難度拆解】（若省略對照表，此區請編號為 `### 2.`）
- **回收分級判定與白話機制解析**：依知識庫明確判定本件屬於「第一級（100% 單一純質閉環回收）」、「第二級（同屬性混紡降級回收）」或「第三級（異質混紡或含彈性纖維回收死角）」（若網購規格未寫清比例，請說明可能的分級範圍與關鍵變數），並針對本次確切纖維組合與比例，用消費者聽得懂的白話說明回收廠實際上為什麼容易或很難把這些纖維分開再利用（若提及光學分選、化學溶解或再生纖維等術語，務必用白話解釋）。
- **附屬裝飾配件與實務回收影響**：檢視「不含附屬品及裝飾配件」標示及本件可能涉及的車縫線、織標、鈕扣、拉鍊、印花塗層或鬆緊帶對再生純度的影響，並說明汰換時應如何處理。

### 4. 【漂綠七宗罪與環境足跡查核】（若省略對照表，此區請編號為 `### 3.`）
- **漂綠行為查核／資訊透明度檢視**：
  - 若屬**軌道 A（有環保／永續／節能減碳行銷）**：直接指正！依知識庫逐條核對命中了「漂綠七宗罪」中哪幾宗罪並引用原句拆解誤導處。
  - 若屬**軌道 B（無環保／永續行銷）**：明確宣告「✅ 未檢出漂綠行為（商家未以環保為賣點，不構成漂綠）」，禁止套用漂綠七宗罪指責賣家；若文案含品牌機能詞彙（如專利感名稱），僅委婉補充其屬於舒適或機能訴求、並非環保認證即可，並正面肯定其成分與產地公開處。
- **材質製程與生命週期環境足跡（消費者永續背景提醒）**：
  - 若屬**軌道 A**：緊扣本次材質與產地，直指品牌宣稱未提及的真實環境代價與權衡。
  - 若屬**軌道 B**：改以**溫和、客觀提醒消費者**的角度補充賣場未提及的產業環境背景（不否定賣家、不指責「隱瞞」或影射有害殘留）：例如說明一般慣行棉在種植端耗水屬產業常態，珍惜耐穿久用最能攤平足跡；缺乏第三方認證屬日常服飾常見情況，新衣或深色衣物穿前先冷水過水清洗即可更安心；產地跨國運輸距離則作為消費選購的客觀參考。
- **水洗標或規格細節檢核**：實質檢核照片或賣場規格中的纖維比例加總是否為 100%、洗滌符號與文字說明是否一致，以及中英文印刷是否有漏字、拼寫錯誤、自相矛盾或關鍵資訊空白。

### 5. 【永續替代與洗護建議】（若省略對照表，此區請編號為 `### 4.`）
- **針對本件材質的專屬洗護延壽處方**：依本件實際洗標圖示與纖維特性，提出具體的建議水溫、洗劑選擇（用白話說明為何應避開柔軟精或含氯漂白劑）、脫水轉速、晾曬方式與微塑膠攔截措施（如濾網洗衣袋），並說明為什麼延長穿著壽命是最有感的永續行動。
- **替代選購與台灣在地循環去處指引**：依據本件欠缺的永續環節，明確推薦應優先選擇的認證標章（簡要白話說明該標章代表什麼把關）或單一純質替代材質；同時提供這件衣服未來汰換時在台灣最適合的循環去處（如八成新以上優先二手轉售／換衣市集／社福捐贈；破損的第一級純棉可剪裁為抹布或送單一材質回收；破損的第二、三級混紡或含彈性纖維切勿誤投只收堪穿二手衣的街頭舊衣回收箱）。

【報告最末尾必附結構化摘要區塊】（請務必接在整份報告最後一行之後，勿寫在報告正文內）：
請嚴格使用以下格式輸出 9 行欄位，供系統繪製永續儀表板與賣家提問範本：
===SUSTAIN_DATA===
透明度: 高|中|低 | 18字內白話說明
循環力: 高|中|低 | 18字內白話說明
誠實度: 高|中|低 | 18字內白話說明
認證背書: 有|無 | 實際檢出標章，或填「日常多無標章（屬加分項）」
耗水量: 高|中|低 | 14字內白話說明
微塑膠: 高|中|低|無 | 14字內白話說明（100%天然纖維請填無）
分解性: 易|中|難 | 14字內白話說明
回收度: 易|中|難 | 14字內白話說明
賣家提問: （資訊完整填「無」；若有缺漏法定纖維成分名稱與比例／是否含彈性纖維／產地／認證字號與認證範圍是否涵蓋整件成衣，寫一段 40～90 字禮貌自然的繁中私訊賣家提問句）
===END_SUSTAIN_DATA===
"""

st.set_page_config(page_title="服飾永續與漂綠檢驗助手", page_icon="🌿", layout="wide")


# --- 設定、連線與影像快取工具 ---
@st.cache_data(show_spinner=False, max_entries=4)
def load_api_keys(env_mtime: float) -> tuple[str, ...]:
    """優先讀取本機 .env，若部署至 Streamlit Cloud 則自動讀取 st.secrets（依 .env 修改時間快取）"""
    load_dotenv(override=True)
    raw = os.getenv("GEMINI_API_KEYS") or os.getenv("GEMINI_API_KEY") or ""
    if not raw:
        try:
            raw = st.secrets.get("GEMINI_API_KEYS") or st.secrets.get("GEMINI_API_KEY") or ""
        except Exception:
            raw = ""
    return tuple(
        k.strip()
        for k in str(raw).split(",")
        if k.strip() and k.strip() != "你的API_Key貼在這裡"
    )


@st.cache_resource(show_spinner=False, max_entries=16)
def get_genai_client(api_key: str, timeout_ms: int) -> genai.Client:
    """建立並快取強制關閉 SDK 隱藏重試的 Client，重複利用連線池降低開銷"""
    try:
        if hasattr(types, "HttpRetryOptions"):
            return genai.Client(
                api_key=api_key,
                http_options=types.HttpOptions(
                    timeout=timeout_ms,
                    retry_options=types.HttpRetryOptions(attempts=1),
                ),
            )
    except Exception:
        pass
    return genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=timeout_ms))


@st.cache_data(show_spinner=False, max_entries=16)
def compress_image(raw_bytes: bytes) -> bytes:
    """於記憶體內自動轉正、縮放並壓縮為輕量 JPEG（不寫入硬碟、超過 40KB 自動二次減肥）"""
    with Image.open(io.BytesIO(raw_bytes)) as img:
        img = ImageOps.exif_transpose(img).convert("RGB")
        img.thumbnail(IMG_MAX_SIZE)
        with io.BytesIO() as buf:
            img.save(buf, format="JPEG", quality=IMG_JPEG_QUALITY, optimize=True)
            data = buf.getvalue()
        if len(data) > IMG_TARGET_MAX_BYTES:
            img.thumbnail(IMG_FALLBACK_SIZE)
            with io.BytesIO() as buf:
                img.save(buf, format="JPEG", quality=52, optimize=True)
                data = buf.getvalue()
        return data


# --- 本地知識庫解析與智慧檢索 ---
def get_kb_signature(kb_folder: str) -> tuple[tuple[str, int, int], ...]:
    """計算知識庫檔案指紋（檔名、修改時間、大小），修改 Word 後重整即自動重新解析"""
    if not os.path.isdir(kb_folder):
        return ()
    sig = []
    for fname in sorted(os.listdir(kb_folder)):
        if fname.lower().endswith(".docx") and not fname.startswith("~$"):
            st_res = os.stat(os.path.join(kb_folder, fname))
            sig.append((fname, st_res.st_mtime_ns, st_res.st_size))
    return tuple(sig)


_KB_CARD_PREFIXES = ("【總綱：", "【大廠查核：", "【國際纖維：", "【驗證機構：")
_ONDEMAND_STOP_TOKENS = frozenset({
    "集團", "實業", "興業", "公司", "團隊", "台灣", "美國", "義大利", "奧地利",
    "日本", "德國", "原杜邦", "再生尼龍", "生質", "纖維", "涼爽紗", "cu", "ptt",
})


def _norm_kb_match(text: str) -> str:
    """正規化文字供大廠與驗證機構名稱比對（統一臺/台、去除商標符號並轉小寫）"""
    return (
        text.replace("臺", "台")
        .replace("é", "e")
        .replace("É", "e")
        .replace("®", "")
        .replace("™", "")
        .lower()
    )


def _is_ondemand_kb(fname: str) -> bool:
    """判斷是否為按需命中載入之大廠與機構字典（未提及廠牌時零字數負擔）"""
    low = fname.lower()
    return fname.startswith("04_") or "fiber_brands" in low or "certifiers" in low


def _extract_card_triggers(card_text: str) -> tuple[str, ...]:
    """從原子卡片首行標題（【類型：名詞...】）萃取出專屬檢索觸發詞"""
    first_line = card_text.splitlines()[0].strip()
    if "：" not in first_line:
        return ()
    inner = first_line.split("：", 1)[1].rstrip("】")
    tokens = []
    for part in re.split(r"[｜／（）()、]+", inner):
        tok = _norm_kb_match(part).strip()
        if len(tok) >= 2 and tok not in _ONDEMAND_STOP_TOKENS:
            tokens.append(tok)
    return tuple(dict.fromkeys(tokens))


def _card_matches_query(triggers: tuple[str, ...], norm_query: str) -> bool:
    for tok in triggers:
        if tok.isascii() and len(tok) <= 4:
            if re.search(rf"(?<![a-z0-9]){re.escape(tok)}(?![a-z0-9])", norm_query):
                return True
        elif tok in norm_query:
            return True
    return False


def extract_docx_chunks(filepath: str, target_chunk_size: int = 450) -> tuple[tuple[int, str], ...]:
    """以標準庫直讀 .docx 段落與表格並切塊（零額外套件依賴，支援原子卡片完整不截斷）"""
    with zipfile.ZipFile(filepath) as zf:
        root = ET.fromstring(zf.read("word/document.xml"))

    body = root.find(f"{WORD_NS}body")
    if body is None:
        return ()

    lines, seen = [], set()
    for elem in body:
        if elem.tag == f"{WORD_NS}p":
            line = "".join(t.text for t in elem.iter(f"{WORD_NS}t") if t.text).strip()
            if line and line not in seen:
                seen.add(line)
                lines.append(line)
        elif elem.tag == f"{WORD_NS}tbl":
            for tr in elem.findall(f".//{WORD_NS}tr"):
                cells = [
                    " ".join("".join(t.text for t in tc.iter(f"{WORD_NS}t") if t.text).split())
                    for tc in tr.findall(f"{WORD_NS}tc")
                ]
                row = " | ".join(c for c in cells if c)
                if row and row not in seen:
                    seen.add(row)
                    lines.append(row)

    chunks, buf, buf_len = [], [], 0
    for line in lines:
        in_card = bool(buf and buf[0].startswith(_KB_CARD_PREFIXES))
        if buf and (
            line.startswith(_KB_CARD_PREFIXES)
            or (in_card and line.startswith(("一、", "二、", "三、")))
        ):
            chunks.append((len(chunks), "\n".join(buf)))
            buf, buf_len = [], 0
            in_card = False
        buf.append(line)
        buf_len += len(line)
        if not in_card and not buf[0].startswith(_KB_CARD_PREFIXES) and buf_len >= target_chunk_size:
            chunks.append((len(chunks), "\n".join(buf)))
            buf, buf_len = [], 0
    if buf:
        chunks.append((len(chunks), "\n".join(buf)))

    return tuple(chunks)


@st.cache_data(show_spinner=False, max_entries=4)
def load_local_knowledge_bases(
    kb_folder: str,
    files_sig: tuple[tuple[str, int, int], ...],
) -> tuple[tuple[str, ...], dict[str, tuple[tuple[int, str], ...]], tuple[str, ...]]:
    """依檔案指紋快取解析結果，避免 Streamlit 每次互動重跑都重複解壓 Word 檔"""
    loaded_files, chunks_by_file, warnings = [], {}, []
    for fname, _, _ in files_sig:
        try:
            file_chunks = extract_docx_chunks(os.path.join(kb_folder, fname))
            if file_chunks:
                loaded_files.append(fname)
                chunks_by_file[fname] = file_chunks
        except Exception as e:
            warnings.append(f"⚠️ 無法讀取 {fname}（請確認 Word 是否已關閉）：{e}")
    return tuple(loaded_files), chunks_by_file, tuple(warnings)


def retrieve_relevant_kb_context(
    chunks_by_file: dict[str, tuple[tuple[int, str], ...]],
    claim_text: str,
    per_file_char_limit: int = KB_PER_FILE_LIMIT,
) -> str:
    """從本地知識庫依核心關鍵詞與宣傳文案精準檢索條文（大廠字典採「命中才載入」零額外負擔模式）"""
    if not chunks_by_file:
        return ""

    has_claim = bool(claim_text.strip())
    norm_query = _norm_kb_match(claim_text) if has_claim else ""
    user_terms = (
        tuple({w.lower() for w in (_EN_TERM_RE.findall(claim_text) + _ZH_TERM_RE.findall(claim_text))})
        if has_claim
        else ()
    )

    sections = []
    for fname, chunks in chunks_by_file.items():
        if _is_ondemand_kb(fname):
            master_chunks = [txt for _, txt in chunks if "【總綱：" in txt]
            if not has_claim:
                if master_chunks:
                    sections.append(f"【知識庫精選規則：{fname}】\n" + "\n---\n".join(master_chunks))
                continue

            matched_cards = []
            for _, txt in chunks:
                if "【總綱：" in txt or not txt.lstrip().startswith(_KB_CARD_PREFIXES):
                    continue
                if _card_matches_query(_extract_card_triggers(txt), norm_query):
                    matched_cards.append(txt)

            if not matched_cards:
                continue

            picked_od, used_od = [], 0
            for txt in master_chunks + matched_cards:
                if used_od + len(txt) <= per_file_char_limit or len(picked_od) <= len(master_chunks):
                    picked_od.append(txt)
                    used_od += len(txt)
                if used_od >= per_file_char_limit:
                    break
            sections.append(f"【知識庫精選規則：{fname}】\n" + "\n---\n".join(picked_od))
            continue

        scored = []
        for idx, txt in chunks:
            txt_lower = txt.lower()
            score = (
                (6 if idx < 2 else 0)
                + sum(2 for kw in CORE_KEYWORDS if kw in txt_lower)
                + sum(5 for ut in user_terms if ut in txt_lower)
            )
            scored.append((score, idx, txt))

        scored.sort(key=lambda x: (-x[0], x[1]))
        picked, used_chars = [], 0
        for _, idx, txt in scored:
            if used_chars + len(txt) <= per_file_char_limit or not picked:
                picked.append((idx, txt))
                used_chars += len(txt)
            if used_chars >= per_file_char_limit:
                break

        picked.sort(key=lambda x: x[0])
        sections.append(f"【知識庫精選規則：{fname}】\n" + "\n---\n".join(t for _, t in picked))

    return "\n\n".join(sections)


# --- 報告清洗、解析與視覺元件 ---
_SEC_TITLE_LINE_RE = re.compile(r"(?m)^###\s*([1-5])[\.、\s]+(【[^\n]+)")
_QUOTE_PAIRS = {"「": "」", "『": "』", "（": "）", "【": "】", "《": "》"}
_OPEN_PUNCT_CHARS = "「『（【《("
_CLOSE_PUNCT_CHARS = "」』）】》)"
_TRAILING_SAFE_CHARS = " \t\n」』）】》)。，、；：！？.,;:!?*)"
_SECTION_KEYWORDS = (
    (2, ("交叉對照", "宣傳 vs", "對照表")),
    (3, ("混紡回收", "回收難度")),
    (4, ("漂綠七宗罪", "環境足跡")),
    (5, ("永續替代", "洗護建議")),
    (1, ("風險評級",)),
)
_NEG_OR_HYPO_CERT_TOKENS = (
    "無認證", "無第三方", "無任何", "無國際", "無有機", "未檢出", "未見",
    "未標示", "未附", "未達", "缺乏", "缺少", "沒有", "僅有", "自繪", "自創", "非國際",
    "建議", "推薦", "替代", "若能", "改選", "優先選擇", "未來",
)


def normalize_section_numbers(text: str) -> str:
    """依實際出現的章節順序統一主標題編號（省略對照表時自動順編為 1、2、3、4）"""
    seq = 0

    def _replace_header(match: re.Match) -> str:
        nonlocal seq
        header_tail = match.group(2)
        if any(kw in header_tail for _, kws in _SECTION_KEYWORDS for kw in kws):
            seq += 1
            return f"### {seq}. {header_tail}"
        return match.group(0)

    return _SEC_TITLE_LINE_RE.sub(_replace_header, text)


def fix_markdown_bold_delimiters(text: str) -> str:
    """以成對粗體邊界解析修復 CommonMark 星號露出問題（清除內側多餘空白、外移成對引號、補正中英文標點間距）"""
    out_lines = []
    for line in text.split("\n"):
        if "**" not in line:
            out_lines.append(line)
            continue
        parts = line.split("**")
        for idx in range(1, len(parts) - 1, 2):
            content = parts[idx].strip(" \t")
            if not content:
                continue
            if (
                len(content) >= 3
                and content[0] in _QUOTE_PAIRS
                and content[-1] == _QUOTE_PAIRS[content[0]]
                and _QUOTE_PAIRS[content[0]] not in content[1:-1]
            ):
                parts[idx - 1] += content[0]
                parts[idx + 1] = content[-1] + parts[idx + 1]
                content = content[1:-1].strip(" \t")
            parts[idx] = content
            if content and content[0] in _OPEN_PUNCT_CHARS and parts[idx - 1]:
                prev_ch = parts[idx - 1][-1]
                if not prev_ch.isspace() and prev_ch not in _OPEN_PUNCT_CHARS:
                    parts[idx - 1] += " "
            if content and content[-1] in _CLOSE_PUNCT_CHARS and parts[idx + 1]:
                if parts[idx + 1][0] not in _TRAILING_SAFE_CHARS:
                    parts[idx + 1] = " " + parts[idx + 1]
        out_lines.append("**".join(parts))
    return "\n".join(out_lines)


def strip_sustain_meta(text: str, strip_partial: bool = False) -> str:
    """移除報告末尾供系統解析的結構化永續摘要區塊，避免顯示於正文或匯出檔"""
    cleaned = _SUSTAIN_META_SPLIT_RE.sub("", text)
    if strip_partial:
        cleaned = _SUSTAIN_META_PARTIAL_RE.sub("", cleaned)
    return cleaned.rstrip()


def clean_report_text(text: str, strip_partial: bool = False) -> str:
    """移除系統摘要區塊、清除 HTML 換行標籤、修復中文標點旁的粗體星號，並順編連貫章節序號"""
    body = strip_sustain_meta(text, strip_partial=strip_partial)
    cleaned = fix_markdown_bold_delimiters(_BR_TAG_RE.sub("；", body))
    return normalize_section_numbers(cleaned)


def _match_section_num(header_line: str, fallback_num: int) -> int:
    """優先依標題關鍵字歸類章節編號，避免第 2 區省略後模型重編序號導致分頁錯位"""
    for sec_num, keywords in _SECTION_KEYWORDS:
        if any(kw in header_line for kw in keywords):
            return sec_num
    return fallback_num


def parse_report_sections(report_text: str) -> dict[int, str]:
    """將報告拆解為分區字典，供分頁與總覽卡呈現（支援省略第 2 區）"""
    matches = list(_SEC_HEADER_RE.finditer(report_text))
    sections: dict[int, str] = {}
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(report_text)
        line_end = report_text.find("\n", start)
        header_line = report_text[start:line_end if line_end != -1 else end]
        sec_num = _match_section_num(header_line, int(m.group(1)))
        sections[sec_num] = report_text[start:end].strip()
    return sections


def _extract_certs_from_text(text: str) -> list[str]:
    """從文字片段抽取實際檢出的國際認證，排除否定與推薦語句"""
    found: list[str] = []
    for clause in re.split(r"[；;，,。\n（(]", text):
        if any(tok in clause for tok in _NEG_OR_HYPO_CERT_TOKENS):
            continue
        clause_lower = clause.lower()
        for cert in KNOWN_CERTS:
            if cert.lower() in clause_lower and cert not in found:
                found.append(cert)
    return found


def extract_kpi_summary(report_text: str, sections: dict[int, str]) -> dict[str, tuple[str, str, str]]:
    """從報告內容萃取頂部 3 張體檢總覽卡資料（風險評級、回收分級、國際認證）"""
    # 1. 風險評級（與系統標準用詞一致）
    preview = report_text[:260]
    if "🔴" in preview or "高風險" in preview:
        risk = ("🔴 高風險", "高度漂綠疑慮｜宣傳與實物或回收／認證落差明顯", "risk-high")
    elif "🟡" in preview or "中風險" in preview:
        risk = ("🟡 中風險", "資訊模糊或回收受限｜缺第三方認證或具製程足跡", "risk-mid")
    elif "🟢" in preview or "低風險" in preview:
        risk = ("🟢 低風險", "具公信力認證或純質易回收｜標示相符且永續表現佳", "risk-low")
    else:
        risk = ("⚪ 已完成", "請見下方各面向詳細查核結果", "risk-neutral")

    # 2. 混紡回收分級
    sec3 = sections.get(3, report_text)
    m_tier = _TIER_EXPLICIT_RE.search(sec3) or _TIER_ANY_RE.search(sec3)
    tier_map = {
        "第一級": ("♻️ 第一級", "100% 單一純質・可閉環回收", "risk-low"),
        "第二級": ("🔄 第二級", "同屬性混紡・多屬降級回收", "risk-mid"),
        "第三級": ("⛔ 第三級", "異質混紡或含彈性纖維・回收死角", "risk-high"),
    }
    recycling = tier_map.get(m_tier.group(1), ("♻️ 見分頁詳解", "請檢視混紡與附屬配件回收影響", "risk-neutral")) if m_tier else ("♻️ 見分頁詳解", "請檢視混紡與附屬配件回收影響", "risk-neutral")

    # 3. 國際認證辨識（優先讀取第 2 區對照表第 3 欄；若省略第 2 區則改由第 1 區實物辨識句萃取）
    sec2 = sections.get(2, "")
    cert_source = ""
    for line in sec2.splitlines():
        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|") and "---" not in stripped and "品牌宣傳宣稱" not in stripped:
            cols = [c.strip() for c in stripped.strip("|").split("|")]
            if len(cols) >= 3:
                cert_source = cols[2]
                break

    if not cert_source:
        cert_source = sections.get(1, "")

    found_certs = _extract_certs_from_text(cert_source)
    if found_certs:
        certs = ("✅ " + " / ".join(found_certs[:3]), "具國際第三方信任背書（永續加分項）", "risk-low")
    else:
        certs = ("🏷️ 無國際標章", "日常服飾多無認證（屬加分項），請看下方 4 大永續維度", "risk-neutral")

    return {"risk": risk, "recycling": recycling, "certs": certs}


_NEG_FIBER_TOKENS = ("不含", "無", "未含", "沒有", "不同於", "不像", "非", "避免", "零", "不必", "不需")
_NON_FIBER_NEG_RE = re.compile(r"無(?:第三方|國際|有機|毒|標章|認證)")
_SYNTH_FIBER_TERMS = ("聚酯", "polyester", "尼龍", "nylon", "壓克力", "亞克力", "彈性纖維", "spandex", "elastane", "人造纖維", "化纖")
_NATURAL_FIBER_TERMS = ("純棉", "棉", "cotton", "亞麻", "linen", "羊毛", "wool", "蠶絲", "silk", "天然纖維")


def _norm_level(val: str, valid: tuple[str, ...], default: str) -> str:
    """將模型輸出的程度詞（含常見同義詞如零／極低）正規化為標準分級"""
    s = val.strip()
    if "無" in valid and "高" in valid:
        rules = (
            ("無", ("無微塑膠", "無風險", "零微塑膠", "沒有", "無", "零")),
            ("低", ("極低", "很少", "低", "少")),
            ("中", ("普通", "一般", "中")),
            ("高", ("極高", "高", "強")),
        )
    elif valid == ("高", "中", "低"):
        rules = (
            ("低", ("極低", "沒有", "無", "零", "很少", "少", "弱", "低")),
            ("中", ("普通", "一般", "中")),
            ("高", ("極高", "高", "強")),
        )
    elif valid == ("易", "中", "難"):
        rules = (
            ("難", ("極難", "困難", "無法", "不可", "難", "低", "差")),
            ("中", ("普通", "尚可", "中")),
            ("易", ("極易", "容易", "易", "高", "佳", "好")),
        )
    elif valid == ("有", "無"):
        rules = (
            ("無", ("沒有", "未見", "未檢出", "無")),
            ("有", ("具備", "通過", "有")),
        )
    else:
        rules = tuple((opt, (opt,)) for opt in valid)

    for target, tokens in rules:
        if target in valid and any(tok in s for tok in tokens):
            return target
    return default


def _detect_fiber_flags(sec1_text: str, fallback_text: str) -> tuple[bool, bool]:
    """從第 1 區實物辨識句偵測是否含合成纖維或天然植物纖維，略過「不含聚酯」等否定子句"""
    def _scan(text: str) -> tuple[bool, bool]:
        has_s, has_c = False, False
        for raw_clause in re.split(r"[；;，,。\n（(]", text):
            clause = _NON_FIBER_NEG_RE.sub("", raw_clause).lower()
            if any(neg in clause for neg in _NEG_FIBER_TOKENS):
                continue
            if any(t in clause for t in _SYNTH_FIBER_TERMS):
                has_s = True
            if any(t in clause for t in _NATURAL_FIBER_TERMS):
                has_c = True
        return has_s, has_c

    has_synth, has_cotton = _scan(sec1_text) if sec1_text else (False, False)
    if not has_synth and not has_cotton and fallback_text:
        has_synth, has_cotton = _scan(fallback_text)
    return has_synth, has_cotton


def parse_sustain_data(
    raw_text: str,
    report_text: str,
    sections: dict[int, str],
    kpi: dict[str, tuple[str, str, str]],
    is_online_mode: bool = False,
) -> dict:
    """從 AI 末尾摘要區塊解析 4 大永續維度、4 項材質環境指標與賣家提問，缺漏時自動補齊安全預設值"""
    fields: dict[str, str] = {}
    m = _SUSTAIN_META_BLOCK_RE.search(raw_text or "")
    if m:
        for line in m.group(1).splitlines():
            if ":" in line or "：" in line:
                key, val = re.split(r"[:：]", line, maxsplit=1)
                fields[key.strip()] = val.strip()

    def _split_pair(key: str, valid: tuple[str, ...], default_lvl: str, default_desc: str) -> dict[str, str]:
        raw_val = fields.get(key, "")
        if "|" in raw_val:
            lvl_part, desc_part = raw_val.split("|", 1)
            return {
                "level": _norm_level(lvl_part.strip(), valid, default_lvl),
                "desc": desc_part.strip() or default_desc,
            }
        if raw_val:
            return {"level": _norm_level(raw_val, valid, default_lvl), "desc": default_desc}
        return {"level": default_lvl, "desc": default_desc}

    # 依報告既有線索準備穩健的預設值
    risk_title = kpi["risk"][0]
    rec_title = kpi["recycling"][0]
    has_cert = kpi["certs"][0].startswith("✅")

    def_rec_lvl = "高" if "第一級" in rec_title else ("中" if "第二級" in rec_title else "低")
    def_rec_desc = (
        "單一純質具衣到衣閉環潛力" if def_rec_lvl == "高"
        else ("同屬性混紡多屬降級回收" if def_rec_lvl == "中" else "異質混紡或彈性纖維難拆解")
    )
    def_honest_lvl = "低" if "🔴" in risk_title else ("中" if "🟡" in risk_title else "高")
    def_honest_desc = (
        "宣傳與實物落差較明顯" if def_honest_lvl == "低"
        else ("部分用語偏模糊或缺佐證" if def_honest_lvl == "中" else "無誇大宣稱且標示相符")
    )
    def_trans_lvl = "低" if ("未揭露" in report_text or "未標示" in report_text) and is_online_mode else ("中" if is_online_mode else "高")
    def_trans_desc = (
        "關鍵比例或產地未寫清楚" if def_trans_lvl == "低"
        else ("建議向賣家補齊細節資訊" if def_trans_lvl == "中" else "成分與基本標示尚屬明確")
    )

    has_synth, has_cotton = _detect_fiber_flags(sections.get(1, ""), report_text or "")
    def_water = ("高", "傳統棉花種植耗水量較高") if has_cotton else (("低", "化纖製程耗水較天然棉低") if has_synth else ("中", "視原料與染整製程而定"))
    def_micro = ("高", "洗滌易釋放合成微塑膠纖維") if has_synth else (("無", "天然植物纖維無塑膠微粒") if has_cotton else ("中", "需視混紡化纖比例而定"))
    def_bio = ("難", "合成塑膠纖維極難自然分解") if has_synth else (("易", "天然純質纖維較容易分解") if has_cotton else ("中", "混紡或塗層會拖慢分解"))
    def_rec_env = ("易" if def_rec_lvl == "高" else ("中" if def_rec_lvl == "中" else "難"), def_rec_desc[:14])

    transparency = _split_pair("透明度", ("高", "中", "低"), def_trans_lvl, def_trans_desc)
    circularity = _split_pair("循環力", ("高", "中", "低"), def_rec_lvl, def_rec_desc)
    honesty = _split_pair("誠實度", ("高", "中", "低"), def_honest_lvl, def_honest_desc)
    cert_bonus = _split_pair(
        "認證背書",
        ("有", "無"),
        "有" if has_cert else "無",
        kpi["certs"][0].replace("✅ ", "") if has_cert else "日常多無標章（屬加分項）",
    )

    water = _split_pair("耗水量", ("高", "中", "低"), *def_water)
    microplastic = _split_pair("微塑膠", ("無", "低", "中", "高"), *def_micro)
    biodeg = _split_pair("分解性", ("易", "中", "難"), *def_bio)
    recyclability = _split_pair("回收度", ("易", "中", "難"), *def_rec_env)

    # 標籤與白話說明一致性防呆：避免「零微塑膠」卻顯示紅燈「高」
    micro_desc = microplastic["desc"]
    if any(t in micro_desc for t in ("零微塑膠", "無微塑膠", "無塑膠微粒", "不含塑膠", "不釋放微塑膠", "不會釋放微塑膠", "無微塑膠排放")) and "釋放合成" not in micro_desc:
        microplastic["level"] = "無"
    water_desc = water["desc"]
    if any(t in water_desc for t in ("極低", "耗水低", "低耗水", "省水")) and "高" not in water_desc:
        water["level"] = "低"

    seller_q = fields.get("賣家提問", "").strip()
    if seller_q in ("無", "無須", "不需要", "完整", "無需提問", "N/A", "-"):
        seller_q = ""
    elif not seller_q and (is_online_mode or transparency["level"] != "高"):
        seller_q = (
            "您好，想向您確認這件商品的完整材質與產地資訊：請問確切的纖維成分百分比是多少？"
            "是否有含彈性纖維（如 Spandex／Lycra）？另外請問產地以及是否具備相關環保或無毒認證呢？謝謝您！"
        )

    return {
        "dims": {
            "transparency": transparency,
            "circularity": circularity,
            "honesty": honesty,
            "cert_bonus": cert_bonus,
        },
        "env": {
            "water": water,
            "microplastic": microplastic,
            "biodeg": biodeg,
            "recyclability": recyclability,
        },
        "seller_question": seller_q,
    }


def render_kpi_cards(kpi: dict[str, tuple[str, str, str]]) -> None:
    """渲染報告頂部的 3 張視覺化體檢總覽卡"""
    cards = [
        ("漂綠風險評級", *kpi["risk"]),
        ("混紡回收分級", *kpi["recycling"]),
        ("國際認證標章（加分項）", *kpi["certs"]),
    ]
    cols = st.columns(3)
    for col, (label, value, desc, style_cls) in zip(cols, cards):
        col.markdown(
            f"""
            <div class="kpi-card {style_cls}">
                <div class="kpi-label">{html.escape(label)}</div>
                <div class="kpi-value">{html.escape(value)}</div>
                <div class="kpi-desc">{html.escape(desc)}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )


def render_sustain_dimensions(sdata: dict) -> None:
    """渲染 4 大永續體檢維度橫向列，平衡日常無認證服飾之評價"""
    dims = sdata["dims"]
    level_cls_3 = {"高": "lvl-good", "中": "lvl-mid", "低": "lvl-bad"}
    items = [
        ("🔍 資訊透明度", dims["transparency"]["level"], dims["transparency"]["desc"], level_cls_3.get(dims["transparency"]["level"], "lvl-mid")),
        ("♻️ 材質循環力", dims["circularity"]["level"], dims["circularity"]["desc"], level_cls_3.get(dims["circularity"]["level"], "lvl-mid")),
        ("🛡️ 誠實無漂綠", dims["honesty"]["level"], dims["honesty"]["desc"], level_cls_3.get(dims["honesty"]["level"], "lvl-mid")),
        (
            "🏅 認證加分項",
            "已獲背書" if dims["cert_bonus"]["level"] == "有" else "無標章・加分項",
            dims["cert_bonus"]["desc"],
            "lvl-good" if dims["cert_bonus"]["level"] == "有" else "lvl-neutral",
        ),
    ]
    st.markdown("<div class='section-mini-title'>🌱 4 大永續體檢維度</div>", unsafe_allow_html=True)
    cols = st.columns(4)
    for col, (title, badge, desc, cls) in zip(cols, items):
        col.markdown(
            f"""
            <div class="dim-card">
                <div class="dim-head">
                    <span class="dim-title">{html.escape(title)}</span>
                    <span class="dim-badge {cls}">{html.escape(badge)}</span>
                </div>
                <div class="dim-desc">{html.escape(desc)}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )


def render_seller_question_box(sdata: dict, is_online_mode: bool) -> None:
    """顯示一鍵複製私訊賣家提問範本，協助網購缺少水洗標時推動資訊透明"""
    q_text = sdata.get("seller_question", "")
    if q_text:
        st.markdown(
            """
            <div class="seller-box">
                <div class="seller-box-title">💬 商家標示不夠清楚？一鍵複製私訊賣家提問範本</div>
                <div class="seller-box-sub">點選下方區塊右上角的複製圖示，貼到賣場聊聊或客服詢問，用消費提問促進資訊透明：</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.code(q_text, language=None)
    elif is_online_mode:
        st.caption("✅ 本次提供的商品材質與資訊揭露已算完整，暫無須額外向賣家追問。")


def render_env_impact_bars(sdata: dict) -> None:
    """於漂綠與足跡分頁頂部顯示 4 項材質環境指標視覺進度條"""
    env = sdata["env"]
    # 耗水量與微塑膠：越低越友善(綠)；分解性與回收度：越易越友善(綠)
    specs = [
        ("💧 耗水程度", env["water"], {"低": ("lvl-good", 30), "中": ("lvl-mid", 65), "高": ("lvl-bad", 100)}),
        ("🫧 微塑膠風險", env["microplastic"], {"無": ("lvl-good", 12), "低": ("lvl-good", 30), "中": ("lvl-mid", 65), "高": ("lvl-bad", 100)}),
        ("🍂 自然分解性", env["biodeg"], {"易": ("lvl-good", 95), "中": ("lvl-mid", 60), "難": ("lvl-bad", 28)}),
        ("♻️ 循環回收度", env["recyclability"], {"易": ("lvl-good", 95), "中": ("lvl-mid", 60), "難": ("lvl-bad", 28)}),
    ]
    st.markdown("<div class='section-mini-title'>🌍 材質環境足跡速覽</div>", unsafe_allow_html=True)
    cols = st.columns(4)
    for col, (title, item, style_map) in zip(cols, specs):
        lvl = item["level"]
        cls, pct = style_map.get(lvl, ("lvl-mid", 60))
        col.markdown(
            f"""
            <div class="env-card">
                <div class="dim-head">
                    <span class="env-title">{html.escape(title)}</span>
                    <span class="dim-badge {cls}">{html.escape(lvl)}</span>
                </div>
                <div class="env-bar-bg"><div class="env-bar-fill {cls}" style="width:{pct}%"></div></div>
                <div class="dim-desc">{html.escape(item["desc"])}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)


def render_wear_longevity_calculator(record_id: int) -> None:
    """呈現單次穿著成本（CPW）與延長穿著壽命永續計算器"""
    with st.container(border=True):
        st.markdown("#### ⏳ 穿久更永續：單次穿著成本（CPW）計算器")
        c1, c2 = st.columns(2)
        price = c1.number_input(
            "預計入手價格（NT$）",
            min_value=50,
            max_value=50000,
            value=690,
            step=50,
            key=f"cpw_price_{record_id}",
        )
        wears = c2.slider(
            "預計會穿幾次？",
            min_value=1,
            max_value=120,
            value=30,
            key=f"cpw_wears_{record_id}",
        )
        cpw = price / wears
        if wears < 10:
            tier_msg = "⚠️ 低於 10 次較容易淪為衝動快時尚，建議多想一下搭配度再決定。"
            tier_cls = "risk-high"
        elif wears < 30:
            tier_msg = "🟡 距離國際永續倡議「#30Wears（至少穿 30 次）」還差一點，再多穿幾次就回本了！"
            tier_cls = "risk-mid"
        elif wears < 60:
            tier_msg = "🟢 已達成「#30Wears」永續門檻！每多穿一次都在攤薄這件衣服的環境足跡。"
            tier_cls = "risk-low"
        else:
            tier_msg = "🌿 超高實穿率！把衣服穿到淋漓盡致，就是對地球最實際、最省錢的減碳行動。"
            tier_cls = "risk-low"

        st.markdown(
            f"""
            <div class="cpw-result {tier_cls}">
                <div class="cpw-main">單次穿著成本：<strong>NT$ {cpw:,.1f} / 次</strong>（共穿 {wears} 次）</div>
                <div class="cpw-sub">{html.escape(tier_msg)}</div>
                <div class="cpw-tip">💡 英國永續服飾研究（WRAP）指出，將衣物穿著壽命延長約 9 個月，即可減少約 20%～30% 的碳、水與廢棄物足跡；冷水洗與自然陰乾是最有效的延壽習慣。</div>
            </div>
            """,
            unsafe_allow_html=True,
        )


def render_taiwan_disposal_guide(kpi: dict[str, tuple[str, str, str]]) -> None:
    """依本件混紡回收分級，呈現台灣在地舊衣淘汰與循環去處指引"""
    rec_val = kpi["recycling"][0]
    is_tier1 = "第一級" in rec_val
    is_tier23 = "第二級" in rec_val or "第三級" in rec_val

    with st.expander("🇹🇼 台灣在地淘汰與舊衣循環去處指引", expanded=True):
        c1, c2, c3 = st.columns(3)
        c1.markdown(
            """
            <div class="disposal-card">
                <div class="disposal-title">👕 八成新・品相良好</div>
                <div class="disposal-text">優先<strong>二手轉賣</strong>（旋轉拍賣、蝦皮二手）、參與換衣市集、親友轉贈，或捐贈給實際有需求之社福單位，讓衣物繼續被穿最環保。</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        t1_cls = "disposal-card disposal-highlight" if is_tier1 else "disposal-card"
        t1_tag = " <span class='tag-best'>⭐ 本件破損適用</span>" if is_tier1 else ""
        c2.markdown(
            f"""
            <div class="{t1_cls}">
                <div class="disposal-title">♻️ 破損・第一級純棉/純天然{t1_tag}</div>
                <div class="disposal-text">100% 純棉破損後非常吸水，適合<strong>剪裁成居家抹布</strong>或收納布袋；亦適合交由支援單一材質再生的紡織回收專案。</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        t23_cls = "disposal-card disposal-highlight" if is_tier23 else "disposal-card"
        t23_tag = " <span class='tag-best'>⭐ 本件破損適用</span>" if is_tier23 else ""
        c3.markdown(
            f"""
            <div class="{t23_cls}">
                <div class="disposal-title">🚫 破損・第二／三級混紡{t23_tag}</div>
                <div class="disposal-text"><strong>切勿投入街頭舊衣回收箱</strong>（多數僅收堪穿衣物，混紡破損會造成後端分選與清運負擔）；建議先作一次性擦拭後再依一般垃圾處理。</div>
            </div>
            """,
            unsafe_allow_html=True,
        )


def render_scoring_guide(compact: bool = False) -> None:
    """呈現清楚、合乎邏輯且精煉的「風險評級標準與三大判讀依據」"""
    if not compact:
        st.markdown(
            """
            <div class="guide-hero">
                <div class="guide-kicker">SCORING FRAMEWORK</div>
                <h3>🎯 我們如何判定漂綠與永續表現？</h3>
                <p>同時支援<strong>實體拍照水洗標</strong>與<strong>網購下單前規格快篩</strong>，根據<strong>「宣傳與資訊透明度」</strong>、<strong>「國際認證加分門檻」</strong>與<strong>「混紡回收可行性」</strong>交叉查核，不因日常服飾無標章而一味扣分。</p>
            </div>
            """,
            unsafe_allow_html=True,
        )

    st.markdown(
        """
        <div class="rubric-grid">
            <div class="rubric-card risk-high">
                <div class="rubric-title">🔴 高風險｜高度漂綠疑慮</div>
                <div class="rubric-body">
                    <strong>觸發條件：</strong>品牌主動強調環保、永續或使用自繪樹葉標章，但水洗標實際為<strong>無國際認證的異質混紡或含彈性纖維（第三級不可回收）</strong>。<br>
                    <strong>評分邏輯：</strong>行銷宣稱與實際回收／製程表現明顯矛盾，觸犯「無證據、假標籤或隱藏權衡」等漂綠特徵，最容易誤導消費決策。
                </div>
            </div>
            <div class="rubric-card risk-mid">
                <div class="rubric-title">🟡 中風險｜資訊模糊或回收受限</div>
                <div class="rubric-body">
                    <strong>觸發條件：</strong>① 雖有採用大廠再生／有機原料，但將上游紗線認證寫成成品認證、缺字號或行銷偏誇大；② 賣場隱藏比例／產地；或 ③ 材質屬<strong>第二級降級回收</strong>或無認證傳統纖維。<br>
                    <strong>評分邏輯：</strong>非完全造假，但供應鏈揭露或認證範圍仍不夠嚴謹，或製程仍具實質環境足跡。
                </div>
            </div>
            <div class="rubric-card risk-low">
                <div class="rubric-title">🟢 低風險｜具公信力認證或純質易回收</div>
                <div class="rubric-body">
                    <strong>觸發條件：</strong>宣稱與實際成分相符，具備可驗證的國際認證（如 <strong>GOTS、GRS、OEKO-TEX Standard 100、bluesign</strong>），且屬<strong>第一級 100% 單一純質</strong>或合規再生材質。<br>
                    <strong>評分邏輯：</strong>有第三方機構把關原料與製程安全，且衣物汰換後具備較高的循環再生價值。
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if not compact:
        st.markdown("#### 🔎 三大核心查核支柱")
        c1, c2, c3 = st.columns(3)
        c1.markdown(
            """
            <div class="pillar-card">
                <div class="pillar-title">1. 透明揭露與認證加分</div>
                <div class="pillar-text">日常服飾多無標章，認證屬信任加分項；若網購賣場未標示成分或產地，系統會自動產生賣家私訊提問範本。</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        c2.markdown(
            """
            <div class="pillar-card">
                <div class="pillar-title">2. 混紡回收三級制</div>
                <div class="pillar-text">依纖維組成劃分：第一級單一純質閉環回收、第二級同屬性降級回收、第三級異質混紡或含 Spandex 彈性纖維回收死角。</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        c3.markdown(
            """
            <div class="pillar-card">
                <div class="pillar-title">3. 七宗罪與延壽洗護</div>
                <div class="pillar-text">拆解耗水、微塑膠與漂綠話術，並提供單次穿著成本計算、專屬洗護延壽與台灣在地汰換指引。</div>
            </div>
            """,
            unsafe_allow_html=True,
        )


def render_recycling_guide() -> None:
    """呈現混紡回收難度分頁專屬的「三級分類」白話定義卡片"""
    st.markdown(
        """
        <div class="rubric-grid">
            <div class="rubric-card risk-low">
                <div class="rubric-title">♻️ 第一級｜100% 單一純質・可「衣到衣」閉環回收</div>
                <div class="rubric-body">
                    <strong>定義與條件：</strong>衣服主體只用<strong>一種材質（100%）</strong>做成（例如 100% 純棉、100% 聚酯纖維、100% 羊毛），沒有混其他纖維。<br>
                    <strong>白話意義：</strong>回收廠不需費力挑除雜質，直接切碎重新紡紗或溶解重抽，就能再製成同等級的新布與新衣（衣到衣循環），回收價值最高。
                </div>
            </div>
            <div class="rubric-card risk-mid">
                <div class="rubric-title">🔄 第二級｜同屬性混紡・多屬降級回收</div>
                <div class="rubric-body">
                    <strong>定義與條件：</strong>由兩種以上<strong>同為天然材質</strong>（如棉＋亞麻）或<strong>同為人造化纖</strong>（如聚酯＋尼龍）混紡，但沒有含彈性纖維。<br>
                    <strong>白話意義：</strong>雖然同大類，但不同纖維耐溫與縮率不同、很難拆開提純，實務上多只能「降級」打碎做成抹布、填充棉或隔音墊，很難再變回新衣服。
                </div>
            </div>
            <div class="rubric-card risk-high">
                <div class="rubric-title">⛔ 第三級｜異質混紡或含彈性纖維・回收死角</div>
                <div class="rubric-body">
                    <strong>定義與條件：</strong>把天然纖維與塑膠化纖混在一起（如棉＋聚酯），或混入任何比例的<strong>彈性纖維（英文常見 Spandex / Elastane / Lycra，類似橡皮筋的伸縮材質）</strong>。<br>
                    <strong>白話意義：</strong>現有技術極難把不同性質的材質分開，彈性纖維也會卡住機器；淘汰後多只能焚化或掩埋，因此最需要透過正確洗護延長穿著壽命。
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.caption("📚 分類邏輯依據：參考 Ellen MacArthur Foundation《A New Textiles Economy》紡織循環經濟架構、紡織回收分選實務及台灣環境部資源循環署單一材質設計方向彙整。")


@st.dialog("🎯 我們如何判定漂綠風險？（補充說明）", width="large")
def open_scoring_guide_dialog() -> None:
    """以可關閉的彈出視窗呈現評分標準與三大查核支柱，不清空或覆蓋目前報告"""
    render_scoring_guide(compact=False)
    st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
    if st.button("✖️ 關閉說明，繼續查看報告", type="primary", use_container_width=True):
        st.rerun()


def render_report_dashboard(record: dict) -> None:
    """以「頂部總覽卡 ＋ 4 大永續維度 ＋ 分頁切換」渲染完成後的檢驗報告"""
    report_text = clean_report_text(record["report"])
    elapsed = record["elapsed"]
    retrieved_kb = record.get("kb_text", "")
    is_online_mode = bool(record.get("is_online_mode", False))
    sections = parse_report_sections(report_text)
    kpi = extract_kpi_summary(report_text, sections)
    sdata = record.get("sustain_data") or parse_sustain_data("", report_text, sections, kpi, is_online_mode)

    top_l, top_r = st.columns([3, 1.2])
    top_l.success(f"✅ 分析完成（總耗時 **{elapsed:.1f}** 秒）")
    if top_r.button("📖 查看評分說明", use_container_width=True):
        open_scoring_guide_dialog()
    if record.get("truncated"):
        st.warning("⚠️ 本次報告內容較長，末尾可能受限於單次輸出上限而略微截斷；建議可重新分析或精簡輸入文字。")

    render_kpi_cards(kpi)
    render_sustain_dimensions(sdata)
    st.markdown("<div style='height:10px'></div>", unsafe_allow_html=True)

    has_sec2 = 2 in sections
    has_core_tabs = len(sections) >= 4 and all(k in sections for k in (1, 3, 4, 5))

    if has_core_tabs:
        tab1_label = "📋 總評與對照" if has_sec2 else "📋 風險總評"
        tab1, tab2, tab3, tab4, tab5 = st.tabs([
            tab1_label,
            "♻️ 混紡回收",
            "🚨 漂綠與足跡",
            "🧺 洗護與替代",
            "📄 完整報告",
        ])
        with tab1:
            part12 = "\n\n".join(s for s in (sections.get(1), sections.get(2)) if s)
            st.markdown(part12 or report_text)
            render_seller_question_box(sdata, is_online_mode)
            with st.expander("📖 什麼是 🔴 高風險／🟡 中風險／🟢 低風險？（風險評級標準說明）"):
                render_scoring_guide(compact=True)
        with tab2:
            st.markdown(sections.get(3, "（本報告未獨立切出混紡回收段落，請切換至「完整報告」檢視）"))
            with st.expander("📖 什麼是第一級／第二級／第三級？（混紡回收難度分級說明）"):
                render_recycling_guide()
        with tab3:
            render_env_impact_bars(sdata)
            st.markdown(sections.get(4, "（本報告未獨立切出漂綠查核段落，請切換至「完整報告」檢視）"))
            with st.expander("📖 什麼是 🔴 高風險／🟡 中風險／🟢 低風險？（風險評級標準說明）"):
                render_scoring_guide(compact=True)
        with tab4:
            st.markdown(sections.get(5, "（本報告未獨立切出洗護建議段落，請切換至「完整報告」檢視）"))
            render_wear_longevity_calculator(record["id"])
            render_taiwan_disposal_guide(kpi)
            with st.expander("📖 什麼是 🔴 高風險／🟡 中風險／🟢 低風險？（風險評級標準說明）"):
                render_scoring_guide(compact=True)
        with tab5:
            st.markdown(report_text)
            st.download_button(
                "⬇️ 下載完整檢驗報告 (.md)",
                data=report_text.encode("utf-8"),
                file_name=f"服飾永續檢驗報告_{record['time'].replace(':', '')}.md",
                mime="text/markdown",
                use_container_width=True,
            )
            with st.expander("📖 什麼是 🔴 高風險／🟡 中風險／🟢 低風險？（風險評級標準說明）"):
                render_scoring_guide(compact=True)
            with st.expander("📖 什麼是第一級／第二級／第三級？（混紡回收難度分級說明）"):
                render_recycling_guide()
    else:
        render_env_impact_bars(sdata)
        st.markdown(report_text)
        render_seller_question_box(sdata, is_online_mode)
        render_wear_longevity_calculator(record["id"])
        render_taiwan_disposal_guide(kpi)
        st.download_button(
            "⬇️ 下載完整檢驗報告 (.md)",
            data=report_text.encode("utf-8"),
            file_name=f"服飾永續檢驗報告_{record['time'].replace(':', '')}.md",
            mime="text/markdown",
            use_container_width=True,
        )
        with st.expander("📖 什麼是 🔴 高風險／🟡 中風險／🟢 低風險？（風險評級標準說明）"):
            render_scoring_guide(compact=True)
        with st.expander("📖 什麼是第一級／第二級／第三級？（混紡回收難度分級說明）"):
            render_recycling_guide()

    if retrieved_kb:
        with st.expander("📚 查看本次判讀參考的知識庫條文"):
            st.text(retrieved_kb)


def render_live_timer(
    placeholder,
    label_text: str,
    elapsed_offset: float = 0.0,
    stopped_error: bool = False,
) -> None:
    """渲染累計計時不歸零、支援偵測停止與錯誤凍結的輕量即時碼表（不跨元件覆寫，避免 React DOM 衝突）"""
    offset_ms = int(elapsed_offset * 1000)
    safe_label = html.escape(label_text)
    if stopped_error:
        with placeholder.container():
            components.html(
                f"""
                <div translate="no" style="font-family:'Source Sans Pro',sans-serif;padding:14px 16px;background:#fdf2f2;color:#991b1b;border-radius:10px;border:1px solid #f5c2c2;font-size:15px;display:flex;align-items:center;box-sizing:border-box;">
                    <span style="margin-right:10px;font-size:18px;">❌</span>
                    <span>{safe_label}（總耗時 <strong>{elapsed_offset:.1f}</strong> 秒）</span>
                </div>
                """,
                height=62,
            )
        return

    with placeholder.container():
        components.html(
            f"""
            <div id="tb" translate="no" style="font-family:'Source Sans Pro',sans-serif;padding:14px 16px;background:#eaf3ee;color:#1b4332;border-radius:10px;border:1px solid #b7d7c5;font-size:15px;display:flex;align-items:center;box-sizing:border-box;">
                <span id="ti" style="margin-right:10px;font-size:18px;">🌿</span>
                <span id="tm">{safe_label}（已累計 <strong id="ts">{elapsed_offset:.1f}</strong> 秒）</span>
            </div>
            <script>
                const t0 = Date.now() - {offset_ms};
                let seen = false;
                const iv = setInterval(() => {{
                    const sec = ((Date.now() - t0) / 1000).toFixed(1);
                    try {{
                        const pdoc = window.parent.document;
                        const w = pdoc.querySelector('[data-testid="stStatusWidget"]');
                        if (w) {{ seen = true; }}
                        else if (seen) {{
                            clearInterval(iv);
                            const b = document.getElementById("tb");
                            if (b) {{ b.style.background="#f5f5f3"; b.style.color="#495057"; b.style.borderColor="#d9d8d0"; }}
                            document.getElementById("ti").innerText = "⏹️";
                            document.getElementById("tm").innerHTML = "已手動停止分析（停止前耗時 <strong>" + sec + "</strong> 秒）";
                            return;
                        }}
                        const reportEl = pdoc.querySelector('[data-gw-streaming="1"]');
                        if (reportEl) {{
                            document.getElementById("ti").innerText = "✍️";
                            const lbl = document.getElementById("tm");
                            if (lbl && !lbl.dataset.streamMode) {{
                                lbl.dataset.streamMode = "1";
                                lbl.innerHTML = "正在即時生成檢驗報告（已累計 <strong id=\\"ts\\">" + sec + "</strong> 秒）";
                            }}
                        }}
                    }} catch (e) {{}}
                    const el = document.getElementById("ts");
                    if (el) el.innerText = sec;
                }}, 250);
            </script>
            """,
            height=62,
        )


def run_ai_analysis(
    api_keys: tuple[str, ...],
    invalid_keys: set[int],
    image_bytes_list: list[bytes],
    claim_text: str,
    retrieved_kb: str,
    is_online_mode: bool = False,
) -> dict | None:
    """執行多節點與雙模型輪替之串流快篩分析，完成後回傳結構化報告紀錄"""
    system_prompt = (
        f"{BASE_SYSTEM_PROMPT}\n\n【專屬審查知識庫精選規則】：\n{retrieved_kb}"
        if retrieved_kb
        else BASE_SYSTEM_PROMPT
    )

    has_claim_text = bool(claim_text.strip())
    if is_online_mode:
        mode_hint = (
            "【本次輸入模式】：使用者處於「網購下單前（免水洗標）」模式，"
            "僅有賣場文字規格或商品頁截圖，沒有實體水洗標照片。"
            "請直接依已提供資訊評估永續與漂綠風險，具體點出未標示的成分比例／產地／彈性纖維缺口，"
            "並在末尾 ===SUSTAIN_DATA=== 的『賣家提問』欄位產出禮貌實用的私訊提問範本；"
            "完整輸出第 1～5 點（包含第 2 點【宣傳 vs. 實際成分交叉對照表】）。"
        )
    elif has_claim_text or len(image_bytes_list) > 1:
        mode_hint = (
            "【本次輸入模式】：使用者已提供多張圖片或品牌宣傳文字，"
            "請完整輸出第 1～5 點（包含第 2 點【宣傳 vs. 實際成分交叉對照表】）。"
        )
    elif len(image_bytes_list) == 1:
        mode_hint = (
            "【本次輸入模式】：使用者未填寫宣傳文字且僅上傳 1 張圖片。"
            "請先檢視該圖是否包含品牌環保／永續宣傳標語；若僅為單張水洗標／成分標籤照片，"
            "請直接省略【宣傳 vs. 實際成分交叉對照表】，並將後續三個章節直接順編為 "
            "### 2. 【混紡回收難度拆解】、### 3. 【漂綠七宗罪與環境足跡查核】、### 4. 【永續替代與洗護建議】（維持 1、2、3、4 連貫編號）。"
        )
    else:
        mode_hint = "【本次輸入模式】：請依據提供的資料審查並輸出報告。"

    user_contents: list = [
        f"請嚴格依據專屬審查知識庫規範與以下實物資料，產出客製化、詳細且各區職責不重複的深度審查報告。\n{mode_hint}"
    ]
    use_part_bytes = hasattr(types.Part, "from_bytes")
    for idx_img, img_bytes in enumerate(image_bytes_list, start=1):
        img_part = (
            types.Part.from_bytes(data=img_bytes, mime_type="image/jpeg")
            if use_part_bytes
            else Image.open(io.BytesIO(img_bytes))
        )
        user_contents.extend([f"【圖片 {idx_img}】", img_part])
    if has_claim_text:
        user_contents.append(f"【品牌宣傳文字】：\n{claim_text.strip()}")

    gen_config = types.GenerateContentConfig(
        system_instruction=system_prompt,
        temperature=0.2,
        max_output_tokens=MAX_OUTPUT_TOKENS,
        thinking_config=types.ThinkingConfig(thinking_budget=THINKING_BUDGET),
    )

    valid_indices = [i for i in range(len(api_keys)) if i not in invalid_keys] or list(range(len(api_keys)))
    random.shuffle(valid_indices)
    pick_key = lambda n: valid_indices[n % len(valid_indices)]

    attempt_plan = (
        (MODELS_TO_TRY[0], pick_key(0), True, 60000),   # 第 1 順位：主力模型（節點 A｜串流）
        (MODELS_TO_TRY[1], pick_key(1), True, 60000),   # 第 2 順位：備援模型（節點 B｜串流，避開同模型尖峰塞車）
        (MODELS_TO_TRY[0], pick_key(2), False, 60000),  # 第 3 順位：主力模型（節點 C｜標準通道）
    )

    timer_box = st.empty()
    retry_box = st.empty()
    report_box = st.empty()

    start_time = time.time()
    # 全程只建立一次計時器 iframe，避免重試時反覆銷毀 DOM 節點
    render_live_timer(timer_box, "正在分析衣物成分與永續資訊", elapsed_offset=0.0)

    full_text = ""
    is_truncated = False
    error_logs: list[str] = []
    last_was_auth_err = False

    def _check_max_tokens(resp_obj) -> bool:
        try:
            cands = getattr(resp_obj, "candidates", None) or ()
            for cand in cands:
                fr = str(getattr(cand, "finish_reason", "") or "")
                if "MAX_TOKENS" in fr:
                    return True
        except Exception:
            pass
        return False

    for attempt_idx, (model_name, k_idx, use_stream, timeout_ms) in enumerate(attempt_plan, start=1):
        if k_idx in invalid_keys:
            continue
        if attempt_idx > 1:
            retry_box.caption(f"⏳ 目前免費節點較忙碌，正在切換備援通道重試（第 {attempt_idx} / {len(attempt_plan)} 順位）...")
            if not last_was_auth_err:
                time.sleep(0.8)

        client = get_genai_client(api_keys[k_idx], timeout_ms)
        attempt_truncated = False
        try:
            if use_stream:
                chunks_buf: list[str] = []
                last_render = 0.0
                for chunk in client.models.generate_content_stream(
                    model=model_name, contents=user_contents, config=gen_config
                ):
                    if _check_max_tokens(chunk):
                        attempt_truncated = True
                    if not chunk.text:
                        continue
                    now = time.time()
                    chunks_buf.append(chunk.text)
                    # 節流重繪，大幅降低 WebSocket 與瀏覽器排版負荷
                    if now - last_render >= STREAM_UPDATE_INTERVAL:
                        last_render = now
                        stream_md = clean_report_text("".join(chunks_buf), strip_partial=True) + " ▌"
                        report_box.markdown(
                            f'<div data-gw-streaming="1" class="notranslate" translate="no">\n\n{stream_md}\n\n</div>',
                            unsafe_allow_html=True,
                        )
                full_text = "".join(chunks_buf)
            else:
                resp = client.models.generate_content(
                    model=model_name, contents=user_contents, config=gen_config
                )
                attempt_truncated = _check_max_tokens(resp)
                if resp and resp.text:
                    full_text = resp.text

            if not full_text:
                raise RuntimeError("模型回傳空白內容")
            is_truncated = attempt_truncated
            break
        except Exception as e:
            err_str = str(e)
            last_was_auth_err = any(tok in err_str for tok in ("401", "API key not valid", "UNAUTHENTICATED"))
            if last_was_auth_err:
                invalid_keys.add(k_idx)
            error_logs.append(err_str)
            full_text = ""

    total_elapsed = time.time() - start_time
    if full_text:
        clean_final = clean_report_text(full_text)
        if clean_final.count("**") % 2 == 1 and clean_final.rstrip().endswith("**"):
            clean_final = clean_final.rstrip()[:-2].rstrip()
        parsed_sections = parse_report_sections(clean_final)
        parsed_kpi = extract_kpi_summary(clean_final, parsed_sections)
        sustain_data = parse_sustain_data(full_text, clean_final, parsed_sections, parsed_kpi, is_online_mode)
        preview = clean_final[:150]
        dot = "🔴" if "🔴" in preview else ("🟡" if "🟡" in preview else ("🟢" if "🟢" in preview else "⚪"))
        default_tag = "網購規格快篩" if is_online_mode else "水洗標單獨體檢"
        snippet = claim_text.strip()[:14] + ("…" if len(claim_text.strip()) > 14 else "") if claim_text.strip() else default_tag
        return {
            "id": int(time.time() * 1000),
            "time": time.strftime("%H:%M:%S"),
            "label": f"{dot} {snippet}",
            "report": clean_final,
            "elapsed": total_elapsed,
            "kb_text": retrieved_kb,
            "truncated": is_truncated,
            "is_online_mode": is_online_mode,
            "sustain_data": sustain_data,
        }

    # 失敗時以同類型的靜態紅框取代碼表並凍結秒數，避免跨元件替換觸發 React removeChild 錯誤
    render_live_timer(
        timer_box,
        "目前服務忙碌或連線失敗，請稍後再試",
        elapsed_offset=total_elapsed,
        stopped_error=True,
    )
    retry_box.caption("💡 提示：免費版 API 尖峰時段偶爾會排隊逾時，建議稍等幾秒再試一次。")
    if error_logs:
        with st.expander("ℹ️ 查看錯誤詳情"):
            st.markdown("\n".join(f"- {err}" for err in error_logs))
    return None


# --- 初始化狀態與快取資料 ---
env_path = os.path.join(BASE_DIR, ".env")
env_mtime = os.path.getmtime(env_path) if os.path.exists(env_path) else 0.0
API_KEYS = load_api_keys(env_mtime)
invalid_key_indices: set[int] = st.session_state.setdefault("invalid_key_indices", set())
scan_history: list[dict] = st.session_state.setdefault("scan_history", [])
st.session_state.setdefault("current_report", None)

KB_FILES, KB_CHUNKS_BY_FILE, KB_WARNINGS = load_local_knowledge_bases(
    KB_FOLDER, get_kb_signature(KB_FOLDER)
)

# --- 自然永續主題樣式（亞麻紙感底色 ＋ 森林深綠主色 ＋ 卡片排版） ---
# 防止瀏覽器自動翻譯改寫文字節點導致 React removeChild 崩潰
components.html(
    """
    <script>
    try {
        const doc = window.parent.document;
        const root = doc.documentElement;
        root.lang = "zh-TW";
        root.setAttribute("translate", "no");
        root.classList.add("notranslate");
        if (doc.body) {
            doc.body.setAttribute("translate", "no");
            doc.body.classList.add("notranslate");
        }
        if (!doc.querySelector('meta[name="google"][content="notranslate"]')) {
            const m = doc.createElement("meta");
            m.name = "google";
            m.content = "notranslate";
            doc.head.appendChild(m);
        }
    } catch (e) {}
    </script>
    """,
    height=0,
)
st.markdown(
    """<style>
.stApp{background-color:#F6F5F0;color:#1F2923}
section[data-testid="stSidebar"]{background-color:#EEECE4;border-right:1px solid #DFDDD2}
.block-container{padding-top:1.5rem!important;padding-bottom:2rem!important;max-width:1320px}
.hero-banner{background:linear-gradient(135deg,#1E4635 0%,#2E654B 100%);color:#F8FAF8;padding:1.25rem 1.6rem;border-radius:14px;margin-bottom:1.15rem;box-shadow:0 4px 14px rgba(30,70,53,.12)}
.hero-banner h1{margin:0;font-size:1.65rem;font-weight:700;letter-spacing:.3px;color:#FFFFFF}
.hero-banner p{margin:.35rem 0 0;font-size:.93rem;color:#DCECE3}
.guide-hero{background:#FFFFFF;border:1px solid #E3E0D5;border-radius:12px;padding:1.05rem 1.2rem;margin-bottom:.85rem}
.guide-kicker{font-size:.72rem;font-weight:700;letter-spacing:1.2px;color:#2E654B;margin-bottom:.2rem}
.guide-hero h3{margin:0 0 .35rem;font-size:1.15rem;color:#1E4635}
.guide-hero p{margin:0;font-size:.9rem;line-height:1.55;color:#3E4C44}
.rubric-grid{display:flex;flex-direction:column;gap:.65rem;margin-bottom:1rem}
.rubric-card{background:#FFFFFF;border-radius:10px;padding:.85rem 1rem;border:1px solid #E4E2D8;border-left-width:5px;box-shadow:0 1px 4px rgba(0,0,0,.03)}
.rubric-title{font-weight:700;font-size:.96rem;margin-bottom:.3rem;color:#1E2A23}
.rubric-body{font-size:.86rem;line-height:1.58;color:#3D4A42}
.pillar-card{background:#FFFFFF;border:1px solid #E3E0D5;border-radius:10px;padding:.8rem .9rem;height:100%}
.pillar-title{font-weight:700;font-size:.9rem;color:#1E4635;margin-bottom:.25rem}
.pillar-text{font-size:.82rem;line-height:1.52;color:#4A5850}
.kpi-card{background:#FFFFFF;border-radius:11px;padding:.85rem .95rem;border:1px solid #E3E0D5;border-top-width:4px;box-shadow:0 2px 6px rgba(0,0,0,.035);min-height:108px}
.kpi-label{font-size:.76rem;font-weight:600;color:#627068;letter-spacing:.4px;margin-bottom:.25rem}
.kpi-value{font-size:1.12rem;font-weight:700;color:#1C2721;margin-bottom:.22rem}
.kpi-desc{font-size:.78rem;line-height:1.4;color:#55635B}
.section-mini-title{font-size:.86rem;font-weight:700;color:#1E4635;margin:.75rem 0 .4rem;letter-spacing:.3px}
.dim-card,.env-card{background:#FFFFFF;border:1px solid #E3E0D5;border-radius:10px;padding:.68rem .78rem;height:100%;box-shadow:0 1px 4px rgba(0,0,0,.025)}
.dim-head{display:flex;justify-content:space-between;align-items:center;gap:6px;margin-bottom:.32rem;flex-wrap:wrap}
.dim-title,.env-title{font-size:.82rem;font-weight:700;color:#1F2923}
.dim-badge{font-size:.72rem;font-weight:700;padding:2px 8px;border-radius:999px;white-space:nowrap}
.dim-badge.lvl-good{background:#E3F3EA;color:#1E6542}
.dim-badge.lvl-mid{background:#FDF3DF;color:#9E6A0E}
.dim-badge.lvl-bad{background:#FBE8E8;color:#A82929}
.dim-badge.lvl-neutral{background:#EFEFE9;color:#55625A}
.dim-desc{font-size:.75rem;line-height:1.4;color:#55635B}
.env-bar-bg{background:#ECEAE0;height:7px;border-radius:999px;overflow:hidden;margin:.28rem 0 .35rem}
.env-bar-fill{height:100%;border-radius:999px}
.env-bar-fill.lvl-good{background:#2A7B52}
.env-bar-fill.lvl-mid{background:#C98A1B}
.env-bar-fill.lvl-bad{background:#C93B3B}
.seller-box{background:#EEF6F1;border:1px solid #C2DEC9;border-left:5px solid #2A7B52;border-radius:10px;padding:.75rem .95rem;margin:.85rem 0 .4rem}
.seller-box-title{font-size:.92rem;font-weight:700;color:#1E4635;margin-bottom:.2rem}
.seller-box-sub{font-size:.8rem;color:#3E5448}
.cpw-result{border:1px solid #E3E0D5;border-left-width:5px;border-radius:10px;padding:.8rem 1rem;margin-top:.35rem}
.cpw-main{font-size:.98rem;color:#1C2721;margin-bottom:.25rem}
.cpw-sub{font-size:.85rem;color:#2F3D35;margin-bottom:.35rem}
.cpw-tip{font-size:.78rem;line-height:1.48;color:#55635B}
.disposal-card{background:#FFFFFF;border:1px solid #E3E0D5;border-radius:10px;padding:.78rem .88rem;height:100%}
.disposal-highlight{background:#F5FBF7;border:1.5px solid #2A7B52;box-shadow:0 2px 8px rgba(42,123,82,.08)}
.disposal-title{font-size:.85rem;font-weight:700;color:#1E4635;margin-bottom:.3rem}
.disposal-text{font-size:.79rem;line-height:1.5;color:#3E4C44}
.tag-best{display:inline-block;background:#E3F3EA;color:#1E6542;font-size:.7rem;padding:1px 6px;border-radius:999px;margin-left:4px}
.risk-high{border-color:#E7C6C4;border-left-color:#C93B3B!important;border-top-color:#C93B3B!important;background:#FFFBFB}
.risk-mid{border-color:#EADBB5;border-left-color:#C98A1B!important;border-top-color:#C98A1B!important;background:#FFFDF7}
.risk-low{border-color:#C3DEC9;border-left-color:#2A7B52!important;border-top-color:#2A7B52!important;background:#F9FDFB}
.risk-neutral{border-color:#DAD8CE;border-left-color:#6C7A72!important;border-top-color:#6C7A72!important}
.stButton>button[kind="primary"]{background-color:#23533E!important;border-color:#23533E!important;color:#FFFFFF!important;border-radius:10px;font-weight:600;padding:.55rem 1rem}
.stButton>button[kind="primary"]:hover{background-color:#1A3F2F!important;border-color:#1A3F2F!important}
[data-testid="stImage"] img{max-height:140px!important;width:auto!important;max-width:100%!important;object-fit:contain!important;border-radius:8px;border:1px solid #dcd9ce;padding:3px;background:#fff}
</style>""",
    unsafe_allow_html=True,
)

# --- 側邊欄：工具簡介、知識庫開關與檢驗紀錄 ---
with st.sidebar:
    st.header("🌿 工具簡介")
    st.markdown(
        "協助消費者快速比對服飾的**品牌環保宣稱**與**實際材質成分**，涵蓋完整永續體檢：\n"
        "- **網購免水洗標快篩**：沒標籤也能查缺漏並生成賣家提問\n"
        "- **4 大永續體檢維度**：透明度、循環力、誠實度與認證加分\n"
        "- **混紡回收與環境足跡**：解析三級回收、耗水與微塑膠風險\n"
        "- **延壽洗護與在地汰換**：單次穿著成本計算與台灣循環去處"
    )

    st.divider()
    st.subheader("📚 永續規範知識庫")
    use_kb = st.toggle(
        "開啟知識庫",
        value=True,
        help="開啟後會參考本地服飾永續規範文件進行審查；關閉則僅依 AI 內建知識判讀",
    )
    if use_kb:
        if KB_FILES:
            st.caption(f"目前狀態：已開啟（共載入 **{len(KB_FILES)}** 份規範文件）")
            st.caption("  \n".join(f"• `{f}`" for f in KB_FILES))
        else:
            st.warning("⚠️ 尚未在 `kb_docs` 資料夾偵測到 `.docx` 檔案")
    else:
        st.caption("目前狀態：已關閉（本次將僅依 AI 內建知識判讀）")
    for warn in KB_WARNINGS:
        st.warning(warn)

    if scan_history:
        st.divider()
        st.subheader("🕘 本次檢驗紀錄")
        for rec in scan_history:
            if st.button(f"{rec['label']}（{rec['time']}）", key=f"hist_{rec['id']}", use_container_width=True):
                st.session_state["current_report"] = rec
                st.rerun()

# --- 主畫面頁首 ---
st.markdown(
    """
    <div class="hero-banner">
        <h1>🌿 服飾永續與漂綠檢驗助手</h1>
        <p>上傳水洗標與品牌宣傳圖文，即時核對成分認證、解析混紡回收難度、查核漂綠風險並提供洗護建議</p>
    </div>
    """,
    unsafe_allow_html=True,
)

# --- 左右固定對照工作台（輸入左、報告／評分說明右，避免結果一路往下跳） ---
col_input, col_stage = st.columns([1, 1.38], gap="large")

with col_input:
    with st.container(border=True):
        input_mode = st.radio(
            "選擇檢驗情境",
            ("🏷️ 現場／已有衣服（拍水洗標）", "🛒 網購下單前（免水洗標）"),
            horizontal=True,
        )
        is_online_mode = input_mode.startswith("🛒")

        if is_online_mode:
            st.subheader("📝 步驟一：貼上賣場材質規格與宣傳文案")
            claim_text = st.text_area(
                "貼上賣場寫的成分說明、產地或環保宣傳（就算商家沒寫比例也可直接貼上）：",
                placeholder="例如：賣場寫『親膚環保棉質、永續穿搭首選』，規格寫『棉 65%、聚酯纖維 35%』（若賣場完全沒寫比例也直接貼上，AI 會幫你抓出缺漏並生成賣家提問！）",
                height=120,
                max_chars=MAX_CLAIM_CHARS,
                label_visibility="collapsed",
            )
            st.markdown("<div style='height:4px'></div>", unsafe_allow_html=True)
            st.subheader("🖼️ 步驟二：網購商品頁／規格區截圖（選填）")
            st.caption("✅ 請截：成分比例／產地、認證標章或證書、環保宣稱圖　｜　❌ 免截：穿搭照、尺寸表、評價")
            uploaded_files = st.file_uploader(
                f"可上傳網購商品頁、材質規格表或賣場宣稱截圖（最多 {MAX_IMAGES} 張）",
                type=["jpg", "jpeg", "png", "webp"],
                accept_multiple_files=True,
                key="unified_uploader",
            )
            camera_photo = None
        else:
            st.subheader("📸 步驟一：上傳或拍攝水洗標")
            uploaded_files = st.file_uploader(
                f"可上傳「成分水洗標」、「環保吊牌」、「實體海報」或「商品頁截圖」（最多 {MAX_IMAGES} 張）",
                type=["jpg", "jpeg", "png", "webp"],
                accept_multiple_files=True,
                key="unified_uploader",
            )
            enable_camera = st.toggle("📷 啟用相機現場拍攝（適合手機逛街實測）", value=False)
            camera_photo = st.camera_input("對準衣物水洗標或吊牌拍照") if enable_camera else None

        raw_inputs = list(uploaded_files or [])
        if camera_photo is not None:
            raw_inputs.append(camera_photo)
        if len(raw_inputs) > MAX_IMAGES:
            st.warning(f"⚠️ 單次分析上限為 {MAX_IMAGES} 張圖片，已自動採用前 {MAX_IMAGES} 張。")
            raw_inputs = raw_inputs[:MAX_IMAGES]

        processed_images: list[bytes] = []
        if raw_inputs:
            thumb_cols = st.columns(3)
            for i, file in enumerate(raw_inputs):
                img_bytes = compress_image(file.getvalue())
                processed_images.append(img_bytes)
                with thumb_cols[i % 3]:
                    st.image(img_bytes, caption=f"圖片 {i + 1} ({len(img_bytes) // 1024}KB)")

        if not is_online_mode:
            st.markdown("<div style='height:6px'></div>", unsafe_allow_html=True)
            st.subheader("📝 步驟二：品牌宣傳文案（選填）")
            claim_text = st.text_area(
                "貼上商品頁面描述、環保宣言或吊牌文案（未填寫則自動進行水洗標單獨體檢）：",
                placeholder="例如：本款採用 Eco-Conscious 環境友善純淨棉料製成，愛地球永續穿搭首選...",
                height=115,
                max_chars=MAX_CLAIM_CHARS,
                label_visibility="collapsed",
            )

        run_real_ai = st.button("🔍 開始 AI 永續快篩分析", type="primary", use_container_width=True)
        st.caption("💡 圖片僅於記憶體即時判讀，絕不儲存於硬碟；網購無水洗標或未附國際認證時，系統仍會公正體檢並附上賣家提問範本。")

with col_stage:
    if run_real_ai:
        if not processed_images and not claim_text.strip():
            st.warning("👆 請先在左側上傳照片，或貼上賣場規格與宣傳文案再開始分析！")
            if st.session_state["current_report"]:
                render_report_dashboard(st.session_state["current_report"])
            else:
                render_scoring_guide()
        elif not API_KEYS:
            st.error("❌ 尚未在 `.env` 或雲端 Secrets 中設定有效的 `GEMINI_API_KEYS`。")
        else:
            retrieved_kb = (
                retrieve_relevant_kb_context(KB_CHUNKS_BY_FILE, claim_text)
                if use_kb and KB_FILES
                else ""
            )
            result = run_ai_analysis(
                api_keys=API_KEYS,
                invalid_keys=invalid_key_indices,
                image_bytes_list=processed_images,
                claim_text=claim_text,
                retrieved_kb=retrieved_kb,
                is_online_mode=is_online_mode,
            )
            if result:
                st.session_state["current_report"] = result
                scan_history.insert(0, result)
                del scan_history[MAX_HISTORY_ITEMS:]
                st.rerun()
    elif st.session_state["current_report"]:
        render_report_dashboard(st.session_state["current_report"])
    else:
        render_scoring_guide()
