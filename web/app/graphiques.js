// --- Graphiques (Chart.js, copié dans web/vendor : aucune requête vers un CDN) ---------
// Chart.js dessine à partir de nombres JavaScript : les montants y sont convertis avec
// Number() uniquement pour placer les points à l'écran. Les infobulles réaffichent le texte
// exact reçu de l'API (formatDecimal), jamais un nombre recalculé.

// Couleurs lues dans styles.css (variables CSS) : le mode sombre suit sans code en plus.
// Une teinte dérivée (color-mix, D-115) est peinte sur un point puis relue : Chart.js ne sait
// éclaircir ou rendre transparente qu'une couleur écrite en rgb.
// Écran de téléphone : légende sous le graphique, moins de dates sur l'axe.
const isNarrow = () => window.innerWidth < 600;
const cssProbe = document.createElement("canvas").getContext("2d", { willReadFrequently: true });
const css = (name) => {
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  if (!value.startsWith("color-mix")) return value;
  cssProbe.clearRect(0, 0, 1, 1);
  cssProbe.fillStyle = value;
  cssProbe.fillRect(0, 0, 1, 1);
  const [r, g, b, a] = cssProbe.getImageData(0, 0, 1, 1).data;
  return a === 255 ? `rgb(${r} ${g} ${b})` : `rgb(${r} ${g} ${b} / ${(a / 255).toFixed(3)})`;
};
const CLASS_COLORS = { ETF: "--series-1", Actions: "--series-2", Crypto: "--series-3", "Espèces": "--neutral" };
// Sept couleurs bien distinctes (vérifiées pour les daltonismes courants), toujours dans cet
// ordre ; au-delà, l'œil ne les sépare plus : les plus petites parts passent dans « Autres ».
const SERIES = ["--series-1", "--series-2", "--series-3", "--series-4", "--series-5", "--series-6", "--series-7"];
// Couleurs au choix d'un actif (D-142) : les sept des graphiques, plus cinq réservées au choix
// (jamais attribuées automatiquement, pour ne pas changer les graphiques existants).
const PALETTE = [...SERIES, "--series-8", "--series-9", "--series-10", "--series-11", "--series-12"];
const otherColor = () => `color-mix(in srgb, ${css("--neutral")} 45%, ${css("--surface")})`;

// Couleurs des lignes (D-051, D-052) : celle choisie dans « Gérer » d'abord ; les autres
// lignes parmi les 7 plus grosses prennent, dans l'ordre du poids, les couleurs que personne
// n'a choisies ; au-delà, gris. Calculé sur le patrimoine complet, jamais sur une vue filtrée.
let lineColorCache = { portfolio: null, map: new Map() };

function lineColors() {
  const portfolio = lastPortfolio;
  if (lineColorCache.portfolio === portfolio) return lineColorCache.map;
  const map = new Map();
  for (const p of portfolio?.positions ?? []) if (p.color) map.set(p.asset, PALETTE[p.color - 1]);
  const free = SERIES.filter((s) => ![...map.values()].includes(s));
  const ranked = (portfolio?.groupings?.ligne?.all ?? []).filter((p) => p.label !== "Espèces");
  ranked.slice(0, SERIES.length).forEach((p) => {
    if (!map.has(p.label) && free.length) map.set(p.label, free.shift());
  });
  lineColorCache = { portfolio, map };
  return map;
}

// Couleur d'une classe : fixe pour ETF, actions, crypto (les plus courantes) ; les autres
// classes (fonds euros, immobilier, livrets...) prennent les couleurs restantes par ordre de
// poids dans le patrimoine ; au-delà, gris (D-056).
function classColorName(label) {
  if (CLASS_COLORS[label]) return CLASS_COLORS[label];
  const fixed = new Set(Object.values(CLASS_COLORS));
  const free = SERIES.filter((s) => !fixed.has(s));
  const ranked = (lastPortfolio?.groupings?.classe?.all ?? [])
    .map((p) => p.label)
    .filter((l) => !CLASS_COLORS[l] && l !== "Autres");
  const i = ranked.indexOf(label);
  return i >= 0 && i < free.length ? free[i] : null;
}

