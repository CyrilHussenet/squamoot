import os
import requests
import gpxpy
import folium
import logging
import json
import time
from folium.plugins import HeatMap

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger()

USER_ID = os.getenv("KOMOOT_USER_ID")
SESSION_COOKIE = os.getenv("KOMOOT_SESSION_COOKIE")
DATA_FILE = "all_points.json"

def load_existing_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r') as f:
                return json.load(f)
        except: pass
    return {"points": [], "tour_ids": [], "stats": {"dist": 0, "elev": 0, "count": 0}}

def run_sync():
    if not USER_ID or not SESSION_COOKIE:
        logger.error("Secrets manquants (ID ou Cookie).")
        return
    
    session = requests.Session()
    # On injecte le cookie de session manuellement
    session.cookies.set('komoot_session', SESSION_COOKIE, domain='.komoot.com')
    
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
        'Accept': 'application/json'
    })
    
    storage = load_existing_data()
    
    # On utilise l'URL que tu as trouvée
    url = f"https://www.komoot.com/api/v007/users/{USER_ID}/tours/?type=tour_recorded&sort_field=date&sort_direction=desc&limit=100"
    
    try:
        resp = session.get(url)
        if resp.status_code != 200:
            logger.error(f"Erreur API {resp.status_code}. Le cookie est peut-être expiré.")
            return

        tours = resp.json().get('_embedded', {}).get('tours', [])
        new_count = 0

        for tour in tours:
            t_id = str(tour['id'])
            if t_id not in storage["tour_ids"]:
                logger.info(f"Téléchargement : {tour['name']}")
                res_gpx = session.get(f"https://www.komoot.com/api/v1/tours/{t_id}.gpx")
                if res_gpx.status_code == 200:
                    gpx = gpxpy.parse(res_gpx.text)
                    storage["stats"]["dist"] += gpx.length_2d()
                    storage["stats"]["count"] += 1
                    storage["tour_ids"].append(t_id)
                    for track in gpx.tracks:
                        for seg in track.segments:
                            for p in seg.points:
                                storage["points"].append([round(p.latitude, 5), round(p.longitude, 5)])
                    new_count += 1
                    time.sleep(0.1)

        if new_count > 0 or not os.path.exists(DATA_FILE):
            with open(DATA_FILE, 'w') as f: json.dump(storage, f)
            
        if storage["points"]:
            m = folium.Map(location=storage["points"][-1], zoom_start=11, tiles='CartoDB dark_matter')
            HeatMap(storage["points"], radius=3, blur=2).add_to(m)
            m.save("index.html")
            logger.info("✅ Carte mise à jour avec succès.")

    except Exception as e:
        logger.error(f"Erreur : {e}")

if __name__ == "__main__":
    run_sync()
