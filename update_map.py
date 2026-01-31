import os
import requests
import gpxpy
import folium
import logging
import json
import time
from folium.plugins import HeatMap

# Configuration du logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger()

# --- CONFIGURATION ---
USER_ID = os.getenv("KOMOOT_USER_ID")
DATA_FILE = "all_points.json"

def load_existing_data():
    """Charge les données locales pour éviter de tout retélécharger."""
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r') as f:
                data = json.load(f)
                # S'assurer que les clés existent
                if "tour_ids" not in data: data["tour_ids"] = []
                if "stats" not in data: data["stats"] = {"dist": 0, "elev": 0, "count": 0}
                return data
        except Exception as e:
            logger.error(f"Erreur lecture JSON : {e}")
    return {"points": [], "tour_ids": [], "stats": {"dist": 0, "elev": 0, "count": 0}}

def save_data(data):
    """Sauvegarde les points et les IDs de tours."""
    with open(DATA_FILE, 'w') as f:
        json.dump(data, f)

def run_sync():
    if not USER_ID:
        logger.error("L'ID utilisateur est manquant dans les Secrets GitHub.")
        return

    # Configuration de la session pour éviter la 403
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
        'Accept': 'application/json, text/plain, */*',
        'Accept-Language': 'fr-FR,fr;q=0.9',
        'Referer': f'https://www.komoot.com/fr-fr/user/{USER_ID}',
        'Origin': 'https://www.komoot.com'
    })
    
    storage = load_existing_data()
    is_first_run = len(storage["tour_ids"]) == 0
    new_tours_found = 0
    page = 0

    try:
        # Simulation d'une visite sur le profil pour les cookies
        session.get(f"https://www.komoot.com/fr-fr/user/{USER_ID}")
        time.sleep(1)

        while True:
            # On boucle sur les pages (100 par 100)
            url = f"https://www.komoot.com/api/v007/users/{USER_ID}/tours/?type=tour_recorded&sort_field=date&sort_direction=desc&limit=100&page={page}"
            logger.info(f"Analyse page {page}...")
            
            resp = session.get(url)
            if resp.status_code != 200:
                logger.error(f"Erreur API {resp.status_code} à la page {page}")
                break

            tours = resp.json().get('_embedded', {}).get('tours', [])
            if not tours:
                break

            for tour in tours:
                tour_id = str(tour['id'])
                
                # Si le tour est déjà connu, on arrête la boucle (sauf au premier lancement)
                if tour_id in storage["tour_ids"]:
                    if not is_first_run:
                        logger.info("Tour déjà connu atteint. Fin de la synchronisation.")
                        return finalize(storage)
                    continue

                logger.info(f"📥 Nouveau tour détecté : {tour['name']} ({tour_id})")
                
                # Récupération du GPX
                gpx_url = f"https://www.komoot.com/api/v1/tours/{tour_id}.gpx"
                gpx_res = session.get(gpx_url)
                
                if gpx_res.status_code == 200:
                    try:
                        gpx = gpxpy.parse(gpx_res.text)
                        storage["stats"]["dist"] += gpx.length_2d()
                        storage["stats"]["elev"] += gpx.get_uphill_downhill().uphill
                        storage["stats"]["count"] += 1
                        storage["tour_ids"].append(tour_id)
                        
                        for track in gpx.tracks:
                            for seg in track.segments:
                                for p in seg.points:
                                    storage["points"].append([round(p.latitude, 5), round(p.longitude, 5)])
                        
                        new_tours_found += 1
                        time.sleep(0.05) # Petit délai pour la courtoisie
                    except: continue
                
            page += 1
            # Sécurité pour ne pas boucler à l'infini
            if page > 20: break

        finalize(storage)

    except Exception as e:
        logger.error(f"Erreur critique : {e}")

def finalize(storage):
    """Sauvegarde les données et génère la carte."""
    save_data(storage)
    
    if storage["points"]:
        # Création de la carte Heatmap
        avg_lat = sum(p[0] for p in storage["points"][-100:]) / 100 # Centré sur les derniers points
        avg_lon = sum(p[1] for p in storage["points"][-100:]) / 100
        
        m = folium.Map(location=[avg_lat, avg_lon], zoom_start=11, tiles='CartoDB dark_matter')
        
        HeatMap(storage["points"], radius=3, blur=2, min_opacity=0.3, 
                gradient={0.4:'blue', 0.7:'cyan', 1:'white'}).add_to(m)
        
        # Dashboard HTML
        stats = storage["stats"]
        info_box = f'''
        <div style="position:fixed; top:10px; left:50px; z-index:1000; background:rgba(0,0,0,0.8); color:white; padding:15px; border-radius:10px; border:1px solid #00f2ff; font-family:sans-serif; box-shadow: 0 0 10px #00f2ff;">
            <b style="color:#00f2ff; font-size:16px;">SQUADRA MAP</b><br>
            <span style="font-size:14px;"><b>{stats['count']}</b> sorties</span><br>
            <span style="font-size:14px;"><b>{round(stats['dist']/1000)}</b> km parcourus</span>
        </div>
        '''
        m.get_root().html.add_child(folium.Element(info_box))
        m.save("index.html")
        logger.info("✅ Données sauvegardées et index.html généré.")
    else:
        logger.warning("Aucune donnée GPS à afficher.")

if __name__ == "__main__":
    run_sync()
