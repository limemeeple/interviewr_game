import time
from langchain_ollama import ChatOllama

llm = ChatOllama(
    model="gemma4:e4b",
    reasoning=False,
    num_ctx=8192,
    temperature=0.7,
)

prompt = "너는 면접관이다. 스마트팜 관련 면접 질문을 한 문장으로만 말해. 설명 없이 질문만."

for i in range(2):
    start = time.time()
    res = llm.invoke(prompt)
    meta = res.response_metadata
    print(f"[{i+1}번째] {time.time() - start:.1f}초")
    print("  질문:", res.content)
    print(f"  모델 로딩: {meta.get('load_duration', 0) / 1e9:.1f}초")
    print(f"  답 생성:   {meta.get('eval_duration', 0) / 1e9:.1f}초 ({meta.get('eval_count')}토큰)")