# cleanup.py
from g_load_doc import coll

for doc_id in [1, 2]:
    coll.delete(where={"document_id": doc_id})

ids = coll.get(include=[])["ids"]
print("남은 청크 수:", len(ids))
print("남은 문서 번호:", sorted({i.split("_")[0] for i in ids}))