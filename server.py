#!/usr/bin/env python3
"""Amapiano Music Library v2 - genre tagging, Spotify lookup, Serato crates, playlists."""

import json
import os
import re
import hashlib
import struct
import time
from pathlib import Path

from flask import Flask, request, jsonify, send_from_directory, send_file
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

MOBILE_DIR = Path(__file__).parent.parent / "amapiano-iphone"
SERATO_DIR = Path.home() / "Music" / "_Serato_" / "Subcrates"
SERATO_BACKUP = Path.home() / "Music" / "_Serato_Backup" / "Subcrates"

# Spotify credentials from spotdl
SPOTIFY_ID = "50509b8c6608434fbb2c86dd8cfdaf90"
SPOTIFY_SECRET = "36bb45f66ab447fe971a17cafb48dbeb"
_spotify_token = {"token": None, "expires": 0}


def get_spotify_token():
    import time
    if _spotify_token["token"] and time.time() < _spotify_token["expires"]:
        return _spotify_token["token"]
    import urllib.request
    import base64
    auth = base64.b64encode(f"{SPOTIFY_ID}:{SPOTIFY_SECRET}".encode()).decode()
    req = urllib.request.Request(
        "https://accounts.spotify.com/api/token",
        data=b"grant_type=client_credentials",
        headers={"Authorization": f"Basic {auth}", "Content-Type": "application/x-www-form-urlencoded"},
    )
    resp = json.loads(urllib.request.urlopen(req, timeout=10).read())
    _spotify_token["token"] = resp["access_token"]
    _spotify_token["expires"] = time.time() + resp["expires_in"] - 60
    return _spotify_token["token"]


