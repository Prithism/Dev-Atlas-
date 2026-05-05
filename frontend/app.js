import { API_BASE } from "./modules/config.js";
import { queryAtlas, readableQueryError } from "./modules/api.js";
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

async function handleSearch() {
  const rawQuery = dom.qInput.value.trim();
  if (!rawQuery) return;

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
    if (event.key === "Enter") handleSearch();
    if (event.key === "Escape") dom.qInput.value = "";
  });
  dom.qInput.addEventListener("focus", () => dom.qInput.select());
  window.addEventListener("resize", graphRenderer.handleResize);

  const resizeObserver = new ResizeObserver(() => graphRenderer.handleResize());
  resizeObserver.observe(dom.graphStage);

  dom.qInput.value = "";
  dom.qInput.focus();
  setDataSourceBadge("ready");
  setGraphStatus("Awaiting query");
}

window.addEventListener("DOMContentLoaded", init);

// Keeps the legacy /app.js smoke test anchored to the live backend constant.
void API_BASE;
