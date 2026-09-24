/* Statusseite Mueller Holding AG: liest Messwerte aus Prometheus (nur Leseabfragen ueber nginx) und zeichnet
   Kennzahlen, Tabellen und Verlaeufe (uPlot). Kein Schreibzugriff, keine Fremddienste. */
(() => {
  "use strict";

  const BEREICHE = [
    ["15m", 15 * 60, "15 Min"],
    ["1h", 3600, "1 Std"],
    ["24h", 86400, "24 Std"],
    ["7d", 7 * 86400, "7 Tage"],
    ["30d", 30 * 86400, "30 Tage"],
    ["1y", 365 * 86400, "1 Jahr"],
  ];
  const FARBEN = ["#E3AC48", "#2E2D2E", "#9F9F9F", "#B8862B", "#5C5B5C", "#D9C58F", "#7A6A3A", "#C4C2BD", "#8C6D1F", "#6E6D6E", "#EBD3A0", "#3F3E3F"];
  const MODI = { user: "Anwendungen", system: "Kernel", iowait: "E/A-Wartezeit", steal: "Entzug durch Wirt", irq: "Interrupts", softirq: "Soft-Interrupts", nice: "Anwendungen (nice)", idle: "Leerlauf" };
  const GERAET = 'device=~"sd.*|nvme.*|vd.*|xvd.*|md.*|dm-.*"';
  const NETZ = 'device!~"veth.*|br-.*|docker0|lo"';
  const FS = 'fstype!~"tmpfs|overlay|squashfs|ramfs"';
  const KEINE_GRENZE = 1e18; // cAdvisor meldet ohne Speichergrenze einen Wert nahe 2^63

  const state = { bereich: "1h", auto: true, timer: null, charts: new Map(), kern: null, container: null, laden: false };

  // ---------- Formatierung ----------
  const de = (v, nk) => v.toLocaleString("de-DE", { maximumFractionDigits: nk });
  function fmtBytes(v, nach) {
    if (v == null || !isFinite(v)) return "k. A.";
    const e = ["B", "KiB", "MiB", "GiB", "TiB"];
    let x = Math.abs(v), i = 0;
    while (x >= 1024 && i < e.length - 1) { x /= 1024; i++; }
    return (v < 0 ? "-" : "") + de(x, x < 10 ? 2 : x < 100 ? 1 : 0) + " " + e[i] + (nach || "");
  }
  const FMT = {
    pct: v => (v == null || !isFinite(v)) ? "k. A." : de(v, v < 10 ? 1 : 0) + " %",
    bytes: v => fmtBytes(v),
    rate: v => fmtBytes(v, "/s"),
    num: v => (v == null || !isFinite(v)) ? "k. A." : de(v, Math.abs(v) >= 100 ? 0 : 2),
    secs: v => (v == null || !isFinite(v)) ? "k. A." : (Math.abs(v) < 1 ? de(v * 1000, 0) + " ms" : de(v, 2) + " s"),
    dauer: v => fmtDauer(v),
  };
  function fmtDauer(s) {
    if (s == null || !isFinite(s)) return "k. A.";
    const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
    if (d > 0) return `${d} Tage ${h} Std`;
    if (h > 0) return `${h} Std ${m} Min`;
    return `${m} Min`;
  }
  const bereichSek = () => BEREICHE.find(b => b[0] === state.bereich)[1];
  function fmtZeit(t) {
    const d = new Date(t * 1000), sek = bereichSek();
    const uhr = d.toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });
    if (sek <= 86400) return uhr;
    if (sek <= 30 * 86400) return d.toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit" }) + " " + uhr;
    return d.toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit", year: "2-digit" });
  }
  const fmtDatum = t => new Date(t * 1000).toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit", year: "numeric" });

  // ---------- Abfragen ----------
  async function prom(pfad, params) {
    const r = await fetch("/api/v1/" + pfad + "?" + new URLSearchParams(params), { cache: "no-store" });
    if (!r.ok) throw new Error("Messdatenbank antwortet mit HTTP " + r.status);
    const j = await r.json();
    if (j.status !== "success") throw new Error(j.error || "Abfrage fehlgeschlagen");
    return j.data.result;
  }
  function fenster() {
    const sek = bereichSek(), end = Math.floor(Date.now() / 1000);
    const step = Math.max(15, Math.ceil(sek / 360 / 15) * 15);
    return { start: end - sek, end, step, w: Math.max(120, 2 * step) + "s" };
  }
  const mitFenster = (expr, f) => expr.replaceAll("[W]", "[" + f.w + "]");
  const sofort = expr => prom("query", { query: mitFenster(expr, { w: "120s" }) });
  async function verlauf(expr) {
    const f = fenster();
    const res = await prom("query_range", { query: mitFenster(expr, f), start: f.start, end: f.end, step: f.step });
    return { res, f };
  }
  function alsDaten(res, f, label) {
    const n = Math.floor((f.end - f.start) / f.step) + 1;
    const xs = Array.from({ length: n }, (_, i) => f.start + i * f.step);
    const reihen = res.map(s => {
      const a = new Array(n).fill(null);
      for (const [t, v] of s.values) {
        const i = Math.round((t - f.start) / f.step);
        if (i >= 0 && i < n) { const x = parseFloat(v); a[i] = isFinite(x) ? x : null; }
      }
      return a;
    });
    return { data: [xs, ...reihen], labels: res.map(s => label(s.metric)) };
  }
  const wert = (res, i = 0) => (res && res[i] ? parseFloat(res[i].value[1]) : null);
  const nachLabel = (res, l) => Object.fromEntries(res.map(s => [s.metric[l], parseFloat(s.value[1])]));

  // ---------- Verlaufsdiagramme ----------
  function chart(el, unit, labels, data, p) {
    const zeit = { label: "Zeit", value: (u, v) => v == null ? "" : new Date(v * 1000).toLocaleString("de-DE") };
    const series = [zeit].concat(labels.map((l, i) => ({
      label: l, stroke: FARBEN[i % FARBEN.length], width: 1.5,
      fill: labels.length === 1 ? "rgba(227,172,72,0.14)" : undefined,
      value: (u, v) => v == null ? "" : FMT[unit](v),
    })));
    const achse = { stroke: "#9F9F9F", grid: { stroke: "#EEECE7", width: 1 }, ticks: { stroke: "#DDDBD6", width: 1 } };
    const o = {
      width: Math.max(280, el.clientWidth), height: 230, series,
      axes: [
        Object.assign({ values: (u, sp) => sp.map(fmtZeit), space: () => (bereichSek() <= 86400 ? 70 : 118) }, achse),
        Object.assign({ size: 72, values: (u, sp) => sp.map(v => FMT[unit](v)) }, achse),
      ],
      scales: { x: { time: true }, y: { range: (u, min, max) => {
        if (p.bis100) return [0, 100];
        const lo = Math.min(0, min), hi = max <= lo ? lo + 1 : max;
        return [lo, hi + (hi - lo) * 0.06];
      } } },
      legend: { show: true, live: true },
      cursor: { drag: { x: true, y: false }, focus: { prox: 30 } },
    };
    return new uPlot(o, data, el);
  }
  function zerstoere(id) { const c = state.charts.get(id); if (c) { c.u.destroy(); state.charts.delete(id); } }
  function panelEl(raster, id, titel) {
    let el = document.getElementById("p-" + id);
    if (!el) {
      el = document.createElement("div"); el.className = "panel"; el.id = "p-" + id;
      el.innerHTML = '<div class="titel"><span class="name"></span><span class="jetzt"></span></div><div class="flaeche"></div>';
      raster.appendChild(el);
    }
    el.querySelector(".name").textContent = titel;
    return el;
  }
  async function zeichne(raster, p) {
    const el = panelEl(raster, p.id, p.titel), flaeche = el.querySelector(".flaeche"), jetzt = el.querySelector(".jetzt");
    try {
      const { res, f } = await verlauf(p.expr);
      if (!res.length) { zerstoere(p.id); flaeche.innerHTML = '<div class="leer">keine Messwerte im Zeitbereich</div>'; jetzt.textContent = ""; return; }
      const { data, labels } = alsDaten(res, f, p.label || (() => p.titel));
      const alt = state.charts.get(p.id);
      if (alt && alt.labels.join("\u0001") === labels.join("\u0001")) alt.u.setData(data);
      else { zerstoere(p.id); flaeche.innerHTML = ""; state.charts.set(p.id, { u: chart(flaeche, p.unit, labels, data, p), labels, el: flaeche }); }
      const letzte = data[1].slice().reverse().find(v => v != null);
      jetzt.textContent = labels.length === 1 ? "aktuell " + FMT[p.unit](letzte) : labels.length + " Reihen";
    } catch (e) { zerstoere(p.id); flaeche.innerHTML = '<div class="leer"></div>'; flaeche.firstChild.textContent = e.message; jetzt.textContent = ""; }
  }
  function leereRaster(raster, behalte) {
    for (const el of Array.from(raster.querySelectorAll(".panel"))) {
      if (!behalte.has(el.id.slice(2))) { zerstoere(el.id.slice(2)); el.remove(); }
    }
  }

  // ---------- Panel-Definitionen ----------
  const lr = (expr, name) => `label_replace(${expr}, "w", "${name}", "", "")`;
  const w = m => m.w;
  const PANELS = {
    cpu: [
      { id: "cpu_gesamt", titel: "CPU gesamt", unit: "pct", bis100: true, expr: '100 * (1 - avg(rate(node_cpu_seconds_total{mode="idle"}[W])))', label: () => "Auslastung" },
      { id: "cpu_modi", titel: "CPU nach Betriebsart", unit: "pct", expr: '100 * sum by (mode) (rate(node_cpu_seconds_total{mode!="idle"}[W])) / scalar(count(node_cpu_seconds_total{mode="idle"}))', label: m => MODI[m.mode] || m.mode },
      { id: "last", titel: "Systemlast (Load)", unit: "num", expr: `${lr("node_load1", "1 Minute")} or ${lr("node_load5", "5 Minuten")} or ${lr("node_load15", "15 Minuten")}`, label: w },
      { id: "psi_cpu", titel: "CPU-Druck (PSI, Zeitanteil wartender Prozesse)", unit: "pct", expr: '100 * rate(node_pressure_cpu_waiting_seconds_total[W])', label: () => "wartend" },
    ],
    mem: [
      { id: "mem", titel: "Arbeitsspeicher", unit: "bytes", expr: `${lr("node_memory_MemTotal_bytes - node_memory_MemAvailable_bytes", "Belegt")} or ${lr("node_memory_Cached_bytes + node_memory_Buffers_bytes", "Cache und Puffer")} or ${lr("node_memory_MemFree_bytes", "Frei")}`, label: w },
      { id: "swap", titel: "Swap belegt", unit: "bytes", expr: 'node_memory_SwapTotal_bytes - node_memory_SwapFree_bytes', label: () => "Swap" },
      { id: "psi_mem", titel: "Speicherdruck (PSI)", unit: "pct", expr: '100 * rate(node_pressure_memory_waiting_seconds_total[W])', label: () => "wartend" },
      { id: "oom", titel: "Speichernot (OOM-Kills je Stunde)", unit: "num", expr: 'increase(node_vmstat_oom_kill[1h])', label: () => "OOM-Kills" },
    ],
    net: [
      { id: "net", titel: "Netzwerk Durchsatz", unit: "rate", expr: `label_replace(rate(node_network_receive_bytes_total{${NETZ}}[W]), "w", "$1 empfangen", "device", "(.*)") or label_replace(rate(node_network_transmit_bytes_total{${NETZ}}[W]), "w", "$1 gesendet", "device", "(.*)")`, label: w },
      { id: "net_pak", titel: "Netzwerk Pakete je Sekunde", unit: "num", expr: `label_replace(rate(node_network_receive_packets_total{${NETZ}}[W]), "w", "$1 empfangen", "device", "(.*)") or label_replace(rate(node_network_transmit_packets_total{${NETZ}}[W]), "w", "$1 gesendet", "device", "(.*)")`, label: w },
      { id: "net_fehler", titel: "Netzwerk Fehler und Verwürfe je Sekunde", unit: "num", expr: `sum by (device) (rate(node_network_receive_errs_total{${NETZ}}[W]) + rate(node_network_transmit_errs_total{${NETZ}}[W]) + rate(node_network_receive_drop_total{${NETZ}}[W]) + rate(node_network_transmit_drop_total{${NETZ}}[W]))`, label: m => m.device },
      { id: "tcp", titel: "TCP-Verbindungen", unit: "num", expr: `${lr("node_netstat_Tcp_CurrEstab", "verbunden")} or ${lr("node_sockstat_TCP_tw", "time_wait")} or ${lr("node_nf_conntrack_entries", "Conntrack-Einträge")}`, label: w },
    ],
    disk: [
      { id: "disk_io", titel: "Datenträger Durchsatz", unit: "rate", expr: `label_replace(rate(node_disk_read_bytes_total{${GERAET}}[W]), "w", "$1 lesen", "device", "(.*)") or label_replace(rate(node_disk_written_bytes_total{${GERAET}}[W]), "w", "$1 schreiben", "device", "(.*)")`, label: w },
      { id: "disk_iops", titel: "Datenträger Vorgänge je Sekunde", unit: "num", expr: `label_replace(rate(node_disk_reads_completed_total{${GERAET}}[W]), "w", "$1 lesen", "device", "(.*)") or label_replace(rate(node_disk_writes_completed_total{${GERAET}}[W]), "w", "$1 schreiben", "device", "(.*)")`, label: w },
      { id: "disk_util", titel: "Datenträger Auslastung (Zeitanteil beschäftigt)", unit: "pct", bis100: true, expr: `100 * rate(node_disk_io_time_seconds_total{${GERAET}}[W])`, label: m => m.device },
      { id: "disk_lat", titel: "Datenträger Antwortzeit je Vorgang", unit: "secs", expr: `label_replace(rate(node_disk_read_time_seconds_total{${GERAET}}[W]) / rate(node_disk_reads_completed_total{${GERAET}}[W]), "w", "$1 lesen", "device", "(.*)") or label_replace(rate(node_disk_write_time_seconds_total{${GERAET}}[W]) / rate(node_disk_writes_completed_total{${GERAET}}[W]), "w", "$1 schreiben", "device", "(.*)")`, label: w },
      { id: "psi_io", titel: "E/A-Druck (PSI)", unit: "pct", expr: '100 * rate(node_pressure_io_waiting_seconds_total[W])', label: () => "wartend" },
      { id: "fs_verlauf", titel: "Belegung der Dateisysteme", unit: "pct", bis100: true, expr: `100 * (1 - node_filesystem_avail_bytes{${FS}} / node_filesystem_size_bytes{${FS}})`, label: m => m.mountpoint },
    ],
    ctr: [
      { id: "ctr_cpu", titel: "Container CPU (Kerne), die zwölf größten", unit: "num", expr: 'topk(12, sum by (name) (rate(container_cpu_usage_seconds_total{name!=""}[W])))', label: m => m.name },
      { id: "ctr_mem", titel: "Container Arbeitsspeicher, die zwölf größten", unit: "bytes", expr: 'topk(12, max by (name) (container_memory_working_set_bytes{name!=""}))', label: m => m.name },
      { id: "ctr_net", titel: "Container Netz (gesendet und empfangen), die zwölf größten", unit: "rate", expr: 'topk(12, sum by (name) (rate(container_network_receive_bytes_total{name!=""}[W]) + rate(container_network_transmit_bytes_total{name!=""}[W])))', label: m => m.name },
      { id: "ctr_io", titel: "Container Datenträger (lesen und schreiben), die zwölf größten", unit: "rate", expr: 'topk(12, sum by (name) (rate(container_fs_reads_bytes_total{name!=""}[W]) + rate(container_fs_writes_bytes_total{name!=""}[W])))', label: m => m.name },
    ],
    avail: [
      { id: "probe_dauer", titel: "Antwortzeit der Prüfungen", unit: "secs", expr: 'probe_duration_seconds', label: m => m.instance },
      { id: "probe_ok", titel: "Erreichbarkeit (100 = erreichbar)", unit: "pct", bis100: true, expr: '100 * probe_success', label: m => m.instance },
      { id: "quellen", titel: "Messquellen (1 = liefert Werte)", unit: "num", expr: 'up', label: m => m.job },
    ],
    sys: [
      { id: "procs", titel: "Prozesse", unit: "num", expr: `${lr("node_procs_running", "laufend")} or ${lr("node_procs_blocked", "blockiert (E/A)")} or ${lr('node_processes_state{state="Z"}', "Zombies")}`, label: w },
      { id: "ctx", titel: "Kontextwechsel und Interrupts je Sekunde", unit: "num", expr: `${lr("rate(node_context_switches_total[W])", "Kontextwechsel")} or ${lr("rate(node_intr_total[W])", "Interrupts")}`, label: w },
      { id: "fds", titel: "Offene Dateideskriptoren", unit: "num", expr: 'node_filefd_allocated', label: () => "belegt" },
      { id: "ntp", titel: "Zeitabweichung der Systemuhr", unit: "secs", expr: 'node_timex_offset_seconds', label: () => "Abweichung" },
      { id: "temp", titel: "Temperaturen (falls der Wirt sie meldet)", unit: "num", expr: 'node_hwmon_temp_celsius', label: m => [m.chip, m.sensor].filter(Boolean).join(" ") },
      { id: "prom_speicher", titel: "Messdatenbank: Speicher auf der Platte", unit: "bytes", expr: 'prometheus_tsdb_storage_blocks_bytes', label: () => "Messdaten" },
      { id: "prom_reihen", titel: "Messdatenbank: aktive Messreihen", unit: "num", expr: 'prometheus_tsdb_head_series', label: () => "Reihen" },
    ],
  };
  const kernPanels = k => [{ id: "kern_" + k, titel: "Kern " + k + " nach Betriebsart", unit: "pct", bis100: true, expr: `100 * sum by (mode) (rate(node_cpu_seconds_total{cpu="${k}",mode!="idle"}[W]))`, label: m => MODI[m.mode] || m.mode }];
  const containerPanels = n => {
    const q = n.replace(/"/g, '\\"');
    return [
      { id: "ctrd_cpu", titel: n + ": CPU (Kerne)", unit: "num", expr: `sum(rate(container_cpu_usage_seconds_total{name="${q}"}[W]))`, label: () => "Kerne" },
      { id: "ctrd_mem", titel: n + ": Arbeitsspeicher", unit: "bytes", expr: `${lr(`max(container_memory_working_set_bytes{name="${q}"})`, "Arbeitsmenge")} or ${lr(`max(container_memory_usage_bytes{name="${q}"})`, "inkl. Cache")}`, label: w },
      { id: "ctrd_net", titel: n + ": Netz", unit: "rate", expr: `${lr(`sum(rate(container_network_receive_bytes_total{name="${q}"}[W]))`, "empfangen")} or ${lr(`sum(rate(container_network_transmit_bytes_total{name="${q}"}[W]))`, "gesendet")}`, label: w },
      { id: "ctrd_drossel", titel: n + ": CPU-Drosselung durch Grenze (Sekunden je Sekunde)", unit: "num", expr: `sum(rate(container_cpu_cfs_throttled_seconds_total{name="${q}"}[W]))`, label: () => "gedrosselt" },
    ];
  };

  // ---------- Kennzahlen ----------
  const WERTE = {
    cpu: '100 * (1 - avg(rate(node_cpu_seconds_total{mode="idle"}[W])))',
    kerne: 'count(node_cpu_seconds_total{mode="idle"})',
    steal: '100 * avg(rate(node_cpu_seconds_total{mode="steal"}[W]))',
    last1: "node_load1", last5: "node_load5", last15: "node_load15",
    memPct: '100 * (1 - node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)',
    memBelegt: 'node_memory_MemTotal_bytes - node_memory_MemAvailable_bytes',
    memGesamt: 'node_memory_MemTotal_bytes',
    swap: 'node_memory_SwapTotal_bytes - node_memory_SwapFree_bytes',
    diskPct: '100 * (1 - node_filesystem_avail_bytes{mountpoint="/"} / node_filesystem_size_bytes{mountpoint="/"})',
    diskFrei: 'node_filesystem_avail_bytes{mountpoint="/"}',
    netz: `sum(rate(node_network_receive_bytes_total{${NETZ}}[W]) + rate(node_network_transmit_bytes_total{${NETZ}}[W]))`,
    container: 'count(container_last_seen{name!=""} > (time() - 120))',
    probeOk: 'sum(probe_success)', probeAlle: 'count(probe_success)',
    betrieb: 'time() - node_boot_time_seconds',
    zombies: 'sum(node_processes_state{state="Z"})',
    psi: '100 * rate(node_pressure_cpu_waiting_seconds_total[W])',
  };
  function kachel(id, titel, text, neben, stufe) {
    return `<div class="kachel ${stufe || ""}" id="k-${id}"><div class="versal">${titel}</div><div class="wert">${text}</div><div class="neben">${neben || ""}</div></div>`;
  }
  const stufe = (v, warn, schlecht) => v == null ? "" : v >= schlecht ? "schlecht" : v >= warn ? "warn" : "";
  async function kacheln() {
    const keys = Object.keys(WERTE);
    const res = await Promise.all(keys.map(k => sofort(WERTE[k])));
    const v = Object.fromEntries(keys.map((k, i) => [k, wert(res[i])]));
    const html = [
      kachel("cpu", "CPU gesamt", FMT.pct(v.cpu), `${FMT.num(v.kerne)} Kerne, Entzug ${FMT.pct(v.steal)}`, stufe(v.cpu, 70, 90)),
      kachel("last", "Systemlast 1 Min", FMT.num(v.last1), `5 Min ${FMT.num(v.last5)}, 15 Min ${FMT.num(v.last15)}`, stufe(v.kerne ? v.last1 / v.kerne * 100 : null, 70, 100)),
      kachel("psi", "CPU-Druck", FMT.pct(v.psi), "Zeitanteil wartender Prozesse", stufe(v.psi, 20, 50)),
      kachel("mem", "Arbeitsspeicher", FMT.pct(v.memPct), `${FMT.bytes(v.memBelegt)} von ${FMT.bytes(v.memGesamt)}`, stufe(v.memPct, 80, 92)),
      kachel("swap", "Swap belegt", FMT.bytes(v.swap), "", stufe(v.swap, 512 * 1024 * 1024, 2 * 1024 ** 3)),
      kachel("disk", "Platte /", FMT.pct(v.diskPct), `${FMT.bytes(v.diskFrei)} frei`, stufe(v.diskPct, 80, 90)),
      kachel("net", "Netz gesamt", FMT.rate(v.netz), "gesendet und empfangen", ""),
      kachel("ctr", "Container aktiv", FMT.num(v.container), "in den letzten zwei Minuten gesehen", ""),
      kachel("probe", "Dienste erreichbar", `${FMT.num(v.probeOk)} von ${FMT.num(v.probeAlle)}`, "HTTP-Prüfungen", v.probeAlle && v.probeOk < v.probeAlle ? "schlecht" : ""),
      kachel("zombies", "Zombie-Prozesse", FMT.num(v.zombies), "", stufe(v.zombies, 50, 200)),
      kachel("betrieb", "Betriebszeit", fmtDauer(v.betrieb), "seit dem letzten Neustart", ""),
    ].join("");
    document.getElementById("kacheln").innerHTML = html;
    setText("summe-cpu", `${FMT.pct(v.cpu)}, Last ${FMT.num(v.last1)}`);
    setText("summe-mem", `${FMT.pct(v.memPct)} belegt`);
    setText("summe-net", FMT.rate(v.netz));
    setText("summe-disk", `/ zu ${FMT.pct(v.diskPct)} belegt`);
    setText("summe-ctr", `${FMT.num(v.container)} aktiv`);
    setText("summe-avail", `${FMT.num(v.probeOk)} von ${FMT.num(v.probeAlle)} erreichbar`);
    setText("summe-sys", `Betriebszeit ${fmtDauer(v.betrieb)}`);
  }
  const setText = (id, t) => { const el = document.getElementById(id); if (el) el.textContent = t; };

  // ---------- CPU je Kern ----------
  async function kerne() {
    const res = await sofort('100 * (1 - rate(node_cpu_seconds_total{mode="idle"}[W]))');
    const werte = nachLabel(res, "cpu");
    const nrs = Object.keys(werte).sort((a, b) => Number(a) - Number(b));
    const box = document.getElementById("kerne");
    box.innerHTML = nrs.map(k => {
      const v = werte[k], pz = Math.max(0, Math.min(100, v));
      return `<div class="kern ${v >= 90 ? "hoch" : ""} ${state.kern === k ? "aktiv" : ""}" data-kern="${k}"><div class="nr">Kern ${k}</div><div class="pz">${FMT.pct(v)}</div><div class="balken"><i style="width:${pz}%"></i></div></div>`;
    }).join("");
    const raster = document.getElementById("raster-kern");
    if (state.kern != null) {
      const ps = kernPanels(state.kern);
      leereRaster(raster, new Set(ps.map(p => p.id)));
      await Promise.all(ps.map(p => zeichne(raster, p)));
    } else leereRaster(raster, new Set());
  }
  document.getElementById("kerne").addEventListener("click", ev => {
    const el = ev.target.closest(".kern"); if (!el) return;
    state.kern = state.kern === el.dataset.kern ? null : el.dataset.kern;
    kerne().catch(zeigeFehler);
  });

  // ---------- Tabellen ----------
  function zeile(zellen, attrs) { return `<tr ${attrs || ""}>${zellen.map(z => `<td class="${z.zahl ? "zahl" : ""}">${z.html != null ? z.html : esc(z.text)}</td>`).join("")}</tr>`; }
  const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  async function tabelleFs() {
    const [size, avail] = await Promise.all([sofort(`node_filesystem_size_bytes{${FS}}`), sofort(`node_filesystem_avail_bytes{${FS}}`)]);
    const frei = Object.fromEntries(avail.map(s => [s.metric.mountpoint, parseFloat(s.value[1])]));
    const rows = size.map(s => ({ mp: s.metric.mountpoint, dev: s.metric.device, size: parseFloat(s.value[1]), avail: frei[s.metric.mountpoint] }))
      .sort((a, b) => a.mp.localeCompare(b.mp));
    document.querySelector("#tab-fs tbody").innerHTML = rows.map(r => {
      const pct = r.size ? 100 * (1 - r.avail / r.size) : null;
      return zeile([{ text: r.mp }, { text: r.dev }, { text: FMT.bytes(r.size), zahl: true }, { text: FMT.bytes(r.size - r.avail), zahl: true }, { text: FMT.bytes(r.avail), zahl: true }, { text: FMT.pct(pct), zahl: true }]);
    }).join("") || zeile([{ text: "keine Dateisysteme gemeldet" }]);
  }

  async function tabelleContainer() {
    const [cpu, mem, grenze, netz, start] = await Promise.all([
      sofort('sum by (name, container_label_com_docker_compose_project) (rate(container_cpu_usage_seconds_total{name!=""}[W]))'),
      sofort('max by (name) (container_memory_working_set_bytes{name!=""})'),
      sofort('max by (name) (container_spec_memory_limit_bytes{name!=""})'),
      sofort('sum by (name) (rate(container_network_receive_bytes_total{name!=""}[W]) + rate(container_network_transmit_bytes_total{name!=""}[W]))'),
      sofort('max by (name) (container_start_time_seconds{name!=""})'),
    ]);
    const m = nachLabel(mem, "name"), g = nachLabel(grenze, "name"), n = nachLabel(netz, "name"), st = nachLabel(start, "name");
    const jetzt = Date.now() / 1000;
    const rows = cpu.map(s => ({ name: s.metric.name, projekt: s.metric.container_label_com_docker_compose_project || "", cpu: parseFloat(s.value[1]) }))
      .sort((a, b) => b.cpu - a.cpu);
    document.querySelector("#tab-ctr tbody").innerHTML = rows.map(r => zeile([
      { text: r.name }, { text: r.projekt }, { text: FMT.num(r.cpu), zahl: true }, { text: FMT.bytes(m[r.name]), zahl: true },
      { text: g[r.name] == null || g[r.name] > KEINE_GRENZE ? "keine" : FMT.bytes(g[r.name]), zahl: true },
      { text: FMT.rate(n[r.name]), zahl: true }, { text: st[r.name] ? fmtDauer(jetzt - st[r.name]) : "k. A." },
    ], `class="klick ${state.container === r.name ? "aktiv" : ""}" data-name="${esc(r.name)}"`)).join("") || zeile([{ text: "keine Container gemeldet" }]);
    const raster = document.getElementById("raster-ctr-detail");
    if (state.container) {
      const ps = containerPanels(state.container);
      leereRaster(raster, new Set(ps.map(p => p.id)));
      await Promise.all(ps.map(p => zeichne(raster, p)));
    } else leereRaster(raster, new Set());
  }
  document.querySelector("#tab-ctr tbody").addEventListener("click", ev => {
    const tr = ev.target.closest("tr.klick"); if (!tr) return;
    state.container = state.container === tr.dataset.name ? null : tr.dataset.name;
    tabelleContainer().catch(zeigeFehler);
  });

  async function tabelleProbe() {
    const [ok, dauer, code, cert] = await Promise.all([sofort("probe_success"), sofort("probe_duration_seconds"), sofort("probe_http_status_code"), sofort("probe_ssl_earliest_cert_expiry")]);
    const d = nachLabel(dauer, "instance"), c = nachLabel(code, "instance"), z = nachLabel(cert, "instance");
    const rows = ok.map(s => ({ inst: s.metric.instance, ok: parseFloat(s.value[1]) })).sort((a, b) => a.inst.localeCompare(b.inst));
    document.querySelector("#tab-probe tbody").innerHTML = rows.map(r => zeile([
      { text: r.inst },
      { html: `<span class="status ${r.ok >= 1 ? "ok" : "fehl"}"></span>${r.ok >= 1 ? "erreichbar" : "gestört"}` },
      { text: FMT.secs(d[r.inst]), zahl: true }, { text: c[r.inst] ? String(c[r.inst]) : "k. A.", zahl: true },
      { text: z[r.inst] ? fmtDatum(z[r.inst]) + " (" + fmtDauer(z[r.inst] - Date.now() / 1000) + ")" : "k. A." },
    ])).join("") || zeile([{ text: "keine Prüfungen konfiguriert" }]);
  }

  // ---------- Ablauf ----------
  function zeigeFehler(e) { const h = document.getElementById("hinweis"); h.hidden = !e; h.textContent = e ? "Anzeige unvollständig: " + (e.message || e) : ""; }
  async function aktualisieren() {
    if (state.laden) return;
    state.laden = true;
    const t0 = Date.now();
    try {
      const jobs = [kacheln(), kerne(), tabelleFs(), tabelleContainer(), tabelleProbe()];
      for (const [sek, ps] of Object.entries(PANELS)) { const raster = document.getElementById("raster-" + sek); jobs.push(...ps.map(p => zeichne(raster, p))); }
      const erg = await Promise.allSettled(jobs);
      const fehler = erg.find(r => r.status === "rejected");
      zeigeFehler(fehler ? fehler.reason : null);
      setText("stand", `Stand: ${new Date().toLocaleTimeString("de-DE")} (${Math.round((Date.now() - t0) / 100) / 10} s)`);
    } finally { state.laden = false; }
  }
  function planen() {
    clearInterval(state.timer);
    if (state.auto) state.timer = setInterval(aktualisieren, bereichSek() <= 86400 ? 30000 : 300000);
  }
  function bereichWaehlen(b) {
    state.bereich = b;
    try { localStorage.setItem("status.bereich", b); } catch (e) { /* ohne Speicher */ }
    for (const el of document.querySelectorAll(".bereich")) el.classList.toggle("aktiv", el.dataset.b === b);
    planen();
    aktualisieren();
  }
  const leiste = document.getElementById("bereiche");
  leiste.innerHTML = BEREICHE.map(b => `<button type="button" class="bereich" data-b="${b[0]}">${b[2]}</button>`).join("");
  leiste.addEventListener("click", ev => { const el = ev.target.closest(".bereich"); if (el) bereichWaehlen(el.dataset.b); });
  document.getElementById("jetzt").addEventListener("click", () => aktualisieren());
  document.getElementById("auto").addEventListener("change", ev => { state.auto = ev.target.checked; planen(); });
  let resizeTimer = null;
  window.addEventListener("resize", () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(() => state.charts.forEach(c => c.u.setSize({ width: Math.max(280, c.el.clientWidth), height: 230 })), 150); });
  document.addEventListener("visibilitychange", () => { if (!document.hidden && state.auto) aktualisieren(); });
  for (const d of document.querySelectorAll("details")) d.addEventListener("toggle", () => { if (d.open) state.charts.forEach(c => { if (d.contains(c.el)) c.u.setSize({ width: Math.max(280, c.el.clientWidth), height: 230 }); }); });

  let gespeichert = null;
  try { gespeichert = localStorage.getItem("status.bereich"); } catch (e) { /* ohne Speicher */ }
  bereichWaehlen(BEREICHE.some(b => b[0] === gespeichert) ? gespeichert : "1h");
})();
