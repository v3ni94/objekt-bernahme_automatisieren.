"""Syntaxpruefung der Betriebsskripte und der eingebetteten Python-Bloecke (26.09.2026, Paket Deployment-Tests).

Die Deploy-Skripte laufen nur auf dem Server; ein Tippfehler faellt sonst erst beim naechsten Fernaufruf auf.
Hier: bash -n bzw. sh -n je Skript nach Shebang, py_compile fuer jeden Heredoc-Block (<<'PY' ... PY) in
scripts/deploy_remote.sh, Vollstaendigkeit der Aktionsliste (Kopfkommentar, case-Zweig, Fehlerliste) und die
Schutzregeln der Scratch-Datenbanken (Test T6 und T8 duerfen die Produktionsdatenbank nie treffen).
"""

from __future__ import annotations

import py_compile
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SHELL_SKRIPTE = sorted(
    list((REPO / "scripts").glob("*.sh"))
    + list((REPO / "docker" / "backup").glob("*.sh"))
    + list((REPO / "docker" / "db" / "init").glob("*.sh"))
    + [REPO / "docker" / "entrypoint.sh"]
)
DEPLOY_REMOTE = REPO / "scripts" / "deploy_remote.sh"
DEPLOY_SH = REPO / "scripts" / "deploy.sh"
RESTORE_PROBE = REPO / "docker" / "backup" / "restore_probe.sh"
WORKFLOW = REPO / ".github" / "workflows" / "deploy.yml"
ACCOUNTS_INIT = REPO / "docker" / "db" / "init" / "01_accounts.sh"
NEUE_AKTIONEN = (
    "restart-probe",
    "restore-probe",
    "migrations-roundtrip",
    "oauth-proof",
    "perf-probe",
    # Sammelpaket 26.09.2026: Deploy-Aktionen der Pakete C, D, E und G
    "restformate-bereinigen",
    "zuordnung-stichprobe",
    "email-status",
    "faelle-sammelaktion",
)


def _shell_fuer(skript: Path) -> str:
    erste = skript.read_text(encoding="utf-8").splitlines()[0]
    return "bash" if "bash" in erste else "sh"


@pytest.mark.parametrize("skript", SHELL_SKRIPTE, ids=lambda p: str(p.relative_to(REPO)))
def test_shell_syntax(skript: Path):
    shell = _shell_fuer(skript)
    if shutil.which(shell) is None:
        pytest.skip(f"{shell} nicht installiert")
    ergebnis = subprocess.run([shell, "-n", str(skript)], capture_output=True, text=True)
    assert ergebnis.returncode == 0, ergebnis.stderr


def heredoc_bloecke(text: str) -> list[tuple[int, str]]:
    """Alle mit <<'PY' eingeleiteten Bloecke mit ihrer Startzeile; das Ende ist die Zeile, die nur PY enthaelt."""
    bloecke: list[tuple[int, str]] = []
    zeilen = text.splitlines()
    i = 0
    while i < len(zeilen):
        if "<<'PY'" in zeilen[i]:
            start = i + 1
            j = start
            while j < len(zeilen) and zeilen[j] != "PY":
                j += 1
            assert j < len(zeilen), f"Heredoc ab Zeile {start} ohne Abschluss PY"
            bloecke.append((start + 1, "\n".join(zeilen[start:j]) + "\n"))
            i = j
        i += 1
    return bloecke


def test_heredoc_bloecke_kompilieren(tmp_path: Path):
    bloecke = heredoc_bloecke(DEPLOY_REMOTE.read_text(encoding="utf-8"))
    assert len(bloecke) >= 5, "deploy_remote.sh enthaelt weniger Python-Bloecke als erwartet"
    for zeile, code in bloecke:
        quelle = tmp_path / f"block_{zeile}.py"
        quelle.write_text(code, encoding="utf-8")
        try:
            py_compile.compile(str(quelle), doraise=True)
        except py_compile.PyCompileError as exc:  # pragma: no cover - nur im Fehlerfall
            pytest.fail(f"Python-Block ab Zeile {zeile} in deploy_remote.sh: {exc.msg}")


def test_heredoc_bloecke_reichen_werte_nur_per_umgebung():
    """Konvention: Werte gehen mit -e VAR= an Python, nie per Shell-Interpolation in den Block (<<'PY' ist quotiert)."""
    text = DEPLOY_REMOTE.read_text(encoding="utf-8")
    assert "<<PY" not in text and '<<"PY"' not in text, "Heredoc ohne einfache Anfuehrungszeichen gefunden"


