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
const quickQueryButtons = document.querySelectorAll(".quick-query");

let graph3d = null;
let highlightedNodeId = null;
let currentQuery = "";
let resizeObserver = null;
let currentLayout = null;
let currentModel = null;
let sceneDecor = {
  helpers: [],
  labels: [],
  lights: []
};

const COLORS = {
  person: 0xff8d7a,
  repo: 0x2563eb,
  event: 0xffd84d,
  neutral: 0x7d7d7d,
  ink: "#141414",
  accent: "#2e6bff",
  accentWarm: "#ff5d73",
  edge: "#1f1f1f",
  edgeHighlight: "#ff5d73"
};

const COMMUNITY_ORDER = ["people", "projects", "events"];
const COMMUNITY_NAMES = {
  people: "PEOPLE",
  projects: "PROJECTS",
  events: "EVENTS"
};

const COMMUNITY_THEMES = {
  people: {
    fill: "#ffe6df",
    stroke: "#141414",
    accent: "#ff8d7a"
  },
  projects: {
    fill: "#edf4ff",
    stroke: "#141414",
    accent: "#2563eb"
  },
  events: {
    fill: "#fff3bf",
    stroke: "#141414",
    accent: "#ffcf33"
  }
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

function getCommunityTheme(communityKey) {
  return COMMUNITY_THEMES[communityKey] || COMMUNITY_THEMES.people;
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
    node.visualSize = clamp(5 + node.queryWeight * 1.3, 5, 16);
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

  const model = { nodes, links, terms, resultMap, nodeById, communityCounts };
  model.focusCommunity = getIntentProfile(query, model);
  return model;
}

function getLayoutProfile(model) {
  const focus = model.focusCommunity;
  const dense = model.nodes.length > 15;
  const centers = {
    people: { x: -120, y: 50, z: 0 },
    projects: { x: 120, y: 40, z: 0 },
    events: { x: 0, y: -110, z: 0 }
  };

  if (focus === "people") {
    centers.people = { x: 0, y: 65, z: 20 };
    centers.projects = { x: 145, y: 10, z: -20 };
    centers.events = { x: -145, y: -35, z: -30 };
  } else if (focus === "projects") {
    centers.projects = { x: 0, y: 55, z: 20 };
    centers.people = { x: -150, y: 0, z: -25 };
    centers.events = { x: 150, y: -35, z: -25 };
  } else if (focus === "events") {
    centers.events = { x: 0, y: 55, z: 20 };
    centers.people = { x: -155, y: -20, z: -20 };
    centers.projects = { x: 155, y: -15, z: -20 };
  }

  return {
    focusCommunity: focus,
    charge: dense ? -165 : -190,
    distance: dense ? 52 : 70,
    clusterPull: dense ? 0.007 : 0.009,
    centers
  };
}

function seedNodePositions(model, layout) {
  model.nodes.forEach((node, index) => {
    const anchor = layout.centers[node.communityKey] || layout.centers.people;
    const offset = 26 + (node.queryWeight * 2.4);
    const angle = (index + 1) * 1.7;

    node.x = anchor.x + Math.cos(angle) * offset;
    node.y = anchor.y + Math.sin(angle) * offset * 0.65;
    node.z = (node.isResult ? 28 : -12) + ((index % 5) - 2) * 12;
  });
}

function createLabelSprite(text, opts = {}) {
  const fontsize = opts.fontsize || 26;
  const textColor = opts.textColor || "#f2f2ef";
  const fill = opts.fill || "#000000";
  const stroke = opts.stroke || "#f2f2ef";

  const canvas = document.createElement("canvas");
  const context = canvas.getContext("2d");
  context.font = `700 ${fontsize}px Courier New`;
  const textWidth = context.measureText(text).width;

  canvas.width = textWidth + 40;
  canvas.height = fontsize + 26;

  context.fillStyle = fill;
  context.fillRect(0, 0, canvas.width, canvas.height);
  context.strokeStyle = stroke;
  context.lineWidth = 3;
  context.strokeRect(1.5, 1.5, canvas.width - 3, canvas.height - 3);
  context.font = `700 ${fontsize}px Courier New`;
  context.fillStyle = textColor;
  context.textAlign = "center";
  context.textBaseline = "middle";
  context.fillText(text, canvas.width / 2, canvas.height / 2);

  const texture = new THREE.CanvasTexture(canvas);
  const material = new THREE.SpriteMaterial({
    map: texture,
    transparent: true,
    depthWrite: false
  });

  const sprite = new THREE.Sprite(material);
  sprite.scale.set(canvas.width / 5.5, canvas.height / 5.5, 1);
  return sprite;
}

function getNodeMaterialColor(node, isHighlighted) {
  if (isHighlighted) return 0x141414;
  if (node.type === "repo") return COLORS.repo;
  if (node.type === "event") return COLORS.event;
  return COLORS.person;
}

function buildNodeObject(node) {
  const isHighlighted = node.id === highlightedNodeId;
  const group = new THREE.Group();
  const baseColor = getNodeMaterialColor(node, isHighlighted);
  const theme = getCommunityTheme(node.communityKey);
  const size = node.visualSize || 6;
  let geometry;

  if (node.type === "repo") {
    geometry = new THREE.BoxGeometry(size * 1.45, size * 1.45, size * 1.45);
  } else if (node.type === "event") {
    geometry = new THREE.OctahedronGeometry(size * 1.1, 0);
  } else {
    geometry = new THREE.SphereGeometry(size, 16, 16);
  }

  const core = new THREE.Mesh(
    geometry,
    new THREE.MeshStandardMaterial({
      color: baseColor,
      metalness: 0.06,
      roughness: 0.72,
      emissive: node.isResult ? baseColor : 0x000000,
      emissiveIntensity: node.isResult ? 0.08 : 0
    })
  );
  group.add(core);

  const outline = new THREE.Mesh(
    geometry.clone(),
    new THREE.MeshBasicMaterial({
      color: isHighlighted ? 0xff5d73 : 0x141414,
      wireframe: true,
      transparent: true,
      opacity: isHighlighted ? 1 : 0.72
    })
  );
  outline.scale.setScalar(isHighlighted ? 1.16 : 1.09);
  group.add(outline);

  if (node.isResult || node.termMatches) {
    const frame = new THREE.LineSegments(
      new THREE.EdgesGeometry(new THREE.BoxGeometry(size * 3, size * 3, size * 3)),
      new THREE.LineBasicMaterial({
        color: node.isResult ? new THREE.Color(theme.accent) : 0x141414,
        transparent: true,
        opacity: isHighlighted ? 1 : 0.46
      })
    );
    group.add(frame);
  }

  const label = createLabelSprite(truncateLabel(node.label || node.id), {
    fontsize: isHighlighted ? 30 : 24,
    fill: isHighlighted ? "#ff5d73" : theme.fill,
    stroke: theme.stroke,
    textColor: "#141414"
  });
  label.position.set(0, -(size * 2.05), 0);
  group.add(label);

  return group;
}

function truncateLabel(label) {
  return label.length > 18 ? `${label.slice(0, 15)}...` : label;
}

function isHighlightedLink(link) {
  const sourceId = typeof link.source === "object" ? link.source.id : link.source;
  const targetId = typeof link.target === "object" ? link.target.id : link.target;
  return highlightedNodeId && (sourceId === highlightedNodeId || targetId === highlightedNodeId);
}

function cleanupSceneDecor() {
  if (!graph3d) return;

  const scene = graph3d.scene();
  [...sceneDecor.helpers, ...sceneDecor.labels, ...sceneDecor.lights].forEach((entry) => {
    scene.remove(entry);
  });

  sceneDecor = {
    helpers: [],
    labels: [],
    lights: []
  };
}

function addSceneDecor(layout) {
  if (!graph3d) return;

  const scene = graph3d.scene();
  cleanupSceneDecor();

  const ambient = new THREE.AmbientLight(0xffffff, 0.95);
  const front = new THREE.DirectionalLight(0xffffff, 0.75);
  front.position.set(0, 120, 220);
  const side = new THREE.DirectionalLight(0xffd84d, 0.42);
  side.position.set(180, -60, 120);
  sceneDecor.lights.push(ambient, front, side);
  scene.add(ambient, front, side);

  COMMUNITY_ORDER.forEach((communityKey) => {
    const center = layout.centers[communityKey];
    const theme = getCommunityTheme(communityKey);
    const frame = new THREE.LineSegments(
      new THREE.EdgesGeometry(new THREE.BoxGeometry(130, 110, 90)),
      new THREE.LineBasicMaterial({
        color: new THREE.Color(layout.focusCommunity === communityKey ? COLORS.accentWarm : theme.accent),
        transparent: true,
        opacity: layout.focusCommunity === communityKey ? 0.95 : 0.55
      })
    );
    frame.position.set(center.x, center.y, center.z);
    sceneDecor.helpers.push(frame);
    scene.add(frame);

    const label = createLabelSprite(COMMUNITY_NAMES[communityKey], {
      fontsize: 22,
      fill: layout.focusCommunity === communityKey ? "#ffffff" : theme.fill,
      stroke: layout.focusCommunity === communityKey ? COLORS.accentWarm : theme.stroke,
      textColor: "#141414"
    });
    label.position.set(center.x, center.y + 70, center.z);
    sceneDecor.labels.push(label);
    scene.add(label);
  });
}

function updateCommunityGuides(model) {
  COMMUNITY_ORDER.forEach((communityKey) => {
    const guide = document.getElementById(`cluster-${communityKey}`);
    const count = document.getElementById(`cluster-${communityKey}-count`);
    if (guide) {
      guide.classList.toggle("active", model.focusCommunity === communityKey);
    }
    if (count) {
      count.textContent = `${model.communityCounts[communityKey] || 0} nodes`;
    }
  });
}

function applyClusterForces(nodes, layout) {
  nodes.forEach((node) => {
    const anchor = layout.centers[node.communityKey] || layout.centers.people;
    const emphasis = node.isResult ? 1.45 : 1;
    const weight = 0.45 + (node.queryWeight / 10);
    const pull = layout.clusterPull * emphasis * weight;

    node.vx = (node.vx || 0) + (anchor.x - (node.x || 0)) * pull;
    node.vy = (node.vy || 0) + (anchor.y - (node.y || 0)) * pull;
    node.vz = (node.vz || 0) + (anchor.z - (node.z || 0)) * pull * 0.85;
  });
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

  graph3d = ForceGraph3D()(vizContainer)
    .graphData({ nodes: model.nodes, links: model.links })
    .backgroundColor("rgba(0,0,0,0)")
    .showNavInfo(false)
    .width(vizContainer.clientWidth)
    .height(vizContainer.clientHeight)
    .nodeThreeObject(buildNodeObject)
    .linkColor((link) => {
      if (isHighlightedLink(link)) return COLORS.edgeHighlight;
      if (link.isResultBridge) return COLORS.accent;
      return link.isInternalCommunity ? "#141414" : "#686868";
    })
    .linkWidth((link) => {
      if (isHighlightedLink(link)) return 2.8;
      return link.isResultBridge ? 1.7 : 0.8;
    })
    .linkOpacity(0.95)
    .linkDirectionalParticles((link) => {
      if (isHighlightedLink(link)) return 5;
      return link.isResultBridge ? 2 : 0;
    })
    .linkDirectionalParticleWidth((link) => (isHighlightedLink(link) ? 3.5 : 2))
    .linkDirectionalParticleColor((link) => (isHighlightedLink(link) ? COLORS.edgeHighlight : COLORS.accent))
    .linkDirectionalParticleSpeed((link) => (isHighlightedLink(link) ? 0.012 : 0.004))
    .linkLabel((link) => `<span style="color:#f2f2ef;font-family:Courier New,monospace;font-size:11px;">${link.type}</span>`)
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
    .onEngineTick(() => {
      applyClusterForces(model.nodes, layout);
    });

  graph3d.d3Force("charge").strength(layout.charge);
  graph3d.d3Force("link").distance((link) => clamp(layout.distance - link.bridgeStrength * 6, 36, 94));
  graph3d.d3VelocityDecay(0.26);
  graph3d.cooldownTicks(120);
  graph3d.renderer().setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));

  addSceneDecor(layout);
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
  let didAutoFocus = false;

  if (leadNodeId) {
    graph3d.onEngineStop(() => {
      if (didAutoFocus) return;
      didAutoFocus = true;
      focusOnNode(leadNodeId);
      setActiveResultCard(leadNodeId);
    });
  }
}

