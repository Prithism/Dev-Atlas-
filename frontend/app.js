// Kolkata Dev Atlas frontend

const qInput = document.getElementById("q");
const searchBtn = document.getElementById("search-btn");
const resultsList = document.getElementById("results-list");
const loadingIndicator = document.getElementById("loading");
const vizContainer = document.getElementById("viz");
const resultCount = document.getElementById("result-count");
const graphState = document.getElementById("graph-state");
const queryContext = document.getElementById("query-context");
const nodeCount = document.getElementById("node-count");
const linkCount = document.getElementById("link-count");
const focusLabel = document.getElementById("focus-label");
const API_BASE = window.location.protocol === "file:"
  ? "http://localhost:8000"
  : window.location.origin;
const QUERY_TIMEOUT_MS = 45000;

let graph3d = null;
let highlightedNodeId = null;
let currentQuery = "";
let resizeObserver = null;
let currentLayout = null;
let currentModel = null;
let resizeFitTimer = null;

// Edge colours (still used by link particle callbacks)
const EDGE_HIGHLIGHT = "#ff5d73";
const EDGE_ACCENT    = "#3b82f6";

const COMMUNITY_ORDER = ["people", "projects", "events"];
const COMMUNITY_NAMES = {
  people:   "PEOPLE",
  projects: "PROJECTS",
  events:   "EVENTS"
};

// Node visual palette — used in drawNodeCanvas
const NODE_PALETTE = {
  person:  { fill: "#fb7185", stroke: "#f43f5e", glowI: "rgba(251,113,133,0.35)", glowO: "rgba(251,113,133,0)" },
  repo:    { fill: "#60a5fa", stroke: "#3b82f6", glowI: "rgba(96,165,250,0.30)",  glowO: "rgba(96,165,250,0)"  },
  event:   { fill: "#fbbf24", stroke: "#f59e0b", glowI: "rgba(251,191,36,0.35)",  glowO: "rgba(251,191,36,0)"  },
};

const STOP_WORDS = new Set([
  "the",
  "and",
  "for",
  "with",
  "from",
  "that",
  "this",
  "into",
  "about",
  "show",
  "works",
  "work",
  "who",
  "what",
  "where",
  "when",
  "why",
  "how",
  "in",
  "on",
  "at",
  "to",
  "of",
  "me",
  "my",
  "a",
  "an"
]);

