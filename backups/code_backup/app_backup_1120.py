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
MAX_OUTPUT_TOKENS = 4096  # 預留自動思考與完整 5 大架構報告空間，避免截斷
KB_FOLDER = os.path.join(BASE_DIR, "kb_docs")
KB_PER_FILE_LIMIT = 2800
IMG_MAX_SIZE = (512, 512)
IMG_JPEG_QUALITY = 75
STREAM_UPDATE_INTERVAL = 0.12  # 串流畫面節流間隔（秒），減少瀏覽器頻繁重繪
MAX_HISTORY_ITEMS = 5

WORD_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_BR_TAG_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
_EN_TERM_RE = re.compile(r"[A-Za-z0-9\-]{2,}")
_ZH_TERM_RE = re.compile(r"[\u4e00-\u9fff]{2,4}")
_SEC_HEADER_RE = re.compile(r"(?m)^###\s*([1-5])[\.、\s]")
_TIER_EXPLICIT_RE = re.compile(r"(?:屬於|判定為?|列為|歸類為?|屬)[^。\n]{0,14}(第[一二三]級)")
_TIER_ANY_RE = re.compile(r"(第[一二三]級)")

KNOWN_CERTS = (
    "GOTS", "OCS", "GRS", "RCS", "OEKO-TEX", "bluesign",
    "Cradle to Cradle", "Fair Trade", "FSC", "RDS", "RWS", "BCI",
)
_NEG_CERT_TOKENS = ("無", "未", "缺", "非", "沒有", "僅有", "自繪")

# 預先轉小寫的核心領域關鍵詞（避免檢索迴圈重複配置字串）
CORE_KEYWORDS = tuple(
    kw.lower()
    for kw in (
        "GOTS", "OCS", "GRS", "RCS", "OEKO-TEX", "bluesign", "認證", "門檻", "有機", "再生", "無毒",
        "第一級", "第二級", "第三級", "閉環", "降級", "混紡", "純棉", "聚酯", "彈性纖維", "Spandex", "Elastane",
        "七宗罪", "隱藏權衡", "無證據", "模糊不清", "無關緊要", "兩害相權", "撒謊", "假標籤", "漂綠", "永續", "碳足跡",
    )
)

