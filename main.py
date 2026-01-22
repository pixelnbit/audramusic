from fastapi import FastAPI, Query, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, RedirectResponse
from ytmusicapi.ytmusic import YTMusic
from concurrent.futures import ThreadPoolExecutor, as_completed
import boto3
from botocore.config import Config
import httpx
import time
import os
import asyncio

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

R2_ACCOUNT_ID = os.getenv("R2_ACCOUNT_ID", "cfc842a40b4ee9ef4d556523e51da3d8")
R2_ACCESS_KEY = os.getenv("R2_ACCESS_KEY_ID", "b142bf37d03235a685e0d3bb945b9e06")
R2_SECRET_KEY = os.getenv("R2_SECRET_ACCESS_KEY", "780543a20063a1bc5cd28386a641d3005f93b24652e2908bc3a64a1f57b97462")
R2_BUCKET = os.getenv("R2_BUCKET_NAME", "audramusic")
R2_ENDPOINT = f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com"

s3 = boto3.client(
    "s3",
    endpoint_url=R2_ENDPOINT,
    aws_access_key_id=R2_ACCESS_KEY,
    aws_secret_access_key=R2_SECRET_KEY,
    config=Config(signature_version="s3v4"),
    region_name="auto"
)

PIPED_INSTANCES = [
    "https://pipedapi.kavin.rocks",
    "https://pipedapi.adminforge.de",
    "https://api.piped.yt",
    "https://pipedapi.r4fo.com",
    "https://pipedapi.colinslegacy.com",
]

stream_url_cache = {}
cache_expiry = {}

def check_r2_exists(video_id: str) -> bool:
    try:
        s3.head_object(Bucket=R2_BUCKET, Key=f"audio/{video_id}.webm")
        return True
    except:
        return False

def get_r2_url(video_id: str) -> str:
    try:
        return s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": R2_BUCKET, "Key": f"audio/{video_id}.webm"},
            ExpiresIn=86400
        )
    except:
        return None

async def upload_to_r2(video_id: str, audio_data: bytes):
    try:
        s3.put_object(
            Bucket=R2_BUCKET,
            Key=f"audio/{video_id}.webm",
            Body=audio_data,
            ContentType="audio/webm"
        )
        return True
    except Exception as e:
        print(f"R2 upload error: {e}")
        return False

async def get_audio_url_from_piped(video_id: str) -> dict:
    if video_id in stream_url_cache and cache_expiry.get(video_id, 0) > time.time():
        return stream_url_cache[video_id]
    
    async with httpx.AsyncClient(timeout=15) as client:
        for instance in PIPED_INSTANCES:
            try:
                resp = await client.get(f"{instance}/streams/{video_id}")
                if resp.status_code == 200:
                    data = resp.json()
                    audio_streams = data.get("audioStreams", [])
                    if audio_streams:
                        best = max(audio_streams, key=lambda x: x.get("bitrate", 0))
                        result = {
                            "url": best.get("url"),
                            "bitrate": best.get("bitrate"),
                            "format": best.get("format", "webm"),
                            "codec": best.get("codec", "opus")
                        }
                        stream_url_cache[video_id] = result
                        cache_expiry[video_id] = time.time() + 14400
                        return result
            except:
                continue
    return None

async def download_and_cache(video_id: str, audio_url: str) -> bool:
    try:
        async with httpx.AsyncClient(timeout=300) as client:
            resp = await client.get(audio_url)
            if resp.status_code == 200:
                await upload_to_r2(video_id, resp.content)
                return True
    except Exception as e:
        print(f"Download error: {e}")
    return False

def get_best_thumbnail(thumbnails: list) -> str:
    if not thumbnails:
        return None
    best = max(thumbnails, key=lambda x: x.get("width", 0) * x.get("height", 0))
    url = best.get("url", "")
    if "ytimg.com" in url:
        if "/vi/" in url:
            vid_id = url.split("/vi/")[1].split("/")[0]
        elif "i.ytimg.com" in url:
            vid_id = url.split("/")[-2] if url.count("/") > 3 else None
        else:
            vid_id = None
        if vid_id:
            return f"https://img.youtube.com/vi/{vid_id}/maxresdefault.jpg"
    if "lh3.googleusercontent.com" in url:
        return url.split("=")[0] + "=w800-h800-l90-rj"
    if "yt3.ggpht.com" in url or "yt3.googleusercontent.com" in url:
        return url.split("=")[0] + "=s800-c-k-c0x00ffffff-no-rj"
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

