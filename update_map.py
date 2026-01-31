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
                res_gpx = session.get(f"https://www.komoot.com/api/v1/tours/{t_id}.gpx")
                if res_gpx.status_code == 200:
                    gpx = gpxpy.parse(res_gpx.text)
                    storage["tour_ids"].append(t_id)
                    storage["stats"]["count"] += 1
                    for track in gpx.tracks:
                        for seg in track.segments:
                            for p in seg.points: storage["points"].append([round(p.latitude, 5), round(p.longitude, 5)])
                time.sleep(0.05)

    if storage["points"]:
        # --- CARTE STYLE KOMOOT PLAN ---
        # On utilise le serveur de tuiles OSM.de qui est très proche du style Komoot
        m = folium.Map(location=[46.5, 2.2], zoom_start=6, 
                       tiles='https://{s}.tile.openstreetmap.de/tiles/osmde/{z}/{x}/{y}.png', 
                       attr='&copy; OpenStreetMap contributors (Style Komoot-like)')
        
        Fullscreen(position='topleft').add_to(m)

        # TUILES
        visited_tiles = set(get_tile_coords(p[0], p[1]) for p in storage["points"])
        max_cluster = calculate_max_cluster(visited_tiles)
        for tile in visited_tiles:
            folium.Rectangle(bounds=get_tile_rect(tile[0], tile[1]), 
                             color='#7ED321', # Vert Komoot
                             fill=True, fill_opacity=0.2, weight=1).add_to(m)

        # HEATMAP (Couleurs sobres pour carte claire)
        HeatMap(storage["points"], radius=4, blur=3, min_opacity=0.3, 
                gradient={0.4: '#4A90E2', 1: '#D0021B'}).add_to(m)

        # STATS MENSUELLES
        h, m_time = int(month_time_sec // 3600), int((month_time_sec % 3600) // 60)
        tours_html = "".join([f"<div style='border-bottom:1px solid #eee; padding:5px 0;'><b>{t['name']}</b><br><small>{t['date']} - {t['dist']}km</small></div>" for t in storage["last_tours"]])
        
        sidebar_html = f'''
        <div id="sidebar" style="position:fixed; top:10px; right:10px; width:220px; z-index:1000; background:white; color:#333; padding:15px; border-radius:10px; font-family:sans-serif; border:1px solid #ddd; box-shadow: 0 2px 10px rgba(0,0,0,0.1);">
            <div style="text-align:center; margin-bottom:10px;"><img src="https://upload.wikimedia.org/wikipedia/commons/e/e3/Komoot_logo.png" style="width:80px;"></div>
            <div style="background:#f0f7e7; padding:10px; border-radius:8px; margin-bottom:10px; border:1px solid #7ED321;">
                <b style="font-size:10px; color:#5a9616;">BILAN {datetime.now().strftime('%B').upper()}</b><br>
                <b style="font-size:15px;">{round(month_dist/1000, 1)} km</b><br>
                <small>{h}h {m_time}min en selle</small>
            </div>
            <div style="display:flex; justify-content:space-around; margin-bottom:10px; text-align:center; font-size:12px;">
                <div><b>{len(visited_tiles)}</b><br>Tiles</div>
                <div><b>{max_cluster}</b><br>Cluster</div>
                <div><b>{storage["stats"]["count"]}</b><br>Tours</div>
            </div>
            <div style="font-size:11px; border-top:1px solid #eee; pt:10px;">
                <b style="color:#999;">DERNIERS PARCOURS</b>
                {tours_html}
            </div>
        </div>
        <style>@media (max-width: 600px) {{ #sidebar {{ width: 150px !important; font-size: 10px !important; }} }}</style>
        '''
        m.get_root().html.add_child(folium.Element(sidebar_html))
        
        with open(DATA_FILE, 'w') as f: json.dump(storage, f)
        m.save("index.html")

if __name__ == "__main__":
    run_sync()
