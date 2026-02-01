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
TILE_COLOR = "#FFA500"  # Orange
TRACE_COLOR = "#0000FF" # Bleu

# Estimation du nombre total de tuiles Z14 pour la France Métropolitaine
# Basé sur une bounding box approx: Lat [41.3, 51.1], Lon [-5.1, 9.6]
TOTAL_TILES_FRANCE = 428000 

# ==========================================
# GESTION API
# ==========================================

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
    logger.info(f"📡 Récupération des activités pour {user_id}...")
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
                    'id': t['id'], 
                    'name': t.get('name', 'Sans nom'),
                    'date': t.get('date'),
                    'distance': t.get('distance', 0),
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

# ============= LOGIQUE GÉOGRAPHIQUE =============

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

# ============= BASE DE DONNÉES =============

def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, 'r') as f:
            return json.load(f)
    return {"tour_details": {}, "traces": {}, "tiles": [], "stats": {}}

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
        if tid not in db["tour_details"] or "elevation_up" not in db["tour_details"][tid]:
            count += 1
            logger.info(f"🔄 Mise à jour sortie {tid} : {tour['name']}")
            points = fetch_tour_coordinates(tid)
            if points:
                city = get_city_from_coords(points[0][0], points[0][1])
                db["tour_details"][tid] = {
                    "id": tid, "name": tour['name'], "date": tour['date'],
                    "distance": tour['distance'], "elevation_up": tour['elevation_up'], "city": city
                }
                simplified = points[::SIMPLIFY_FACTOR]
                db["traces"][tid] = simplified
                for lat, lon in simplified:
                    existing_tiles.add(deg2num(lat, lon, TILE_ZOOM))
            
            if count % 10 == 0: save_data(db)

    db["tiles"] = list(existing_tiles)
    save_data(db)
    create_map(db)

# ============= GÉNÉRATION CARTE =============