def _aktionen_im_kopf(text: str) -> set[str]:
    kopf = text.split("# Jede andere Eingabe wird abgewiesen.")[0]
    return set(re.findall(r"^#   ([a-z][a-z0-9-]*) ", kopf, flags=re.MULTILINE))


def _aktionen_im_case(text: str) -> set[str]:
    return set(re.findall(r"^  ([a-z][a-z0-9-]*)\)", text, flags=re.MULTILINE))


def _aktionen_in_fehlerliste(text: str) -> set[str]:
    zeile = next(z for z in text.splitlines() if z.startswith('  *) echo "Nur '))
    return set(re.findall(r"[a-z][a-z0-9-]+", zeile.split('"Nur ')[1].split(" erlaubt")[0]))


def test_neue_aktionen_vollstaendig_registriert():
    text = DEPLOY_REMOTE.read_text(encoding="utf-8")
    kopf, case, fehler = _aktionen_im_kopf(text), _aktionen_im_case(text), _aktionen_in_fehlerliste(text)
    for aktion in NEUE_AKTIONEN:
        assert aktion in kopf, f"{aktion} fehlt im Kopfkommentar"
        assert aktion in case, f"{aktion} fehlt als case-Zweig"
        assert aktion in fehler, f"{aktion} fehlt in der Liste erlaubter Aktionen"
    # Bestandsluecke: aeltere Zweige (lauf-monitor, worker-stop u. a.) fehlen im Kopf; nur neue Aktionen sind Pflicht
    assert set(NEUE_AKTIONEN) <= case & kopf & fehler


def test_deploy_tests_deckt_t4_t5_t12_t13_ab():
    text = DEPLOY_REMOTE.read_text(encoding="utf-8")
    block = text.split("  deploy-tests)")[1].split("\n  restart-probe)")[0]
    for marker in (
        "T4/T5",
        "T12",
        "T13",
        "PermitRootLogin",
        "PasswordAuthentication",
        "ufw",
        "unattended-upgrades",
    ):
        assert marker in block, f"{marker} fehlt im Block deploy-tests"
    assert "timedatectl" in block
    assert "mfa_required_roles" in block and "auth.denied" in block
    assert "docker compose restart" not in block, "deploy-tests muss lesend bleiben"


def test_restart_probe_startet_nur_worker_und_nur_mit_echt():
    text = DEPLOY_REMOTE.read_text(encoding="utf-8")
    block = text.split("  restart-probe)")[1].split("\n  restore-probe)")[0]
    neustarts = re.findall(r"docker compose restart (\S+)", block)
    assert neustarts == ["worker"], neustarts
    assert 'if [ "${ARG:-}" = "echt" ]' in block


def test_scratch_datenbanken_schuetzen_die_produktion():
    text = DEPLOY_REMOTE.read_text(encoding="utf-8")
    block = text.split("  migrations-roundtrip)")[1].split("\n  oauth-proof)")[0]
    assert 'PROBE="${PROD}_probe"' in block
    assert '[ "$PROBE" != "$PROD" ] ||' in block, "harter Abbruch bei Namensgleichheit fehlt"
    assert "DROP DATABASE IF EXISTS \\`$PROBE\\`" in block, "Scratch-Datenbank wird nicht geloescht"
    assert 'DB_NAME="$PROBE"' in block
    probe = (REPO / "docker" / "backup" / "restore_probe.sh").read_text(encoding="utf-8")
    assert 'TARGET="${DB_NAME}_restore_probe"' in probe
    assert '[ "$TARGET" = "$DB_NAME" ]' in probe, "harter Abbruch bei Namensgleichheit fehlt"
    assert "trap cleanup EXIT" in probe and "DROP DATABASE IF EXISTS" in probe
    assert "DB_ROOT_PASSWORD" not in probe, "die Probe laeuft als app_backup, nicht als root"
    assert "DEFINER=" in probe, "DEFINER-Klauseln muessen entfernt werden (sonst SUPER noetig)"


def test_backup_image_enthaelt_restore_probe():
    dockerfile = (REPO / "docker" / "backup" / "Dockerfile").read_text(encoding="utf-8")
    assert "docker/backup/restore_probe.sh" in dockerfile
    assert "chmod +x" in dockerfile and "/usr/local/bin/restore_probe.sh" in dockerfile


def test_roundtrip_skript_akzeptiert_repo_aus_umgebung():
    text = (REPO / "scripts" / "check_migrations_roundtrip.sh").read_text(encoding="utf-8")
    assert 'cd "${ROUNDTRIP_REPO:-$(dirname "$0")/..}"' in text


