import os
import time
import json
import math
import logging
import cloudscraper  # REMPLACE requests pour contourner le 403
import folium
from folium.plugins import Fullscreen
from datetime import datetime

# ==========================================
# CONFIGURATION
# ==========================================
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger()

# Secrets
USER_ID = os.getenv("KOMOOT_USER_ID") # Votre ID numérique (ex: 123456789)

# Paramètres Carte
DATA_FILE = "all_points.json"
SIMPLIFY_FACTOR = int(os.getenv("SIMPLIFY_FACTOR", "2"))  # Réduire les points pour alléger
TILE_ZOOM = 14
TILE_COLOR = os.getenv("TILE_COLOR", "#7ED321")
TRACE_COLOR = os.getenv("TRACE_COLOR", "#D0021B")

# ==========================================
# GESTION API (CLOUDSCRAPER)
# ==========================================

def get_scraper():
    """Crée un scraper capable de passer les protections anti-bot de Komoot"""
    scraper = cloudscraper.create_scraper(
        browser={
            'browser': 'chrome',
            'platform': 'windows',
            'desktop': True
        }
    )
    return scraper

def fetch_public_tours_list(user_id):
    """Récupère la liste de TOUS les tours enregistrés (Public uniquement)"""
    scraper = get_scraper()
    tours = []
    page = 0
    per_page = 50
    
    logger.info(f"📡 Récupération de la liste des tours pour l'utilisateur {user_id}...")
    
    while True:
        url = f"https://api.komoot.de/v007/users/{user_id}/tours/"
        params = {
            'type': 'tour_recorded',
            'sort': 'date',
            'sort_direction': 'desc',
            'status': 'public',  # Important: ne cherche que les publics
            'page': page,
            'limit': per_page
        }
        
        try:
            resp = scraper.get(url, params=params, timeout=15)
            if resp.status_code == 403:
                logger.error("❌ 403 Forbidden - Votre profil est-il bien 'Public' ?")
                break
            if resp.status_code != 200:
                logger.error(f"❌ Erreur {resp.status_code} sur la page {page}")
                break
                
            data = resp.json()
            embedded_tours = data.get('_embedded', {}).get('tours', [])
            
            if not embedded_tours:
                break
                
            for t in embedded_tours:
                # On ne garde que l'essentiel
                tours.append({
                    'id': t['id'],
                    'name': t.get('name', 'Sans nom'),
                    'date': t.get('date'),
                    'distance': t.get('distance', 0)
                })
            
            logger.info(f"   Page {page}: {len(embedded_tours)} tours trouvés (Total: {len(tours)})")
            
            # Vérification s'il reste des pages
            pagination = data.get('page', {})
            if page >= pagination.get('totalPages', 0) - 1:
                break
                
            page += 1
            time.sleep(1) # Pause gentille
            
        except Exception as e:
            logger.error(f"❌ Erreur réseau: {e}")
            break
            
    return tours

