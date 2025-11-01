# Windows Care - Cleanup Utility

A comprehensive Windows cleanup tool that removes temporary files, clears browser history, empties the Recycle Bin, and optionally runs system package updates.

## Features

- 🧹 **Temporary File Cleanup**: Cleans temp directories for current user, all users, service profiles, and Windows system temp
- 🗑️ **Recycle Bin**: Empties the Recycle Bin for all drives
- 🌐 **Browser History**: Clears browsing data for Chrome, Edge, Firefox, Brave, and Opera
- ⬆️ **Package Updates**: Optional automatic updates via winget or Chocolatey
- 🔒 **Smart Deletion**: Handles locked files gracefully, schedules deletion on reboot when needed
- 📊 **Reporting**: JSON reports and detailed logging support
- 🛡️ **Safe by Default**: Dry-run mode, age filters, exclusion patterns, and confirmation prompts

## Requirements

### For Running the Python Script
- **Windows** 10/11
- **Python** 3.8+ 
- Optional: `colorama` package for enhanced console colors

### For Running the Executable
- **Windows** 10/11 only
- **No Python installation required!** The executable is standalone and runs on any Windows PC.

## Installation

### Option 1: Run as Python Script

1. Install Python 3.8 or later
2. Install optional dependencies:
   ```bash
   pip install colorama
   ```
3. Run the script:
   ```bash
   python cleanup_windows.py
   ```

### Option 2: Use the Standalone Executable (Recommended for End-Users)

**No Python required!** Simply:
1. Download `WindowsCare.exe`
2. Double-click to run
3. Follow the on-screen prompts

The executable works on any Windows PC without any additional software.

## Build Batch File

Create a file named `build.bat` with the following content:

```batch
@echo off
echo Building Windows Care executable...
pyinstaller --onefile --windowed --name "WindowsCare" --icon=NONE cleanup_windows.py
echo.
echo Build complete! Executable is in the 'dist' folder.
pause
```

**Alternative build.bat with console window** (if you want to see console output):
```batch
@echo off
echo Building Windows Care executable...
pyinstaller --onefile --name "WindowsCare" cleanup_windows.py
echo.
echo Build complete! Executable is in the 'dist' folder.
pause
```

## Usage

### Running the Python Script

```bash
python cleanup_windows.py [options]
```

### Running the Executable

**Simple method:**
- Double-click `WindowsCare.exe`

**Command line method:**
```bash
WindowsCare.exe [options]
```

## Command-Line Options

| Option | Description |
|--------|-------------|
| `--yes` | Assume yes for all prompts |
| `--no` | Assume no for all prompts |
| `--no-browser` | Skip clearing browser data |
| `--no-upgrade` | Skip package upgrades |
| `--dry-run` | Preview actions without deleting anything |
| `--force` | Force kill browsers and force delete locked files |
| `--older-than DAYS` | Only delete items older than specified days |
| `--exclude GLOB` | Exclude files matching glob pattern (can repeat) |
| `--json PATH` | Write JSON summary report to specified path |
| `--log PATH` | Append plaintext logs to specified path |
| `-q, --quiet` | Quiet mode (errors and summary only) |
| `-v, --verbose` | Increase verbosity (use -vv for maximum) |
| `--confirm-each` | Prompt yes/no before each individual action |
| `--owner-name NAME` | Display custom owner name in header (default: "Amlan") |

## Examples

### Basic cleanup with prompts
```bash
python cleanup_windows.py
# or
WindowsCare.exe
```

### Dry-run to preview actions
```bash
python cleanup_windows.py --dry-run
# or
WindowsCare.exe --dry-run
```

### Automatic cleanup (all prompts answered yes)
```bash
python cleanup_windows.py --yes
# or
WindowsCare.exe --yes
```

### Clean only files older than 7 days
```bash
python cleanup_windows.py --older-than 7
# or
WindowsCare.exe --older-than 7
```

### Exclude specific directories
```bash
python cleanup_windows.py --exclude "*\ImportantFolder\*" --exclude "*\Documents\*"
# or
WindowsCare.exe --exclude "*\ImportantFolder\*" --exclude "*\Documents\*"
```

### Quiet mode with JSON report
```bash
python cleanup_windows.py --yes -q --json cleanup_report.json
# or
WindowsCare.exe --yes -q --json cleanup_report.json
```

### Verbose logging to file
```bash
python cleanup_windows.py --verbose --log cleanup.log
# or
WindowsCare.exe --verbose --log cleanup.log
```

### Skip browser cleanup but do everything else
```bash
python cleanup_windows.py --yes --no-browser
# or
WindowsCare.exe --yes --no-browser
```

## Administrator Privileges

Some operations require administrator privileges:
- Cleaning ALL USERS' temp directories
- Cleaning Windows system temp
- Cleaning Prefetch files
- Cleaning service profile temp directories

The script will automatically request elevation when needed (UAC prompt).

## Safety Features

- **Dry-run mode**: Test what would be deleted without making changes
- **Age filtering**: Only delete files older than specified days
- **Exclusion patterns**: Glob patterns to protect specific files/directories
- **Confirmation prompts**: Ask before each action (with `--confirm-each`)
- **Error handling**: Graceful handling of locked files and permission errors

## Output

The script provides:
- Real-time progress indicators with spinners
- Color-coded status messages
- Summary statistics (files/dirs deleted, space freed)
- Optional JSON reports with detailed statistics
- Optional plaintext log files

## Building the Executable

### Using the Build Batch File

1. Ensure PyInstaller is installed: `pip install pyinstaller`
2. Run `build.bat`
3. Find `WindowsCare.exe` in the `dist` folder

### Manual Build Commands

**For windowed application (no console):**
```bash
pyinstaller --onefile --windowed --name "WindowsCare" cleanup_windows.py
```

**For console application (with console output):**
```bash
pyinstaller --onefile --name "WindowsCare" cleanup_windows.py
```

**With custom icon:**
```bash
pyinstaller --onefile --windowed --name "WindowsCare" --icon=icon.ico cleanup_windows.py
```

## Notes

- The executable uses Windows MessageBox dialogs for prompts when running as `.exe`
- Console prompts are used when running as Python script
- The script automatically handles UTF-8 encoding for Windows console
- Temporary files that cannot be deleted immediately are scheduled for deletion on reboot

## Troubleshooting

### Permission Errors
Run the script as Administrator for system-wide cleanup operations.

### Files Not Deleting
Some files may be locked by running processes. Use `--force` to force-kill browsers or restart your computer to clear locked files scheduled for deletion.

### Build Errors
Ensure PyInstaller is up to date:
```bash
pip install --upgrade pyinstaller
```

### Executable Won't Run
- Windows may show SmartScreen warning on first run - click "More info" then "Run anyway"
- Ensure you're running on Windows 10 or 11
- Try right-clicking and selecting "Run as Administrator"

## License

[Add your license information here]

## Author

Amlan (default owner name, customizable with `--owner-name`)

---

**⚠️ Warning**: This tool deletes files permanently. Always use `--dry-run` first to preview actions, and ensure you have backups of important data.
