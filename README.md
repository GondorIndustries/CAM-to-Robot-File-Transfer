# CAM to Robot File Transfer

Automated pipeline for getting toolpaths from Fusion 360 into an ABB IRC5 robot controller — without size limits, without manual USB transfers, and without needing the controller to be on when the file is ready.

---

## How It Works

The IRC5 FlexPendant has a hard cap of roughly 30,000 lines per RAPID program file. A detailed stone sculpture toolpath can exceed 1 million lines. This pipeline solves that by automatically splitting large programs into numbered parts, each within the limit, and loading them one at a time on the controller.

```
Fusion 360  →  Google Drive Inbox  →  This laptop  →  IRC5 controller
                                      always running    may be off — that's fine
```

**Step by step:**

1. The CAM programmer finishes a toolpath in Fusion 360 and exports it as a RAPID program (a `.pgf` file and one or two `.mod` files).
2. The programmer drops those files into a shared Google Drive folder.
3. Google Drive syncs the files to the laptop automatically (mounted as the `G:` drive).
4. The pipeline detects the new files, splits the large movement module into parts of up to 25,000 move targets each, and adds the job to an upload queue.
5. The pipeline checks the IRC5 every 30 seconds. The moment the controller is powered on and reachable, it uploads the split files via FTP to `HOME:/ProgramName_split/` on the controller.
6. On the FlexPendant, the operator loads the program and presses Start. The robot automatically loads Part 01, runs it, unloads it, loads Part 02, and so on — no intervention needed between parts.

If the controller is off when a job is ready, the queue persists to disk. It uploads the next time the controller is on, even after a laptop restart.

---

## File Naming Convention

> **This section is for the CAM programmer.**

ABB RAPID module names must contain only **letters, numbers, and underscores**. Anything else — dashes, dots, spaces, brackets — will cause the controller to fail when loading the file.

### The rule

```
Use only:   A–Z   a–z   0–9   _
Never use:  spaces  -  .  (  )  /  \  ,  @  #  or any other character
```

### Format

Name each program as: **`ProjectName_ToolDiameter`**

The project name identifies the sculpture. The tool diameter identifies the pass. Together they make each program unique and immediately readable.

| Program name | What it means |
|---|---|
| `GondorRand_100mm` | Gondor Rand sculpture, 100 mm roughing pass |
| `GondorRand_13mm` | Gondor Rand sculpture, 13 mm finishing pass |
| `GondorRand_6mm` | Gondor Rand sculpture, 6 mm detail pass |
| `AynRand_50mm` | Ayn Rand sculpture, 50 mm roughing pass |
| `Socrates_8mm` | Socrates sculpture, 8 mm finishing pass |

### Upload order matters

The pipeline processes programs in the order they arrive in Google Drive. For a multi-pass sculpture, **upload the roughing pass first, then progressively finer passes**. Do not upload all passes at once unless you are confident in the order.

### How to set the name in Fusion 360

The program name comes from what Fusion 360 uses when it exports the files. Set the **Setup name** or **Post output filename** in Fusion to follow this convention before exporting. If Fusion adds anything automatically (like a prefix `m` on module files), that is fine — the pipeline handles it. The `.pgf` filename is the one that must follow the naming rule.

### Examples of bad names and why they fail

| Bad name | Problem |
|---|---|
| `Gondor Rand_100mm` | Space in name — RAPID treats this as two separate tokens |
| `Gondor-Rand_100mm` | Dash — not a valid RAPID identifier character |
| `Gondor_Rand_100mm.pgf` | Dot — only the extension should have a dot, not the name |
| `GondorRand_100%` | Percent sign — will corrupt the module load call |
| `GondorRand_(front)` | Brackets — invalid in RAPID identifiers |

---

## Programmer Workflow

