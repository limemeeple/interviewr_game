import re
import shutil
import uuid
from pathlib import Path

import chromadb
from PyPDF2 import PdfReader
from chromadb.utils import embedding_functions
from langchain_text_splitters import RecursiveCharacterTextSplitter
from sqlalchemy import text

from db import get_conn

MIN_CHUNK_LEN = 50              #청크 중 해당 숫자보다 적을 경우 없애는 기준
UPLOAD_DIR = Path("uploads")    #
UPLOAD_DIR.mkdir(exist_ok=True)

ollama_ef = embedding_functions.OllamaEmbeddingFunction(
    url="http://localhost:11434",
    model_name="bge-m3:latest"
)

client = chromadb.PersistentClient(path="./store")
coll = client.get_or_create_collection(
    name="document",
    embedding_function=ollama_ef,
    metadata={"hnsw:space": "cosine"}
)

# 텍스트 분리작업
text_splitter = RecursiveCharacterTextSplitter(
    chunk_size=400,         # 청킹할때 최대 길이
    chunk_overlap=50,       # 겹치는 구간 길이
    length_function=len     # 조각길이를 어떤걸로 셀지
)

def read_pages(path:str) -> list[tuple[int,str]]:
    p = Path(path)
    ext = p.suffix.lower()

    if ext == ".pdf":
        reader = PdfReader(p)
        pages = []
        for i, page in enumerate(reader.pages):
            text = (page.extract_text() or "").strip()
            if text:  # 빈 페이지는 건너뜀
                pages.append((i, text))
        return pages

    if ext == ".txt":
        for enc in ("utf-8", "cp949"):  # 윈도우 메모장 한글 파일 대비
            try:
                text = p.read_text(encoding=enc).strip()
                return [(1, text)] if text else []
            except UnicodeDecodeError:
                continue
        print("텍스트 인코딩을 알 수 없습니다")

    print(f"지원하지 않는 파일 형식: {ext}")

# 5. 문서 저장
def insert_document(document_id: int, user_id: str, path: str) -> int:
    pages = read_pages(path)

    ids, chunks, metas = [], [], []
    for page_no, text in pages:
        text = re.sub(r"[ \t]+", " ", text)
        for j, chunk in enumerate(text_splitter.split_text(text)):
            if len(chunk.strip()) < MIN_CHUNK_LEN:   # 짧은 조각 건너뜀
                continue
            ids.append(f"doc{document_id}_p{page_no}_{j}")
            chunks.append(chunk)
            metas.append({
                "document_id": document_id,
                "user_id": user_id,
                "page": page_no,
            })

    if not chunks:
        print("글자를 추출하지 못했습니다 (스캔 PDF일 수 있음)")

    coll.delete(where={"document_id": document_id})
    coll.upsert(ids=ids, documents=chunks, metadatas=metas)

    print(f"[문서 {document_id}] {len(pages)}페이지 → {len(chunks)}개 청크 저장")
    return len(chunks)


# 6. 검색 (선택한 문서들 안에서만)
def search_document(document_ids: list[int], query: str, n: int = 3) -> list[dict]:
    results = coll.query(
        query_texts=[query],
        n_results=n,
        where={"document_id": {"$in": document_ids}},
    )
    return [
        {"id": i, "text": d, "page": m["page"], "document_id": m["document_id"]}
        for i, d, m in zip(results["ids"][0], results["documents"][0], results["metadatas"][0])
    ]

def register_document(user_id: str, name: str, src_path: str) -> int:
    src = Path(src_path)

    # 업로드할 경로를 생성.
    # 파일을 업로드 할때 이름이 겹칠 수 있기 때문에 앞에 랜덤한 숫자를 넣어주게 함.
    saved_path = UPLOAD_DIR / f"{uuid.uuid4().hex[:8]}_{src.name}"
    shutil.copy(src, saved_path)

    with get_conn() as conn:

        result = conn.execute(
            text("""
                INSERT INTO doc_upload (user_id, name, file_name, file_path, status)
                VALUES (:user_id, :name, :file_name, :file_path, 'PROCESSING')
            """),
            {"user_id": user_id, "name": name,
             "file_name": src.name, "file_path": str(saved_path)},
        )
        conn.commit()
        document_id = result.lastrowid


        try:
            count = insert_document(document_id, user_id, str(saved_path))
            conn.execute(
                text("UPDATE doc_upload SET status='READY', chunk_count=:c WHERE id=:id"),
                {"c": count, "id": document_id},
            )
        except Exception as e:
            print(f"[문서 {document_id}] 처리 실패: {e}")
            conn.execute(
                text("UPDATE doc_upload SET status='FAILED' WHERE id=:id"),
                {"id": document_id},
            )
        conn.commit()

    return document_id



if __name__ == "__main__":
    doc_id = register_document('admin', "판다스 기초", "data/pandas.pdf")
    print("등록된 문서 id:", doc_id)

    for hit in search_document([doc_id], "데이터 앞부분 확인하는 함수"):
        print(f"[{hit['id']}] {hit['page']}페이지 | {hit['text'][:60]}")
