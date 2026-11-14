// Infobulles « ? » : <button class="tip" data-tip="texte">. Le texte s'affiche au survol, au focus
// clavier et au clic (utile au toucher), juste sous le bouton, ou au-dessus s'il manque de place.
// L'infobulle est dans la « top layer » (popover) pour passer au-dessus des fenêtres ouvertes.
(() => {
  const pop = document.createElement("div");
  pop.id = "tip-pop";
  pop.setAttribute("role", "tooltip");
  pop.setAttribute("popover", "manual");
  document.body.append(pop);

  let current = null;
  let pinned = false;

  function hide() {
    if (!current) return;
    current.removeAttribute("aria-describedby");
    current = null;
    pinned = false;
    if (pop.matches(":popover-open")) pop.hidePopover();
  }

  function show(btn) {
    if (current === btn) return;
    hide();
    current = btn;
    pop.textContent = btn.dataset.tip;
    btn.setAttribute("aria-describedby", "tip-pop");
    pop.showPopover();
    const gap = 8;
    const r = btn.getBoundingClientRect();
    const p = pop.getBoundingClientRect();
    const left = Math.min(Math.max(gap, r.left + r.width / 2 - p.width / 2), window.innerWidth - p.width - gap);
    const below = r.bottom + gap;
    const top = below + p.height > window.innerHeight - gap && r.top - gap - p.height > gap ? r.top - gap - p.height : below;
    pop.style.left = `${left}px`;
    pop.style.top = `${top}px`;
  }

  const tipOf = (e) => e.target.closest?.(".tip[data-tip]");

  document.addEventListener("mouseover", (e) => {
    const btn = tipOf(e);
    if (btn && !pinned) show(btn);
  });
  document.addEventListener("mouseout", (e) => {
    if (tipOf(e) && !pinned) hide();
  });
  document.addEventListener("focusin", (e) => {
    const btn = tipOf(e);
    if (btn) show(btn);
  });
  document.addEventListener("focusout", (e) => {
    if (tipOf(e)) hide();
  });
  document.addEventListener("click", (e) => {
    const btn = tipOf(e);
    if (btn) {
      e.preventDefault();
      if (current === btn && pinned) hide();
      else {
        hide();
        show(btn);
        pinned = true;
      }
    } else hide();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") hide();
  });
  window.addEventListener("scroll", hide, true);
  window.addEventListener("resize", hide);

  // Pour les textes construits par app.js.
  window.makeTip = (text) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "tip";
    btn.dataset.tip = text;
    btn.setAttribute("aria-label", "Plus d'infos");
    btn.textContent = "?";
    return btn;
  };
})();