BASE_SYSTEM_PROMPT = """你是專業的「服飾消費品漂綠審查員」。請先在思考階段快速核對上傳圖片與文字中的「品牌宣傳資訊」與「實際成分水洗標資訊」是否存在矛盾，並嚴格對照下方附帶的【專屬審查知識庫精選規則】進行認證門檻、混紡回收分級與漂綠七宗罪判讀（思考請控制在 3 秒內聚焦重點，切勿冗長推演）。

【格式與精簡鐵則】：
1. 每個主標題獨立一行（`### ` 開頭），標題下方空一行再寫內容；每點分析精準扼要（約 2～3 句直指核心，總報告控制在 900～1300 字內），並在分析中明確引用知識庫規範依據。
2. Markdown 表格前必須空一行，禁止使用 `<br>` 等 HTML 標籤（多項資訊用全形分號「；」隔開）。
3. 表格標題固定為：`| 品牌宣傳宣稱 | 水洗標實際成分與產地 | 辨識到的第三方認證標章 |`。

請用繁體中文依以下 5 大架構輸出報告：

### 1. 【風險評級】
第一行標示「🔴 高風險（高度漂綠疑慮）」、「🟡 中風險（資訊模糊或回收受限）」或「🟢 低風險（具備公信力認證或純質易回收）」，並從「宣傳與材質一致性」、「國際認證有無」及「混紡回收分級」三個關鍵依據精要說明為何判定此等級。

### 2. 【宣傳 vs. 實際成分交叉對照表】
（空一行後輸出標準 Markdown 表格；無外部宣傳時第一欄填「未提供外部宣傳，僅就水洗標體檢」）

### 3. 【混紡回收難度拆解】
- **回收分級判定**：依知識庫分級標準判定屬於第一級（100%單一純質閉環回收）、第二級（同屬性混紡降級回收）或第三級（異質混紡或含 Spandex/Elastane 彈性纖維不可回收死角），並說明回收原理。
- **附屬裝飾配件與實務回收影響**：檢視「不含附屬品及裝飾配件」標示及鈕扣、車縫線、印花塗層、鬆緊帶對回收純度的影響。

### 4. 【漂綠七宗罪與環境足跡查核】
- **漂綠行為／傳統製程資源足跡**：若有宣傳則依知識庫逐條核對漂綠七宗罪；若僅有水洗標則剖析該材質在無有機認證（GOTS、OCS）下的耗水、農藥與肥料足跡。
- **印染化學殘留與供應鏈透明度**：依知識庫檢視是否缺乏 OEKO-TEX Standard 100 或 bluesign 認證，並分析產地標示對運輸碳足跡的意義。
- **水洗標細節與印刷準確度查核**：檢查中英文洗滌標示、圖示與成分文字是否有漏字、誤植或矛盾。

### 5. 【永續替代與洗護建議】
- **延長衣物壽命的具體洗護行動**：依洗標圖示提供水溫、洗劑（無磷中性洗劑、避免柔軟精）、脫水與陰乾建議。
- **未來選購與淘汰替代方案**：提供具體替代認證標章建議（GOTS、OCS、GRS、OEKO-TEX）及汰換後的二手傳承／回收管道建議。"""

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
    """於記憶體內自動轉正、縮放至 512×512 並壓縮為 JPEG 位元組（不寫入硬碟、不保留大型 PIL 物件）"""
    with Image.open(io.BytesIO(raw_bytes)) as img:
        img = ImageOps.exif_transpose(img).convert("RGB")
        img.thumbnail(IMG_MAX_SIZE)
        with io.BytesIO() as buf:
            img.save(buf, format="JPEG", quality=IMG_JPEG_QUALITY, optimize=True)
            return buf.getvalue()


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


def extract_docx_chunks(filepath: str, target_chunk_size: int = 450) -> tuple[tuple[int, str], ...]:
    """以標準庫直讀 .docx 段落與表格並切塊（零額外套件依賴）"""
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
        buf.append(line)
        buf_len += len(line)
        if buf_len >= target_chunk_size:
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
    """依檔案指紋快取解析結果，避免 Streamlit 每次互動重跑都重複解壓 9 萬字 Word 檔"""
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
    """從本地知識庫依核心關鍵詞與宣傳文案精準檢索條文，每檔上限約 2,800 字"""
    if not chunks_by_file:
        return ""

    user_terms = (
        tuple({w.lower() for w in (_EN_TERM_RE.findall(claim_text) + _ZH_TERM_RE.findall(claim_text))})
        if claim_text.strip()
        else ()
    )

    sections = []
    for fname, chunks in chunks_by_file.items():
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
def clean_report_text(text: str) -> str:
    """清除模型可能誤輸出的 HTML 換行標籤，統一替換為全形分號"""
    return _BR_TAG_RE.sub("；", text)


def parse_report_sections(report_text: str) -> dict[int, str]:
    """將 5 大架構 Markdown 報告拆解為分區字典，供分頁與總覽卡呈現"""
    matches = list(_SEC_HEADER_RE.finditer(report_text))
    sections: dict[int, str] = {}
    for i, m in enumerate(matches):
        sec_num = int(m.group(1))
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(report_text)
        sections[sec_num] = report_text[start:end].strip()
    return sections


