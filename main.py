from fastapi import FastAPI, Query, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, RedirectResponse
from ytmusicapi.ytmusic import YTMusic
from concurrent.futures import ThreadPoolExecutor, as_completed
import subprocess
import json
import httpx
import time

app = FastAPI(title="YTMusic API", docs_url="/docs")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

yt = YTMusic()
executor = ThreadPoolExecutor(max_workers=100)
stream_cache = {}
cache_expiry = {}

def get_audio_urls(video_id: str) -> list:
    if video_id in stream_cache and cache_expiry.get(video_id, 0) > time.time():
        return stream_cache[video_id]
    try:
        cmd = ["yt-dlp", "-f", "bestaudio", "-j", "--no-warnings", "--no-playlist", "--no-check-certificates", "--extractor-args", "youtube:player_client=android", f"https://music.youtube.com/watch?v={video_id}"]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        if result.returncode == 0:
            data = json.loads(result.stdout)
            formats = []
            for f in data.get("formats", []):
                if f.get("acodec") != "none" and f.get("vcodec") == "none":
                    formats.append({"url": f.get("url"), "format": f.get("ext"), "bitrate": f.get("abr", 0), "codec": f.get("acodec")})
            formats.sort(key=lambda x: x.get("bitrate", 0), reverse=True)
            result_formats = formats[:3]
            if result_formats:
                stream_cache[video_id] = result_formats
                cache_expiry[video_id] = time.time() + 18000
            return result_formats
    except Exception as e:
        print(f"yt-dlp error: {e}")
    return []

def get_best_thumbnail(thumbnails: list) -> str:
    if not thumbnails:
        return None
    best = max(thumbnails, key=lambda x: x.get("width", 0) * x.get("height", 0))
    url = best.get("url", "")
    if "ytimg.com" in url and "/vi/" in url:
        vid_id = url.split("/vi/")[1].split("/")[0]
        return f"https://i.ytimg.com/vi/{vid_id}/maxresdefault.jpg"
    return url

def format_track(track: dict) -> dict:
    thumbnails = track.get("thumbnails", [])
    album = track.get("album", {})
    return {
        "id": track.get("videoId"),
        "title": track.get("title"),
        "artist": ", ".join([a.get("name", "") for a in track.get("artists", [])]) if track.get("artists") else track.get("author", ""),
        "album": album.get("name") if isinstance(album, dict) else album,
        "duration": track.get("duration"),
        "duration_seconds": track.get("duration_seconds"),
        "cover": get_best_thumbnail(thumbnails),
        "isExplicit": track.get("isExplicit", False)
    }

@app.get("/")
def root():
    return {"status": "ok", "service": "YTMusic API"}

@app.get("/search")
def search(query: str = Query(...), filter: str = Query(None), limit: int = Query(20), ignore_spelling: bool = Query(False)):
    try:
        results = yt.search(query, filter=filter, limit=limit, ignore_spelling=ignore_spelling)
        formatted = [format_track(item) if item.get("resultType") in ["song", "video"] else item for item in results]
        return {"success": True, "data": formatted}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/search/suggestions")
