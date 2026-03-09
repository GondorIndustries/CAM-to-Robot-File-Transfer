#!/usr/bin/env python3
"""
ABB RAPID .mod splitter for IRB6620
Splits a large movement module into chunks of <=25,000 targets.
Modules are loaded and unloaded one at a time to avoid heap overflow.

USAGE (auto-detect):
    python split_rapid.py

    Place this script in the same folder as your three RAPID files
    (.pgf + two .mod files) and run it. It finds everything automatically.

USAGE (manual):
    python split_rapid.py <movement_module.mod> <main_module.mod> <program.pgf>

REQUIREMENTS:
    - Python 3.6+
    - No external libraries needed

CONTROLLER PATH:
    Change CONTROLLER_PATH below to match where files are stored:
      "HOME:/"       - copied directly to controller hard drive
      "USBDISK1:/"   - USB stick (most common USB name on ABB FlexPendant)
      "USBDISK2:/"   - if a second USB or different USB slot
    Check your FlexPendant File Manager to confirm the exact name.
"""

import re
import os
import sys

MAX_TARGETS = 25_000
MOVE_RE     = re.compile(r'^\s*(MoveL|MoveJ|MoveAbsJ|MoveC)\b', re.IGNORECASE)

# ── Change this to match where files will be stored on the controller ────────
CONTROLLER_PATH = "HOME:/_13mmfrontcomp_split/"
# ─────────────────────────────────────────────────────────────────────────────


def auto_detect_files(folder):
    pgf_files = [f for f in os.listdir(folder) if f.lower().endswith(".pgf")]
    mod_files = [f for f in os.listdir(folder) if f.lower().endswith(".mod")]

    if len(pgf_files) == 0:
        print("ERROR: No .pgf file found in this folder.")
        sys.exit(1)
    if len(pgf_files) > 1:
        print(f"ERROR: Multiple .pgf files found: {pgf_files}")
        print("       Place only one program's files in the folder.")
        sys.exit(1)
    if len(mod_files) < 2:
        print(f"ERROR: Expected at least 2 .mod files, found {len(mod_files)}.")
        sys.exit(1)

    mod_files_sorted = sorted(
        mod_files,
        key=lambda f: os.path.getsize(os.path.join(folder, f)),
        reverse=True
    )
    movement_mod = mod_files_sorted[0]
    main_mod     = mod_files_sorted[1]
    pgf          = pgf_files[0]

    print(f"  Auto-detected:")
    print(f"    Movement module : {movement_mod}")
    print(f"    Main module     : {main_mod}")
    print(f"    Program file    : {pgf}")
    print()

    return (
        os.path.join(folder, movement_mod),
        os.path.join(folder, main_mod),
        os.path.join(folder, pgf)
    )


def read_lines(path):
    """Read file and normalise all line endings to plain \n internally."""
    with open(path, "rb") as f:
        raw = f.read()
    text = raw.decode("latin-1").replace("\r\n", "\n").replace("\r", "\n")
    return text.splitlines(keepends=True)


def write_file(path, lines):
    """Write lines converting \n to \r\n (Windows/RAPID standard)."""
    text = "".join(lines)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    with open(path, "wb") as f:
        f.write(text.replace("\n", "\r\n").encode("latin-1"))


def extract_module_name(lines):
    for line in lines:
        m = re.match(r'^\s*MODULE\s+(\S+)', line)
        if m:
            return m.group(1)
    return None


def extract_proc_info(lines):
    proc_name = None
    inside    = False
    body      = []
    for line in lines:
        if not inside:
            m = re.match(r'^\s*PROC\s+(\S+)\s*\(\s*\)', line)
            if m:
                proc_name = m.group(1)
                inside    = True
            continue
        if re.match(r'^\s*ENDPROC\b', line):
            break
        body.append(line)
    return proc_name, body


def chunk_body(body):
    chunks, current, count = [], [], 0
    for line in body:
        current.append(line)
        if MOVE_RE.match(line):
            count += 1
            if count >= MAX_TARGETS:
                chunks.append(current)
                current = []
                count   = 0
    if current:
        chunks.append(current)
    return chunks


def build_part_module(mod_name, proc_name, body_lines):
    lines  = []
    lines += ["%%%\n", "  VERSION:1\n", "  LANGUAGE:ENGLISH\n", "%%%\n", "\n"]
    lines += [f"MODULE {mod_name}\n"]
    lines += [f"  PROC {proc_name}()\n"]
    lines += body_lines
    lines += ["  ENDPROC\n"]
    lines += ["ENDMODULE\n"]
    return lines


