#!/usr/bin/env python3
"""
IRC5 Auto-Deploy Pipeline
=========================
Watches the Google Drive inbox folder for new Fusion 360 RAPID exports,
splits them automatically, then uploads to the ABB IRC5 controller via FTP
whenever the controller is powered on.

Google Drive for Desktop keeps the G: drive in sync automatically — no
rclone or manual transfers needed.

DAILY USE
  Double-click start_pipeline.bat — it keeps this script running.
  The programmer drops .pgf + .mod files into the Google Drive inbox folder.
  Everything else is automatic.

PROGRAMMER NAMING RULES (IMPORTANT)
  ABB RAPID breaks on dots, dashes, spaces, or other punctuation in names.
  Use ONLY letters, numbers, and underscores.
  Good:  GondorRand_100mm    AynRand_13mm    Socrates_6mm
  Bad:   Gondor-Rand_100mm   Ayn.Rand_13mm   Socrates 6mm

  For multi-pass projects, upload passes in execution order
  (biggest tool first). The pipeline processes them in that order.
"""

import re
import os
import sys
import time
import json
import ftplib
import logging
import datetime
import xml.etree.ElementTree as ET
from pathlib import Path
from logging.handlers import RotatingFileHandler

# ── CONFIGURATION ─────────────────────────────────────────────────────────────

SCRIPT_DIR        = Path(__file__).parent.resolve()
SPLIT_OUTPUT_BASE = SCRIPT_DIR / "split_output"
STATE_FILE        = SCRIPT_DIR / "state.json"
LOG_FILE          = SCRIPT_DIR / "pipeline.log"

# Google Drive for Desktop mounts as G: — inbox folder watched directly.
# Processed files are moved to the Processed subfolder on Drive.
LOCAL_INBOX       = Path("G:/My Drive/RobotInbox/Inbox")
LOCAL_PROCESSED   = Path("G:/My Drive/RobotInbox/Processed")

# IRC5 connection
IRC5_HOST         = "192.168.125.1"
IRC5_FTP_PORT     = 21
IRC5_FTP_USER     = ""          # leave empty for anonymous
IRC5_FTP_PASS     = ""

# Splitting
MAX_TARGETS       = 25_000      # max Move targets per part file

# Timing (seconds)
POLL_INTERVAL         = 30      # main loop sleep
FILE_STABLE_CHECKS    = 3       # stability: check file size this many times
FILE_STABLE_SLEEP     = 4       # seconds between size checks (3×4 = 12s total)

# Valid RAPID identifier — letters, numbers, underscores only
NAME_RE = re.compile(r'^[A-Za-z0-9_]+$')
MOVE_RE = re.compile(r'^\s*(MoveL|MoveJ|MoveAbsJ|MoveC)\b', re.IGNORECASE)

# ─────────────────────────────────────────────────────────────────────────────

log = logging.getLogger("pipeline")


# ── LOGGING ──────────────────────────────────────────────────────────────────

def setup_logging():
    log.setLevel(logging.DEBUG)
    fh = RotatingFileHandler(LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3,
                             encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s  %(levelname)-8s  %(message)s",
                            datefmt="%Y-%m-%d %H:%M:%S")
    fh.setFormatter(fmt)
    ch.setFormatter(fmt)
    log.addHandler(fh)
    log.addHandler(ch)


# ── STATE ─────────────────────────────────────────────────────────────────────

def _empty_state():
    return {
        "schema_version": 1,
        "queue": [],
        "processed_pgf_stems": [],
        "last_rclone_sync": None,
    }


def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            log.warning(f"Could not read state.json ({e}) — starting fresh.")
    return _empty_state()


def save_state(state):
    """Atomic write: write to .tmp then rename."""
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(STATE_FILE)


def make_job_id(pgf_stem):
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{pgf_stem}_{ts}"


# ── SPLIT FUNCTIONS ───────────────────────────────────────────────────────────
# Adapted from split_rapid.py — CONTROLLER_PATH is passed as a parameter
# so each job can have its own path derived from the program name.

def _read_lines(path):
    raw = Path(path).read_bytes()
    text = raw.decode("latin-1").replace("\r\n", "\n").replace("\r", "\n")
    return text.splitlines(keepends=True)


def _write_file(path, lines):
    text = "".join(lines).replace("\r\n", "\n").replace("\r", "\n")
    Path(path).write_bytes(text.replace("\n", "\r\n").encode("latin-1"))


def _extract_module_name(lines):
    for line in lines:
        m = re.match(r'^\s*MODULE\s+(\S+)', line)
        if m:
            return m.group(1)
    return None