// Couleur d'une part. Classes : voir classColorName. Lignes : voir lineColors. Comptes, devises :
// rang de la part dans le patrimoine complet.
function partColor(by, label) {
  if (label === "Espèces") return css("--neutral");
  if (label === "Autres") return otherColor();
  if (by === "classe") {
    const name = classColorName(label);
    return name ? css(name) : otherColor();
  }
  if (by === "ligne") {
    const name = lineColors().get(label);
    return name ? css(name) : otherColor();
  }
  const ranked = (lastPortfolio?.groupings?.[by]?.all ?? []).filter((p) => p.label !== "Espèces");
  const i = ranked.findIndex((p) => p.label === label);
  return i >= 0 && i < SERIES.length ? css(SERIES[i]) : otherColor();
}
const charts = {};
let lastPortfolio = null; // patrimoine complet : couleurs, positions, alertes
let overviewPortfolio = null; // ce que montre Vue d'ensemble (filtré ou non)
let lastHistory = null;

function chartTheme() {
  Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
  Chart.defaults.font.size = 12;
  Chart.defaults.color = css("--text-2");
  Chart.defaults.borderColor = css("--border");
  Chart.defaults.plugins.legend.labels.usePointStyle = true;
  Chart.defaults.plugins.legend.labels.boxWidth = 8;
  Chart.defaults.plugins.tooltip.padding = 10;
  Chart.defaults.plugins.tooltip.cornerRadius = 8;
}

// Passage clair/sombre (système ou carte Apparence, theme.js) : graphiques recréés avec les nouvelles couleurs.
document.addEventListener("themechange", () => {
  for (const id of Object.keys(charts)) {
    charts[id].destroy();
    delete charts[id];
  }
  if (overviewPortfolio) renderCharts(overviewPortfolio);
  if (lastHistory) drawHistory(lastHistory);
  if (lastPerformance) renderPerformance(lastPerformance);
});

// Carte Apparence (D-133) : thème clair, sombre ou celui du système, gardé par theme.js.
function renderThemeChoice() {
  for (const button of $("theme-choice").querySelectorAll("button")) {
    const chosen = button.dataset.themeChoice === window.patrimoineTheme.get();
    button.classList.toggle("active", chosen);
    button.setAttribute("aria-pressed", String(chosen));
  }
}
$("theme-choice").addEventListener("click", (e) => {
  const button = e.target.closest("button[data-theme-choice]");
  if (!button) return;
  window.patrimoineTheme.set(button.dataset.themeChoice);
  renderThemeChoice();
});
renderThemeChoice();
const euros = new Intl.NumberFormat("fr-FR", { style: "currency", currency: "EUR", maximumFractionDigits: 0 });

// Crée le graphique au premier appel, puis remplace seulement ses données (sans animation)
// pour que l'actualisation automatique ne fasse pas « sauter » la page.
function drawChart(id, config) {
  if (typeof Chart === "undefined") return; // fichier vendor absent : le reste de la page marche
  chartTheme();
  if (charts[id]) {
    charts[id].data = config.data;
    charts[id].options = config.options; // infobulles liées aux nouvelles données
    charts[id].update("none");
  } else {
    charts[id] = new Chart($(id), config);
  }
}

// Légende des courbes : un vrai trait (plein ou en tirets, comme la courbe) au lieu du
// petit rectangle par défaut, dont le contour pointillé donnait un trait cassé et flou.
const lineLegend = {
  position: "top",
  align: "end",
  labels: {
    usePointStyle: true,
    pointStyle: "line",
    pointStyleWidth: 30,
    boxHeight: 6,
    // Épaisseur et tirets repris de la courbe (Chart.js ne lit pas l'épaisseur par défaut).
    generateLabels: (chart) =>
      Chart.defaults.plugins.legend.labels.generateLabels(chart).map((l) => {
        const ds = chart.data.datasets[l.datasetIndex];
        return { ...l, lineWidth: ds.borderWidth ?? 2.5, lineDash: ds.borderDash ?? [], strokeStyle: ds.borderColor };
      }),
  },
};

