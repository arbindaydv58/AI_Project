"""Tools for long-term memory and document search."""

from __future__ import annotations

from ..registry import S, tool


@tool("remember", "Save a durable fact about the user or their preferences to long-term memory.",
      {"fact": S("A short, self-contained fact, e.g. 'User prefers metric units'")}, ["fact"])
def t_remember(ag, fact: str):
    fid = ag.app.memory.add_fact(fact)
    return f"Saved to memory (id {fid}): {fact}"


@tool("recall", "Search long-term memory for facts about the user.",
      {"query": S("What to look for")}, ["query"])
def t_recall(ag, query: str):
    hits = ag.app.memory.search_facts(query, 8)
    return "\n".join(f"[{r['id']}] {r['text']}" for r, _ in hits) or "Nothing relevant in memory."


@tool("forget", "Delete a fact from long-term memory by id.", {"id": S("Fact id", "integer")}, ["id"], risky=True)
def t_forget(ag, id: int):
    return "Forgotten." if ag.app.memory.forget(int(id)) else f"No fact with id {id}."


@tool("search_docs", "Search the user's indexed documents (added with `jarvis ingest`). Returns passages "
      "with their source file. Cite sources in your answer.",
      {"query": S("What to look for"), "k": S("Number of passages (default 5)", "integer")}, ["query"])
def t_search_docs(ag, query: str, k: int = 5):
    hits = ag.app.memory.search_docs(query, max(1, min(int(k or 5), 12)))
    if not hits:
        nfiles, _ = ag.app.memory.doc_stats()
        return "No matching passages." + ("" if nfiles else " (No documents indexed yet: run `jarvis ingest <folder>`.)")
    return "\n\n".join(f"[{i}] source: {r['path']} (part {r['idx'] + 1})\n{r['text']}" for i, (r, _) in enumerate(hits, 1))