const MOCK_DATA = {
  langgraph: {
    results: [
      {
        id: "rishiraj",
        name: "Rishiraj Acharya",
        score: 0.91,
        evidence: ["Maintains langgraph-bengali", "GDG Cloud organizer"],
        url: "https://github.com/rishiraj"
      },
      {
        id: "ananya",
        name: "Ananya D.",
        score: 0.85,
        evidence: ["Contributed to langgraph-core", "Speaker at PyData Kolkata"],
        url: "https://github.com/ananya"
      }
    ],
    subgraph: {
      nodes: [
        { id: "rishiraj", label: "Rishiraj A.", type: "person" },
        { id: "ananya", label: "Ananya D.", type: "person" },
        { id: "langgraph-bengali", label: "langgraph-bengali", type: "repo" },
        { id: "langgraph-core", label: "langgraph-core", type: "repo" },
        { id: "pydata-kol", label: "PyData Kolkata", type: "event" },
        { id: "gdg-cloud-kol", label: "GDG Cloud Kolkata", type: "event" }
      ],
      edges: [
        { src: "rishiraj", dst: "langgraph-bengali", type: "maintains" },
        { src: "ananya", dst: "langgraph-core", type: "contributes" },
        { src: "ananya", dst: "pydata-kol", type: "spoke at" },
        { src: "rishiraj", dst: "pydata-kol", type: "attended" },
        { src: "rishiraj", dst: "gdg-cloud-kol", type: "organizes" }
      ]
    }
  },
  mentors: {
    results: [
      {
        id: "kiran",
        name: "Kiran M.",
        score: 0.98,
        evidence: ["Mentors junior ML devs", "Runs Bangla-Python meetup"],
        url: "https://github.com/kiran"
      },
      {
        id: "soumik",
        name: "Soumik N.",
        score: 0.88,
        evidence: ["HuggingFace Fellow", "ML community builder"],
        url: "https://github.com/soumik"
      }
    ],
    subgraph: {
      nodes: [
        { id: "kiran", label: "Kiran M.", type: "person" },
        { id: "soumik", label: "Soumik N.", type: "person" },
        { id: "bangla-python", label: "Bangla-Python", type: "event" },
        { id: "hf-fellows", label: "HF Fellows", type: "repo" },
        { id: "jun-1", label: "Dev A.", type: "person" },
        { id: "jun-2", label: "Dev B.", type: "person" },
        { id: "jun-3", label: "Dev C.", type: "person" }
      ],
      edges: [
        { src: "kiran", dst: "bangla-python", type: "organizes" },
        { src: "soumik", dst: "hf-fellows", type: "member" },
        { src: "kiran", dst: "jun-1", type: "mentors" },
        { src: "kiran", dst: "jun-2", type: "mentors" },
        { src: "soumik", dst: "jun-2", type: "mentors" },
        { src: "soumik", dst: "jun-3", type: "mentors" },
        { src: "jun-1", dst: "bangla-python", type: "attends" }
      ]
    }
  },
  jadavpur: {
    results: [
      {
        id: "ju-club",
        name: "JU Coding Club",
        score: 0.95,
        evidence: ["Active repository group", "Jadavpur University tag"],
        url: "https://github.com/jadavpur-coding"
      }
    ],
    subgraph: {
      nodes: [
        { id: "ju-club", label: "JU Club", type: "repo" },
        { id: "p1", label: "Student A", type: "person" },
        { id: "p2", label: "Student B", type: "person" },
        { id: "p3", label: "Student C", type: "person" },
        { id: "p4", label: "Student D", type: "person" },
        { id: "p5", label: "Student E", type: "person" },
        { id: "ju-hackathon", label: "JU Hack 2025", type: "event" },
        { id: "ju-ml-proj", label: "ju-ml-toolkit", type: "repo" },
        { id: "ju-web-proj", label: "ju-web-platform", type: "repo" }
      ],
      edges: [
        { src: "p1", dst: "ju-club", type: "member" },
        { src: "p2", dst: "ju-club", type: "member" },
        { src: "p3", dst: "ju-club", type: "member" },
        { src: "p4", dst: "ju-club", type: "member" },
        { src: "p5", dst: "ju-club", type: "member" },
        { src: "p1", dst: "ju-hackathon", type: "won" },
        { src: "p3", dst: "ju-hackathon", type: "attended" },
        { src: "p5", dst: "ju-hackathon", type: "attended" },
        { src: "p2", dst: "ju-ml-proj", type: "maintains" },
        { src: "p4", dst: "ju-web-proj", type: "maintains" },
        { src: "p1", dst: "ju-ml-proj", type: "contributes" }
      ]
    }
  }
};

function setGraphStatus(text) {
  graphState.textContent = text;
}

function setQueryContext(text) {
  queryContext.textContent = text;
}

function setGraphMetrics(nodes = 0, links = 0) {
  nodeCount.textContent = String(nodes);
  linkCount.textContent = String(links);
}

function setFocusLabel(text) {
  focusLabel.textContent = `Focus: ${text}`;
}

function setResultCount(total) {
  resultCount.textContent = `${total} result${total === 1 ? "" : "s"}`;
}

function setLoading(isLoading) {
  loadingIndicator.classList.toggle("hidden", !isLoading);
}

function normalizeText(value = "") {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
}

function tokenize(value = "") {
  return normalizeText(value)
    .split(/\s+/)
    .filter((token) => token && token.length > 2 && !STOP_WORDS.has(token));
}

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function pickMockData(query) {
  if (query.includes("langgraph")) return MOCK_DATA.langgraph;
  if (query.includes("mentor")) return MOCK_DATA.mentors;
  if (query.includes("jadavpur")) return MOCK_DATA.jadavpur;
  return MOCK_DATA.langgraph;
}

function getCommunityKey(node) {
  if (node.type === "repo") return "projects";
  if (node.type === "event") return "events";
  return "people";
}

function getIntentProfile(query, model) {
  const lowerQuery = normalizeText(query);
  const scores = {
    people: /mentor|mentors|junior|people|developer|developers|engineer|engineers|community/.test(lowerQuery) ? 3 : 0,
    projects: /repo|repos|project|projects|github|langgraph|toolkit|maintain|contribute|code/.test(lowerQuery) ? 3 : 0,
    events: /event|events|meetup|meetups|conference|speaker|hackathon|pydata|gdg/.test(lowerQuery) ? 3 : 0
  };

  model.nodes.forEach((node) => {
    scores[node.communityKey] += node.queryWeight;
  });

  return COMMUNITY_ORDER.reduce((best, key) => {
    if (!best || scores[key] > scores[best]) return key;
    return best;
  }, "people");
}

