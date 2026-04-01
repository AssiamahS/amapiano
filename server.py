#!/usr/bin/env python3
"""Amapiano Music Library - Apple Music-style browser with genre tagging."""

import json
import os
import base64
import hashlib
from pathlib import Path

from flask import Flask, request, jsonify, send_from_directory, send_file, Response
from flask_cors import CORS
import mutagen
from mutagen.id3 import ID3
from mutagen.mp4 import MP4

app = Flask(__name__, static_folder="public")
CORS(app)

MUSIC_DIRS = [
    Path.home() / "Music" / "yt-dlp",
    Path.home() / "Music" / "Music" / "Media.localized" / "Music",
    Path.home() / "Downloads",
]
AUDIO_EXTS = {".mp3", ".m4a", ".wav", ".flac", ".aac", ".opus", ".ogg"}
DB_FILE = Path(__file__).parent / "library.json"
COVERS_DIR = Path(__file__).parent / "covers"
COVERS_DIR.mkdir(exist_ok=True)


def load_db():
    if DB_FILE.exists():
        return json.loads(DB_FILE.read_text())
    return {"tracks": {}, "tags": []}


def save_db(db):
    DB_FILE.write_text(json.dumps(db, indent=2, ensure_ascii=False))


def file_id(path):
    return hashlib.md5(path.encode()).hexdigest()[:12]


def extract_cover(filepath, fid):
    """Extract embedded cover art, save as JPEG, return relative URL."""
    cover_path = COVERS_DIR / f"{fid}.jpg"
    if cover_path.exists():
        return f"/api/cover/{fid}"

    try:
        ext = Path(filepath).suffix.lower()
        if ext == ".mp3":
            tags = ID3(filepath)
            for key in tags:
                if key.startswith("APIC"):
                    cover_path.write_bytes(tags[key].data)
                    return f"/api/cover/{fid}"
        elif ext in (".m4a", ".mp4", ".aac"):
            mp4 = MP4(filepath)
            if "covr" in mp4.tags:
                cover_path.write_bytes(bytes(mp4.tags["covr"][0]))
                return f"/api/cover/{fid}"
    except Exception:
        pass
    return None


def scan_track(filepath):
    """Read metadata from a single audio file."""
    fid = file_id(filepath)
    name = Path(filepath).stem
    try:
        m = mutagen.File(filepath, easy=True)
        if m is None:
            return None
        title = (m.get("title") or [name])[0]
        artist = (m.get("artist") or ["Unknown"])[0]
        genre = (m.get("genre") or [""])[0]
        album = (m.get("album") or [""])[0]
        duration = m.info.length if m.info else 0

        # Try to parse artist - title from filename if missing
        if title == name and " - " in name:
            parts = name.split(" - ", 1)
            artist = parts[0].strip()
            title = parts[1].strip()

        cover = extract_cover(filepath, fid)

        return {
            "id": fid,
            "path": filepath,
            "title": title,
            "artist": artist,
            "genre": genre,
            "album": album,
            "duration": round(duration, 1),
            "cover": cover,
            "custom_tags": [],
        }
    except Exception:
        return None


@app.route("/")
def index():
    return send_from_directory("public", "index.html")


@app.route("/api/scan", methods=["POST"])
def scan_library():
    """Scan music directories and build/update the library."""
    db = load_db()
    found = 0
    new = 0

    for music_dir in MUSIC_DIRS:
        if not music_dir.exists():
            continue
        for root, dirs, files in os.walk(str(music_dir)):
            for f in files:
                if Path(f).suffix.lower() not in AUDIO_EXTS:
                    continue
                filepath = os.path.join(root, f)
                fid = file_id(filepath)
                if fid not in db["tracks"]:
                    track = scan_track(filepath)
                    if track:
                        db["tracks"][fid] = track
                        new += 1
                found += 1

    save_db(db)
    return jsonify({"found": found, "new": new, "total": len(db["tracks"])})


@app.route("/api/tracks")
def get_tracks():
    """Return all tracks, optionally filtered."""
    db = load_db()
    tracks = list(db["tracks"].values())

    q = request.args.get("q", "").lower()
    genre = request.args.get("genre", "")
    tag = request.args.get("tag", "")

    if q:
        tracks = [t for t in tracks if q in t["title"].lower() or q in t["artist"].lower()]
    if genre:
        tracks = [t for t in tracks if t.get("genre", "").lower() == genre.lower()]
    if tag:
        tracks = [t for t in tracks if tag in t.get("custom_tags", [])]

    # Sort by artist then title
    tracks.sort(key=lambda t: (t["artist"].lower(), t["title"].lower()))
    return jsonify({"tracks": tracks, "total": len(tracks)})


@app.route("/api/tracks/<track_id>", methods=["PATCH"])
def update_track(track_id):
    """Update track metadata (genre, custom_tags)."""
    db = load_db()
    if track_id not in db["tracks"]:
        return jsonify({"error": "Track not found"}), 404

    data = request.json
    track = db["tracks"][track_id]

    if "genre" in data:
        track["genre"] = data["genre"]
        # Also write to file metadata
        try:
            m = mutagen.File(track["path"], easy=True)
            if m is not None:
                m["genre"] = data["genre"]
                m.save()
        except Exception:
            pass

    if "custom_tags" in data:
        track["custom_tags"] = data["custom_tags"]

    if "title" in data:
        track["title"] = data["title"]
    if "artist" in data:
        track["artist"] = data["artist"]

    save_db(db)
    return jsonify(track)


@app.route("/api/tags")
def get_tags():
    """Return all custom tags and genres in use."""
    db = load_db()
    genres = set()
    custom_tags = set()
    for t in db["tracks"].values():
        if t.get("genre"):
            genres.add(t["genre"])
        for ct in t.get("custom_tags", []):
            custom_tags.add(ct)

    return jsonify({
        "genres": sorted(genres),
        "custom_tags": sorted(custom_tags),
    })


@app.route("/api/cover/<fid>")
def serve_cover(fid):
    cover_path = COVERS_DIR / f"{fid}.jpg"
    if cover_path.exists():
        return send_file(str(cover_path), mimetype="image/jpeg")
    return "", 404


@app.route("/api/audio")
def serve_audio():
    filepath = request.args.get("path", "")
    if not filepath or not os.path.exists(filepath):
        return "Not found", 404
    return send_file(filepath)


@app.route("/api/stats")
def stats():
    db = load_db()
    tracks = list(db["tracks"].values())
    genres = {}
    no_genre = 0
    for t in tracks:
        g = t.get("genre", "")
        if g:
            genres[g] = genres.get(g, 0) + 1
        else:
            no_genre += 1

    artists = {}
    for t in tracks:
        a = t.get("artist", "Unknown")
        artists[a] = artists.get(a, 0) + 1

    return jsonify({
        "total": len(tracks),
        "no_genre": no_genre,
        "genres": dict(sorted(genres.items(), key=lambda x: -x[1])[:20]),
        "top_artists": dict(sorted(artists.items(), key=lambda x: -x[1])[:20]),
    })


if __name__ == "__main__":
    print("Amapiano Music Library at http://localhost:8766")
    app.run(host="0.0.0.0", port=8766, debug=False)
