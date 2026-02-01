import os
import time
import json
import math
import logging
import cloudscraper
import folium
from folium.plugins import Fullscreen, LocateControl
import requests
from datetime import datetime, timedelta

# ==========================================
# CONFIGURATION
# ==========================================
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger()

USER_ID = os.getenv("KOMOOT_USER_ID")
DATA_FILE = "all_points.json"

SIMPLIFY_FACTOR = int(os.getenv("SIMPLIFY_FACTOR", "2"))
TILE_ZOOM = 14
TILE_COLOR = "#FFA500"
TRACE_COLOR = "#0000FF"

# Estimation tuiles Z14 pour la France Métropolitaine
TOTAL_TILES_FRANCE = 428000 

def get_scraper():
    return cloudscraper.create_scraper(browser={'browser': 'chrome','platform': 'windows','desktop': True})

def get_city_from_coords(lat, lon):
    url = "https://nominatim.openstreetmap.org/reverse"
    params = {"lat": lat, "lon": lon, "format": "json", "zoom": 10}
    headers = {'User-Agent': 'KomootSquadraMap/3.0'}
    try:
        time.sleep(1.1) 
        response = requests.get(url, params=params, headers=headers, timeout=5)
        if response.status_code == 200:
            addr = response.json().get('address', {})
            return addr.get('city') or addr.get('town') or addr.get('village') or addr.get('municipality') or "Inconnue"
    except Exception: pass
    return "Inconnue"

def fetch_public_tours_list(user_id):
    scraper = get_scraper()
    tours = []
    page = 0
    while True:
        url = f"https://api.komoot.de/v007/users/{user_id}/tours/"
        params = {'type': 'tour_recorded', 'sort': 'date', 'status': 'public', 'page': page, 'limit': 50}
        try:
            resp = scraper.get(url, params=params, timeout=15)
            if resp.status_code != 200: break
            data = resp.json()
            items = data.get('_embedded', {}).get('tours', [])
            if not items: break
            for t in items:
                tours.append({
                    'id': t['id'], 'name': t.get('name', 'Sans nom'),
                    'date': t.get('date'), 'distance': t.get('distance', 0),
                    'elevation_up': t.get('elevation_up', 0)
                })
            if page >= data.get('page', {}).get('totalPages', 0) - 1: break
            page += 1
            time.sleep(0.5)
        except Exception: break
    return tours

def fetch_tour_coordinates(tour_id):
    scraper = get_scraper()
    url = f"https://api.komoot.de/v007/tours/{tour_id}/coordinates"
    try:
        resp = scraper.get(url, timeout=10)
        if resp.status_code == 200:
            return [(item['lat'], item['lng']) for item in resp.json().get('items', [])]
    except Exception: pass
    return []

def deg2num(lat_deg, lon_deg, zoom):
    lat_rad = math.radians(lat_deg)
    n = 2.0 ** zoom
    xtile = int((lon_deg + 180.0) / 360.0 * n)
    ytile = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
    return (xtile, ytile)

def num2deg(xtile, ytile, zoom):
    n = 2.0 ** zoom
    lon_deg = xtile / n * 360.0 - 180.0
    lat_rad = math.atan(math.sinh(math.pi * (1 - 2 * ytile / n)))
    return (math.degrees(lat_rad), lon_deg)

def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, 'r') as f:
            db = json.load(f)
            # REPARATION DE L'ERREUR : Force dict pour les traces
            if not isinstance(db.get("traces"), dict):
                db["traces"] = {}
            if "tour_details" not in db:
                db["tour_details"] = {}
            return db
    return {"tour_details": {}, "traces": {}, "tiles": []}

def save_data(data):
    with open(DATA_FILE, 'w') as f:
        json.dump(data, f)

