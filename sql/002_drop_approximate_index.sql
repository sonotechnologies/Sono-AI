-- The ivfflat index from 001 is an *approximate* nearest-neighbor index.
-- With only ~6,000 rows split across 100 lists (~60 rows/list) and pgvector's
-- default of probing just 1 list per query, similar_to() was searching roughly
-- 1% of the catalog and missing true nearest neighbors (verified: Breaking Bad
-- <-> Better Call Saul cosine similarity is 0.57, but the indexed query never
-- surfaced it).
--
-- At this catalog size, exact brute-force search over the embedding column is
-- still fast (well under what matters for a search page), so drop the index
-- rather than tune probes. Revisit with an HNSW index (better recall than
-- ivfflat) once the catalog grows into the tens of thousands of rows.

drop index if exists titles_embedding_idx;
