import os
import stat
import hashlib
import shutil
import json
import logging
import re
import urllib.request
import urllib.parse
import urllib.error
from pathlib import Path
from collections import defaultdict

VIDEO_EXTENSIONS = {'.mp4', '.mkv', '.avi', '.mov', '.wmv', '.m4v',
                    '.mpg', '.mpeg', '.flv', '.ts', '.vob', '.divx', '.webm'}

BACKUP_DIR  = Path.home() / "MovieDuplicateBackup"
CONFIG_FILE = Path.home() / ".movie_scanner_config.json"
LOG_FILE    = Path.home() / "movie_scanner.log"

TMDB_BASE = "https://api.themoviedb.org/3"

logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)


# ── Security helpers ─────────────────────────────────────────────────────────

def resolve_safe_path(user_input):
    """
    Resolve a user-supplied path to an absolute, real path.
    Rejects symlinks at the top level and ensures the path exists as a directory.
    Returns a Path or None on failure.
    """
    try:
        p = Path(user_input.strip()).expanduser()
        p_abs = p.absolute()
        if p_abs.is_symlink():
            print(f"  [!] Symlink directories are not allowed: {p_abs}")
            logging.warning(f"Rejected symlink path: {p_abs}")
            return None
        real = p_abs.resolve()
        if not real.exists():
            print(f"  [!] Directory not found: {real}")
            return None
        if not real.is_dir():
            print(f"  [!] Not a directory: {real}")
            return None
        return real
    except Exception as e:
        print(f"  [!] Invalid path: {e}")
        return None


def is_safe_file(path):
    """Return True only if path is a real, non-symlink file."""
    try:
        return path.exists() and not path.is_symlink() and path.is_file()
    except Exception:
        return False


def safe_filename(name):
    """Strip path separators and null bytes from a filename component."""
    name = name.replace('\x00', '')
    for ch in ('/', '\\', '..'):
        name = name.replace(ch, '_')
    return name.strip('. ') or '_'


def secure_config_file(filepath):
    """Set config file to owner-read/write only (600)."""
    try:
        os.chmod(filepath, stat.S_IRUSR | stat.S_IWUSR)
    except Exception:
        pass


# ── Config ────────────────────────────────────────────────────────────────────

def load_config():
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text())
        except Exception:
            pass
    return {}


def save_config(config):
    CONFIG_FILE.write_text(json.dumps(config, indent=2))
    secure_config_file(CONFIG_FILE)


# ── Hashing ──────────────────────────────────────────────────────────────────

def get_file_hash(filepath, quick=False):
    """Hash a file. Quick mode reads first + last 64KB for large video files."""
    h = hashlib.sha256()
    try:
        size = os.path.getsize(filepath)
        with open(filepath, 'rb') as f:
            if quick:
                h.update(f.read(65536))
                if size > 131072:
                    f.seek(-65536, 2)
                    h.update(f.read(65536))
            else:
                while chunk := f.read(131072):
                    h.update(chunk)
        return h.hexdigest()
    except (OSError, IOError):
        return None


# ── Scanning ─────────────────────────────────────────────────────────────────

def scan_directory(directory):
    """Scan recursively, skipping symlinks."""
    video_files = []
    for path in directory.rglob('*'):
        if is_safe_file(path) and path.suffix.lower() in VIDEO_EXTENSIONS:
            video_files.append(path)
    return video_files


def find_duplicates(files):
    print(f"\n  Hashing {len(files)} files (this may take a while for large collections)...")
    hash_groups = defaultdict(list)

    quick_groups = defaultdict(list)
    for i, f in enumerate(files, 1):
        print(f"  Quick-hashing {i}/{len(files)}: {f.name[:60]}", end='\r')
        qh = get_file_hash(f, quick=True)
        if qh:
            quick_groups[qh].append(f)
    print()

    candidates = [g for g in quick_groups.values() if len(g) > 1]
    total_candidates = sum(len(g) for g in candidates)
    print(f"  Full-hashing {total_candidates} candidate files...")

    done = 0
    for group in candidates:
        for f in group:
            done += 1
            print(f"  Full-hashing {done}/{total_candidates}: {f.name[:60]}", end='\r')
            fh = get_file_hash(f, quick=False)
            if fh:
                hash_groups[fh].append(f)
    print()

    return {h: paths for h, paths in hash_groups.items() if len(paths) > 1}


# ── TMDB API ──────────────────────────────────────────────────────────────────

def tmdb_search(title, year, token):
    params = urllib.parse.urlencode({'query': title, 'year': year or ''})
    url = f"{TMDB_BASE}/search/movie?{params}"
    req = urllib.request.Request(url, headers={
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json'
    })
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            results = data.get('results', [])
            if results:
                return results[0]
    except Exception as e:
        logging.warning(f"TMDB lookup failed for '{title}': {e}")
    return None


