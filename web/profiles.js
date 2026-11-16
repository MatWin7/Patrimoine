// Profils (D-098) : plusieurs personnes sur le même ordinateur, une base chacune.
// Ce script tourne avant app.js : il demande au serveur quel profil est ouvert pour ce
// navigateur. Aucun ouvert : écran « Qui consulte ? ». Sinon (ou sans profils) : il range les
// réglages du navigateur sous ce profil puis charge app.js. Le jeton de session est un cookie
// HttpOnly que ce script ne lit jamais ; aucun montant ne passe par ici.
(() => {
  const $ = (id) => document.getElementById(id);
  const JSON_HEADERS = { "Content-Type": "application/json" };

  // Requête vers l'API des profils ; annonce le profil affiché (X-Profile-Id), comme app.js.
  async function send(path, { method = "POST", body, raw, json = true } = {}) {
    const headers = raw ? { "Content-Type": "application/octet-stream" } : { ...JSON_HEADERS };
    if (window.patrimoineProfile) headers["X-Profile-Id"] = window.patrimoineProfile.id;
    const res = await fetch(path, { method, headers, body: raw ?? (body === undefined ? undefined : JSON.stringify(body)) });
    if (res.ok) return res.status === 204 || !json ? res : res.json();
    let detail = "Action impossible pour l'instant.";
    try {
      const data = await res.json();
      if (typeof data.detail === "string") detail = data.detail;
      else if (Array.isArray(data.detail)) detail = data.detail.map((e) => e.msg).join(" ; ");
    } catch {
      // réponse en texte (refus du serveur) : message générique
    }
    throw new Error(detail);
  }

  const call = (path, body) => send(path, { body: body ?? {} });

  function showError(el, text) {
    el.textContent = text;
    el.hidden = !text;
  }

  // Pastille : initiale du nom sur la couleur du profil.
  function avatar(profile, el = document.createElement("span")) {
    el.className = "avatar";
    el.style.backgroundColor = profile.color; // pas le raccourci « background » : il réinitialise la taille (cover)
    el.style.backgroundImage = "";
    if (profile.photo) {
      // Photo choisie : elle remplace la lettre, qui n'est plus écrite du tout (data URL
      // gardée dans profils.json).
      el.textContent = "";
      el.classList.add("photo");
      el.style.backgroundImage = `url("${profile.photo}")`;
    } else {
      el.textContent = profile.name.trim().charAt(0).toUpperCase();
    }
    el.setAttribute("aria-hidden", "true");
    return el;
  }

  function loadApp() {
    const script = document.createElement("script");
    script.src = "app.js";
    document.body.append(script);
  }

  // --- Écran « Qui consulte ? » -----------------------------------------------------------

  function showPicker(state) {
    document.querySelector("main").hidden = true;
    $("profile-picker").hidden = false;
    const form = $("picker-code");
    let chosen = null;

    async function open(profile, code = "") {
      try {
        await call(`/api/profiles/${encodeURIComponent(profile.id)}/open`, { code });
        location.reload();
      } catch (err) {
        showError($("picker-error"), err.message);
        form.code.select();
      }
    }

    $("picker-list").replaceChildren(...state.profiles.map((p) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "picker-profile";
      const name = document.createElement("span");
      name.textContent = p.name;
      button.append(avatar(p), name);
      if (p.has_code) {
        const lock = document.createElement("span");
        lock.className = "muted small";
        lock.textContent = "code";
        button.append(lock);
      }
      button.addEventListener("click", () => {
        if (!p.has_code) return open(p);
        chosen = p;
        $("picker-name").textContent = p.name;
        $("picker-list").hidden = true;
        form.hidden = false;
        form.reset();
        showError($("picker-error"), "");
        form.code.focus();
      });
      return button;
    }));

    form.addEventListener("submit", (e) => {
      e.preventDefault();
      if (chosen) open(chosen, form.code.value);
    });
    $("picker-back").addEventListener("click", () => {
      form.hidden = true;
      $("picker-list").hidden = false;
    });
  }

  // --- Profil ouvert : nom en haut de la page, code ---------------------------------------

  // Les autres profils, dans la fenêtre du bouton en haut à droite : un clic ouvre le profil
  // choisi (le serveur remplace la session), avec son code s'il en a un, sans repasser par
  // l'écran « Qui consulte ? ».
  function showOthers(profile, profiles) {
    const others = profiles.filter((p) => p.id !== profile.id);
    const box = $("profile-others");
    const form = $("form-profile-open");
    box.hidden = !others.length;
    $("profile-others-title").hidden = !others.length;
    box.setAttribute("aria-labelledby", "profile-others-title");
    form.hidden = true;
    let chosen = null;
    async function open(target, code = "") {
      try {
        await call(`/api/profiles/${encodeURIComponent(target.id)}/open`, { code });
        location.reload();
      } catch (err) {
        showError($("profile-open-error"), err.message);
        form.hidden = false;
        form.code.select();
      }
    }
    box.replaceChildren(...others.map((p) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "picker-profile-row";
      const name = document.createElement("span");
      name.textContent = p.name;
      button.append(avatar(p), name);
      if (p.has_code) {
        const lock = document.createElement("span");
        lock.className = "muted small";
        lock.textContent = "code";
        button.append(lock);
      }
      button.addEventListener("click", () => {
        if (!p.has_code) return open(p);
        chosen = p;
        $("profile-open-name").textContent = p.name;
        form.hidden = false;
        form.reset();
        showError($("profile-open-error"), "");
        form.code.focus();
      });
      return button;
    }));
    form.onsubmit = (e) => {
      e.preventDefault();
      if (chosen) open(chosen, form.code.value);
    };
    $("profile-open-cancel").onclick = () => {
      form.hidden = true;
      chosen = null;
    };
  }

  // Avis du profil Démo : masqué par son ✕ jusqu'au prochain lancement (il flotte sur la page).
  let demoNoticeClosed = false;
  $("demo-banner-close").onclick = () => {
    demoNoticeClosed = true;
    $("demo-banner").hidden = true;
  };

  function showCurrent(profile, state) {
    $("demo-banner").hidden = !profile.demo || demoNoticeClosed;
    $("form-profile-code").hidden = !!profile.demo; // données fictives : pas de code à proposer
    const toggle = $("profile-toggle");
    toggle.hidden = false;
    avatar(profile, $("profile-avatar"));
    $("profile-name").textContent = profile.name;
    toggle.setAttribute("aria-label", `Profil : ${profile.name}`);
    $("profile-panel-title").textContent = profile.name;
    showOthers(profile, state.profiles);

    // Photo : réduite en vignette 128 px dans le navigateur (l'original ne quitte pas la page),
    // puis envoyée au serveur qui la range dans profils.json.
    const fileInput = $("profile-photo-file");
    const syncPhoto = () => {
      avatar(profile, $("profile-photo-preview"));
      avatar(profile, $("profile-avatar"));
      const inCard = document.querySelector(`#profiles-list li[data-id="${profile.id}"] .avatar`);
      if (inCard) avatar(profile, inCard); // la carte « Profils » de Réglages suit tout de suite
      $("profile-photo-remove").hidden = !profile.photo;
    };
    syncPhoto();
    async function savePhoto(photo) {
      try {
        const updated = await fetch(`/api/profiles/${encodeURIComponent(profile.id)}/photo`, {
          method: "PUT",
          headers: { ...JSON_HEADERS, "X-Profile-Id": profile.id },
          body: JSON.stringify({ photo }),
        }).then(async (res) => {
          const data = await res.json().catch(() => ({}));
          if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Photo refusée.");
          return data;
        });
        Object.assign(profile, updated);
        syncPhoto();
        showError($("profile-photo-error"), "");
      } catch (err) {
        showError($("profile-photo-error"), err.message);
      }
    }
    // Recadrage : un cadre carré (le rond de la pastille y est dessiné) que l'on déplace à la
    // souris ou au doigt et que l'on agrandit avec le curseur. Seule la zone choisie est
    // gardée, réduite à 128 px ; l'original ne quitte pas la page.
    function cropPhoto(file) {
      return new Promise((resolve, reject) => {
        const url = URL.createObjectURL(file);
        const img = new Image();
        img.onerror = () => {
          URL.revokeObjectURL(url);
          reject(new Error("Image illisible."));
        };
        img.onload = () => {
          const dlg = $("dlg-crop");
          const canvas = $("crop-canvas");
          const zoom = $("crop-zoom");
          const ctx = canvas.getContext("2d");
          const view = canvas.width;
          const short = Math.min(img.width, img.height);
          const crop = { cx: img.width / 2, cy: img.height / 2 };
          zoom.value = "1";
          const side = () => short / Number(zoom.value); // côté du carré, en pixels de l'image
          const clamp = () => {
            const half = side() / 2;
            crop.cx = Math.min(img.width - half, Math.max(half, crop.cx));
            crop.cy = Math.min(img.height - half, Math.max(half, crop.cy));
          };
          const paint = (target, size, guide) => {
            clamp();
            const c = target.getContext("2d");
            c.clearRect(0, 0, size, size);
            c.drawImage(img, crop.cx - side() / 2, crop.cy - side() / 2, side(), side(), 0, 0, size, size);
            if (!guide) return;
            c.fillStyle = "rgb(0 0 0 / 50%)"; // hors du rond : ce que la pastille ne montrera pas
            c.beginPath();
            c.rect(0, 0, size, size);
            c.arc(size / 2, size / 2, size / 2 - 2, 0, Math.PI * 2, true);
            c.fill("evenodd");
          };
          const draw = () => paint(canvas, view, true);
          let last = null;
          canvas.onpointerdown = (e) => {
            last = e;
            canvas.setPointerCapture(e.pointerId);
          };
          canvas.onpointermove = (e) => {
            if (!last) return;
            const k = side() / view;
            crop.cx -= (e.clientX - last.clientX) * k * (view / canvas.clientWidth);
            crop.cy -= (e.clientY - last.clientY) * k * (view / canvas.clientHeight);
            last = e;
            draw();
          };
          canvas.onpointerup = canvas.onpointercancel = () => {
            last = null;
          };
          zoom.oninput = draw;
          let result = null;
          $("crop-save").onclick = () => {
            const out = document.createElement("canvas");
            out.width = out.height = 128;
            paint(out, 128, false);
            result = out.toDataURL("image/jpeg", 0.85);
            dlg.close();
          };
          dlg.addEventListener("close", () => {
            URL.revokeObjectURL(url);
            resolve(result);
          }, { once: true });
          draw();
          dlg.showModal();
        };
        img.src = url;
      });
    }
    fileInput.addEventListener("change", async () => {
      const file = fileInput.files[0];
      fileInput.value = "";
      if (!file) return;
      try {
        const photo = await cropPhoto(file);
        if (photo) await savePhoto(photo);
      } catch (err) {
        showError($("profile-photo-error"), err.message);
      }
    });
    $("profile-photo-remove").addEventListener("click", () => savePhoto(null));

    const form = $("form-profile-code");
    const syncCode = () => {
      $("profile-code-current").hidden = !profile.has_code;
      $("profile-code-remove").hidden = !profile.has_code;
      $("profile-code-title").firstChild.textContent = profile.has_code ? "Changer le code " : "Choisir un code ";
    };
    syncCode();
    async function save(newCode) {
      try {
        const updated = await fetch(`/api/profiles/${encodeURIComponent(profile.id)}/code`, {
          method: "PUT",
          headers: { ...JSON_HEADERS, "X-Profile-Id": profile.id },
          body: JSON.stringify({ current: form.current.value, new: newCode }),
        }).then(async (res) => {
          const data = await res.json().catch(() => ({}));
          if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Code refusé.");
          return data;
        });
        Object.assign(profile, updated);
        form.reset();
        syncCode();
        showError($("profile-code-error"), "");
        if (typeof showMessage === "function") showMessage(newCode ? "Code enregistré." : "Code retiré.");
      } catch (err) {
        showError($("profile-code-error"), err.message);
      }
    }
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      save(form.new.value);
    });
    $("profile-code-remove").addEventListener("click", () => save(null));
  }

  // --- Carte « Profils » de Réglages : ajouter, renommer, exporter, restaurer, supprimer -------

  function linkButton(text, className, onClick) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = className;
    button.textContent = text;
    button.addEventListener("click", onClick);
    return button;
  }

  // Petit formulaire sous une ligne de la liste (renommer, supprimer) ; un seul ouvert à la fois.
  function openInline(li, build) {
    document.querySelectorAll(".profile-inline").forEach((el) => el.remove());
    const form = document.createElement("form");
    form.className = "profile-inline";
    const error = document.createElement("p");
    error.className = "ko small";
    error.hidden = true;
    build(form, error);
    form.append(error);
    li.after(form);
    form.querySelector("input")?.focus();
  }

  function field(label, input) {
    const wrap = document.createElement("label");
    wrap.append(label, input);
    return wrap;
  }

  function input(name, props = {}) {
    return Object.assign(document.createElement("input"), { name, maxLength: 64, autocomplete: "off", ...props });
  }

  function actions(submitText, submitClass = "") {
    const box = document.createElement("div");
    box.className = "picker-actions";
    const cancel = linkButton("Annuler", "", (e) => e.target.closest("form").remove());
    const submit = Object.assign(document.createElement("button"), { type: "submit", textContent: submitText, className: submitClass });
    box.append(cancel, submit);
    return box;
  }

  function renameForm(li, p) {
    openInline(li, (form, error) => {
      const name = input("name", { value: p.name, maxLength: 40, required: true });
      form.append(field("Nouveau nom", name), actions("Renommer"));
      form.addEventListener("submit", async (e) => {
        e.preventDefault();
        try {
          await send(`/api/profiles/${encodeURIComponent(p.id)}`, { method: "PATCH", body: { name: name.value } });
          location.reload();
        } catch (err) {
          showError(error, err.message);
        }
      });
    });
  }

  function deleteForm(li, p) {
    openInline(li, (form, error) => {
      const warning = document.createElement("p");
      warning.className = "delete-warning small";
      warning.textContent = `Le profil ${p.name} et ses données quittent l'app. Une sauvegarde est faite, et sa base est rangée dans la corbeille du dossier des données : rien n'est effacé du disque.`;
      const confirm = input("confirm", { maxLength: 40, required: true, placeholder: p.name });
      form.append(warning, field(`Retape « ${p.name} » pour confirmer`, confirm));
      const code = input("code", { type: "password", autocomplete: "current-password" });
      if (p.has_code) form.append(field(`Code de ${p.name}`, code));
      form.append(actions("Supprimer", "danger"));
      form.addEventListener("submit", async (e) => {
        e.preventDefault();
        try {
          await send(`/api/profiles/${encodeURIComponent(p.id)}`, { method: "DELETE", body: { confirm: confirm.value, code: code.value } });
          location.reload();
        } catch (err) {
          showError(error, err.message);
        }
      });
    });
  }

  async function exportProfile(p) {
    try {
      const res = await send(`/api/profiles/${encodeURIComponent(p.id)}/export`, { method: "GET", json: false });
      const name = /filename="([^"]+)"/.exec(res.headers.get("Content-Disposition") ?? "")?.[1] ?? "patrimoine.db";
      const link = document.createElement("a");
      link.href = URL.createObjectURL(await res.blob());
      link.download = name;
      link.click();
      setTimeout(() => URL.revokeObjectURL(link.href), 1000);
    } catch (err) {
      showError($("profile-new-error"), err.message);
    }
  }

  async function addDemo() {
    try {
      await call("/api/profiles/demo");
      location.reload();
    } catch (err) {
      showError($("profile-new-error"), err.message);
    }
  }

  function showManageCard(state) {
    const list = $("profiles-list");
    const current = state.current;
    list.replaceChildren(...state.profiles.map((p) => {
      const li = document.createElement("li");
      li.dataset.id = p.id;
      const name = document.createElement("span");
      name.className = "profile-row-name";
      name.textContent = p.name;
      li.append(avatar(p), name);
      if (current && p.id === current.id) {
        // À gauche, collé au nom (le reste de la ligne : les boutons, à droite).
        const tag = document.createElement("span");
        tag.className = "muted small";
        tag.textContent = " (Actif)";
        name.append(tag);
      }
      const tools = document.createElement("span");
      tools.className = "profile-row-actions";
      if (p.demo) {
        // Profil Démo : tout y est fictif, il se remet à neuf au lieu de s'exporter.
        tools.append(linkButton("Recréer", "link edit", () => addDemo()));
      } else {
        tools.append(linkButton("Renommer", "link edit", () => renameForm(li, p)));
        // Exporter : seulement ses propres données (le serveur refuse celles d'un autre).
        if (current && p.id === current.id) tools.append(linkButton("Exporter", "link edit", () => exportProfile(p)));
      }
      if (state.profiles.length > 1) tools.append(linkButton("Supprimer", "link", () => deleteForm(li, p)));
      li.append(tools);
      return li;
    }));
    list.hidden = !state.enabled;
    $("profile-demo-add").hidden = state.profiles.some((p) => p.demo);
    $("profile-demo-add").addEventListener("click", addDemo);

    const form = $("form-profile-new");
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      try {
        await call("/api/profiles", { name: form.name.value });
        location.reload();
      } catch (err) {
        showError($("profile-new-error"), err.message);
      }
    });

    // Restaurer : un fichier .db exporté devient un nouveau profil (jamais par-dessus un autre).
    const restore = $("form-profile-restore");
    restore.file.addEventListener("change", () => {
      const file = restore.file.files[0];
      if (file && !restore.name.value) {
        restore.name.value = file.name.replace(/\.db$/i, "").replace(/^patrimoine-/, "").replace(/-\d{4}-\d{2}-\d{2}$/, "");
      }
    });
    restore.addEventListener("submit", async (e) => {
      e.preventDefault();
      const file = restore.file.files[0];
      if (!file) return;
      const button = restore.querySelector("button[type=submit]");
      button.disabled = true;
      try {
        await send(`/api/profiles/restore?name=${encodeURIComponent(restore.name.value)}`, { raw: await file.arrayBuffer() });
        location.reload();
      } catch (err) {
        showError($("profile-restore-error"), err.message);
      } finally {
        button.disabled = false;
      }
    });
  }

  (async () => {
    let state = { enabled: false, profiles: [], current: null };
    try {
      const res = await fetch("/api/profiles");
      if (res.ok) state = await res.json();
    } catch {
      // serveur injoignable : app.js affichera son propre message
    }
    if (state.enabled && !state.current) {
      // Fond et mot d'accueil choisis (D-134) : posés avant d'afficher l'écran, sans éclair du
      // fond par défaut. Lecture en échec : l'écran s'affiche avec le fond du thème.
      await window.patrimoineAccueil?.ready;
      showPicker(state);
      return;
    }
    const current = state.enabled ? state.current : null;
    window.patrimoineProfile = current;
    // Le profil « Principal » garde les réglages d'avant les profils.
    window.patrimoinePrefPrefix = current && current.id !== "principal" ? `patrimoine.${current.id}.` : "patrimoine.";
    if (current) showCurrent(current, state);
    showManageCard(state);
    loadApp();
  })();
})();