def _extract_proc_info(lines):
    proc_name, body, inside = None, [], False
    for line in lines:
        if not inside:
            m = re.match(r'^\s*PROC\s+(\S+)\s*\(\s*\)', line)
            if m:
                proc_name = m.group(1)
                inside = True
            continue
        if re.match(r'^\s*ENDPROC\b', line):
            break
        body.append(line)
    return proc_name, body


def _chunk_body(body):
    chunks, current, count = [], [], 0
    for line in body:
        current.append(line)
        if MOVE_RE.match(line):
            count += 1
            if count >= MAX_TARGETS:
                chunks.append(current)
                current, count = [], 0
    if current:
        chunks.append(current)
    return chunks


def _build_part_module(mod_name, proc_name, body_lines):
    return (
        ["%%%\n", "  VERSION:1\n", "  LANGUAGE:ENGLISH\n", "%%%\n", "\n"]
        + [f"MODULE {mod_name}\n", f"  PROC {proc_name}()\n"]
        + body_lines
        + ["  ENDPROC\n", "ENDMODULE\n"]
    )


def _extract_main_parts(back_lines):
    """Pull preamble (tool/wobj/speed defs), pre-call, and post-call from main()."""
    preamble, pre_call, post_call = [], [], []
    in_header = False
    header_done = False
    in_main = False
    found_call = False
    for line in back_lines:
        if re.match(r'^\s*%%%', line):
            in_header = not in_header
            continue
        if in_header:
            continue
        if re.match(r'^\s*MODULE\b', line):
            header_done = True
            continue
        if re.match(r'^\s*PROC main\(\)', line):
            in_main = True
            continue
        if re.match(r'^\s*ENDPROC\b', line):
            break
        if not in_main:
            if header_done:
                preamble.append(line)
            continue
        if not found_call and re.search(r'^\s*p[A-Za-z0-9_]+\s*;', line):
            found_call = True
            continue
        if not found_call:
            pre_call.append(line)
        else:
            post_call.append(line)
    return preamble, pre_call, post_call


def _build_main_module(back_lines, mod_names, proc_names, main_mod_name, controller_path):
    preamble, pre_call, post_call = _extract_main_parts(back_lines)
    load_lines = []
    for mod_name, proc_name in zip(mod_names, proc_names):
        mod_file = f"{controller_path}{mod_name}.mod"
        load_lines += [
            f'    Load \\Dynamic, "{mod_file}";\n',
            f'    %"{proc_name}"% ;\n',
            f'    UnLoad "{mod_file}";\n',
            f'    !\n',
        ]
    return (
        ["%%%\n", "  VERSION:1\n", "  LANGUAGE:ENGLISH\n", "%%%\n", "\n"]
        + [f"MODULE {main_mod_name}\n"]
        + preamble
        + ["  PROC main()\n"]
        + pre_call
        + load_lines
        + post_call
        + ["  ENDPROC\n", "ENDMODULE\n"]
    )


def _build_pgf(main_mod_name):
    return [
        '<?xml version="1.0" encoding="ISO-8859-1"?>\n',
        "<Program>\n",
        f"  <Module>{main_mod_name}.mod</Module>\n",
        "</Program>\n",
    ]


# ── INBOX SCANNING ────────────────────────────────────────────────────────────

def parse_pgf_modules(pgf_path):
    """Return list of module filenames referenced in a .pgf file."""
    tree = ET.parse(pgf_path)
    return [m.text.strip() for m in tree.findall(".//Module") if m.text]


def is_file_stable(path):
    """Return True if the file size has been constant for FILE_STABLE_CHECKS checks."""
    sizes = []
    for _ in range(FILE_STABLE_CHECKS):
        try:
            sizes.append(path.stat().st_size)
        except FileNotFoundError:
            return False
        time.sleep(FILE_STABLE_SLEEP)
    return len(set(sizes)) == 1 and sizes[0] > 0


