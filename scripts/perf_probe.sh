#!/usr/bin/env bash
# OCR-Probelauf fuer Meilenstein M0 in einem temporaeren Container (Umsetzungsplan 2.1 Schritte 6 bis 8).
#
# Baut das Mess-Image aus docker/probe.Dockerfile, erzeugt einen synthetischen Korpus, misst
# t_ocr, e, m_ocr, t_txt, Zeichenfehlerrate und Plattenfaktor und schreibt
# performance-entscheidung.md sowie results.json in das Ausgabeverzeichnis.
#
# Der Container hat keine Host-Ports, kein Traefik-Netz und kein Netzwerk. Er wird mit --rm
# gestartet und nach dem Lauf entfernt; das Image wird ohne --keep-image ebenfalls geloescht.
# Voraussetzung: Freigabe des Auftraggebers fuer den Messcontainer (Umsetzungsplan V-03).
#
# Aufruf auf dem VPS im Repository-Verzeichnis:
#   bash scripts/perf_probe.sh [--pages 100] [--scan-share 0.5] [--procs 1,2,auto] \
#                              [--out ./probe-out] [--keep-image] [--not-target-host]
set -euo pipefail

PAGES=100
SCAN_SHARE=0.5
PROCS="1,2,auto"
OUT="./probe-out/$(date +%Y%m%d-%H%M%S)"
KEEP_IMAGE=0
TARGET_FLAG="--on-target-host"
IMAGE="objektakte-probe:local"
NAME="objektakte-probe"

while [ $# -gt 0 ]; do
  case "$1" in
    --pages) PAGES="$2"; shift 2;;
    --scan-share) SCAN_SHARE="$2"; shift 2;;
    --procs) PROCS="$2"; shift 2;;
    --out) OUT="$2"; shift 2;;
    --keep-image) KEEP_IMAGE=1; shift;;
    --not-target-host) TARGET_FLAG=""; shift;;
    -h|--help) sed -n '2,16p' "$0"; exit 0;;
    *) echo "Unbekannter Parameter: $1" >&2; exit 2;;
  esac
done

REPO="$(cd "$(dirname "$0")/.." && pwd)"
command -v docker >/dev/null || { echo "docker nicht gefunden" >&2; exit 1; }
docker info >/dev/null 2>&1 || { echo "Docker-Daemon nicht erreichbar (Gruppe docker?)" >&2; exit 1; }

mkdir -p "$OUT"
OUT="$(cd "$OUT" && pwd)"
chmod 777 "$OUT"   # der Container schreibt als UID 10001

echo "[perf_probe] Baue Mess-Image $IMAGE ..."
docker build -q -f "$REPO/docker/probe.Dockerfile" -t "$IMAGE" "$REPO" >/dev/null

echo "[perf_probe] Starte Messcontainer (kein Netzwerk, keine Ports) ..."
STATS_FILE="$OUT/docker-stats.log"
: > "$STATS_FILE"
docker run --rm --name "$NAME" --network none \
  --security-opt no-new-privileges:true \
  -e OMP_THREAD_LIMIT=1 \
  -v "$REPO/tests/performance:/probe:ro" \
  -v "$OUT:/out" \
  "$IMAGE" bash -c "
    set -e
    python3 /probe/corpus_generator.py --out /out/corpus --pages $PAGES --scan-share $SCAN_SHARE --dpi 300 &&
    python3 /probe/perf_probe.py --corpus /out/corpus --out /out --procs '$PROCS' --host-label '$(hostname)' $TARGET_FLAG
  " &
RUN_PID=$!

# Container-Speicher alle 2 s mitschreiben (Ergaenzung zu MaxRSS je Prozess)
( sleep 3; while kill -0 "$RUN_PID" 2>/dev/null; do
    docker stats --no-stream --format '{{.MemUsage}} {{.CPUPerc}}' "$NAME" 2>/dev/null >> "$STATS_FILE" || true
    sleep 2
  done ) &
STATS_PID=$!

set +e
wait "$RUN_PID"; RC=$?
set -e
kill "$STATS_PID" 2>/dev/null || true

if docker ps -a --format '{{.Names}}' | grep -qx "$NAME"; then
  echo "[perf_probe] Container noch vorhanden, wird entfernt."
  docker rm -f "$NAME" >/dev/null
fi
if [ "$KEEP_IMAGE" -eq 0 ]; then
  docker image rm "$IMAGE" >/dev/null 2>&1 || true
fi

PEAK="$(awk '{print $1}' "$STATS_FILE" | sed 's#/.*##' | sort -h | tail -1)"
echo "[perf_probe] Container-Speicherspitze laut docker stats: ${PEAK:-nicht erfasst}"
echo "[perf_probe] Ergebnis: $OUT/performance-entscheidung.md (Rohdaten results.json)"
echo "[perf_probe] Nach Pruefung nach docs/betrieb/performance-entscheidung.md uebernehmen."
echo "[perf_probe] Kontrolle: docker ps -a | grep $NAME  (muss leer sein)"
exit "$RC"