def parse_filename(filename):
    stem = Path(filename).stem
    stem = re.sub(r'\b(1080p|720p|4k|bluray|webrip|hdtv|x264|x265|hevc|aac|dts|remux)\b',
                  '', stem, flags=re.IGNORECASE)
    year_match = re.search(r'\b(19|20)\d{2}\b', stem)
    year = year_match.group(0) if year_match else None
    if year_match:
        stem = stem[:year_match.start()]
    title = re.sub(r'[._\-\[\]()]', ' ', stem).strip()
    title = re.sub(r'\s+', ' ', title).strip()
    return title, year


# ── Organization ──────────────────────────────────────────────────────────────

def organize_movies(files, dest_dir, token):
    moved = 0
    skipped = 0
    not_found = 0

    print(f"\n  Organizing {len(files)} files using TMDB...\n")
    for i, f in enumerate(files, 1):
        title, year = parse_filename(f.name)
        print(f"  [{i}/{len(files)}] Looking up: {title} ({year or '?'})")

        info = tmdb_search(title, year, token) if token else None

        if info:
            movie_title = info.get('title', title)
            release = info.get('release_date', '')
            movie_year = release[:4] if release else (year or 'Unknown')
            safe_title = re.sub(r'[<>:"/\\|?*\x00]', '', movie_title).strip('. ') or 'Unknown'
            folder = dest_dir / movie_year / safe_title
        else:
            not_found += 1
            safe_title = re.sub(r'[<>:"/\\|?*\x00]', '', title).strip('. ') or 'Unknown'
            folder = dest_dir / (year or 'Unknown') / safe_title

        # Path traversal guard: ensure folder stays inside dest_dir
        try:
            folder.resolve().relative_to(dest_dir.resolve())
        except ValueError:
            logging.error(f"Path traversal blocked: {folder}")
            skipped += 1
            continue

        try:
            folder.mkdir(parents=True, exist_ok=True)
            dest = folder / f.name
            if dest.exists():
                print(f"    Skipped (already exists): {dest}")
                skipped += 1
                continue
            shutil.move(str(f), str(dest))
            logging.info(f"Organized: {f} -> {dest}")
            moved += 1
        except Exception as e:
            logging.error(f"Failed to move {f}: {e}")
            skipped += 1

    print(f"\n  ✓ Done.  Moved: {moved}  |  Skipped: {skipped}  |  Not found on TMDB: {not_found}")


# ── Backup & Delete ───────────────────────────────────────────────────────────

def backup_and_delete(file_path):
    try:
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        safe_name = safe_filename(file_path.name)
        dest = BACKUP_DIR / safe_name
        counter = 1
        stem = Path(safe_name).stem
        suffix = Path(safe_name).suffix
        while dest.exists():
            dest = BACKUP_DIR / f"{stem}_{counter}{suffix}"
            counter += 1
        # Path traversal guard
        if not str(dest.resolve()).startswith(str(BACKUP_DIR.resolve())):
            logging.error(f"Path traversal blocked for: {file_path}")
            return False
        shutil.move(str(file_path), str(dest))
        logging.info(f"Moved duplicate to backup: {file_path} -> {dest}")
        return True
    except Exception as e:
        logging.error(f"Failed to backup {file_path}: {e}")
        return False


# ── Helpers ───────────────────────────────────────────────────────────────────

def format_size(bytes_val):
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if bytes_val < 1024:
            return f"{bytes_val:.1f} {unit}"
        bytes_val /= 1024
    return f"{bytes_val:.1f} PB"


# ── Menu functions ────────────────────────────────────────────────────────────

def menu_set_token(config):
    print("\n─────────────────────────────────────")
    print("  SET TMDB API TOKEN")
    print("─────────────────────────────────────")
    print("  Get a free token at: themoviedb.org → Settings → API")
    print(f"  Current token: {'set ✓' if config.get('tmdb_token') else 'not set'}\n")
    token = input("  Paste your TMDB Read Access Token (or press Enter to skip): ").strip()
    if token:
        config['tmdb_token'] = token
        save_config(config)
        print("  ✓ Token saved securely.")
    else:
        print("  No changes made.")


def menu_scan():
    print("\n─────────────────────────────────────")
    print("  SCAN FOR DUPLICATES")
    print("─────────────────────────────────────")
    raw = input("  Enter directory to scan (e.g. C:/Users/you/Movies): ").strip()
    if not raw:
        print("  No directory entered.")
        return None

    directory = resolve_safe_path(raw)
    if not directory:
        return None

    print(f"\n  Scanning '{directory}' for video files...")
    files = scan_directory(directory)
    if not files:
        print("  No video files found.")
        return None

    print(f"  Found {len(files)} video files.")
    duplicates = find_duplicates(files)

    if not duplicates:
        print("\n  ✓ No duplicates found! Your library is clean.")
        return None

    total_wasted = 0
    print(f"\n  Found {len(duplicates)} duplicate group(s):\n")
    for i, (hash_val, paths) in enumerate(duplicates.items(), 1):
        sizes = [p.stat().st_size for p in paths if p.exists()]
        wasted = sum(sizes[1:])
        total_wasted += wasted
        print(f"  Group {i}: {len(paths)} copies  (wasting {format_size(wasted)})")
        for j, p in enumerate(paths):
            tag = "  KEEP  " if j == 0 else "  DUP   "
            size = format_size(p.stat().st_size) if p.exists() else "?"
            print(f"    [{tag}] {p}  ({size})")

    print(f"\n  Total space wasted by duplicates: {format_size(total_wasted)}")
    return duplicates