def update_database(user_id):
    db = load_data()
    online_tours = fetch_public_tours_list(user_id)
    existing_tiles = set(tuple(t) for t in db.get("tiles", []))
    
    count = 0
    for tour in online_tours:
        tid = str(tour['id'])
        if tid not in db["tour_details"]:
            count += 1
            logger.info(f"🔄 Nouveau tour : {tour['name']}")
            points = fetch_tour_coordinates(tid)
            if points:
                city = get_city_from_coords(points[0][0], points[0][1])
                db["tour_details"][tid] = {
                    "id": tid, "name": tour['name'], "date": tour['date'],
                    "distance": tour['distance'], "elevation_up": tour['elevation_up'], "city": city
                }
                db["traces"][tid] = points[::SIMPLIFY_FACTOR]
                for lat, lon in db["traces"][tid]:
                    existing_tiles.add(deg2num(lat, lon, TILE_ZOOM))
            if count % 10 == 0: save_data(db)

    db["tiles"] = list(existing_tiles)
    save_data(db)
    create_map(db)

def create_map(db):
    m = folium.Map(location=[46.6, 2.2], zoom_start=6, tiles=None)
    
    folium.TileLayer('OpenStreetMap', name='Plan').add_to(m)
    folium.TileLayer(
        tiles='https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
        attr='Esri', name='Satellite', overlay=False
    ).add_to(m)

    # Couche Tuiles
    tile_group = folium.FeatureGroup(name="Tuiles Orange", show=True).add_to(m)
    for xtile, ytile in db.get("tiles", []):
        nw, se = num2deg(xtile, ytile, TILE_ZOOM), num2deg(xtile + 1, ytile + 1, TILE_ZOOM)
        folium.Rectangle(bounds=[[nw[0], nw[1]], [se[0], se[1]]], color=None, fill=True, fill_color=TILE_COLOR, fill_opacity=0.4).add_to(tile_group)

    # Couche Traces
    trace_group = folium.FeatureGroup(name="Parcours", show=True).add_to(m)
    for tid, coords in db.get("traces", {}).items():
        info = db["tour_details"].get(tid, {})
        dist = round(info.get('distance', 0)/1000, 1)
        popup = f"<b>{info.get('name')}</b><br>📅 {info.get('date')[:10]}<br>📏 {dist}km | ⛰️ {info.get('elevation_up', 0)}m D+"
        folium.PolyLine(coords, color=TRACE_COLOR, weight=3, opacity=0.7, popup=folium.Popup(popup, max_width=200)).add_to(trace_group)

    folium.LayerControl(collapsed=False).add_to(m)
    Fullscreen().add_to(m)
    LocateControl().add_to(m)

    # Calcul Stats
    now = datetime.now()
    cur_m, last_m = now.strftime("%Y-%m"), (now.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
    km_cur, km_last = 0, 0
    for t in db["tour_details"].values():
        d = t.get('distance', 0)/1000
        if t.get('date', '').startswith(cur_m): km_cur += d
        elif t.get('date', '').startswith(last_m): km_last += d

    percent_fr = (len(db["tiles"]) / TOTAL_TILES_FRANCE) * 100
    
    # Dashboard HTML
    html_dash = f"""
    <style>
        #dash {{ position: fixed; top: 10px; right: 10px; width: 280px; z-index: 9999; background: white; 
                border-radius: 10px; box-shadow: 0 0 10px rgba(0,0,0,0.2); font-family: sans-serif; transition: 0.3s; }}
        #dash.collapsed {{ width: 40px; height: 40px; overflow: hidden; cursor: pointer; }}
        .h {{ background: #333; color: white; padding: 10px; border-radius: 10px 10px 0 0; cursor: pointer; display: flex; justify-content: space-between; }}
        .c {{ padding: 15px; font-size: 12px; }}
        .val {{ font-size: 18px; font-weight: bold; color: #d35400; }}
    </style>
    <div id="dash">
        <div class="h" onclick="document.getElementById('dash').classList.toggle('collapsed')"><span>📊 Stats</span><span>↔</span></div>
        <div class="c">
            <p>Ce mois: <span class="val">{int(km_cur)} km</span><br><small>Mois dernier: {int(km_last)} km</small></p>
            <p>Exploration France:<br><span class="val">{percent_fr:.4f}%</span></p>
            <div style="width:100%; background:#eee; height:8px; border-radius:4px;"><div style="width:{min(percent_fr*500, 100)}%; background:orange; height:100%; border-radius:4px;"></div></div>
        </div>
    </div>
    """
    m.get_root().html.add_child(folium.Element(html_dash))
    m.save("index.html")

if __name__ == "__main__":
    if USER_ID: update_database(USER_ID)
