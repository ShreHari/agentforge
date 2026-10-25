"""Tools the research agent can use. A tool is a plain function the AI may decide to call.

The AI never sees this code. It sees each tool's NAME, type hints and DOCSTRING, so the
docstring is instructions for the AI.
"""

from functools import lru_cache

from tavily import TavilyClient

MAX_RESULTS = 3  # results per search
MAX_SNIPPET_CHARS = 500  # cut long snippets: less to pay for, less room for injected text


@lru_cache  # create the client on first use, then reuse it
def get_tavily() -> TavilyClient:
    # Created lazily, not at import time, so fake mode and tests never need a Tavily key.
    return TavilyClient()  # reads TAVILY_API_KEY from the environment


def search_web(query: str) -> str:
    """Search the web. Returns the top results, each with a title, URL and snippet."""
    response = get_tavily().search(query, max_results=MAX_RESULTS)
    blocks = []
    for r in response["results"]:
        snippet = r["content"][:MAX_SNIPPET_CHARS]
        blocks.append(f"Title: {r['title']}\nURL: {r['url']}\nSnippet: {snippet}")
    if not blocks:
        return "No results."
    return "\n\n".join(blocks)