function buildSearchModel(subgraph, results, query) {
  const terms = tokenize(query);
  const nodes = (subgraph.nodes || []).slice(0, 50).map((node) => ({ ...node }));
  const nodeIds = new Set(nodes.map((node) => node.id));
  const resultMap = new Map(results.map((result, index) => [result.id, { ...result, rank: index }]));
  const adjacency = new Map();

  const links = (subgraph.edges || [])
    .filter((edge) => nodeIds.has(edge.src) && nodeIds.has(edge.dst))
    .map((edge, index) => {
      if (!adjacency.has(edge.src)) adjacency.set(edge.src, new Set());
      if (!adjacency.has(edge.dst)) adjacency.set(edge.dst, new Set());
      adjacency.get(edge.src).add(edge.dst);
      adjacency.get(edge.dst).add(edge.src);

      return {
        id: `${edge.src}-${edge.dst}-${edge.type}-${index}`,
        source: edge.src,
        target: edge.dst,
        type: edge.type
      };
    });

  const communityCounts = {
    people: 0,
    projects: 0,
    events: 0
  };

  const nodeById = new Map();

  nodes.forEach((node) => {
    const resultMeta = resultMap.get(node.id);
    const neighbors = adjacency.get(node.id) || new Set();
    const labelTokens = tokenize(`${node.label || ""} ${node.id} ${node.type || ""}`);
    const termMatches = terms.filter((term) => labelTokens.some((token) => token.includes(term) || term.includes(token))).length;
    const degree = neighbors.size;
    const connectedResultCount = Array.from(neighbors).filter((neighborId) => resultMap.has(neighborId)).length;
    const rankBoost = resultMeta ? Math.max(0, 3 - resultMeta.rank) : 0;
    const scoreBoost = resultMeta && typeof resultMeta.score === "number" ? resultMeta.score * 2.4 : 0;

    node.communityKey = getCommunityKey(node);
    node.degree = degree;
    node.termMatches = termMatches;
    node.connectedResultCount = connectedResultCount;
    node.resultScore = resultMeta && typeof resultMeta.score === "number" ? resultMeta.score : 0;
    node.queryWeight = rankBoost + scoreBoost + termMatches * 1.7 + connectedResultCount * 0.9 + Math.min(degree, 5) * 0.3;
    node.visualSize = clamp(7 + node.queryWeight * 1.35, 7, 20);
    node.isResult = resultMap.has(node.id);

    communityCounts[node.communityKey] += 1;
    nodeById.set(node.id, node);
  });

  links.forEach((link) => {
    const sourceNode = nodeById.get(link.source);
    const targetNode = nodeById.get(link.target);
    const sourceWeight = sourceNode ? sourceNode.queryWeight : 0;
    const targetWeight = targetNode ? targetNode.queryWeight : 0;
    link.queryWeight = (sourceWeight + targetWeight) / 2;
    link.bridgeStrength = clamp(link.queryWeight / 4, 0.4, 2.8);
    link.isResultBridge = Boolean(sourceNode?.isResult || targetNode?.isResult);
    link.isInternalCommunity = sourceNode?.communityKey === targetNode?.communityKey;
  });

  const model = { nodes, links, terms, resultMap, nodeById, communityCounts, adjacency };
  model.focusCommunity = getIntentProfile(query, model);
  return model;
}

function getLayoutProfile(model) {
  const focus = model.focusCommunity;
  const dense = model.nodes.length > 15;

  // Scale cluster separation to actual canvas size so nodes spread across 70% of canvas
  const W = vizContainer.clientWidth  || 700;
  const H = vizContainer.clientHeight || 420;
  const hx = Math.round(W * 0.38);   // horizontal offset (~38 % of width)
  const vy = Math.round(H * 0.36);   // vertical   offset (~36 % of height)

  // Default: triangle layout — people left, projects right, events bottom-centre
  const centers = {
    people:   { x: -hx, y: -Math.round(vy * 0.20), z: 0 },
    projects: { x:  hx, y: -Math.round(vy * 0.20), z: 0 },
    events:   { x:   0, y:  vy,                     z: 0 }
  };

  // Focus variant: pull the dominant cluster to centre, push others to the wings
  if (focus === "people") {
    centers.people   = { x:    0, y: -Math.round(vy * 0.25), z: 0 };
    centers.projects = { x:   hx, y:  Math.round(vy * 0.15), z: 0 };
    centers.events   = { x:  -hx, y:  vy,                    z: 0 };
  } else if (focus === "projects") {
    centers.projects = { x:    0, y: -Math.round(vy * 0.25), z: 0 };
    centers.people   = { x:  -hx, y:  Math.round(vy * 0.15), z: 0 };
    centers.events   = { x:   hx, y:  vy,                    z: 0 };
  } else if (focus === "events") {
    centers.events   = { x:   0, y:    0, z: 0 };
    centers.people   = { x: -hx, y:   vy, z: 0 };
    centers.projects = { x:  hx, y:   vy, z: 0 };
  }

  return {
    focusCommunity: focus,
    // Stronger repulsion so nodes within each cluster spread out more
    charge:      dense ? -420 : -520,
    // Longer link rest-length — keeps connected nodes readable
    distance:    dense ?  115 :  145,
    // clusterPull is used by the D3 force (applied with alpha)
    clusterPull: dense ? 0.10 : 0.13,
    centers
  };
}

