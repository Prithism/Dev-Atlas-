import { dom, setLoading, setResultCount } from "./dom.js";

export function setActiveResultCard(id) {
  document.querySelectorAll(".result-card").forEach((card) => {
    card.classList.toggle("active", card.id === `result-${id}`);
  });
}

export function renderResults(results, { onResultFocus } = {}) {
  setLoading(false);
  dom.resultsList.innerHTML = "";
  setResultCount(results.length);

  if (!results.length) {
    dom.resultsList.innerHTML = '<div class="empty-state">No matches found. Try a broader query to reveal new communities.</div>';
    return;
  }

  results.forEach((result, index) => {
    const card = document.createElement("article");
    card.className = "result-card";
    card.id = `result-${result.id}`;
    card.tabIndex = 0;
    card.setAttribute("role", "button");
    card.setAttribute("aria-label", `Focus ${result.name} on the graph`);

    const header = document.createElement("div");
    header.className = "result-header";

    const name = document.createElement("span");
    name.className = "result-name";
    name.textContent = `[${index + 1}] ${result.name}`;

    const score = document.createElement("span");
    score.className = "result-score";
    score.textContent = typeof result.score === "number" ? result.score.toFixed(2) : "--";

    header.append(name, score);
    card.appendChild(header);

    if (Array.isArray(result.evidence) && result.evidence.length) {
      const evidenceWrap = document.createElement("div");
      evidenceWrap.className = "result-evidence";
      const evidenceList = document.createElement("ul");

      result.evidence.forEach((item) => {
        const evidenceItem = document.createElement("li");
        evidenceItem.textContent = item;
        evidenceList.appendChild(evidenceItem);
      });

      evidenceWrap.appendChild(evidenceList);
      card.appendChild(evidenceWrap);
    }

    if (result.url) {
      const link = document.createElement("a");
      link.className = "result-url";
      link.href = result.url;
      link.target = "_blank";
      link.rel = "noreferrer";
      link.textContent = result.url.replace(/^https?:\/\//, "");
      link.addEventListener("click", (event) => event.stopPropagation());
      card.appendChild(link);
    }

    const activate = () => {
      setActiveResultCard(result.id);
      onResultFocus?.(result.id);
    };

    card.addEventListener("click", activate);
    card.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        activate();
      }
    });

    dom.resultsList.appendChild(card);
  });
}
