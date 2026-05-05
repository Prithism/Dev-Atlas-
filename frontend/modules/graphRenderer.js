import { COMMUNITY_NAMES, EDGE_ACCENT, EDGE_HIGHLIGHT } from "./config.js";
import {
  setFocusLabel,
  setGraphMetrics,
  setGraphStatus,
  setLoading,
  updateCommunityGuides,
  dom,
} from "./dom.js";
import { buildSearchModel, getLayoutProfile, seedNodePositions } from "./graphModel.js";
import { drawNode } from "./canvasDraw.js";
import { clamp } from "./utils.js";



function linkEndpointId(endpoint) {
  return typeof endpoint === "object" ? endpoint.id : endpoint;
}

export function createGraphRenderer({ onNodeFocus } = {}) {
  let graph = null;
  let highlightedNodeId = null;
  let currentModel = null;
  let resizeFitTimer = null;

  const isHighlightedLink = (link) => {
    if (!highlightedNodeId) return false;
    return linkEndpointId(link.source) === highlightedNodeId
      || linkEndpointId(link.target) === highlightedNodeId;
  };

  const drawNodeCanvas = (node, ctx, scale) =>
    drawNode(node, ctx, scale, {
      highlightedNodeId,
      adjacency: currentModel?.adjacency,
    });

  function getLinkColor(link) {
    if (isHighlightedLink(link)) return "rgba(255, 93, 115, 0.95)";
    if (link.isResultBridge) return "rgba(59, 130, 246, 0.72)";
    if (highlightedNodeId) return "rgba(80, 80, 80, 0.08)";
    return link.isInternalCommunity ? "rgba(20, 20, 20, 0.22)" : "rgba(110, 110, 110, 0.16)";
  }

  function getLinkWidth(link) {
    if (isHighlightedLink(link)) return 2.8;
    return link.isResultBridge ? 1.8 : 0.8;
  }

  function fitGraphView(duration = 900, padding = 64) {
    if (!graph) return;
    if (typeof graph.zoomToFit === "function") {
      graph.zoomToFit(duration, padding);
    } else {
      graph.centerAt(0, 0, duration);
      graph.zoom(1.8, duration);
    }
  }



  function syncGraphHighlight() {
    if (!graph) return;
    graph.nodeCanvasObject(drawNodeCanvas);
    graph.linkColor(getLinkColor);
    graph.linkWidth(getLinkWidth);
    graph.linkDirectionalParticles((link) => {
      if (isHighlightedLink(link)) return 4;
      return link.isResultBridge ? 1 : 0;
    });
    graph.linkDirectionalParticleWidth((link) => (isHighlightedLink(link) ? 3 : 1.8));
    graph.linkDirectionalParticleColor((link) => (isHighlightedLink(link) ? EDGE_HIGHLIGHT : EDGE_ACCENT));
    graph.linkDirectionalParticleSpeed((link) => (isHighlightedLink(link) ? 0.01 : 0.004));
    graph.refresh?.();
  }

  function flyToNode(node) {
    if (!graph || !node) return;
    graph.centerAt?.(node.x || 0, node.y || 0, 700);
    graph.zoom?.(2.8, 700);
  }

  function clear() {
    graph = null;
    currentModel = null;
    highlightedNodeId = null;
    dom.vizContainer.innerHTML = "";
  }

  function renderStageMessage(html) {
    dom.vizContainer.innerHTML = `<div class="graph-stage-message">${html}</div>`;
  }

  function configureForces(layout, model) {
    graph.d3Force("center", null);
    graph.d3Force("charge").strength(layout.charge);
    graph.d3Force("link").distance((link) =>
      clamp(layout.distance - link.bridgeStrength * 10, 60, 200));

    const { centers, clusterPull } = layout;
    graph.d3Force("cluster", (alpha) => {
      model.nodes.forEach((node) => {
        const anchor = centers[node.communityKey] || centers.people;
        const emphasis = node.isResult ? 1.6 : 1.0;
        const k = clusterPull * alpha * emphasis;
        node.vx = (node.vx || 0) + (anchor.x - (node.x || 0)) * k;
        node.vy = (node.vy || 0) + (anchor.y - (node.y || 0)) * k;
      });
    });
    graph.d3VelocityDecay(0.38);
  }

  function handleNodeClick(node) {
    highlightedNodeId = node.id;
    syncGraphHighlight();
    onNodeFocus?.(node.id);
    setFocusLabel(node.label || node.id);
    document.getElementById(`result-${node.id}`)
      ?.scrollIntoView({ behavior: "smooth", block: "nearest" });
    flyToNode(node);
  }

  function renderGraph(subgraph, results = [], query = "") {
    if (!subgraph || !Array.isArray(subgraph.nodes) || !subgraph.nodes.length) {
      clear();
      setLoading(false);
      setGraphStatus("No graph data");
      setGraphMetrics(0, 0);
      renderStageMessage('<div class="empty-state">No graph data for this query.</div>');
      return;
    }

    const model = buildSearchModel(subgraph, results, query);
    const layout = getLayoutProfile(model, dom.vizContainer);
    seedNodePositions(model, layout);
    currentModel = model;
    highlightedNodeId = null;
    dom.vizContainer.innerHTML = "";

    if (typeof window.ForceGraph !== "function") {
      renderStageMessage('<div class="empty-state">Graph view unavailable right now. Results are still usable while the graph library is offline.</div>');
      setGraphMetrics(model.nodes.length, model.links.length);
      setGraphStatus("Graph library unavailable");
      updateCommunityGuides(model);
      setLoading(false);
      return;
    }

    try {
      graph = window.ForceGraph()(dom.vizContainer)
        .backgroundColor("rgba(0,0,0,0)")
        .width(dom.vizContainer.clientWidth)
        .height(dom.vizContainer.clientHeight)
        .nodeCanvasObject(drawNodeCanvas)
        .nodeCanvasObjectMode(() => "replace")
        .nodeLabel((node) => `${node.label || node.id} (${node.type})`)
        .linkColor(getLinkColor)
        .linkWidth(getLinkWidth)
        .linkDirectionalParticles((link) => {
          if (isHighlightedLink(link)) return 5;
          return link.isResultBridge ? 2 : 0;
        })
        .linkDirectionalParticleWidth((link) => (isHighlightedLink(link) ? 3.5 : 2))
        .linkDirectionalParticleColor((link) => (isHighlightedLink(link) ? EDGE_HIGHLIGHT : EDGE_ACCENT))
        .linkDirectionalParticleSpeed((link) => (isHighlightedLink(link) ? 0.012 : 0.004))
        .linkLabel((link) => link.type)
        .onNodeClick(handleNodeClick)
        .onNodeHover((node) => {
          dom.vizContainer.style.cursor = node ? "pointer" : "default";
        })
        .warmupTicks(80)
        .cooldownTicks(320)
        .graphData({ nodes: model.nodes, links: model.links });

      configureForces(layout, model);
      updateCommunityGuides(model);
      setGraphMetrics(model.nodes.length, model.links.length);
      setGraphStatus(`${results.length} primary matches | ${COMMUNITY_NAMES[layout.focusCommunity]} in focus`);
      setLoading(false);

      const rankedNodes = [...model.nodes].sort((a, b) => b.queryWeight - a.queryWeight);
      const leadNodeId = results[0]?.id || rankedNodes[0]?.id;
      let didFitView = false;

      graph.onEngineStop(() => {
        if (didFitView) return;
        didFitView = true;
        setFocusLabel("overview");
        if (leadNodeId) onNodeFocus?.(leadNodeId);
        fitGraphView(900, 64);
      });
    } catch (error) {
      console.warn("Graph rendering unavailable:", error.message);
      renderStageMessage('<div class="empty-state">Graph rendering is unavailable in this browser session, but the ranked results are ready.</div>');
      setGraphMetrics(model.nodes.length, model.links.length);
      setGraphStatus("Graph rendering unavailable");
      updateCommunityGuides(model);
      setLoading(false);
    }
  }

  function focusOnNode(id) {
    if (!graph) return;
    const node = graph.graphData().nodes.find((entry) => entry.id === id);
    if (!node) return;
    highlightedNodeId = id;
    setFocusLabel(node.label || node.id);
    syncGraphHighlight();
    flyToNode(node);
  }

  function handleResize() {
    if (!graph) return;
    graph.width(dom.vizContainer.clientWidth).height(dom.vizContainer.clientHeight);
    window.clearTimeout(resizeFitTimer);
    resizeFitTimer = window.setTimeout(() => fitGraphView(0, 64), 120);
  }

  return {
    clear,
    focusOnNode,
    handleResize,
    renderGraph,
    renderStageMessage,
  };
}
