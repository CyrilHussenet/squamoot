import os
import time
import json
import math
import logging
import cloudscraper
import folium
from folium.plugins import Fullscreen

# ==========================================
# CONFIGURATION
# ==========================================
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger()

# Secrets
USER_ID = os.getenv("KOMOOT_USER_ID")

# Paramètres
# IMPORTANT : On garde le même nom que le workflow attend
DATA_FILE = "all_points.json"  
SIMPLIFY_FACTOR = int(os.getenv("SIMPLIFY_FACTOR", "2"))
TILE_ZOOM = 14
TILE_COLOR = os.getenv("TILE_COLOR", "#7ED321")
TRACE_COLOR = os.getenv("TRACE_COLOR", "#D0021B")

# ==========================================
# GESTION API (CLOUDSCRAPER)
# ==========================================

def get_scraper():
    return cloudscraper.create_scraper(browser={'browser': 'chrome','platform': 'windows','desktop': True})

def fetch_public_tours_list(user_id):
    """Récupère la liste de TOUS les tours publics"""
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
                logger.error(f"❌ Stop: Erreur {resp.status_code} page {page}")
                break
                
            data = resp.json()
            embedded_tours = data.get('_embedded', {}).get('tours', [])
            
            if not embedded_tours:
                break
                
            for t in embedded_tours:
                tours.append({'id': t['id'], 'name': t.get('name', 'Sans nom')})
            
            # Pagination
            if page >= data.get('page', {}).get('totalPages', 0) - 1:
                break
            page += 1
            time.sleep(0.5)
            
        except Exception as e:
            logger.error(f"❌ Erreur réseau: {e}")
            break
            
    return tours

def fetch_tour_coordinates(tour_id):
    """Récupère les points GPS"""
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
# LOGIQUE TILES & UPDATE
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
                # Migration de compatibilité (si ancien format)
                if "tours_processed" not in data: 
                    data["tours_processed"] = data.get("tour_ids", [])
                if "tiles" not in data:
                    data["tiles"] = []
                if "traces" not in data:
                    data["traces"] = []
                return data
        except Exception:
            pass
    return {"tours_processed": [], "points": [], "tiles": [], "stats": {"count": 0}}

def save_data(data):
    with open(DATA_FILE, 'w') as f:
        json.dump(data, f)

def update_database(user_id):
    db = load_data()
    
    # On convertit tout en string pour être sûr de la comparaison
    processed_ids = set(str(x) for x in db["tours_processed"])
    
    # 1. Récupérer la liste complète des tours disponibles en ligne
    online_tours = fetch_public_tours_list(user_id)
    
    # 2. FILTRAGE : On ne garde que ceux qu'on n'a PAS encore traités
    new_tours = [t for t in online_tours if str(t['id']) not in processed_ids]
    
    logger.info(f"📊 Bilan : {len(online_tours)} tours en ligne | {len(processed_ids)} déjà en cache.")
    
    if not new_tours:
        logger.info("✨ Tout est à jour ! Aucune nouvelle trace à télécharger.")
        create_map(db) # On régénère quand même la carte html
        return

    logger.info(f"🚀 Démarrage du téléchargement pour les {len(new_tours)} nouveaux tours...")
    
    existing_tiles = set(tuple(t) for t in db["tiles"])
    
    for i, tour in enumerate(new_tours):
        logger.info(f"   [{i+1}/{len(new_tours)}] Téléchargement : {tour['name']}")
        points = fetch_tour_coordinates(tour['id'])
        
        if points:
            simplified = points[::SIMPLIFY_FACTOR]
            db["traces"].append(simplified)
            
            for lat, lon in simplified:
                tile = deg2num(lat, lon, TILE_ZOOM)
                existing_tiles.add(tile)
            
            db["tours_processed"].append(tour['id'])
            
        time.sleep(0.2) # Petite pause API
        
        # Sauvegarde intermédiaire tous les 10 tours (sécurité crash)
        if i % 10 == 0:
            db["tiles"] = list(existing_tiles)
            db["stats"]["count"] = len(db["tours_processed"])
            save_data(db)

    # Sauvegarde finale
    db["tiles"] = list(existing_tiles)
    db["stats"]["count"] = len(db["tours_processed"])
    save_data(db)
    logger.info("✅ Base de données mise à jour.")
    
    create_map(db)

# ==========================================
# GÉNÉRATION CARTE
# ==========================================

def create_map(db):
    if not db["tiles"]:
        logger.warning("⚠️ Aucune donnée tile à afficher.")
        return

    start_loc = db["traces"][-1][0] if db["traces"] else [48.8566, 2.3522]
    m = folium.Map(location=start_loc, zoom_start=12, tiles="CartoDB dark_matter")
    Fullscreen().add_to(m)

    # Tiles
    for xtile, ytile in db["tiles"]:
        nw = num2deg(xtile, ytile, TILE_ZOOM)
        se = num2deg(xtile + 1, ytile + 1, TILE_ZOOM)
        folium.Rectangle(
            bounds=[[nw[0], nw[1]], [se[0], se[1]]],
            color=None, fill=True, fill_color=TILE_COLOR, fill_opacity=0.3, weight=0
        ).add_to(m)

    # Traces
    for trace in db["traces"]:
        if len(trace) > 1:
            folium.PolyLine(trace, color=TRACE_COLOR, weight=2, opacity=0.6).add_to(m)

    m.save("index.html")
    logger.info("🗺️ Carte index.html générée avec succès.")

if __name__ == "__main__":
    if not USER_ID:
        logger.error("❌ ERREUR: KOMOOT_USER_ID manquant.")
        exit(1)
    update_database(USER_ID)