# Gegenpruefung 26.09.2026 (Korrektur F-deployment-tests)


def _argumentfilter_im_workflow() -> str:
    zeile = next(z for z in WORKFLOW.read_text(encoding="utf-8").splitlines() if 'case "$ARGUMENT" in' in z)
    return zeile.strip()


@pytest.mark.parametrize(
    ("argument", "erlaubt"),
    [
        ("pages=20+procs=1,2", True),
        ("procs=1,2,auto", True),
        ("SCHLUESSEL=wert+ZWEITER=x", True),
        ("admin@example.org", True),
        ("ergebnis=/data/exports/stichproben/2026-09-26_stichprobe.csv", True),
        ("gruppe=office+limit=2", True),
        ("echt", True),
        ("", True),
        ("a;rm", False),
        ("a b", False),
        ("$(id)", False),
    ],
)
def test_workflow_argumentfilter_laesst_komma_fuer_perf_probe_zu(argument: str, erlaubt: bool):
    """Der Filter in deploy.yml muss die dokumentierte Form pages=N+procs=1,2 durchlassen, sonst erreicht
    perf-probe den Server nie; alles ausserhalb des Zeichenvorrats bleibt abgewiesen."""
    if shutil.which("bash") is None:
        pytest.skip("bash nicht installiert")
    skript = f'ARGUMENT="$1"\n{_argumentfilter_im_workflow()}\necho durch'
    ergebnis = subprocess.run(["bash", "-c", skript, "x", argument], capture_output=True, text=True)
    assert (ergebnis.returncode == 0) is erlaubt, ergebnis.stdout + ergebnis.stderr


def test_workflow_und_deploy_remote_filtern_argumente_gleich():
    """Beide Filter nennen denselben Zeichenvorrat; sonst weist der Workflow ab, was der Server erlaubt."""
    werkstatt = re.search(r"\*\[!([^\]]+)\]\*", _argumentfilter_im_workflow())
    assert werkstatt is not None
    # allgemeiner Filter fuer alle Aktionen ausser config-set (eigener, weiterer Zeichenvorrat fuer JSON)
    server = re.search(
        r'case "\$\{ARG:-\}" in \*\[!([^\]]+)\]\*\) echo "Argument unzulaessig"',
        DEPLOY_REMOTE.read_text(encoding="utf-8"),
    )
    assert server is not None, "allgemeiner Argumentfilter in deploy_remote.sh nicht gefunden"
    assert set(werkstatt.group(1)) == set(server.group(1))


def test_restore_probe_raeumt_auch_nach_signal_auf(tmp_path: Path):
    """dash fuehrt den EXIT-Trap nach TERM/HUP/INT nicht aus; die Trap-Zeilen des Skripts muessen cleanup dennoch
    erreichen (Abbruch des Laufs, SSH-Abbruch, docker compose stop). Nachgestellt mit denselben trap-Zeilen."""
    if shutil.which("sh") is None:
        pytest.skip("sh nicht installiert")
    trap_zeilen = [z for z in RESTORE_PROBE.read_text(encoding="utf-8").splitlines() if z.startswith("trap ")]
    assert "trap cleanup EXIT" in trap_zeilen
    assert any(z.startswith("trap ") and "TERM" in z and "HUP" in z and "INT" in z for z in trap_zeilen)
    skript = tmp_path / "probe.sh"
    skript.write_text(
        # sleep im Hintergrund plus wait: das Signal trifft nur die Shell (wie TERM an den exec-Prozess), und
        # wait wird durch ein abgefangenes Signal beendet; ein Vordergrundkind wuerde die Behandlung nur aufschieben
        "cleanup() { echo CLEANUP; }\n"
        + "\n".join(trap_zeilen)
        + "\necho BEREIT\nsleep 20 >/dev/null 2>&1 &\nwait\n",
        encoding="utf-8",
    )
    prozess = subprocess.Popen(["sh", str(skript)], stdout=subprocess.PIPE, text=True)
    assert prozess.stdout is not None
    assert prozess.stdout.readline().strip() == "BEREIT"
    prozess.terminate()
    ausgabe, _ = prozess.communicate(timeout=10)
    assert "CLEANUP" in ausgabe, f"cleanup lief nach TERM nicht (rc {prozess.returncode})"
    assert prozess.returncode != 0


