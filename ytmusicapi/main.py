import sys
sys.path.insert(0, '.')

from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from ytmusic import YTMusic

app = FastAPI(title="YTMusic API", docs_url="/docs")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

yt = YTMusic()

@app.get("/")
def root():
    return {"status": "ok", "service": "YTMusic API"}

@app.get("/search")
def search(
    query: str = Query(...),
    filter: str = Query(None, description="songs, videos, albums, artists, playlists, community_playlists, featured_playlists, uploads"),
    limit: int = Query(20),
    ignore_spelling: bool = Query(False)
):
    try:
        results = yt.search(query, filter=filter, limit=limit, ignore_spelling=ignore_spelling)
        return {"success": True, "data": results}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/search/suggestions")
def search_suggestions(query: str = Query(...)):
    try:
        results = yt.get_search_suggestions(query)
        return {"success": True, "data": results}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/home")
def get_home():
    try:
        results = yt.get_home(limit=6)
        return {"success": True, "data": results}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/artist/{artist_id}")
def get_artist(artist_id: str):
    try:
        results = yt.get_artist(artist_id)
        return {"success": True, "data": results}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/artist/{artist_id}/albums")
def get_artist_albums(artist_id: str):
    try:
        artist = yt.get_artist(artist_id)
        if "albums" in artist and "browseId" in artist["albums"]:
            results = yt.get_artist_albums(artist["albums"]["browseId"], artist["albums"]["params"])
            return {"success": True, "data": results}
        return {"success": True, "data": []}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/album/{album_id}")
def get_album(album_id: str):
    try:
        results = yt.get_album(album_id)
        return {"success": True, "data": results}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/song/{video_id}")
def get_song(video_id: str):
    try:
        results = yt.get_song(video_id)
        return {"success": True, "data": results}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/lyrics/{video_id}")
def get_lyrics(video_id: str):
    try:
        watch = yt.get_watch_playlist(video_id)
        if watch and "lyrics" in watch:
            lyrics = yt.get_lyrics(watch["lyrics"])
            return {"success": True, "data": lyrics}
        return {"success": False, "data": None, "error": "No lyrics found"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/playlist/{playlist_id}")
def get_playlist(playlist_id: str, limit: int = Query(100)):
    try:
        results = yt.get_playlist(playlist_id, limit=limit)
        return {"success": True, "data": results}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/charts")
def get_charts(country: str = Query("ZZ", description="Country code (ZZ=global, US, GB, IN, etc)")):
    try:
        results = yt.get_charts(country)
        return {"success": True, "data": results}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/moods")
def get_mood_categories():
    try:
        results = yt.get_mood_categories()
        return {"success": True, "data": results}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/moods/{params}")
def get_mood_playlists(params: str):
    try:
        results = yt.get_mood_playlists(params)
        return {"success": True, "data": results}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/radio/{video_id}")
def get_watch_playlist(video_id: str, limit: int = Query(25)):
    try:
        results = yt.get_watch_playlist(videoId=video_id, limit=limit)
        return {"success": True, "data": results}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/tasteprofile")
def get_tasteprofile():
    try:
        results = yt.get_tasteprofile()
        return {"success": True, "data": results}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/user/{channel_id}")
def get_user(channel_id: str):
    try:
        results = yt.get_user(channel_id)
        return {"success": True, "data": results}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/user/{channel_id}/playlists")
def get_user_playlists(channel_id: str):
    try:
        user = yt.get_user(channel_id)
        if "playlists" in user and "browseId" in user["playlists"]:
            results = yt.get_user_playlists(channel_id, user["playlists"]["params"])
            return {"success": True, "data": results}
        return {"success": True, "data": []}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