def menu_delete(duplicates):
    if not duplicates:
        print("\n  No duplicates loaded. Run a scan first (option 1).")
        return

    print("\n─────────────────────────────────────")
    print("  REMOVE DUPLICATES")
    print("─────────────────────────────────────")
    print(f"  {len(duplicates)} duplicate group(s) ready to process.")
    print(f"  Duplicates will be MOVED to: {BACKUP_DIR}")
    print("  (The first file in each group will be kept.)\n")

    confirm = input("  Type YES to proceed: ").strip().upper()
    if confirm != 'YES':
        print("  Cancelled.")
        return

    moved = 0
    failed = 0
    for hash_val, paths in duplicates.items():
        for dup in paths[1:]:
            if backup_and_delete(dup):
                moved += 1
            else:
                failed += 1

    print(f"\n  ✓ Done.  Moved: {moved}  |  Failed: {failed}")
    print(f"  Backups stored in: {BACKUP_DIR}")
    if failed:
        print(f"  Check log for errors: {LOG_FILE}")


def menu_organize(config):
    print("\n─────────────────────────────────────")
    print("  ORGANIZE MOVIE LIBRARY")
    print("─────────────────────────────────────")
    token = config.get('tmdb_token')
    if not token:
        print("  ⚠ No TMDB token set. Movies will be organized by parsed filename only.")
        print("  (Set a token in option 5 for better results.)\n")

    src_raw = input("  Source directory (where your movies are): ").strip()
    if not src_raw:
        print("  No directory entered.")
        return

    dest_raw = input("  Destination directory (where to organize them): ").strip()
    if not dest_raw:
        print("  No directory entered.")
        return

    src = resolve_safe_path(src_raw)
    if not src:
        return

    # Destination may not exist yet — resolve parent and recreate safely
    dest = Path(dest_raw).expanduser().absolute()
    try:
        dest.mkdir(parents=True, exist_ok=True)
        dest = dest.resolve()
    except Exception as e:
        print(f"  [!] Could not create destination: {e}")
        return

    # Prevent organizing a folder into itself or a parent
    try:
        src.relative_to(dest)
        print("  [!] Source cannot be inside the destination directory.")
        return
    except ValueError:
        pass

    files = scan_directory(src)
    if not files:
        print("  No video files found.")
        return

    print(f"\n  Found {len(files)} video files.")
    print(f"  Source:      {src}")
    print(f"  Destination: {dest}")
    confirm = input("\n  Files will be MOVED into organized folders. Type YES to proceed: ").strip().upper()
    if confirm != 'YES':
        print("  Cancelled.")
        return

    organize_movies(files, dest, token)


def menu_view_backup():
    print("\n─────────────────────────────────────")
    print("  BACKUP FOLDER CONTENTS")
    print("─────────────────────────────────────")
    if not BACKUP_DIR.exists() or not any(BACKUP_DIR.iterdir()):
        print("  Backup folder is empty.")
        return

    files = list(BACKUP_DIR.iterdir())
    total = sum(f.stat().st_size for f in files if f.is_file())
    print(f"  Location: {BACKUP_DIR}")
    print(f"  Files: {len(files)}  |  Total size: {format_size(total)}\n")
    for f in sorted(files):
        print(f"    {f.name}  ({format_size(f.stat().st_size)})")


def main():
    config = load_config()
    print("\n╔══════════════════════════════════════╗")
    print("║     MOVIE DUPLICATE SCANNER  v1.1   ║")
    print("╚══════════════════════════════════════╝")
    if not config.get('tmdb_token'):
        print("\n  Tip: Set a TMDB API token (option 5) to enable movie metadata lookup.")

    duplicates = None

    while True:
        print("\n  1. Scan a directory for duplicates")
        print("  2. Remove duplicates (backup first)")
        print("  3. Organize movie library by title/year")
        print("  4. View backup folder")
        print("  5. Set TMDB API token")
        print("  6. Exit")
        print()
        choice = input("  Choose an option (1-6): ").strip()

        if choice == '1':
            duplicates = menu_scan()
        elif choice == '2':
            menu_delete(duplicates)
        elif choice == '3':
            menu_organize(config)
        elif choice == '4':
            menu_view_backup()
        elif choice == '5':
            menu_set_token(config)
        elif choice == '6':
            print("\n  Goodbye!\n")
            break
        else:
            print("  Invalid option. Please enter 1 through 6.")


if __name__ == '__main__':
    main()
