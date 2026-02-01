import os
import time
import json
import math
import logging
import cloudscraper
import folium
import requests
from folium.plugins import Fullscreen
from datetime import datetime

# ==========================================
# CONFIGURATION
# ==========================================
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger()

# Secrets
USER_ID = os.getenv("KOMOOT_USER_ID")

# Fichiers
DATA_FILE = "all_points.json"

# Paramètres Visuels (Demandés)
SIMPLIFY_FACTOR = int(os.getenv("SIMPLIFY_FACTOR", "2"))
TILE_ZOOM = 14
TILE_COLOR = "#FFA500"  # Orange
TRACE_COLOR = "#0000FF" # Bleu
MAP_TILES = "OpenStreetMap" # Fond blanc style OSM

# ==========================================
# GESTION API (CLOUDSCRAPER & NOMINATIM)
# ==========================================

def get_scraper():
    return cloudscraper.create_scraper(browser={'browser': 'chrome','platform': 'windows','desktop': True})

def get_city_from_coords(lat, lon):
    """Trouve la commune à partir des coordonnées (via OSM Nominatim)"""
    url = "https://nominatim.openstreetmap.org/reverse"
    params = {
        "lat": lat, "lon": lon, "format": "json", "zoom": 10
    }
    headers = {'User-Agent': 'KomootSquadraMap/1.0 (github-action)'}
    
    try:
        # Respecter la politique OSM (max 1 req/sec)
        time.sleep(1.1) 
        response = requests.get(url, params=params, headers=headers, timeout=5)
        if response.status_code == 200:
            data = response.json()
            address = data.get('address', {})
            # On cherche la ville, village, ou municipalité
            return address.get('city') or address.get('town') or address.get('village') or address.get('municipality') or "Inconnue"
    except Exception as e:
        logger.warning(f"Erreur géocodage: {e}")
    
    return "Inconnue"

def fetch_public_tours_list(user_id):
    """Récupère la liste complète des tours avec métadonnées"""
    scraper = get_scraper()
    tours = []
    page = 0
    per_page = 50
    
    logger.info(f"📡 Récupération de l'index des tours pour {user_id}...")
    
    while True:
        url = f"https://api.komoot.de/v007/users/{user_id}/tours/"
        params = {'type': 'tour_recorded', 'sort': 'date', 'sort_direction': 'desc', 'status': 'public', 'page': page, 'limit': per_page}
        
        try:
            resp = scraper.get(url, params=params, timeout=15)
            if resp.status_code != 200:
                break
                
            data = resp.json()
            embedded_tours = data.get('_embedded', {}).get('tours', [])
            
            if not embedded_tours:
                break
                
            for t in embedded_tours:
                tours.append({
                    'id': t['id'], 
                    'name': t.get('name', 'Sans nom'),
                    'date': t.get('date'),
                    'distance': t.get('distance', 0)
                })
            
            if page >= data.get('page', {}).get('totalPages', 0) - 1:
                break
            page += 1
            time.sleep(0.5)
            
        except Exception:
            break
            
    return tours

def fetch_tour_coordinates(tour_id):
    scraper = get_scraper()
    url = f"https://api.komoot.de/v007/tours/{tour_id}/coordinates"
    try:
        resp = scraper.get(url, timeout=10)
        if resp.status_code == 200:
            items = resp.json().get('items', [])
            return [(item['lat'], item['lng']) for item in items]
    except Exception:
        pass
    return []

# ==========================================
# LOGIQUE & UPDATE
# ==========================================

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
    lat_deg = math.degrees(lat_rad)
    return (lat_deg, lon_deg)

def load_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r') as f:
                data = json.load(f)
                # Migration de structure si besoin
                if "tour_details" not in data: data["tour_details"] = {}
                if "traces" not in data: data["traces"] = []
                if "tiles" not in data: data["tiles"] = []
                return data
        except Exception:
            pass
    return {"tour_details": {}, "traces": [], "tiles": [], "stats": {"count": 0}}

def save_data(data):
    with open(DATA_FILE, 'w') as f:
        json.dump(data, f)

