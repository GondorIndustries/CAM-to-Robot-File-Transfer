#!/usr/bin/env python3
"""
Combine multiple split step directories into a single program.

Takes two or more already-split-and-postprocessed step directories and merges
them into one program that runs all steps sequentially. Each step keeps its
own work object (wobj) so the robot uses the correct calibration for each.

Usage:
    python combine_steps.py split_output/Step3_split split_output/Step4_split --output split_output/Steps3and4_split

The order of arguments determines the execution order on the robot.

Requirements:
    - Each input directory must be a fully postprocessed split directory
      (i.e., already run through the pipeline: split_rapid.py + postprocess.py)
    - Each step should use a unique work object name (e.g., wStep3, wStep4)
      so calibrations don't conflict.
"""

import os
import re
import sys
import shutil
from pathlib import Path

# Controller path prefix — files are uploaded to HOME:/<output_dir_name>/
CONTROLLER_BASE = "HOME:/"

# Between-step transitions use scSafeZone.mod (scSafeDepart / scSafeArrive).
# The combiner injects calls to these procs between consecutive steps.
# No hardcoded transit moves — all motion logic lives in scSafeZone.mod.
#
# scSafeDepart(tool, wobj): lift z=50, lift z=655, MoveAbsJ to safe home
# scSafeArrive(tool, wobj, homeJoints, firstCut): wrist reconfig at safe area,
#   descend to 655, MoveJ to z=50 above first cut


def _extract_first_cartesian_move(content):
    """Return the first MoveL or MoveJ (Cartesian target) line from *content*."""
    m = re.search(r"^\s*(Move[LJ]\s+\[\[.+;)", content, re.MULTILINE)
    return m.group(1).strip() if m else None


def _extract_first_moveabsj(content):
    """Return the first MoveAbsJ instruction line (stripped) from *content*."""
    m = re.search(r"^\s*(MoveAbsJ\s+.+;)", content, re.MULTILINE)
    return m.group(1).strip() if m else None


def _extract_robtarget(move_line):
    """Extract the robtarget portion from a MoveL/MoveJ line.

    Returns the [[pos],[orient],[confdata],[extax]] string.
    """
    m = re.search(r"(\[\[[^\]]+\](?:,\[[^\]]+\]){3}\])", move_line)
    return m.group(1) if m else None


def _extract_jointtarget(moveabsj_line):
    """Extract the jointtarget portion from a MoveAbsJ line.

    Returns the [[rax],[eax]] string.
    """
    m = re.search(r"(\[\[[^\]]+\],\[[^\]]+\]\])", moveabsj_line)
    return m.group(1) if m else None


def _extract_tool_wobj(move_line):
    """Extract tool name and optional wobj name from a RAPID move line."""
    wobj_m = re.search(r"\\WObj:=(\w+)", move_line)
    wobj = wobj_m.group(1) if wobj_m else None
    if wobj:
        tool_m = re.search(r",(\w+)\\WObj", move_line)
    else:
        tool_m = re.search(r",(\w+);", move_line)
    tool = tool_m.group(1) if tool_m else "tADSK2"
    return tool, wobj


def read_file(path):
    """Read file with latin-1 encoding (RAPID standard)."""
    with open(path, "rb") as f:
        raw = f.read()
    return raw.decode("latin-1").replace("\r\n", "\n").replace("\r", "\n")