function seedNodePositions(model, layout) {
  // Group nodes by their cluster so each group fans out evenly around its anchor
  const byCluster = {};
  model.nodes.forEach((node) => {
    const key = node.communityKey;
    if (!byCluster[key]) byCluster[key] = [];
    byCluster[key].push(node);
  });

  for (const [key, nodes] of Object.entries(byCluster)) {
    const anchor = layout.centers[key] || layout.centers.people;
    // Use concentric rings: inner ring holds result nodes, outer rings hold neighbours
    const resultNodes = nodes.filter((n) => n.isResult);
    const otherNodes  = nodes.filter((n) => !n.isResult);
    const allOrdered  = [...resultNodes, ...otherNodes];
    // Ring capacity: sqrt gives organic-looking distribution
    const ringSize    = Math.max(Math.ceil(Math.sqrt(allOrdered.length)), 1);

    allOrdered.forEach((node, i) => {
      const ring   = Math.floor(i / ringSize);
      const count  = Math.min(ringSize, allOrdered.length - ring * ringSize);
      // Offset start angle per ring to avoid line-up artifacts
      const offset = ring % 2 === 0 ? 0 : Math.PI / count;
      const angle  = offset + (2 * Math.PI * (i % ringSize)) / count;
      // Larger initial radius so nodes are already spread before the simulation runs
      const radius = 72 + ring * 100;
      node.x = anchor.x + Math.cos(angle) * radius;
      node.y = anchor.y + Math.sin(angle) * radius * 0.82;
      node.vx = 0;
      node.vy = 0;
      node.z  = 0;
    });
  }
}

function truncateLabel(label) {
  return label.length > 22 ? `${label.slice(0, 19)}…` : label;
}

// Draw a rounded rectangle path (no fill/stroke — caller does that)
function roundedRect(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.lineTo(x + w - r, y);
  ctx.arcTo(x + w, y,     x + w, y + r,     r);
  ctx.lineTo(x + w, y + h - r);
  ctx.arcTo(x + w, y + h, x + w - r, y + h, r);
  ctx.lineTo(x + r, y + h);
  ctx.arcTo(x,     y + h, x,     y + h - r, r);
  ctx.lineTo(x,     y + r);
  ctx.arcTo(x,     y,     x + r, y,         r);
  ctx.closePath();
}