async def get_country_from_ip(request) -> str:
    try:
        forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        client_ip = forwarded or request.client.host
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"http://ip-api.com/json/{client_ip}?fields=countryCode")
            if resp.status_code == 200:
                return resp.json().get("countryCode", "US")
    except:
        pass
    return "US"

@app.get("/")
def root():
    return {"status": "ok", "service": "YTMusic API"}

@app.get("/explore")
async def get_explore(request: Request, country: str = Query(None)):
    try:
        if not country:
            country = await get_country_from_ip(request)
        charts = yt.get_charts(country)
        moods = yt.get_mood_categories()
        
        if isinstance(charts, list):
            return {"success": True, "data": {
                "country": country,
                "trending": charts[:20] if charts else [],
                "top_songs": [],
                "top_videos": [],
                "top_artists": [],
                "moods": moods
            }}
        
        return {"success": True, "data": {
            "country": country,
            "trending": charts.get("trending", {}).get("items", [])[:20] if isinstance(charts.get("trending"), dict) else [],
            "top_songs": charts.get("songs", {}).get("items", [])[:20] if isinstance(charts.get("songs"), dict) else [],
            "top_videos": charts.get("videos", {}).get("items", [])[:20] if isinstance(charts.get("videos"), dict) else [],
            "top_artists": charts.get("artists", {}).get("items", [])[:20] if isinstance(charts.get("artists"), dict) else [],
            "moods": moods
        }}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/search")
def search(query: str = Query(...), filter: str = Query(None), limit: int = Query(20), ignore_spelling: bool = Query(False)):
    try:
        clean_filter = filter if filter and filter.strip() else None
        results = yt.search(query, filter=clean_filter, limit=limit, ignore_spelling=ignore_spelling)
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
async def get_song(video_id: str):
    try:
        song = yt.get_song(video_id)
        details = song.get("videoDetails", {})
        thumbnails = details.get("thumbnail", {}).get("thumbnails", [])
        
        in_r2 = check_r2_exists(video_id)
        
        return {"success": True, "data": {
            "id": video_id, 
            "title": details.get("title"), 
            "artist": details.get("author"),
            "duration": int(details.get("lengthSeconds", 0)), 
            "cover": get_best_thumbnail(thumbnails),
            "views": details.get("viewCount"), 
            "stream_url": f"/stream/{video_id}",
            "cached": in_r2
        }}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/stream/{video_id}")
async def stream_audio(video_id: str):
    if check_r2_exists(video_id):
        url = get_r2_url(video_id)
        if url:
            return RedirectResponse(url=url, status_code=302)
    
    audio_info = await get_audio_url_from_piped(video_id)
    if not audio_info:
        raise HTTPException(status_code=404, detail="No audio found")
    
    audio_url = audio_info["url"]
    
    asyncio.create_task(download_and_cache(video_id, audio_url))
    
    async def stream_generator():
        async with httpx.AsyncClient(timeout=httpx.Timeout(300, connect=10)) as client:
            async with client.stream("GET", audio_url) as resp:
                async for chunk in resp.aiter_bytes(chunk_size=65536):
                    yield chunk
    
    return StreamingResponse(
        stream_generator(), 
        media_type="audio/webm",
        headers={"Accept-Ranges": "bytes", "Cache-Control": "public, max-age=3600"}
    )

@app.get("/artist/{artist_id}")
def get_artist(artist_id: str):
    try:
        artist = yt.get_artist(artist_id)
        artist["cover"] = get_best_thumbnail(artist.get("thumbnails", []))
        return {"success": True, "data": artist}
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

@app.get("/radio/{video_id}")
def get_radio(video_id: str, limit: int = Query(25)):
    try:
        results = yt.get_watch_playlist(videoId=video_id, limit=limit)
        tracks = [format_track(t) for t in results.get("tracks", [])]
        return {"success": True, "data": {"playlistId": results.get("playlistId"), "tracks": tracks}}
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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