def test_migrations_roundtrip_loescht_ueber_exit_trap():
    text = DEPLOY_REMOTE.read_text(encoding="utf-8")
    block = text.split("  migrations-roundtrip)")[1].split("\n  oauth-proof)")[0]
    assert re.search(
        r"^\s*trap '.*DROP DATABASE IF EXISTS \\`\$PROBE\\`.*' EXIT$", block, flags=re.MULTILINE
    ), "DROP der Scratch-Datenbank muss im EXIT-Trap stehen (laeuft in bash auch nach Signalen)"
    assert 'PROBE_MASK="${PROBE//_/\\\\_}"' in block, "GRANT braucht den maskierten Namen (_ ist Platzhalter)"
    assert "GRANT ALL PRIVILEGES ON \\`$PROBE_MASK\\`.*" in block


def test_accounts_init_maskiert_unterstriche_im_grant():
    text = ACCOUNTS_INIT.read_text(encoding="utf-8")
    assert "GRANT ALL PRIVILEGES ON \\`${DB_MASK}\\_restore\\_probe\\`.* TO 'app_backup'@'%';" in text
    assert "GRANT ALL PRIVILEGES ON \\`${DB_MASK}\\_probe\\`.* TO 'app_migrate'@'%';" in text
    assert "_restore_probe\\`" not in text.replace("\\_restore\\_probe\\`", ""), "unmaskierter Name im GRANT"
    if shutil.which("sh") is None:
        pytest.skip("sh nicht installiert")
    zeile = next(z for z in text.splitlines() if z.startswith("DB_MASK="))
    ergebnis = subprocess.run(
        ["sh", "-c", zeile + '\nprintf "%s" "$DB_MASK"'],
        capture_output=True,
        text=True,
        env={"MARIADB_DATABASE": "objekt_akte", "PATH": "/usr/bin:/bin"},
    )
    assert ergebnis.stdout == "objekt\\_akte", ergebnis.stdout + ergebnis.stderr


def test_deploy_entfernt_reste_der_scratch_datenbanken_vor_den_rechten():
    text = DEPLOY_SH.read_text(encoding="utf-8")
    drop = text.index("DROP DATABASE IF EXISTS \\`${MARIADB_DATABASE}_restore_probe\\`")
    assert "DROP DATABASE IF EXISTS \\`${MARIADB_DATABASE}_probe\\`" in text
    assert drop < text.index("app-grants-sql"), "Reste muessen vor dem Nachziehen der Rechte entfernt werden"


def test_db_status_weist_scratch_datenbanken_aus():
    text = DEPLOY_REMOTE.read_text(encoding="utf-8")
    block = text.split("  db-status)")[1].split("\n  db-reset)")[0]
    assert "_restore_probe" in block and "_probe" in block
    assert "information_schema.schemata" in block
    assert "DROP" not in block, "db-status bleibt lesend"


def test_leere_treffer_erzeugen_hinweis_statt_stiller_ausgabe():
    """Ein || am Ende einer Pipeline haengt an sed (immer 0); der Hinweis muss an der Leere der Ausgabe haengen."""
    text = DEPLOY_REMOTE.read_text(encoding="utf-8")
    assert not re.search(r"\| sed 's/\^/  /' \\\n\s*\|\| echo", text), "Fallback am Pipeline-Ende gefunden"
    tests_block = text.split("  deploy-tests)")[1].split("\n  restart-probe)")[0]
    assert 'out="$(grep -HEi' in tests_block
    assert (
        "keine explizite Einstellung gefunden" in tests_block and "PasswordAuthentication yes" in tests_block
    )
    restart_block = text.split("  restart-probe)")[1].split("\n  restore-probe)")[0]
    assert 'out="$(docker compose logs' in restart_block
    assert 'if [ -n "$out" ]' in restart_block and "noch keine (Beat holt" in restart_block


def test_perf_probe_zaehlt_wartende_jobs_als_aktiv():
    text = DEPLOY_REMOTE.read_text(encoding="utf-8")
    block = text.split("  perf-probe)")[1].split("\n  doc-status)")[0]
    assert "ProcessingJob.objects.filter(status__in=[JobStatus.PENDING, JobStatus.RUNNING])" in block
    assert "worker-stop" in block, "Hinweis auf worker-stop fuer eine Messung ausserhalb des Leerlaufs fehlt"


def test_restore_probe_dokumentiert_exit_bei_fehlender_kerntabelle():
    probe = RESTORE_PROBE.read_text(encoding="utf-8")
    assert '[ "$RESTORED" -eq 1 ] && [ "$FEHLT" -eq 0 ]' in probe
    assert "keine Kerntabelle" in probe.split("set -eu")[0]
    for doku in ("docs/betrieb.md", "docs/betrieb/deployment-test.md"):
        assert "keine Kerntabelle" in (REPO / doku).read_text(encoding="utf-8"), doku