def extract_kpi_summary(report_text: str, sections: dict[int, str]) -> dict[str, tuple[str, str, str]]:
    """從報告內容萃取頂部 3 張體檢總覽卡資料（風險評級、回收分級、國際認證）"""
    # 1. 風險評級
    preview = report_text[:220]
    if "🔴" in preview or "高風險" in preview:
        risk = ("🔴 高風險", "高度漂綠疑慮，宣傳與材質或認證落差明顯", "risk-high")
    elif "🟡" in preview or "中風險" in preview:
        risk = ("🟡 中風險", "資訊透明度不足，或回收／製程仍有改善空間", "risk-mid")
    elif "🟢" in preview or "低風險" in preview:
        risk = ("🟢 低風險", "具公信力認證或單一純質，永續與回收表現佳", "risk-low")
    else:
        risk = ("⚪ 已完成", "請見下方各面向詳細查核結果", "risk-neutral")

    # 2. 混紡回收分級
    sec3 = sections.get(3, report_text)
    m_tier = _TIER_EXPLICIT_RE.search(sec3) or _TIER_ANY_RE.search(sec3)
    tier_map = {
        "第一級": ("♻️ 第一級", "100% 單一純質・可閉環回收", "risk-low"),
        "第二級": ("🔄 第二級", "同屬性混紡・多屬降級回收", "risk-mid"),
        "第三級": ("⛔ 第三級", "異質混紡或含彈性纖維・極難回收", "risk-high"),
    }
    recycling = tier_map.get(m_tier.group(1), ("♻️ 見分頁詳解", "請檢視混紡與附屬配件回收影響", "risk-neutral")) if m_tier else ("♻️ 見分頁詳解", "請檢視混紡與附屬配件回收影響", "risk-neutral")

    # 3. 國際認證辨識（精準讀取第 2 區對照表的第 3 欄，避免誤抓第 5 區的推薦認證）
    sec2 = sections.get(2, "")
    cert_cell = ""
    for line in sec2.splitlines():
        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|") and "---" not in stripped and "品牌宣傳宣稱" not in stripped:
            cols = [c.strip() for c in stripped.strip("|").split("|")]
            if len(cols) >= 3:
                cert_cell = cols[2]
                break

    found_certs: list[str] = []
    if cert_cell:
        for clause in re.split(r"[；;，,（(]", cert_cell):
            if any(neg in clause for neg in _NEG_CERT_TOKENS):
                continue
            for cert in KNOWN_CERTS:
                if cert.lower() in clause.lower() and cert not in found_certs:
                    found_certs.append(cert)

    if found_certs:
        certs = ("✅ " + " / ".join(found_certs[:3]), "於水洗標或吊牌檢出國際第三方認證", "risk-low")
    else:
        certs = ("🏷️ 未檢出認證", "未見 GOTS、GRS、OEKO-TEX 等國際標章", "risk-mid")

    return {"risk": risk, "recycling": recycling, "certs": certs}