def build_main_module(back_lines, mod_names, proc_names, main_mod_name):
    """
    Rebuild main module so main() uses Load/UnLoad to run each part
    one at a time, freeing heap memory between parts.
    """

    # Extract preamble (tool/wobj/speed declarations) — skip %%% header and MODULE line
    preamble    = []
    in_header   = False
    header_done = False
    for line in back_lines:
        if re.match(r'^\s*PROC main\(\)', line):
            break
        if re.match(r'^\s*%%%', line):
            in_header = not in_header
            continue
        if in_header:
            continue
        if re.match(r'^\s*MODULE\b', line):
            header_done = True
            continue
        if not header_done:
            continue
        preamble.append(line)

    # Extract pre-call and post-call lines from main()
    pre_call, post_call = [], []
    found_call   = False
    in_main_body = False
    for line in back_lines:
        if re.match(r'^\s*PROC main\(\)', line):
            in_main_body = True
            continue
        if re.match(r'^\s*ENDPROC\b', line):
            break
        if not in_main_body:
            continue
        if not found_call and re.search(r'^\s*p[A-Za-z0-9_]+\s*;', line):
            found_call = True
            continue
        if not found_call:
            pre_call.append(line)
        else:
            post_call.append(line)

    # Build Load/UnLoad block for each part
    load_lines = []
    for mod_name, proc_name in zip(mod_names, proc_names):
        mod_file = f"{CONTROLLER_PATH}{mod_name}.mod"
        load_lines += [
            f'    Load \\Dynamic, "{mod_file}";\n',
            f'    %"{proc_name}"% ;\n',
            f'    UnLoad "{mod_file}";\n',
            f'    !\n',
        ]

    # Assemble clean new main module
    out  = []
    out += ["%%%\n", "  VERSION:1\n", "  LANGUAGE:ENGLISH\n", "%%%\n", "\n"]
    out += [f"MODULE {main_mod_name}\n"]
    out += preamble
    out += ["  PROC main()\n"]
    out += pre_call
    out += load_lines
    out += post_call
    out += ["  ENDPROC\n"]
    out += ["ENDMODULE\n"]
    return out


def build_pgf(main_mod_name):
    """pgf only lists the main module — parts are loaded dynamically at runtime."""
    lines  = []
    lines += ['<?xml version="1.0" encoding="ISO-8859-1"?>\n']
    lines += ["<Program>\n"]
    lines += [f"  <Module>{main_mod_name}.mod</Module>\n"]
    lines += ["</Program>\n"]
    return lines


def main():
    folder = os.path.dirname(os.path.abspath(__file__))

    if len(sys.argv) == 1:
        print("\nNo arguments given — scanning folder for files...\n")
        movement_mod_path, main_mod_path, pgf_path = auto_detect_files(folder)
    elif len(sys.argv) == 4:
        movement_mod_path = sys.argv[1]
        main_mod_path     = sys.argv[2]
        pgf_path          = sys.argv[3]
        for p in [movement_mod_path, main_mod_path, pgf_path]:
            if not os.path.isfile(p):
                print(f"ERROR: File not found: {p}")
                sys.exit(1)
    else:
        print(__doc__)
        sys.exit(1)

    pgf_stem   = os.path.splitext(os.path.basename(pgf_path))[0]
    output_dir = os.path.join(folder, pgf_stem + "_split")
    os.makedirs(output_dir, exist_ok=True)

    mov_lines  = read_lines(movement_mod_path)
    back_lines = read_lines(main_mod_path)

    base_mod_name        = extract_module_name(mov_lines)
    orig_proc_name, body = extract_proc_info(mov_lines)
    main_mod_name        = extract_module_name(back_lines) or os.path.splitext(
                               os.path.basename(main_mod_path))[0]

    if not base_mod_name or not orig_proc_name:
        print("ERROR: Could not parse MODULE or PROC name from movement file.")
        sys.exit(1)

    total_targets = sum(1 for l in body if MOVE_RE.match(l))
    chunks        = chunk_body(body)
    n             = len(chunks)

    print(f"  Controller path: {CONTROLLER_PATH}")
    print(f"  Module         : {base_mod_name}")
    print(f"  Procedure      : {orig_proc_name}")
    print(f"  Targets        : {total_targets:,}")
    print(f"  Chunks         : {n}  (max {MAX_TARGETS:,} targets each)")
    print(f"  Output dir     : {output_dir}\n")

    mod_names  = [f"{base_mod_name}_part{i+1:02d}" for i in range(n)]
    proc_names = [f"{orig_proc_name}_part{i+1:02d}" for i in range(n)]

    # Write part modules
    for i, (mod_name, proc_name, body_chunk) in enumerate(
            zip(mod_names, proc_names, chunks)):
        ct    = sum(1 for l in body_chunk if MOVE_RE.match(l))
        lines = build_part_module(mod_name, proc_name, body_chunk)
        write_file(os.path.join(output_dir, f"{mod_name}.mod"), lines)
        print(f"  [{i+1:02d}/{n}] {mod_name}.mod   {ct:,} targets")

    # Write updated main module with Load/UnLoad sequencing
    back_out = build_main_module(back_lines, mod_names, proc_names, main_mod_name)
    write_file(os.path.join(output_dir, os.path.basename(main_mod_path)), back_out)
    print(f"\n  Updated {os.path.basename(main_mod_path)}  (Load/UnLoad from {CONTROLLER_PATH})")

    # Write .pgf — only lists main module
    write_file(os.path.join(output_dir, os.path.basename(pgf_path)), build_pgf(main_mod_name))
    print(f"  Updated {os.path.basename(pgf_path)}  (lists main module only)")

    print(f"\n  Done. Copy everything in the _split folder to your USB stick.")
    print(f"\n  -- On the controller ----------------------------------------")
    print(f"     1. Plug USB into FlexPendant")
    print(f"     2. File -> Load Program -> select {os.path.basename(pgf_path)}")
    print(f"        (load it from the USB, not HOME:/)")
    print(f"     3. Press START -- each part loads, runs, and unloads")
    print(f"        automatically from {CONTROLLER_PATH}")
    print(f"  -------------------------------------------------------------")
    print(f"\n  NOTE: If your USB shows a different name on the FlexPendant")
    print(f"  (not USBDISK1:/), change CONTROLLER_PATH at the top of this")
    print(f"  script and re-run it.")
    print()

    input("  Press Enter to close...")


if __name__ == "__main__":
    main()