// Camembert : parts en EUR, pourcentage repris dans la légende (« ETF · 82,5 % »).
// `colorOf` donne la couleur d'une part ; `onPick` (facultatif) reçoit le libellé cliqué.
// Le total des parts s'écrit au centre de l'anneau (greffon propre à ce graphique).
function doughnutConfig(parts, colorOf, onPick) {
  const total = parts.reduce((sum, p) => sum + Number(p.value), 0);
  const centerTotal = {
    id: "centerTotal",
    afterDraw(chart) {
      const { ctx, chartArea: a } = chart;
      if (!a) return;
      const x = (a.left + a.right) / 2;
      const y = (a.top + a.bottom) / 2;
      const size = Math.max(11, Math.min(26, (a.right - a.left) / 9));
      ctx.save();
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillStyle = css("--text-3");
      ctx.font = `500 ${Math.round(size * 0.55)}px ${Chart.defaults.font.family}`;
      ctx.fillText("Total", x, y - size * 0.7);
      ctx.fillStyle = css("--text");
      ctx.font = `600 ${Math.round(size)}px ${Chart.defaults.font.family}`;
      ctx.fillText(euros.format(total), x, y + size * 0.4);
      ctx.restore();
    },
  };
  return {
    type: "doughnut",
    plugins: [centerTotal],
    data: {
      labels: parts.map((p) => `${p.display ?? p.label} · ${formatDecimal(p.pct)} %`),
      datasets: [{
        data: parts.map((p) => Number(p.value)),
        backgroundColor: parts.map((p, i) => colorOf(p, i)),
        borderColor: css("--surface"),
        borderWidth: 3,
        borderRadius: 4,
        spacing: 1,
        hoverOffset: 6,
      }],
    },
    options: {
      maintainAspectRatio: false,
      cutout: "72%",
      layout: { padding: isNarrow() ? 4 : { top: 4, bottom: 4, left: 8, right: 16 } },
      onClick: onPick ? (_, elements) => elements.length && onPick(parts[elements[0].index].label) : undefined,
      onHover: (event, elements) => {
        event.native.target.style.cursor = onPick && elements.length ? "pointer" : "default";
      },
      plugins: {
        legend: { position: isNarrow() ? "bottom" : "right", labels: { padding: 10, font: { size: 13 }, boxWidth: 10, boxHeight: 10 } },
        tooltip: {
          callbacks: {
            label: (ctx) => ` ${formatDecimal(parts[ctx.dataIndex].value, "EUR")} (${formatDecimal(parts[ctx.dataIndex].pct)} %)`,
          },
        },
      },
    },
  };
}

const classColor = (p) => partColor("classe", p.label);
// Détail d'une classe : nuances de la couleur de la classe, de la plus soutenue à la plus claire.
// Détail d'une classe : chaque ligne garde sa couleur (choisie dans « Gérer », sinon son rang) ;
// les lignes sans couleur prennent des nuances de gris pour rester distinctes. Espèces : nuances.
function detailColor(classLabel) {
  if (classLabel === "Espèces") return shadeColor("--neutral");
  const grey = shadeColor("--neutral");
  let greys = 0;
  return (p) => {
    const name = lineColors().get(p.label);
    return name ? css(name) : grey(p, greys++);
  };
}

const shadeColor = (base) => (p, i) => `color-mix(in srgb, ${css(base)} ${Math.max(30, 100 - i * 28)}%, ${css("--surface")})`;

// Plus-values par ligne ; `horizontal` pour la vue agrandie, lisible même avec 30 lignes.
function gainsConfig(lines, horizontal = false) {
  const value = { border: { display: false }, ticks: { callback: (v) => euros.format(v) } };
  const names = { grid: { display: false } };
  return {
    type: "bar",
    data: {
      labels: lines.map((p) => p.asset),
      datasets: [{
        data: lines.map((p) => Number(p.gain_eur)),
        backgroundColor: lines.map((p) => css(p.gain_eur.startsWith("-") ? "--ko" : "--ok")),
        borderRadius: 4,
        maxBarThickness: horizontal ? 22 : 56,
      }],
    },
    options: {
      maintainAspectRatio: false,
      indexAxis: horizontal ? "y" : "x",
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: (ctx) => {
              const p = lines[ctx.dataIndex];
              return ` ${formatDecimal(signed(p.gain_eur), "EUR")}${p.gain_pct ? ` (${formatPct(p.gain_pct)})` : ""}`;
            },
          },
        },
      },
      scales: horizontal ? { x: value, y: names } : { x: names, y: value },
    },
  };
}

