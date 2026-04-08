"""
Vector Search Service with RAG

Implements hybrid search (vector + full text) for bug reports using:
- OpenAI text-embedding-3-small for vector embeddings
- TOON format for token-efficient data representation
- In-memory vector store with full-text search capabilities
"""

import json
import logging
import re
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime, timezone
from dataclasses import asdict
from pathlib import Path
import numpy as np
from openai import OpenAI
from ...observability.tracing import get_traced_client
from sklearn.metrics.pairwise import cosine_similarity
from .models import BugReportRecord

try:
    # Prefer the official python-toon library if installed
    from toon import encode as toon_encode  # pip install python-toon
except Exception:
    toon_encode = None

logger = logging.getLogger(__name__)


class VectorSearchService:
    """
    Hybrid vector + full-text search service for bug reports
    Uses OpenAI embeddings and TOON format for token optimization
    """
    
    def __init__(self, api_key: Optional[str] = None, storage_path: Optional[str] = None):
        """Initialize vector search service with JSON persistence"""
        self.client = get_traced_client(OpenAI(api_key=api_key))
        self.model = "text-embedding-3-small"
        self.embedding_dim = 1536
        # Resolve default path relative to the backend/ folder to avoid nested backend/backend paths
        # Path hierarchy: search_service.py -> search/ -> agents/ -> app/ -> backend/
        backend_dir = Path(__file__).resolve().parents[3]  # .../backend
        default_path = backend_dir / "data" / "embeddings.json"
        self.storage_path = Path(storage_path) if storage_path else default_path

        # In-memory storage
        self.records: Dict[str, BugReportRecord] = {}
        self.embeddings: Dict[str, np.ndarray] = {}
        self.full_text_index: Dict[str, List[str]] = {}  # word -> doc_ids

        # Load existing embeddings from JSON
        self._load_from_json()

        logger.info(f"VectorSearchService initialized with {len(self.records)} records from {self.storage_path}")
    
    def _tokenize_text(self, text: str) -> List[str]:
        """Tokenization for full-text search with punctuation removal"""
        # Convert to lowercase and remove punctuation
        text = text.lower()
        # Remove punctuation but keep spaces
        text = re.sub(r'[^\w\s]', ' ', text)
        # Split on whitespace and filter empty tokens
        tokens = [t for t in text.split() if t]
        return tokens
    
    def _build_full_text_index(self, doc_id: str, text: str):
        """Build full-text search index"""
        tokens = self._tokenize_text(text)
        for token in tokens:
            if token not in self.full_text_index:
                self.full_text_index[token] = []
            if doc_id not in self.full_text_index[token]:
                self.full_text_index[token].append(doc_id)
    
    def _normalize(self, vec: np.ndarray) -> np.ndarray:
        """L2-normalize a vector for cosine/dot similarity (per OpenAI Cookbook)."""
        norm = np.linalg.norm(vec)
        if norm == 0 or not np.isfinite(norm):
            return vec
        return vec / norm

    def _save_to_json(self):
        """Save embeddings and records to JSON file for persistence"""
        try:
            # Create directory if it doesn't exist
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)

            # Prepare data for JSON serialization
            data = {
                "records": {},
                "embeddings": {},
                "metadata": {
                    "model": self.model,
                    "embedding_dim": self.embedding_dim,
                    "last_updated": datetime.now(timezone.utc).isoformat(),
                    "total_records": len(self.records)
                }
            }

            # Serialize records and embeddings
            for doc_id, record in self.records.items():
                data["records"][doc_id] = {
                    "id": record.id,
                    "title": record.title,
                    "description": record.description,
                    "status": record.status,
                    "author": record.author,
                    "date": record.date,
                    "repros": record.repros,
                    "severity": record.severity,
                    "tags": record.tags,
                    "created_at": record.created_at
                }

                # Convert numpy array to list for JSON
                if doc_id in self.embeddings:
                    data["embeddings"][doc_id] = self.embeddings[doc_id].tolist()

            # Write to file
            with open(self.storage_path, 'w') as f:
                json.dump(data, f, indent=2)

            logger.info(f"Saved {len(self.records)} records to {self.storage_path}")
        except Exception as e:
            logger.error(f"Failed to save embeddings to JSON: {e}")

    def _load_from_json(self):
        """Load embeddings and records from JSON file"""
        try:
            if not self.storage_path.exists():
                logger.info(f"No existing embeddings file at {self.storage_path}")
                return

            with open(self.storage_path, 'r') as f:
                data = json.load(f)

            # Load records
            for doc_id, record_data in data.get("records", {}).items():
                self.records[doc_id] = BugReportRecord(**record_data)

            # Load embeddings (convert list back to numpy array)
            for doc_id, embedding_list in data.get("embeddings", {}).items():
                self.embeddings[doc_id] = np.array(embedding_list, dtype=np.float32)

            # Rebuild full-text index
            for doc_id, record in self.records.items():
                search_text = f"{record.title} {record.description}"
                self._build_full_text_index(doc_id, search_text)

            metadata = data.get("metadata", {})
            logger.info(f"Loaded {len(self.records)} records from {self.storage_path} (last updated: {metadata.get('last_updated', 'unknown')})")
        except Exception as e:
            logger.error(f"Failed to load embeddings from JSON: {e}")

    def _to_toon_text(self, record: 'BugReportRecord') -> str:
        """Serialize record into compact TOON text for token-efficient embedding."""
        payload = {
            "id": record.id,
            "title": record.title,
            "description": record.description,
            "status": record.status,
            "author": record.author,
            "date": record.date,
            "repros": record.repros,
            "severity": record.severity,
            "tags": record.tags or [],
        }
        try:
            if toon_encode is not None:
                # Compact options; delimiter="," keeps default minimal form
                return toon_encode(payload, {"indent": 0})
        except Exception:
            pass
        # Fallback to compact JSON if python-toon is unavailable
        return json.dumps(payload, separators=(",", ":"))

    def _get_embedding(self, text: str) -> List[float]:
        """Get embedding from OpenAI and return as normalized list."""
        try:
            response = self.client.embeddings.create(
                input=text,
                model=self.model
            )
            emb = np.array(response.data[0].embedding, dtype=np.float32)
            emb = self._normalize(emb)
            return emb.tolist()
        except Exception as e:
            logger.error(f"Error getting embedding: {e}")
            raise
    
    def upsert(self, record: BugReportRecord) -> Dict[str, Any]:
        """
        Upsert a bug report record with embedding
        
        Args:
            record: BugReportRecord to upsert
            
        Returns:
            Upsert result with metadata
        """
        try:
            # Build compact text (TOON) for embedding
            text_to_embed = self._to_toon_text(record)

            # Get embedding (normalized)
            embedding = self._get_embedding(text_to_embed)
            record.embedding = embedding

            # Store record and normalized embedding
            self.records[record.id] = record
            self.embeddings[record.id] = np.array(embedding, dtype=np.float32)

            # Build full-text index on richer fields (include id for unique recall)
            indexing_text = f"{record.id} {record.title} {record.description} {record.author} {record.status} {record.severity} {' '.join(record.tags or [])}"
            self._build_full_text_index(record.id, indexing_text)

            # Save to JSON for persistence
            self._save_to_json()

            logger.info(f"Upserted record: {record.id}")

            return {
                "success": True,
                "id": record.id,
                "message": f"Record {record.id} upserted successfully",
                "embedding_dim": len(embedding)
            }
        except Exception as e:
            logger.error(f"Error upserting record: {e}")
            return {
                "success": False,
                "id": record.id,
                "error": str(e)
            }
    
    def delete(self, record_id: str) -> Dict[str, Any]:
        """
        Delete a bug report record

        Args:
            record_id: ID of record to delete

        Returns:
            Delete result
        """
        try:
            if record_id not in self.records:
                return {
                    "success": False,
                    "id": record_id,
                    "error": f"Record {record_id} not found"
                }

            # Remove from all indices
            del self.records[record_id]
            if record_id in self.embeddings:
                del self.embeddings[record_id]

            # Remove from full-text index - clean up all references
            for token in list(self.full_text_index.keys()):
                if record_id in self.full_text_index[token]:
                    self.full_text_index[token].remove(record_id)
                # Clean up empty token entries
                if not self.full_text_index[token]:
                    del self.full_text_index[token]

            # Save to JSON for persistence
            self._save_to_json()

            logger.info(f"Deleted record: {record_id}")

            return {
                "success": True,
                "id": record_id,
                "message": f"Record {record_id} deleted successfully"
            }
        except Exception as e:
            logger.error(f"Error deleting record: {e}")
            return {
                "success": False,
                "id": record_id,
                "error": str(e)
            }
    
    def query(
        self,
        query_text: str,
        k: int = 3,
        alpha: float = 0.7
    ) -> List[Dict[str, Any]]:
        """
        Hybrid search: vector + full-text
        
        Args:
            query_text: Query text
            k: Number of results to return
            alpha: Weight for vector search (1-alpha for full-text)
            
        Returns:
            List of top K results with scores
        """
        try:
            if not self.records:
                return []
            
            # Vector search (normalize query; embeddings already normalized)
            query_embedding = np.array(self._get_embedding(query_text), dtype=np.float32)
            vector_scores = {}

            for doc_id, embedding in self.embeddings.items():
                # With normalized vectors, dot product == cosine similarity
                similarity = float(np.dot(query_embedding, embedding))
                vector_scores[doc_id] = similarity
            
            # Full-text search with improved scoring
            query_tokens = self._tokenize_text(query_text)
            full_text_scores = {}

            # Calculate IDF (inverse document frequency) for each query token
            idf_scores = {}
            total_docs = len(self.records)
            for token in query_tokens:
                if token in self.full_text_index:
                    doc_count = len(self.full_text_index[token])
                    # IDF = log(total_docs / doc_count)
                    idf_scores[token] = np.log(total_docs / max(doc_count, 1)) if doc_count > 0 else 0
                else:
                    idf_scores[token] = 0

            # Calculate TF-IDF score for each document
            for doc_id in self.records:
                score = 0
                for token in query_tokens:
                    if token in self.full_text_index and doc_id in self.full_text_index[token]:
                        # TF = 1 (token present), IDF = log(total/doc_count)
                        score += idf_scores[token]

                # Normalize by max possible IDF score
                max_idf = sum(idf_scores.values()) if query_tokens else 1
                full_text_scores[doc_id] = score / max(max_idf, 1)
            
            # Combine scores
            combined_scores = {}
            for doc_id in self.records:
                combined_scores[doc_id] = (
                    alpha * vector_scores.get(doc_id, 0) +
                    (1 - alpha) * full_text_scores.get(doc_id, 0)
                )
            
            # Sort and return top K
            sorted_results = sorted(
                combined_scores.items(),
                key=lambda x: x[1],
                reverse=True
            )[:k]
            
            results = []
            for doc_id, score in sorted_results:
                record = self.records[doc_id]
                results.append({
                    "id": record.id,
                    "title": record.title,
                    "description": record.description,
                    "status": record.status,
                    "author": record.author,
                    "date": record.date,
                    "repros": record.repros,
                    "severity": record.severity,
                    "tags": record.tags,
                    "score": score,
                    "vector_score": vector_scores.get(doc_id, 0),
                    "text_score": full_text_scores.get(doc_id, 0)
                })
            
            logger.info(f"Query returned {len(results)} results")
            return results
            
        except Exception as e:
            logger.error(f"Error querying: {e}")
            return []
    
    def get_stats(self) -> Dict[str, Any]:
        """Get service statistics"""
        return {
            "total_records": len(self.records),
            "embedding_model": self.model,
            "embedding_dim": self.embedding_dim,
            "full_text_index_size": len(self.full_text_index),
            "storage_path": str(self.storage_path)
        }

    def get_all_records(self) -> List[Dict[str, Any]]:
        """Get all records with their embeddings for display"""
        results = []
        for doc_id, record in self.records.items():
            results.append({
                "id": record.id,
                "title": record.title,
                "description": record.description,
                "status": record.status,
                "author": record.author,
                "date": record.date,
                "repros": record.repros,
                "severity": record.severity,
                "tags": record.tags,
                "created_at": record.created_at,
                "has_embedding": doc_id in self.embeddings,
                "embedding_preview": self.embeddings[doc_id][:5].tolist() if doc_id in self.embeddings else None
            })
        return results

