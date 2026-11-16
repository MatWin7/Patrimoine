// Page de démarrage réglable (D-134) : fond et mot d'accueil, communs à tous les profils de
// l'ordinateur (reglages.json du dossier des données). Ce script tourne avant profiles.js : il
// lit le réglage (GET /api/start répond sans profil ouvert) et le pose sur <html> avant que
// l'écran « Qui consulte ? » s'affiche. Il tient aussi les réglages de la carte Apparence.
// Une image choisie est réduite et réencodée en JPEG ici, dans la page : l'original n'est pas
// envoyé au serveur local, ses métadonnées (lieu, appareil) non plus.
(() => {
  const $ = (id) => document.getElementById(id);
  const MAX_SIDE = 2560; // plus grand côté gardé, en pixels : assez pour un écran 1440p
  const MAX_BYTES = 2_800_000; // sous la limite du serveur (3 Mo)
  const MAX_SOURCE_BYTES = 40_000_000;
  const QUALITIES = [0.86, 0.76, 0.66, 0.5];
  let state = null;

  function showError(text) {
    $("fond-error").textContent = text;
    $("fond-error").hidden = !text;
  }

  function showState(text, ok = true) {
    const el = $("accueil-state");
    el.textContent = text;
    el.className = `small ${ok ? "ok" : "ko"}`;
    el.hidden = !text;
  }

  // Pose le réglage sur la page : le fond par un attribut de <html> (les fonds sont dans
  // styles.css), l'image par une variable CSS, le mot d'accueil comme du texte.
  function apply(next) {
    state = next;
    const root = document.documentElement;
    root.dataset.fond = state.fond;
    if (/^\d+$/.test(String(state.image ?? ""))) {
      root.style.setProperty("--fond-image", `url("/api/start/image?v=${state.image}")`);
    } else {
      root.style.removeProperty("--fond-image");
    }
    $("picker-text").textContent = state.texte;
    render();
  }

  function render() {
    for (const button of $("fond-choice").querySelectorAll("button")) {
      const chosen = button.dataset.fond === state.fond;
      button.classList.toggle("active", chosen);
      button.setAttribute("aria-pressed", String(chosen));
      if (button.dataset.fond === "image") button.hidden = !state.image;
    }
    $("fond-image-remove").hidden = !state.image;
    $("fond-image-pick").textContent = state.image ? "Changer d'image…" : "Choisir une image…";
    const input = $("form-accueil").texte;
    input.placeholder = state.texte_defaut;
    input.maxLength = state.texte_max;
    // Texte d'origine : champ vide, le texte d'origine en filigrane.
    if (document.activeElement !== input) input.value = state.texte === state.texte_defaut ? "" : state.texte;
  }

  async function send(path, options = {}) {
    const headers = { ...options.headers };
    if (window.patrimoineProfile) headers["X-Profile-Id"] = window.patrimoineProfile.id;
    const res = await fetch(path, { ...options, headers });
    if (res.ok) return res.json();
    let detail = "Action impossible pour l'instant.";
    try {
      const data = await res.json();
      if (typeof data.detail === "string") detail = data.detail;
    } catch {
      // réponse en texte (refus du serveur) : message générique
    }
    throw new Error(detail);
  }

  const put = (body) => send("/api/start", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

  function loadImage(file) {
    return new Promise((resolve, reject) => {
      const url = URL.createObjectURL(file);
      const img = new Image();
      img.onload = () => {
        URL.revokeObjectURL(url);
        resolve(img);
      };
      img.onerror = () => {
        URL.revokeObjectURL(url);
        reject(new Error("Image illisible."));
      };
      img.src = url;
    });
  }

  // Réduit l'image à MAX_SIDE et la réencode en JPEG, en baissant la qualité tant qu'elle pèse
  // trop. Redessiner dans un canvas ne garde que les pixels.
  async function reduce(file) {
    if (!/^image\/(jpeg|png|webp)$/.test(file.type)) throw new Error("Image refusée : JPEG, PNG ou WebP.");
    if (file.size > MAX_SOURCE_BYTES) throw new Error("Image trop lourde : 40 Mo au plus.");
    const img = await loadImage(file);
    if (!img.naturalWidth || !img.naturalHeight) throw new Error("Image illisible.");
    const scale = Math.min(1, MAX_SIDE / Math.max(img.naturalWidth, img.naturalHeight));
    const canvas = document.createElement("canvas");
    canvas.width = Math.max(1, Math.round(img.naturalWidth * scale));
    canvas.height = Math.max(1, Math.round(img.naturalHeight * scale));
    const ctx = canvas.getContext("2d");
    ctx.fillStyle = "#ffffff"; // un PNG transparent n'a pas de fond : blanc plutôt que noir
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
    for (const quality of QUALITIES) {
      const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", quality));
      if (blob && blob.size <= MAX_BYTES) return blob;
    }
    throw new Error("Image trop lourde, même réduite.");
  }

  async function run(action) {
    showError("");
    try {
      apply(await action());
    } catch (err) {
      showError(err.message);
    }
  }

  $("fond-choice").addEventListener("click", (e) => {
    const button = e.target.closest("button[data-fond]");
    if (button && state && button.dataset.fond !== state.fond) run(() => put({ fond: button.dataset.fond }));
  });
  $("fond-image-pick").addEventListener("click", () => $("fond-image-file").click());
  $("fond-image-file").addEventListener("change", (e) => {
    const file = e.target.files[0];
    e.target.value = ""; // choisir deux fois le même fichier doit marcher
    if (!file) return;
    run(async () => send("/api/start/image", {
      method: "PUT",
      headers: { "Content-Type": "application/octet-stream" },
      body: await reduce(file),
    }));
  });
  $("fond-image-remove").addEventListener("click", () => run(() => send("/api/start/image", { method: "DELETE" })));
  $("form-accueil").addEventListener("submit", async (e) => {
    e.preventDefault();
    const input = e.target.texte;
    try {
      const next = await put({ texte: input.value });
      input.blur(); // le champ reprend la valeur enregistrée (espaces resserrés)
      apply(next);
      showState("Enregistré.");
    } catch (err) {
      showState(err.message, false);
    }
  });
  $("form-accueil").texte.addEventListener("input", () => showState(""));

  const ready = fetch("/api/start")
    .then((res) => (res.ok ? res.json() : Promise.reject(new Error("réglage illisible"))))
    .then(apply)
    .catch(() => {
      // serveur injoignable ou réponse inattendue : fond du thème et texte d'origine
    });
  window.patrimoineAccueil = { ready };
})();