const gainLines = (portfolio) => portfolio.positions.filter((p) => p.gain_eur !== null);
// Petit graphique : les plus grosses plus-values et moins-values seulement (en valeur
// absolue), la vue agrandie montre toutes les lignes.
const GAINS_MAX = 12;
function biggestGains(lines) {
  if (lines.length <= GAINS_MAX) return lines;
  const kept = new Set([...lines].sort((a, b) => Math.abs(Number(b.gain_eur)) - Math.abs(Number(a.gain_eur))).slice(0, GAINS_MAX));
  return lines.filter((p) => kept.has(p));
}

// --- Répartition : regrouper par classe, ligne, compte ou devise (D-051) ---------------
// Niveau des cibles (D-061) : par ligne ou par catégorie, gardé dans le navigateur.
const TARGET_KEY = PREF + "cible";
const targetPref = loadPref(TARGET_KEY, { level: "ligne" });
// Une ligne visible, le mode d'emploi dans l'infobulle (D-091).
const TARGET_HELP = {
  ligne: ["Donne une part visée (en %) aux lignes qui comptent pour toi.", "L'app montre l'écart de chaque ligne et répartit ton prochain versement pour t'en rapprocher, sans rien vendre. Les parts se calculent entre les lignes qui ont une cible."],
  categorie: ["Donne une part visée (en %) à tes catégories.", "Catégories choisies dans « Gérer » > Actifs ; à défaut, la classe de l'actif. Le montant à acheter dans une catégorie va sur sa plus grosse ligne : un seul ordre, moins de frais. Pour viser une nouvelle catégorie, donne-la d'abord à un actif."],
};

const GROUP_KEY = PREF + "repartition";
const groupPref = loadPref(GROUP_KEY, { by: "ligne" });
const GROUP_NAMES = { ligne: ["ligne", "lignes"], classe: ["classe", "classes"], categorie: ["catégorie", "catégories"], compte: ["compte", "comptes"], devise: ["devise", "devises"] };

function groupedParts(portfolio, by) {
  const g = portfolio.groupings?.[by];
  if (!g) return portfolio.allocation; // ancienne réponse de l'API : par classe
  const [one, many] = GROUP_NAMES[by];
  return g.top.map((p) => (p.label === "Autres" ? { ...p, display: `Autres (${g.others} ${g.others > 1 ? many : one})` } : p));
}

function pickPart(by, label) {
  openZoom("liste", by);
}

function renderCharts(portfolio) {
  overviewPortfolio = portfolio;
  const by = groupPref.by;
  for (const b of $("group-by").querySelectorAll("button")) b.classList.toggle("active", b.dataset.group === by);
  const parts = groupedParts(portfolio, by);
  drawChart("chart-allocation", doughnutConfig(parts, (p) => partColor(by, p.label), (label) => pickPart(by, label)));
  const others = portfolio.groupings?.[by]?.others ?? 0;
  $("allocation-note").textContent = others
    ? `Les ${others} plus petites ${GROUP_NAMES[by][1]} sont regroupées dans « Autres » : clique dessus pour tout voir.`
    : "Clique sur une part pour voir son détail.";
  const lines = gainLines(portfolio);
  const shown = biggestGains(lines);
  drawChart("chart-gains", gainsConfig(shown));
  $("gains-note").hidden = shown.length === lines.length;
  $("gains-note").textContent = `Les ${shown.length} plus gros écarts sur ${lines.length} lignes : agrandis pour tout voir.`;
  if ($("zoom").open) renderZoom(); // actualisation automatique : la fenêtre suit
}

for (const b of $("group-by").querySelectorAll("button")) {
  b.addEventListener("click", () => {
    groupPref.by = b.dataset.group;
    savePref(GROUP_KEY, groupPref);
    // Le nombre de parts change : graphique neuf plutôt qu'une mise à jour animée.
    charts["chart-allocation"]?.destroy();
    delete charts["chart-allocation"];
    if (overviewPortfolio) renderCharts(overviewPortfolio);
  });
}

// Toutes les parts d'un regroupement en barres horizontales : lisible même avec 100 lignes.
function partsBarConfig(parts, by) {
  return {
    type: "bar",
    data: {
      labels: parts.map((p) => p.label),
      datasets: [{
        data: parts.map((p) => Number(p.value)),
        backgroundColor: parts.map((p) => partColor(by, p.label)),
        borderRadius: 4,
        maxBarThickness: 22,
      }],
    },
    options: {
      maintainAspectRatio: false,
      indexAxis: "y",
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: (ctx) => ` ${formatDecimal(parts[ctx.dataIndex].value, "EUR")} (${formatDecimal(parts[ctx.dataIndex].pct)} %)`,
          },
        },
      },
      scales: {
        x: { border: { display: false }, ticks: { callback: (v) => euros.format(v) } },
        y: { grid: { display: false } },
      },
    },
  };
}

