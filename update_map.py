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
    
    # Init stats mensuelles
    current_month = datetime.now().strftime("%Y-%m")
    month_dist = 0
    month_time_sec = 0
    
    url = f"https://www.komoot.com/api/v007/users/{USER_ID}/tours/?type=tour_recorded&sort_field=date&sort_direction=desc&limit=50"
    resp = session.get(url, headers={'User-Agent': 'Mozilla/5.0'})
    
    if resp.status_code == 200:
        tours_data = resp.json().get('_embedded', {}).get('tours', [])
        
        # Calcul des stats du mois sur les tours reçus
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
        # 1. CARTE (Satellite + Labels FR)
        m = folium.Map(location=[46.5, 2.2], zoom_start=6, tiles=None)
        folium.TileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', attr='Esri', name='Satellite').add_to(m)
        folium.TileLayer('https://{s}.tile.openstreetmap.fr/osmfr/{z}/{x}/{y}.png', attr='OSM FR', name='Labels', overlay=True, opacity=0.8).add_to(m)
        Fullscreen().add_to(m)

        # 2. TUILES
        visited_tiles = set(get_tile_coords(p[0], p[1]) for p in storage["points"])
        max_cluster = calculate_max_cluster(visited_tiles)
        for tile in visited_tiles:
            folium.Rectangle(bounds=get_tile_rect(tile[0], tile[1]), color='#FFFF00', fill=True, fill_opacity=0.3, weight=1).add_to(m)

        # 3. HEATMAP
        HeatMap(storage["points"], radius=3, blur=2, min_opacity=0.4, gradient={0.4: 'red', 1: 'yellow'}).add_to(m)

        # Conversion temps mensuel
        h = int(month_time_sec // 3600)
        m_time = int((month_time_sec % 3600) // 60)

        # Dashboard Responsive (Correction Mobile)
        tours_html = "".join([f"<div style='border-bottom:1px solid #eee; padding:5px 0;'><b>{t['name']}</b><br><small>{t['date']} - {t['dist']}km</small></div>" for t in storage["last_tours"]])
        
        sidebar_html = f'''
        <div id="sidebar" style="position:fixed; top:10px; right:10px; width:220px; z-index:1000; background:rgba(255,255,255,0.95); color:#222; padding:15px; border-radius:12px; font-family:sans-serif; border:2px solid #FFFF00; box-shadow: 0 4px 15px rgba(0,0,0,0.2); font-size: 13px;">
            <h3 style="margin:0 0 10px 0; text-align:center; font-size:16px;">SQUADRA DASHBOARD</h3>
            
            <div style="background:#f9f9f9; padding:10px; border-radius:8px; margin-bottom:10px; border-left:4px solid #FFFF00;">
                <b style="font-size:11px; color:#666;">CE MOIS ({datetime.now().strftime('%B')})</b><br>
                <b>{round(month_dist/1000, 1)} km</b> | <b>{h}h {m_time}min</b>
            </div>

            <div style="display:flex; justify-content:space-around; margin-bottom:10px; text-align:center; font-weight:bold;">
                <div>{len(visited_tiles)}<br><small style="font-size:9px;">TUILES</small></div>
                <div>{max_cluster}<br><small style="font-size:9px;">CLUSTER</small></div>
                <div>{storage["stats"]["count"]}<br><small style="font-size:9px;">TOURS</small></div>
            </div>

            <div style="border-top:1px solid #ddd; padding-top:10px;">
                <b style="font-size:10px; color:#666;">5 DERNIÈRES SORTIES</b>
                <div style="max-height:150px; overflow-y:auto; font-size:11px;">{tours_html}</div>
            </div>
        </div>
        <style>
            @media (max-width: 600px) {{
                #sidebar {{ width: 160px !important; padding: 10px !important; font-size: 11px !important; }}
                h3 {{ font-size: 12px !important; }}
            }}
        </style>
        '''
        m.get_root().html.add_child(folium.Element(sidebar_html))
        
        with open(DATA_FILE, 'w') as f: json.dump(storage, f)
        m.save("index.html")

if __name__ == "__main__":
    run_sync()