function drawNodeCanvas(node, ctx, globalScale) {
  const isSelected  = node.id === highlightedNodeId;
  const hasFocus    = highlightedNodeId !== null;
  // Neighbours of the currently selected node (bidirectional, built in buildSearchModel)
  const neighbors   = hasFocus ? (currentModel?.adjacency?.get(highlightedNodeId) || new Set()) : new Set();
  const isConnected = hasFocus && neighbors.has(node.id);
  // Dim everything that has no relation to the selection
  const dimmed      = hasFocus && !isSelected && !isConnected;

  const size = node.visualSize || 7;
  const p    = NODE_PALETTE[node.type] || NODE_PALETTE.person;

  ctx.save();
  ctx.globalAlpha = dimmed ? 0.12 : 1.0;
  ctx.translate(node.x, node.y);

  // ── Glow ──────────────────────────────────────────────────────────
  if (isSelected) {
    // Strong warm glow behind selected node
    const g = ctx.createRadialGradient(0, 0, size * 0.5, 0, 0, size * 3.6);
    g.addColorStop(0, "rgba(255, 93, 115, 0.55)");
    g.addColorStop(0.45, "rgba(255, 93, 115, 0.18)");
    g.addColorStop(1,    "rgba(255, 93, 115, 0)");
    ctx.fillStyle = g;
    ctx.beginPath();
    ctx.arc(0, 0, size * 3.6, 0, Math.PI * 2);
    ctx.fill();
  } else if (node.isResult && !hasFocus) {
    // Subtle type-coloured glow for result nodes at rest
    const g = ctx.createRadialGradient(0, 0, 0, 0, 0, size * 2.6);
    g.addColorStop(0, p.glowI);
    g.addColorStop(1, p.glowO);
    ctx.fillStyle = g;
    ctx.beginPath();
    ctx.arc(0, 0, size * 2.6, 0, Math.PI * 2);
    ctx.fill();
  }

  // ── Node body ──────────────────────────────────────────────────────
  ctx.fillStyle   = isSelected ? "#0f0f1a" : p.fill;
  ctx.strokeStyle = isSelected ? "#ff5d73" : (isConnected ? "#ffffff" : p.stroke);
  ctx.lineWidth   = (isSelected ? 2.5 : isConnected ? 2.2 : 1.4) / globalScale;

  if (node.type === "repo") {
    // Rounded square
    const s = size * 1.08;
    const r = s * 0.32;
    roundedRect(ctx, -s, -s, s * 2, s * 2, r);
    ctx.fill();
    ctx.stroke();
    // Three horizontal lines → "code file" icon
    ctx.strokeStyle = isSelected ? "#ff5d73" : "rgba(255,255,255,0.55)";
    ctx.lineWidth = 1.2 / globalScale;
    ctx.lineCap = "round";
    for (let i = -1; i <= 1; i++) {
      const lw = i === 0 ? s * 0.7 : s * 0.45;
      ctx.beginPath();
      ctx.moveTo(-lw, i * s * 0.38);
      ctx.lineTo( lw, i * s * 0.38);
      ctx.stroke();
    }
  } else if (node.type === "event") {
    // Diamond
    const d = size * 1.22;
    ctx.beginPath();
    ctx.moveTo(0,    -d);
    ctx.lineTo(d * 0.82,  0);
    ctx.lineTo(0,     d);
    ctx.lineTo(-d * 0.82, 0);
    ctx.closePath();
    ctx.fill();
    ctx.stroke();
    // Star-dot inside
    ctx.fillStyle = isSelected ? "#ff5d73" : "rgba(255,255,255,0.6)";
    ctx.beginPath();
    ctx.arc(0, 0, d * 0.22, 0, Math.PI * 2);
    ctx.fill();
  } else {
    // Person: circle with a minimal head+shoulders hint
    ctx.beginPath();
    ctx.arc(0, 0, size, 0, Math.PI * 2);
    ctx.fill();
    ctx.stroke();
    // Head dot + shoulder arc
    ctx.fillStyle   = isSelected ? "#ff5d73" : "rgba(255,255,255,0.5)";
    ctx.strokeStyle = isSelected ? "#ff5d73" : "rgba(255,255,255,0.4)";
    ctx.lineWidth   = 1 / globalScale;
    ctx.beginPath();
    ctx.arc(0, -size * 0.22, size * 0.26, 0, Math.PI * 2);
    ctx.fill();
    ctx.beginPath();
    ctx.arc(0, size * 0.55, size * 0.52, Math.PI, 0);
    ctx.stroke();
  }

  // ── Selection orbit ring ───────────────────────────────────────────
  if (isSelected) {
    ctx.strokeStyle = "#ff5d73";
    ctx.lineWidth   = 1.5 / globalScale;
    ctx.globalAlpha = 0.7;
    ctx.setLineDash([5 / globalScale, 4 / globalScale]);
    ctx.beginPath();
    ctx.arc(0, 0, size * 2.2, 0, Math.PI * 2);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.globalAlpha = dimmed ? 0.12 : 1.0;
  }

  // ── Label ─────────────────────────────────────────────────────────
  const showLabel = isSelected || node.isResult || (globalScale > 2.0 && node.degree > 0);
  if (showLabel) {
    const label    = truncateLabel(node.label || node.id);
    const fontSize = clamp(11 / globalScale, 8.5, 13);
    ctx.font       = `600 ${fontSize}px Inter, system-ui, sans-serif`;
    const tw   = ctx.measureText(label).width;
    const padX = 7  / globalScale;
    const padY = 4  / globalScale;
    const bw   = tw + padX * 2;
    const bh   = fontSize + padY * 2;
    const bx   = size + 7 / globalScale;
    const by   = -bh / 2;
    const br   = Math.min(bh * 0.38, 5 / globalScale);

    ctx.shadowColor = "rgba(0,0,0,0.18)";
    ctx.shadowBlur  = 5 / globalScale;
    ctx.fillStyle   = isSelected ? "rgba(15,15,26,0.94)" : "rgba(255,255,255,0.93)";
    roundedRect(ctx, bx, by, bw, bh, br);
    ctx.fill();
    ctx.shadowBlur = 0;

    ctx.fillStyle = isSelected ? "#ffd84d" : "#111827";
    ctx.fillText(label, bx + padX, by + padY + fontSize * 0.83);
  }

  ctx.restore();
}