// --- Fenêtre « agrandir » ---------------------------------------------------------------
// <dialog> est une fenêtre native du navigateur : elle passe au-dessus de la page, se ferme
// avec Échap et n'ajoute aucune bibliothèque.

const zoom = { kind: null, detail: null };

function openZoom(kind, detail = null) {
  Object.assign(zoom, { kind, detail });
  const dialog = $("zoom");
  if (!dialog.open) dialog.showModal();
  renderZoom();
}

function zoomChip(text, active, onClick) {
  const b = document.createElement("button");
  b.type = "button";
  b.className = `chip${active ? " active" : ""}`;
  b.textContent = text;
  b.addEventListener("click", onClick);
  return b;
}

function renderZoom() {
  const portfolio = overviewPortfolio ?? lastPortfolio; // agrandissement de Vue d'ensemble
  if (!portfolio) return;
  // Un camembert et des barres ne se transforment pas l'un en l'autre : graphique neuf.
  charts["chart-zoom"]?.destroy();
  delete charts["chart-zoom"];
  const chips = [];
  let config;
  let note = "";
  if (zoom.kind === "liste") {
    const by = GROUP_NAMES[zoom.detail] ? zoom.detail : "ligne";
    for (const key of Object.keys(GROUP_NAMES)) {
      chips.push(zoomChip(`Par ${GROUP_NAMES[key][0]}`, key === by, () => openZoom("liste", key)));
    }
    const parts = portfolio.groupings?.[by]?.all ?? [];
    $("zoom-title").textContent = `Répartition par ${GROUP_NAMES[by][0]}`;
    config = partsBarConfig(parts, by);
    $("zoom-chart").style.height = `${Math.max(320, parts.length * 30 + 60)}px`;
    note = parts.length > SERIES.length + 1
      ? `${parts.length} ${GROUP_NAMES[by][1]}, en euros et en % du patrimoine. En gris : celles regroupées dans « Autres » sur la vue d'ensemble.`
      : `En euros et en % du patrimoine.`;
  } else {
    const lines = gainLines(portfolio);
    $("zoom-title").textContent = "Plus/moins-values latentes";
    config = gainsConfig(lines, true);
    // Une ligne = 34 px : au-delà de la fenêtre, la zone défile au lieu d'écraser les barres.
    $("zoom-chart").style.height = `${Math.max(320, lines.length * 34 + 60)}px`;
    note = "En euros, par ligne détenue.";
  }
  $("zoom-chips").replaceChildren(...chips);
  $("zoom-note").textContent = note;
  drawChart("chart-zoom", config);
}

// Bouton « agrandir » de la répartition : le détail par classe, ou la liste complète du
// regroupement choisi.
for (const b of document.querySelectorAll("[data-zoom]")) {
  b.addEventListener("click", () => {
    if (b.dataset.zoom === "allocation") openZoom("liste", groupPref.by);
    else openZoom(b.dataset.zoom);
  });
}
$("zoom").querySelector("[data-close]").addEventListener("click", () => $("zoom").close());
// Clic sur le fond sombre (hors de la fenêtre) : on ferme aussi.
$("zoom").addEventListener("click", (e) => {
  if (e.target === $("zoom")) $("zoom").close();
});

// Taux d'inflation après le dernier mois publié par Eurostat (D-067), gardé dans le navigateur.
const INFLATION_KEY = PREF + "inflation";
const inflationPref = loadPref(INFLATION_KEY, { rate: "2" });
$("inflation-rate").value = inflationPref.rate;
$("inflation-rate").addEventListener("change", (e) => {
  const rate = e.target.value.trim();
  if (rate === "" || !e.target.checkValidity()) {
    e.target.value = inflationPref.rate;
    return;
  }
  inflationPref.rate = rate;
  savePref(INFLATION_KEY, inflationPref);
  refreshHistory().catch((err) => showMessage(err.message, true));
});