def update_database(user_id):
    db = load_data()
    
    # Récupérer la liste en ligne (avec métadonnées distance/date)
    online_tours = fetch_public_tours_list(user_id)
    
    # Map des tours déjà traités (IDs)
    processed_ids = set(str(k) for k in db["tour_details"].keys())
    
    # Identifier les nouveaux ou ceux incomplets (migration ancienne version)
    tours_to_process = []
    
    for t in online_tours:
        tid = str(t['id'])
        # Si nouveau OU si on a l'ID mais pas la commune (ancienne version)
        if tid not in processed_ids or "city" not in db["tour_details"][tid]:
            tours_to_process.append(t)

    logger.info(f"📊 Bilan : {len(tours_to_process)} tours à traiter/mettre à jour sur {len(online_tours)}.")
    
    existing_tiles = set(tuple(t) for t in db["tiles"])
    
    count = 0
    for tour in tours_to_process:
        tid = str(tour['id'])
        count += 1
        logger.info(f"   [{count}/{len(tours_to_process)}] Traitement : {tour['name']}")
        
        # 1. Si on n'a pas les traces, on télécharge
        points = []
        is_new_trace = False
        
        # Vérification si on a déjà téléchargé la trace dans le passé (via l'ancienne liste 'traces')
        # Pour simplifier, on re-télécharge si c'est une update critique, sinon on saute
        points = fetch_tour_coordinates(tid)
        
        if points:
            # Récupération de la Commune (sur le 1er point)
            lat_start, lon_start = points[0]
            city = get_city_from_coords(lat_start, lon_start)
            
            # Sauvegarde des métadonnées complètes
            db["tour_details"][tid] = {
                "id": tid,
                "name": tour['name'],
                "date": tour['date'],
                "distance": tour['distance'],
                "city": city
            }
            
            # Si c'est un tour vraiment nouveau (pas dans les anciens processed), on ajoute trace et tiles
            # Note : cela peut dupliquer des traces si on avait l'ID mais pas les détails. 
            # Pour éviter ça, on vérifie si on doit ajouter la géométrie.
            # Dans le doute pour cette migration : on ajoute seulement si l'ID n'était pas connu du tout.
            if tid not in processed_ids:
                simplified = points[::SIMPLIFY_FACTOR]
                db["traces"].append(simplified)
                for lat, lon in simplified:
                    existing_tiles.add(deg2num(lat, lon, TILE_ZOOM))

        # Sauvegarde régulière
        if count % 5 == 0:
            db["tiles"] = list(existing_tiles)
            save_data(db)

    # Finalisation
    db["tiles"] = list(existing_tiles)
    db["stats"]["count"] = len(db["tour_details"])
    save_data(db)
    logger.info("✅ Base mise à jour.")
    
    create_map(db)

# ==========================================
# GÉNÉRATION CARTE & DASHBOARD
# ==========================================

def create_map(db):
    start_loc = [48.8566, 2.3522]
    if db["traces"]:
        start_loc = db["traces"][-1][0]
    
    # 1. Carte OSM Blanche
    m = folium.Map(location=start_loc, zoom_start=12, tiles=MAP_TILES)
    Fullscreen().add_to(m)

    # 2. Tiles (Orange Transparent)
    if db.get("tiles"):
        for xtile, ytile in db["tiles"]:
            nw = num2deg(xtile, ytile, TILE_ZOOM)
            se = num2deg(xtile + 1, ytile + 1, TILE_ZOOM)
            folium.Rectangle(
                bounds=[[nw[0], nw[1]], [se[0], se[1]]],
                color=None, fill=True, fill_color=TILE_COLOR, fill_opacity=0.4, weight=0
            ).add_to(m)

    # 3. Traces (Bleu)
    if db.get("traces"):
        for trace in db["traces"]:
            if len(trace) > 1:
                folium.PolyLine(trace, color=TRACE_COLOR, weight=2, opacity=0.7).add_to(m)

    # 4. CALCUL DES STATS POUR LE DASHBOARD
    details = db.get("tour_details", {}).values()
    
    # A. 5 Dernières Sorties
    sorted_tours = sorted(details, key=lambda x: x.get('date') or "", reverse=True)
    last_5 = sorted_tours[:5]
    
    last_5_html = ""
    for t in last_5:
        date_str = t.get('date', '')[:10] # YYYY-MM-DD
        dist_km = round(t.get('distance', 0) / 1000, 1)
        last_5_html += f"<tr><td>{date_str}</td><td>{t['name']}</td><td><b>{dist_km} km</b></td></tr>"

    # B. Stats par Commune
    commune_stats = {}
    for t in details:
        city = t.get('city', 'Inconnue')
        if city not in commune_stats:
            commune_stats[city] = {'count': 0, 'dist': 0}
        commune_stats[city]['count'] += 1
        commune_stats[city]['dist'] += t.get('distance', 0)
    
    # Tri par nombre de sorties
    sorted_communes = sorted(commune_stats.items(), key=lambda item: item[1]['count'], reverse=True)
    
    commune_html = ""
    for city, stats in sorted_communes:
        dist_km = round(stats['dist'] / 1000, 0)
        commune_html += f"<tr><td>{city}</td><td>{stats['count']}</td><td>{int(dist_km)} km</td></tr>"

    # 5. INJECTION HTML (Floating Dashboard)
    html_dashboard = f"""
    <div id="dashboard" style="
        position: fixed; 
        top: 10px; right: 10px; width: 320px;
        background-color: white; padding: 10px; 
        border: 2px solid #ccc; border-radius: 8px; 
        z-index: 9999; font-family: sans-serif; font-size: 12px;
        box-shadow: 0 0 10px rgba(0,0,0,0.2);
        max-height: 90vh; overflow-y: auto;">
        
        <h3 style="margin-top:0; color: #333;">🚴‍♂️ Mes Statistiques</h3>
        
        <h4 style="border-bottom: 1px solid #eee;">Dernières Sorties</h4>
        <table style="width:100%; border-collapse: collapse;">
            {last_5_html}
        </table>
        
        <h4 style="border-bottom: 1px solid #eee; margin-top: 15px;">Par Commune</h4>
        <table style="width:100%; border-collapse: collapse;">
            <tr style="text-align:left; color:#777;"><th>Ville</th><th>#</th><th>Km</th></tr>
            {commune_html}
        </table>
    </div>
    """
    
    m.get_root().html.add_child(folium.Element(html_dashboard))
    m.save("index.html")
    logger.info("✅ Carte index.html générée avec Dashboard.")

if __name__ == "__main__":
    if not USER_ID:
        exit(1)
    update_database(USER_ID)
