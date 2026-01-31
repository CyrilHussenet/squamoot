import os
import requests
import gpxpy
import folium
import logging
import json
import math
import time
from folium.plugins import HeatMap, Fullscreen
from datetime import datetime

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger()

USER_ID = os.getenv("KOMOOT_USER_ID")
SESSION_COOKIE = os.getenv("KOMOOT_SESSION_COOKIE")
DATA_FILE = "all_points.json"

def get_tile_coords(lat, lon, zoom=14):
    lat_rad = math.radians(lat)
    n = 2.0 ** zoom
    xtile = int((lon + 180.0) / 360.0 * n)
    ytile = int((1.0 - math.log(math.tan(lat_rad) + (1 / math.cos(lat_rad))) / math.pi) / 2.0 * n)
    return xtile, ytile

def get_tile_rect(xtile, ytile, zoom=14):
    n = 2.0 ** zoom
    def f(x, y):
        lon = x / n * 360.0 - 180.0
        lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
        return [lat, lon]
    return [f(xtile, ytile), f(xtile + 1, ytile + 1)]

def calculate_max_cluster(tiles_set):
    if not tiles_set: return 0
    visited, max_cluster = set(), 0
    tiles_list = list(tiles_set)
    for tile in tiles_list:
        if tile not in visited:
            cluster_size, queue = 0, [tile]
            visited.add(tile)
            while queue:
                curr = queue.pop(0)
                cluster_size += 1
                for dx, dy in [(0,1), (0,-1), (1,0), (-1,0)]:
                    neighbor = (curr[0]+dx, curr[1]+dy)
                    if neighbor in tiles_set and neighbor not in visited:
                        visited.add(neighbor)
                        queue.append(neighbor)
            max_cluster = max(max_cluster, cluster_size)
    return max_cluster

def load_existing_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r') as f: return json.load(f)
        except: pass
    return {"points": [], "tour_ids": [], "last_tours": [], "stats": {"dist": 0, "count": 0}}

def run_sync():
    if not USER_ID or not SESSION_COOKIE: return
    session = requests.Session()
    session.cookies.set('komoot_session', SESSION_COOKIE, domain='.komoot.com')
    storage = load_existing_data()
    
    # Sync Komoot
    url = f"https://www.komoot.com/api/v007/users/{USER_ID}/tours/?type=tour_recorded&sort_field=date&sort_direction=desc&limit=20"
    resp = session.get(url, headers={'User-Agent': 'Mozilla/5.0'})
    if resp.status_code == 200:
        tours_data = resp.json().get('_embedded', {}).get('tours', [])
        storage["last_tours"] = [{"name": t["name"], "date": t["date"][:10], "dist": round(t["distance"]/1000, 1)} for t in tours_data[:5]]
        for tour in tours_data:
            t_id = str(tour['id'])
            if t_id not in storage["tour_ids"]:
                res_gpx = session.get(f"https://www.komoot.com/api/v1/tours/{t_id}.gpx")
                if res_gpx.status_code == 200:
                    gpx = gpxpy.parse(res_gpx.text)
                    storage["tour_ids"].append(t_id)
                    storage["stats"]["count"] += 1
                    storage["stats"]["dist"] += gpx.length_2d()
                    for track in gpx.tracks:
                        for seg in track.segments:
                            for p in seg.points: storage["points"].append([round(p.latitude, 5), round(p.longitude, 5)])
                time.sleep(0.05)

    if storage["points"]:
        # 1. CARTE SATELLITE CLAIRE
        m = folium.Map(location=[46.5, 2.5], zoom_start=6, tiles=None)
        
        # Couche Satellite
        folium.TileLayer(
            tiles='https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
            attr='Esri',
            name='Satellite',
            overlay=False,
            control=True
        ).add_to(m)
        
        # Couche Labels (Toponymes en français)
        folium.TileLayer(
            tiles='https://{s}.tile.openstreetmap.fr/osmfr/{z}/{x}/{y}.png',
            attr='OpenStreetMap France',
            name='Toponymes',
            overlay=True,
            control=True,
            opacity=0.7 # On laisse les noms un peu transparents
        ).add_to(m)

        Fullscreen().add_to(m)

        # 2. TUILES (CARRÉS)
        visited_tiles = set(get_tile_coords(p[0], p[1]) for p in storage["points"])
        max_cluster = calculate_max_cluster(visited_tiles)

        for tile in visited_tiles:
            folium.Rectangle(
                bounds=get_tile_rect(tile[0], tile[1]), 
                color='#FFFF00', # Jaune pour contraste max sur satellite
                fill=True, 
                fill_opacity=0.25, 
                weight=1,
                popup=f"Tuile {tile}"
            ).add_to(m)

        # 3. HEATMAP (Rouge/Orange pour visibilité satellite)
        HeatMap(storage["points"], radius=3, blur=2, min_opacity=0.5, gradient={0.4: 'red', 1: 'yellow'}).add_to(m)

        # Sidebar Stats
        tours_html = "".join([f"<div style='border-bottom:1px solid #444; padding:5px 0;'><b>{t['name']}</b><br><small>{t['date']} - {t['dist']}km</small></div>" for t in storage["last_tours"]])
        sidebar_html = f'''
        <div style="position:fixed; top:20px; right:20px; width:240px; z-index:1000; background:rgba(255,255,255,0.9); color:#333; padding:20px; border-radius:15px; font-family:sans-serif; border:2px solid #FFFF00; box-shadow: 0 4px 15px rgba(0,0,0,0.3);">
            <h3 style="margin:0 0 15px 0; color:#333; text-align:center; border-bottom: 2px solid #FFFF00;">SQUADRA SATELLITE</h3>
            <div style="display:flex; justify-content:space-between; margin-bottom:20px; text-align:center;">
                <div><b style="font-size:18px;">{len(visited_tiles)}</b><br><small>TUILES</small></div>
                <div><b style="font-size:18px;">{max_cluster}</b><br><small>CLUSTER</small></div>
            </div>
            <div style="font-size:12px;">
                <b style="color:#666; font-size:10px; text-transform:uppercase;">Dernières activités</b>
                <div style="max-height:180px; overflow-y:auto; margin-top:5px;">{tours_html}</div>
            </div>
        </div>
        '''
        m.get_root().html.add_child(folium.Element(sidebar_html))
        
        with open(DATA_FILE, 'w') as f: json.dump(storage, f)
        m.save("index.html")

if __name__ == "__main__":
    run_sync()