def create_map(db):
    start_loc = [46.6033, 1.8883] # Centre France
    if db["traces"]:
        last_tid = list(db["traces"].keys())[-1]
        start_loc = db["traces"][last_tid][0]
    
    m = folium.Map(location=start_loc, zoom_start=10, tiles=None)
    
    # Couches de fond
    folium.TileLayer('OpenStreetMap', name='Plan (OSM)').add_to(m)
    folium.TileLayer(
        tiles='https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
        attr='Esri', name='Satellite (Aérien)', overlay=False
    ).add_to(m)

    # Tuiles exploration
    tile_group = folium.FeatureGroup(name="Exploration (Tuiles)", show=True).add_to(m)
    for xtile, ytile in db.get("tiles", []):
        nw = num2deg(xtile, ytile, TILE_ZOOM)
        se = num2deg(xtile + 1, ytile + 1, TILE_ZOOM)
        folium.Rectangle(
            bounds=[[nw[0], nw[1]], [se[0], se[1]]],
            color=None, fill=True, fill_color=TILE_COLOR, fill_opacity=0.3, weight=0
        ).add_to(tile_group)

    # Traces avec popups
    trace_group = folium.FeatureGroup(name="Traces GPS", show=True).add_to(m)
    for tid, coords in db.get("traces", {}).items():
        info = db["tour_details"].get(tid, {})
        dist_km = round(info.get('distance', 0)/1000, 1)
        ele = info.get('elevation_up', 0)
        date_iso = info.get('date', '')[:10]
        
        popup_txt = f"""
        <div style='font-family:sans-serif; width:160px;'>
            <b style='color:#007bff;'>{info.get('name')}</b><br>
            📅 {date_iso}<br>
            📏 {dist_km} km<br>
            ⛰️ {ele} m D+
        </div>
        """
        folium.PolyLine(
            coords, color=TRACE_COLOR, weight=3, opacity=0.6,
            tooltip=f"{info.get('name')} ({dist_km}km)",
            popup=folium.Popup(popup_txt)
        ).add_to(trace_group)

    folium.LayerControl(collapsed=False).add_to(m)
    Fullscreen().add_to(m)
    LocateControl().add_to(m)

    # CALCUL STATISTIQUES
    now = datetime.now()
    this_month_str = now.strftime("%Y-%m")
    last_month_date = now.replace(day=1) - timedelta(days=1)
    last_month_str = last_month_date.strftime("%Y-%m")

    km_this_month = 0
    km_last_month = 0
    max_dist = 0
    
    details = list(db["tour_details"].values())
    for t in details:
        d = t.get('distance', 0) / 1000
        date_t = t.get('date', '')
        if date_t.startswith(this_month_str): km_this_month += d
        elif date_t.startswith(last_month_str): km_last_month += d
        if d > max_dist: max_dist = d

    # % France
    percent_france = (len(db.get("tiles", [])) / TOTAL_TILES_FRANCE) * 100

    # DASHBOARD HTML
    sorted_tours = sorted(details, key=lambda x: x.get('date') or "", reverse=True)
    last_5_rows = "".join([f"<tr><td>{t.get('date')[:10]}</td><td>{t['name'][:18]}</td><td><b>{round(t['distance']/1000,1)}k</b></td></tr>" for t in sorted_tours[:5]])

    html_dashboard = f"""
    <style>
        #dash {{ position: fixed; top: 10px; right: 10px; width: 300px; z-index: 9999; 
                background: rgba(255,255,255,0.95); border-radius: 12px; box-shadow: 0 4px 15px rgba(0,0,0,0.3);
                font-family: 'Segoe UI', Arial; transition: 0.3s; overflow: hidden; border: 1px solid #ddd; }}
        #dash.collapsed {{ width: 50px; height: 50px; cursor: pointer; }}
        .header {{ background: #2c3e50; color: white; padding: 12px; display: flex; justify-content: space-between; cursor: pointer; }}
        .content {{ padding: 15px; font-size: 13px; max-height: 75vh; overflow-y: auto; }}
        .stat-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-bottom: 15px; }}
        .stat-card {{ background: #f8f9fa; padding: 8px; border-radius: 6px; text-align: center; border: 1px solid #eee; }}
        .stat-val {{ font-size: 16px; font-weight: bold; color: #e67e22; display: block; }}
        .stat-label {{ font-size: 10px; color: #7f8c8d; text-transform: uppercase; }}
        table {{ width: 100%; border-collapse: collapse; }}
        td {{ padding: 6px 0; border-bottom: 1px solid #eee; }}
        .trend {{ font-size: 10px; color: {'green' if km_this_month >= km_last_month else 'red'}; }}
    </style>
    
    <div id="dash">
        <div class="header" onclick="document.getElementById('dash').classList.toggle('collapsed')">
            <span>📊 Mon Tableau de Bord</span>
            <span>↱</span>
        </div>
        <div class="content">
            <div class="stat-grid">
                <div class="stat-card">
                    <span class="stat-label">Ce mois</span>
                    <span class="stat-val">{int(km_this_month)} km</span>
                    <span class="trend">{'▲' if km_this_month >= km_last_month else '▼'} vs mois dernier</span>
                </div>
                <div class="stat-card">
                    <span class="stat-label">Mois dernier</span>
                    <span class="stat-val">{int(km_last_month)} km</span>
                </div>
                <div class="stat-card" style="grid-column: span 2;">
                    <span class="stat-label">Exploration France Métropolitaine</span>
                    <span class="stat-val">{percent_france:.4f} %</span>
                    <div style="width:100%; background:#eee; height:6px; border-radius:3px; margin-top:5px;">
                        <div style="width:{min(percent_france*100, 100)}%; background:#FFA500; height:100%; border-radius:3px;"></div>
                    </div>
                </div>
            </div>
            
            <p style="margin:0; font-size: 11px; color:#27ae60;">🏆 Record de distance : <b>{int(max_dist)} km</b></p>
            
            <h4 style="border-bottom: 2px solid #eee; margin: 15px 0 8px 0;">Dernières activités</h4>
            <table>{last_5_rows}</table>
        </div>
    </div>
    """
    
    m.get_root().html.add_child(folium.Element(html_dashboard))
    m.save("index.html")
    logger.info("✅ Carte index.html générée avec statistiques étendues.")

if __name__ == "__main__":
    if USER_ID:
        update_database(USER_ID)