def search_suggestions(query: str = Query(...)):
    try:
        return {"success": True, "data": yt.get_search_suggestions(query)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/home")
def get_home(limit: int = Query(6)):
    try:
        return {"success": True, "data": yt.get_home(limit=limit)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/song/{video_id}")
def get_song(video_id: str):
    try:
        futures = {executor.submit(lambda: yt.get_song(video_id)): "song", executor.submit(lambda: get_audio_urls(video_id)): "streams"}
        song, streams = None, []
        for future in as_completed(futures):
            if futures[future] == "song":
                song = future.result()
            else:
                streams = future.result()
        details = song.get("videoDetails", {})
        thumbnails = details.get("thumbnail", {}).get("thumbnails", [])
        return {"success": True, "data": {
            "id": video_id, "title": details.get("title"), "artist": details.get("author"),
            "duration": int(details.get("lengthSeconds", 0)), "cover": get_best_thumbnail(thumbnails),
            "views": details.get("viewCount"), "stream_url": f"/stream/{video_id}", "streams": streams
        }}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/stream/{video_id}")
async def stream_audio(video_id: str, quality: str = Query("best")):
    try:
        streams = get_audio_urls(video_id)
        if not streams:
            raise HTTPException(status_code=404, detail="No audio streams found")
        
        stream = streams[-1] if quality == "low" else (streams[1] if quality == "medium" and len(streams) > 1 else streams[0])
        audio_url = stream["url"]
        
        async def stream_generator():
            async with httpx.AsyncClient(timeout=httpx.Timeout(300, connect=10)) as client:
                async with client.stream("GET", audio_url, headers={"Range": "bytes=0-"}) as resp:
                    async for chunk in resp.aiter_bytes(chunk_size=131072):
                        yield chunk
        
        content_type = "audio/webm" if stream["format"] == "webm" else "audio/mp4"
        return StreamingResponse(stream_generator(), media_type=content_type, headers={
            "Accept-Ranges": "bytes", "Cache-Control": "public, max-age=3600", "Content-Type": content_type
        })
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/artist/{artist_id}")
def get_artist(artist_id: str):
    try:
        artist = yt.get_artist(artist_id)
        artist["cover"] = get_best_thumbnail(artist.get("thumbnails", []))
        return {"success": True, "data": artist}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/artist/{artist_id}/albums")
def get_artist_albums(artist_id: str):
    try:
        artist = yt.get_artist(artist_id)
        if "albums" in artist and "browseId" in artist["albums"]:
            return {"success": True, "data": yt.get_artist_albums(artist["albums"]["browseId"], artist["albums"]["params"])}
        return {"success": True, "data": []}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/artist/{artist_id}/singles")
def get_artist_singles(artist_id: str):
    try:
        artist = yt.get_artist(artist_id)
        if "singles" in artist and "browseId" in artist["singles"]:
            return {"success": True, "data": yt.get_artist_albums(artist["singles"]["browseId"], artist["singles"]["params"])}
        return {"success": True, "data": []}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/album/{album_id}")
def get_album(album_id: str):
    try:
        album = yt.get_album(album_id)
        cover = get_best_thumbnail(album.get("thumbnails", []))
        tracks = []
        for t in album.get("tracks", []):
            formatted = format_track(t)
            formatted["cover"] = cover
            tracks.append(formatted)
        album["tracks"] = tracks
        album["cover"] = cover
        return {"success": True, "data": album}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/album/{album_id}/browse")
def get_album_browse(album_id: str):
    try:
        return {"success": True, "data": yt.get_album_browse_id(album_id)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/charts")
def get_charts(country: str = Query("ZZ")):
    try:
        return {"success": True, "data": yt.get_charts(country)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/moods")
def get_mood_categories():
    try:
        return {"success": True, "data": yt.get_mood_categories()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/moods/{params}")
def get_mood_playlists(params: str):
    try:
        return {"success": True, "data": yt.get_mood_playlists(params)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/genres")
def get_genres():
    try:
        return {"success": True, "data": yt.get_mood_categories()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/new_releases")
def get_new_releases():
    try:
        charts = yt.get_charts("ZZ")
        return {"success": True, "data": charts.get("trending", {}).get("items", [])}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/tasteprofile")
def get_tasteprofile():
    try:
        return {"success": True, "data": yt.get_tasteprofile()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/playlist/{playlist_id}")
def get_playlist(playlist_id: str, limit: int = Query(100)):
    try:
        playlist = yt.get_playlist(playlist_id, limit=limit)
        playlist["cover"] = get_best_thumbnail(playlist.get("thumbnails", []))
        tracks = [format_track(t) for t in playlist.get("tracks", [])]
        playlist["tracks"] = tracks
        return {"success": True, "data": playlist}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/playlist/{playlist_id}/suggestions")
def get_playlist_suggestions(playlist_id: str):
    try:
        playlist = yt.get_playlist(playlist_id, limit=1, suggestions_limit=25)
        return {"success": True, "data": playlist.get("suggestions", [])}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/radio/{video_id}")
def get_radio(video_id: str, limit: int = Query(25)):
    try:
        results = yt.get_watch_playlist(videoId=video_id, limit=limit)
        tracks = [format_track(t) for t in results.get("tracks", [])]
        return {"success": True, "data": {"playlistId": results.get("playlistId"), "tracks": tracks}}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/related/{video_id}")
def get_related(video_id: str):
    try:
        results = yt.get_watch_playlist(videoId=video_id, limit=50, radio=False)
        tracks = [format_track(t) for t in results.get("tracks", [])]
        return {"success": True, "data": tracks}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/lyrics/{video_id}")
def get_lyrics(video_id: str):
    try:
        watch = yt.get_watch_playlist(video_id)
        if watch and "lyrics" in watch:
            return {"success": True, "data": yt.get_lyrics(watch["lyrics"])}
        return {"success": False, "data": None, "error": "No lyrics"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/user/{channel_id}")
def get_user(channel_id: str):
    try:
        return {"success": True, "data": yt.get_user(channel_id)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/user/{channel_id}/playlists")
def get_user_playlists(channel_id: str):
    try:
        user = yt.get_user(channel_id)
        if "playlists" in user and "browseId" in user["playlists"]:
            return {"success": True, "data": yt.get_user_playlists(channel_id, user["playlists"]["params"])}
        return {"success": True, "data": []}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/podcast/{podcast_id}")
def get_podcast(podcast_id: str):
    try:
        return {"success": True, "data": yt.get_podcast(podcast_id)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/episode/{episode_id}")
def get_episode(episode_id: str):
    try:
        return {"success": True, "data": yt.get_episode(episode_id)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/channel/{channel_id}")
def get_channel(channel_id: str):
    try:
        return {"success": True, "data": yt.get_channel(channel_id)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
