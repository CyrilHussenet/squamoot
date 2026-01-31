import os
import requests
import gpxpy
import folium
import logging
import time
import re
import json
from folium.plugins import HeatMap
from datetime import datetime

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger()

# --- VOTRE LIEN DE PROFIL ---
PROFILE_URL = "https://www.komoot.com/fr-fr/user/1366042741035" 
# --- INFOS GITHUB ---
REPO_OWNER = "CyrilHussenet"
REPO_NAME = "squamoot"

def get_real_id(session):
    try:
        logger.info(f"Téléchargement du profil : {PROFILE_URL}")
        response = session.get(PROFILE_URL)
        if response.status_code != 200:
            logger.error(f"Erreur accès profil : {response.status_code}")
            return None
        
        # On cherche le bloc de données caché de Next.js
        # C'est là que Komoot stocke toutes les infos de la page
        match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', response.text)
        
        if match:
            data_json = json.loads(match.group(1))
            # On navigue dans la structure JSON pour trouver l'ID du profil affiché
            # Chemin standard : props -> pageProps -> profile -> id
            try:
                profile_data = data_json.get('props', {}).get('pageProps', {}).get('profile', {})
                found_id = profile_data.get('id')
                
                if found_id:
                    logger.info(f"✅ ID TECHNIQUE TROUVÉ : {found_id}")
                    return found_id
            except:
                pass
            
            # Si le chemin standard échoue, on cherche récursivement n'importe quel ID utilisateur
            logger.info("Chemin standard échoué, recherche profonde...")
            str_data = str(data_json)
            # On cherche un ID qui ressemble à un entier long (ex: 396172...)
            ids = re.findall(r"'id': (\d{5,15})", str_data)
            for i in ids:
                if i != "1366042741035": # On exclut l'ID public de l'URL
                    logger.info(f"✅ ID alternatif trouvé : {i}")
                    return i
                    
        logger.error("❌ Impossible d'extraire l'ID du bloc JSON.")
        return None

    except Exception as e:
        logger.error(f"Erreur d'analyse : {e}")
        return None

def run_sync():
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'fr-FR,fr;q=0.9',
    })
    
    user_id = get_real_id(session)
    
    # --- PLAN DE SECOURS ---
    # Si le script échoue encore à trouver l'ID automatiquement,
    # vous pourrez remplacer "None" ci-dessous par votre ID trouvé manuellement (voir tuto après le code)
    if not user_id: 
        logger.warning("Tentative avec l'ID URL par défaut (faible chance de succès)...")
        user_id = "1366042741035"

    all_points = []
    total_dist, total_elev, count = 0.0, 0.0, 0
    
    try:
        page = 0
        while True:
            logger.info(f"Récupération traces pour ID {user_id} (Page {page})...")
            # API endpoint
            tours_url = f"https://www.komoot.com/api/v007/users/{user_id}/tours/?type=tour_recorded&status=public&limit=50&page={page}"
            resp = session.get(tours_url)
            
            if resp.status_code == 404:
                 logger.error("❌ L'API renvoie 404. L'ID est incorrect ou le profil est privé.")
                 break
            
            tours = resp.json().get('_embedded', {}).get('tours', [])
            if not tours: 
                logger.info("Fin de la liste.")
                break

            for tour in tours:
                gpx_res = session.get(f"https://www.komoot.com/api/v007/tours/{tour['id']}.gpx")
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
            page += 1

        if all_points:
            avg_lat = sum(p[0] for p in all_points) / len(all_points)
            avg_lon = sum(p[1] for p in all_points) / len(all_points)
            m = folium.Map(location=[avg_lat, avg_lon], zoom_start=11, tiles='CartoDB dark_matter')
            HeatMap(all_points, radius=4, blur=2, min_opacity=0.4, gradient={0.4:'blue', 0.7:'cyan', 1:'white'}).add_to(m)
            
            m.get_root().html.add_child(folium.Element(f'''
                <div style="position:fixed; top:10px; left:50px; z-index:1000; background:rgba(20,20,20,0.9); color:white; padding:15px; border-radius:10px; border:1px solid #00f2ff; font-family:sans-serif;">
                    <h2 style="margin:0; font-size:16px; color:#00f2ff;">SQUADRA MAP</h2>
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
            logger.info("🎉 SUCCÈS TOTAL : Carte générée !")
        else:
            logger.warning("Pas de traces trouvées (ID valide mais 0 sortie ou privé).")

    except Exception as e:
        logger.error(f"Erreur : {e}")

if __name__ == "__main__":
    run_sync()
