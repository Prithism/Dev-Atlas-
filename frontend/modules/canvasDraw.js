import { EDGE_HIGHLIGHT, NODE_PALETTE } from "./config.js";
import { clamp } from "./utils.js";

const LABEL_MAX = 22;
const LABEL_SLICE = 19;

export function truncateLabel(label = "") {
  return label.length > LABEL_MAX ? `${label.slice(0, LABEL_SLICE)}...` : label;
}

export function roundedRect(ctx, x, y, width, height, radius) {
  ctx.beginPath();
  ctx.moveTo(x + radius, y);
  ctx.lineTo(x + width - radius, y);
  ctx.arcTo(x + width, y, x + width, y + radius, radius);
  ctx.lineTo(x + width, y + height - radius);
  ctx.arcTo(x + width, y + height, x + width - radius, y + height, radius);
  ctx.lineTo(x + radius, y + height);
  ctx.arcTo(x, y + height, x, y + height - radius, radius);
  ctx.lineTo(x, y + radius);
  ctx.arcTo(x, y, x + radius, y, radius);
  ctx.closePath();
}

function drawSelectionGlow(ctx, size) {
  const glow = ctx.createRadialGradient(0, 0, size * 0.5, 0, 0, size * 3.6);
  glow.addColorStop(0, "rgba(255, 93, 115, 0.55)");
  glow.addColorStop(0.45, "rgba(255, 93, 115, 0.18)");
  glow.addColorStop(1, "rgba(255, 93, 115, 0)");
  ctx.fillStyle = glow;
  ctx.beginPath();
  ctx.arc(0, 0, size * 3.6, 0, Math.PI * 2);
  ctx.fill();
}

function drawResultGlow(ctx, size, palette) {
  const glow = ctx.createRadialGradient(0, 0, 0, 0, 0, size * 2.6);
  glow.addColorStop(0, palette.glowI);
  glow.addColorStop(1, palette.glowO);
  ctx.fillStyle = glow;
  ctx.beginPath();
  ctx.arc(0, 0, size * 2.6, 0, Math.PI * 2);
  ctx.fill();
}

function drawRepoShape(ctx, size, isSelected, globalScale) {
  const square = size * 1.08;
  roundedRect(ctx, -square, -square, square * 2, square * 2, square * 0.32);
  ctx.fill();
  ctx.stroke();
  ctx.strokeStyle = isSelected ? EDGE_HIGHLIGHT : "rgba(255,255,255,0.55)";
  ctx.lineWidth = 1.2 / globalScale;
  ctx.lineCap = "round";
  for (let index = -1; index <= 1; index += 1) {
    const lineWidth = index === 0 ? square * 0.7 : square * 0.45;
    ctx.beginPath();
    ctx.moveTo(-lineWidth, index * square * 0.38);
    ctx.lineTo(lineWidth, index * square * 0.38);
    ctx.stroke();
  }
}

function drawEventShape(ctx, size, isSelected) {
  const diamond = size * 1.22;
  ctx.beginPath();
  ctx.moveTo(0, -diamond);
  ctx.lineTo(diamond * 0.82, 0);
  ctx.lineTo(0, diamond);
  ctx.lineTo(-diamond * 0.82, 0);
  ctx.closePath();
  ctx.fill();
  ctx.stroke();
  ctx.fillStyle = isSelected ? EDGE_HIGHLIGHT : "rgba(255,255,255,0.6)";
  ctx.beginPath();
  ctx.arc(0, 0, diamond * 0.22, 0, Math.PI * 2);
  ctx.fill();
}

function drawPersonShape(ctx, size, isSelected, globalScale) {
  ctx.beginPath();
  ctx.arc(0, 0, size, 0, Math.PI * 2);
  ctx.fill();
  ctx.stroke();
  ctx.fillStyle = isSelected ? EDGE_HIGHLIGHT : "rgba(255,255,255,0.5)";
  ctx.strokeStyle = isSelected ? EDGE_HIGHLIGHT : "rgba(255,255,255,0.4)";
  ctx.lineWidth = 1 / globalScale;
  ctx.beginPath();
  ctx.arc(0, -size * 0.22, size * 0.26, 0, Math.PI * 2);
  ctx.fill();
  ctx.beginPath();
  ctx.arc(0, size * 0.55, size * 0.52, Math.PI, 0);
  ctx.stroke();
}