def scan_inbox(state):
    """Return list of complete, stable, valid program sets ready to process."""
    processed = set(state.get("processed_pgf_stems", []))
    ready = []

    for pgf_path in sorted(LOCAL_INBOX.glob("*.pgf")):
        stem = pgf_path.stem

        if stem in processed:
            continue

        # Validate program name
        if not NAME_RE.match(stem):
            log.warning(
                f"Skipping '{pgf_path.name}': name contains invalid characters. "
                f"Use only letters, numbers, underscores."
            )
            continue

        # Parse which .mod files this program needs
        try:
            module_filenames = parse_pgf_modules(pgf_path)
        except Exception as e:
            log.warning(f"Cannot parse {pgf_path.name}: {e}")
            continue

        # Check all referenced .mod files exist
        mod_paths = []
        all_present = True
        for mod_filename in module_filenames:
            mod_path = LOCAL_INBOX / mod_filename
            if not mod_path.exists():
                log.debug(f"  Waiting for {mod_filename} ...")
                all_present = False
                break
            mod_paths.append(mod_path)
        if not all_present:
            continue

        # Validate module names
        names_ok = True
        for mp in mod_paths:
            if not NAME_RE.match(mp.stem):
                log.warning(
                    f"Skipping '{stem}': module '{mp.stem}' has invalid characters."
                )
                names_ok = False
                break
        if not names_ok:
            continue

        # Stability check — all files must not be changing size
        all_stable = True
        log.info(f"Found candidate: {stem} — checking file stability ...")
        for p in [pgf_path] + mod_paths:
            if not is_file_stable(p):
                log.debug(f"  Still syncing: {p.name}")
                all_stable = False
                break
        if not all_stable:
            continue

        log.info(f"Ready to process: {stem}")
        ready.append({
            "pgf_stem": stem,
            "pgf_path": pgf_path,
            "mod_paths": mod_paths,
        })

    return ready


# ── SPLITTING ─────────────────────────────────────────────────────────────────

def run_split(program_set):
    stem          = program_set["pgf_stem"]
    pgf_path      = program_set["pgf_path"]
    mod_paths     = program_set["mod_paths"]
    output_dir    = SPLIT_OUTPUT_BASE / f"{stem}_split"
    ctrl_path     = f"HOME:/{stem}_split/"

    output_dir.mkdir(parents=True, exist_ok=True)

    # Identify movement module (largest) and main module (smaller)
    sorted_mods = sorted(mod_paths, key=lambda p: p.stat().st_size, reverse=True)
    if len(sorted_mods) < 2:
        raise ValueError(f"Expected at least 2 .mod files for '{stem}', got {len(sorted_mods)}")
    movement_mod_path = sorted_mods[0]
    main_mod_path     = sorted_mods[1]

    mov_lines  = _read_lines(movement_mod_path)
    back_lines = _read_lines(main_mod_path)

    base_mod_name         = _extract_module_name(mov_lines)
    orig_proc_name, body  = _extract_proc_info(mov_lines)
    main_mod_name         = _extract_module_name(back_lines) or main_mod_path.stem

    if not base_mod_name or not orig_proc_name:
        raise ValueError(f"Could not parse MODULE/PROC from {movement_mod_path.name}")

    total_targets = sum(1 for l in body if MOVE_RE.match(l))
    chunks = _chunk_body(body)
    n = len(chunks)

    log.info(f"  Splitting '{base_mod_name}': {total_targets:,} targets → {n} parts")

    mod_names  = [f"{base_mod_name}_part{i+1:02d}" for i in range(n)]
    proc_names = [f"{orig_proc_name}_part{i+1:02d}" for i in range(n)]

    # Write part modules
    for mod_name, proc_name, chunk in zip(mod_names, proc_names, chunks):
        ct = sum(1 for l in chunk if MOVE_RE.match(l))
        lines = _build_part_module(mod_name, proc_name, chunk)
        _write_file(output_dir / f"{mod_name}.mod", lines)
        log.debug(f"    {mod_name}.mod  ({ct:,} targets)")

    # Write updated main module
    main_out = _build_main_module(back_lines, mod_names, proc_names, main_mod_name, ctrl_path)
    _write_file(output_dir / main_mod_path.name, main_out)

    # Write .pgf
    pgf_out = _build_pgf(main_mod_name)
    _write_file(output_dir / pgf_path.name, pgf_out)

    log.info(f"  Split complete → {output_dir.name}  ({n} parts, controller path: {ctrl_path})")

    files_to_upload = [f.name for f in sorted(output_dir.iterdir()) if f.is_file()]

    return {
        "job_id":            make_job_id(stem),
        "pgf_stem":          stem,
        "status":            "pending_upload",
        "created_at":        datetime.datetime.now().isoformat(),
        "split_output_dir":  str(output_dir),
        "files_to_upload":   files_to_upload,
        "ftp_remote_dir":    f"{stem}_split",
        "controller_path":   ctrl_path,
        "upload_attempts":   0,
        "last_attempt_at":   None,
        "completed_at":      None,
        "error":             None,
    }


# ── IRC5 / FTP ────────────────────────────────────────────────────────────────