function setActiveResultCard(id) {
  document.querySelectorAll(".result-card").forEach((card) => {
    card.classList.toggle("active", card.id === `result-${id}`);
  });
}

function syncGraphHighlight() {
  if (!graph3d) return;

  graph3d.nodeThreeObject(buildNodeObject);
  graph3d.linkColor((link) => {
    if (isHighlightedLink(link)) return COLORS.edgeHighlight;
    if (link.isResultBridge) return COLORS.accent;
    return link.isInternalCommunity ? "#141414" : "#686868";
  });
  graph3d.linkWidth((link) => {
    if (isHighlightedLink(link)) return 2.8;
    return link.isResultBridge ? 1.7 : 0.8;
  });
  graph3d.linkDirectionalParticles((link) => {
    if (isHighlightedLink(link)) return 5;
    return link.isResultBridge ? 2 : 0;
  });
  graph3d.linkDirectionalParticleWidth((link) => (isHighlightedLink(link) ? 3.5 : 2));
  graph3d.linkDirectionalParticleColor((link) => (isHighlightedLink(link) ? COLORS.edgeHighlight : COLORS.accent));
  graph3d.linkDirectionalParticleSpeed((link) => (isHighlightedLink(link) ? 0.012 : 0.004));
}

function flyToNode(node) {
  if (!graph3d || !node) return;

  const distance = 105;
  const safeMagnitude = Math.max(1, Math.hypot(node.x || 0, node.y || 0, node.z || 0));
  const ratio = 1 + distance / safeMagnitude;

  graph3d.cameraPosition(
    {
      x: (node.x || 0) * ratio,
      y: (node.y || 0) * ratio,
      z: (node.z || 0) * ratio + 10
    },
    node,
    1000
  );
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
  graph3d.renderer().setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
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

  try {
    const response = await fetch("http://localhost:8000/query", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ q: query })
    });

    if (!response.ok) {
      throw new Error("Backend not available");
    }

    const data = await response.json();
    renderResults(data.results || []);
    renderGraph(data.subgraph, data.results || [], rawQuery);
  } catch (error) {
    console.log("Falling back to mock data:", error.message);
    const data = pickMockData(query);

    setTimeout(() => {
      renderResults(data.results);
      renderGraph(data.subgraph, data.results, rawQuery);
    }, 260);
  }
}

searchBtn.addEventListener("click", handleSearch);
qInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter") handleSearch();
});

quickQueryButtons.forEach((button) => {
  button.addEventListener("click", () => {
    qInput.value = button.dataset.query || "";
    handleSearch();
  });
});

window.addEventListener("resize", handleResize);

window.addEventListener("DOMContentLoaded", () => {
  resizeObserver = new ResizeObserver(() => handleResize());
  resizeObserver.observe(vizContainer);
  qInput.value = "Who works on LangGraph in Kolkata";
  handleSearch();
});
