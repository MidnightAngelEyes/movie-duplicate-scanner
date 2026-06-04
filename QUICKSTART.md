# Movie Duplicate Scanner — Quick Start

## Setup (one time only)

```bash
cd /c/Users/jcmor/movie-duplicate-scanner
python -m venv venv
source venv/Scripts/activate
pip install -r requirements.txt
```

## Run the program

```bash
cd /c/Users/jcmor/movie-duplicate-scanner
source venv/Scripts/activate
python scanner.py
```

## First time: set your TMDB token (option 5)

1. Create a free account at themoviedb.org
2. Go to Settings → API → Create → Developer
3. Copy your **API Read Access Token** (the long one)
4. In the app, choose option 5 and paste it in

The token is saved locally so you only do this once.

## What each option does

1. **Scan for duplicates** — scans a folder for video files, groups exact duplicates using SHA-256 hashing, and shows what's wasting space
2. **Remove duplicates** — moves duplicates to `~/MovieDuplicateBackup` (keeps the first copy of each)
3. **Organize library** — moves your movies into `Destination/Year/Title/` folders using TMDB metadata
4. **View backup** — shows what's been moved to backup
5. **Set TMDB token** — connect to The Movie Database for accurate title/year lookup

## Supported formats

MP4, MKV, AVI, MOV, WMV, M4V, MPG, MPEG, FLV, TS, VOB, DIVX, WEBM

## Restore a file

Backups live at: `C:\Users\jcmor\MovieDuplicateBackup\`

## Log file

`C:\Users\jcmor\movie_scanner.log`