def render_kpi_cards(kpi: dict[str, tuple[str, str, str]]) -> None:
    """渲染報告頂部的 3 張視覺化體檢總覽卡"""
    cards = [
        ("漂綠風險評級", *kpi["risk"]),
        ("混紡回收分級", *kpi["recycling"]),
        ("國際認證標章", *kpi["certs"]),
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


def render_scoring_guide(compact: bool = False) -> None:
    """呈現清楚、合乎邏輯且精煉的「風險評級標準與三大判讀依據」"""
    if not compact:
        st.markdown(
            """
            <div class="guide-hero">
                <div class="guide-kicker">SCORING FRAMEWORK</div>
                <h3>🎯 我們如何判定漂綠風險？</h3>
                <p>每一件服飾都會根據<strong>「宣傳與材質一致性」</strong>、<strong>「國際認證門檻」</strong>與<strong>「混紡回收可行性」</strong>三大依據進行交叉查核，而非僅憑字面印象給分。</p>
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
                    <strong>觸發條件：</strong>① 宣傳用語空泛（如「環境友善」）且缺少第三方認證佐證；或 ② 未作誇大宣傳，但材質屬<strong>第二級降級回收</strong>，或為<strong>無有機／無毒認證的一般傳統纖維</strong>。<br>
                    <strong>評分邏輯：</strong>雖無明顯造假，但供應鏈透明度不足，或傳統種植耗水、農藥與印染化學殘留仍具實質環境足跡。
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
                <div class="pillar-title">1. 國際認證門檻</div>
                <div class="pillar-text">區分具公信力的原料認證（GOTS、OCS、GRS、RCS）與無毒製程認證（OEKO-TEX、bluesign），排除品牌自創無效綠標。</div>
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
                <div class="pillar-title">3. 漂綠七宗罪與足跡</div>
                <div class="pillar-text">有宣傳時逐條比對七宗罪；若僅上傳水洗標，則自動轉為單獨體檢，解析該材質的耗水、印染風險與正確洗護方式。</div>
            </div>
            """,
            unsafe_allow_html=True,
        )


def render_report_dashboard(record: dict) -> None:
    """以「頂部總覽卡 ＋ 分頁切換」渲染完成後的檢驗報告，避免頁面無限下拉"""
    report_text = record["report"]
    elapsed = record["elapsed"]
    retrieved_kb = record.get("kb_text", "")
    sections = parse_report_sections(report_text)
    kpi = extract_kpi_summary(report_text, sections)

    top_l, top_r = st.columns([3, 1.2])
    top_l.success(f"✅ 分析完成（總耗時 **{elapsed:.1f}** 秒）")
    if top_r.button("📖 查看評分說明", use_container_width=True):
        st.session_state["current_report"] = None
        st.rerun()

    render_kpi_cards(kpi)
    st.markdown("<div style='height:10px'></div>", unsafe_allow_html=True)

    if len(sections) >= 4:
        tab1, tab2, tab3, tab4, tab5 = st.tabs([
            "📋 總評與對照",
            "♻️ 混紡回收",
            "🚨 漂綠與足跡",
            "🧺 洗護與替代",
            "📄 完整報告",
        ])
        with tab1:
            part12 = "\n\n".join(s for s in (sections.get(1), sections.get(2)) if s)
            st.markdown(part12 or report_text)
        with tab2:
            st.markdown(sections.get(3, "（本報告未獨立切出混紡回收段落，請切換至「完整報告」檢視）"))
        with tab3:
            st.markdown(sections.get(4, "（本報告未獨立切出漂綠查核段落，請切換至「完整報告」檢視）"))
        with tab4:
            st.markdown(sections.get(5, "（本報告未獨立切出洗護建議段落，請切換至「完整報告」檢視）"))
        with tab5:
            st.markdown(report_text)
            st.download_button(
                "⬇️ 下載完整檢驗報告 (.md)",
                data=report_text.encode("utf-8"),
                file_name=f"服飾永續檢驗報告_{record['time'].replace(':', '')}.md",
                mime="text/markdown",
                use_container_width=True,
            )
    else:
        st.markdown(report_text)
        st.download_button(
            "⬇️ 下載完整檢驗報告 (.md)",
            data=report_text.encode("utf-8"),
            file_name=f"服飾永續檢驗報告_{record['time'].replace(':', '')}.md",
            mime="text/markdown",
            use_container_width=True,
        )

    with st.expander("📖 為什麼會評定高／中／低風險？（快速查閱評分邏輯）"):
        render_scoring_guide(compact=True)
    if retrieved_kb:
        with st.expander("📚 查看本次判讀參考的知識庫條文"):
            st.text(retrieved_kb)


def render_live_timer(placeholder, label_text: str, elapsed_offset: float = 0.0) -> None:
    """渲染累計計時不歸零、且自動偵測右上角停止按鈕的輕量即時碼表"""
    offset_ms = int(elapsed_offset * 1000)
    safe_label = html.escape(label_text)
    with placeholder.container():
        components.html(
            f"""
            <div id="tb" style="font-family:'Source Sans Pro',sans-serif;padding:14px 16px;background:#eaf3ee;color:#1b4332;border-radius:10px;border:1px solid #b7d7c5;font-size:15px;display:flex;align-items:center;box-sizing:border-box;">
                <span id="ti" style="margin-right:10px;font-size:18px;">🌿</span>
                <span id="tm">{safe_label}（已累計 <strong id="ts">{elapsed_offset:.1f}</strong> 秒）</span>
            </div>
            <script>
                const t0 = Date.now() - {offset_ms};
                let seen = false;
                const iv = setInterval(() => {{
                    const sec = ((Date.now() - t0) / 1000).toFixed(1);
                    try {{
                        const w = window.parent.document.querySelector('[data-testid="stStatusWidget"]');
                        if (w) {{ seen = true; }}
                        else if (seen) {{
                            clearInterval(iv);
                            const b = document.getElementById("tb");
                            if (b) {{ b.style.background="#f5f5f3"; b.style.color="#495057"; b.style.borderColor="#d9d8d0"; }}
                            document.getElementById("ti").innerText = "⏹️";
                            document.getElementById("tm").innerHTML = "已手動停止分析（停止前耗時 <strong>" + sec + "</strong> 秒）";
                            return;
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
) -> dict | None:
    """執行多節點與雙模型輪替之串流快篩分析，完成後回傳結構化報告紀錄"""
    system_prompt = (
        f"{BASE_SYSTEM_PROMPT}\n\n【專屬審查知識庫精選規則】：\n{retrieved_kb}"
        if retrieved_kb
        else BASE_SYSTEM_PROMPT
    )

    user_contents: list = ["請嚴格依據專屬審查知識庫規範，審查以下服飾資料並輸出完整快篩報告："]
    use_part_bytes = hasattr(types.Part, "from_bytes")
    for idx_img, img_bytes in enumerate(image_bytes_list, start=1):
        img_part = (
            types.Part.from_bytes(data=img_bytes, mime_type="image/jpeg")
            if use_part_bytes
            else Image.open(io.BytesIO(img_bytes))
        )
        user_contents.extend([f"【圖片 {idx_img}】", img_part])
    if claim_text.strip():
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
        (MODELS_TO_TRY[0], pick_key(0), True, 30000),   # 第 1 順位：主力模型（節點 A｜串流）
        (MODELS_TO_TRY[0], pick_key(1), True, 30000),   # 第 2 順位：主力模型（節點 B｜串流）
        (MODELS_TO_TRY[0], pick_key(2), False, 32000),  # 第 3 順位：主力模型（節點 C｜標準通道）
        (MODELS_TO_TRY[1], pick_key(3), True, 30000),   # 第 4 順位：備援模型（串流）
    )

    status_box = st.empty()
    report_box = st.empty()

    start_time = time.time()
    full_text = ""
    error_logs: list[str] = []
    last_was_auth_err = False

    for attempt_idx, (model_name, k_idx, use_stream, timeout_ms) in enumerate(attempt_plan, start=1):
        if k_idx in invalid_keys:
            continue
        if attempt_idx > 1 and not last_was_auth_err:
            time.sleep(0.8)

        label = "正在分析衣物成分與永續資訊" if attempt_idx == 1 else "正在重新連線並分析資料"
        render_live_timer(status_box, label, elapsed_offset=time.time() - start_time)

        client = get_genai_client(api_keys[k_idx], timeout_ms)
        try:
            if use_stream:
                chunks_buf: list[str] = []
                last_render = 0.0
                for chunk in client.models.generate_content_stream(
                    model=model_name, contents=user_contents, config=gen_config
                ):
                    if not chunk.text:
                        continue
                    now = time.time()
                    chunks_buf.append(chunk.text)
                    # 節流重繪，大幅降低 WebSocket 與瀏覽器排版負荷
                    if now - last_render >= STREAM_UPDATE_INTERVAL:
                        last_render = now
                        status_box.info(f"✍️ 正在即時生成檢驗報告（累計 **{now - start_time:.1f}** 秒）")
                        report_box.markdown(clean_report_text("".join(chunks_buf)) + " ▌")
                full_text = "".join(chunks_buf)
            else:
                resp = client.models.generate_content(
                    model=model_name, contents=user_contents, config=gen_config
                )
                if resp and resp.text:
                    full_text = resp.text

            if not full_text:
                raise RuntimeError("模型回傳空白內容")
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
        preview = clean_final[:150]
        dot = "🔴" if "🔴" in preview else ("🟡" if "🟡" in preview else ("🟢" if "🟢" in preview else "⚪"))
        snippet = claim_text.strip()[:14] + ("…" if len(claim_text.strip()) > 14 else "") if claim_text.strip() else "水洗標單獨體檢"
        return {
            "id": int(time.time() * 1000),
            "time": time.strftime("%H:%M:%S"),
            "label": f"{dot} {snippet}",
            "report": clean_final,
            "elapsed": total_elapsed,
            "kb_text": retrieved_kb,
        }

    status_box.error(f"❌ 目前服務忙碌或連線失敗，請稍後再試（耗時 {total_elapsed:.1f} 秒）")
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
        "協助消費者快速比對服飾的**品牌環保宣稱**與**實際水洗標成分**，涵蓋 5 大檢驗面向：\n"
        "- **風險評級**：紅／黃／綠燈判定漂綠疑慮\n"
        "- **成分與認證對照**：核對材質與第三方標章\n"
        "- **混紡回收難度**：解析三級回收可行性\n"
        "- **漂綠與環境足跡**：查核七宗罪與製程風險\n"
        "- **洗護與永續建議**：延長壽命與替代選購"
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
        st.subheader("📸 步驟一：上傳或拍攝水洗標")
        uploaded_files = st.file_uploader(
            "可上傳「成分水洗標（必填）」、「環保吊牌」、「實體海報」或「網購商品頁截圖」",
            type=["jpg", "jpeg", "png", "webp"],
            accept_multiple_files=True,
            key="unified_uploader",
        )
        enable_camera = st.toggle("📷 啟用相機現場拍攝（適合手機逛街實測）", value=False)
        camera_photo = st.camera_input("對準衣物水洗標或吊牌拍照") if enable_camera else None

        raw_inputs = list(uploaded_files or [])
        if camera_photo is not None:
            raw_inputs.append(camera_photo)

        processed_images: list[bytes] = []
        if raw_inputs:
            thumb_cols = st.columns(3)
            for i, file in enumerate(raw_inputs):
                img_bytes = compress_image(file.getvalue())
                processed_images.append(img_bytes)
                with thumb_cols[i % 3]:
                    st.image(img_bytes, caption=f"圖片 {i + 1} ({len(img_bytes) // 1024}KB)")

        st.markdown("<div style='height:6px'></div>", unsafe_allow_html=True)
        st.subheader("📝 步驟二：品牌宣傳文案（選填）")
        claim_text = st.text_area(
            "貼上商品頁面描述、環保宣言或吊牌文案（未填寫則自動進行水洗標單獨體檢）：",
            placeholder="例如：本款採用 Eco-Conscious 環境友善純淨棉料製成，愛地球永續穿搭首選...",
            height=115,
            label_visibility="collapsed",
        )

        run_real_ai = st.button("🔍 開始 AI 漂綠快篩分析", type="primary", use_container_width=True)
        st.caption("💡 圖片僅於記憶體即時判讀，絕不儲存於硬碟；未提供宣傳文案時，系統自動針對水洗標材質與認證進行單獨體檢。")

with col_stage:
    if run_real_ai:
        if not processed_images and not claim_text.strip():
            st.warning("👆 請先在左側上傳一張照片（水洗標或宣傳圖），或輸入一段品牌宣傳文案再開始分析！")
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