1. Finish a toolpath in Fusion 360.
2. Set the program name following the convention above (`ProjectName_ToolDiameter`).
3. Export RAPID files — Fusion produces a `.pgf` and one or two `.mod` files.
4. Drop all of those files into the shared **Google Drive inbox folder**.
5. Done. Splitting, queuing, and upload happen automatically.

You can drop the next pass immediately after — the pipeline queues jobs and processes them in order.

---

## Loading on the FlexPendant

After the pipeline uploads a program, the operator loads it on the pendant:

1. FlexPendant → **File Manager** → navigate to `HOME:/{ProgramName}_split/`
2. Select `{ProgramName}.pgf` → **Load Program**
3. Press **Start**

The robot loads Part 01, executes it, unloads it, then loads Part 02 automatically, and so on through all parts. No operator input is needed between parts.

---

## Repository Contents

| File | Purpose |
|---|---|
| `pipeline_service.py` | The pipeline — detects, splits, queues, uploads |
| `split_rapid.py` | Standalone manual splitter (no pipeline, for one-off use) |
| `start_pipeline.bat` | Double-click to start the pipeline |
| `setup_new_computer.bat` | Run this when moving to a new laptop |

---

## Moving to a New Computer

Run `setup_new_computer.bat` on the new machine. It handles most of the setup automatically. See the [Moving Computers](#moving-to-a-new-computer-detail) section below for the full walkthrough.

---

## Initial Setup (First Time)

### 1. Hardware

Connect the laptop to the IRC5 controller with an Ethernet cable (direct, no switch needed). The IRC5's default IP is `192.168.125.1`. The laptop's Ethernet adapter must be set to a static IP on the same subnet.

`setup_new_computer.bat` configures this automatically.

### 2. Software requirements

- **Python 3.6+** — download from [python.org](https://www.python.org/downloads/). No extra packages needed; only the standard library is used.
- **Google Drive for Desktop** — download from [drive.google.com](https://drive.google.com/). Sign in with the Gondor Industries Google account. After setup, Google Drive mounts as the `G:` drive.
- **Git** — download from [git-scm.com](https://git-scm.com/) (needed only to clone this repo).

### 3. Clone and run

```
git clone https://github.com/GondorIndustries/CAM-to-Robot-File-Transfer.git
cd CAM-to-Robot-File-Transfer
start_pipeline.bat
```

### 4. Google Drive folder

The inbox folder (`G:\My Drive\RobotInbox\Inbox`) is already shared. Google Drive for Desktop will sync it to the `G:` drive once you sign in. No further Google Drive setup is needed on a new machine.

---

<a name="moving-to-a-new-computer-detail"></a>
## Moving to a New Computer — Full Walkthrough

### Step 1 — Install the three requirements on the new machine

| Software | Where to get it | Notes |
|---|---|---|
| Python 3.6+ | [python.org/downloads](https://www.python.org/downloads/) | Tick **"Add Python to PATH"** during install |
| Google Drive for Desktop | [drive.google.com](https://drive.google.com/) | Sign in with the Gondor Industries Google account |
| Git | [git-scm.com](https://git-scm.com/) | Default install options are fine |

After installing Google Drive for Desktop, confirm the `G:` drive appears in File Explorer and `G:\My Drive\RobotInbox\Inbox` is visible.

### Step 2 — Clone the repo and run setup

Open a command prompt and run:

```
git clone https://github.com/GondorIndustries/CAM-to-Robot-File-Transfer.git
cd CAM-to-Robot-File-Transfer
setup_new_computer.bat
```

`setup_new_computer.bat` will:
- Set the Ethernet adapter to the correct static IP (`192.168.125.2 / 255.255.255.0`) so the laptop can talk to the IRC5
- Test the connection to the IRC5 controller
- Create a Windows Task Scheduler entry so the pipeline starts automatically when you log in
- Print a confirmation of everything it did

### Step 3 — Start the pipeline

Double-click `start_pipeline.bat`, or log out and back in if you used Task Scheduler.

That is everything. The old computer does not need to be wiped in any particular way — nothing sensitive is stored in the repo. The `state.json` and logs are local only.

---

## Standalone Splitter

To split a program manually without the full pipeline:

1. Place `split_rapid.py` in the same folder as your `.pgf` and `.mod` files.
2. Open a command prompt in that folder.
3. Run `python split_rapid.py`

It auto-detects the files, splits the movement module at safe move-instruction boundaries, and writes the output to a `{ProgramName}_split/` subfolder.

---

## How the Split Works (Technical)

The movement module (the large `.mod` file) is split on `MoveL`, `MoveJ`, `MoveAbsJ`, and `MoveC` instruction boundaries — never mid-move, never mid-segment. Each part file is a fully valid, self-contained RAPID module:

```
MODULE GondorRand_100mm_part01
  PROC pAdaptive_100mm_part01()
    MoveL ...
    MoveL ...
    (up to 25,000 move targets)
  ENDPROC
ENDMODULE
```

The main module is rewritten so that `main()` loads each part in sequence using RAPID's dynamic loading mechanism, runs it, then unloads it before loading the next — freeing heap memory between parts:

```rapid
Load \Dynamic, "HOME:/GondorRand_100mm_split/GondorRand_100mm_part01.mod";
%"pAdaptive_100mm_part01"% ;
UnLoad "HOME:/GondorRand_100mm_split/GondorRand_100mm_part01.mod";

Load \Dynamic, "HOME:/GondorRand_100mm_split/GondorRand_100mm_part02.mod";
%"pAdaptive_100mm_part02"% ;
UnLoad "HOME:/GondorRand_100mm_split/GondorRand_100mm_part02.mod";
```

The `.pgf` (program file) is rewritten to list only the main module. The part files are not listed in it — they are loaded dynamically at runtime so they do not count against the FlexPendant's initial load limit.

All tool definitions, work object definitions, speed data, and robot configuration (AccSet, ConfJ, ConfL) from the original main module are preserved in the rewritten main module.

---

## Troubleshooting

**"File not found" on the pendant when starting**
The FlexPendant's font renders the digit `1` identically to a lowercase `l`, and double underscores `__` look like single `_`. So `mAdaptive1__3__0_part01` may appear as `mAdaptivel_3_0_part01` on screen — this is a display quirk, not an actual file naming issue. The real causes of this error are:
- Files were uploaded to the wrong location on the controller (must be at FTP root = `HOME:/`, not under `/hd0a/`). The pipeline always puts them in the right place.
- The program name used in the pendant does not match the folder on the controller.

**Pipeline stuck saying "IRC5 offline"**
The controller is off, the Ethernet cable is unplugged, or the laptop's Ethernet IP was reset (e.g. after a Windows update). Run `setup_new_computer.bat` again to re-apply the static IP. The queue is safe — it will upload the moment the controller is reachable.

**Google Drive not syncing**
Check that Google Drive for Desktop is running (look for the Drive icon in the system tray). The `G:` drive must be mounted for the pipeline to see new files.

**Program name rejected (invalid characters warning in log)**
Rename the files to use only letters, numbers, and underscores, then drop them into the inbox again.

**Checking what is in the queue**
Open `state.json` in any text editor. Each entry under `"queue"` shows the program name, status (`pending_upload` or `uploaded`), and timestamps.

---

## Architecture Notes

- `HOME:` in RAPID = the FTP root (`/`) on the IRC5 — not `/hd0a/`, which is the physical drive root
- IRC5 FTP: anonymous login, passive mode, port 21
- Uploads are only marked complete after `ftp.quit()` closes cleanly — a crash mid-upload retries the whole job
- `state.json` is written atomically (write to `.tmp`, then rename) so a crash mid-save never corrupts the queue
- The pipeline watches `G:\My Drive\RobotInbox\Inbox` directly — Google Drive for Desktop handles all syncing