function getLinkColor(link) {
  if (isHighlightedLink(link)) return "rgba(255, 93, 115, 0.95)";
  if (link.isResultBridge) return "rgba(59, 130, 246, 0.72)";
  if (highlightedNodeId) return "rgba(80, 80, 80, 0.08)"; // dim unrelated links on selection
  return link.isInternalCommunity ? "rgba(20, 20, 20, 0.22)" : "rgba(110, 110, 110, 0.16)";
}

function getLinkWidth(link) {
  if (isHighlightedLink(link)) return 2.8;
  return link.isResultBridge ? 1.8 : 0.8;
}

function isHighlightedLink(link) {
  const sourceId = typeof link.source === "object" ? link.source.id : link.source;
  const targetId = typeof link.target === "object" ? link.target.id : link.target;
  return highlightedNodeId && (sourceId === highlightedNodeId || targetId === highlightedNodeId);
}


function updateCommunityGuides(model) {
  COMMUNITY_ORDER.forEach((communityKey) => {
    const chip  = document.getElementById(`cluster-${communityKey}`);
    const count = document.getElementById(`cluster-${communityKey}-count`);
    if (chip) {
      chip.classList.toggle("active", model.focusCommunity === communityKey);
    }
    if (count) {
      count.textContent = String(model.communityCounts[communityKey] || 0);
    }
  });
}

function fitGraphView(duration = 900, padding = 64) {
  if (!graph3d) return;

  if (typeof graph3d.zoomToFit === "function") {
    graph3d.zoomToFit(duration, padding);
  } else {
    graph3d.centerAt(0, 0, duration);
    graph3d.zoom(1.8, duration);
  }
}