def write_file(path, text):
    """Write file with CRLF line endings (RAPID standard)."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    with open(path, "wb") as f:
        f.write(text.replace("\n", "\r\n").encode("latin-1"))


def parse_main_module(main_path):
    """Extract declarations, Load/UnLoad blocks, and metadata from a main module."""
    content = read_file(main_path)

    # Extract PERS declarations (tooldata, wobjdata, speeddata, etc.)
    pers_lines = []
    for line in content.split("\n"):
        stripped = line.strip()
        if stripped.startswith("PERS ") or stripped.startswith("!CONST "):
            pers_lines.append(line)

    # Extract Load/UnLoad blocks
    load_blocks = []
    for m in re.finditer(
        r'^\s*(Load\s+\\Dynamic,\s*"([^"]+)";\s*\n'
        r'\s*%"([^"]+)"%\s*;\s*\n'
        r'\s*UnLoad\s*"[^"]+";\s*)',
        content,
        re.MULTILINE,
    ):
        load_blocks.append({
            "full_match": m.group(1),
            "file_path": m.group(2),
            "proc_name": m.group(3),
            "mod_filename": os.path.basename(m.group(2)),
        })

    # Extract controller path from first Load block
    controller_path = None
    if load_blocks:
        first_path = load_blocks[0]["file_path"]
        controller_path = first_path.rsplit("/", 1)[0] + "/"

    return {
        "pers_lines": pers_lines,
        "load_blocks": load_blocks,
        "controller_path": controller_path,
        "content": content,
    }


def find_main_module(split_dir):
    """Find the main module (contains PROC main()) in a split directory."""
    for mod_file in sorted(split_dir.glob("*.mod")):
        content = read_file(str(mod_file))
        if re.search(r"^\s*PROC\s+main\s*\(\s*\)", content, re.MULTILINE):
            return mod_file
    return None


def find_part_files(split_dir, main_file):
    """Find all part files (not the main module, not SpeedController)."""
    parts = []
    for mod_file in sorted(split_dir.glob("*.mod")):
        if mod_file.name == main_file.name:
            continue
        if mod_file.name == "SpeedController.mod":
            continue
        # Check if it contains move instructions (it's a part file)
        content = read_file(str(mod_file))
        if re.search(r"^\s*(MoveL|MoveJ|MoveAbsJ|MoveC)\b", content, re.MULTILINE):
            parts.append(mod_file)
    return parts


def merge_pers_declarations(all_pers):
    """Merge PERS declarations from multiple steps, avoiding duplicates.

    For tooldata and speeddata, keep only one copy (they should be identical).
    For wobjdata, keep all (each step has its own wobj).
    """
    seen_tools = set()
    seen_speeds = set()
    seen_wobjs = set()
    merged = []

    for line in all_pers:
        stripped = line.strip()

        # Extract variable name
        m = re.match(r"(?:PERS|!?CONST)\s+\w+\s+(\w+)", stripped)
        if not m:
            continue
        var_name = m.group(1)

        if "tooldata" in stripped:
            if var_name not in seen_tools:
                seen_tools.add(var_name)
                merged.append(line)
        elif "wobjdata" in stripped:
            if var_name not in seen_wobjs:
                seen_wobjs.add(var_name)
                merged.append(line)
        elif "speeddata" in stripped:
            if var_name not in seen_speeds:
                seen_speeds.add(var_name)
                merged.append(line)
        elif "zonedata" in stripped:
            # Keep commented-out zone definitions
            merged.append(line)
        else:
            merged.append(line)

    return merged


def build_combined_main(output_name, controller_path, pers_lines, step_infos):
    """Build the combined main module."""
    lines = []
    lines.append("%%%\n")
    lines.append("  VERSION:1\n")
    lines.append("  LANGUAGE:ENGLISH\n")
    lines.append("%%%\n")
    lines.append("\n")
    lines.append(f"MODULE {output_name}\n")

    # Declarations
    for p in pers_lines:
        lines.append(f"{p}\n")
    lines.append("  VAR intnum speedInt;\n")
    lines.append("\n")

    # PROC main()
    lines.append("  PROC main()\n")

    # Speed control setup
    lines.append("        g_speedPct := 100;\n")
    lines.append("        g_moveCount := 0;\n")
    lines.append("        SetDO doWaterJet, 1;\n")
    lines.append("        IDelete speedInt;\n")
    lines.append("        CONNECT speedInt WITH SpeedTrap;\n")
    lines.append("        ITimer 0.15, speedInt;\n")
    lines.append("        VelSet g_speedPct, 5000;\n")
    lines.append("        scConnect;\n")

    # Robot setup
    lines.append("    !\n")
    lines.append("    ! Combined program - generated by combine_steps.py\n")
    lines.append("    !\n")
    lines.append("    AccSet 20,20;\n")
    lines.append("    ConfJ\\On;\n")
    lines.append("    ConfL\\Off;\n")
    lines.append("    !\n")

    # Load/UnLoad blocks for each step with scSafeDepart/scSafeArrive
    # between consecutive steps.
    prev_step_info = None
    for step_info in step_infos:
        step_name = step_info["name"]

        # Inject safe-area transition between consecutive steps
        if prev_step_info is not None:
            prev_tool = prev_step_info.get("tool", "tADSK2")
            prev_wobj = prev_step_info.get("wobj", "wobj0")
            next_tool = step_info.get("tool", "tADSK2")
            next_wobj = step_info.get("wobj", "wobj0")
            next_home = step_info.get("home_jointtarget", "")
            next_first_cut = step_info.get("first_robtarget", "")

            lines.append("    !\n")
            lines.append(f"    ! --- Safe area transition: {prev_step_info['name']} -> {step_name} ---\n")
            lines.append(f"    scSafeDepart {prev_tool}, {prev_wobj};\n")
            lines.append(f"    scSafeArrive {next_tool}, {next_wobj}, {next_home}, {next_first_cut};\n")
            lines.append(f"    ! --- End safe area transition ---\n")
            lines.append("    !\n")

        lines.append(f"    ! ===== {step_name} =====\n")
        for block in step_info["load_blocks"]:
            mod_file = block["mod_filename"]
            proc_name = block["proc_name"]
            full_path = f"{controller_path}{mod_file}"
            lines.append(f'    Load \\Dynamic, "{full_path}";\n')
            lines.append(f'    %"{proc_name}"% ;\n')
            lines.append(f'    UnLoad "{full_path}";\n')
            lines.append(f"    !\n")
        lines.append("    !\n")
        prev_step_info = step_info

    # Cleanup
    lines.append("    ! Reset and stop\n")
    lines.append("    ConfJ\\On;\n")
    lines.append("    ConfL\\On;\n")
    lines.append("    Stop;\n")
    lines.append("  ENDPROC\n")

    # SpeedTrap
    lines.append("    TRAP SpeedTrap\n")
    lines.append("        scExchange;\n")
    lines.append("        VelSet g_speedPct, 5000;\n")
    lines.append("    ENDTRAP\n")
    lines.append("\n")
    lines.append("ENDMODULE\n")

    return "".join(lines)


def build_pgf(main_mod_name):
    """Build .pgf that references main module + SpeedController + scSafeZone."""
    lines = []
    lines.append('<?xml version="1.0" encoding="ISO-8859-1"?>\n')
    lines.append("<Program>\n")
    lines.append(f"  <Module>{main_mod_name}.mod</Module>\n")
    lines.append("  <Module>SpeedController.mod</Module>\n")
    lines.append("  <Module>scSafeZone.mod</Module>\n")
    lines.append("</Program>\n")
    return "".join(lines)


def _extract_home_joints(part_file):
    """Extract the first MoveAbsJ home joint values from a part file.

    Looks for the first MoveAbsJ in the file — Fusion emits one at the top of
    every part as its approach pose, and this is the canonical "home joints"
    for the step.  No dependency on any injected safe-approach wrapper.
    """
    content = read_file(str(part_file))
    m = re.search(
        r"^\s*MoveAbsJ\s+\[\[([^\]]+)\]",
        content, re.MULTILINE
    )
    if m:
        try:
            joints = [float(v.strip()) for v in m.group(1).split(",")]
            return joints
        except ValueError:
            return None
    return None


def _check_joint_rotations(all_part_files):
    """Check for dangerous joint rotations (>180 deg) between consecutive parts.

    Large rotations on J4/J5/J6 (wrist axes) risk tearing water/air lines.
    """
    JOINT_NAMES = ["J1", "J2", "J3", "J4", "J5", "J6"]
    WRIST_AXES = [3, 4, 5]  # J4, J5, J6 (0-indexed)
    MAX_ROTATION = 180.0

    prev_joints = None
    prev_name = None
    warnings = []

    for pf in all_part_files:
        joints = _extract_home_joints(pf)
        if joints is None or len(joints) < 6:
            continue

        if prev_joints is not None:
            for idx in WRIST_AXES:
                diff = abs(joints[idx] - prev_joints[idx])
                # Check shortest rotation path
                if diff > 360:
                    diff = diff % 360
                if diff > MAX_ROTATION:
                    warnings.append(
                        f"    {JOINT_NAMES[idx]}: {prev_joints[idx]:.1f} -> {joints[idx]:.1f} "
                        f"({diff:.0f} deg) between {prev_name} and {pf.name}"
                    )

        prev_joints = joints
        prev_name = pf.name

    if warnings:
        print(f"\n  !! WARNING: Large wrist rotations detected (>{MAX_ROTATION} deg) !!")
        print(f"  These could tear water/air lines on the spindle:")
        for w in warnings:
            print(w)
        print(f"  Consider changing the starting angle in Fusion for the affected step.")
        print()
    else:
        print(f"\n  Joint rotation check: OK (all wrist rotations < {MAX_ROTATION} deg)")


def main():
    # Parse arguments
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    output_flag = "--output" in sys.argv

    if len(args) < 2 or (output_flag and len(args) < 3):
        print("Usage:")
        print("  python combine_steps.py <step1_split/> <step2_split/> --output <combined_split/>")
        print()
        print("Example:")
        print("  python combine_steps.py split_output/Step3_split split_output/Step4_split --output split_output/Steps3and4_split")
        print()
        print("The order of step directories determines execution order on the robot.")
        sys.exit(1)

    if output_flag:
        idx = sys.argv.index("--output")
        output_dir = Path(sys.argv[idx + 1])
        step_dirs = [Path(a) for a in args if str(Path(a)) != str(output_dir)]
    else:
        # Auto-generate output name
        step_dirs = [Path(a) for a in args]
        combined_name = "_and_".join(d.name.replace("_split", "") for d in step_dirs) + "_split"
        output_dir = step_dirs[0].parent / combined_name

    # Validate input directories
    for d in step_dirs:
        if not d.is_dir():
            print(f"Error: Directory not found: {d}")
            sys.exit(1)

    print(f"\n  Combining {len(step_dirs)} steps:")
    for i, d in enumerate(step_dirs, 1):
        print(f"    [{i}] {d.name}")
    print(f"  Output: {output_dir.name}")
    print()

    # Create output directory
    output_dir.mkdir(parents=True, exist_ok=True)

    # Process each step directory
    all_pers = []
    step_infos = []
    all_part_files = []

    for step_dir in step_dirs:
        main_file = find_main_module(step_dir)
        if main_file is None:
            print(f"Error: No main module (PROC main()) found in {step_dir}")
            sys.exit(1)

        parsed = parse_main_module(main_file)
        part_files = find_part_files(step_dir, main_file)

        all_pers.extend(parsed["pers_lines"])

        # Extract tool/wobj/home/first-cut from part01 for scSafeArrive
        tool_name = "tADSK2"
        wobj_name = "wobj0"
        home_jointtarget = ""
        first_robtarget = ""
        if part_files:
            part01_content = read_file(str(part_files[0]))
            first_cart = _extract_first_cartesian_move(part01_content)
            first_absj = _extract_first_moveabsj(part01_content)
            if first_cart:
                tool_name, wobj_name = _extract_tool_wobj(first_cart)
                first_robtarget = _extract_robtarget(first_cart) or ""
            if first_absj:
                home_jointtarget = _extract_jointtarget(first_absj) or ""

        step_info = {
            "name": step_dir.name.replace("_split", ""),
            "load_blocks": parsed["load_blocks"],
            "part_files": part_files,
            "tool": tool_name,
            "wobj": wobj_name or "wobj0",
            "home_jointtarget": home_jointtarget,
            "first_robtarget": first_robtarget,
        }
        step_infos.append(step_info)
        all_part_files.extend(part_files)

        print(f"  {step_dir.name}:")
        print(f"    Main module: {main_file.name}")
        print(f"    Part files: {len(part_files)}")
        print(f"    Load blocks: {len(parsed['load_blocks'])}")
        print(f"    Tool/wobj: {tool_name} / {wobj_name or 'wobj0'}")

    # Check for wobj conflicts
    wobj_names = set()
    for line in all_pers:
        if "wobjdata" in line:
            m = re.search(r"PERS\s+wobjdata\s+(\w+)", line)
            if m:
                wobj_names.add(m.group(1))

    if len(wobj_names) == 1 and len(step_dirs) > 1:
        wobj = list(wobj_names)[0]
        print(f"\n  WARNING: All steps use the same work object '{wobj}'.")
        print(f"  If the steps need different calibrations, rename the wobj in each")
        print(f"  step's Fusion export (e.g., wStep3, wStep4) before combining.")
        print()

    # Check for dangerous joint rotations between steps
    _check_joint_rotations(all_part_files)

    # Merge declarations (deduplicate tools/speeds, keep all wobjs)
    merged_pers = merge_pers_declarations(all_pers)

    # Determine output names and controller path
    output_name = "m" + output_dir.name.replace("_split", "")
    controller_path = f"{CONTROLLER_BASE}{output_dir.name}/"

    # Copy all part files to output directory
    print(f"\n  Copying part files to {output_dir.name}/:")
    for pf in all_part_files:
        dest = output_dir / pf.name
        if dest.exists():
            print(f"    WARNING: Duplicate filename {pf.name} — overwriting!")
        shutil.copy(pf, dest)
        print(f"    {pf.name}")

    # Copy SpeedController.mod
    sc_source = None
    for step_dir in step_dirs:
        sc = step_dir / "SpeedController.mod"
        if sc.exists():
            sc_source = sc
            break
    if sc_source:
        shutil.copy(sc_source, output_dir / "SpeedController.mod")
        print(f"    SpeedController.mod")
    else:
        # Try from spindle-modbus-robot
        sc_fallback = Path(__file__).parent.parent / "spindle-modbus-robot" / "SpeedController.mod"
        if sc_fallback.exists():
            shutil.copy(sc_fallback, output_dir / "SpeedController.mod")
            print(f"    SpeedController.mod (from spindle-modbus-robot)")
        else:
            print(f"    WARNING: SpeedController.mod not found!")

    # Copy scSafeZone.mod
    sz_source = Path(__file__).parent.parent / "spindle-modbus-robot" / "scSafeZone.mod"
    if sz_source.exists():
        shutil.copy(sz_source, output_dir / "scSafeZone.mod")
        print(f"    scSafeZone.mod")
    else:
        print(f"    WARNING: scSafeZone.mod not found at {sz_source}!")

    # Build combined main module
    print(f"\n  Building combined main module: {output_name}.mod")
    main_content = build_combined_main(output_name, controller_path, merged_pers, step_infos)
    write_file(str(output_dir / f"{output_name}.mod"), main_content)

    # Build .pgf
    pgf_name = output_dir.name.replace("_split", "")
    pgf_content = build_pgf(output_name)
    write_file(str(output_dir / f"{pgf_name}.pgf"), pgf_content)
    print(f"  Built program file: {pgf_name}.pgf")

    # Count total moves
    total_moves = 0
    for pf in all_part_files:
        content = read_file(str(pf))
        count = len(re.findall(r"g_moveCount := g_moveCount \+ 1", content))
        total_moves += count

    # Summary
    print(f"\n  ================================================")
    print(f"  Combined program: {pgf_name}")
    print(f"  Steps: {len(step_dirs)}")
    print(f"  Total part files: {len(all_part_files)}")
    print(f"  Total moves: {total_moves:,}")
    print(f"  Controller path: {controller_path}")
    print(f"  ================================================")
    print(f"\n  Next steps:")
    print(f"    1. Upload {output_dir.name}/ to the controller via FTP")
    print(f"    2. On pendant: load {pgf_name}.pgf")
    print(f"    3. Calibrate all work objects before running")
    print(f"    4. Press play — all steps run sequentially\n")


if __name__ == "__main__":
    main()