async function refreshHistory() {
  const data = await api(`/api/history?${addExclude(new URLSearchParams({ inflation_rate: inflationPref.rate }))}`);
  const points = data.points;
  const notes = [];
  if (data.without_history.length) {
    notes.push(`Sans cours (courbe au prix des opérations) : ${data.without_history.join(", ")}.`);
  }
  if (data.errors.length) notes.push(`Historique non complété : ${data.errors.join(" ; ")}.`);
  notes.push("Titres seulement : les espèces n'ont pas d'historique.");
  notes.push("Argent net placé : achats moins ventes ; cadeaux et récompenses exclus, un arbitrage ne le change pas.");
  const inflation = data.inflation;
  const rate = `${String(inflation?.rate ?? inflationPref.rate).replace(".", ",")} % par an`;
  if (inflation?.last_month) {
    const [y, m] = inflation.last_month.split("-");
    notes.push(`Inflation : ${inflation.source} jusqu'à ${m}/${y}, lissée sur chaque année, puis ${rate} (taux saisi).`);
  } else if (inflation) {
    notes.push(`Eurostat indisponible : inflation au taux saisi de ${rate} sur toute la période.`);
  }
  const partial = data.without_history.length || data.errors.length;
  $("history-status").textContent = partial ? "Historique incomplet pour certaines lignes." : "";
  $("history-tip").dataset.tip = notes.join(" ");
  $("history-tip").hidden = false;
  drawHistory(points);
}

function drawHistory(points) {
  lastHistory = points;
  drawChart("chart-history", {
    type: "line",
    data: {
      labels: points.map((p) => p.date.split("-").reverse().join("/")), // 2025-06-02 -> 02/06/2025
      datasets: [
        {
          label: "Valeur des titres",
          data: points.map((p) => Number(p.value)),
          borderColor: css("--series-1"),
          backgroundColor: css("--series-1-fill"),
          fill: true,
        },
        {
          label: "Argent net placé", // achats moins ventes, cadeaux exclus (D-110)
          data: points.map((p) => Number(p.invested)),
          borderColor: css("--series-2"), // orange : tranche nettement sur le bleu de la valeur
          backgroundColor: css("--series-2"),
          borderDash: [6, 4],
          stepped: true, // ne bouge qu'aux achats et ventes
        },
        // Pouvoir d'achat de l'argent placé : il baisse quand les prix montent (D-045, D-110).
        ...(points.some((p) => p.invested_eroded !== null) ? [{
          label: "Placé moins l'inflation",
          data: points.map((p) => (p.invested_eroded === null ? null : Number(p.invested_eroded))),
          borderColor: css("--ko"), // rouge : une perte, lisible sur fond clair comme sombre
          backgroundColor: css("--ko"),
          borderDash: [3, 3],
          borderWidth: 2.5,
        }] : []),
      ],
    },
    options: {
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false }, // infobulle de toutes les courbes à la fois
      elements: { point: { radius: 0, hoverRadius: 4 }, line: { borderWidth: 2 } },
      plugins: {
        legend: lineLegend,
        tooltip: {
          callbacks: {
            label: (ctx) => {
              const p = points[ctx.dataIndex];
              const amount = [p.value, p.invested, p.invested_eroded][ctx.datasetIndex];
              const line = ` ${ctx.dataset.label} : ${formatDecimal(amount, "EUR")}`;
              // Inflation sur un an du mois survolé (D-112).
              if (ctx.datasetIndex !== 2 || p.inflation_pct === null) return line;
              return `${line} (inflation ${formatDecimal(p.inflation_pct)} %/an)`;
            },
            footer: (items) => {
              const p = points[items[0].dataIndex];
              const lines = [`Gain total : ${formatDecimal(signed(p.gain), "EUR")}`];
              // Ce que l'inflation a pris à l'argent placé, en pouvoir d'achat.
              if (p.inflation_loss !== null) lines.push(`Perdu par l'inflation : ${formatDecimal(p.inflation_loss, "EUR")}`);
              return lines;
            },
          },
        },
      },
      scales: {
        x: { grid: { display: false }, ticks: { maxTicksLimit: isNarrow() ? 3 : 8, maxRotation: 0 } },
        y: { border: { display: false }, ticks: { callback: (v) => euros.format(v) } },
      },
    },
  });
}