function drawSelectionRing(ctx, size, globalScale, dimmed) {
  ctx.strokeStyle = EDGE_HIGHLIGHT;
  ctx.lineWidth = 1.5 / globalScale;
  ctx.globalAlpha = 0.7;
  ctx.setLineDash([5 / globalScale, 4 / globalScale]);
  ctx.beginPath();
  ctx.arc(0, 0, size * 2.2, 0, Math.PI * 2);
  ctx.stroke();
  ctx.setLineDash([]);
  ctx.globalAlpha = dimmed ? 0.12 : 1.0;
}

function drawLabel(ctx, label, size, globalScale, isSelected) {
  const fontSize = clamp(11 / globalScale, 8.5, 13);
  ctx.font = `600 ${fontSize}px Inter, system-ui, sans-serif`;
  const textWidth = ctx.measureText(label).width;
  const padX = 7 / globalScale;
  const padY = 4 / globalScale;
  const boxWidth = textWidth + padX * 2;
  const boxHeight = fontSize + padY * 2;
  const boxX = size + 7 / globalScale;
  const boxY = -boxHeight / 2;
  const boxRadius = Math.min(boxHeight * 0.38, 5 / globalScale);

  ctx.shadowColor = "rgba(0,0,0,0.18)";
  ctx.shadowBlur = 5 / globalScale;
  ctx.fillStyle = isSelected ? "rgba(15,15,26,0.94)" : "rgba(255,255,255,0.93)";
  roundedRect(ctx, boxX, boxY, boxWidth, boxHeight, boxRadius);
  ctx.fill();
  ctx.shadowBlur = 0;
  ctx.fillStyle = isSelected ? "#ffd84d" : "#111827";
  ctx.fillText(label, boxX + padX, boxY + padY + fontSize * 0.83);
}

export function drawNode(node, ctx, globalScale, { highlightedNodeId, adjacency }) {
  const isSelected = node.id === highlightedNodeId;
  const hasFocus = highlightedNodeId !== null;
  const neighbors = hasFocus ? (adjacency?.get(highlightedNodeId) || new Set()) : new Set();
  const isConnected = hasFocus && neighbors.has(node.id);
  const dimmed = hasFocus && !isSelected && !isConnected;
  const size = node.visualSize || 7;
  const palette = NODE_PALETTE[node.type] || NODE_PALETTE.person;

  ctx.save();
  ctx.globalAlpha = dimmed ? 0.12 : 1.0;
  ctx.translate(node.x, node.y);

  if (isSelected) {
    drawSelectionGlow(ctx, size);
  } else if (node.isResult && !hasFocus) {
    drawResultGlow(ctx, size, palette);
  }

  ctx.fillStyle = isSelected ? "#0f0f1a" : palette.fill;
  ctx.strokeStyle = isSelected ? EDGE_HIGHLIGHT : (isConnected ? "#ffffff" : palette.stroke);
  ctx.lineWidth = (isSelected ? 2.5 : isConnected ? 2.2 : 1.4) / globalScale;

  if (node.type === "repo") {
    drawRepoShape(ctx, size, isSelected, globalScale);
  } else if (node.type === "event") {
    drawEventShape(ctx, size, isSelected);
  } else {
    drawPersonShape(ctx, size, isSelected, globalScale);
  }

  if (isSelected) drawSelectionRing(ctx, size, globalScale, dimmed);

  const showLabel = isSelected || node.isResult || (globalScale > 2.0 && node.degree > 0);
  if (showLabel) drawLabel(ctx, truncateLabel(node.label || node.id), size, globalScale, isSelected);

  ctx.restore();
}