def check_irc5_online():
    try:
        ftp = ftplib.FTP()
        ftp.connect(IRC5_HOST, IRC5_FTP_PORT, timeout=5)
        ftp.login(IRC5_FTP_USER, IRC5_FTP_PASS)
        ftp.quit()
        return True
    except Exception:
        return False


def upload_job(job):
    """Upload all split files to the IRC5. Returns True only on clean completion."""
    remote_dir = job["ftp_remote_dir"]
    local_dir  = Path(job["split_output_dir"])
    files      = job["files_to_upload"]

    try:
        ftp = ftplib.FTP()
        ftp.connect(IRC5_HOST, IRC5_FTP_PORT, timeout=15)
        ftp.login(IRC5_FTP_USER, IRC5_FTP_PASS)
        ftp.set_pasv(True)

        # Create remote directory (ignore error if already exists)
        try:
            ftp.mkd(remote_dir)
            log.info(f"  Created HOME:/{remote_dir}/")
        except ftplib.error_perm:
            pass

        total = len(files)
        for i, filename in enumerate(files, 1):
            local_path = local_dir / filename
            size_mb = local_path.stat().st_size / 1_048_576
            log.info(f"  [{i}/{total}] {filename}  ({size_mb:.1f} MB)")
            with open(local_path, "rb") as f:
                ftp.storbinary(f"STOR {remote_dir}/{filename}", f, blocksize=65536)

        ftp.quit()  # clean close — only return True after this succeeds
        return True

    except Exception as e:
        log.warning(f"  Upload error: {e}")
        return False


def archive_inbox_set(pgf_stem, mod_paths, pgf_path):
    """Move processed inbox files to local processed folder."""
    dest = LOCAL_PROCESSED / pgf_stem
    dest.mkdir(parents=True, exist_ok=True)
    for f in [pgf_path] + mod_paths:
        try:
            target = dest / f.name
            f.rename(target)
            log.debug(f"  Archived {f.name}")
        except Exception as e:
            log.warning(f"  Could not archive {f.name}: {e}")


# ── MAIN LOOP ─────────────────────────────────────────────────────────────────

def main():
    setup_logging()

    SPLIT_OUTPUT_BASE.mkdir(parents=True, exist_ok=True)

    log.info("=" * 60)
    log.info("IRC5 Auto-Deploy Pipeline")
    log.info(f"  Inbox    : {LOCAL_INBOX}")
    log.info(f"  IRC5     : {IRC5_HOST}:{IRC5_FTP_PORT}")
    log.info("=" * 60)

    if not LOCAL_INBOX.exists():
        log.warning(f"Inbox folder not found: {LOCAL_INBOX}")
        log.warning("Make sure Google Drive for Desktop is running and the G: drive is mounted.")

    state = load_state()

    while True:
        # ── 1. Detect and split new programs ──────────────────────────────
        ready_sets = scan_inbox(state)
        for program_set in ready_sets:
            stem = program_set["pgf_stem"]
            log.info(f"Processing: {stem}")
            try:
                job = run_split(program_set)
                state["queue"].append(job)
                state["processed_pgf_stems"].append(stem)
                save_state(state)
                archive_inbox_set(stem, program_set["mod_paths"], program_set["pgf_path"])
                log.info(f"Queued for upload: {stem}")
            except Exception as e:
                log.error(f"Split failed for '{stem}': {e}", exc_info=True)
                # Don't add to processed_pgf_stems — allow retry after fixing

        # ── 3. Upload pending jobs when IRC5 is online ────────────────────
        pending = [j for j in state["queue"] if j["status"] == "pending_upload"]

        if pending:
            if check_irc5_online():
                log.info(f"IRC5 online — {len(pending)} job(s) to upload")
                for job in pending:
                    log.info(f"Uploading: {job['pgf_stem']}  ({len(job['files_to_upload'])} files)")
                    job["status"] = "uploading"
                    job["upload_attempts"] += 1
                    job["last_attempt_at"] = datetime.datetime.now().isoformat()
                    save_state(state)

                    success = upload_job(job)
                    if success:
                        job["status"] = "uploaded"
                        job["completed_at"] = datetime.datetime.now().isoformat()
                        log.info(f"DONE: '{job['pgf_stem']}' is on the controller at "
                                 f"HOME:/{job['ftp_remote_dir']}/")
                    else:
                        job["status"] = "pending_upload"  # will retry next cycle
                        log.warning(f"Upload failed for '{job['pgf_stem']}' — will retry.")
                    save_state(state)
            else:
                log.debug(f"IRC5 offline — {len(pending)} job(s) waiting in queue.")

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("Pipeline stopped.")