def spotify_search(title, artist):
    """Search Spotify for track and return genre from artist."""
    import urllib.request, urllib.parse
    token = get_spotify_token()
    q = urllib.parse.quote(f"track:{title} artist:{artist}")
    req = urllib.request.Request(
        f"https://api.spotify.com/v1/search?q={q}&type=track&limit=1",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        data = json.loads(urllib.request.urlopen(req, timeout=10).read())
        tracks = data.get("tracks", {}).get("items", [])
        if not tracks:
            return None
        track = tracks[0]
        artist_id = track["artists"][0]["id"] if track["artists"] else None
        if not artist_id:
            return None
        # Get artist genres
        req2 = urllib.request.Request(
            f"https://api.spotify.com/v1/artists/{artist_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        artist_data = json.loads(urllib.request.urlopen(req2, timeout=10).read())
        genres = artist_data.get("genres", [])
        return genres[0].title() if genres else None
    except Exception:
        return None


def load_db():
    if DB_FILE.exists():
        return json.loads(DB_FILE.read_text())
    return {"tracks": {}, "playlists": {}}


def save_db(db):
    DB_FILE.write_text(json.dumps(db, indent=2, ensure_ascii=False))


def file_id(path):
    return hashlib.md5(path.encode()).hexdigest()[:12]


def classify_title(title):
    """Detect video/lyrics/visualizer markers in title."""
    tl = title.lower()
    flags = []
    if "official" in tl and ("video" in tl or "music video" in tl):
        flags.append("video")
    if "lyric" in tl:
        flags.append("lyrics")
    if "visualizer" in tl:
        flags.append("visualizer")
    return flags


def extract_cover(filepath, fid):
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

        if title == name and " - " in name:
            parts = name.split(" - ", 1)
            artist = parts[0].strip()
            title = parts[1].strip()

        cover = extract_cover(filepath, fid)
        flags = classify_title(title)

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
            "flags": flags,
        }
    except Exception:
        return None


# ── Serato crate parser ──
def parse_serato_crate(crate_path):
    """Parse a Serato .crate file and return list of file paths."""
    try:
        data = crate_path.read_bytes()
        paths = []
        # Find ptrk entries (UTF-16BE encoded file paths after 'ptrk' + 4-byte length)
        i = 0
        while i < len(data):
            idx = data.find(b"ptrk", i)
            if idx == -1:
                break
            length = struct.unpack(">I", data[idx + 4 : idx + 8])[0]
            path_bytes = data[idx + 8 : idx + 8 + length]
            try:
                path = path_bytes.decode("utf-16-be").strip("\x00")
                if path.startswith("/"):
                    paths.append(path)
                else:
                    paths.append("/" + path)
            except Exception:
                pass
            i = idx + 8 + length
        return paths
    except Exception:
        return []


# ── Routes ──

@app.route("/")
def index():
    return send_from_directory("public", "index.html")


@app.route("/downloads")
def downloads_page():
    return """<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Amapiano Downloads</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#0a0a0a;color:#f0f0f0;font-family:-apple-system,BlinkMacSystemFont,sans-serif;padding:24px;max-width:800px;margin:0 auto}
h1{font-size:24px;font-weight:700;color:#ff5500;margin-bottom:20px}
.input-group{display:flex;flex-direction:column;gap:10px;margin-bottom:24px;padding:16px;background:#111;border-radius:12px;border:1px solid #222}
input{background:#1a1a1a;border:1px solid #333;border-radius:8px;padding:10px 14px;color:#eee;font-size:14px;outline:none;width:100%}
input:focus{border-color:#ff5500}
.btn{background:#ff5500;color:#fff;border:none;border-radius:8px;padding:10px 20px;font-size:14px;font-weight:600;cursor:pointer}
.btn:hover{background:#e64d00}
.btn:disabled{background:#333;cursor:not-allowed;color:#666}
.dl-item{padding:14px;background:#111;border-radius:8px;border:1px solid #222;margin-bottom:8px;display:flex;align-items:center;gap:12px}
.dl-status{font-size:11px;padding:3px 8px;border-radius:6px;font-weight:600;flex-shrink:0}
.dl-status.queued{background:#333;color:#888}
.dl-status.downloading{background:#331a00;color:#ff5500}
.dl-status.done{background:#0a2a0a;color:#4c4}
.dl-status.error{background:#2a0a0a;color:#f44}
.dl-info{flex:1;min-width:0}
.dl-name{font-weight:600;font-size:14px}
.dl-url{font-size:11px;color:#555;font-family:monospace;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.dl-detail{font-size:11px;margin-top:3px}
.dl-detail.ok{color:#4c4}
.dl-detail.err{color:#f44}
.empty{color:#444;text-align:center;padding:40px;font-size:14px}
.resolving{font-size:12px;color:#ff5500}
</style></head><body>
<h1>Amapiano Downloads</h1>
<div class="input-group">
  <input id="urlInput" placeholder="Paste Spotify, SoundCloud, or YouTube URL" autocomplete="off">
  <div style="display:flex;gap:10px;align-items:center">
    <input id="nameInput" placeholder="Playlist name (auto-detected)">
    <span class="resolving" id="resolving" style="display:none">Fetching...</span>
  </div>
  <button class="btn" id="dlBtn" onclick="startDl()">Download</button>
</div>
<div id="list"><div class="empty">No downloads yet</div></div>
<script>
const API='/api';
let polling;
document.getElementById('urlInput').addEventListener('input',async e=>{
  const url=e.target.value.trim();
  if(url.includes('spotify.com')){
    document.getElementById('resolving').style.display='inline';
    try{
      const r=await fetch(API+'/resolve-name',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url})});
      const d=await r.json();
      if(d.name&&!document.getElementById('nameInput').value)document.getElementById('nameInput').value=d.name;
    }catch(e){}
    document.getElementById('resolving').style.display='none';
  }
});
async function startDl(){
  const url=document.getElementById('urlInput').value.trim();
  if(!url)return;
  const name=document.getElementById('nameInput').value.trim()||'';
  document.getElementById('dlBtn').disabled=true;
  await fetch(API+'/download',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url,name})});
  document.getElementById('urlInput').value='';
  document.getElementById('nameInput').value='';
  document.getElementById('dlBtn').disabled=false;
  poll();
}
async function poll(){
  try{
    const r=await fetch(API+'/downloads');
    const d=await r.json();
    const list=document.getElementById('list');
    if(!d.downloads.length){list.innerHTML='<div class="empty">No downloads yet</div>';return;}
    list.innerHTML=d.downloads.map(dl=>`<div class="dl-item">
      <span class="dl-status ${dl.status}">${dl.status}</span>
      <div class="dl-info">
        <div class="dl-name">${esc(dl.name)}</div>
        <div class="dl-url">${esc(dl.url)}</div>
        ${dl.status==='done'&&dl.new_tracks?`<div class="dl-detail ok">${dl.new_tracks} tracks added</div>`:''}
        ${dl.error?`<div class="dl-detail err">${esc(dl.error).slice(0,200)}</div>`:''}
      </div>
    </div>`).join('');
  }catch(e){}
}
function esc(s){return s?s.replace(/&/g,'&amp;').replace(/</g,'&lt;'):''}
poll();
polling=setInterval(poll,3000);
</script></body></html>"""


@app.route("/mobile")
def mobile():
    return send_from_directory(str(MOBILE_DIR), "index.html")


@app.route("/mobile/<path:filename>")
def mobile_static(filename):
    return send_from_directory(str(MOBILE_DIR), filename)


@app.route("/api/scan", methods=["POST"])
def scan_library():
    db = load_db()
    found = new = 0
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
                else:
                    # Update flags on existing
                    t = db["tracks"][fid]
                    if "flags" not in t:
                        t["flags"] = classify_title(t.get("title", ""))
                found += 1
    save_db(db)
    return jsonify({"found": found, "new": new, "total": len(db["tracks"])})


@app.route("/api/tracks")
def get_tracks():
    db = load_db()
    tracks = list(db["tracks"].values())
    q = request.args.get("q", "").lower()
    genre = request.args.get("genre", "")
    tag = request.args.get("tag", "")
    flag = request.args.get("flag", "")

    if q:
        tracks = [t for t in tracks if q in t["title"].lower() or q in t["artist"].lower()]
    if genre == "__none__":
        tracks = [t for t in tracks if not t.get("genre")]
    elif genre:
        tracks = [t for t in tracks if t.get("genre", "").lower() == genre.lower()]
    if tag:
        tracks = [t for t in tracks if tag in t.get("custom_tags", [])]
    if flag:
        tracks = [t for t in tracks if flag in t.get("flags", [])]

    tracks.sort(key=lambda t: (t["artist"].lower(), t["title"].lower()))
    return jsonify({"tracks": tracks, "total": len(tracks)})


@app.route("/api/tracks/<track_id>", methods=["PATCH"])
def update_track(track_id):
    db = load_db()
    if track_id not in db["tracks"]:
        return jsonify({"error": "Track not found"}), 404

    data = request.json
    track = db["tracks"][track_id]

    if "genre" in data:
        track["genre"] = data["genre"]
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


@app.route("/api/tracks/batch-genre", methods=["POST"])
def batch_genre():
    """Auto-tag genres via Spotify for tracks missing genre."""
    db = load_db()
    data = request.json
    track_ids = data.get("ids", [])
    updated = 0

    for tid in track_ids:
        if tid not in db["tracks"]:
            continue
        t = db["tracks"][tid]
        if t.get("genre"):
            continue
        genre = spotify_search(t["title"], t["artist"])
        if genre:
            t["genre"] = genre
            try:
                m = mutagen.File(t["path"], easy=True)
                if m is not None:
                    m["genre"] = genre
                    m.save()
            except Exception:
                pass
            updated += 1

    save_db(db)
    return jsonify({"updated": updated, "total": len(track_ids)})


@app.route("/api/spotify-genre", methods=["POST"])
def spotify_genre_lookup():
    """Lookup genre for a single track via Spotify."""
    data = request.json
    genre = spotify_search(data.get("title", ""), data.get("artist", ""))
    return jsonify({"genre": genre})


@app.route("/api/tags")
def get_tags():
    db = load_db()
    genres = set()
    custom_tags = set()
    for t in db["tracks"].values():
        if t.get("genre"):
            genres.add(t["genre"])
        for ct in t.get("custom_tags", []):
            custom_tags.add(ct)
    return jsonify({"genres": sorted(genres), "custom_tags": sorted(custom_tags)})


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
    video_count = 0
    lyrics_count = 0
    for t in tracks:
        g = t.get("genre", "")
        if g:
            genres[g] = genres.get(g, 0) + 1
        else:
            no_genre += 1
        flags = t.get("flags", [])
        if "video" in flags:
            video_count += 1
        if "lyrics" in flags or "visualizer" in flags:
            lyrics_count += 1

    artists = {}
    for t in tracks:
        a = t.get("artist", "Unknown")
        artists[a] = artists.get(a, 0) + 1

    return jsonify({
        "total": len(tracks),
        "no_genre": no_genre,
        "video_count": video_count,
        "lyrics_count": lyrics_count,
        "genres": dict(sorted(genres.items(), key=lambda x: -x[1])[:20]),
        "top_artists": dict(sorted(artists.items(), key=lambda x: -x[1])[:20]),
    })


# ── Playlists ──

@app.route("/api/playlists")
def list_playlists():
    db = load_db()
    playlists = []
    for pid, pl in db.get("playlists", {}).items():
        playlists.append({"id": pid, "name": pl["name"], "count": len(pl.get("track_ids", []))})
    return jsonify({"playlists": playlists})


@app.route("/api/playlists", methods=["POST"])
def create_playlist():
    db = load_db()
    data = request.json
    pid = hashlib.md5(data["name"].encode()).hexdigest()[:10]
    if "playlists" not in db:
        db["playlists"] = {}
    db["playlists"][pid] = {"name": data["name"], "track_ids": data.get("track_ids", [])}
    save_db(db)
    return jsonify({"id": pid, "name": data["name"]})


@app.route("/api/playlists/<pid>")
def get_playlist(pid):
    db = load_db()
    pl = db.get("playlists", {}).get(pid)
    if not pl:
        return jsonify({"error": "Not found"}), 404
    tracks = [db["tracks"][tid] for tid in pl.get("track_ids", []) if tid in db["tracks"]]
    return jsonify({"id": pid, "name": pl["name"], "tracks": tracks})


@app.route("/api/playlists/<pid>", methods=["PATCH"])
def update_playlist(pid):
    db = load_db()
    if pid not in db.get("playlists", {}):
        return jsonify({"error": "Not found"}), 404
    data = request.json
    if "name" in data:
        db["playlists"][pid]["name"] = data["name"]
    if "track_ids" in data:
        db["playlists"][pid]["track_ids"] = data["track_ids"]
    if "add_track" in data:
        tid = data["add_track"]
        if tid not in db["playlists"][pid]["track_ids"]:
            db["playlists"][pid]["track_ids"].append(tid)
    save_db(db)
    return jsonify(db["playlists"][pid])


@app.route("/api/playlists/<pid>", methods=["DELETE"])
def delete_playlist(pid):
    db = load_db()
    db.get("playlists", {}).pop(pid, None)
    save_db(db)
    return jsonify({"deleted": True})


# ── Serato crates ──

@app.route("/api/serato/crates")
def list_serato_crates():
    crates = []
    for d in [SERATO_DIR, SERATO_BACKUP]:
        if not d.exists():
            continue
        for f in sorted(d.glob("*.crate")):
            name = f.stem.replace("%%", " > ")
            paths = parse_serato_crate(f)
            crates.append({"name": name, "path": str(f), "count": len(paths), "source": "backup" if "Backup" in str(d) else "live"})
    return jsonify({"crates": crates})


@app.route("/api/serato/crates/import", methods=["POST"])
def import_serato_crate():
    """Import a Serato crate as a playlist."""
    data = request.json
    crate_path = Path(data["path"])
    if not crate_path.exists():
        return jsonify({"error": "Crate not found"}), 404

    file_paths = parse_serato_crate(crate_path)
    db = load_db()

    track_ids = []
    for fp in file_paths:
        fid = file_id(fp)
        if fid in db["tracks"]:
            track_ids.append(fid)
        elif os.path.exists(fp):
            track = scan_track(fp)
            if track:
                db["tracks"][fid] = track
                track_ids.append(fid)

    name = data.get("name", crate_path.stem.replace("%%", " > "))
    pid = hashlib.md5(name.encode()).hexdigest()[:10]
    if "playlists" not in db:
        db["playlists"] = {}
    db["playlists"][pid] = {"name": f"[Serato] {name}", "track_ids": track_ids}
    save_db(db)

    return jsonify({"id": pid, "name": f"[Serato] {name}", "matched": len(track_ids), "total": len(file_paths)})


@app.route("/api/serato/export", methods=["POST"])
def export_to_serato():
    """Export a playlist as a Serato .crate file."""
    data = request.json
    pid = data.get("playlist_id")
    db = load_db()
    pl = db.get("playlists", {}).get(pid)
    if not pl:
        return jsonify({"error": "Playlist not found"}), 404

    tracks = [db["tracks"][tid] for tid in pl.get("track_ids", []) if tid in db["tracks"]]
    crate_name = pl["name"].replace("[Serato] ", "").replace(" > ", "%%")

    # Build Serato crate binary
    buf = bytearray()
    # Version header
    ver = "1.0/Serato ScratchLive Crate".encode("utf-16-be")
    buf += b"vrsn" + struct.pack(">I", len(ver)) + ver

    for t in tracks:
        path = t["path"]
        if path.startswith("/"):
            path = path[1:]  # Serato paths don't have leading /
        path_bytes = path.encode("utf-16-be")
        buf += b"otrk" + struct.pack(">I", len(path_bytes) + 8)
        buf += b"ptrk" + struct.pack(">I", len(path_bytes)) + path_bytes

    dest = SERATO_DIR / f"{crate_name}.crate"
    dest.write_bytes(bytes(buf))

    return jsonify({"exported": True, "path": str(dest), "tracks": len(tracks)})


# ── Direct Serato crate management ──

def _write_crate(name, track_paths):
    """Write a Serato .crate file from a list of file paths."""
    buf = bytearray()
    ver = "1.0/Serato ScratchLive Crate".encode("utf-16-be")
    buf += b"vrsn" + struct.pack(">I", len(ver)) + ver
    for path in track_paths:
        p = path[1:] if path.startswith("/") else path
        path_bytes = p.encode("utf-16-be")
        buf += b"otrk" + struct.pack(">I", len(path_bytes) + 8)
        buf += b"ptrk" + struct.pack(">I", len(path_bytes)) + path_bytes
    crate_name = name.replace(" > ", "%%")
    dest = SERATO_DIR / f"{crate_name}.crate"
    dest.write_bytes(bytes(buf))
    return str(dest)


@app.route("/api/serato/crates/<path:crate_name>/tracks", methods=["GET"])
def get_crate_tracks(crate_name):
    """Get tracks in a Serato crate with full metadata."""
    real_name = crate_name.replace(" > ", "%%")
    crate_path = SERATO_DIR / f"{real_name}.crate"
    if not crate_path.exists():
        return jsonify({"error": "Crate not found"}), 404
    file_paths = parse_serato_crate(crate_path)
    db = load_db()
    tracks = []
    for fp in file_paths:
        fid = file_id(fp)
        if fid in db["tracks"]:
            tracks.append(db["tracks"][fid])
        elif os.path.exists(fp):
            track = scan_track(fp)
            if track:
                db["tracks"][fid] = track
                tracks.append(track)
    save_db(db)
    return jsonify({"name": crate_name, "tracks": tracks})


@app.route("/api/serato/crates/<path:crate_name>/add", methods=["POST"])
def add_to_crate(crate_name):
    """Add a track to a Serato crate by track ID."""
    data = request.json
    tid = data.get("track_id")
    db = load_db()
    if tid not in db["tracks"]:
        return jsonify({"error": "Track not found"}), 404
    track_path = db["tracks"][tid]["path"]

    real_name = crate_name.replace(" > ", "%%")
    crate_path = SERATO_DIR / f"{real_name}.crate"
    existing = parse_serato_crate(crate_path) if crate_path.exists() else []

    if track_path not in existing:
        existing.append(track_path)
    _write_crate(crate_name, existing)
    return jsonify({"added": True, "tracks": len(existing)})


@app.route("/api/serato/crates/<path:crate_name>/remove", methods=["POST"])
def remove_from_crate(crate_name):
    """Remove a track from a Serato crate."""
    data = request.json
    tid = data.get("track_id")
    db = load_db()
    if tid not in db["tracks"]:
        return jsonify({"error": "Track not found"}), 404
    track_path = db["tracks"][tid]["path"]

    real_name = crate_name.replace(" > ", "%%")
    crate_path = SERATO_DIR / f"{real_name}.crate"
    existing = parse_serato_crate(crate_path) if crate_path.exists() else []
    existing = [p for p in existing if p != track_path]
    _write_crate(crate_name, existing)
    return jsonify({"removed": True, "tracks": len(existing)})


@app.route("/api/serato/crates/<path:crate_name>/reorder", methods=["POST"])
def reorder_crate(crate_name):
    """Reorder tracks in a crate. Expects {"track_ids": [...]} in new order."""
    data = request.json
    track_ids = data.get("track_ids", [])
    db = load_db()
    paths = []
    for tid in track_ids:
        if tid in db["tracks"]:
            paths.append(db["tracks"][tid]["path"])
    _write_crate(crate_name, paths)
    return jsonify({"reordered": True, "tracks": len(paths)})


@app.route("/api/serato/crates/<path:crate_name>/rename", methods=["POST"])
def rename_crate(crate_name):
    """Rename a Serato crate."""
    data = request.json
    new_name = data.get("name", "")
    if not new_name:
        return jsonify({"error": "Name required"}), 400

    real_name = crate_name.replace(" > ", "%%")
    crate_path = SERATO_DIR / f"{real_name}.crate"
    existing = parse_serato_crate(crate_path) if crate_path.exists() else []

    # Write new, delete old
    new_path = _write_crate(new_name, existing)
    if crate_path.exists() and str(crate_path) != new_path:
        crate_path.unlink()
    return jsonify({"renamed": True, "old": crate_name, "new": new_name})


@app.route("/api/serato/crates/create", methods=["POST"])
def create_crate():
    """Create a new empty Serato crate."""
    data = request.json
    name = data.get("name", "")
    if not name:
        return jsonify({"error": "Name required"}), 400
    _write_crate(name, [])
    return jsonify({"created": True, "name": name})


# ── Downloads via spotdl ──

import subprocess
import threading

_downloads = {}  # id -> {status, url, name, tracks: [], error}
_download_lock = threading.Lock()


def _run_download(download_id, url, playlist_name):
    """Run download in background thread. Uses yt-dlp for most, spotdl for Spotify."""
    safe_name = re.sub(r'[^\w\s\-]', '', playlist_name).strip() or "Downloads"
    output_dir = Path.home() / "Music" / "yt-dlp" / safe_name
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        with _download_lock:
            _downloads[download_id]["status"] = "downloading"

        if "spotify.com" in url:
            # Use spotdl for Spotify — retry on rate limit
            result = subprocess.run(
                ["/Users/djsly/.local/bin/spotdl", "download", url,
                 "--output", str(output_dir / "{artist} - {title}.{output-ext}"),
                 "--max-retries", "10"],
                capture_output=True, text=True, timeout=1200
            )
        else:
            # Use yt-dlp for SoundCloud, YouTube, etc
            result = subprocess.run(
                ["yt-dlp", "-x", "--audio-format", "mp3",
                 "--audio-quality", "0",
                 "-o", str(output_dir / "%(artist)s - %(title)s.%(ext)s"),
                 "--no-playlist" if "/track" in url else "--yes-playlist",
                 url],
                capture_output=True, text=True, timeout=900
            )

        # Scan new files in this playlist folder
        db = load_db()
        new_tracks = []
        for root, dirs, files in os.walk(str(output_dir)):
            for f in files:
                if Path(f).suffix.lower() not in AUDIO_EXTS:
                    continue
                filepath = os.path.join(root, f)
                fid = file_id(filepath)
                if fid not in db["tracks"]:
                    track = scan_track(filepath)
                    if track:
                        db["tracks"][fid] = track
                        new_tracks.append(fid)

        # Create playlist from downloaded tracks if we got any
        if new_tracks and playlist_name:
            pid = hashlib.md5(playlist_name.encode()).hexdigest()[:10]
            if "playlists" not in db:
                db["playlists"] = {}
            if pid in db["playlists"]:
                # Add to existing
                existing = db["playlists"][pid].get("track_ids", [])
                for tid in new_tracks:
                    if tid not in existing:
                        existing.append(tid)
                db["playlists"][pid]["track_ids"] = existing
            else:
                db["playlists"][pid] = {"name": playlist_name, "track_ids": new_tracks}
            # Auto-export to Serato
            tracks = [db["tracks"][tid] for tid in db["playlists"][pid]["track_ids"] if tid in db["tracks"]]
            crate_name = playlist_name.replace(" > ", "%%")
            _write_crate(playlist_name, [t["path"] for t in tracks])

        save_db(db)

        with _download_lock:
            _downloads[download_id]["status"] = "done"
            _downloads[download_id]["new_tracks"] = len(new_tracks)
            if result.returncode != 0:
                _downloads[download_id]["error"] = result.stderr[:500]
    except Exception as e:
        with _download_lock:
            _downloads[download_id]["status"] = "error"
            _downloads[download_id]["error"] = str(e)


def _fetch_playlist_name(url):
    """Get playlist/album name from any URL using yt-dlp or Spotify API."""
    # Try yt-dlp first — works for SoundCloud, YouTube, and most URLs
    try:
        result = subprocess.run(
            ["yt-dlp", "--flat-playlist", "--print", "playlist_title", "-I", "1", url],
            capture_output=True, text=True, timeout=15
        )
        name = result.stdout.strip().split("\n")[0].strip()
        if name and name != "NA":
            return name
    except Exception:
        pass

    # Try Spotify API for Spotify URLs
    if "spotify.com" in url:
        try:
            import urllib.parse
            path = urllib.parse.urlparse(url).path
            parts = path.strip("/").split("/")
            if len(parts) >= 2 and parts[0] in ("playlist", "album"):
                token = get_spotify_token()
                req = urllib.request.Request(
                    f"https://api.spotify.com/v1/{parts[0]}s/{parts[1].split('?')[0]}",
                    headers={"Authorization": f"Bearer {token}"},
                )
                data = json.loads(urllib.request.urlopen(req, timeout=10).read())
                return data.get("name", "")
        except Exception:
            pass

    return ""


@app.route("/api/resolve-name", methods=["POST"])
def resolve_name():
    """Resolve playlist/album name from URL."""
    data = request.json
    url = data.get("url", "").strip()
    name = _fetch_playlist_name(url)
    return jsonify({"name": name})


@app.route("/api/download", methods=["POST"])
def start_download():
    """Start a spotdl download. Accepts {url, name}."""
    data = request.json
    url = data.get("url", "").strip()
    name = data.get("name", "").strip()

    # Auto-fetch playlist name if not provided
    if not name:
        name = _fetch_playlist_name(url)
    if not name:
        name = "Downloads"

    if not url:
        return jsonify({"error": "URL required"}), 400

    download_id = hashlib.md5(f"{url}{time.time()}".encode()).hexdigest()[:10]
    with _download_lock:
        _downloads[download_id] = {
            "id": download_id,
            "status": "queued",
            "url": url,
            "name": name,
            "new_tracks": 0,
            "error": None,
        }

    thread = threading.Thread(target=_run_download, args=(download_id, url, name))
    thread.daemon = True
    thread.start()

    return jsonify({"id": download_id, "status": "queued", "name": name})


@app.route("/api/download/<download_id>")
def download_status(download_id):
    with _download_lock:
        dl = _downloads.get(download_id)
    if not dl:
        return jsonify({"error": "Not found"}), 404
    return jsonify(dl)


@app.route("/api/downloads")
def list_downloads():
    with _download_lock:
        return jsonify({"downloads": list(_downloads.values())})


if __name__ == "__main__":
    print("Amapiano Music Library v2 at http://localhost:8766")
    app.run(host="0.0.0.0", port=8766, debug=False)
