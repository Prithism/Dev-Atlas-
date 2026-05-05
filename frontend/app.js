import { API_BASE, INITIAL_GRAPH_NODES } from "./modules/config.js";
import { fetchFullGraph, queryAtlas, readableQueryError } from "./modules/api.js";
import {
  dom,
  setDataSourceBadge,
  setFocusLabel,
  setGraphMetrics,
  setGraphStatus,
  setLoading,
  setResultCount,
  updateCommunityGuides,
} from "./modules/dom.js";
import { createGraphRenderer } from "./modules/graphRenderer.js";
import { renderResults, setActiveResultCard } from "./modules/results.js";
import { escapeHtml } from "./modules/utils.js";

let currentQuery = "";
let viewMode = "overview"; // "overview" | "query"

const graphRenderer = createGraphRenderer({
  onNodeFocus: setActiveResultCard,
});

function renderSearchError(message, query = currentQuery) {
  const safeMessage = escapeHtml(message);

  graphRenderer.clear();
  setLoading(false);
  setResultCount(0);
  setGraphMetrics(0, 0);
  setGraphStatus(`Live data unavailable — ${query || "search"} (${message})`);
  setFocusLabel("none");
  dom.resultsList.innerHTML = `
    <div class="empty-state">
      Could not load live GitHub-backed results.<br>
      <small>${safeMessage}</small>
    </div>
  `;
  graphRenderer.renderStageMessage(`
    <div class="empty-state">
      Graph view is unavailable because the backend request failed.<br>
      <small>Run the API and rebuild the atlas data if needed.</small>
    </div>
  `);
  updateCommunityGuides({
    focusCommunity: "people",
    communityCounts: { people: 0, projects: 0, events: 0 },
  });
}

function renderOverviewError(message) {
  const safeMessage = escapeHtml(message);
  graphRenderer.clear();
  setLoading(false);
  setResultCount(0);
  setGraphMetrics(0, 0);
  setGraphStatus(`Atlas overview unavailable — ${message}`);
  setFocusLabel("none");
  dom.resultsList.innerHTML = `
    <div class="empty-state">
      Could not load the atlas overview.<br>
      <small>${safeMessage}</small>
    </div>
  `;
  graphRenderer.renderStageMessage(`
    <div class="empty-state">
      Graph view is unavailable because the backend request failed.<br>
      <small>Start the API (uvicorn atlas.main:app) and rebuild data if needed.</small>
    </div>
  `);
}

async function loadOverview() {
  viewMode = "overview";
  currentQuery = "";
  setLoading(true);
  setDataSourceBadge("loading");
  setGraphStatus("Loading complete Kolkata atlas...");
  setFocusLabel("none");
  setResultCount(0);
  setGraphMetrics(0, 0);
  dom.resultsList.innerHTML = `
    <div class="empty-state">
      Showing the complete Kolkata atlas.<br>
      <small>Type a query to focus on a specific cluster, or click any node in the graph.</small>
    </div>
  `;

  try {
    const data = await fetchFullGraph(INITIAL_GRAPH_NODES);
    setDataSourceBadge("live");
    const subgraph = data.subgraph || { nodes: [], edges: [] };
    graphRenderer.renderGraph(subgraph, [], "");
    const nodeTotal = typeof data.node_total === "number" ? data.node_total : subgraph.nodes.length;
    const edgeTotal = typeof data.edge_total === "number" ? data.edge_total : subgraph.edges.length;
    const showingAll = subgraph.nodes.length >= nodeTotal;
    setGraphStatus(
      showingAll
        ? `Atlas overview — ${subgraph.nodes.length} of ${nodeTotal} nodes`
        : `Atlas overview — top ${subgraph.nodes.length} of ${nodeTotal} nodes by centrality`
    );
    setLoading(false);
  } catch (error) {
    const reason = readableQueryError(error);
    console.warn(`Overview load failed (${reason})`);
    setDataSourceBadge("error");
    renderOverviewError(reason);
  }
}

async function handleSearch() {
  const rawQuery = dom.qInput.value.trim();
  if (!rawQuery) {
    // Empty query => return to overview.
    await loadOverview();
    return;
  }

  viewMode = "query";
  currentQuery = rawQuery;
  setLoading(true);
  setDataSourceBadge("loading");
  setGraphStatus(`Scanning atlas — ${rawQuery}`);
  setFocusLabel("none");
  setResultCount(0);
  setGraphMetrics(0, 0);
  dom.resultsList.innerHTML = '<div class="empty-state">Reading query and regrouping communities...</div>';

  try {
    const data = await queryAtlas(rawQuery);
    setDataSourceBadge("live");
    renderResults(data.results || [], {
      onResultFocus: graphRenderer.focusOnNode,
    });
    graphRenderer.renderGraph(data.subgraph, data.results || [], rawQuery);
  } catch (error) {
    const reason = readableQueryError(error);
    console.warn(`Backend unavailable (${reason}), live data not rendered`);
    setDataSourceBadge("error");
    renderSearchError(reason, rawQuery);
  }
}

function init() {
  dom.searchBtn.addEventListener("click", handleSearch);
  dom.qInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      handleSearch();
    } else if (event.key === "Escape") {
      // Escape clears the query AND returns to the full atlas overview.
      dom.qInput.value = "";
      if (viewMode !== "overview") loadOverview();
    }
  });
  dom.qInput.addEventListener("focus", () => dom.qInput.select());
  window.addEventListener("resize", graphRenderer.handleResize);

  const resizeObserver = new ResizeObserver(() => graphRenderer.handleResize());
  resizeObserver.observe(dom.graphStage);

  dom.qInput.value = "";
  dom.qInput.focus();

  // Load the full atlas as the initial view instead of an empty stage.
  loadOverview();
}

window.addEventListener("DOMContentLoaded", init);

// Keeps the legacy /app.js smoke test anchored to the live backend constant.
void API_BASE;