def fetch_tour_coordinates(tour_id):
    """Récupère les points GPS d'un tour spécifique via l'API coordonnées"""
    scraper = get_scraper()
    # Cette URL est souvent ouverte même sans cookie pour les tours publics
    url = f"https://api.komoot.de/v007/tours/{tour_id}/coordinates"
    
    try:
        resp = scraper.get(url, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            # L'API renvoie souvent : {'items': [{'lat':..., 'lng':...}, ...]}
            items = data.get('items', [])
            points = []
            for item in items:
                points.append((item['lat'], item['lng']))
            return points
        elif resp.status_code == 403:
            logger.warning(f"⚠️ Accès refusé aux coords du tour {tour_id} (Peut-être privé ?)")
        else:
            logger.warning(f"⚠️ Erreur {resp.status_code} pour le tour {tour_id}")
            
    except Exception as e:
        logger.error(f"Erreur récup coords {tour_id}: {e}")
    
    return []

# ==========================================
# LOGIQUE TILE & CARTE
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
        with open(DATA_FILE, 'r') as f:
            return json.load(f)
    return {"tours_processed": [], "points": [], "tiles": []}

def save_data(data):
    with open(DATA_FILE, 'w') as f:
        json.dump(data, f)

def update_database(user_id):
    db = load_data()
    processed_ids = set(db["tours_processed"])
    
    # 1. Récupérer la liste des tours
    all_tours = fetch_public_tours_list(user_id)
    
    new_tours = [t for t in all_tours if t['id'] not in processed_ids]
    logger.info(f"🔄 {len(new_tours)} nouveaux tours à traiter.")
    
    existing_tiles = set(tuple(t) for t in db["tiles"])
    new_points_count = 0
    
    # 2. Récupérer les détails pour les nouveaux
    for i, tour in enumerate(new_tours):
        logger.info(f"Downloading tour {i+1}/{len(new_tours)}: {tour['name']}")
        
        points = fetch_tour_coordinates(tour['id'])
        
        if points:
            # Simplification (1 point sur N)
            simplified = points[::SIMPLIFY_FACTOR]
            
            # Ajouter aux points globaux (pour le tracé rouge)
            # On stocke par tour pour éviter un fichier JSON monolithique trop gros si besoin,
            # mais ici on garde la structure simple : liste de listes de points
            db.setdefault("traces", []).append(simplified)
            
            # Calcul des Tiles
            for lat, lon in simplified:
                tile = deg2num(lat, lon, TILE_ZOOM)
                existing_tiles.add(tile)
            
            db["tours_processed"].append(tour['id'])
            new_points_count += len(simplified)
            
        time.sleep(0.5) # Politesse API
        
    db["tiles"] = list(existing_tiles)
    
    # Recalcul stats basiques
    logger.info(f"✅ Mise à jour terminée. Total tiles: {len(db['tiles'])}")
    save_data(db)
    return db

# ==========================================
# GÉNÉRATION CARTE HTML
# ==========================================

def create_map(db):
    if not db["tiles"]:
        logger.warning("⚠️ Aucune donnée à afficher.")
        return

    # Centre de la carte (Dernière trace ou Paris par défaut)
    start_loc = [48.8566, 2.3522]
    if db.get("traces"):
        start_loc = db["traces"][-1][0]

    m = folium.Map(location=start_loc, zoom_start=10, tiles="CartoDB dark_matter")
    Fullscreen().add_to(m)

    # 1. DESSINER LES TILES (Carrés)
    # On groupe les tiles pour réduire le DOM HTML si trop nombreux ? Non, simple pour l'instant.
    logger.info("🎨 Dessin des tiles...")
    for xtile, ytile in db["tiles"]:
        # Coins du carré
        nw = num2deg(xtile, ytile, TILE_ZOOM)
        se = num2deg(xtile + 1, ytile + 1, TILE_ZOOM)
        
        bounds = [
            [nw[0], nw[1]], # Nord-Ouest
            [se[0], se[1]]  # Sud-Est
        ]
        
        folium.Rectangle(
            bounds=bounds,
            color=None,
            fill=True,
            fill_color=TILE_COLOR,
            fill_opacity=0.3,
            weight=0
        ).add_to(m)

    # 2. DESSINER LES TRACES (Lignes rouges)
    logger.info("🎨 Dessin des traces...")
    if "traces" in db:
        for trace in db["traces"]:
            if len(trace) > 1:
                folium.PolyLine(
                    trace,
                    color=TRACE_COLOR,
                    weight=2,
                    opacity=0.6
                ).add_to(m)

    m.save("index.html")
    logger.info("🚀 Carte générée : index.html")

# ==========================================
# MAIN
# ==========================================

if __name__ == "__main__":
    if not USER_ID:
        logger.error("❌ Erreur: La variable d'environnement KOMOOT_USER_ID est manquante.")
        exit(1)
        
    logger.info("=== Démarrage Komoot Public Scraper ===")
    data = update_database(USER_ID)
    create_map(data)
