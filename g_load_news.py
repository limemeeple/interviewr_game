import chromadb
from chromadb.utils import embedding_functions
from sqlalchemy import text

from db import get_conn

ollama_ef = embedding_functions.OllamaEmbeddingFunction(
    url="http://localhost:11434",
    model_name="bge-m3:latest"
)

client = chromadb.PersistentClient(path="./store")

#컬렉션을 열고 없으면 만든다.
newscoll = client.get_or_create_collection(
    name = "news",                      # news라는 컬렉션
    embedding_function= ollama_ef,      # 글자를 벡터로 바꿀때 사용할 모델
    metadata={"hnsw:space" : "cosine"}  # 벡터끼리 얼마나 비슷한지 재는 방법(hnsw는 색인, cosine는 거리재는 방법)
)

def sync_news() -> int:
    existing = set(newscoll.get(include=[])["ids"])

    sql = text("SELECT a.id, a.company_id, c.industry, a.title, a.press, a.pub_date FROM news_articles a JOIN companies c ON c.id = a.company_id")

    with get_conn() as conn:
        rows = conn.execute(sql).mappings().all()

    new_rows = [r for r in rows if f"news_{r['id']}" not in existing]
    if not new_rows:
        print("새로 넣을 기사가 없습니다")
        return 0

    newscoll.add(
        ids=[f"news_{r['id']}" for r in new_rows],
        documents=[r["title"] for r in new_rows],
        metadatas=[{
            "article_id": r["id"],
            "company_id": r["company_id"],
            "industry": r["industry"],
            "press": r["press"] or "",
            "pub_ts": int(r["pub_date"].timestamp()),
        } for r in new_rows],
    )
    print(f"기사 {len(new_rows)}건 벡터 저장")
    return len(new_rows)


if __name__ == "__main__":
    sync_news()

    res = newscoll.query(
        query_texts=["청년 농업인 지원"],
        n_results=3,
        where={"company_id": 1},
    )
    for i, d, m in zip(res["ids"][0], res["documents"][0], res["metadatas"][0]):
        print(f"[{i}] {m['press']} | {d}")