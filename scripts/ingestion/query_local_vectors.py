from __future__ import annotations

import argparse
import json
import pickle
import sqlite3
import sys
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Query local embedded qdrant data and reconstruct chunk text.",
    )
    parser.add_argument(
        "--qdrant-path",
        default="index/qdrant_local",
        help="Local embedded qdrant directory (default: index/qdrant_local).",
    )
    parser.add_argument("--collection", help="Collection name, e.g. col_tenant_a_app_a_bge_m3.")
    parser.add_argument("--job-id", help="Filter by payload.ingest_job_id.")
    parser.add_argument("--file-name", help="Filter by payload.file_name (contains match).")
    parser.add_argument("--doc-id", help="Filter by payload.doc_id.")
    parser.add_argument("--limit", type=int, default=20, help="Max points to print.")
    parser.add_argument("--show-vector", action="store_true", help="Show vector dim and vector head.")
    parser.add_argument(
        "--show-text",
        action="store_true",
        help="Show payload.chunk_text for each matched point (if available).",
    )
    parser.add_argument(
        "--text-max-chars",
        type=int,
        default=1000,
        help="Max characters for --show-text output per point.",
    )
    parser.add_argument(
        "--reconstruct",
        action="store_true",
        help="Reconstruct text by doc_id + chunk_order using payload.chunk_text.",
    )
    return parser.parse_args()


def load_meta(qdrant_path: Path) -> dict[str, Any]:
    meta_path = qdrant_path / "meta.json"
    if not meta_path.exists():
        raise FileNotFoundError(f"meta.json not found: {meta_path}")
    return json.loads(meta_path.read_text(encoding="utf-8"))


def select_collection(meta: dict[str, Any], name: str | None) -> str:
    collections = list((meta.get("collections") or {}).keys())
    if not collections:
        raise RuntimeError("no collections found")
    if name:
        if name not in collections:
            raise RuntimeError(f"collection '{name}' not found, available={collections}")
        return name
    if len(collections) == 1:
        return collections[0]
    raise RuntimeError(f"multiple collections found, please set --collection: {collections}")


def load_points(db_path: Path) -> list[dict[str, Any]]:
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.cursor()
        cur.execute("SELECT point FROM points")
        rows = cur.fetchall()
    finally:
        conn.close()

    points: list[dict[str, Any]] = []
    for (blob,) in rows:
        try:
            point = pickle.loads(blob)
        except Exception:
            continue
        points.append(
            {
                "id": getattr(point, "id", None),
                "vector": getattr(point, "vector", None),
                "payload": getattr(point, "payload", None) or {},
            }
        )
    return points


def matched(point: dict[str, Any], args: argparse.Namespace) -> bool:
    payload = point.get("payload") or {}
    if args.job_id and payload.get("ingest_job_id") != args.job_id:
        return False
    if args.doc_id and payload.get("doc_id") != args.doc_id:
        return False
    if args.file_name:
        file_name = str(payload.get("file_name") or "")
        if args.file_name not in file_name:
            return False
    return True


def print_points(points: list[dict[str, Any]], args: argparse.Namespace) -> None:
    print(f"[info] matched_points={len(points)}")
    missing_chunk_text = 0
    for idx, point in enumerate(points[: args.limit], start=1):
        payload = point.get("payload") or {}
        chunk_text = str(payload.get("chunk_text") or "")
        if not chunk_text:
            missing_chunk_text += 1
        row: dict[str, Any] = {
            "id": point.get("id"),
            "doc_id": payload.get("doc_id"),
            "file_name": payload.get("file_name"),
            "chunk_order": payload.get("chunk_order"),
            "chunk_source_ref": payload.get("chunk_source_ref"),
            "ingest_job_id": payload.get("ingest_job_id"),
            "chunk_text_preview": chunk_text[:200],
        }
        if args.show_vector:
            vector = point.get("vector") or []
            row["vector_dim"] = len(vector)
            row["vector_head"] = vector[:8]
        if args.show_text:
            row["chunk_text"] = chunk_text[: max(args.text_max_chars, 0)]
        print(f"[point:{idx}] {json.dumps(row, ensure_ascii=False)}")

    if args.show_text and missing_chunk_text > 0:
        print(
            f"[warn] {missing_chunk_text}/{min(len(points), args.limit)} points have empty chunk_text. "
            "Vectors cannot be reversed into original text; re-ingest with chunk_text payload enabled."
        )


def merge_with_overlap(chunks: list[str]) -> str:
    if not chunks:
        return ""
    merged = chunks[0]
    for chunk in chunks[1:]:
        max_overlap = min(len(merged), len(chunk), 200)
        overlap = 0
        for n in range(max_overlap, 0, -1):
            if merged[-n:] == chunk[:n]:
                overlap = n
                break
        merged += chunk[overlap:]
    return merged


def reconstruct(points: list[dict[str, Any]]) -> None:
    docs: dict[str, list[tuple[int, str, str]]] = {}
    for point in points:
        payload = point.get("payload") or {}
        doc_id = str(payload.get("doc_id") or "")
        if not doc_id:
            continue
        chunk_text = payload.get("chunk_text")
        if not isinstance(chunk_text, str) or not chunk_text.strip():
            continue
        order = int(payload.get("chunk_order") or 0)
        file_name = str(payload.get("file_name") or "")
        docs.setdefault(doc_id, []).append((order, chunk_text, file_name))

    if not docs:
        print("[warn] no payload.chunk_text found; re-ingest data after enabling chunk_text payload.")
        return

    for doc_id, chunks in docs.items():
        chunks.sort(key=lambda x: x[0])
        merged = merge_with_overlap([item[1] for item in chunks])
        file_name = chunks[0][2]
        print(f"\n[doc] doc_id={doc_id} file_name={file_name} chunk_count={len(chunks)}")
        print(merged[:4000])
        if len(merged) > 4000:
            print(f"...(truncated, total_chars={len(merged)})")


def main() -> int:
    args = parse_args()
    qdrant_path = Path(args.qdrant_path).resolve()
    meta = load_meta(qdrant_path)
    collection = select_collection(meta, args.collection)

    db_path = qdrant_path / "collection" / collection / "storage.sqlite"
    if not db_path.exists():
        print(f"[error] collection sqlite not found: {db_path}", file=sys.stderr)
        return 2

    points = load_points(db_path)
    filtered = [point for point in points if matched(point, args)]
    print(f"[info] collection={collection} total_points={len(points)}")
    print_points(filtered, args)
    if args.reconstruct:
        reconstruct(filtered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
