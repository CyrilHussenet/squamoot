import os
import requests
import gpxpy
import folium
import logging
import json
import math
import time
from folium.plugins import Fullscreen
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
    # On stocke maintenant des listes de lignes (traces) au lieu de points isolés
    return {"traces": [], "tour_ids": [], "last_tours": [], "stats": {"dist": 0, "count": 0}}

def run_sync():
    if not USER_ID or not SESSION_COOKIE: return
    session = requests.Session()
    session.cookies.set('komoot_session', SESSION_COOKIE, domain='.komoot.com')
    storage = load_existing_data()
    
    current_month = datetime.now().strftime("%Y-%m")
    month_dist, month_time_sec = 0, 0
    
    url = f"https://www.komoot.com/api/v007/users/{USER_ID}/tours/?type=tour_recorded&sort_field=date&sort_direction=desc&limit=50"
    resp = session.get(url, headers={'User-Agent': 'Mozilla/5.0'})
    
    if resp.status_code == 200:
        tours_data = resp.json().get('_embedded', {}).get('tours', [])
        for t in tours_data:
            if t['date'].startswith(current_month):
                month_dist += t['distance']
                month_time_sec += t['duration']

        storage["last_tours"] = [{"name": t["name"], "date": t["date"][:10], "dist": round(t["distance"]/1000, 1)} for t in tours_data[:5]]
        
        for tour in tours_data:
            t_id = str(tour['id'])
            if t_id not in storage["tour_ids"]:
                logger.info(f"Sync tour : {t_id}")
                res_gpx = session.get(f"https://www.komoot.com/api/v1/tours/{t_id}.gpx")
                if res_gpx.status_code == 200:
                    gpx = gpxpy.parse(res_gpx.text)
                    storage["tour_ids"].append(t_id)
                    storage["stats"]["count"] += 1
                    
                    # On extrait chaque segment comme une ligne continue
                    for track in gpx.tracks:
                        for seg in track.segments:
                            # Simplification : on ne garde qu'un point sur 3 pour alléger
                            line = [[round(p.latitude, 5), round(p.longitude, 5)] for p in seg.points[::3]]
                            if len(line) > 1:
                                storage["traces"].append(line)
                time.sleep(0.05)

    if storage.get("traces"):
        # CARTE STYLE KOMOOT
        m = folium.Map(location=[46.5, 2.2], zoom_start=6, 
                       tiles='https://{s}.tile.openstreetmap.de/tiles/osmde/{z}/{x}/{y}.png', 
                       attr='&copy; OpenStreetMap contributors')
        
        Fullscreen(position='topleft').add_to(m)

        # 1. TUILES (CARRÉS) - On calcule les tuiles à partir des traces
        visited_tiles = set()
        for trace in storage["traces"]:
            for p in trace:
                visited_tiles.add(get_tile_coords(p[0], p[1]))
        
        max_cluster = calculate_max_cluster(visited_tiles)
        
        for tile in visited_tiles:
            folium.Rectangle(bounds=get_tile_rect(tile[0], tile[1]), 
                             color='#7ED321', fill=True, fill_opacity=0.15, weight=0.5).add_to(m)

        # 2. TRACÉS (LIGNES) - Beaucoup plus fluide que la Heatmap
        for trace in storage["traces"]:
            folium.PolyLine(trace, color='#D0021B', weight=2, opacity=0.6).add_to(m)

        # DASHBOARD
        h, m_time = int(month_time_sec // 3600), int((month_time_sec % 3600) // 60)
        tours_html = "".join([f"<div style='border-bottom:1px solid #eee; padding:5px 0;'><b>{t['name']}</b><br><small>{t['date']} - {t['dist']}km</small></div>" for t in storage["last_tours"]])
        
        sidebar_html = f'''
        <div id="sidebar" style="position:fixed; top:10px; right:10px; width:220px; z-index:1000; background:white; color:#333; padding:15px; border-radius:10px; font-family:sans-serif; border:1px solid #ddd; box-shadow: 0 2px 10px rgba(0,0,0,0.1); font-size:12px;">
            <div style="text-align:center; margin-bottom:10px;"><b style="color:#7ED321; font-size:16px;">SQUADRA MAP</b></div>
            <div style="background:#f0f7e7; padding:10px; border-radius:8px; margin-bottom:10px; border:1px solid #7ED321;">
                <b style="font-size:10px; color:#5a9616;">BILAN {datetime.now().strftime('%B').upper()}</b><br>
                <b style="font-size:15px;">{round(month_dist/1000, 1)} km</b><br>
                <small>{h}h {m_time}min en selle</small>
            </div>
            <div style="display:flex; justify-content:space-around; margin-bottom:10px; text-align:center;">
                <div><b>{len(visited_tiles)}</b><br><small>Tiles</small></div>
                <div><b>{max_cluster}</b><br><small>Cluster</small></div>
                <div><b>{storage["stats"]["count"]}</b><br><small>Tours</small></div>
            </div>
            <div style="border-top:1px solid #eee; padding-top:10px;">
                <b style="color:#999; font-size:10px;">DERNIERS PARCOURS</b>
                <div style="max-height:150px; overflow-y:auto;">{tours_html}</div>
            </div>
        </div>
        '''
        m.get_root().html.add_child(folium.Element(sidebar_html))
        
        with open(DATA_FILE, 'w') as f: json.dump(storage, f)
        m.save("index.html")

if __name__ == "__main__":
    run_sync()
