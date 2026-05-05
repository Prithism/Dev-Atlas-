export const API_BASE = window.location.protocol === "file:"
  ? "http://localhost:8000"
  : window.location.origin;

export const QUERY_TIMEOUT_MS = 45000;

export const EDGE_HIGHLIGHT = "#ff5d73";
export const EDGE_ACCENT = "#3b82f6";

export const COMMUNITY_ORDER = ["people", "projects", "events"];

export const COMMUNITY_NAMES = {
  people: "PEOPLE",
  projects: "PROJECTS",
  events: "EVENTS",
};

export const NODE_PALETTE = {
  person: {
    fill: "#fb7185",
    stroke: "#f43f5e",
    glowI: "rgba(251,113,133,0.35)",
    glowO: "rgba(251,113,133,0)",
  },
  repo: {
    fill: "#60a5fa",
    stroke: "#3b82f6",
    glowI: "rgba(96,165,250,0.30)",
    glowO: "rgba(96,165,250,0)",
  },
  event: {
    fill: "#fbbf24",
    stroke: "#f59e0b",
    glowI: "rgba(251,191,36,0.35)",
    glowO: "rgba(251,191,36,0)",
  },
};