function renderResults(results) {
  setLoading(false);
  resultsList.innerHTML = "";
  setResultCount(results.length);

  if (!results.length) {
    resultsList.innerHTML = '<div class="empty-state">No matches found. Try a broader query to reveal new communities.</div>';
    return;
  }

  results.forEach((res, index) => {
    const card = document.createElement("article");
    card.className = "result-card";
    card.id = `result-${res.id}`;

    const header = document.createElement("div");
    header.className = "result-header";

    const name = document.createElement("span");
    name.className = "result-name";
    name.textContent = `[${index + 1}] ${res.name}`;

    const score = document.createElement("span");
    score.className = "result-score";
    score.textContent = typeof res.score === "number" ? res.score.toFixed(2) : "--";

    header.append(name, score);
    card.appendChild(header);

    if (Array.isArray(res.evidence) && res.evidence.length) {
      const evidenceWrap = document.createElement("div");
      evidenceWrap.className = "result-evidence";
      const evidenceList = document.createElement("ul");

      res.evidence.forEach((item) => {
        const evidenceItem = document.createElement("li");
        evidenceItem.textContent = item;
        evidenceList.appendChild(evidenceItem);
      });

      evidenceWrap.appendChild(evidenceList);
      card.appendChild(evidenceWrap);
    }

    if (res.url) {
      const link = document.createElement("a");
      link.className = "result-url";
      link.href = res.url;
      link.target = "_blank";
      link.rel = "noreferrer";
      link.textContent = res.url.replace(/^https?:\/\//, "");
      link.addEventListener("click", (event) => event.stopPropagation());
      card.appendChild(link);
    }

    card.addEventListener("click", () => {
      setActiveResultCard(res.id);
      focusOnNode(res.id);
    });

    resultsList.appendChild(card);
  });
}

function renderGraph(subgraph, results = [], query = currentQuery) {
  if (!subgraph || !Array.isArray(subgraph.nodes) || !subgraph.nodes.length) {
    vizContainer.innerHTML = "";
    setLoading(false);
    setGraphStatus("No graph data");
    setGraphMetrics(0, 0);
    return;
  }

  const model = buildSearchModel(subgraph, results, query);
  const layout = getLayoutProfile(model);
  seedNodePositions(model, layout);

  currentModel = model;
  currentLayout = layout;
  vizContainer.innerHTML = "";

  if (typeof ForceGraph !== "function") {
    vizContainer.innerHTML = '<div class="empty-state">Graph view unavailable right now. Results are still usable while the graph library is offline.</div>';
    setGraphMetrics(model.nodes.length, model.links.length);
    setGraphStatus("Graph library unavailable");
    setQueryContext(
      model.terms.length
        ? `Query terms: ${model.terms.join(" / ")}`
        : `Query terms: ${query || "all signals"}`
    );
    updateCommunityGuides(model);
    setLoading(false);
    return;
  }

  try {
    graph3d = ForceGraph()(vizContainer)
      .backgroundColor("rgba(0,0,0,0)")
      .width(vizContainer.clientWidth)
      .height(vizContainer.clientHeight)
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
      .onNodeClick((node) => {
        highlightedNodeId = node.id;
        syncGraphHighlight();
        setActiveResultCard(node.id);
        setFocusLabel(node.label || node.id);

        const card = document.getElementById(`result-${node.id}`);
        if (card) {
          card.scrollIntoView({ behavior: "smooth", block: "nearest" });
        }

        flyToNode(node);
      })
      .onNodeHover((node) => {
        vizContainer.style.cursor = node ? "pointer" : "default";
      })
      .warmupTicks(80)    // pre-run 80 ticks synchronously — nodes arrive pre-clustered
      .cooldownTicks(320) // keep animating until clusters fully settle
      .graphData({ nodes: model.nodes, links: model.links });

    // ── D3 force tuning ───────────────────────────────────────────────
    // Disable the built-in center force — it fights cluster positioning
    graph3d.d3Force("center", null);

    // Strong repulsion so nodes within each cluster spread out
    graph3d.d3Force("charge").strength(layout.charge);

    // Longer link rest-length keeps connected pairs readable
    graph3d.d3Force("link").distance(
      (link) => clamp(layout.distance - link.bridgeStrength * 10, 60, 200)
    );

    // Cluster force: pull each node toward its group anchor, scaled by alpha
    // (alpha starts at 1 and cools toward 0, so the pull naturally fades as the simulation settles)
    const clusterCenters = layout.centers;
    const clusterPull    = layout.clusterPull;
    graph3d.d3Force("cluster", (alpha) => {
      model.nodes.forEach((node) => {
        const anchor   = clusterCenters[node.communityKey] || clusterCenters.people;
        // Result nodes are pulled harder so they stay front-and-centre in their cluster
        const emphasis = node.isResult ? 1.6 : 1.0;
        const k        = clusterPull * alpha * emphasis;
        node.vx = (node.vx || 0) + (anchor.x - (node.x || 0)) * k;
        node.vy = (node.vy || 0) + (anchor.y - (node.y || 0)) * k;
      });
    });

    // Higher decay = faster settling, less overshooting
    graph3d.d3VelocityDecay(0.38);

    addLegend();
    updateCommunityGuides(model);
    setGraphMetrics(model.nodes.length, model.links.length);
    setGraphStatus(`${results.length} primary matches | ${COMMUNITY_NAMES[layout.focusCommunity]} in focus`);
    setQueryContext(
      model.terms.length
        ? `Query terms: ${model.terms.join(" / ")}`
        : `Query terms: ${query || "all signals"}`
    );
    setLoading(false);

    const rankedNodes = [...model.nodes].sort((a, b) => b.queryWeight - a.queryWeight);
    const leadNodeId = results[0]?.id || rankedNodes[0]?.id;
    let didFitView = false;

    graph3d.onEngineStop(() => {
      if (didFitView) return;
      didFitView = true;
      setFocusLabel("overview");
      if (leadNodeId) {
        setActiveResultCard(leadNodeId);
      }
      fitGraphView(900, 64);
    });
  } catch (error) {
    console.warn("Graph rendering unavailable:", error.message);
    vizContainer.innerHTML = '<div class="empty-state">Graph rendering is unavailable in this browser session, but the ranked results are ready.</div>';
    setGraphMetrics(model.nodes.length, model.links.length);
    setGraphStatus("Graph rendering unavailable");
    setQueryContext(
      model.terms.length
        ? `Query terms: ${model.terms.join(" / ")}`
        : `Query terms: ${query || "all signals"}`
    );
    updateCommunityGuides(model);
    setLoading(false);
  }
}

function setActiveResultCard(id) {
  document.querySelectorAll(".result-card").forEach((card) => {
    card.classList.toggle("active", card.id === `result-${id}`);
  });
}

function syncGraphHighlight() {
  if (!graph3d) return;
  // Re-register callbacks so they close over the latest highlightedNodeId
  graph3d.nodeCanvasObject(drawNodeCanvas);
  graph3d.linkColor(getLinkColor);
  graph3d.linkWidth(getLinkWidth);
  graph3d.linkDirectionalParticles((link) => {
    if (isHighlightedLink(link)) return 4;
    return link.isResultBridge ? 1 : 0;
  });
  graph3d.linkDirectionalParticleWidth((link) => (isHighlightedLink(link) ? 3 : 1.8));
  graph3d.linkDirectionalParticleColor((link) => (isHighlightedLink(link) ? EDGE_HIGHLIGHT : EDGE_ACCENT));
  graph3d.linkDirectionalParticleSpeed((link) => (isHighlightedLink(link) ? 0.01 : 0.004));
  if (typeof graph3d.refresh === "function") {
    graph3d.refresh();
  }
}

function flyToNode(node) {
  if (!graph3d || !node) return;
  // Centre on the node without zooming in too aggressively
  if (typeof graph3d.centerAt === "function") {
    graph3d.centerAt(node.x || 0, node.y || 0, 700);
  }
  if (typeof graph3d.zoom === "function") {
    graph3d.zoom(2.8, 700);
  }
}

function focusOnNode(id) {
  if (!graph3d) return;

  const node = graph3d.graphData().nodes.find((entry) => entry.id === id);
  if (!node) return;

  highlightedNodeId = id;
  setFocusLabel(node.label || node.id);
  syncGraphHighlight();
  flyToNode(node);
}

function addLegend() {
  const existing = document.querySelector(".graph-legend");
  if (existing) existing.remove();

  const legend = document.createElement("div");
  legend.className = "graph-legend";
  legend.innerHTML = `
    <div class="legend-item">
      <span class="legend-swatch"></span> People
    </div>
    <div class="legend-item">
      <span class="legend-swatch repo"></span> Projects
    </div>
    <div class="legend-item">
      <span class="legend-swatch event"></span> Events
    </div>
  `;

  document.getElementById("graph").appendChild(legend);
}

function handleResize() {
  if (!graph3d) return;
  graph3d.width(vizContainer.clientWidth).height(vizContainer.clientHeight);
  window.clearTimeout(resizeFitTimer);
  resizeFitTimer = window.setTimeout(() => fitGraphView(0, 64), 120);
}

function setDataSourceBadge(source) {
  let badge = document.getElementById("data-source-badge");
  if (!badge) {
    badge = document.createElement("div");
    badge.id = "data-source-badge";
    badge.style.cssText = [
      "position:fixed", "top:14px", "right:14px", "z-index:999",
      "padding:6px 14px", "font:700 11px/1 'Courier New',monospace",
      "border:2px solid #141414", "letter-spacing:.06em", "text-transform:uppercase",
      "border-radius:12px", "pointer-events:none"
    ].join(";");
    document.body.appendChild(badge);
  }
  if (source === "live") {
    badge.textContent = "● Live data";
    badge.style.background = "#d4f5d4";
    badge.style.color = "#141414";
  } else {
    badge.textContent = "○ Demo data";
    badge.style.background = "#fff3bf";
    badge.style.color = "#141414";
  }
}

async function handleSearch() {
  const rawQuery = qInput.value.trim();
  const query = rawQuery.toLowerCase();
  if (!query) return;

  currentQuery = rawQuery;
  highlightedNodeId = null;
  setLoading(true);
  setGraphStatus("Scanning atlas");
  setQueryContext(`Query terms: ${rawQuery}`);
  setFocusLabel("none");
  setResultCount(0);
  setGraphMetrics(0, 0);
  resultsList.innerHTML = '<div class="empty-state">Reading query and regrouping communities...</div>';

  let timeoutId = null;

  try {
    const controller = new AbortController();
    timeoutId = window.setTimeout(() => controller.abort(), QUERY_TIMEOUT_MS);
    const response = await fetch(`${API_BASE}/query`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ q: query }),
      signal: controller.signal
    });

    if (!response.ok) {
      const detail = await response.text().catch(() => response.statusText);
      throw new Error(`Backend error ${response.status}: ${detail}`);
    }

    const data = await response.json();
    setDataSourceBadge("live");
    renderResults(data.results || []);
    renderGraph(data.subgraph, data.results || [], rawQuery);
  } catch (error) {
    const isTimeout = error.name === "AbortError";
    const reason = isTimeout ? "request timed out after 45s" : error.message;
    console.warn(`Backend unavailable (${reason}), showing demo data`);
    setDataSourceBadge("demo");
    const data = pickMockData(query);

    setTimeout(() => {
      renderResults(data.results);
      renderGraph(data.subgraph, data.results, rawQuery);
    }, 260);
  } finally {
    if (timeoutId !== null) {
      window.clearTimeout(timeoutId);
    }
  }
}

searchBtn.addEventListener("click", handleSearch);
qInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter") handleSearch();
});

window.addEventListener("resize", handleResize);

window.addEventListener("DOMContentLoaded", () => {
  resizeObserver = new ResizeObserver(() => handleResize());
  resizeObserver.observe(vizContainer);
  qInput.value = "Who works on LangGraph in Kolkata";
  // Defer by one animation frame so CSS layout is fully computed before
  // getLayoutProfile() reads vizContainer.clientWidth / clientHeight
  requestAnimationFrame(() => handleSearch());
});
