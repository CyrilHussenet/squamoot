import os
import requests
import gpxpy
import folium
import logging
import time
import re
from folium.plugins import HeatMap
from datetime import datetime

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger()

# --- VOTRE LIEN DE PROFIL ICI ---
PROFILE_URL = "https://www.komoot.com/fr-fr/user/1366042741035" 
# --- INFOS GITHUB ---
REPO_OWNER = "CyrilHussenet"
REPO_NAME = "squamoot"

def get_real_id(session):
    """Cherche le vrai ID technique caché dans le code HTML de la page de profil"""
    try:
        logger.info(f"Analyse de la page profil : {PROFILE_URL}")
        response = session.get(PROFILE_URL)
        if response.status_code != 200:
            logger.error("Impossible d'accéder au profil public.")
            return None
        
        # On cherche un motif du type "api/v1/users/123456" dans le code de la page
        # C'est souvent présent dans les scripts intégrés
        match = re.search(r'api/v1/users/(\d+)', response.text)
        if match:
            real_id = match.group(1)
            logger.info(f"✅ Vrai ID trouvé : {real_id}")
            return real_id
        else:
            logger.error("Aucun ID trouvé dans la page HTML.")
            return None
    except Exception as e:
        logger.error(f"Erreur lors de la recherche d'ID : {e}")
        return None

def run_sync():
    session = requests.Session()
    # On se fait passer pour un vrai navigateur Chrome
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept-Language': 'fr-FR,fr;q=0.9',
    })
    
    # 1. On trouve le bon ID
    user_id = get_real_id(session)
    if not user_id: return

    all_points = []
    total_dist, total_elev, count = 0.0, 0.0, 0
    
    try:
        page = 0
        while True:
            # 2. On utilise l'ID trouvé pour interroger l'API
            tours_url = f"https://www.komoot.com/api/v1/users/{user_id}/tours/?type=tour_recorded&status=public&limit=50&page={page}"
            resp = session.get(tours_url)
            
            if resp.status_code != 200:
                logger.error(f"Erreur API ({resp.status_code}) à la page {page}.")
                break
            
            tours = resp.json().get('_embedded', {}).get('tours', [])
            if not tours: break

            for tour in tours:
                gpx_url = f"https://www.komoot.com/api/v1/tours/{tour['id']}.gpx"
                gpx_res = session.get(gpx_url)
                if gpx_res.status_code == 200:
                    try:
                        gpx = gpxpy.parse(gpx_res.text)
                        total_dist += gpx.length_2d()
                        total_elev += gpx.get_uphill_downhill().uphill
                        count += 1
                        for track in gpx.tracks:
                            for seg in track.segments:
                                for p in seg.points:
                                    all_points.append([p.latitude, p.longitude])
                    except: continue
                time.sleep(0.05)
            
            logger.info(f"Page {page} traitée.")
            page += 1

        if all_points:
            avg_lat = sum(p[0] for p in all_points) / len(all_points)
            avg_lon = sum(p[1] for p in all_points) / len(all_points)
            m = folium.Map(location=[avg_lat, avg_lon], zoom_start=11, tiles='CartoDB dark_matter')
            HeatMap(all_points, radius=4, blur=2, min_opacity=0.4, gradient={0.4:'blue', 0.7:'cyan', 1:'white'}).add_to(m)
            
            # HTML Dashboard
            m.get_root().html.add_child(folium.Element(f'''
                <div style="position:fixed; top:10px; left:50px; z-index:1000; background:rgba(20,20,20,0.9); color:white; padding:15px; border-radius:10px; border:1px solid #00f2ff; font-family:sans-serif;">
                    <h2 style="margin:0; font-size:16px; color:#00f2ff;">KOMOOT SQUADRA</h2>
                    <p>{count} sorties | {round(total_dist/1000)} km | {int(total_elev)}m D+</p>
                </div>
                <button onclick="triggerUpdate()" style="position:fixed; top:10px; right:10px; z-index:1000; background:#1a1a1a; color:#00f2ff; border:1px solid #00f2ff; padding:10px; cursor:pointer; border-radius:5px;">🔄 UPDATE</button>
                <script>
                function triggerUpdate() {{
                    const token = prompt("Token GitHub :");
                    if(!token) return;
                    fetch('https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/actions/workflows/main.yml/dispatches', {{
                        method:'POST', headers:{{'Authorization':'Bearer '+token}}, body:JSON.stringify({{ref:'main'}})
                    }}).then(r=>alert("Mise à jour lancée !"));
                }}
                </script>
            '''))
            m.save("index.html")
            logger.info("Fichier index.html généré avec succès !")
        else:
            logger.warning("Connexion réussie mais aucune trace GPS trouvée.")

    except Exception as e:
        logger.error(f"Erreur : {e}")

if __name__ == "__main__":
    run_sync()
