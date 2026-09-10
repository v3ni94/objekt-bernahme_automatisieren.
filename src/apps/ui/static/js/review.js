/* Review Center: Tastenbelegung (H 2.6), Zielbereich-Felder, Eigentuemer- und Mietersuche, Kandidaten uebernehmen,
   Seitenvorschau blaettern, Aufteilen. Kein Framework, keine externen Quellen. */
(function () {
  "use strict";
  const form = document.getElementById("entscheidungsform");
  const liste = document.getElementById("fallliste");

  function showFields(category) {
    document.querySelectorAll(".felder").forEach(function (el) {
      el.hidden = el.dataset.for !== category;
    });
    document.querySelectorAll("#zielwahl a").forEach(function (a) {
      a.classList.toggle("aktiv", a.dataset.category === category);
    });
    ["subfolder", "document_type"].forEach(function (id) {
      const sel = document.getElementById(id);
      if (!sel) return;
      Array.from(sel.options).forEach(function (o) {
        if (!o.value) return;
        o.hidden = o.dataset.category !== category;
        if (o.hidden && o.selected) sel.value = "";
      });
    });
  }

  function setCategory(category) {
    const input = document.getElementById("category");
    if (!input) return;
    input.value = category;
    showFields(category);
  }

  function suggest(inputId, hiddenId, boxId, url, onPick) {
    const input = document.getElementById(inputId);
    const hidden = document.getElementById(hiddenId);
    const box = document.getElementById(boxId);
    if (!input || !box) return;
    let timer = null;
    input.addEventListener("input", function () {
      clearTimeout(timer);
      const q = input.value.trim();
      if (q.length < 2) { box.innerHTML = ""; return; }
      timer = setTimeout(function () {
        fetch(url + "?q=" + encodeURIComponent(q), { credentials: "same-origin" })
          .then(function (r) { return r.json(); })
          .then(function (data) {
            box.innerHTML = "";
            data.results.forEach(function (row) {
              const b = document.createElement("button");
              b.type = "button";
              b.className = "link vorschlag";
              b.textContent = row.name + (row.in_object ? "" : " (anderes Objekt)") +
                (row.units ? " " + row.units.map(function (u) { return u.unit + " " + (u.valid_from || "") + " bis " + (u.valid_to || "heute"); }).join(", ") : "");
              b.addEventListener("click", function () {
                hidden.value = row.id;
                input.value = row.name;
                box.innerHTML = "";
                if (onPick) onPick(row);
              });
              box.appendChild(b);
            });
          });
      }, 200);
    });
  }

  function pickOwner(row) {
    const unitSel = document.getElementById("unit_id");
    const assignment = document.getElementById("assignment_id");
    const hint = document.getElementById("zuordnung_hinweis");
    if (!unitSel) return;
    const inObject = (row.units || []).filter(function (u) { return document.querySelector('#unit_id option[value="' + u.unit_id + '"]'); });
    if (inObject.length === 1) {
      unitSel.value = inObject[0].unit_id;
      assignment.value = inObject[0].assignment_id;
      hint.textContent = "Zuordnung " + inObject[0].unit + " " + (inObject[0].valid_from || "") + " bis " + (inObject[0].valid_to || "heute") + " vorbelegt.";
    } else if (inObject.length === 0) {
      assignment.value = "";
      hint.textContent = "Keine Zuordnung dieses Eigentümers im Objekt: Einheit wählen und Eigentumsbeginn für die neue Zuordnung angeben.";
    } else {
      assignment.value = "";
      hint.textContent = inObject.length + " Einheiten dieses Eigentümers im Objekt: Einheit wählen.";
    }
  }

  if (form) {
    showFields(document.getElementById("category").value);
    document.querySelectorAll("#zielwahl a").forEach(function (a) {
      a.addEventListener("click", function (ev) { ev.preventDefault(); setCategory(a.dataset.category); });
    });
    suggest("owner_search", "owner_id", "owner_vorschlaege", form.dataset.ownerSearch, pickOwner);
    suggest("tenant_search", "tenant_id", "tenant_vorschlaege", form.dataset.tenantSearch, null);
    document.querySelectorAll(".kandidat").forEach(function (b) {
      b.addEventListener("click", function () {
        setCategory("05");
        document.getElementById("owner_id").value = b.dataset.owner;
        document.getElementById("unit_id").value = b.dataset.unit;
        document.getElementById("assignment_id").value = b.dataset.assignment;
        document.getElementById("owner_name").textContent = "Kandidat übernommen";
      });
    });
    const dtype = document.getElementById("document_type");
    dtype.addEventListener("change", function () {
      const o = dtype.selectedOptions[0];
      if (o && o.dataset.subfolder) document.getElementById("subfolder").value = o.dataset.subfolder;
      document.getElementById("period_year").required = o && o.dataset.period === "1";
    });
    const split = document.getElementById("splitform");
    if (split) {
      split.addEventListener("submit", function () {
        const units = JSON.parse(document.getElementById("units-json").textContent || "[]");
        const spec = document.getElementById("split_spec").value;
        const segments = [];
        spec.split(",").forEach(function (part) {
          const m = part.trim().match(/^(\d+)\s*-\s*(\d+)\s*:\s*(.+)$/);
          if (!m) return;
          const unit = units.find(function (u) { return u.label.replace(/\s+/g, "").toUpperCase() === m[3].replace(/\s+/g, "").toUpperCase(); });
          segments.push({ page_from: parseInt(m[1], 10), page_to: parseInt(m[2], 10), category: "05",
            subfolder: document.getElementById("subfolder").value, document_type: document.getElementById("document_type").value,
            period_year: document.getElementById("period_year").value, unit_id: unit ? unit.id : "", owner_unknown: unit ? "0" : "1" });
        });
        document.getElementById("segments").value = JSON.stringify(segments);
      });
    }
  }

  // Seitenvorschau
  const seiten = Array.from(document.querySelectorAll(".seite-inhalt"));
  let aktuelleSeite = 0;
  function zeigeSeite(i) {
    if (!seiten.length) return;
    aktuelleSeite = Math.max(0, Math.min(seiten.length - 1, i));
    seiten.forEach(function (s, idx) { s.hidden = idx !== aktuelleSeite; });
  }
  document.querySelectorAll(".seite").forEach(function (b) {
    b.addEventListener("click", function () { zeigeSeite(parseInt(b.dataset.page, 10) - 1); });
  });
  document.querySelectorAll(".toggle-text").forEach(function (b) {
    b.addEventListener("click", function () { const pre = b.closest(".seite-inhalt").querySelector(".seitentext"); pre.hidden = !pre.hidden; });
  });

  // Liste: Zeilenwahl
  let zeile = -1;
  function markiere(i) {
    if (!liste) return;
    const rows = liste.querySelectorAll("tbody tr[data-href]");
    if (!rows.length) return;
    zeile = Math.max(0, Math.min(rows.length - 1, i));
    rows.forEach(function (r, idx) { r.classList.toggle("gelb", idx === zeile); });
    rows[zeile].scrollIntoView({ block: "nearest" });
  }

  document.addEventListener("keydown", function (ev) {
    const tag = (ev.target.tagName || "").toLowerCase();
    const typing = tag === "input" || tag === "select" || tag === "textarea";
    if (ev.key === "?" && !typing) { alert("Tasten: J/K Fall wechseln, 1 bis 6 Zielbereich, E Eigentümer, N Einheit, Y Jahr, T Unterart, B Speichern, Z Zurückstellen, V Verwerfen, A Zuweisen, D Dublette, S Aufteilen, W Wiedereröffnen, Leertaste nächste Seite, Esc zurück zur Liste"); return; }
    if (ev.key === "Escape") { const back = document.querySelector('a[href^="/review/?"]'); if (back && form) window.location = back.href; return; }
    if (typing && ev.key !== "Enter") return;
    if (liste) {
      if (ev.key === "j" || ev.key === "ArrowDown") { markiere(zeile + 1); ev.preventDefault(); }
      if (ev.key === "k" || ev.key === "ArrowUp") { markiere(zeile - 1); ev.preventDefault(); }
      if (ev.key === " " && zeile >= 0) { const cb = liste.querySelectorAll("tbody tr[data-href]")[zeile].querySelector(".markierung"); if (cb) cb.checked = !cb.checked; ev.preventDefault(); }
      if (ev.key === "Enter" && ev.shiftKey) { const f = document.getElementById("sammelform"); if (f) f.submit(); return; }
      if (ev.key === "Enter" && zeile >= 0) { window.location = liste.querySelectorAll("tbody tr[data-href]")[zeile].dataset.href; }
      if (/^[1-6]$/.test(ev.key)) { const sel = document.querySelector('select[name="ziel"]'); if (sel) { sel.value = "0" + ev.key; document.getElementById("filterform").submit(); } }
      return;
    }
    if (!form) return;
    if (/^[1-6]$/.test(ev.key) && !typing) { setCategory("0" + ev.key); return; }
    const focus = { e: "owner_search", n: "unit_id", y: "period_year", t: "document_type" };
    if (focus[ev.key] && !typing) { const el = document.getElementById(focus[ev.key]); if (el) { el.focus(); ev.preventDefault(); } return; }
    if (ev.key === "j" && !typing) { const n = document.getElementById("nav-next"); if (n) window.location = n.href; return; }
    if (ev.key === "k" && !typing) { const p = document.getElementById("nav-prev"); if (p) window.location = p.href; return; }
    if (ev.key === " " && !typing) { zeigeSeite(aktuelleSeite + (ev.shiftKey ? -1 : 1)); ev.preventDefault(); return; }
    if (ev.key === "Enter" && ev.shiftKey) { form.querySelector('input[name="serie"]').value = "1"; form.submit(); return; }
    const keyed = document.querySelector('[data-key="' + ev.key.toLowerCase() + '"]');
    if (keyed && !typing && !keyed.disabled) { keyed.click(); ev.preventDefault(); }
  });
})();
