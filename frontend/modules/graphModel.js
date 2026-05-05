import { COMMUNITY_ORDER } from "./config.js";
import { clamp, normalizeText, tokenize } from "./utils.js";

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
    events: /event|events|meetup|meetups|conference|speaker|hackathon|pydata|gdg/.test(lowerQuery) ? 3 : 0,
  };

  model.nodes.forEach((node) => {
    scores[node.communityKey] += node.queryWeight;
  });

  return COMMUNITY_ORDER.reduce((best, key) => {
    if (!best || scores[key] > scores[best]) return key;
    return best;
  }, "people");
}

export function buildSearchModel(subgraph, results, query) {
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
        type: edge.type,
      };
    });

  const communityCounts = {
    people: 0,
    projects: 0,
    events: 0,
  };

  const nodeById = new Map();

  nodes.forEach((node) => {
    const resultMeta = resultMap.get(node.id);
    const neighbors = adjacency.get(node.id) || new Set();
    const labelTokens = tokenize(`${node.label || ""} ${node.id} ${node.type || ""}`);
    const termMatches = terms.filter((term) =>
      labelTokens.some((token) => token.includes(term) || term.includes(token))
    ).length;
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

export function getLayoutProfile(model, container) {
  const focus = model.focusCommunity;
  const dense = model.nodes.length > 15;
  const width = container.clientWidth || 700;
  const height = container.clientHeight || 420;
  const hx = Math.round(width * 0.34);
  const vy = Math.round(height * 0.32);

  const centers = {
    people: { x: -hx, y: -Math.round(vy * 0.2), z: 0 },
    projects: { x: hx, y: -Math.round(vy * 0.2), z: 0 },
    events: { x: 0, y: vy, z: 0 },
  };

  if (focus === "people") {
    centers.people = { x: 0, y: -Math.round(vy * 0.25), z: 0 };
    centers.projects = { x: hx, y: Math.round(vy * 0.15), z: 0 };
    centers.events = { x: -hx, y: vy, z: 0 };
  } else if (focus === "projects") {
    centers.projects = { x: 0, y: -Math.round(vy * 0.25), z: 0 };
    centers.people = { x: -hx, y: Math.round(vy * 0.15), z: 0 };
    centers.events = { x: hx, y: vy, z: 0 };
  } else if (focus === "events") {
    centers.events = { x: 0, y: 0, z: 0 };
    centers.people = { x: -hx, y: vy, z: 0 };
    centers.projects = { x: hx, y: vy, z: 0 };
  }

  return {
    focusCommunity: focus,
    charge: dense ? -420 : -520,
    distance: dense ? 115 : 145,
    clusterPull: dense ? 0.1 : 0.13,
    centers,
  };
}

export function seedNodePositions(model, layout) {
  const byCluster = {};
  model.nodes.forEach((node) => {
    const key = node.communityKey;
    if (!byCluster[key]) byCluster[key] = [];
    byCluster[key].push(node);
  });

  for (const [key, nodes] of Object.entries(byCluster)) {
    const anchor = layout.centers[key] || layout.centers.people;
    const resultNodes = nodes.filter((node) => node.isResult);
    const otherNodes = nodes.filter((node) => !node.isResult);
    const allOrdered = [...resultNodes, ...otherNodes];
    const ringSize = Math.max(Math.ceil(Math.sqrt(allOrdered.length)), 1);

    allOrdered.forEach((node, index) => {
      const ring = Math.floor(index / ringSize);
      const count = Math.min(ringSize, allOrdered.length - ring * ringSize);
      const offset = ring % 2 === 0 ? 0 : Math.PI / count;
      const angle = offset + (2 * Math.PI * (index % ringSize)) / count;
      const radius = 72 + ring * 100;
      node.x = anchor.x + Math.cos(angle) * radius;
      node.y = anchor.y + Math.sin(angle) * radius * 0.82;
      node.vx = 0;
      node.vy = 0;
      node.z = 0;
    });
  }
}
