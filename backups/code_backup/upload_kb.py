import os
import time
from dotenv import load_dotenv
from google import genai

load_dotenv()
raw_keys = os.getenv("GEMINI_API_KEYS") or os.getenv("GEMINI_API_KEY") or ""
API_KEYS = [k.strip() for k in raw_keys.split(",") if k.strip()]

kb_dir = "kb_docs"
files = sorted([
    os.path.join(kb_dir, f) for f in os.listdir(kb_dir)
    if f.lower().endswith((".docx", ".pdf")) and not f.startswith("~$")
])

if not API_KEYS or not files:
    print("❌ 找不到 API Keys 或 kb_docs 檔案！")
    exit(1)

print(f"📂 準備上傳 {len(files)} 份知識庫檔案至 {len(API_KEYS)} 組 API Key 雲端空間...")
store_names = []

for idx, key in enumerate(API_KEYS, start=1):
    print(f"\n🔑 正在處理第 {idx}/{len(API_KEYS)} 組金鑰...")
    try:
        client = genai.Client(api_key=key)
        try:
            for old_s in client.file_search_stores.list():
                if getattr(old_s, "display_name", "").startswith("greenwash_kb"):
                    client.file_search_stores.delete(name=old_s.name, config={"force": True})
                    print(f"   🧹 已清理舊知識庫：{old_s.name}")
        except Exception:
            pass

        store = client.file_search_stores.create(config={"display_name": f"greenwash_kb_key{idx}"})
        for fpath in files:
            fname = os.path.basename(fpath)
            op = client.file_search_stores.upload_to_file_search_store(
                file=fpath,
                file_search_store_name=store.name,
                config={"display_name": fname}
            )
            while not op.done:
                time.sleep(2)
                op = client.operations.get(op)
            print(f"   ✅ 已建立索引：{fname}")
        store_names.append(store.name)
        print(f"   🎉 第 {idx} 組知識庫就緒：{store.name}")
    except Exception as e:
        print(f"   ⚠️ 第 {idx} 組金鑰上傳失敗（將跳過此節點）：{e}")
        store_names.append("")

env_path = ".env"
lines = []
if os.path.exists(env_path):
    with open(env_path, "r", encoding="utf-8") as f:
        lines = [line for line in f.readlines() if not line.startswith(("STORE_NAME=", "STORE_NAMES="))]

first_valid = next((s for s in store_names if s), "")
lines.append(f"\nSTORE_NAME={first_valid}\n")
lines.append(f"STORE_NAMES={','.join(store_names)}\n")

with open(env_path, "w", encoding="utf-8") as f:
    f.writelines(lines)

print("\n🚀 全部完成！所有雲端知識庫 ID 已自動寫入 .env 設定檔！")