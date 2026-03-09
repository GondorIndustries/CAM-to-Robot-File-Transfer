# CAM to Robot File Transfer

Automated pipeline for getting toolpaths from Fusion 360 into an ABB IRC5 robot controller — without size limits, without manual USB transfers, and without needing the controller to be on when the file is ready.

---

## The Problem

The ABB IRC5 FlexPendant has a hard cap of ~30,000 lines per RAPID program file. Fusion 360 toolpaths for complex stone sculptures easily exceed this, sometimes reaching 1.2 million lines. USB transfers are also slow and error-prone.

## The Solution

A three-part pipeline running on a laptop that is permanently connected to the IRC5 via Ethernet:

1. **Split** — Automatically splits oversized RAPID programs into parts that each stay under the line limit, with correct headers/footers so each part runs independently.
2. **Queue** — Holds split programs in a persistent queue. If the IRC5 is off, jobs wait silently and upload the moment it powers on.
3. **Sync** — Watches a Google Drive folder. The CAM programmer drops files in; the laptop picks them up automatically.

```
Fusion 360  →  Google Drive Inbox  →  Laptop (split + queue)  →  IRC5 controller
                                           ↑ always running           ↑ may be off
```

---

## Repository Contents

| File | Purpose |
|---|---|
| `split_rapid.py` | Standalone splitter — run manually on any RAPID program |
| `pipeline_service.py` | Always-on pipeline service (split + queue + upload) |
| `setup_gdrive.bat` | One-time setup: downloads rclone, connects to Google Drive |
| `start_pipeline.bat` | Double-click launcher for the pipeline service |

---

## Controller Setup

- **Controller**: ABB IRC5
- **Language**: RAPID
- **Connection**: Ethernet, direct link (laptop `192.168.125.2` ↔ IRC5 `192.168.125.1`)
- **FTP**: Anonymous, port 21. The FTP root maps to `HOME:` in RAPID.
- **File location on controller**: `HOME:/{program_name}_split/`

---

## Project and Naming Convention

Each sculpture has multiple machining passes (roughing → finishing → detail). Each pass is a separate RAPID program.

**Naming rules — strictly letters, numbers, and underscores only.** Dashes, dots, and spaces break ABB RAPID module loading.

```
Good:  GondorRand_100mm    AynRand_13mm    Socrates_6mm
Bad:   Gondor-Rand_100mm   Ayn.Rand_13mm   Socrates 6mm
```

**Upload order = execution order.** The pipeline processes programs in the order they arrive. For a multi-pass sculpture, upload the 100mm roughing pass first, then the 13mm, then the 6mm, etc.

The program name becomes the folder name on the controller:
```
GondorRand_100mm  →  HOME:/GondorRand_100mm_split/
GondorRand_13mm   →  HOME:/GondorRand_13mm_split/
```

---

## Programmer Workflow

1. Finish a toolpath in Fusion 360
2. Export RAPID files (`.pgf` + `.mod` files)
3. Drop them into the shared **Google Drive inbox folder** (`RobotInbox/Inbox/`)
4. Done — splitting and upload to the controller happen automatically

The pipeline confirms each upload in `pipeline.log`.

---

## Setup (One Time)

### 1. Hardware
- Connect the laptop to the IRC5 controller via Ethernet
- Set the laptop's Ethernet adapter to a static IP on the same subnet as the IRC5 (default IRC5 IP: `192.168.125.1`, so set laptop to `192.168.125.2 / 255.255.255.0`)

### 2. Software
```
pip install  (nothing — only Python standard library is used)
```
Python 3.6+ required. No third-party packages needed.

### 3. Google Drive
Run `setup_gdrive.bat` once. It will:
- Download `rclone.exe` into this folder
- Open a browser for Google Drive OAuth (name the remote exactly `gdrive`)
- Create the local `inbox/`, `processed/`, and `split_output/` folders

Then in Google Drive, create:
```
My Drive/
  RobotInbox/
    Inbox/       ← share this folder with the programmer
    Processed/
```

### 4. Start the pipeline
```
Double-click start_pipeline.bat
```
Or add it to Windows Task Scheduler to start on login.

---

## Using the Standalone Splitter

To split a program manually without the full pipeline:

```
python split_rapid.py
```

Place `split_rapid.py` in the same folder as your `.pgf` and `.mod` files and run it. It auto-detects the files and splits the movement module into parts of ≤25,000 move targets. Output goes into a `{program_name}_split/` folder ready to copy to USB or push via FTP.

Change `CONTROLLER_PATH` at the top of the script if your files will live somewhere other than `HOME:/{program_name}_split/`.

---

## Loading on the FlexPendant

After the pipeline uploads a program:

1. **FlexPendant** → File Manager → navigate to `HOME:/{program_name}_split/`
2. Load `{program_name}.pgf`
3. Press **START**

The robot will automatically load Part 01, run it, unload it, load Part 02, and continue — no operator intervention between parts.

---

## How the Split Works

The movement module (the large `.mod` file) is split on `MoveL` / `MoveJ` / `MoveAbsJ` / `MoveC` boundaries. Each part gets:

- A RAPID header (`%%%`, `VERSION`, `LANGUAGE`)
- `MODULE {name}_partNN`
- `PROC {proc}_partNN()` containing up to 25,000 move targets
- `ENDPROC` + `ENDMODULE`

The main module is rewritten to use `Load \Dynamic` / `%"proc"%` / `UnLoad` for each part in sequence. This frees heap memory between parts, which is necessary for very large programs.

The `.pgf` is rewritten to list only the main module — parts are loaded dynamically at runtime.

---

## Troubleshooting

**"File not found" error on the pendant**
The pendant's font renders `1` as `l` and `__` as `_`, so `mAdaptive1__3__0_part01` can look like `mAdaptivel_3_0_part01`. If you see this error, check the actual path in the RAPID code — it's almost always a wrong upload location rather than a wrong filename.

The files must be at the **FTP root** level (= `HOME:/`), not in a subdirectory like `/hd0a/`. Use `pipeline_service.py` to upload — it always places files at the correct location.

**Pipeline says "IRC5 offline" and won't upload**
The IRC5 is either off or the Ethernet link is down. The queue persists — once the controller comes on, the pipeline will detect it within 30 seconds and upload automatically.

**rclone sync failing**
Run `setup_gdrive.bat` again to re-authorise. Or check that the remote is named exactly `gdrive` and the GDrive folder path matches `GDRIVE_REMOTE` in `pipeline_service.py`.

**Program name has invalid characters**
The pipeline will log a warning and skip the program. Rename the files using only letters, numbers, and underscores, then re-upload.

---

## Architecture Notes

- `HOME:` in RAPID = FTP root (`/`) — **not** `/hd0a/` (physical drive root)
- IRC5 FTP: anonymous login, passive mode, port 21
- State is persisted to `state.json` using atomic rename — safe against mid-write crashes
- Uploads are only marked complete after `ftp.quit()` succeeds — partial uploads are retried
- rclone uses `copy` (not `sync`) so it never deletes local files mid-scan
