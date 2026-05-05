import { API_BASE, QUERY_TIMEOUT_MS, GRAPH_TIMEOUT_MS } from "./config.js";

export async function queryAtlas(query) {
  const controller = new AbortController();
  const timeoutId = window.setTimeout(() => controller.abort(), QUERY_TIMEOUT_MS);

  try {
    const response = await fetch(`${API_BASE}/query`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ q: query.toLowerCase() }),
      signal: controller.signal,
    });

    if (!response.ok) {
      const detail = await response.text().catch(() => response.statusText);
      throw new Error(`Backend error ${response.status}: ${detail}`);
    }

    return await response.json();
  } finally {
    window.clearTimeout(timeoutId);
  }
}

export async function fetchFullGraph(maxNodes = 350) {
  const controller = new AbortController();
  const timeoutId = window.setTimeout(() => controller.abort(), GRAPH_TIMEOUT_MS);

  try {
    const response = await fetch(`${API_BASE}/graph?max_nodes=${maxNodes}`, {
      method: "GET",
      signal: controller.signal,
    });

    if (!response.ok) {
      const detail = await response.text().catch(() => response.statusText);
      throw new Error(`Backend error ${response.status}: ${detail}`);
    }

    return await response.json();
  } finally {
    window.clearTimeout(timeoutId);
  }
}

export function readableQueryError(error) {
  return error.name === "AbortError"
    ? "request timed out after 45s"
    : error.message;
}
