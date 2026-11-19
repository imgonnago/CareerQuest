from engine.rag.store import search, get_document
import json

def data_split(split: str = "dev"):
    if split == "dev":
        data = json.load(open("eval/data/rag_dev.json", encoding="utf-8"))
        return data
    else:
        data = json.load(open("eval/data/rag_test.json", encoding="utf-8"))
        return data

def main() -> None:
    data = data_split(split="test")
    for d in data:
        rag = search(d["question"], k=10, min_score=0.55, chunks_per_doc=1)
        ids = [r.source_document_id for r in rag]
        answers = set(d["answer_document_ids"])   # 정답 문서가 여러 개일 수 있음 → 가장 먼저 나온 정답의 순위
        rank = next((i for i, x in enumerate(ids, 1) if x in answers), 0)
        print(d["id"], "정답 순위:", rank)
        for i, r in enumerate(rag[:3], 1):
            print(f"{i}. [{r.source_document_id}] {r.title} 유사도 : {r.score:.3f}")
            print(f"근거: {r.chunks[0].content[:80]}")
            print("\n")

if __name__ == "__main__":
    main()