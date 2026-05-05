import { COMMUNITY_ORDER } from "./config.js";

export const dom = {
  qInput: document.getElementById("q"),
  searchBtn: document.getElementById("search-btn"),
  resultsList: document.getElementById("results-list"),
  loadingIndicator: document.getElementById("loading"),
  graphStage: document.getElementById("graph-stage"),
  vizContainer: document.getElementById("viz"),
  resultCount: document.getElementById("result-count"),
  graphState: document.getElementById("graph-state"),
  nodeCount: document.getElementById("node-count"),
  linkCount: document.getElementById("link-count"),
  focusLabel: document.getElementById("focus-label"),
  dataSourceBadge: document.getElementById("data-source-badge"),
};

const DATA_SOURCE_LABEL = {
  live: "Live data",
  error: "Live data failed",
  loading: "Searching...",
  ready: "Ready",
};

export function setGraphStatus(text) {
  dom.graphState.textContent = text;
}

export function setGraphMetrics(nodes = 0, links = 0) {
  dom.nodeCount.textContent = String(nodes);
  dom.linkCount.textContent = String(links);
}

export function setFocusLabel(text) {
  dom.focusLabel.textContent = `Focus: ${text}`;
}

export function setResultCount(total) {
  dom.resultCount.textContent = `${total} result${total === 1 ? "" : "s"}`;
}

export function setLoading(isLoading) {
  dom.loadingIndicator.classList.toggle("hidden", !isLoading);
}

export function updateCommunityGuides(model) {
  COMMUNITY_ORDER.forEach((communityKey) => {
    const chip = document.getElementById(`cluster-${communityKey}`);
    const count = document.getElementById(`cluster-${communityKey}-count`);
    if (chip) {
      chip.classList.toggle("active", model.focusCommunity === communityKey);
    }
    if (count) {
      count.textContent = String(model.communityCounts[communityKey] || 0);
    }
  });
}

export function setDataSourceBadge(source) {
  const badge = dom.dataSourceBadge;
  if (!badge) return;
  badge.className = `data-source-badge ${source}`;
  badge.textContent = DATA_SOURCE_LABEL[source] || DATA_SOURCE_LABEL.loading;
}
