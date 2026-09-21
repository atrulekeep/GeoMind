"""知识库检索（RAG，hybrid）

检索策略：
- 向量通道：本地 Ollama bge-large-zh（涉密不出本机），query 用官方检索指令前缀，
  文档侧标题加权、正文截断以控制 512 token 上下文
- BM25 通道：零依赖自实现，中文单字 + bigram
- 融合：两路分数在全部 chunk 上 min-max 归一化后加权（0.65 向量 + 0.35 BM25）

Ollama 不可用时降级为纯 BM25；向量缓存按版本号管理。
"""

import json
import math
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import httpx
import numpy as np

KB_DIR = Path(__file__).parent / 'knowledge'
CHUNKS_FILE = KB_DIR / 'chunks.json'
CACHE_FILE = KB_DIR / '.cache' / 'embeddings.v3.npy'
OLLAMA_EMBED_URL = 'http://localhost:11434/api/embeddings'
EMBED_MODEL = 'bge-large:latest'
# bge-large-zh-v1.5 官方推荐的检索侧指令
QUERY_PREFIX = '为这个句子生成表示以用于检索相关文章：'

VEC_WEIGHT, BM25_WEIGHT = 0.4, 0.6

_TOKEN_RE = re.compile(r'[a-z0-9]+|[\u4e00-\u9fff]')


def tokenize(text: str) -> list[str]:
    """英文整词 + 中文单字（BM25 对中文单字 + bigram 足够有效）"""
    tokens = _TOKEN_RE.findall(text.lower())
    han = ''.join(t for t in tokens if '\u4e00' <= t <= '\u9fff')
    tokens += [han[i:i + 2] for i in range(len(han) - 1)]
    return tokens


@lru_cache(maxsize=1)
def load_chunks() -> list[dict[str, Any]]:
    return json.loads(CHUNKS_FILE.read_text(encoding='utf-8'))


def _doc_embed_text(chunk: dict[str, Any]) -> str:
    """标题 + tags + 正文前 120 字。

    bge-large-zh 上下文仅 512 token，而 chunk 正文开头高度模板化
    （"用户说/需求形如"），长文本会导致嵌入坍塌；短而精的文本区分度最好。
    """
    tags = ' '.join(chunk.get('tags', []))
    return f"{chunk['title']} {chunk['title']} {tags} {chunk['text'][:120]}"


def _normalize(scores: np.ndarray) -> np.ndarray:
    lo, hi = float(np.min(scores)), float(np.max(scores))
    if hi - lo < 1e-9:
        return np.zeros_like(scores)
    return (scores - lo) / (hi - lo)


class BM25Retriever:
    def __init__(self, chunks: list[dict[str, Any]]) -> None:
        self.chunks = chunks
        docs = [tokenize(' '.join([c['title'], ' '.join(c.get('tags', [])), c['text']])) for c in chunks]
        k1, b = 1.5, 0.75
        n = len(docs)
        avgdl = sum(len(d) for d in docs) / max(n, 1)
        df: dict[str, int] = {}
        for doc in docs:
            for term in set(doc):
                df[term] = df.get(term, 0) + 1
        idf = {t: math.log(1 + (n - dfi + 0.5) / (dfi + 0.5)) for t, dfi in df.items()}
        tables = []
        for doc in docs:
            tf: dict[str, int] = {}
            for term in doc:
                tf[term] = tf.get(term, 0) + 1
            tables.append({
                t: idf.get(t, 0.0) * (count * (k1 + 1)) / (count + k1 * (1 - b + b * len(doc) / max(avgdl, 1)))
                for t, count in tf.items()
            })
        self._tables = tables

    def score_all(self, query: str) -> np.ndarray:
        q_terms = tokenize(query)
        return np.array(
            [sum(table.get(t, 0.0) for t in q_terms) for table in self._tables],
            dtype=np.float32,
        )


async def _embed_one(client: httpx.AsyncClient, text: str) -> np.ndarray | None:
    try:
        resp = await client.post(OLLAMA_EMBED_URL, json={'model': EMBED_MODEL, 'prompt': text[:2000]})
        if resp.status_code != 200:
            return None
        vec = np.asarray(resp.json().get('embedding') or [], dtype=np.float32)
        return vec if vec.shape[0] > 0 else None
    except Exception:
        return None


async def _load_or_build_doc_vectors(chunks: list[dict[str, Any]]) -> np.ndarray | None:
    if CACHE_FILE.exists():
        try:
            mat = np.load(CACHE_FILE)
            if mat.shape[0] == len(chunks):
                return mat
        except Exception:
            pass

    async with httpx.AsyncClient(timeout=30) as client:
        # 探测模型可用性
        if await _embed_one(client, '探测') is None:
            return None
        vectors = []
        for chunk in chunks:
            vec = await _embed_one(client, _doc_embed_text(chunk))
            if vec is None:
                return None
            vectors.append(vec)

    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    mat = np.asarray(vectors, dtype=np.float32)
    np.save(CACHE_FILE, mat)
    return mat


_doc_vectors: np.ndarray | None = None
_bm25: BM25Retriever | None = None
_backend: str | None = None


async def search_knowledge(query: str, k: int = 4) -> dict[str, Any]:
    """Agent 工具入口：hybrid（bge 向量 + BM25），Ollama 不可用时纯 BM25"""
    global _doc_vectors, _bm25, _backend
    chunks = load_chunks()
    if _bm25 is None:
        _bm25 = BM25Retriever(chunks)
    bm25_scores = _bm25.score_all(query)

    if _doc_vectors is None:
        _doc_vectors = await _load_or_build_doc_vectors(chunks)

    vec_scores: np.ndarray | None = None
    if _doc_vectors is not None:
        async with httpx.AsyncClient(timeout=8) as client:
            qv = await _embed_one(client, QUERY_PREFIX + query)
        if qv is not None and qv.shape[0] == _doc_vectors.shape[1]:
            vec_scores = _doc_vectors @ qv / (
                np.linalg.norm(_doc_vectors, axis=1) * np.linalg.norm(qv) + 1e-8
            )
            _backend = 'hybrid(bge-large-zh+bm25)'
        else:
            # 查询期 Ollama 不可用：本进程内放弃向量通道
            _doc_vectors = None
            _backend = 'bm25'
    else:
        _backend = 'bm25'

    if vec_scores is not None:
        fused = VEC_WEIGHT * _normalize(vec_scores) + BM25_WEIGHT * _normalize(bm25_scores)
    else:
        fused = bm25_scores

    order = np.argsort(-fused)[:k]
    results = []
    for i in order:
        if fused[int(i)] <= 0:
            continue
        c = chunks[int(i)]
        results.append({
            'id': c['id'], 'title': c['title'], 'category': c['category'],
            'text': c['text'], 'score': round(float(fused[int(i)]), 3),
        })
    return {'backend': _backend, 'query': query, 'results': results}
